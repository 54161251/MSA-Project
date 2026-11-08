"""Registers the briefing route."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from msa.agents.briefing import agent
from msa.core.auth import require_staff
from msa.core.db import get_db
from msa.core.models import User
from msa.core.registry import registry

router = APIRouter(prefix="/briefing", tags=["briefing"])


@router.get("")
def briefing(staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    return agent.run(db, {}, staff.id).model_dump()


registry.router(router)
