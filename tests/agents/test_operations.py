"""R6: maintenance, triage and inventory, including a simulated 30 days in the makerspace."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from msa.agents.inventory import agent as inv
from msa.agents.maintenance import agent as maint
from msa.core import orchestrator
from msa.core.db import utcnow
from msa.core.models import (
    Approval,
    Equipment,
    Escalation,
    InventoryItem,
    InventoryTxn,
    MaintenancePlan,
    Notification,
    OrderDraft,
    Report,
)
from msa.core.services import ServiceError, decide_approval
from msa.core.testing import make_equipment, make_user


def publish_machine(db, eid, brand, model, cat):
    eq = make_equipment(db, eid, name=eid, brand=brand, model_number=model, category=cat)
    orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    staff = make_user(db, role="staff")
    for _ in range(4):
        for a in db.scalars(select(Approval).where(Approval.status == "pending",
                                                   Approval.kind.in_(["publish_doc", "quiz_bank",
                                                                      "maintenance_plan"]))).all():
            decide_approval(db, a.id, True, staff)
    return eq, staff


# ------------------------------------------------------------- maintenance


def test_parse_interval():
    assert maint.parse_interval("Every 8 hours of use: clean the lens.")["interval_hours"] == 8
    assert maint.parse_interval("Every 30 days: empty the tray.")["interval_days"] == 30
    assert maint.parse_interval("Every 2 weeks: oil")["interval_days"] == 14
    assert maint.parse_interval("Weekly: check belts")["interval_days"] == 7
    assert maint.parse_interval("Clean it sometimes") is None


def test_plans_drafted_from_manual_with_citations(db):
    eq, _ = publish_machine(db, "laser-01", "Lumenic", "Vector 60", "laser_cutter")
    plans = db.scalars(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                     MaintenancePlan.status == "approved")).all()
    tasks = {p.task: (p.interval_days, p.interval_hours) for p in plans}
    assert tasks["clean the focus lens with lens wipes"] == (None, 8)
    assert all(p.source_ref.get("document_id") for p in plans)


def test_due_then_overdue_then_logged(db):
    eq, staff = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    weekly = db.scalar(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                     MaintenancePlan.interval_days == 7))
    now = utcnow()
    maint.daily_check(db, now + timedelta(days=8))
    assert eq.status == "maintenance_due"
    maint.daily_check(db, now + timedelta(days=9))  # repeat: still one notification
    n = db.scalar(select(Notification).where(Notification.dedup_key == f"maintenance:{eq.id}:{weekly.id}"))
    assert n.count == 2
    maint.daily_check(db, now + timedelta(days=7 + 8))  # past the 7-day grace
    assert eq.status == "out_of_service"
    later = now + timedelta(days=16)
    for p in db.scalars(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                      MaintenancePlan.status == "approved")).all():
        if maint.is_due(p, eq, later)[0]:
            maint.log_work(db, eq.id, "maintenance", "done", staff.id, p.id, now=later)
    assert eq.status == "available"


def test_usage_hours_make_a_plan_due(db):
    eq, _ = publish_machine(db, "laser-01", "Lumenic", "Vector 60", "laser_cutter")
    eq.usage_hours = 9.0
    out = maint.daily_check(db)
    assert any("lens" in d["task"] for d in out["due"])


def test_log_corrections_must_point_at_an_entry(db):
    eq = make_equipment(db, "drill-01")
    with pytest.raises(ServiceError):
        maint.log_work(db, eq.id, "correction", "oops", "staff")
    e = maint.log_work(db, eq.id, "inspection", "looked fine", "staff")
    c = maint.log_work(db, eq.id, "correction", "it was the other drill", "staff", corrects_id=e.id)
    assert c.corrects_id == e.id


# ------------------------------------------------------------------ triage


def triage(db, user, text, eid=None, hint=None):
    final, _ = orchestrator.run(db, "triage", {"user_id": user.id, "text": text,
                                               "equipment_id": eid, "kind_hint": hint}, user.id)
    return final


def test_duplicate_reports_make_one_notification_and_e15(db):
    eq, _ = publish_machine(db, "laser-01", "Lumenic", "Vector 60", "laser_cutter")
    users = [make_user(db) for _ in range(3)]
    texts = ["The laser cutter is not firing", "laser won't fire at all", "Laser is broken, no beam"]
    for u, t in zip(users, texts, strict=True):
        r = triage(db, u, t, eq.id)
    assert r.output["reports_in_cluster"] == 3
    notes = db.scalars(select(Notification).where(Notification.dedup_key.like("feedback:%"))).all()
    assert len(notes) == 1 and notes[0].count == 3
    assert eq.status == "issue_reported"
    assert db.scalar(select(Escalation).where(Escalation.code == "E15"))


def test_incident_takes_machine_out_and_shows_emergency_info(db):
    eq, _ = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    r = triage(db, make_user(db), "Someone cut my finger on the table saw, it's bleeding", eq.id)
    assert r.output["kind"] == "incident" and r.output["emergency"]
    assert "E10" in r.escalations and eq.status == "out_of_service"


def test_near_miss_is_recorded(db):
    eq, _ = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    r = triage(db, make_user(db), "Almost had a kickback on the table saw when ripping pine", eq.id)
    assert r.output["kind"] == "near_miss"
    assert db.scalar(select(Report).where(Report.kind == "near_miss"))


def test_suggestion_comes_from_approved_docs(db):
    eq, _ = publish_machine(db, "fdm-01", "Strata", "Forge S1", "fdm_printer")
    r = triage(db, make_user(db), "My print does not stick to the plate", eq.id)
    s = r.output.get("suggestion")
    assert s and s["citations"]


# --------------------------------------------------------------- inventory


@pytest.fixture
def stock(db):
    acrylic = InventoryItem(name="Acrylic sheet 3 mm", unit="sheet", aliases=["acrylic", "3 mm acrylic"],
                            reorder_point=6, reorder_qty=20, supplier="Northgate")
    ply = InventoryItem(name="Plywood 3 mm", unit="sheet", aliases=["plywood", "ply"],
                        reorder_point=5, reorder_qty=20, supplier="Northgate")
    db.add_all([acrylic, ply])
    db.flush()
    db.add_all([InventoryTxn(item_id=acrylic.id, delta=8), InventoryTxn(item_id=ply.id, delta=10)])
    db.flush()
    return acrylic, ply


def test_plain_language_use_and_restock(db, stock):
    acrylic, _ = stock
    u = make_user(db)
    r = inv.log_use(db, {"text": "used two sheets of 3 mm acrylic"}, u.id)
    assert r.output["delta"] == -2 and r.output["on_hand"] == 6
    r = inv.log_use(db, {"text": "restocked 10 acrylic sheets"}, u.id)
    assert r.output["delta"] == 10


def test_ambiguous_note_asks_instead_of_guessing(db, stock):
    r = inv.log_use(db, {"text": "used 2 sheets"}, "u")
    assert r.status == "refused" and r.output["needs_clarification"]


def test_low_stock_drafts_one_order_per_supplier(db, stock):
    acrylic, ply = stock
    inv.log_use(db, {"text": "used 3 acrylic sheets"}, "u")
    inv.log_use(db, {"text": "used 6 sheets of plywood"}, "u")
    drafts = db.scalars(select(OrderDraft)).all()
    assert len(drafts) == 1 and {i["item_id"] for i in drafts[0].items} == {acrylic.id, ply.id}
    assert db.scalar(select(Approval).where(Approval.kind == "order_draft"))


# ------------------------------------------------------- 30-day simulation


def test_simulated_30_days(db, stock):
    eq, staff = publish_machine(db, "laser-01", "Lumenic", "Vector 60", "laser_cutter")
    acrylic, _ = stock
    start = utcnow()
    due_days = []
    for day in range(30):
        now = start + timedelta(days=day)
        eq.usage_hours += 0.5  # half an hour of cutting a day
        if day % 4 == 0:
            db.add(InventoryTxn(item_id=acrylic.id, delta=-1, created_at=now))
        out = maint.daily_check(db, now)
        if out["due"]:
            due_days.append(day)
            for p in db.scalars(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                              MaintenancePlan.status == "approved")):
                if maint.is_due(p, eq, now)[0]:
                    maint.log_work(db, eq.id, "maintenance", "done", staff.id, p.id, now=now)
        db.flush()
    # 8-hour lens cleaning at 0.5 h/day comes due around day 16; 30-day tasks don't fall due yet
    assert due_days and 14 <= due_days[0] <= 17
    assert eq.status == "available"
    assert db.get(Equipment, eq.id).status == "available"
    assert inv.on_hand(db, acrylic.id) == 0  # 8 - 8 uses
    assert inv.check_reorder(db, acrylic) is not None


def test_model_plan_with_odd_types_is_cleaned(db):
    """Regression (real Claude reply, 2026-11-07): "line" came back as a number."""
    from msa.core.llm import ScriptedLLM, set_llm

    set_llm(ScriptedLLM({"maintenance.plan": {"plans": [
        {"task": "clean and lubricate the linear rails", "interval_days": None,
         "interval_hours": 40, "line": 1},
        {"task": "made-up task", "interval_days": 5, "line": 2},
        {"task": "empty the dust collector bag", "interval_days": "30", "line": None}]}}))
    eq, _ = publish_machine(db, "cnc-01", "Ironleaf", "R4848", "cnc_router")
    tasks = {p.task: (p.interval_days, p.interval_hours) for p in db.scalars(
        select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id))}
    assert tasks["clean and lubricate the linear rails"] == (None, 40.0)
    assert tasks["empty the dust collector bag"] == (30, None)
    assert "made-up task" not in tasks


def test_a_crashing_event_handler_does_not_block_publishing(db):
    from msa.core.registry import registry
    from msa.core.services import emit

    def boom(db, **kw):
        raise RuntimeError("bug in some role's handler")

    registry.on_event("test.event")(boom)
    emit(db, "test.event")  # must not raise
    assert db.scalar(select(Escalation).where(Escalation.code == "E14"))
