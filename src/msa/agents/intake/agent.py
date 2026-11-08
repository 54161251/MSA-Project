"""Equipment Intake agent.

    intake         payload: {equipment_id, manual_text?, manual_filename?, staff_notes?: [{title, text}]}
    intake_revise  payload: {doc_ids, issues: [{doc_id, index, code, text}]}  (sent by Review)

Steps: find or read the manual -> store it as a source document -> extract specs (E12 when a
needed field is unclear) -> draft SOP, training doc and materials list with citations -> hand
the drafts to Review if Review is installed, otherwise ask staff to approve them directly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.intake import drafting
from msa.agents.intake.library import find_manual
from msa.agents.intake.specs import extract_specs, low_confidence
from msa.core.contracts import AgentResult, Handoff
from msa.core.docs import latest
from msa.core.models import Document, Equipment
from msa.core.registry import registry
from msa.core.services import emit, escalate, request_approval

DRAFT_TYPES = ["sop", "training", "materials"]


def _next_version(db: Session, equipment_id: str, doc_type: str) -> int:
    d = latest(db, equipment_id, doc_type)
    return (d.version + 1) if d else 1


def store_manual(db: Session, eq: Equipment, text: str, source: str, actor: str,
                 staff_supplied: bool) -> Document:
    """Save a manual as a source. Staff-supplied manuals count as approved sources; manuals the
    agent found itself stay drafts until staff approve the SOP built from them."""
    doc = Document(equipment_id=eq.id, type="manual", title=f"{eq.name} manual",
                   version=_next_version(db, eq.id, "manual"), body=text,
                   status="approved" if staff_supplied else "in_review",
                   source_refs=[{"source": source}], created_by=actor)
    db.add(doc)
    db.flush()
    eq.manual_ref = source
    return doc


def store_staff_notes(db: Session, eq: Equipment, notes: list[dict[str, str]], actor: str) -> list[Document]:
    out = []
    for n in notes:
        title = n.get("title") or "Staff notes"
        body = n.get("text", "")
        if not body.strip():
            continue
        if not body.lstrip().startswith("[p."):
            body = f"[p.1]\n## {title}\n{body}"
        d = Document(equipment_id=eq.id, type="staff_note", title=title, body=body,
                     version=_next_version(db, eq.id, "staff_note"), status="approved",
                     source_refs=[{"source": "staff", "author": actor}], created_by=actor)
        db.add(d)
        db.flush()
        out.append(d)
    return out


@registry.agent("intake")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    trace: list[str] = []
    eq = db.get(Equipment, payload.get("equipment_id", ""))
    if eq is None:
        return AgentResult(agent="intake", status="error", output={"error": "unknown equipment"})

    # 1. manual
    if payload.get("manual_text"):
        manual = store_manual(db, eq, payload["manual_text"],
                              payload.get("manual_filename") or "staff upload", actor, True)
        trace.append("manual: staff upload")
    else:
        found = find_manual(eq.brand, eq.model_number)
        if found is None:
            escalate(db, "E02", "equipment", eq.id,
                     f"No manual found for {eq.brand} {eq.model_number}; please upload one.",
                     "intake")
            return AgentResult(agent="intake", status="escalated", escalations=["E02"],
                               output={"message": "No manual found. Upload the manual to continue."},
                               trace=["tool:find_manual -> none"])
        manual = store_manual(db, eq, found.read_text(encoding="utf-8"), f"library:{found.name}",
                              actor, False)
        trace.append(f"tool:find_manual -> {found.name}")
    notes = store_staff_notes(db, eq, payload.get("staff_notes") or [], actor)

    # 2. specs
    ex = extract_specs(manual)
    trace.append("llm:intake.extract_specs")
    eq.specs = {**(eq.specs or {}), **ex.get("specs", {})}
    escalated = []
    if ex.get("footprint_m"):
        eq.footprint_w, eq.footprint_d = ex["footprint_m"]
    for field in low_confidence(ex):
        escalate(db, "E12", "equipment", eq.id,
                 f"{eq.name}: could not read '{field}' from the manual with confidence. "
                 "Please enter it on the equipment page.", "intake")
        escalated.append("E12")

    # 3. drafts
    sources = [manual, *notes]
    sop = drafting.draft_sop_statements(eq, sources)
    trace.append(f"llm:intake.draft_sop -> {len(sop)} statements")
    training = drafting.training_statements(eq, sop)
    # the materials list becomes the machine's material rules, so it is always the manual's
    # own wording (see drafting.complete_with_manual), never a paraphrase
    materials = [s for s in sop if s["kind"] == "material" and s["section"] == "Material rules"]
    drafts = []
    for dtype, title, sts in [("sop", f"SOP: {eq.name}", sop),
                              ("training", f"Training guide: {eq.name}", training),
                              ("materials", f"Allowed materials: {eq.name}", materials)]:
        d = Document(equipment_id=eq.id, type=dtype, title=title,
                     version=_next_version(db, eq.id, dtype), status="in_review", statements=sts,
                     body=drafting.render_markdown(title, sts),
                     source_refs=[{"document_id": s.id, "version": s.version, "type": s.type}
                                  for s in sources], created_by="intake")
        db.add(d)
        db.flush()
        drafts.append(d)
        emit(db, "doc.drafted", document=d)
    doc_ids = [d.id for d in drafts]
    out = {"equipment_id": eq.id, "manual_id": manual.id, "doc_ids": doc_ids,
           "specs": eq.specs, "footprint_m": ex.get("footprint_m")}

    # 4. review (if installed) or straight to staff
    if "review" in registry.agents:
        return AgentResult(agent="intake", status="ok", output=out, escalations=escalated,
                           trace=trace, handoff=Handoff(agent="review", payload={
                               "doc_ids": doc_ids, "revise_agent": "intake_revise", "cycle": 1}))
    approvals = [request_publish(db, d).id for d in drafts]
    return AgentResult(agent="intake", status="needs_approval", output=out, escalations=escalated,
                       approvals=approvals, trace=trace)


def request_publish(db: Session, doc: Document, review: dict[str, Any] | None = None):
    flags = (review or doc.review or {}).get("flags", [])
    summary = f"{doc.title} v{doc.version}: {len(doc.statements)} statements"
    if flags:
        summary += f", {len(flags)} review flag(s)"
    return request_approval(db, "publish_doc", doc.id, {"equipment_id": doc.equipment_id,
                                                        "type": doc.type}, "intake", summary)


@registry.agent("intake_revise")
def revise(db: Session, payload: dict, actor: str) -> AgentResult:
    """Fix what can be fixed without inventing content: drop statements Review flagged as
    uncited (E07), contradicting (E08) or rule-breaking (E09). Missing sections can't be fixed by
    the agent; those stay flagged for staff."""
    issues = payload.get("issues", [])
    changed = refilled = 0
    for doc_id in payload.get("doc_ids", []):
        d = db.get(Document, doc_id)
        if d is None:
            continue
        drop = {i["index"] for i in issues if i.get("doc_id") == doc_id and i.get("index") is not None
                and i.get("code") in ("E07", "E08", "E09")}
        if drop:
            d.statements = [s for k, s in enumerate(d.statements) if k not in drop]
            changed += len(drop)
        # only lines rejected for their CONTENT stay out; a line dropped for a broken citation
        # comes back with the manual's own, valid citation
        bad_content = {i.get("text", "") for i in [*issues, *payload.get("history", [])]
                       if i.get("code") in ("E08", "E09") and i.get("index") is not None}
        wanted = {i.get("section") for i in issues if i.get("doc_id") == doc_id and i.get("section")}
        refilled += refill_empty_sections(db, d, bad_content, wanted)
        d.body = drafting.render_markdown(d.title, d.statements)
    trace = [f"revise: removed {changed} flagged statement(s), refilled {refilled} from the manual"]
    return AgentResult(agent="intake_revise", status="ok", output={"removed": changed},
                       trace=trace, handoff=Handoff(agent="review", payload={
                           "doc_ids": payload.get("doc_ids", []), "revise_agent": "intake_revise",
                           "cycle": int(payload.get("cycle", 1)) + 1, "after_revision": True,
                           "history": payload.get("history", [])}))


def refill_empty_sections(db: Session, doc: Document, flagged_texts: set[str],
                          top_up: set[str] | None = None) -> int:
    """If removing flagged lines emptied a section, put back the manual's own (verbatim, cited)
    lines for that section, except any line that was flagged before. Verbatim lines always
    match their source, so this can't loop forever."""
    if doc.type not in ("sop", "materials", "training"):
        return 0
    sources = [db.get(Document, r.get("document_id", "")) for r in doc.source_refs or []]
    sources = [x for x in sources if x is not None]
    base = drafting.deterministic_statements(sources)
    if doc.type == "materials":
        base = [b for b in base if b["kind"] == "material"]
    elif doc.type == "training":
        eq = db.get(Equipment, doc.equipment_id)
        base = drafting.training_statements(eq, base)
    present = {s["section"] for s in doc.statements}
    texts = {s["text"] for s in doc.statements}
    top_up = top_up or set()
    added = 0
    for b in base:
        if b["text"] in flagged_texts or b["text"] in texts:
            continue
        if b["section"] not in present or b["section"] in top_up:
            doc.statements = [*doc.statements, b]
            texts.add(b["text"])
            added += 1
    return added


def pending_drafts(db: Session, equipment_id: str) -> list[Document]:
    return list(db.scalars(select(Document).where(Document.equipment_id == equipment_id,
                                                  Document.status == "in_review")).all())
