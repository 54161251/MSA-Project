"""Registers the Ask agent route."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from msa.agents.ask import agent  # noqa: F401  (registers "ask")
from msa.core import orchestrator
from msa.core.auth import current_user
from msa.core.db import get_db
from msa.core.models import User
from msa.core.registry import registry

router = APIRouter(prefix="/ask", tags=["ask"])


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    equipment_id: str | None = None


@router.post("")
def ask(body: AskIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    final, _ = orchestrator.run(db, "ask", {"user_id": user.id, "question": body.question,
                                            "equipment_id": body.equipment_id}, user.id)
    return final.model_dump()


registry.router(router)
