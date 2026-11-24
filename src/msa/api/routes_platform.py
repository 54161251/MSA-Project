"""Platform routes: accounts, equipment records, documents, approvals, escalations,
notifications, settings, audit, usage sessions and telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa.api import schemas
from msa.core import training
from msa.core.auth import create_user, current_user, login, require_staff
from msa.core.contracts import USABLE_STATUSES, MachineStatus
from msa.core.db import get_db, utcnow
from msa.core.models import (
    Approval,
    AuditEvent,
    Document,
    Equipment,
    Escalation,
    LLMCall,
    Notification,
    TrainingRecord,
    UsageSession,
    User,
    to_dict,
)
from msa.core.services import (
    ServiceError,
    audit,
    decide_approval,
    emit,
    resolve_escalation,
    set_machine_status,
    site_settings,
)

router = APIRouter()


def public_user(u: User) -> dict:
    return to_dict(u, exclude={"password_hash"})


# ---------------------------------------------------------------- accounts


@router.post("/auth/signup", tags=["auth"])
def signup(body: schemas.SignupIn, db: Session = Depends(get_db)):
    u = create_user(db, body.name, body.email, body.birth_year, body.password,
                    agreed_to_terms=body.agreed_to_terms)
    return public_user(u)


@router.post("/auth/login", tags=["auth"])
def do_login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    token, u = login(db, body.email, body.password)
    return {"token": token, "user": public_user(u)}


@router.get("/auth/me", tags=["auth"])
def me(user: User = Depends(current_user)):
    return public_user(user)


@router.post("/users/me/dependents", tags=["users"])
def add_dependent(body: schemas.DependentIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """An adult links a younger member to their own account (A3)."""
    u = create_user(db, body.name, body.email, body.birth_year, body.password, guardian=user,
                    agreed_to_terms=body.agreed_to_terms, actor=user.id)
    return public_user(u)


@router.get("/users", tags=["users"])
def list_users(staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    return [public_user(u) for u in db.scalars(select(User).order_by(User.name)).all()]


@router.get("/users/{user_id}", tags=["users"])
def get_user(user_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != "staff" and user.id != user_id:
        raise HTTPException(403, "You can only view your own record")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "No such user")
    return {"user": public_user(u), "training": training_summary(db, u.id)}


@router.post("/users/{user_id}/waiver", tags=["users"])
def set_waiver(user_id: str, body: schemas.WaiverIn, staff: User = Depends(require_staff),
               db: Session = Depends(get_db)):
    if body.waiver_status not in ("guardian_signed", "guardian_required", "self_signed", "none"):
        raise ServiceError("Unknown waiver status")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "No such user")
    before = {"waiver_status": u.waiver_status}
    u.waiver_status = body.waiver_status
    audit(db, staff.id, "user.waiver", "user", u.id, before, {"waiver_status": u.waiver_status})
    return public_user(u)


@router.post("/users/{user_id}/training/{equipment_id}", tags=["users"])
def set_training(user_id: str, equipment_id: str, body: schemas.TrainingIn,
                 staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    rec = training.set_state(db, user_id, equipment_id, body.state, staff.id, body.reason)
    return to_dict(rec)


def training_summary(db: Session, user_id: str) -> list[dict]:
    out = []
    for eq in db.scalars(select(Equipment).where(Equipment.status != "retired")).all():
        rec = training.current_record(db, user_id, eq.id)
        out.append({
            "equipment_id": eq.id, "equipment_name": eq.name,
            "state": training.current_state(db, user_id, eq.id),
            "expires_at": rec.expires_at.isoformat() if rec and rec.expires_at else None,
            "quiz_score": rec.quiz_score if rec else None,
        })
    return out


# --------------------------------------------------------------- equipment


@router.get("/equipment", tags=["equipment"])
def list_equipment(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Equipment).order_by(Equipment.name)
    if user.role != "staff":
        q = q.where(Equipment.status != MachineStatus.RETIRED.value)
    return [to_dict(e) for e in db.scalars(q).all()]


@router.get("/equipment/{equipment_id}", tags=["equipment"])
def get_equipment(equipment_id: str, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    eq = db.get(Equipment, equipment_id)
    if eq is None or (user.role != "staff" and eq.status == "retired"):
        raise HTTPException(404, "No such equipment")
    q = select(Document).where(Document.equipment_id == eq.id)
    if user.role != "staff":
        q = q.where(Document.status == "approved")
    docs = [to_dict(d, exclude={"body"}) for d in db.scalars(q.order_by(Document.type)).all()]
    return {"equipment": to_dict(eq), "documents": docs,
            "my_training": training.current_state(db, user.id, eq.id)}


@router.patch("/equipment/{equipment_id}", tags=["equipment"])
def patch_equipment(equipment_id: str, body: schemas.EquipmentPatch,
                    staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    eq = db.get(Equipment, equipment_id)
    if eq is None:
        raise HTTPException(404, "No such equipment")
    changes = body.model_dump(exclude_none=True)
    if "approval_mode" in changes and changes["approval_mode"] not in ("auto", "competency_check"):
        raise ServiceError("approval_mode must be auto or competency_check")
    before = {k: getattr(eq, k) for k in changes}
    for k, v in changes.items():
        setattr(eq, k, v)
    audit(db, staff.id, "equipment.edit", "equipment", eq.id, before, changes)
    return to_dict(eq)


@router.post("/equipment/{equipment_id}/status", tags=["equipment"])
def override_status(equipment_id: str, body: schemas.StatusIn,
                    staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    eq = set_machine_status(db, equipment_id, body.status, body.reason, staff.id, override=True)
    return to_dict(eq)


# --------------------------------------------------------------- documents


@router.get("/documents/{doc_id}", tags=["documents"])
def get_document(doc_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if d is None or (user.role != "staff" and d.status != "approved"):
        raise HTTPException(404, "No such document")
    return to_dict(d)


@router.get("/documents", tags=["documents"])
def list_documents(status: str | None = None, staff: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    q = select(Document)
    if status:
        q = q.where(Document.status == status)
    return [to_dict(d, exclude={"body"}) for d in db.scalars(q.order_by(Document.created_at.desc()))]


# ------------------------------------------------- approvals and alerts


@router.get("/approvals", tags=["approvals"])
def list_approvals(status: str = "pending", staff: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    q = select(Approval).order_by(Approval.created_at.desc())
    if status != "all":
        q = q.where(Approval.status == status)
    return [to_dict(a) for a in db.scalars(q).all()]


@router.post("/approvals/{approval_id}/decide", tags=["approvals"])
def decide(approval_id: str, body: schemas.DecisionIn, staff: User = Depends(require_staff),
           db: Session = Depends(get_db)):
    return to_dict(decide_approval(db, approval_id, body.approve, staff, body.reason))


@router.get("/escalations", tags=["alerts"])
def list_escalations(status: str = "open", staff: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    q = select(Escalation).order_by(Escalation.created_at.desc())
    if status != "all":
        q = q.where(Escalation.status == status)
    names = {u.id: u.name for u in db.scalars(select(User)).all()}
    return [{**to_dict(e), **_readable(db, e, names)} for e in db.scalars(q).all()]


URGENT_CODES = {"E10", "E04", "E14"}
INFO_CODES = {"E11", "E12", "E15"}  # don't block anyone; for staff to know


def _readable(db: Session, e: Escalation, names: dict[str, str]) -> dict:
    """Plain-language fields for the Alerts page: what it is, who/what it's about, urgency."""
    from msa.core import escalations
    from msa.core.models import FloorPlan, ProjectCheck, Report

    about = ""
    t, tid = e.target_type, e.target_id
    if t == "ask":
        about = f"Member: {names.get(tid, 'unknown member')}"
    elif t == "equipment":
        eq = db.get(Equipment, tid)
        about = f"Machine: {eq.name if eq else tid}"
    elif t == "report":
        rep = db.get(Report, tid)
        if rep is not None:
            eq = db.get(Equipment, rep.equipment_id) if rep.equipment_id else None
            who = names.get(rep.user_id or "", "a member")
            about = f"Report by {who}" + (f" about the {eq.name}" if eq else "")
    elif t == "project_check":
        pc = db.get(ProjectCheck, tid)
        about = f"Project by {names.get(pc.user_id, 'a member')}" if pc else "Member project"
    elif t == "document":
        doc = db.get(Document, tid)
        about = f"Document: {doc.title}" if doc else "Drafted document"
    elif t == "floorplan":
        plan = db.get(FloorPlan, tid)
        about = f"Floor plan: {plan.name}" if plan else "Floor plan"
    elif t in ("agent", "event"):
        about = f"System: {tid}"
    details = e.details or ""
    for uid, name in names.items():  # "3f3c...: not trained" -> "Marcus Lee: not trained"
        details = details.replace(uid, name)
    what = escalations.explain(e.code, 1).split(" ", 1)[1]
    return {"what": what[0].upper() + what[1:], "about": about, "details_readable": details,
            "urgency": "urgent" if e.code in URGENT_CODES else
                       ("info" if e.code in INFO_CODES else "action")}


