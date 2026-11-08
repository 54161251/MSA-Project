"""Registers the Quiz agent: routes, approvals, and auto-generation when docs publish."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.quiz import agent
from msa.core import orchestrator, training
from msa.core.auth import current_user, require_staff
from msa.core.db import get_db
from msa.core.models import Document, QuizBank, User, to_dict
from msa.core.registry import registry

router = APIRouter(prefix="/quiz", tags=["quiz"])


class GenerateIn(BaseModel):
    guidelines: str = ""


class SubmitIn(BaseModel):
    answers: dict[str, int]


@router.post("/{equipment_id}/generate")
def generate(equipment_id: str, body: GenerateIn, staff: User = Depends(require_staff),
             db: Session = Depends(get_db)):
    final, _ = orchestrator.run(db, "quiz", {"equipment_id": equipment_id,
                                             "guidelines": body.guidelines}, staff.id)
    return final.model_dump()


@router.get("/{equipment_id}/banks")
def banks(equipment_id: str, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = select(QuizBank).where(QuizBank.equipment_id == equipment_id).order_by(QuizBank.version.desc())
    return [to_dict(b) for b in db.scalars(q).all()]


@router.post("/{equipment_id}/start")
def start(equipment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    attempt, questions = agent.start_attempt(db, user, equipment_id)
    return {"attempt_id": attempt.id, "questions": questions}


@router.post("/attempts/{attempt_id}/submit")
def submit(attempt_id: str, body: SubmitIn, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    return agent.submit_attempt(db, user, attempt_id, body.answers)


registry.router(router)


@registry.on_approve("quiz_bank")
def _approve_bank(db, approval, staff) -> None:
    bank = db.get(QuizBank, approval.target_id)
    if bank is None:
        return
    for old in db.scalars(select(QuizBank).where(QuizBank.equipment_id == bank.equipment_id,
                                                 QuizBank.status == "approved")).all():
        old.status = "retired"
    bank.status = "approved"


@registry.on_reject("quiz_bank")
def _reject_bank(db, approval, staff) -> None:
    bank = db.get(QuizBank, approval.target_id)
    if bank:
        bank.status = "rejected"


@registry.on_approve("competency_check")
def _passed_check(db, approval, staff) -> None:
    user_id, equipment_id = approval.target_id.split(":", 1)
    training.set_state(db, user_id, equipment_id, "certified", staff.id,
                       reason="in-person competency check passed")


@registry.on_reject("competency_check")
def _failed_check(db, approval, staff) -> None:
    user_id, equipment_id = approval.target_id.split(":", 1)
    training.set_state(db, user_id, equipment_id, "not_started", staff.id,
                       reason=f"competency check not passed: {approval.reason}")


@registry.on_event("doc.published")
def _auto_generate(db: Session, document) -> None:
    """Once a machine has both an approved SOP and an approved materials list (and no newer
    draft of either waiting), draft a fresh quiz bank for staff to approve."""
    if document.type not in ("sop", "materials") or not document.equipment_id:
        return
    eid = document.equipment_id
    approved = {d.type for d in db.scalars(select(Document).where(
        Document.equipment_id == eid, Document.status == "approved")).all()}
    waiting = {d.type for d in db.scalars(select(Document).where(
        Document.equipment_id == eid, Document.status == "in_review")).all()}
    if {"sop", "materials"} <= approved and not ({"sop", "materials"} & waiting):
        agent.generate(db, {"equipment_id": eid}, "quiz")
