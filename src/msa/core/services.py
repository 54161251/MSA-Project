"""Shared functions every role uses (Assumptions doc C3-C5).

All of them take an open SQLAlchemy session and flush, but never commit: the caller
(an API request or `session_scope()`) owns the transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core import escalations
from msa.core.contracts import ApprovalKind, MachineStatus
from msa.core.db import utcnow
from msa.core.models import (
    Approval,
    AuditEvent,
    Equipment,
    Escalation,
    Notification,
    SiteSettings,
    User,
    to_dict,
)
from msa.core.registry import registry

STAFF = "staff"  # notification recipient meaning "every staff member"


class ServiceError(ValueError):
    """Raised for a request that breaks a rule (shown to the user as a 400)."""


# ------------------------------------------------------------------ audit


def audit(
    db: Session,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        actor=actor, created_by=actor, action=action, target_type=target_type,
        target_id=target_id, before=before, after=after,
    )
    db.add(ev)
    db.flush()
    return ev


# ------------------------------------------------------------------ events


def emit(db: Session, event: str, **payload: Any) -> None:
    """Synchronously call every handler registered with registry.on_event(event).

    A handler may raise ServiceError on purpose to block an action (e.g. the machine-use
    check). Any other crash in one role's handler is recorded as E14 instead of breaking the
    action that triggered it (a maintenance-plan bug must not stop an SOP from publishing)."""
    for fn in list(registry.event_handlers.get(event, [])):
        try:
            fn(db, **payload)
        except ServiceError:
            raise
        except Exception as exc:
            escalate(db, "E14", "event", event,
                     f"{getattr(fn, '__module__', '')}.{getattr(fn, '__name__', '')}: "
                     f"{type(exc).__name__}: {exc}"[:400], "platform")


# ----------------------------------------------------------- notifications


def notify(
    db: Session, recipient: str, dedup_key: str, message: str, severity: str = "info"
) -> Notification:
    """C4: one open notification per dedup_key. Repeats raise its count instead."""
    existing = db.scalar(
        select(Notification).where(
            Notification.dedup_key == dedup_key,
            Notification.recipient == recipient,
            Notification.status == "open",
        )
    )
    if existing:
        existing.count += 1
        existing.last_at = utcnow()
        if _sev_rank(severity) > _sev_rank(existing.severity):
            existing.severity = severity
        db.flush()
        return existing
    n = Notification(recipient=recipient, dedup_key=dedup_key, message=message, severity=severity)
    db.add(n)
    db.flush()
    return n


def resolve_notifications(db: Session, dedup_key: str) -> int:
    rows = db.scalars(
        select(Notification).where(
            Notification.dedup_key == dedup_key, Notification.status == "open"
        )
    ).all()
    for r in rows:
        r.status = "resolved"
    db.flush()
    return len(rows)


def _sev_rank(s: str) -> int:
    return {"info": 0, "warning": 1, "high": 2, "critical": 3}.get(s, 0)


# --------------------------------------------------------------- approvals


def request_approval(
    db: Session,
    kind: str,
    target_id: str,
    payload: dict | None = None,
    requested_by: str = "system",
    summary: str = "",
) -> Approval:
    """C3: create a pending approval (one pending per kind+target) and alert staff."""
    kind = ApprovalKind(kind).value
    existing = db.scalar(
        select(Approval).where(
            Approval.kind == kind, Approval.target_id == target_id, Approval.status == "pending"
        )
    )
    if existing:
        existing.payload = payload or existing.payload
        existing.summary = summary or existing.summary
        db.flush()
        return existing
    a = Approval(
        kind=kind, target_id=target_id, payload=payload or {}, summary=summary,
        created_by=requested_by,
    )
    db.add(a)
    db.flush()
    notify(db, STAFF, f"approval:{kind}:{target_id}",
           f"Approval needed ({kind.replace('_', ' ')}): {summary or target_id}", "info")
    return a


def decide_approval(
    db: Session, approval_id: str, approve: bool, staff: User, reason: str = ""
) -> Approval:
    """Staff approve or reject. Approve runs the handler the owning role registered."""
    if staff.role != "staff":
        raise ServiceError("Only staff can decide approvals")
    a = db.get(Approval, approval_id)
    if a is None:
        raise ServiceError("Approval not found")
    if a.status != "pending":
        raise ServiceError(f"Approval already {a.status}")
    if not approve and not reason.strip():
        raise ServiceError("A rejection needs a reason")
    handler = (registry.approve_handlers if approve else registry.reject_handlers).get(a.kind)
    if approve and handler is None:
        raise ServiceError(f"No handler registered for approval kind {a.kind!r}")
    if handler:
        handler(db, a, staff)
    a.status = "approved" if approve else "rejected"
    a.decided_by = staff.id
    a.decided_at = utcnow()
    a.reason = reason
    resolve_notifications(db, f"approval:{a.kind}:{a.target_id}")
    audit(db, staff.id, f"approval.{a.status}", "approval", a.id, None,
          {"kind": a.kind, "target_id": a.target_id, "reason": reason})
    db.flush()
    return a


# ------------------------------------------------------------- escalations


def escalate(
    db: Session,
    code: str,
    target_type: str = "",
    target_id: str = "",
    details: str = "",
    raised_by: str = "system",
) -> Escalation:
    """Raise an E-code (dedups while the same code+target is open) and alert staff."""
    meta = escalations.get(code)
    existing = db.scalar(
        select(Escalation).where(
            Escalation.code == code,
            Escalation.target_type == target_type,
            Escalation.target_id == target_id,
            Escalation.status == "open",
        )
    )
    if existing:
        if details and details not in existing.details:
            existing.details = (existing.details + "\n" + details).strip()
        db.flush()
        esc = existing
    else:
        esc = Escalation(
            code=code, target_type=target_type, target_id=target_id, details=details,
            created_by=raised_by,
        )
        db.add(esc)
        db.flush()
    severity = "critical" if code == "E10" else ("warning" if meta.blocking else "info")
    notify(db, STAFF, f"escalation:{code}:{target_type}:{target_id}",
           f"{code} {meta.title}: {details[:200]}", severity)
    return esc


def resolve_escalation(db: Session, esc_id: str, staff: User, resolution: str) -> Escalation:
    esc = db.get(Escalation, esc_id)
    if esc is None:
        raise ServiceError("Escalation not found")
    if not resolution.strip():
        raise ServiceError("Resolving an escalation needs a note")
    esc.status = "resolved"
    esc.resolved_by = staff.id
    esc.resolution = resolution
    resolve_notifications(db, f"escalation:{esc.code}:{esc.target_type}:{esc.target_id}")
    audit(db, staff.id, "escalation.resolved", "escalation", esc.id, None,
          {"code": esc.code, "resolution": resolution})
    db.flush()
    return esc


# ---------------------------------------------------------- machine status


def set_machine_status(
    db: Session,
    equipment_id: str,
    status: str,
    reason: str,
    actor: str,
    override: bool = False,
) -> Equipment:
    """C5: the only way to change a machine's status. Staff overrides need a reason."""
    status = MachineStatus(status).value
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        raise ServiceError(f"Unknown equipment {equipment_id}")
    if override and not reason.strip():
        raise ServiceError("A staff override needs a written reason")
    if eq.status == status and eq.status_reason == reason:
        return eq
    before = {"status": eq.status, "reason": eq.status_reason}
    eq.status = status
    eq.status_reason = reason
    audit(db, actor, "equipment.status_override" if override else "equipment.status",
          "equipment", eq.id, before, {"status": status, "reason": reason})
    emit(db, "equipment.status_changed", equipment=eq, before=before["status"], actor=actor)
    db.flush()
    return eq


# ---------------------------------------------------------------- settings


def site_settings(db: Session) -> SiteSettings:
    s = db.get(SiteSettings, "site")
    if s is None:
        s = SiteSettings(id="site")
        db.add(s)
        db.flush()
    return s


def row(obj: Any) -> dict:
    return to_dict(obj)
