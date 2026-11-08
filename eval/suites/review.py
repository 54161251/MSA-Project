"""Review suite: SOP drafts with planted errors, plus clean controls.

Metrics: catch rate (planted error flagged with the expected code), false-alarm rate (clean
drafts that got any flag).
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from eval.suites.common import pct, summarize
from msa.agents.review.agent import review_document
from msa.core.db import session_scope
from msa.core.docs import approved_docs
from msa.core.models import Document

SUITE = "review"


def _base(db, machine: str) -> tuple[list[dict], Document]:
    sop = approved_docs(db, machine, ["sop"])[0]
    manual = db.scalar(select(Document).where(Document.equipment_id == machine,
                                              Document.type == "manual",
                                              Document.status == "approved"))
    return copy.deepcopy(sop.statements), manual


def _cite_section(manual: Document, heading_start: str) -> list[dict]:
    from msa.core.docs import citation_for, sections

    for s in sections(manual):
        if s["heading"].lower().startswith(heading_start.lower()):
            return [citation_for(manual, s).model_dump()]
    return []


def apply_plant(db, machine: str, plant: dict) -> list[dict]:
    sts, manual = _base(db, machine)
    kind = plant.get("type", "none")
    if kind == "remove_kind":
        sts = [s for s in sts if s["kind"] != plant["kind"]]
    elif kind == "remove_section":
        sts = [s for s in sts if s["section"] != plant["section"]]
    elif kind == "add":
        cites = _cite_section(manual, plant["cite_heading"]) if plant.get("cite_heading") else []
        sts.append({"section": plant["section"], "kind": plant["kind"], "text": plant["text"],
                    "citations": cites})
    elif kind == "edit":
        for s in sts:
            if plant["find"] in s["text"]:
                s["text"] = s["text"].replace(plant["find"], plant["replace"])
                break
        else:
            raise ValueError(f"text {plant['find']!r} not found in {machine} SOP")
    elif kind == "staff_note":
        db.add(Document(equipment_id=machine, type="staff_note", title="Eval house rule",
                        status="approved", body=f"[p.1]\n## House rules\n- {plant['note']}",
                        created_by="eval"))
        db.flush()
    return sts


def run_suite(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        with session_scope() as db:
            if not approved_docs(db, c["machine"], ["sop"]):
                rows.append({"id": c["id"], "expected": c["expect"], "ok": False,
                             "got": "no approved SOP to start from (see build report)",
                             "planted": c["expect"] != "none"})
                continue
            sts = apply_plant(db, c["machine"], c.get("plant", {}))
            draft = Document(equipment_id=c["machine"], type="sop", title=f"eval {c['id']}",
                             status="in_review", statements=sts, created_by="eval")
            db.add(draft)
            db.flush()
            r = review_document(db, draft)
            codes = sorted({f["code"] for f in r["issues"] + r["doc_level"]})
            exp = c["expect"]
            ok = (not codes) if exp == "none" else (exp in codes)
            rows.append({"id": c["id"], "expected": exp, "got": codes or "none", "ok": ok,
                         "planted": exp != "none"})
            db.rollback()
    planted = [r for r in rows if r["planted"]]
    clean = [r for r in rows if not r["planted"]]
    return summarize(rows, {
        "catch_rate": pct(sum(r["ok"] for r in planted), len(planted)),
        "false_alarm_rate": pct(sum(not r["ok"] for r in clean), len(clean)),
    })
