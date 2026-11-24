"""Staff Briefing: collects the facts deterministically, then (optionally) has the LLM write
them up as a short summary. The facts are always shown too, so nothing depends on the prose."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa.core import escalations
from msa.core.contracts import AgentResult
from msa.core.db import utcnow
from msa.core.llm import get_llm
from msa.core.models import Approval, Equipment, Escalation, Report
from msa.core.registry import registry
from msa.core.services import notify

APPROVAL_WORDS = {
    "publish_doc": "document(s) to publish", "quiz_bank": "quiz(zes) to check",
    "competency_check": "in-person training sign-off(s)", "maintenance_plan": "maintenance plan(s)",
    "order_draft": "supply order(s)", "layout_change": "floor plan suggestion(s)",
    "project_check": "member project(s) to review", "status_override": "machine status change(s)",
}


def facts(db: Session) -> dict[str, Any]:
    out: dict[str, Any] = {"date": utcnow().date().isoformat()}
    out["machines_not_available"] = [
        {"id": e.id, "name": e.name, "status": e.status, "reason": e.status_reason}
        for e in db.scalars(select(Equipment).where(Equipment.status.notin_(["available", "retired"])))]
    rows = db.execute(select(Escalation.code, func.count()).where(Escalation.status == "open")
                      .group_by(Escalation.code)).all()
    rank = {c: i for i, c in enumerate(escalations.URGENCY)}
    out["escalations"] = [
        {"code": code, "count": n, "what": escalations.explain(code, n)}
        for code, n in sorted(rows, key=lambda r: rank.get(r[0], 99))]
    out["pending_approvals"] = [
        {"kind": k, "count": n, "what": f"{n} {APPROVAL_WORDS.get(k, k.replace('_', ' '))}"}
        for k, n in db.execute(
            select(Approval.kind, func.count()).where(Approval.status == "pending")
            .group_by(Approval.kind)).all()]
    out["open_reports"] = [
        {"kind": k, "count": n} for k, n in db.execute(
            select(Report.kind, func.count()).where(Report.status == "open").group_by(Report.kind)).all()]
    try:  # present when the maintenance agent is installed
        from msa.agents.maintenance.agent import is_due
        from msa.core.models import MaintenancePlan

        now = utcnow()
        due = []
        for p in db.scalars(select(MaintenancePlan).where(MaintenancePlan.status == "approved")):
            eq = db.get(Equipment, p.equipment_id)
            if eq and is_due(p, eq, now)[0]:
                due.append({"equipment": eq.name, "task": p.task})
        out["maintenance_due"] = due
    except ImportError:
        pass
    try:  # present when the inventory agent is installed
        from msa.agents.inventory.agent import forecast
        from msa.core.models import InventoryItem

        low = []
        for it in db.scalars(select(InventoryItem).where(InventoryItem.active.is_(True))):
            f = forecast(db, it)
            if f["on_hand"] <= it.reorder_point:
                low.append({"item": it.name, "on_hand": f["on_hand"], "unit": it.unit})
        out["low_stock"] = low
    except ImportError:
        pass
    return out


def plain_summary(f: dict[str, Any]) -> str:
    lines = []
    inc = next((r["count"] for r in f["open_reports"] if r["kind"] == "incident"), 0)
    if inc:
        lines.append(f"{inc} open incident report(s): check these first.")
    if f["machines_not_available"]:
        lines.append("Machines needing attention: " + "; ".join(
            f"{m['name']} ({m['status'].replace('_', ' ')})" for m in f["machines_not_available"]) + ".")
    if f.get("maintenance_due"):
        lines.append(f"{len(f['maintenance_due'])} maintenance task(s) due.")
    if f["escalations"]:
        lines.append("Open alerts: " + "; ".join(e["what"] for e in f["escalations"]) + ".")
    if f["pending_approvals"]:
        lines.append("Waiting for your approval: " + ", ".join(
            a["what"] for a in f["pending_approvals"]) + ".")
    if f.get("low_stock"):
        lines.append("Low stock: " + ", ".join(i["item"] for i in f["low_stock"]) + ".")
    return " ".join(lines) or "Nothing needs attention today."


SYSTEM = ("Write a short, plain morning briefing for makerspace staff from these facts, in a few "
          "sentences. Put injuries, fire, dangerous requests and machines out of service first, "
          "then what is waiting for approval, then stock. Describe each problem in plain words "
          "as the facts do; never write internal codes or ids. Do not add facts. "
          'Return {"summary": "..."}')


def for_llm(f: dict[str, Any]) -> dict[str, Any]:
    """The facts without codes or ids, so the model can't echo them."""
    g = {k: v for k, v in f.items() if k not in ("escalations", "pending_approvals")}
    g["open_alerts"] = [e["what"] for e in f["escalations"]]
    g["waiting_for_approval"] = [a["what"] for a in f["pending_approvals"]]
    g["machines_not_available"] = [{k: v for k, v in m.items() if k != "id"}
                                   for m in f["machines_not_available"]]
    return g


@registry.agent("briefing")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    f = facts(db)
    fallback = {"summary": plain_summary(f)}
    out = get_llm().complete_json("briefing.write", SYSTEM, str(for_llm(f)), lambda: fallback,
                                  max_tokens=400)
    summary = str(out.get("summary") or "").strip()
    if not summary or re.search(r"\bE\d{2}\b", summary):  # a code slipped through: use the facts
        summary = fallback["summary"]
    return AgentResult(agent="briefing", status="ok", output={"summary": summary, "facts": f})


def daily(db: Session) -> dict:
    r = run(db, {}, "briefing")
    notify(db, "staff", f"briefing:{r.output['facts']['date']}", "Daily briefing: " + r.output["summary"])
    return {"summary": r.output["summary"]}


registry.daily_job("zz_briefing")(daily)  # runs after the other daily jobs (sorted names)
