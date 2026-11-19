"""R6 fixes: one test for each problem found in the 2026-11-19 review of the operations agents.

Each test is written to FAIL on the code as it was before the fixes and PASS after them:

1. hour-based maintenance never reaches out_of_service after the grace period
2. a second order draft is created while an approved order is still on its way
3. members can add stock (only staff may restock)
4. an AI-suggested maintenance interval is not checked against the manual
5a. incident reports go to the LLM before the emergency path runs (G5)
5b. a report about bypassing a guard still gets a troubleshooting suggestion (no E04)
6. re-drafting maintenance plans resets the "last done" dates
7. maintenance plans never went through the Review agent (tests at the end of this file)
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from msa.agents.inventory import agent as inv
from msa.agents.maintenance import agent as maint
from msa.agents.review.agent import review_plans
from msa.core import orchestrator
from msa.core.db import session_scope, utcnow
from msa.core.llm import ScriptedLLM, set_llm
from msa.core.models import (
    Approval,
    InventoryItem,
    InventoryTxn,
    MaintenancePlan,
    OrderDraft,
)
from msa.core.services import decide_approval
from msa.core.testing import make_equipment, make_user
from tests.agents.test_operations import publish_machine, triage
from tests.conftest import auth_headers


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


# ---------------------------------------------------------------- 1. maintenance grace


def test_hour_based_task_goes_out_of_service_after_the_grace_period(db):
    """The CNC router's rails are due every 40 hours of use. If nobody does it for more than
    7 days after it falls due, the machine must be taken out of service (C5)."""
    eq, _ = publish_machine(db, "cnc-01", "Ironleaf", "R4848", "cnc_router")
    now = utcnow()
    eq.usage_hours = 41.0  # past the 40 h interval
    maint.daily_check(db, now)
    assert eq.status == "maintenance_due"
    maint.daily_check(db, now + timedelta(days=3))  # inside the grace period
    assert eq.status == "maintenance_due"
    maint.daily_check(db, now + timedelta(days=8))  # grace period over
    assert eq.status == "out_of_service"


# ------------------------------------------------------------------ 2. duplicate orders


def test_no_second_order_while_an_approved_order_is_on_its_way(db, stock):
    acrylic, _ = stock
    staff = make_user(db, role="staff")
    inv.log_use(db, {"text": "used 3 acrylic sheets"}, staff.id)  # 8 -> 5, below the reorder point
    approval = db.scalar(select(Approval).where(Approval.kind == "order_draft",
                                                Approval.status == "pending"))
    decide_approval(db, approval.id, True, staff)  # staff approve; they order outside the system
    inv.log_use(db, {"text": "used 1 acrylic sheet"}, staff.id)  # still waiting for delivery
    inv.daily(db)  # the daily check must not draft it again either
    drafts = db.scalars(select(OrderDraft)).all()
    assert len(drafts) == 1 and drafts[0].status == "approved"
    assert len(db.scalars(select(Approval).where(Approval.kind == "order_draft")).all()) == 1
    assert inv.on_hand(db, acrylic.id) == 4


def test_a_rejected_order_does_not_block_a_new_draft(db, stock):
    staff = make_user(db, role="staff")
    inv.log_use(db, {"text": "used 3 acrylic sheets"}, staff.id)
    approval = db.scalar(select(Approval).where(Approval.kind == "order_draft",
                                                Approval.status == "pending"))
    decide_approval(db, approval.id, False, staff, reason="too expensive this month")
    inv.log_use(db, {"text": "used 1 acrylic sheet"}, staff.id)
    assert len(db.scalars(select(OrderDraft).where(OrderDraft.status == "draft")).all()) == 1


# ------------------------------------------------------------ 3. only staff may restock


def test_only_staff_can_add_stock(db, stock):
    acrylic, _ = stock
    member = make_user(db)
    staff = make_user(db, role="staff")

    r = inv.log_use(db, {"text": "restocked 10 acrylic sheets"}, member.id)
    assert r.status == "refused"
    r = inv.log_use(db, {"item_id": acrylic.id, "quantity": 50, "direction": 1}, member.id)
    assert r.status == "refused"
    assert inv.on_hand(db, acrylic.id) == 8  # nothing changed

    r = inv.log_use(db, {"text": "restocked 10 acrylic sheets"}, staff.id)
    assert r.status == "ok" and inv.on_hand(db, acrylic.id) == 18


def test_a_member_cannot_scale_a_stock_change_with_a_big_direction(db, stock):
    acrylic, _ = stock
    member = make_user(db)
    r = inv.log_use(db, {"item_id": acrylic.id, "quantity": 1, "direction": -100}, member.id)
    assert r.status == "ok" and inv.on_hand(db, acrylic.id) == 7  # used exactly one sheet


def test_member_cannot_restock_through_the_api(client):
    with session_scope() as db:
        item = InventoryItem(name="PLA test spool", unit="spool", aliases=["pla"], reorder_point=1,
                             reorder_qty=5, supplier="Test Supply")
        db.add(item)
        db.flush()
        db.add(InventoryTxn(item_id=item.id, delta=3))
        member = make_user(db)
        email, item_id = member.email, item.id
    h = auth_headers(client, email)
    r = client.post("/inventory/use", json={"item_id": item_id, "quantity": 50, "direction": 1},
                    headers=h)
    assert r.status_code == 200 and r.json()["status"] == "refused"
    with session_scope() as db:
        assert inv.on_hand(db, item_id) == 3


# ----------------------------------------------- 4. AI intervals must match the manual


def test_model_interval_that_disagrees_with_the_manual_is_not_used(db):
    """The Ironleaf manual says "Every 40 hours of use: clean and lubricate the linear rails"."""
    set_llm(ScriptedLLM({"maintenance.plan": {"plans": [
        {"task": "clean and lubricate the linear rails", "interval_days": None,
         "interval_hours": 20, "line": "x"}]}}))
    eq, _ = publish_machine(db, "cnc-01", "Ironleaf", "R4848", "cnc_router")
    rails = db.scalars(select(MaintenancePlan).where(
        MaintenancePlan.equipment_id == eq.id,
        MaintenancePlan.task == "clean and lubricate the linear rails")).all()
    assert rails and all(p.interval_hours == 40 for p in rails)


# ------------------------------------------------------------ 5. triage safety


def test_incident_text_is_never_sent_to_the_llm(db):
    """G5: emergencies skip the agents. The deterministic check decides before any model call."""
    llm = ScriptedLLM({})
    set_llm(llm)
    eq = make_equipment(db, "saw-01", category="table_saw")
    r = triage(db, make_user(db), "Someone cut my finger on the table saw, it's bleeding", eq.id)
    assert r.output["kind"] == "incident" and r.output["emergency"]
    assert "E10" in r.escalations and eq.status == "out_of_service"
    assert [c for c in llm.calls if c["task"] == "triage.classify"] == []


def test_report_about_bypassing_a_guard_gets_no_suggestion_and_raises_e04(db):
    eq, _ = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    r = triage(db, make_user(db), "The saw guard is broken and stuck, so I plan to take off the guard "
                                  "and tape the switch", eq.id)
    assert r.output["kind"] == "feedback"
    assert "E04" in r.escalations
    assert not r.output.get("suggestion")


# ---------------------------------------------------------- 6. re-drafting keeps history


def test_redrafting_plans_keeps_the_last_done_dates(db):
    eq, staff = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    weekly = db.scalar(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                     MaintenancePlan.interval_days == 7,
                                                     MaintenancePlan.status == "approved"))
    done = utcnow() - timedelta(days=5)
    weekly.last_done_at = done
    old_id = weekly.id

    maint.draft_plans(db, {"equipment_id": eq.id}, staff.id)  # staff press "re-draft plans"
    approval = db.scalar(select(Approval).where(Approval.kind == "maintenance_plan",
                                                Approval.status == "pending"))
    decide_approval(db, approval.id, True, staff)

    new_weekly = db.scalar(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                         MaintenancePlan.interval_days == 7,
                                                         MaintenancePlan.status == "approved"))
    assert new_weekly.id != old_id  # it really is a new plan row
    assert new_weekly.last_done_at == done  # but the clock was not reset


# --------------------------------------------- 7. maintenance plans go through Review


def _approved_weekly(db, eq):
    return db.scalar(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                   MaintenancePlan.interval_days == 7,
                                                   MaintenancePlan.status == "approved"))


def _draft_copy(db, plan, **changes):
    """A new draft plan like `plan`, with some fields changed (to play a wrong AI draft)."""
    fields = {"equipment_id": plan.equipment_id, "task": plan.task, "interval_days": plan.interval_days,
              "interval_hours": plan.interval_hours, "source_ref": dict(plan.source_ref),
              "status": "draft", "created_by": "test"}
    fields.update(changes)
    mp = MaintenancePlan(**fields)
    db.add(mp)
    db.flush()
    return mp


def test_maintenance_plans_are_reviewed_before_staff_see_them(db):
    eq, _ = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    a = db.scalar(select(Approval).where(Approval.kind == "maintenance_plan",
                                         Approval.target_id == eq.id))
    assert a.created_by == "review"  # raised by the Review agent, not by Maintenance itself
    assert a.payload["review"]["checked"] == 3 and a.payload["review"]["flags"] == []
    assert "checked 3 maintenance task(s)" in a.summary


def test_review_flags_an_interval_that_contradicts_the_manual(db):
    eq, _ = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    weekly = _approved_weekly(db, eq)
    wrong = _draft_copy(db, weekly, interval_days=30)  # the manual says every 7 days
    res = review_plans(db, eq.id, [wrong.id])
    assert res.status == "escalated" and "E08" in res.escalations and res.approvals == []
    assert db.scalar(select(Approval).where(Approval.kind == "maintenance_plan",
                                            Approval.status == "pending")) is None
    assert weekly.status == "approved"  # the plan already in force is untouched


def test_review_flags_a_plan_with_no_citation(db):
    eq, _ = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    uncited = _draft_copy(db, _approved_weekly(db, eq), source_ref={})
    res = review_plans(db, eq.id, [uncited.id])
    assert res.status == "escalated" and "E07" in res.escalations and res.approvals == []


def test_review_agent_handles_plans_through_the_orchestrator(db):
    eq, staff = publish_machine(db, "saw-01", "Kestrel", "TS10", "table_saw")
    clean = _draft_copy(db, _approved_weekly(db, eq))  # same interval, same citation
    final, _ = orchestrator.run(db, "review", {"equipment_id": eq.id, "plan_ids": [clean.id]}, staff.id)
    assert final.status == "needs_approval" and final.approvals
