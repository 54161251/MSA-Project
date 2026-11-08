"""Helpers for tests and for building a role against fakes before other parts merge.

    db = fresh_db()                       # in-memory database with every table
    staff = make_user(db, role="staff")
    eq = make_equipment(db, "laser-01", category="laser_cutter")
    doc = make_approved_doc(db, eq.id, "sop", "# SOP\n[p.1]\n## PPE\nWear laser goggles.")
    set_llm(ScriptedLLM({...}))           # control LLM answers (see msa.core.llm)
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from msa.core import db as dbmod
from msa.core.auth import create_user
from msa.core.models import Document, Equipment, MaterialRule, User

os.environ.setdefault("MSA_CHECKPOINT_DB", "memory")

_counter = 0


def fresh_db() -> Session:
    dbmod.configure("sqlite://")
    dbmod.init_db(drop=True)
    return dbmod.SessionLocal()


def make_user(
    db: Session, role: str = "member", birth_year: int = 1995, name: str | None = None,
    guardian: User | None = None, password: str = "password123",
) -> User:
    global _counter
    _counter += 1
    return create_user(db, name or f"User {_counter}", f"user{_counter}@example.org", birth_year,
                       password, role=role, guardian=guardian, agreed_to_terms=True,
                       actor="test")


def make_equipment(db: Session, eid: str, name: str | None = None, **kw) -> Equipment:
    eq = Equipment(id=eid, name=name or eid, created_by="test", **kw)
    db.add(eq)
    db.flush()
    return eq


def make_approved_doc(
    db: Session, equipment_id: str | None, doc_type: str, body: str, title: str = "",
    statements: list | None = None,
) -> Document:
    d = Document(equipment_id=equipment_id, type=doc_type, title=title or doc_type, body=body,
                 status="approved", statements=statements or [], created_by="test")
    db.add(d)
    db.flush()
    return d


def make_material_rule(db: Session, equipment_id: str, material: str, verdict: str,
                       reason: str = "") -> MaterialRule:
    r = MaterialRule(equipment_id=equipment_id, material=material, verdict=verdict,
                     reason=reason, created_by="test")
    db.add(r)
    db.flush()
    return r
