"""Ask agent.

Order of checks (each can end the request):
  1. Emergency (G5): urgent words -> emergency info first, staff alerted (E10). No LLM.
  2. High-risk request (E04): bypassing guards, modifying machines, mains work, unattended
     running -> refuse, find staff.
  3. Material questions ("can I cut PVC on the laser?") -> the check_material tool answers.
  4. How-to questions about a machine -> the member must be allowed to use it (E05/E06).
  5. Retrieve passages from APPROVED docs only. No good passage -> refuse (E03).
  6. Answer from the passages with citations (LLM, or the passages themselves offline).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core import escalations
from msa.core.contracts import AgentResult, Citation
from msa.core.docs import approved_docs, citation_for, sections
from msa.core.llm import as_bool, get_llm
from msa.core.models import Equipment
from msa.core.registry import registry
from msa.core.services import escalate, site_settings
from msa.core.textsearch import Index, tokens
from msa.safety_tools import tools
from msa.safety_tools.rules import find_categories, find_materials

MIN_SCORE = 2.0  # BM25 score below which a passage doesn't count as an answer
URGENT = ["on fire", "is burning", "caught fire", "flames", "there's smoke", "smoke is coming",
          "smoking", "i cut myself", "cut my", "bleeding", "i burned", "burned my", "burnt my",
          "got shocked", "electric shock", "shocked me", "hurt myself", "got hurt", "is hurt",
          "am hurt", "injured", "unconscious", "can't breathe", "cannot breathe", "in my eye",
          "in my eyes", "help!", "smoke in the", "smoke from", "smoke is filling", "caught in the",
          "got caught in", "caught my", "electrocuted", "fainted", "passed out", "broke my",
          "broken bone", "blood", "bleed", "seizure", "choking", "not breathing", "chest pain",
          "heart attack", "call 911", "call an ambulance", "need an ambulance", "amputat",
          "crushed", "sliced", "gashed", "splashed in", "sparks and", "burned me", "burnt me"]
HOWTO = re.compile(r"\b(how (do|can|should) i|how to|can i|steps to|walk me through|what settings|"
                   r"what do i do|where do i start|get started|i want to (use|run|start|operate))\b",
                   re.I)
MATERIAL_Q = re.compile(r"\b(can i|can we|is it ok|is it okay|is it safe|am i allowed|okay to|ok to)\b.*"
                        r"\b(cut|engrave|etch|print|use|mill|drill|saw|sew|solder|route|machine|rip|laser|burn|carve|"
                        r"put|run|feed|process)\b",
                        re.I)


def is_emergency(text: str) -> bool:
    t = text.lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t) for w in URGENT)


def emergency_payload(db: Session) -> dict[str, Any]:
    s = site_settings(db)
    out = {"emergency_number": s.emergency_number, "note": s.emergency_note,
           "message": f"If anyone is hurt or there is a fire, call {s.emergency_number} now. "
                      "Staff have been alerted."}
    loc = nearest_safety_gear(db)
    if loc:
        out["safety_gear"] = loc
    return out


def nearest_safety_gear(db: Session) -> list[str]:
    try:
        from msa.core.models import PlacedItem

        items = db.scalars(select(PlacedItem).where(PlacedItem.kind == "safety")).all()
        return [f"{i.label or i.safety_type}" for i in items
                if i.safety_type in ("first_aid", "extinguisher", "eyewash")]
    except Exception:
        return []


def detect_equipment(db: Session, question: str, equipment_id: str | None) -> Equipment | None:
    if equipment_id:
        return db.get(Equipment, equipment_id)
    machines = db.scalars(select(Equipment).where(Equipment.status != "retired")).all()
    q = question.lower()
    for m in machines:
        short = m.name.split(" (")[0].lower()
        if m.id in q or short in q or (m.model_number and m.model_number.lower() in q):
            return m
    cats = find_categories(question)
    if cats:
        return next((m for m in machines if m.category == cats[0]), None)
    return None


def passages(db: Session, equipment: Equipment | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    docs = approved_docs(db, equipment.id if equipment else None,
                         ["sop", "training", "materials", "staff_note", "manual"])
    for d in docs:
        if d.type in ("sop", "training", "materials"):
            for st in d.statements:
                if st.get("citations"):
                    out.append({"text": f"{st.get('section', '')}: {st['text']}", "kind": st.get("kind"),
                                "citations": st["citations"], "doc_type": d.type,
                                "equipment_id": d.equipment_id})
        else:
            for sec in sections(d):
                out.append({"text": sec["text"], "kind": "manual",
                            "citations": [citation_for(d, sec).model_dump()],
                            "doc_type": d.type, "equipment_id": d.equipment_id})
    return out


SYSTEM = (
    "You answer a makerspace member's question using ONLY the numbered passages. Use short, plain "
    "sentences. Cite the passage numbers you used. If the passages don't answer the question, "
    'set answerable=false. Return {"answerable": bool, "answer": str, "used": [passage numbers]}'
)


def refuse(code: str, message: str | None = None, **out) -> AgentResult:
    meta = escalations.get(code)
    return AgentResult(agent="ask", status="refused", escalations=[code],
                       output={"answer": message or meta.member_message, "refused": True, **out})


@registry.agent("ask")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    user_id = payload.get("user_id") or actor
    question = (payload.get("question") or "").strip()
    trace: list[str] = []
    if not question:
        return AgentResult(agent="ask", status="refused", output={"answer": "Please type a question."})

    if is_emergency(question):
        escalate(db, "E10", "ask", user_id, f"Emergency words in a question: {question[:200]}", "ask")
        return AgentResult(agent="ask", status="escalated", escalations=["E10"],
                           output={"emergency": True, **emergency_payload(db),
                                   "answer": emergency_payload(db)["message"]},
                           trace=["rule:emergency"])

    risk = escalations.match_high_risk(question)
    if risk:
        escalate(db, "E04", "ask", user_id, f"{', '.join(risk)}: {question[:200]}", "ask")
        return refuse("E04", categories=risk)

    eq = detect_equipment(db, question, payload.get("equipment_id"))
    trace.append(f"detect_equipment -> {eq.id if eq else 'none'}")

    mats = find_materials(question)
    if eq and MATERIAL_Q.search(question) and not mats:
        escalate(db, "E01", "equipment", eq.id, f"Unrecognized material in: {question[:200]}", "ask")
        return refuse("E01", equipment_id=eq.id)
    if eq and mats and MATERIAL_Q.search(question):
        lines, cites, codes = [], [], []
        for m in mats:
            v = tools.check_material(db, eq.id, m)
            trace.append(f"tool:check_material({eq.id},{m}) -> {v.verdict}")
            label = m.replace("_", " ").upper() if len(m) <= 4 else m.replace("_", " ")
            if v.verdict == "allowed":
                limits = " ".join(v.reasons).strip()  # e.g. "Allowed: plywood and MDF up to 6 mm."
                lines.append(f"Yes, {label} is approved for the {eq.name}."
                             + (f" The manual says: {limits}" if limits else ""))
                ref = v.data.get("source_ref") or {}
                if ref.get("document_id"):
                    cites.append(Citation(**ref))
            elif v.verdict == "banned":
                lines.append(f"No. {label} must not be used on the {eq.name}. {' '.join(v.reasons)}")
            else:
                lines.append(f"{label} isn't on the approved list for the {eq.name}. Please ask staff.")
                codes.append("E01")
        if "E01" in codes:
            escalate(db, "E01", "equipment", eq.id, f"Asked about: {', '.join(mats)}", "ask")
        return AgentResult(agent="ask", status="escalated" if codes else "ok", escalations=codes,
                           citations=cites, trace=trace,
                           output={"answer": " ".join(lines), "equipment_id": eq.id,
                                   "source": "check_material"})

    if eq and HOWTO.search(question):
        v = tools.can_use(db, user_id, eq.id)
        trace.append(f"tool:can_use({eq.id}) -> {v.verdict}")
        if v.verdict != "yes":
            code = "E06" if "E06" in v.data.get("codes", []) else (
                "E13" if "E13" in v.data.get("codes", []) else "E05")
            escalate(db, code, "equipment", eq.id, f"{user_id}: {'; '.join(v.reasons)}", "ask")
            return refuse(code, f"{escalations.get(code).member_message} ({'; '.join(v.reasons)})",
                          equipment_id=eq.id, reasons=v.reasons)

    corpus = passages(db, eq)
    if not corpus:
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03")
    q_tokens = set(tokens(question))
    hits, seen_text = [], set()
    for sc, p in Index(corpus).search(question, k=12):
        key = " ".join(tokens(p["text"].split(":", 1)[-1]))
        if sc < MIN_SCORE or key in seen_text:  # the SOP and training guide share lines
            continue
        if len(q_tokens & set(tokens(p["text"]))) < min(2, len(q_tokens)):
            continue
        seen_text.add(key)
        hits.append((sc, p))
        if len(hits) == 6:
            break
    trace.append(f"retrieve -> {len(hits)} passage(s)")
    if not hits:
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03")
    numbered = "\n\n".join(f"[{i}] {p['text']}" for i, (_, p) in enumerate(hits))
    fallback = {"answerable": True, "answer": _offline_answer(hits), "used": list(range(len(hits[:2])))}
    out = get_llm().complete_json("ask.answer", SYSTEM,
                                  f"Question: {question}\n\nPassages:\n{numbered}", lambda: fallback,
                                  max_tokens=600)
    if not as_bool(out.get("answerable", False)):
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03")
    used = [i for i in out.get("used", []) if isinstance(i, int) and 0 <= i < len(hits)] or [0]
    cites = [Citation(**c) for i in used for c in hits[i][1]["citations"]]
    if not cites:
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03")
    warnings = []
    if eq:
        v = tools.can_use(db, user_id, eq.id)
        warnings = v.data.get("warnings", [])
    return AgentResult(agent="ask", status="ok", citations=_dedup(cites), trace=trace,
                       output={"answer": str(out.get("answer", "")).strip(),
                               "equipment_id": eq.id if eq else None, "warnings": warnings})


def _offline_answer(hits) -> str:
    top = [p["text"] for _, p in hits[:2]]
    return "From the approved documents: " + " ".join(t if t.endswith(".") else t + "." for t in top)


def _dedup(cites: list[Citation]) -> list[Citation]:
    seen, out = set(), []
    for c in cites:
        if (c.document_id, c.locator) not in seen:
            seen.add((c.document_id, c.locator))
            out.append(c)
    return out
