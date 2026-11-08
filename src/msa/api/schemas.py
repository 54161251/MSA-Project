"""Request bodies for the platform routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8)
    birth_year: int
    agreed_to_terms: bool = False


class DependentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200, description="placeholder address is fine")
    birth_year: int
    password: str | None = None
    agreed_to_terms: bool = False


class LoginIn(BaseModel):
    email: str
    password: str


class DecisionIn(BaseModel):
    approve: bool
    reason: str = ""


class NoteIn(BaseModel):
    note: str = ""


class StatusIn(BaseModel):
    status: str
    reason: str


class WaiverIn(BaseModel):
    waiver_status: str  # guardian_signed | guardian_required | self_signed


class TrainingIn(BaseModel):
    state: str
    reason: str = ""


class EquipmentPatch(BaseModel):
    name: str | None = None
    brand: str | None = None
    model_number: str | None = None
    category: str | None = None
    footprint_w: float | None = None
    footprint_d: float | None = None
    clearance: dict[str, float] | None = None
    approval_mode: str | None = None
    prerequisites: list[str] | None = None
    training_valid_months: int | None = None
    specs: dict[str, Any] | None = None
    video_links: list[str] | None = None
    notes: str | None = None


class SettingsPatch(BaseModel):
    name: str | None = None
    emergency_number: str | None = None
    emergency_note: str | None = None
    maintenance_grace_days: int | None = None
    display_units: str | None = None


class UsageIn(BaseModel):
    equipment_id: str
