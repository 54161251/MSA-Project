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
from msa.safety_tools.thickness import over_thickness

MIN_SCORE = 2.0  # BM25 score below which a passage doesn't count as an answer
URGENT = ["on fire", "is burning", "caught fire", "flames", "there's smoke", "smoke is coming",
          "smoking", "i cut myself", "cut my", "bleeding", "i burned", "burned my", "burnt my",
          "got shocked", "electric shock", "shocked me", "hurt myself", "got hurt", "is hurt",
          "am hurt", "injured", "unconscious", "can't breathe", "cannot breathe", "in my eye",
          "in my eyes", "help!", "smoke in the", "smoke is filling", "caught my", "electrocuted",
          "fainted", "passed out", "broke my",
          "broken bone", "blood", "seizure", "choking", "not breathing", "chest pain",
          "heart attack", "call 911", "call an ambulance", "need an ambulance", "amputated",
          "crushed my", "got crushed", "sliced my", "sliced open", "gashed", "splashed in",
          "sparks and", "burned me", "burnt me"]
# "my arm got caught in the machine" is an emergency; "filament got caught in the extruder" is not
CAUGHT = re.compile(r"\b(arm|hand|hands|finger|fingers|thumb|hair|sleeve|shirt|clothes|clothing|"
                    r"glove|gloves|scarf|necklace|lanyard)\b[^.?!]{0,25}\b(caught|pulled into|"
                    r"sucked into|trapped|stuck) in\b", re.I)
HOWTO = re.compile(r"\b(how (do|can|should) i|how to|can i|steps to|walk me through|what settings|"
                   r"what do i do|where do i start|get started|i want to (use|run|start|operate))\b",
                   re.I)
MATERIAL_Q = re.compile(r"\b(can i|can we|is it ok|is it okay|is it safe|am i allowed|okay to|ok to)\b.*"
                        r"\b(cut|engrave|etch|print|use|mill|drill|saw|sew|solder|route|machine|rip|laser|burn|carve|"
                        r"put|run|feed|process)\b",
                        re.I)


def is_emergency(text: str) -> bool:
    t = text.lower()
    if CAUGHT.search(t):
        return True
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
    "sentences and never add a step, number, setting or safety rule that the passages don't state. "
    "If the passages answer the question, answer it. If they answer only part of it, give that "
    "part, say plainly what the documents don't cover, set partial=true and still set "
    "answerable=true. Set answerable=false only when no passage is relevant to the question. "
    "Cite the passage numbers you used. "
    'Return {"answerable": bool, "partial": bool, "answer": str, "used": [passage numbers]}'
)

# Words that shape a question but say nothing about its subject ("What do I need to know ...")
FILLER = set(tokens("need needs needed know tell want wants like get gets getting got make sure ok "
                    "okay please also just really any some thing things way ways happen happens "
                    "happened"))

PARTIAL_NOTE = "The approved documents don't cover all of this; ask a staff member for the rest."


def content_tokens(question: str, eq: Equipment | None) -> set[str]:
    """Question words that say what is being asked, not which machine it is about.

    The machine's own name ("laser cutter", "Lumenic Vector 60") is in almost every question
    about it, so counting it as a match let unrelated lines through and pushed relevant ones out
    (real Claude run, 2026-11-18: "What PPE do I need for the laser cutter?" lost every safety
    glasses line and was refused)."""
    q = {t for t in tokens(question) if len(t) > 1}  # "can't" leaves a stray "t"
    words = q - FILLER
    if eq is not None:
        words -= set(tokens(" ".join([eq.name, eq.id.replace("-", " "), eq.brand or "",
                                      eq.model_number or "", eq.category.replace("_", " ")])))
    return words or q


def refuse(code: str, message: str | None = None, trace: list[str] | None = None,
           **out) -> AgentResult:
    meta = escalations.get(code)
    return AgentResult(agent="ask", status="refused", escalations=[code], trace=trace or [],
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
        return refuse("E04", categories=risk, trace=[f"rule:high_risk {risk}"])

    eq = detect_equipment(db, question, payload.get("equipment_id"))
    trace.append(f"detect_equipment -> {eq.id if eq else 'none'}")

    mats = find_materials(question)
    if eq and MATERIAL_Q.search(question) and not mats:
        escalate(db, "E01", "equipment", eq.id, f"Unrecognized material in: {question[:200]}", "ask")
        return refuse("E01", equipment_id=eq.id, trace=trace + ["material: not recognized"])
    if eq and mats and MATERIAL_Q.search(question):
        lines, cites, codes = [], [], []
        for m in mats:
            v = tools.check_material(db, eq.id, m)
            trace.append(f"tool:check_material({eq.id},{m}) -> {v.verdict}")
            label = m.replace("_", " ").upper() if len(m) <= 4 else m.replace("_", " ")
            if v.verdict == "allowed":
                limits = " ".join(v.reasons).strip()  # e.g. "Allowed: plywood and MDF up to 6 mm."
                over = over_thickness(question, m, limits)
                if over:  # the material is allowed, but not at the size asked about
                    trace.append(f"thickness:{m} -> {over[0]} over limit {over[1]}")
                    lines.append(f"No. {label} is allowed on the {eq.name} only up to {over[1]}, "
                                 f"and {over[0]} is thicker than that. Please ask staff.")
                    codes.append("E01")
                    continue
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
                          equipment_id=eq.id, reasons=v.reasons, trace=trace)

    corpus = passages(db, eq)
    if not corpus:
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03", trace=trace + ["retrieve -> no approved documents"])
    q_tokens = content_tokens(question, eq)
    hits, seen_text = [], set()
    for sc, p in Index(corpus).search(question, k=20):
        key = " ".join(tokens(p["text"].split(":", 1)[-1]))
        if sc < MIN_SCORE or key in seen_text:  # the SOP and training guide share lines
            continue
        if len(q_tokens & set(tokens(p["text"]))) < min(2, len(q_tokens)):
            continue  # must share the question's subject words, not just the machine's name
        seen_text.add(key)
        hits.append((sc, p))
        if len(hits) == 6:
            break
    trace.append(f"retrieve -> {len(hits)} passage(s)")
    if not hits:
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03", trace=trace)
    numbered = "\n\n".join(f"[{i}] {p['text']}" for i, (_, p) in enumerate(hits))
    fallback = {"answerable": True, "partial": False, "answer": _offline_answer(hits),
                "used": list(range(len(hits[:2])))}
    out = get_llm().complete_json("ask.answer", SYSTEM,
                                  f"Question: {question}\n\nPassages:\n{numbered}", lambda: fallback,
                                  max_tokens=600)
    trace.append(f"llm:ask.answer -> answerable={out.get('answerable')} partial={out.get('partial')}")
    if not as_bool(out.get("answerable", False)):
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03", trace=trace)
    used = [i for i in out.get("used", []) if isinstance(i, int) and 0 <= i < len(hits)] or [0]
    cites = [Citation(**c) for i in used for c in hits[i][1]["citations"]]
    if not cites:
        escalate(db, "E03", "ask", user_id, question[:200], "ask")
        return refuse("E03", trace=trace + ["no citations"])
    warnings = []
    if eq:
        v = tools.can_use(db, user_id, eq.id)
        warnings = v.data.get("warnings", [])
    answer = str(out.get("answer", "")).strip()
    partial = as_bool(out.get("partial", False))
    if partial and PARTIAL_NOTE not in answer:
        answer = f"{answer} {PARTIAL_NOTE}".strip()
    return AgentResult(agent="ask", status="ok", citations=_dedup(cites), trace=trace,
                       output={"answer": answer, "partial": partial,
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
