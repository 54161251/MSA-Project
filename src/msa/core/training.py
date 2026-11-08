"""Training record state machine (Assumptions doc C6).

not_started -> quiz_passed -> (pending_check) -> certified -> expired; revoked any time.
Records are history: each change adds a row; the newest row is current.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core.contracts import TrainingState
from msa.core.db import utcnow
from msa.core.models import Equipment, TrainingRecord
from msa.core.services import ServiceError, audit

ALLOWED = {
    "not_started": {"quiz_passed", "revoked"},
    "quiz_passed": {"pending_check", "certified", "revoked", "not_started"},
    "pending_check": {"certified", "not_started", "revoked"},
    "certified": {"expired", "revoked", "quiz_passed"},
    "expired": {"quiz_passed", "revoked", "not_started"},
    "revoked": {"not_started", "quiz_passed"},
}


def current_record(db: Session, user_id: str, equipment_id: str) -> TrainingRecord | None:
    return db.scalar(
        select(TrainingRecord)
        .where(TrainingRecord.user_id == user_id, TrainingRecord.equipment_id == equipment_id)
        .order_by(TrainingRecord.created_at.desc(), TrainingRecord.id.desc())
        .limit(1)
    )


def current_state(db: Session, user_id: str, equipment_id: str, now: datetime | None = None) -> str:
    rec = current_record(db, user_id, equipment_id)
    if rec is None:
        return TrainingState.NOT_STARTED.value
    now = now or utcnow()
    if rec.state == "certified" and rec.expires_at and rec.expires_at <= now:
        return TrainingState.EXPIRED.value
    return rec.state


def set_state(
    db: Session,
    user_id: str,
    equipment_id: str,
    state: str,
    actor: str,
    reason: str = "",
    quiz_score: float | None = None,
    now: datetime | None = None,
) -> TrainingRecord:
    state = TrainingState(state).value
    old = current_state(db, user_id, equipment_id, now)
    if state != old and state not in ALLOWED.get(old, set()):
        raise ServiceError(f"Training can't go from {old} to {state}")
    if state == "revoked" and not reason.strip():
        raise ServiceError("Revoking training needs a reason")
    now = now or utcnow()
    prev = current_record(db, user_id, equipment_id)
    rec = TrainingRecord(
        user_id=user_id, equipment_id=equipment_id, state=state, reason=reason,
        created_by=actor, created_at=now,
        quiz_score=quiz_score if quiz_score is not None else (prev.quiz_score if prev else None),
    )
    if state == "certified":
        eq = db.get(Equipment, equipment_id)
        months = eq.training_valid_months if eq else 12
        rec.certified_at = now
        rec.expires_at = now + timedelta(days=round(30.44 * months))
        rec.checked_by = actor
    db.add(rec)
    db.flush()
    audit(db, actor, "training.state", "training", f"{user_id}:{equipment_id}",
          {"state": old}, {"state": state, "reason": reason})
    return rec
