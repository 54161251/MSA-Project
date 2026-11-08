"""Shared contracts (Assumptions doc C1-C7). Every role codes against these."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Role(str, Enum):
    STAFF = "staff"
    MEMBER = "member"


class MachineStatus(str, Enum):
    AVAILABLE = "available"
    ISSUE_REPORTED = "issue_reported"
    MAINTENANCE_DUE = "maintenance_due"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED = "retired"


#: statuses in which a trained member may still use the machine (C5)
USABLE_STATUSES = {
    MachineStatus.AVAILABLE.value,
    MachineStatus.ISSUE_REPORTED.value,
    MachineStatus.MAINTENANCE_DUE.value,
}
#: usable, but members see a warning
WARNING_STATUSES = {MachineStatus.ISSUE_REPORTED.value, MachineStatus.MAINTENANCE_DUE.value}


class TrainingState(str, Enum):
    NOT_STARTED = "not_started"
    QUIZ_PASSED = "quiz_passed"
    PENDING_CHECK = "pending_check"
    CERTIFIED = "certified"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApprovalMode(str, Enum):
    AUTO = "auto"
    COMPETENCY_CHECK = "competency_check"


class DocType(str, Enum):
    MANUAL = "manual"
    SPEC = "spec"
    SOP = "sop"
    TRAINING = "training"
    MATERIALS = "materials"
    STAFF_NOTE = "staff_note"


class DocStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"


class ApprovalKind(str, Enum):
    PUBLISH_DOC = "publish_doc"
    QUIZ_BANK = "quiz_bank"
    COMPETENCY_CHECK = "competency_check"
    MAINTENANCE_PLAN = "maintenance_plan"
    ORDER_DRAFT = "order_draft"
    LAYOUT_CHANGE = "layout_change"
    PROJECT_CHECK = "project_check"
    STATUS_OVERRIDE = "status_override"


class ReportKind(str, Enum):
    FEEDBACK = "feedback"
    INCIDENT = "incident"
    NEAR_MISS = "near_miss"


# ---------------------------------------------------------------- C1 / C2


class Citation(BaseModel):
    """Points at the exact place in an approved document that supports a statement."""

    document_id: str
    version: int = 1
    locator: str = Field(description='page/section/step, e.g. "p.3 Material rules"')
    quote: str = Field(default="", description="short supporting excerpt, under 40 words")


class Statement(BaseModel):
    """One safety-relevant sentence in a generated document (SOP, training, materials)."""

    section: str
    kind: Literal["ppe", "step", "warning", "material", "info", "emergency"] = "info"
    text: str
    citations: list[Citation] = []


class Handoff(BaseModel):
    agent: str
    payload: dict[str, Any] = {}


class AgentResult(BaseModel):
    agent: str
    status: Literal["ok", "needs_approval", "escalated", "refused", "error"]
    output: dict[str, Any] = {}
    citations: list[Citation] = []
    escalations: list[str] = []  # E-codes
    approvals: list[str] = []  # Approval ids created
    trace: list[str] = []  # tool calls made, shown on the dashboard
    handoff: Handoff | None = None  # ask the orchestrator to run another agent next


class ToolVerdict(BaseModel):
    """C7: every deterministic safety tool returns this shape."""

    verdict: str
    reasons: list[str] = []
    rule_ids: list[str] = []
    data: dict[str, Any] = {}
