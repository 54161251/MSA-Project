"""Project Check: split a plan into steps, map each to machine + material + process, run the
deterministic tools on every step, add cross-step risks, and return a verdict.

Verdicts: clear | clear_with_conditions | needs_staff. Any blocking E-code => needs_staff,
and a project_check approval is opened for staff.

The LLM only splits free text into steps (task "project_check.plan"). Every safety
judgement comes from msa.safety_tools (G2). If the LLM names a machine or material the
tools don't know, that becomes an escalation, never a pass (G3).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core import escalations
from msa.core.contracts import USABLE_STATUSES, AgentResult, Citation
from msa.core.llm import get_llm
from msa.core.models import Equipment, ProjectCheck
from msa.core.registry import registry
from msa.core.services import escalate, request_approval
from msa.safety_tools import tools
from msa.safety_tools.rules import (
    category_label,
    find_categories,
    find_materials,
    find_processes,
    rules,
)

SYSTEM = (
    "You split a makerspace member's project description into ordered steps. For each step "
    "give: text (the step in plain words), category (one of the machine categories given, or "
    "null for hand work), equipment_id (only if the member named a specific machine id from "
    "the list), materials (the raw stock or consumables being cut, printed, machined, sewn or "
    "soldered with, exactly as the member wrote them; not parts or components such as boards, "
    "pins, screws or wires), processes "
    "(any of: sanding, gluing, painting, lithium_battery). Do not judge safety. "
    'Return {"steps": [...]}'
)


def plan_steps(db: Session, description: str) -> list[dict[str, Any]]:
    machines = db.scalars(select(Equipment).where(Equipment.status != "retired")).all()
    cats = sorted(rules()["categories"])
    prompt = (
        f"Machine categories: {', '.join(cats)}\n"
        f"Machines: {', '.join(f'{m.id} ({m.category})' for m in machines)}\n"
        f"Project description:\n{description}"
    )
    out = get_llm().complete_json("project_check.plan", SYSTEM, prompt,
                                  lambda: {"steps": split_steps(description, machines)})
    steps = out.get("steps") or []
    if not isinstance(steps, list):
        steps = []
    # Never trust the LLM alone: re-scan each step's text with the deterministic matchers
    clean = []
    for s in steps:
        if isinstance(s, str):
            s = {"text": s}
        if not isinstance(s, dict):
            continue
        if not isinstance(s.get("materials"), list):
            s["materials"] = []
        if not isinstance(s.get("processes"), list):
            s["processes"] = []
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        cat = s.get("category") if isinstance(s.get("category"), str) and s["category"] in cats else None
        found = find_categories(text)
        if cat is None and found:
            cat = found[0]
        mats = list(dict.fromkeys([*map(str, s.get("materials") or []), *find_materials(text)]))
        procs = list(dict.fromkeys([*(s.get("processes") or []), *find_processes(text)]))
        eid = s.get("equipment_id") if isinstance(s.get("equipment_id"), str) else None
        clean.append({"text": text, "category": cat, "equipment_id": eid,
                      "materials": mats, "processes": [p for p in procs
                                                       if p in rules()["processes"]]})
    return clean


def split_steps(description: str, machines) -> list[dict[str, Any]]:
    """Deterministic fallback: clause splitting + keyword matching."""
    parts = re.split(r"(?:\.\s+|;\s*|\n+|,?\s+then\s+|,\s+and\s+|\s+and then\s+|\.$)",
                     description.strip(), flags=re.I)
    by_id = {m.id.lower(): m for m in machines}
    steps = []
    for p in parts:
        p = (p or "").strip(" ,.")
        if len(p) < 3:
            continue
        cats = find_categories(p)
        eq_id = next((mid for mid in by_id if re.search(rf"\b{re.escape(mid)}\b", p.lower())), None)
        steps.append({"text": p, "category": cats[0] if cats else
                      (by_id[eq_id].category if eq_id else None),
                      "equipment_id": eq_id, "materials": find_materials(p),
                      "processes": find_processes(p)})
    return steps


_TO_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4}
_SIZE = r"(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|\")"
_LIMIT = re.compile(r"up to\s+" + _SIZE, re.I)


def over_thickness(step_text: str, material: str, rule_text: str) -> tuple[str, str] | None:
    """('12 mm', '6 mm') when the member states a thickness above the approved limit.

    Only a size written as a thickness counts ("12 mm thick", "12 mm plywood"), so the width or
    length of a part ("a 300 mm wide plywood panel") is not mistaken for one."""
    lim = _LIMIT.search(rule_text or "")
    if not lim:
        return None
    limit_mm = float(lim.group(1)) * _TO_MM[lim.group(2).lower()]
    words = "|".join(re.escape(w) for w in material.lower().split("_") if len(w) > 2) or "sheet"
    thick = re.compile(_SIZE + r"(?:[\s-]+thick\b|(?:\s+(?!wide|long|tall|high|by|x)[a-z-]+){0,2}"
                       r"\s+(?:" + words + r"|sheet|board|stock|panel)\b)", re.I)
    for m in thick.finditer(step_text):
        mm = float(m.group(1)) * _TO_MM[m.group(2).lower()]
        if mm > 100 and "thick" not in m.group(0).lower():
            continue  # "600 mm plywood board" is a length, not a thickness
        if mm > limit_mm + 1e-9:
            return f"{m.group(1)} {m.group(2)}", f"{lim.group(1)} {lim.group(2)}"
    return None


def pick_machine(db: Session, category: str | None, equipment_id: str | None) -> Equipment | None:
    if equipment_id:
        eq = db.get(Equipment, equipment_id)
        if eq:
            return eq
    if not category:
        return None
    cands = db.scalars(select(Equipment).where(Equipment.category == category,
                                               Equipment.status != "retired")).all()
    usable = [e for e in cands if e.status in USABLE_STATUSES]
    return (usable or list(cands) or [None])[0]


@registry.agent("project_check")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    user_id = payload.get("user_id") or actor
    description = (payload.get("description") or "").strip()
    trace: list[str] = []
    if len(description) < 10:
        return AgentResult(agent="project_check", status="refused",
                           output={"message": "Describe the project in a sentence or two."})
    steps = plan_steps(db, description)
    trace.append(f"llm:project_check.plan -> {len(steps)} steps")
    findings: list[dict[str, Any]] = []
    codes: set[str] = set()
    citations: list[Citation] = []
    project_cats: set[str] = set()
    project_procs: set[str] = set()

    def add(step_i: int | None, severity: str, text: str, rule_ids=(), code: str | None = None,
            cites=()):
        findings.append({"step": step_i, "severity": severity, "text": text,
                         "rule_ids": list(rule_ids), "code": code,
                         "citations": [c.model_dump() if isinstance(c, Citation) else c
                                       for c in cites]})
        if code:
            codes.add(code)

    risk = escalations.match_high_risk(description)
    if risk:
        add(None, "critical", "This plan includes a high-risk activity "
            f"({', '.join(r.replace('_', ' ') for r in risk)}). A staff member must review it.",
            [f"RISK-{r.upper()}" for r in risk], "E04")

    for i, st in enumerate(steps):
        project_procs.update(st["processes"])
        if not st["category"] and not st["equipment_id"]:
            if st["materials"] or st["processes"]:
                add(i, "info", f"Hand work: {st['text']}")
            continue
        eq = pick_machine(db, st["category"], st["equipment_id"])
        if eq is None:
            add(i, "high", f"No {category_label(st['category'] or '')} is registered in this "
                "makerspace.", ["EQ-NONE"], "E02")
            continue
        st["equipment_id"] = eq.id
        project_cats.add(eq.category)
        v = tools.can_use(db, user_id, eq.id)
        trace.append(f"tool:can_use({eq.id}) -> {v.verdict}")
        if v.verdict != "yes":
            for reason, code in zip(v.reasons, _codes_for(v), strict=False):
                add(i, "high", f"{eq.name}: {reason}", v.rule_ids, code)
        for w in v.data.get("warnings", []):
            add(i, "medium", w, ["USE-STATUS-WARN"])
        for mat in st["materials"]:
            mv = tools.check_material(db, eq.id, mat)
            trace.append(f"tool:check_material({eq.id},{mat}) -> {mv.verdict}")
            label = mat.replace("_", " ").upper() if len(mat) <= 4 else mat.replace("_", " ")
            if mv.verdict == "banned":
                add(i, "critical", f"{label} is not allowed on the {eq.name}: "
                    f"{' '.join(mv.reasons)}", mv.rule_ids, "E01")
            elif mv.verdict == "unknown" and "MAT-UNKNOWN" in mv.rule_ids and \
                    eq.category in rules().get("component_categories", []):
                add(i, "info", f"{mat} isn't a material the system knows; that's normal for parts "
                    f"on the {eq.name}. Ask staff if you're unsure.", mv.rule_ids)
            elif mv.verdict in ("unknown", "ask_staff"):
                add(i, "high", f"{label} on the {eq.name}: {' '.join(mv.reasons) or 'ask staff'}",
                    mv.rule_ids, "E01")
            else:
                ref = mv.data.get("source_ref") or {}
                cites = [Citation(**ref)] if ref.get("document_id") else []
                citations.extend(cites)
                over = over_thickness(st["text"], mat, " ".join(mv.reasons))
                if over:  # approved material, but thicker than the manual's limit: staff decide
                    add(i, "high", f"{label} is approved for the {eq.name} only up to {over[1]}; "
                        f"this plan uses {over[0]}.", [*mv.rule_ids, "MAT-THICKNESS"], "E01", cites)
                else:
                    add(i, "ok", f"{label} is approved for the {eq.name}.", mv.rule_ids, None, cites)
        pv = tools.required_ppe(db, eq.id)
        trace.append(f"tool:required_ppe({eq.id}) -> {pv.verdict}")
        items = pv.data.get("items", [])
        if items:
            cites = [Citation(**c) for it in items for c in it.get("citations", [])]
            citations.extend(cites)
            add(i, "condition", f"For the {eq.name}:\n" + "\n".join(f"- {it['item']}" for it in items),
                pv.rule_ids, None, cites)

    for rule in rules()["cross_step"]:
        trig = set(rule["triggers"])
        if trig <= (project_cats | project_procs):
            sev = {"low": "condition", "medium": "condition", "high": "high"}[rule["severity"]]
            # high-severity combinations need a staff member to look first (E04)
            add(None, sev, rule["text"], [rule["id"]], "E04" if sev == "high" else None)

    if not findings and not project_cats:
        # Nothing was recognized: no machine, material or process. "Looks fine" would be a
        # default-to-safe answer (G3), so ask the member to be specific instead.
        return AgentResult(
            agent="project_check", status="refused", trace=trace,
            output={"needs_clarification": True, "steps": steps, "findings": [],
                    "message": "I couldn't tell which machines or materials this plan uses, so I "
                               "can't check it. Name each machine (for example \"laser cutter\") "
                               "and material, or ask a staff member."})
    blocking = {c for c in codes if escalations.get(c).blocking}
    if blocking:
        verdict = "needs_staff"
    elif any(f["severity"] in ("condition", "medium", "high") for f in findings):
        verdict = "clear_with_conditions"
    else:
        verdict = "clear"

    pc = ProjectCheck(user_id=user_id, description=description, steps=steps, findings=findings,
                      verdict=verdict, status="needs_staff" if blocking else "done",
                      created_by="project_check")
    db.add(pc)
    db.flush()
    approvals = []
    for code in sorted(blocking):
        escalate(db, code, "project_check", pc.id,
                 "; ".join(f["text"] for f in findings if f["code"] == code)[:600], "project_check")
    if blocking:
        a = request_approval(db, "project_check", pc.id, {"verdict": verdict},
                             "project_check", f"Project check needs review: {description[:80]}")
        approvals.append(a.id)
    status = "escalated" if blocking else "ok"
    return AgentResult(
        agent="project_check", status=status,
        output={"project_check_id": pc.id, "verdict": verdict, "steps": steps,
                "findings": findings,
                "message": _member_message(verdict, blocking)},
        citations=_dedup(citations), escalations=sorted(codes), approvals=approvals, trace=trace,
    )


def _codes_for(v) -> list[str]:
    mapping = {"USE-STATUS": "E06", "USE-NO-SOP": "E02", "USE-WAIVER": "E13",
               "USE-TRAINING": "E05", "USE-PREREQ": "E05"}
    return [mapping.get(r, "E05") for r in v.rule_ids]


def _member_message(verdict: str, blocking: set[str]) -> str:
    if verdict == "clear":
        return "Your plan looks fine. Follow each machine's SOP."
    if verdict == "clear_with_conditions":
        return "You can go ahead if you follow the conditions listed for each step."
    return ("A staff member needs to look at this plan before you start: "
            + " ".join(escalations.get(c).member_message for c in sorted(blocking)))


def _dedup(cites: list[Citation]) -> list[Citation]:
    seen, out = set(), []
    for c in cites:
        k = (c.document_id, c.locator)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out
