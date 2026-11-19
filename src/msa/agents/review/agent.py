"""Review agent: checks every draft before a human sees it.

Checks per statement:
  E07  safety statement with no citation, a citation that doesn't resolve, or a citation whose
       text doesn't support the statement
  E08  contradicts the manual or staff notes (e.g. staff note says "never MDF", draft says allowed)
  E09  breaks a deterministic rule (banned material listed as allowed)
Checks per document (SOP only):
  E09  missing required sections or required PPE terms (msa.safety_tools.check_sop_rules)

Fixable, per-statement problems go back to the drafting agent (`revise_agent`) for up to 3
revise cycles. Whatever remains is written to doc.review for the approver. Documents that still
have blocking problems are NOT sent for approval (they can't publish); staff get an escalation.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from msa.agents.intake.drafting import SAFETY_KINDS, material_clause, material_verdict
from msa.agents.maintenance.agent import parse_interval
from msa.core.contracts import AgentResult, Handoff
from msa.core.docs import approved_docs, sections, verify_citation
from msa.core.llm import as_bool, get_llm
from msa.core.models import Document, Equipment, MaintenancePlan
from msa.core.registry import registry
from msa.core.services import escalate, request_approval
from msa.core.textsearch import tokens
from msa.safety_tools import tools
from msa.safety_tools.rules import find_materials

MAX_CYCLES = 3
SUPPORT_MIN = 0.6  # share of a statement's words found in its cited passage


NEGATION = re.compile(r"\b(never|not|no|don't|do not|doesn't|cannot|can't|must not|avoid)\b", re.I)
# words that change what a safety rule means; a statement may only use them if its source does
QUALIFIERS = ["optional", "not required", "not needed", "unnecessary", "instead of", "skip",
              "by hand", "without", "only if", "faster", "for speed", "anyway", "safe to eat",
              "food safe", "is fine", "it's fine"]
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def support_score(statement: str, passage: str) -> float:
    st = set(tokens(statement))
    if not st:
        return 1.0
    ps = set(tokens(passage))
    return len(st & ps) / len(st)


def _best_sentence(statement: str, passage: str) -> str:
    sents = [x for x in re.split(r"(?<=[.!?;])\s+|\n+", passage) if x.strip()]
    return max(sents, key=lambda x: support_score(statement, x)) if sents else passage


def meaning_changed(statement: str, passage: str) -> str | None:
    """Deterministic checks a word-overlap score misses: numbers, negation, risky qualifiers."""
    low_s, low_p = statement.lower(), passage.lower()
    for n in NUMBER.findall(statement):
        if n not in NUMBER.findall(passage):
            return f"number {n} isn't in the source"
    parts = [x for x in re.split(r"(?<=[.!?;])\s+", statement) if x.strip()] or [statement]
    for part in parts:  # compare each sentence of the statement with its closest source sentence
        best = _best_sentence(part, passage)
        if bool(NEGATION.search(part)) != bool(NEGATION.search(best)):
            return "the source says the opposite (negation differs)"
    for q in QUALIFIERS:
        if q in low_s and q not in low_p:
            return f"adds '{q}', which the source doesn't say"
    return None


def _passage(db: Session, cite: dict) -> str | None:
    doc = db.get(Document, cite.get("document_id", ""))
    if doc is None:
        return None
    for s in sections(doc):
        if s["locator"] == cite.get("locator"):
            return s["text"]
    return None


SUPPORT_SYSTEM = (
    "You check whether a cited passage supports a safety statement. Answer supported=true only "
    "if the passage states the same thing. Paraphrase is fine; new facts are not. "
    'Return {"supported": true|false, "why": "..."}'
)


_support_cache: dict[tuple, tuple[bool, str]] = {}


def check_support(db: Session, text: str, cites: list[dict]) -> tuple[bool, str]:
    """Memoized: the SOP, training guide and materials list share statements, so the same
    (statement, source) pair is only judged once."""
    key = (text, tuple(sorted((c.get("document_id", ""), c.get("locator", "")) for c in cites)))
    if key not in _support_cache:
        if len(_support_cache) > 5000:
            _support_cache.clear()
        _support_cache[key] = _check_support(db, text, cites)
    return _support_cache[key]


def _check_support(db: Session, text: str, cites: list[dict]) -> tuple[bool, str]:
    passages = [p for p in (_passage(db, c) for c in cites) if p]
    if not passages:
        return False, "cited passage not found"
    norm = " ".join(tokens(text))
    if any(norm and norm in " ".join(tokens(p)) for p in passages):
        return True, "quoted from source"  # verbatim: no model call needed
    best_p = max(passages, key=lambda p: support_score(text, p))
    best = support_score(text, best_p)
    changed = meaning_changed(text, best_p)
    if changed and changed.startswith("number"):
        return False, changed  # a number that isn't in the source is never a paraphrase
    fallback = {"supported": best >= SUPPORT_MIN and not changed,
                "why": changed or f"word overlap {best:.0%}"}
    hint = (f"\nA rule-based check noticed: {changed}. Decide whether the meaning really "
            "changed; rewording the same rule (e.g. 'keep the lid closed' as 'never open the lid "
            "while it fires') is fine.") if changed else ""
    out = get_llm().complete_json("review.support", SUPPORT_SYSTEM,
                                  f"Statement: {text}\n\nPassage(s):\n" + "\n---\n".join(passages)
                                  + hint, lambda: fallback, max_tokens=300)
    return as_bool(out.get("supported")), str(out.get("why", ""))


def staff_material_verdicts(db: Session, equipment_id: str) -> dict[str, tuple[str, str]]:
    """material -> (verdict, staff note text) from approved staff notes for this machine."""
    out: dict[str, tuple[str, str]] = {}
    for d in approved_docs(db, equipment_id, ["staff_note"]):
        for sec in sections(d):
            for line in sec["text"].splitlines():
                line = line.strip().lstrip("-* ").strip()
                v = material_verdict(line)
                if v is None and re.match(r"^(no|never)\b", line, re.I):
                    v = "banned"
                if v is None:
                    continue
                for m in find_materials(material_clause(line)):
                    out[m] = (v, line)
    return out


def review_document(db: Session, doc: Document) -> dict[str, Any]:
    eq = db.get(Equipment, doc.equipment_id) if doc.equipment_id else None
    category = eq.category if eq else "other"
    issues: list[dict[str, Any]] = []
    staff_rules = staff_material_verdicts(db, doc.equipment_id) if doc.equipment_id else {}
    checked = 0
    for i, st in enumerate(doc.statements):
        if st.get("kind") not in SAFETY_KINDS:
            continue
        checked += 1
        cites = st.get("citations") or []
        if not cites:
            issues.append({"doc_id": doc.id, "index": i, "code": "E07", "text": st["text"],
                           "why": "safety statement has no citation"})
            continue
        bad = [r for r in (verify_citation(db, c, require_approved=False) for c in cites) if r]
        if len(bad) == len(cites):
            issues.append({"doc_id": doc.id, "index": i, "code": "E07", "text": st["text"],
                           "why": "; ".join(bad)})
            continue
        if st.get("kind") == "material":
            v = tools.check_sop_rules(category, [st])
            mat = [(r, w) for r, w in zip(v.rule_ids, v.reasons, strict=False) if r.startswith("MAT-")]
            if mat:
                issues.append({"doc_id": doc.id, "index": i, "code": "E09", "text": st["text"],
                               "why": "; ".join(w for _, w in mat), "rule_ids": [r for r, _ in mat]})
                continue
            verdict = material_verdict(st["text"])
            for m in find_materials(material_clause(st["text"])):
                if m in staff_rules and verdict and staff_rules[m][0] != verdict:
                    issues.append({"doc_id": doc.id, "index": i, "code": "E08", "text": st["text"],
                                   "why": f"staff note says: {staff_rules[m][1]}"})
                    break
            if issues and issues[-1]["index"] == i:
                continue
        ok, why = check_support(db, st["text"], cites)
        if not ok:
            issues.append({"doc_id": doc.id, "index": i, "code": "E07", "text": st["text"],
                           "why": f"citation doesn't support it ({why})"})
    doc_level: list[dict[str, Any]] = []
    if doc.type == "sop":
        v = tools.check_sop_rules(category, doc.statements)
        for reason, rid in zip(v.reasons, v.rule_ids, strict=False):
            if rid.startswith("MAT-"):
                continue  # already reported per statement
            section = None
            if rid.startswith("SOP-SECTION-"):
                section = reason.split(":", 1)[1].strip()
            elif rid.startswith("SOP-PPE"):
                section = "PPE"
            doc_level.append({"doc_id": doc.id, "index": None, "code": "E09", "text": "",
                              "why": reason, "rule_ids": [rid], "section": section})
    return {"issues": issues, "doc_level": doc_level, "checked": checked}


def _interval_text(plan: MaintenancePlan) -> str:
    if plan.interval_hours:
        return f"every {plan.interval_hours:g} h of use"
    return f"every {plan.interval_days} days"


def review_plan(db: Session, plan: MaintenancePlan) -> list[dict[str, Any]]:
    """Check one drafted maintenance task against the manual line it cites.

    E07  no citation, or it doesn't resolve (wrong document, version or locator, quote not there),
         or the cited line doesn't mention the task
    E08  the manual line states a different interval than the plan (contradicts the manual)
    Deterministic on purpose: the interval is a number and the manual either says it or doesn't."""
    label = f"{plan.task} ({_interval_text(plan)})"

    def issue(code: str, why: str) -> list[dict[str, Any]]:
        return [{"plan_id": plan.id, "code": code, "text": label, "why": why}]

    ref = plan.source_ref or {}
    if not ref.get("document_id"):
        return issue("E07", "maintenance task has no citation")
    try:
        bad = verify_citation(db, ref, require_approved=False)
    except Exception:  # a malformed citation record is a failed citation, not a crash
        bad = "citation record is malformed"
    if bad:
        return issue("E07", bad)
    quote = str(ref.get("quote") or "").strip().lstrip("-* ").strip()
    if plan.task.lower()[:30] not in quote.lower():
        return issue("E07", "the cited line doesn't mention this task")
    stated = parse_interval(quote)
    if stated is not None:
        same = (stated["interval_days"] == plan.interval_days
                and stated["interval_hours"] == plan.interval_hours)
    else:
        number = f"{plan.interval_hours:g}" if plan.interval_hours else str(plan.interval_days)
        same = bool(re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", quote))
    if not same:
        return issue("E08", f"the manual says: {quote[:160]}")
    return []


def review_plans(db: Session, equipment_id: str, plan_ids: list[str], actor: str = "review") -> AgentResult:
    """Review drafted maintenance plans. Clean -> one approval for staff, with a reviewer summary.
    Any flag -> nothing is sent for approval and staff get an escalation (the plans can't publish),
    exactly as for documents. Plans already approved stay in force until a clean set replaces them."""
    eq = db.get(Equipment, equipment_id)
    name = eq.name if eq else equipment_id
    plans = [p for p in (db.get(MaintenancePlan, i) for i in plan_ids) if p is not None]
    flags: list[dict[str, Any]] = []
    for p in plans:
        flags.extend(review_plan(db, p))
    summary = (f"checked {len(plans)} maintenance task(s) against the manual, "
               + (f"{len(flags)} open flag(s)" if flags else "no open flags"))
    trace = [f"review:maintenance_plan -> {summary}"]
    if flags:
        codes = sorted({f["code"] for f in flags})
        for code in codes:
            escalate(db, code, "equipment", equipment_id,
                     f"{name} maintenance plan: " + "; ".join(
                         f"{f['text']}: {f['why']}" for f in flags if f["code"] == code)[:500],
                     "review")
        return AgentResult(agent="review", status="escalated", escalations=codes, trace=trace,
                           output={"summary": summary, "plan_flags": flags})
    a = request_approval(db, "maintenance_plan", equipment_id,
                         {"plan_ids": [p.id for p in plans],
                          "review": {"checked": len(plans), "flags": [], "summary": summary}},
                         "review", f"{name}: {len(plans)} maintenance task(s) from the manual ({summary})")
    return AgentResult(agent="review", status="needs_approval", approvals=[a.id], trace=trace,
                       output={"summary": summary, "plan_flags": []})


@registry.agent("review")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    if payload.get("plan_ids"):  # maintenance plans, not documents
        return review_plans(db, payload.get("equipment_id", ""), payload["plan_ids"], actor)
    cycle = int(payload.get("cycle", 1))
    history: list[dict] = list(payload.get("history", []))
    trace: list[str] = []
    fixable: list[dict] = []
    results: dict[str, dict] = {}
    for doc_id in payload.get("doc_ids", []):
        doc = db.get(Document, doc_id)
        if doc is None:
            continue
        r = review_document(db, doc)
        results[doc_id] = r
        fixable.extend(r["issues"])
        fixable.extend(i for i in r["doc_level"] if i.get("section"))
        trace.append(f"review:{doc.type} -> {len(r['issues'])} statement issue(s), "
                     f"{len(r['doc_level'])} document issue(s)")
    revise_agent = payload.get("revise_agent")
    if fixable and revise_agent and revise_agent in registry.agents and cycle <= MAX_CYCLES:
        return AgentResult(agent="review", status="ok", trace=trace,
                           output={"cycle": cycle, "sent_back": len(fixable)},
                           handoff=Handoff(agent=revise_agent, payload={
                               "doc_ids": payload.get("doc_ids", []), "issues": fixable,
                               "cycle": cycle, "history": history + fixable}))
    approvals, escalated = [], set()
    summary_out = {}
    for doc_id, r in results.items():
        doc = db.get(Document, doc_id)
        remaining = r["issues"] + r["doc_level"]
        removed = [h for h in history if h.get("doc_id") == doc_id]
        doc.review = {
            "checked_statements": r["checked"], "cycles": cycle,
            "flags": remaining,
            "removed_in_revision": [{"text": h["text"], "code": h["code"], "why": h["why"]}
                                    for h in removed],
            "summary": _summary(doc, r, removed),
        }
        summary_out[doc_id] = doc.review["summary"]
        if remaining:
            for code in sorted({f["code"] for f in remaining}):
                escalate(db, code, "document", doc.id,
                         f"{doc.title}: " + "; ".join(f["why"] for f in remaining
                                                      if f["code"] == code)[:500], "review")
                escalated.add(code)
            continue  # blocking: can't publish until fixed
        a = request_approval(db, "publish_doc", doc.id,
                             {"equipment_id": doc.equipment_id, "type": doc.type}, "review",
                             f"{doc.title} v{doc.version}: {doc.review['summary']}")
        approvals.append(a.id)
    status = "escalated" if escalated else "needs_approval"
    return AgentResult(agent="review", status=status, approvals=approvals,
                       escalations=sorted(escalated), trace=trace,
                       output={"cycle": cycle, "reviews": summary_out})


def _summary(doc: Document, r: dict, removed: list[dict]) -> str:
    parts = [f"checked {r['checked']} safety statements"]
    if removed:
        parts.append(f"removed {len(removed)} in revision ("
                     + "; ".join(f"{h['code']}: {h['why'][:80]}" for h in removed[:3]) + ")")
    left = r["issues"] + r["doc_level"]
    parts.append(f"{len(left)} open flag(s)" if left else "no open flags")
    return ", ".join(parts)
