"""All shared tables (Assumptions doc: Shared data model). R1 owns this file.

Every row has id, created_at and created_by (a user id or an agent name).
Other roles read and write through these models and msa.core.services, never raw SQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from msa.core.db import Base, new_id, utcnow


class _Row:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)


# ------------------------------------------------------------------ people


class User(_Row, Base):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    birth_year: Mapped[int] = mapped_column(Integer)
    guardian_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # none | self_signed | guardian_required | guardian_signed
    waiver_status: Mapped[str] = mapped_column(String(30), default="none")
    can_sign_in: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthToken(_Row, Base):
    __tablename__ = "auth_tokens"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class SiteSettings(_Row, Base):
    """One row (id='site'): makerspace-wide settings."""

    __tablename__ = "site_settings"
    name: Mapped[str] = mapped_column(String(200), default="Makerspace")
    emergency_number: Mapped[str] = mapped_column(String(40), default="911")
    emergency_note: Mapped[str] = mapped_column(Text, default="")
    maintenance_grace_days: Mapped[int] = mapped_column(Integer, default=7)
    display_units: Mapped[str] = mapped_column(String(10), default="ft")


# --------------------------------------------------------------- equipment


class Equipment(_Row, Base):
    __tablename__ = "equipment"
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(120), default="")
    model_number: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(60), default="other", index=True)
    # footprint and clearances are stored in metres
    footprint_w: Mapped[float] = mapped_column(Float, default=1.0)
    footprint_d: Mapped[float] = mapped_column(Float, default=1.0)
    clearance: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)  # front/back/left/right
    approval_mode: Mapped[str] = mapped_column(String(30), default="competency_check")
    prerequisites: Mapped[list[str]] = mapped_column(JSON, default=list)
    training_valid_months: Mapped[int] = mapped_column(Integer, default=12)
    status: Mapped[str] = mapped_column(String(30), default="available")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    specs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manual_ref: Mapped[str] = mapped_column(String(400), default="")
    video_links: Mapped[list[str]] = mapped_column(JSON, default=list)
    usage_hours: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")


class Document(_Row, Base):
    __tablename__ = "documents"
    equipment_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment.id"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    # structured safety statements with citations (see contracts.Statement)
    statements: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # Review agent summary
    supersedes_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MaterialRule(_Row, Base):
    __tablename__ = "material_rules"
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    material: Mapped[str] = mapped_column(String(120), index=True)
    verdict: Mapped[str] = mapped_column(String(20))  # allowed | banned | ask_staff
    reason: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------- training


class TrainingRecord(_Row, Base):
    """History table: newest row per (user, equipment) is the current state."""

    __tablename__ = "training_records"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="not_started")
    quiz_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class QuizBank(_Row, Base):
    __tablename__ = "quiz_banks"
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    pass_mark: Mapped[float] = mapped_column(Float, default=0.8)
    draw_count: Mapped[int] = mapped_column(Integer, default=10)
    guidelines: Mapped[str] = mapped_column(Text, default="")


class QuizAttempt(_Row, Base):
    __tablename__ = "quiz_attempts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    bank_id: Mapped[str] = mapped_column(ForeignKey("quiz_banks.id"))
    question_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    answers: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------- operations


class LogEntry(_Row, Base):
    """Maintenance / inspection log. Append-only (DB trigger)."""

    __tablename__ = "log_entries"
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # maintenance | inspection | correction
    task: Mapped[str] = mapped_column(String(200), default="")
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    usage_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    corrects_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MaintenancePlan(_Row, Base):
    __tablename__ = "maintenance_plans"
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    task: Mapped[str] = mapped_column(String(200))
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | retired
    last_done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_done_hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class UsageSession(_Row, Base):
    __tablename__ = "usage_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("equipment.id"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hours: Mapped[float | None] = mapped_column(Float, nullable=True)


class Report(_Row, Base):
    """Feedback, incident or near-miss. Facts frozen by trigger; status may change."""

    __tablename__ = "reports"
    kind: Mapped[str] = mapped_column(String(20))
    equipment_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment.id"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    cluster_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[str] = mapped_column(String(20), default="open")
    suggestion: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution: Mapped[str] = mapped_column(Text, default="")


class InventoryItem(_Row, Base):
    __tablename__ = "inventory_items"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    unit: Mapped[str] = mapped_column(String(40), default="each")
    category: Mapped[str] = mapped_column(String(60), default="material")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    reorder_point: Mapped[float] = mapped_column(Float, default=0)
    reorder_qty: Mapped[float] = mapped_column(Float, default=0)
    supplier: Mapped[str] = mapped_column(String(200), default="")
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class InventoryTxn(_Row, Base):
    """Stock change. Append-only: quantity on hand = sum of deltas."""

    __tablename__ = "inventory_txns"
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    delta: Mapped[float] = mapped_column(Float)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    source_text: Mapped[str] = mapped_column(Text, default="")


class OrderDraft(_Row, Base):
    __tablename__ = "order_drafts"
    supplier: Mapped[str] = mapped_column(String(200), default="")
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|rejected
    reason: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------- floor plan


class FloorPlan(_Row, Base):
    __tablename__ = "floor_plans"
    name: Mapped[str] = mapped_column(String(200), default="Main floor")
    image_path: Mapped[str] = mapped_column(String(400), default="")
    width_m: Mapped[float] = mapped_column(Float, default=10.0)
    height_m: Mapped[float] = mapped_column(Float, default=10.0)
    exits: Mapped[list[dict[str, float]]] = mapped_column(JSON, default=list)  # {x, y} metres
    # where the room's outline sits in the image, in image pixels {x0, y0, x1, y1};
    # empty means the room fills the whole image
    image_box: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)


class PlacedItem(_Row, Base):
    __tablename__ = "placed_items"
    plan_id: Mapped[str] = mapped_column(ForeignKey("floor_plans.id"), index=True)
    equipment_id: Mapped[str | None] = mapped_column(ForeignKey("equipment.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="object")  # equipment|object|safety
    shape: Mapped[str] = mapped_column(String(20), default="rect")  # rect|circle|oval|triangle
    label: Mapped[str] = mapped_column(String(200), default="")
    safety_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    x: Mapped[float] = mapped_column(Float, default=0)  # top-left, metres
    y: Mapped[float] = mapped_column(Float, default=0)
    w: Mapped[float] = mapped_column(Float, default=1)
    h: Mapped[float] = mapped_column(Float, default=1)
    rotation: Mapped[float] = mapped_column(Float, default=0)


# ------------------------------------------------------------ agent work


class ProjectCheck(_Row, Base):
    __tablename__ = "project_checks"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    verdict: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(20), default="open")


# ----------------------------------------------- human checkpoints & alerts


class Approval(_Row, Base):
    __tablename__ = "approvals"
    kind: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class Escalation(_Row, Base):
    __tablename__ = "escalations"
    code: Mapped[str] = mapped_column(String(8), index=True)
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution: Mapped[str] = mapped_column(Text, default="")


class Notification(_Row, Base):
    __tablename__ = "notifications"
    recipient: Mapped[str] = mapped_column(String(64), index=True)  # user id or "staff"
    dedup_key: Mapped[str] = mapped_column(String(200), index=True)
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    last_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(_Row, Base):
    """Every write to safety data. Append-only."""

    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class LLMCall(_Row, Base):
    """Telemetry for every LLM call (offline calls are recorded too)."""

    __tablename__ = "llm_calls"
    task: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")


def to_dict(row: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    """Plain dict of a row's columns (datetimes as ISO strings)."""
    exclude = exclude or set()
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        if col.name in exclude:
            continue
        val = getattr(row, col.name)
        out[col.name] = val.isoformat() if isinstance(val, datetime) else val
    return out
