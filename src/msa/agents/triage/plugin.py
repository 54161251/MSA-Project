"""Registers Triage routes: report an issue, list reports, resolve a cluster."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.triage import agent
from msa.core import orchestrator
from msa.core.auth import current_user, require_staff
from msa.core.db import get_db
from msa.core.models import Report, User, to_dict
from msa.core.registry import registry
from msa.core.services import audit, set_machine_status

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportIn(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    equipment_id: str | None = None
    kind: str | None = Field(default=None, description="optional hint: feedback | incident | near_miss")


class ResolveIn(BaseModel):
    note: str = Field(min_length=1)
    set_status: str | None = Field(default=None, description="e.g. available, out_of_service")


@router.post("")
def submit(body: ReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    final, _ = orchestrator.run(db, "triage", {"user_id": user.id, "text": body.text,
                                               "equipment_id": body.equipment_id,
                                               "kind_hint": body.kind}, user.id)
    return final.model_dump()


@router.get("")
def list_reports(status: str = "open", user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    q = select(Report).order_by(Report.created_at.desc()).limit(300)
    if status != "all":
        q = q.where(Report.status == status)
    if user.role != "staff":
        q = q.where(Report.user_id == user.id)
    return [to_dict(r) for r in db.scalars(q).all()]


@router.post("/{report_id}/resolve")
def resolve(report_id: str, body: ResolveIn, staff: User = Depends(require_staff),
            db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "No such report")
    n = agent.resolve_cluster(db, r, staff.id, body.note)
    if body.set_status and r.equipment_id:
        set_machine_status(db, r.equipment_id, body.set_status, f"Staff after report: {body.note}",
                           staff.id, override=True)
    audit(db, staff.id, "report.resolved", "report", r.id, None, {"cluster": r.cluster_id, "count": n})
    return {"resolved": n}


registry.router(router)
