"""Registers the safety tools with the platform: HTTP routes and the machine-use check."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from msa.core.auth import current_user, require_staff
from msa.core.db import get_db
from msa.core.models import User
from msa.core.registry import registry
from msa.core.services import ServiceError
from msa.safety_tools import tools

router = APIRouter(prefix="/tools", tags=["safety tools"])


class MaterialIn(BaseModel):
    equipment_id: str
    material: str


@router.post("/check-material")
def check_material(body: MaterialIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return tools.check_material(db, body.equipment_id, body.material).model_dump()


@router.get("/ppe/{equipment_id}")
def ppe(equipment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return tools.required_ppe(db, equipment_id).model_dump()


@router.get("/can-use/{equipment_id}")
def can_use_me(equipment_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return tools.can_use(db, user.id, equipment_id).model_dump()


@router.get("/can-use/{equipment_id}/{user_id}")
def can_use_other(equipment_id: str, user_id: str, staff: User = Depends(require_staff),
                  db: Session = Depends(get_db)):
    return tools.can_use(db, user_id, equipment_id).model_dump()


@router.get("/layout/{plan_id}")
def layout(plan_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return tools.check_layout(db, plan_id).model_dump()


registry.router(router)


@registry.on_event("usage.before_start")
def _permission_gate(db: Session, user: User, equipment) -> None:
    v = tools.can_use(db, user.id, equipment.id)
    if v.verdict != "yes":
        raise ServiceError("Not allowed yet: " + "; ".join(v.reasons))
