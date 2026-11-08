"""Maintenance agent.

Plans: drafted from the approved manual's Maintenance section ("Every 8 hours of use: ...",
"Every 30 days: ...", "Weekly: ...") with a citation, then approved by staff.

Daily check (registered as a daily job): a plan is due when either its day interval or its
usage-hour interval has passed since it was last done. Due -> one staff notification per
plan (C4) and status `maintenance_due`; still not done after the grace period ->
`out_of_service` (C5). Logging the work puts the machine back to `available` if nothing else
is due and nobody else set a different status.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.agents.intake.drafting import bullets
from msa.core.contracts import AgentResult
from msa.core.db import utcnow
from msa.core.docs import approved_docs, citation_for, sections
from msa.core.llm import get_llm
from msa.core.models import Equipment, LogEntry, MaintenancePlan
from msa.core.registry import registry
from msa.core.services import (
    ServiceError,
    audit,
    notify,
    request_approval,
    resolve_notifications,
    set_machine_status,
    site_settings,
)

WORDS = {"daily": (1, None), "weekly": (7, None), "monthly": (30, None), "quarterly": (90, None),
         "yearly": (365, None), "annually": (365, None)}
EVERY = re.compile(r"^\s*every\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|days?|weeks?|months?|years?)"
                   r"(?:\s+of\s+use)?\s*[:,-]\s*(.+)$", re.I)
DUE_PREFIX = "Maintenance due"
OVERDUE_PREFIX = "Maintenance overdue"


def parse_interval(line: str) -> dict[str, Any] | None:
    m = EVERY.match(line)
    if m:
        n, unit, task = float(m.group(1)), m.group(2).lower(), m.group(3).strip()
        if unit.startswith(("hour", "hr")):
            return {"interval_hours": n, "interval_days": None, "task": task}
        mult = {"day": 1, "week": 7, "month": 30, "year": 365}[unit.rstrip("s")]
        return {"interval_days": int(n * mult), "interval_hours": None, "task": task}
    for w, (days, hours) in WORDS.items():
        mm = re.match(rf"^\s*{w}\s*[:,-]\s*(.+)$", line, re.I)
        if mm:
            return {"interval_days": days, "interval_hours": hours, "task": mm.group(1).strip()}
    return None


def deterministic_plans(eq: Equipment, manual) -> list[dict[str, Any]]:
    out = []
    for sec in sections(manual):
        if not sec["heading"].lower().startswith(("maintenance", "care", "service")):
            continue
        for b in bullets(sec["text"]):
            p = parse_interval(b)
            if p:
                p["source_ref"] = citation_for(manual, sec, b).model_dump()
                p["task"] = p["task"].rstrip(".")
                out.append(p)
    return out


SYSTEM = (
    "From the manual's maintenance instructions, list each recurring task with its interval. Use "
    "interval_days for calendar intervals and interval_hours for hours of use. Copy the task "
    'wording. Return {"plans": [{"task", "interval_days", "interval_hours", "line"}]}'
)


def _num(v, kind):
    try:
        x = kind(float(v))
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _clean_model_plans(raw, manual, fallback_plans: list[dict]) -> list[dict]:
    """Keep only model plans whose task wording is really in the manual and that have a valid
    interval; cite the manual line the deterministic parser found for the same task."""
    if not isinstance(raw, list):
        return fallback_plans
    body = manual.body.lower()
    by_task = {fp["task"].lower(): fp for fp in fallback_plans}
    clean = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        task = str(p.get("task") or "").strip().rstrip(".")
        days, hours = _num(p.get("interval_days"), int), _num(p.get("interval_hours"), float)
        if not task or task.lower()[:30] not in body or not (days or hours):
            continue
        match = by_task.get(task.lower())
        ref = match["source_ref"] if match else {"document_id": manual.id, "version": manual.version,
                                                 "locator": "maintenance", "quote": task[:200]}
        clean.append({"task": task, "interval_days": days, "interval_hours": hours,
                      "source_ref": ref})
    return clean or fallback_plans


@registry.agent("maintenance_plan")
def draft_plans(db: Session, payload: dict, actor: str) -> AgentResult:
    eq = db.get(Equipment, payload.get("equipment_id", ""))
    if eq is None:
        return AgentResult(agent="maintenance_plan", status="error", output={"error": "unknown equipment"})
    manuals = approved_docs(db, eq.id, ["manual"])
    if not manuals:
        return AgentResult(agent="maintenance_plan", status="refused",
                           output={"message": "No approved manual to plan from."})
    manual = manuals[0]
    fallback = {"plans": deterministic_plans(eq, manual)}
    out = get_llm().complete_json("maintenance.plan", SYSTEM, manual.body[:12000], lambda: fallback)
    plans = fallback["plans"] if out is fallback else _clean_model_plans(out.get("plans"), manual,
                                                                          fallback["plans"])
    for old in db.scalars(select(MaintenancePlan).where(MaintenancePlan.equipment_id == eq.id,
                                                        MaintenancePlan.status == "draft")).all():
        old.status = "retired"
    created = []
    for p in plans:
        mp = MaintenancePlan(equipment_id=eq.id, task=p["task"], interval_days=p.get("interval_days"),
                             interval_hours=p.get("interval_hours"), source_ref=p.get("source_ref", {}),
                             status="draft", created_by="maintenance")
        db.add(mp)
        created.append(mp)
    db.flush()
    if not created:
        return AgentResult(agent="maintenance_plan", status="ok",
                           output={"plans": 0, "message": "The manual lists no maintenance intervals."})
    a = request_approval(db, "maintenance_plan", eq.id, {"plan_ids": [m.id for m in created]},
                         "maintenance", f"{eq.name}: {len(created)} maintenance task(s) from the manual")
    return AgentResult(agent="maintenance_plan", status="needs_approval", approvals=[a.id],
                       output={"plans": len(created), "plan_ids": [m.id for m in created]})


def approve_plans(db: Session, equipment_id: str, plan_ids: list[str], actor: str,
                  now: datetime | None = None) -> None:
    now = now or utcnow()
    eq = db.get(Equipment, equipment_id)
    for old in db.scalars(select(MaintenancePlan).where(MaintenancePlan.equipment_id == equipment_id,
                                                        MaintenancePlan.status == "approved")).all():
        if old.id not in plan_ids:
            old.status = "retired"
    for pid in plan_ids:
        mp = db.get(MaintenancePlan, pid)
        if mp and mp.status == "draft":
            mp.status = "approved"
            mp.last_done_at = mp.last_done_at or now  # clock starts when the plan is approved
            mp.last_done_hours = mp.last_done_hours if mp.last_done_hours is not None else (
                eq.usage_hours if eq else 0.0)
    audit(db, actor, "maintenance.plan_approved", "equipment", equipment_id, None,
          {"plan_ids": plan_ids})


def is_due(mp: MaintenancePlan, eq: Equipment, now: datetime) -> tuple[bool, datetime | None, str]:
    """(due?, when it became due if known, why)."""
    reasons, due_at = [], None
    if mp.interval_days and mp.last_done_at:
        when = mp.last_done_at + timedelta(days=mp.interval_days)
        if now >= when:
            reasons.append(f"{mp.interval_days} days since last done")
            due_at = when
    if mp.interval_hours is not None:
        used = (eq.usage_hours or 0) - (mp.last_done_hours or 0)
        if used >= mp.interval_hours:
            reasons.append(f"{used:.1f} h of use (every {mp.interval_hours:g} h)")
            due_at = due_at or now
    return bool(reasons), due_at, "; ".join(reasons)


def daily_check(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    grace = timedelta(days=site_settings(db).maintenance_grace_days)
    due_items, overdue = [], []
    plans = db.scalars(select(MaintenancePlan).where(MaintenancePlan.status == "approved")).all()
    by_eq: dict[str, list] = {}
    for mp in plans:
        eq = db.get(Equipment, mp.equipment_id)
        if eq is None or eq.status == "retired":
            continue
        due, due_at, why = is_due(mp, eq, now)
        if not due:
            continue
        by_eq.setdefault(eq.id, []).append((mp, due_at, why))
        notify(db, "staff", f"maintenance:{eq.id}:{mp.id}",
               f"{eq.name}: '{mp.task}' is due ({why}).", "warning")
        due_items.append({"equipment_id": eq.id, "task": mp.task, "why": why})
    for eid, items in by_eq.items():
        eq = db.get(Equipment, eid)
        late = [(mp, d) for mp, d, _ in items if d is not None and now - d > grace]
        if late:
            if eq.status != "out_of_service":
                set_machine_status(db, eid, "out_of_service",
                                   f"{OVERDUE_PREFIX}: {', '.join(m.task for m, _ in late)}", "maintenance")
            overdue.append(eid)
        elif eq.status == "available":
            set_machine_status(db, eid, "maintenance_due",
                               f"{DUE_PREFIX}: {', '.join(m.task for m, _, _ in items)}", "maintenance")
    return {"due": due_items, "out_of_service": overdue}


registry.daily_job("maintenance")(lambda db: daily_check(db))


def log_work(db: Session, equipment_id: str, kind: str, notes: str, actor: str,
             plan_id: str | None = None, corrects_id: str | None = None,
             now: datetime | None = None) -> LogEntry:
    """Append a maintenance / inspection / correction entry (G6: never edited)."""
    if kind not in ("maintenance", "inspection", "correction"):
        raise ServiceError("kind must be maintenance, inspection or correction")
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        raise ServiceError("Unknown equipment")
    if kind == "correction":
        if not corrects_id or db.get(LogEntry, corrects_id) is None:
            raise ServiceError("A correction must point at the entry it corrects")
    now = now or utcnow()
    mp = db.get(MaintenancePlan, plan_id) if plan_id else None
    entry = LogEntry(equipment_id=eq.id, kind=kind, task=mp.task if mp else "", plan_id=plan_id,
                     notes=notes, usage_hours=eq.usage_hours, corrects_id=corrects_id,
                     created_by=actor, created_at=now)
    db.add(entry)
    if mp and kind in ("maintenance", "inspection"):
        mp.last_done_at = now
        mp.last_done_hours = eq.usage_hours
        resolve_notifications(db, f"maintenance:{eq.id}:{mp.id}")
    db.flush()
    still_due = [m for m in db.scalars(select(MaintenancePlan).where(
        MaintenancePlan.equipment_id == eq.id, MaintenancePlan.status == "approved")).all()
        if is_due(m, eq, now)[0]]
    if not still_due and eq.status in ("maintenance_due", "out_of_service") and \
            eq.status_reason.startswith((DUE_PREFIX, OVERDUE_PREFIX)):
        set_machine_status(db, eq.id, "available", "Maintenance logged", actor)
    return entry


def work_order(db: Session, plan: MaintenancePlan) -> dict[str, Any]:
    """The manual's own wording for the task, with its citation, for whoever does the work."""
    return {"plan_id": plan.id, "equipment_id": plan.equipment_id, "task": plan.task,
            "source": plan.source_ref,
            "steps": [plan.source_ref.get("quote", plan.task)] if plan.source_ref else [plan.task]}
