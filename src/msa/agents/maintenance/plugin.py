"""Registers Maintenance: routes, plan approval, and drafting plans when an SOP publishes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.maintenance import agent
from msa.core.auth import current_user, require_staff
from msa.core.db import get_db, utcnow
from msa.core.models import Equipment, LogEntry, MaintenancePlan, User, to_dict
from msa.core.registry import registry

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


class LogIn(BaseModel):
    kind: str = "maintenance"
    notes: str = ""
    plan_id: str | None = None
    corrects_id: str | None = None


@router.get("/{equipment_id}")
def overview(equipment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        raise HTTPException(404, "No such equipment")
    plans = db.scalars(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                     MaintenancePlan.status.in_(["approved", "draft"]))).all()
    logs = db.scalars(select(LogEntry).where(LogEntry.equipment_id == eq.id)
                      .order_by(LogEntry.created_at.desc()).limit(100)).all()
    now = utcnow()
    return {"plans": [{**to_dict(p), "due": agent.is_due(p, eq, now)[0],
                       "why": agent.is_due(p, eq, now)[2]} for p in plans],
            "log": [to_dict(entry) for entry in logs]}


@router.get("")
def due_now(staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    now = utcnow()
    out = []
    for p in db.scalars(select(MaintenancePlan).where(MaintenancePlan.status == "approved")).all():
        eq = db.get(Equipment, p.equipment_id)
        if eq and agent.is_due(p, eq, now)[0]:
            out.append({**agent.work_order(db, p), "equipment_name": eq.name,
                        "why": agent.is_due(p, eq, now)[2]})
    return out


@router.post("/{equipment_id}/log")
def log(equipment_id: str, body: LogIn, staff: User = Depends(require_staff),
        db: Session = Depends(get_db)):
    entry = agent.log_work(db, equipment_id, body.kind, body.notes, staff.id, body.plan_id,
                           body.corrects_id)
    return to_dict(entry)


@router.post("/{equipment_id}/draft-plans")
def redraft(equipment_id: str, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    return agent.draft_plans(db, {"equipment_id": equipment_id}, staff.id).model_dump()


registry.router(router)


@registry.on_approve("maintenance_plan")
def _approve(db, approval, staff) -> None:
    agent.approve_plans(db, approval.target_id, approval.payload.get("plan_ids", []), staff.id)


@registry.on_reject("maintenance_plan")
def _reject(db, approval, staff) -> None:
    for pid in approval.payload.get("plan_ids", []):
        mp = db.get(MaintenancePlan, pid)
        if mp and mp.status == "draft":
            mp.status = "retired"


@registry.on_event("doc.published")
def _plan_when_sop_publishes(db: Session, document) -> None:
    if document.type != "sop" or not document.equipment_id:
        return
    has = db.scalar(select(MaintenancePlan).where(MaintenancePlan.equipment_id == document.equipment_id,
                                                  MaintenancePlan.status.in_(["approved", "draft"])))
    if has is None:
        agent.draft_plans(db, {"equipment_id": document.equipment_id}, "maintenance")
