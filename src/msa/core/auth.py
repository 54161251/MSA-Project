"""Accounts, age rules (A3), password hashing (G8) and bearer tokens."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa.core.db import get_db, utcnow
from msa.core.models import AuthToken, User
from msa.core.services import ServiceError, audit

MIN_AGE_SOLO = 15  # younger members must be linked to an adult and can't sign in
ADULT_AGE = 18
TOKEN_HOURS = 12


def hash_password(pw: str) -> str:
    if len(pw) < 8:
        raise ServiceError("Password must be at least 8 characters")
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode())


def age_of(birth_year: int, year: int | None = None) -> int:
    """Age from birth year only (G7: no full dates of birth are stored). May be off by one."""
    return (year or utcnow().year) - birth_year


def create_user(
    db: Session,
    name: str,
    email: str,
    birth_year: int,
    password: str | None = None,
    role: str = "member",
    guardian: User | None = None,
    agreed_to_terms: bool = False,
    actor: str = "self",
) -> User:
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise ServiceError("An account with that email already exists")
    if not agreed_to_terms and role != "staff":
        raise ServiceError("The user agreement must be accepted")
    age = age_of(birth_year)
    if age < 5 or age > 120:
        raise ServiceError("Birth year looks wrong")
    if guardian is not None and age_of(guardian.birth_year) < ADULT_AGE:
        raise ServiceError("A guardian must be an adult account")
    if age < MIN_AGE_SOLO and guardian is None:
        raise ServiceError(f"Members under {MIN_AGE_SOLO} must be linked to an adult account")
    if age < ADULT_AGE:
        waiver = "guardian_required"
    else:
        waiver = "self_signed"
    u = User(
        name=name.strip(), email=email, birth_year=birth_year, role=role,
        guardian_id=guardian.id if guardian else None, waiver_status=waiver,
        can_sign_in=age >= MIN_AGE_SOLO, created_by=actor,
        password_hash=hash_password(password) if password and age >= MIN_AGE_SOLO else None,
    )
    if age >= MIN_AGE_SOLO and not password:
        raise ServiceError("A password is required")
    db.add(u)
    db.flush()
    audit(db, actor, "user.created", "user", u.id, None,
          {"role": role, "waiver_status": waiver, "has_guardian": bool(guardian)})
    return u


def login(db: Session, email: str, password: str) -> tuple[str, User]:
    u = db.scalar(select(User).where(User.email == email.strip().lower()))
    if u is None or not u.active or not u.can_sign_in or not check_password(password, u.password_hash):
        raise ServiceError("Email or password is incorrect")
    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(user_id=u.id, token_hash=_h(raw), created_by=u.id,
                     expires_at=utcnow() + timedelta(hours=TOKEN_HOURS)))
    db.flush()
    return raw, u


def user_for_token(db: Session, raw: str) -> User | None:
    tok = db.scalar(select(AuthToken).where(AuthToken.token_hash == _h(raw)))
    if tok is None or tok.expires_at <= utcnow():
        return None
    u = db.get(User, tok.user_id)
    return u if u and u.active else None


def _h(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ------------------------------------------------------- FastAPI dependencies


def current_user(
    authorization: str = Header(default=""), db: Session = Depends(get_db)
) -> User:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required")
    u = user_for_token(db, authorization.split(" ", 1)[1].strip())
    if u is None:
        raise HTTPException(401, "Session expired, please sign in again")
    return u


def require_staff(user: User = Depends(current_user)) -> User:
    """G10: staff-only routes check the role on the server."""
    if user.role != "staff":
        raise HTTPException(403, "Staff only")
    return user
