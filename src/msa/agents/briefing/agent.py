"""Staff Briefing: collects the facts deterministically, then (optionally) has the LLM write
them up as a short summary. The facts are always shown too, so nothing depends on the prose."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa.core.contracts import AgentResult
from msa.core.db import utcnow
from msa.core.llm import get_llm
from msa.core.models import Approval, Equipment, Escalation, Report
from msa.core.registry import registry
from msa.core.services import notify


def facts(db: Session) -> dict[str, Any]:
    out: dict[str, Any] = {"date": utcnow().date().isoformat()}
    out["machines_not_available"] = [
        {"id": e.id, "name": e.name, "status": e.status, "reason": e.status_reason}
        for e in db.scalars(select(Equipment).where(Equipment.status.notin_(["available", "retired"])))]
    out["escalations"] = [
        {"code": code, "count": n} for code, n in db.execute(
            select(Escalation.code, func.count()).where(Escalation.status == "open")
            .group_by(Escalation.code).order_by(Escalation.code)).all()]
    out["pending_approvals"] = [
        {"kind": k, "count": n} for k, n in db.execute(
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
    n_appr = sum(a["count"] for a in f["pending_approvals"])
    if n_appr:
        lines.append(f"{n_appr} approval(s) waiting.")
    n_esc = sum(e["count"] for e in f["escalations"])
    if n_esc:
        lines.append(f"{n_esc} open escalation(s): " + ", ".join(
            f"{e['code']} x{e['count']}" for e in f["escalations"]) + ".")
    if f.get("low_stock"):
        lines.append("Low stock: " + ", ".join(i["item"] for i in f["low_stock"]) + ".")
    return " ".join(lines) or "Nothing needs attention today."


SYSTEM = ("Write a short, plain morning briefing for makerspace staff from these facts. Put "
          "anything about injuries, fire or machines out of service first. Do not add facts. "
          'Return {"summary": "..."}')


@registry.agent("briefing")
def run(db: Session, payload: dict, actor: str) -> AgentResult:
    f = facts(db)
    fallback = {"summary": plain_summary(f)}
    out = get_llm().complete_json("briefing.write", SYSTEM, str(f), lambda: fallback, max_tokens=400)
    return AgentResult(agent="briefing", status="ok",
                       output={"summary": str(out.get("summary") or fallback["summary"]), "facts": f})


def daily(db: Session) -> dict:
    r = run(db, {}, "briefing")
    notify(db, "staff", f"briefing:{r.output['facts']['date']}", "Daily briefing: " + r.output["summary"])
    return {"summary": r.output["summary"]}


registry.daily_job("zz_briefing")(daily)  # runs after the other daily jobs (sorted names)
