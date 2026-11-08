"""Read helpers for documents and citations, shared by every agent.

G4: members only ever see approved content. Use `approved_docs()` for anything member-facing.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core.contracts import Citation, DocStatus
from msa.core.models import Document

PAGE_RE = re.compile(r"^\s*\[p\.(\d+)\]\s*$", re.M)
HEADING_RE = re.compile(r"^#{1,4}\s+(.*)$", re.M)


def approved_docs(
    db: Session, equipment_id: str | None = None, types: list[str] | None = None
) -> list[Document]:
    q = select(Document).where(Document.status == DocStatus.APPROVED.value)
    if equipment_id is not None:
        q = q.where(Document.equipment_id == equipment_id)
    if types:
        q = q.where(Document.type.in_(types))
    return list(db.scalars(q.order_by(Document.type, Document.version.desc())).all())


def latest(db: Session, equipment_id: str, doc_type: str, status: str | None = None) -> Document | None:
    q = select(Document).where(Document.equipment_id == equipment_id, Document.type == doc_type)
    if status:
        q = q.where(Document.status == status)
    return db.scalar(q.order_by(Document.version.desc(), Document.created_at.desc()).limit(1))


def sections(doc: Document) -> list[dict]:
    """Split a manual-style body into sections with page and heading locators.

    Manuals mark pages with a line `[p.3]` and sections with markdown headings.
    Returns [{"locator": "p.3 Material rules", "page": 3, "heading": ..., "text": ...}].
    """
    out: list[dict] = []
    page, heading, buf = 1, doc.title or "Document", []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            out.append({"locator": f"p.{page} {heading}", "page": page, "heading": heading,
                        "text": text})

    for line in doc.body.splitlines():
        pm = PAGE_RE.match(line)
        hm = HEADING_RE.match(line)
        if pm:
            flush()
            buf = []
            page = int(pm.group(1))
        elif hm:
            flush()
            buf = []
            heading = hm.group(1).strip()
        else:
            buf.append(line)
    flush()
    return out


def citation_for(doc: Document, section: dict, quote: str = "") -> Citation:
    q = quote or _first_sentence(section["text"])
    return Citation(document_id=doc.id, version=doc.version, locator=section["locator"],
                    quote=_clip(q, 40))


def verify_citation(db: Session, c: Citation | dict, require_approved: bool = True) -> str | None:
    """Return None if the citation points at real text, else a reason string."""
    c = c if isinstance(c, Citation) else Citation(**c)
    doc = db.get(Document, c.document_id)
    if doc is None:
        return "cited document does not exist"
    if require_approved and doc.status != DocStatus.APPROVED.value and doc.type != "manual":
        return "cited document is not approved"
    if doc.version != c.version:
        return f"cited version {c.version} but document is version {doc.version}"
    secs = sections(doc)
    match = [s for s in secs if s["locator"] == c.locator]
    if not match:
        return f"locator {c.locator!r} not found"
    if c.quote and _norm(c.quote) not in _norm(match[0]["text"]):
        return "quote not found at that locator"
    return None


def _first_sentence(text: str) -> str:
    t = " ".join(text.split())
    m = re.search(r"(.+?[.!?])(\s|$)", t)
    return m.group(1) if m else t


def _clip(text: str, words: int) -> str:
    parts = text.split()
    return " ".join(parts[:words])


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split())
