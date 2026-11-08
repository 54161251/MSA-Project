"""Registers the Review agent and a route to re-run it on a document."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from msa.agents.review import agent  # noqa: F401  (registers "review")
from msa.core import orchestrator
from msa.core.auth import require_staff
from msa.core.db import get_db
from msa.core.models import Document, User
from msa.core.registry import registry

router = APIRouter(prefix="/review", tags=["review"])


@router.post("/{doc_id}")
def rerun(doc_id: str, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if d is None:
        raise HTTPException(404, "No such document")
    final, results = orchestrator.run(db, "review", {"doc_ids": [doc_id]}, staff.id)
    return {"final": final.model_dump(), "steps": [r.model_dump() for r in results]}


registry.router(router)
