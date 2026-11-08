"""Registers Project Check: agent, routes, and what staff approval means."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.project_check import agent  # noqa: F401  (registers the agent)
from msa.core import orchestrator
from msa.core.auth import current_user
from msa.core.db import get_db
from msa.core.models import ProjectCheck, User, to_dict
from msa.core.registry import registry

router = APIRouter(prefix="/project-checks", tags=["project check"])


class CheckIn(BaseModel):
    description: str = Field(min_length=10, max_length=4000)


@router.post("")
def create_check(body: CheckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    final, results = orchestrator.run(db, "project_check",
                                      {"user_id": user.id, "description": body.description}, user.id)
    return final.model_dump()


@router.get("")
def list_checks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(ProjectCheck).order_by(ProjectCheck.created_at.desc()).limit(100)
    if user.role != "staff":
        q = q.where(ProjectCheck.user_id == user.id)
    return [to_dict(p) for p in db.scalars(q).all()]


@router.get("/{check_id}")
def get_check(check_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(ProjectCheck, check_id)
    if p is None or (user.role != "staff" and p.user_id != user.id):
        raise HTTPException(404, "No such project check")
    return to_dict(p)


registry.router(router)


@registry.on_approve("project_check")
def _cleared(db, approval, staff) -> None:
    p = db.get(ProjectCheck, approval.target_id)
    if p:
        p.status = "cleared_by_staff"


@registry.on_reject("project_check")
def _not_cleared(db, approval, staff) -> None:
    p = db.get(ProjectCheck, approval.target_id)
    if p:
        p.status = "not_cleared"
