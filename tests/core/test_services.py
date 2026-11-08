import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from msa.core import training
from msa.core.models import (
    AuditEvent,
    Escalation,
    InventoryItem,
    InventoryTxn,
    LogEntry,
    Notification,
    Report,
)
from msa.core.registry import registry
from msa.core.services import (
    ServiceError,
    decide_approval,
    escalate,
    notify,
    request_approval,
    set_machine_status,
)
from msa.core.testing import make_equipment, make_user


def test_notify_dedups_while_open(db):
    a = notify(db, "staff", "feedback:laser-01:not-firing", "Laser not firing")
    b = notify(db, "staff", "feedback:laser-01:not-firing", "Laser not firing again")
    assert a.id == b.id and b.count == 2
    assert db.scalar(select(Notification).where(Notification.dedup_key.like("feedback%"))).count == 2


def test_append_only_tables_reject_update_and_delete(db):
    eq = make_equipment(db, "saw-01")
    db.add(LogEntry(equipment_id=eq.id, kind="maintenance", notes="blade changed"))
    item = InventoryItem(name="Acrylic 3 mm")
    db.add(item)
    db.flush()
    db.add(InventoryTxn(item_id=item.id, delta=5))
    db.flush()
    for table in ("log_entries", "inventory_txns", "audit_events"):
        if table == "audit_events":
            set_machine_status(db, eq.id, "out_of_service", "test", "t")
        with pytest.raises(DatabaseError):
            db.execute(text(f"UPDATE {table} SET created_by='x'"))
        with pytest.raises(DatabaseError):
            db.execute(text(f"DELETE FROM {table}"))


def test_report_facts_frozen_but_status_can_change(db):
    r = Report(kind="feedback", text="Printer jammed")
    db.add(r)
    db.flush()
    db.execute(text("UPDATE reports SET status='resolved'"))
    with pytest.raises(DatabaseError):
        db.execute(text("UPDATE reports SET text='edited'"))


def test_approval_runs_registered_handler_and_needs_reason_to_reject(db):
    staff = make_user(db, role="staff")
    member = make_user(db)
    called = []
    registry.on_approve("status_override")(lambda d, a, s: called.append(a.target_id))
    a = request_approval(db, "status_override", "laser-01", {}, "test", "check")
    with pytest.raises(ServiceError):
        decide_approval(db, a.id, True, member)  # members can't decide
    with pytest.raises(ServiceError):
        decide_approval(db, a.id, False, staff, "")  # rejection needs reason
    decide_approval(db, a.id, True, staff)
    assert called == ["laser-01"] and a.status == "approved"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "approval.approved"))


def test_escalation_dedups_and_unknown_code_rejected(db):
    e1 = escalate(db, "E01", "equipment", "laser-01", "PVC asked about")
    e2 = escalate(db, "E01", "equipment", "laser-01", "PVC again")
    assert e1.id == e2.id
    assert len(db.scalars(select(Escalation)).all()) == 1
    with pytest.raises(KeyError):
        escalate(db, "E99")


def test_status_override_needs_reason(db):
    make_equipment(db, "cnc-01")
    with pytest.raises(ServiceError):
        set_machine_status(db, "cnc-01", "out_of_service", "", "staff", override=True)
    eq = set_machine_status(db, "cnc-01", "out_of_service", "spindle noise", "staff", override=True)
    assert eq.status == "out_of_service"


def test_training_state_machine_and_expiry(db):
    from datetime import timedelta

    from msa.core.db import utcnow

    eq = make_equipment(db, "drill-01", training_valid_months=1)
    u = make_user(db)
    with pytest.raises(ServiceError):
        training.set_state(db, u.id, eq.id, "certified", "t")  # must pass quiz first
    training.set_state(db, u.id, eq.id, "quiz_passed", "t", quiz_score=0.9)
    rec = training.set_state(db, u.id, eq.id, "certified", "staff")
    assert training.current_state(db, u.id, eq.id) == "certified"
    later = rec.expires_at + timedelta(days=1)
    assert training.current_state(db, u.id, eq.id, now=later) == "expired"
    with pytest.raises(ServiceError):
        training.set_state(db, u.id, eq.id, "revoked", "staff", reason="")
    assert utcnow()