@router.post("/escalations/{esc_id}/resolve", tags=["alerts"])
def resolve(esc_id: str, body: schemas.NoteIn, staff: User = Depends(require_staff),
            db: Session = Depends(get_db)):
    return to_dict(resolve_escalation(db, esc_id, staff, body.note))


@router.get("/notifications", tags=["alerts"])
def list_notifications(status: str = "open", user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    recipients = [user.id] + (["staff"] if user.role == "staff" else [])
    q = select(Notification).where(Notification.recipient.in_(recipients))
    if status != "all":
        q = q.where(Notification.status == status)
    return [to_dict(n) for n in db.scalars(q.order_by(Notification.last_at.desc())).all()]


@router.post("/notifications/{nid}/resolve", tags=["alerts"])
def resolve_notification(nid: str, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if n is None or (n.recipient != user.id and user.role != "staff"):
        raise HTTPException(404, "No such notification")
    n.status = "resolved"
    return to_dict(n)


# ------------------------------------------------------ settings & audit


@router.get("/settings", tags=["settings"])
def get_settings_route(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return to_dict(site_settings(db))


@router.patch("/settings", tags=["settings"])
def patch_settings(body: schemas.SettingsPatch, staff: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    s = site_settings(db)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(s, k, v)
    audit(db, staff.id, "settings.edit", "settings", "site", None, changes)
    return to_dict(s)


@router.get("/audit", tags=["settings"])
def list_audit(limit: int = 200, staff: User = Depends(require_staff),
               db: Session = Depends(get_db)):
    q = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(limit, 1000))
    return [to_dict(a) for a in db.scalars(q).all()]


@router.get("/telemetry/llm", tags=["settings"])
def llm_telemetry(staff: User = Depends(require_staff)):
    from msa.core.llm import telemetry_engine

    with Session(telemetry_engine()) as tdb:
        rows = tdb.execute(
            select(LLMCall.task, LLMCall.provider, func.count(), func.sum(LLMCall.input_tokens),
                   func.sum(LLMCall.output_tokens), func.sum(1 - LLMCall.ok))
            .group_by(LLMCall.task, LLMCall.provider)
        ).all()
    return [{"task": t, "provider": p, "calls": n, "input_tokens": i or 0,
             "output_tokens": o or 0, "failures": f or 0} for t, p, n, i, o, f in rows]


@router.post("/admin/run-daily", tags=["settings"])
def run_daily_now(staff: User = Depends(require_staff)):
    from msa.core.scheduler import run_daily

    return run_daily()


# ----------------------------------------------------------- usage sessions


@router.post("/usage/start", tags=["usage"])
def start_usage(body: schemas.UsageIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    eq = db.get(Equipment, body.equipment_id)
    if eq is None:
        raise HTTPException(404, "No such equipment")
    if eq.status not in USABLE_STATUSES:
        raise ServiceError(f"{eq.name} is {eq.status.replace('_', ' ')}")
    emit(db, "usage.before_start", user=user, equipment=eq)  # R3 adds the permission check
    open_s = db.scalar(select(UsageSession).where(UsageSession.user_id == user.id,
                                                  UsageSession.end.is_(None)))
    if open_s:
        raise ServiceError("You already have a machine session open; end it first")
    s = UsageSession(user_id=user.id, equipment_id=eq.id, created_by=user.id)
    db.add(s)
    db.flush()
    return to_dict(s)


@router.post("/usage/stop", tags=["usage"])
def stop_usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.scalar(select(UsageSession).where(UsageSession.user_id == user.id,
                                             UsageSession.end.is_(None)))
    if s is None:
        raise ServiceError("No open machine session")
    s.end = utcnow()
    s.hours = round((s.end - s.start).total_seconds() / 3600, 3)
    eq = db.get(Equipment, s.equipment_id)
    if eq:
        eq.usage_hours = round((eq.usage_hours or 0) + s.hours, 3)
    return to_dict(s)


@router.get("/training/records", tags=["users"])
def all_training(staff: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = select(TrainingRecord).order_by(TrainingRecord.created_at.desc()).limit(500)
    return [to_dict(r) for r in db.scalars(q).all()]
