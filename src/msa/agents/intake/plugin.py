"""Registers Equipment Intake: routes, publish approval, and the demo seeder."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.intake import agent  # noqa: F401  (registers intake + intake_revise)
from msa.agents.intake.drafting import rule_materials, rule_verdict
from msa.agents.intake.library import read_upload, slug
from msa.core import orchestrator, training
from msa.core.auth import create_user, require_staff
from msa.core.config import DATA_DIR
from msa.core.contracts import ApprovalMode, Citation
from msa.core.db import get_db, utcnow
from msa.core.models import Approval, Document, Equipment, MaterialRule, User, to_dict
from msa.core.registry import registry
from msa.core.services import audit, decide_approval, emit, site_settings

router = APIRouter(prefix="/intake", tags=["intake"])


class NoteIn(BaseModel):
    title: str = "Staff notes"
    text: str


class IntakeIn(BaseModel):
    equipment_id: str | None = Field(default=None, description="existing equipment to re-run")
    id: str | None = Field(default=None, description="id for new equipment, e.g. laser-02")
    name: str | None = None
    brand: str = ""
    model_number: str = ""
    category: str = "other"
    approval_mode: str = "competency_check"
    manual_text: str | None = None
    staff_notes: list[NoteIn] = []
    run_now: bool = Field(default=True, description="False: only register the machine (upload the "
                          "manual next with /intake/{id}/manual)")


def ensure_equipment(db: Session, body: IntakeIn, actor: str) -> Equipment:
    if body.equipment_id:
        eq = db.get(Equipment, body.equipment_id)
        if eq is None:
            raise HTTPException(404, "No such equipment")
        return eq
    if not (body.name or "").strip():
        raise HTTPException(400, "A name is needed for new equipment")
    if body.approval_mode not in {m.value for m in ApprovalMode}:
        raise HTTPException(400, "approval_mode must be auto or competency_check")
    # ids go into URLs (/intake/{id}/manual), so keep them to lowercase letters, digits and '-'
    eid = slug(body.id or body.name)[:40].strip("-")
    if not eid:
        raise HTTPException(400, "Give the machine a short id such as laser-02")
    if db.get(Equipment, eid):
        raise HTTPException(400, f"Equipment id {eid} already exists")
    eq = Equipment(id=eid, name=body.name, brand=body.brand, model_number=body.model_number,
                   category=body.category, approval_mode=body.approval_mode, created_by=actor)
    db.add(eq)
    db.flush()
    audit(db, actor, "equipment.created", "equipment", eq.id, None, to_dict(eq))
    return eq


@router.post("")
def start_intake(body: IntakeIn, staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    eq = ensure_equipment(db, body, staff.id)
    if not body.run_now:
        return {"final": {"agent": "intake", "status": "ok", "output": {"equipment_id": eq.id}},
                "steps": []}
    payload = {"equipment_id": eq.id, "manual_text": body.manual_text,
               "staff_notes": [n.model_dump() for n in body.staff_notes]}
    final, results = orchestrator.run(db, "intake", payload, staff.id)
    return {"final": final.model_dump(), "steps": [r.model_dump() for r in results]}


@router.post("/{equipment_id}/manual")
async def upload_manual(equipment_id: str, file: UploadFile = File(...), notes: str = Form(""),
                        staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        raise HTTPException(404, "No such equipment")
    try:
        text = read_upload(file.filename or "manual", await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    payload = {"equipment_id": eq.id, "manual_text": text, "manual_filename": file.filename,
               "staff_notes": [{"title": "Staff notes", "text": notes}] if notes.strip() else []}
    final, results = orchestrator.run(db, "intake", payload, staff.id)
    return {"final": final.model_dump(), "steps": [r.model_dump() for r in results]}


registry.router(router)


# ------------------------------------------------------------ publishing


@registry.on_approve("publish_doc")
def publish(db: Session, approval: Approval, staff: User) -> None:
    doc = db.get(Document, approval.target_id)
    if doc is None:
        return
    for old in db.scalars(select(Document).where(
            Document.equipment_id == doc.equipment_id, Document.type == doc.type,
            Document.status == "approved", Document.id != doc.id)).all():
        old.status = "retired"
    doc.status = "approved"
    for ref in doc.source_refs or []:
        src = db.get(Document, ref.get("document_id", ""))
        if src is not None and src.status == "in_review" and src.type == "manual":
            src.status = "approved"  # staff approving the SOP also confirms its source manual
    if doc.type == "materials":
        rebuild_material_rules(db, doc, staff.id)
    audit(db, staff.id, "document.published", "document", doc.id, None,
          {"type": doc.type, "version": doc.version})
    emit(db, "doc.published", document=doc)


@registry.on_reject("publish_doc")
def reject(db: Session, approval: Approval, staff: User) -> None:
    doc = db.get(Document, approval.target_id)
    if doc is not None:
        doc.status = "rejected"


def _from_staff(db: Session, st: dict) -> bool:
    for c in st.get("citations") or []:
        src = db.get(Document, c.get("document_id", "")) if isinstance(c, dict) else None
        if src is not None and src.type == "staff_note":
            return True
    return False


def rebuild_material_rules(db: Session, doc: Document, actor: str) -> int:
    """One rule per material from the approved materials list.

    * Only the line's subject counts ("Never: metal with the wood blade" bans metals, not wood).
    * Staff house rules come before the manual's lines, so a house rule wins.
    * Otherwise the first line about a material sets its verdict; a later line that disagrees is
      kept in the rule's reason ("Exception: ...") so the member sees it when the tool answers.
    * Lines with a verdict but no known material can't be checked by the tools; they are
      listed in the audit entry so staff know they rely on people, not the material check."""
    for r in db.scalars(select(MaterialRule).where(MaterialRule.equipment_id == doc.equipment_id)).all():
        db.delete(r)
    db.flush()
    ordered = sorted(doc.statements, key=lambda s: not _from_staff(db, s))  # stable
    rules_by_mat: dict[str, MaterialRule] = {}
    unmapped: list[str] = []
    for st in ordered:
        verdict = rule_verdict(st["text"])
        if verdict is None:
            continue
        mats = rule_materials(st["text"])
        if not mats:
            unmapped.append(st["text"])
            continue
        cite = st.get("citations", [{}])[0] if st.get("citations") else {}
        src = Citation(**cite).model_dump() if cite else {}
        src["document_id"] = cite.get("document_id", doc.id) if cite else doc.id
        for mat in mats:
            rule = rules_by_mat.get(mat)
            if rule is not None:
                if rule.verdict != verdict and st["text"] not in rule.reason:
                    rule.reason = f"{rule.reason} Exception: {st['text']}"
                continue
            rule = MaterialRule(equipment_id=doc.equipment_id, material=mat, verdict=verdict,
                                reason=st["text"], source_ref=src, created_by=actor)
            db.add(rule)
            rules_by_mat[mat] = rule
    db.flush()
    audit(db, actor, "material_rules.rebuilt", "equipment", doc.equipment_id, None,
          {"document_id": doc.id, "rules": {m: r.verdict for m, r in rules_by_mat.items()},
           "not_checkable": unmapped})
    return len(rules_by_mat)


# --------------------------------------------------------------- seeding


def _load_yaml(name: str):
    p = DATA_DIR / name
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


@registry.seeder(20, "makerspace + members + equipment (via Intake)")
def seed(db: Session, approve_all: bool = False) -> None:
    from msa.core.seed import staff_user

    site = _load_yaml("makerspace.yaml") or {}
    s = site_settings(db)
    for k in ("name", "emergency_number", "emergency_note", "maintenance_grace_days", "display_units"):
        if k in site:
            setattr(s, k, site[k])
    staff = staff_user(db)
    year = utcnow().year
    members = _load_yaml("members.yaml") or []
    by_email: dict[str, User] = {}
    for m in members:
        u = db.scalar(select(User).where(User.email == m["email"]))
        if u is None:
            by = m.get("birth_year") or (year - int(m["birth_age"]))
            guardian = by_email.get(m.get("guardian_email", ""))
            u = create_user(db, m["name"], m["email"], by,
                            None if guardian else "password123", role=m.get("role", "member"),
                            guardian=guardian, agreed_to_terms=True, actor="seed")
            if m.get("waiver_status"):
                u.waiver_status = m["waiver_status"]
        by_email[u.email] = u
    for e in _load_yaml("equipment.yaml") or []:
        if db.get(Equipment, e["id"]):
            continue
        eq = Equipment(created_by="seed", **{k: v for k, v in e.items()})
        db.add(eq)
        db.flush()
        orchestrator.run(db, "intake", {"equipment_id": eq.id}, staff.id)
        if approve_all:
            approve_pending(db, staff, kinds=("publish_doc",))
    if approve_all:
        approve_pending(db, staff)  # quiz banks etc. created by publish events
    for m in members:
        u = by_email[m["email"]]
        for eid in m.get("trained_on", []):
            if training.current_state(db, u.id, eid) != "certified":
                training.set_state(db, u.id, eid, "quiz_passed", "seed", quiz_score=1.0)
                training.set_state(db, u.id, eid, "certified", staff.id, reason="seed data")


def approve_pending(db: Session, staff: User, kinds: tuple[str, ...] | None = None) -> int:
    """--approve-all: the seed staff account approves drafts. Still recorded as approvals."""
    n = 0
    for _ in range(5):  # approving can create new approvals (e.g. publish -> quiz bank)
        q = select(Approval).where(Approval.status == "pending")
        if kinds:
            q = q.where(Approval.kind.in_(kinds))
        pending = [a for a in db.scalars(q).all() if a.kind in registry.approve_handlers]
        pending = [a for a in pending if a.kind not in ("competency_check", "project_check",
                                                         "order_draft")]
        if not pending:
            break
        for a in pending:
            decide_approval(db, a.id, True, staff, "approved by seed (--approve-all)")
            n += 1
    return n


def library_path() -> Path:
    return DATA_DIR / "manuals"
