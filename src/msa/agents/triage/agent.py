"""Triage agent.

1. Classify: incident | near_miss | feedback. Emergency words always mean incident (G5),
   whatever the LLM says.
2. Incident: emergency info first, E10, machine out_of_service pending a staff check.
3. Near-miss: recorded and staff told (one notification per machine per open cluster).
4. Feedback: grouped into a cluster (machine + issue type) so N reports = one notification with
   a count (C4); a broken-machine report marks the machine `issue_reported`; 3+ reports about one
   machine in 24 h raise E15.
5. Suggest a fix from approved documents only, using the Ask agent's retrieval (no source, no
   suggestion).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa.agents.ask.agent import detect_equipment, emergency_payload, is_emergency, passages
from msa.core.contracts import AgentResult
from msa.core.db import utcnow
from msa.core.llm import get_llm
from msa.core.models import Equipment, Report
from msa.core.registry import registry
from msa.core.services import escalate, notify, set_machine_status
from msa.core.textsearch import Index

INCIDENT_WORDS = ["caught fire", "on fire", "a fire", "fire started", "flames", "burned my",
                  "burnt my", "burned myself", "got burned", "cut my", "cut myself", "cut himself",
                  "cut herself", "bleeding", "injur", "got a shock", "shocked", "smoke coming",
                  "was smoking", "started smoking", "hospital", "stitches", "hurt my", "got hurt",
                  "hurt himself", "hurt herself", "first aid", "fainted", "in my eye"]
NEAR_MISS_WORDS = ["almost", "nearly", "close call", "near miss", "near-miss", "flare-up", "flare up",
                   "kickback", "kicked back", "could have", "barely"]
ISSUE_TYPES: list[tuple[str, list[str]]] = [
    ("out_of_stock", ["out of", "ran out", "none left", "empty", "no more"]),
    ("running_low", ["running low", "low on", "almost out", "getting low", "only one left"]),
    ("not_working", ["not working", "doesn't work", "won't", "broken", "not firing", "dead",
                     "jammed", "jam", "stuck", "error", "not heating", "won't turn on", "failed",
                     "isn't working", "stopped working", "clogged", "not cutting"]),
    ("damaged", ["cracked", "damaged", "bent", "loose", "frayed", "missing guard", "torn"]),
    ("missing", ["missing", "can't find", "lost", "gone"]),
    ("dirty", ["dirty", "messy", "spill", "spilled", "full of"]),
]
E15_THRESHOLD = 3


def deterministic_classify(text: str) -> dict[str, Any]:
    t = text.lower()
    if is_emergency(text) or any(w in t for w in INCIDENT_WORDS):
        kind = "incident"
    elif any(w in t for w in NEAR_MISS_WORDS):
        kind = "near_miss"
    else:
        kind = "feedback"
    issue = next((name for name, words in ISSUE_TYPES if any(w in t for w in words)), "other")
    return {"kind": kind, "issue_type": issue}


SYSTEM = (
    "Classify a makerspace report. kind: incident (someone was hurt, or there was a fire, smoke "
    "or an electric shock), near_miss (an event happened where someone almost got hurt or a fire "
    "almost started, e.g. kickback, a flare-up), or feedback (anything that is not working, "
    "damaged, missing, dirty or running low, including missing or broken safety equipment: that "
    "is feedback about equipment, not a near-miss event). issue_type: not_working, damaged, "
    'missing, running_low, out_of_stock, dirty, other. Return {"kind": "...", "issue_type": "..."}'
)


def classify(text: str) -> dict[str, Any]:
    base = deterministic_classify(text)
    out = get_llm().complete_json("triage.classify", SYSTEM, text[:2000], lambda: base, max_tokens=100)
    kind = out.get("kind") if out.get("kind") in ("incident", "near_miss", "feedback") else base["kind"]
    if base["kind"] == "incident":
        kind = "incident"  # never let a model downgrade an incident (G5)
    issue = out.get("issue_type") if out.get("issue_type") in {n for n, _ in ISSUE_TYPES} | {"other"} \
        else base["issue_type"]
    return {"kind": kind, "issue_type": issue}


def suggest(db: Session, eq: Equipment | None, text: str) -> dict[str, Any] | None:
    """A possible fix from approved docs (troubleshooting first), or nothing."""
    corpus = passages(db, eq)
    if not corpus:
        return None
    hits = [(s, p) for s, p in Index(corpus).search(text, k=3) if s >= 2.5]
    if not hits:
        return None
    p = hits[0][1]
    return {"text": p["text"], "citations": p["citations"],
            "note": "From the approved documents. If this doesn't fix it, staff will follow up."}


def cluster_key(eq: Equipment | None, issue: str, text: str) -> str:
    if eq is None:
        words = [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3][:3]
        return f"general:{issue}:{'-'.join(words) or 'misc'}"
    return f"{eq.id}:{issue}"


@registry.agent("triage")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    text = (payload.get("text") or "").strip()
    user_id = payload.get("user_id") or actor
    if len(text) < 3:
        return AgentResult(agent="triage", status="refused", output={"message": "Please describe the issue."})
    eq = db.get(Equipment, payload["equipment_id"]) if payload.get("equipment_id") else \
        detect_equipment(db, text, None)
    c = classify(text)
    if payload.get("kind_hint") in ("incident", "near_miss") and c["kind"] == "feedback":
        c["kind"] = payload["kind_hint"]  # the reporter knows best when they say it was an incident
    trace = [f"classify -> {c['kind']}/{c['issue_type']}", f"equipment -> {eq.id if eq else 'none'}"]
    key = cluster_key(eq, c["issue_type"], text) if c["kind"] == "feedback" else None
    severity = {"incident": "critical", "near_miss": "high"}.get(c["kind"], "low")
    rep = Report(kind=c["kind"], equipment_id=eq.id if eq else None, user_id=user_id, text=text,
                 cluster_id=key, severity=severity, created_by=user_id)
    db.add(rep)
    db.flush()
    out: dict[str, Any] = {"report_id": rep.id, "kind": c["kind"], "issue_type": c["issue_type"],
                           "equipment_id": eq.id if eq else None}
    codes: list[str] = []

    if c["kind"] == "incident":
        escalate(db, "E10", "report", rep.id, f"{eq.name if eq else 'Shop'}: {text[:300]}", "triage")
        codes.append("E10")
        if eq and eq.status != "retired":
            set_machine_status(db, eq.id, "out_of_service", "Incident reported: staff must check it",
                               "triage")
        out.update({"emergency": True, **emergency_payload(db)})
        return AgentResult(agent="triage", status="escalated", escalations=codes, output=out, trace=trace)

    if c["kind"] == "near_miss":
        notify(db, "staff", f"near_miss:{eq.id if eq else 'shop'}",
               f"Near-miss reported{' on ' + eq.name if eq else ''}: {text[:200]}", "high")
        out["message"] = "Thank you. Staff have been told so they can prevent it happening again."
        return AgentResult(agent="triage", status="ok", output=out, trace=trace)

    n = notify(db, "staff", f"feedback:{key}",
               f"{eq.name if eq else 'Shop'}: {c['issue_type'].replace('_', ' ')}: {text[:200]}",
               "warning" if c["issue_type"] in ("not_working", "damaged") else "info")
    out["reports_in_cluster"] = n.count
    if eq and c["issue_type"] in ("not_working", "damaged") and eq.status == "available":
        set_machine_status(db, eq.id, "issue_reported", f"Reported: {text[:120]}", "triage")
    if eq:
        since = utcnow() - timedelta(hours=24)
        recent = db.scalar(select(func.count()).select_from(Report).where(
            Report.equipment_id == eq.id, Report.created_at >= since, Report.status == "open"))
        if recent >= E15_THRESHOLD:
            escalate(db, "E15", "equipment", eq.id, f"{recent} reports in 24 h", "triage")
            codes.append("E15")
    s = suggest(db, eq, text)
    if s:
        rep.suggestion = s
        out["suggestion"] = s
    out["message"] = "Thanks, staff have been notified." + (
        f" {n.count} people have reported this." if n.count > 1 else "")
    return AgentResult(agent="triage", status="ok", escalations=codes, output=out, trace=trace)


def resolve_cluster(db: Session, report: Report, staff_id: str, resolution: str) -> int:
    """Resolve a report and every open report in its cluster; closes the notification."""
    from msa.core.services import resolve_notifications

    q = select(Report).where(Report.status == "open")
    if report.cluster_id:
        q = q.where(Report.cluster_id == report.cluster_id)
    else:
        q = q.where(Report.id == report.id)
    rows = db.scalars(q).all()
    for r in rows:
        r.status = "resolved"
        r.resolved_by = staff_id
        r.resolved_at = utcnow()
        r.resolution = resolution
    if report.cluster_id:
        resolve_notifications(db, f"feedback:{report.cluster_id}")
    db.flush()
    return len(rows)
