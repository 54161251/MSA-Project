"""Build a demo database: platform data first, then every registered seeder in order.

    python -m msa.cli seed --reset --approve-all

--approve-all makes the seed staff account approve every draft the agents produce, so the
demo starts with published SOPs. Every approval is still recorded as a normal approval.
"""

from __future__ import annotations

from sqlalchemy import select

from msa.core.auth import create_user
from msa.core.config import get_settings
from msa.core.db import init_db, session_scope
from msa.core.models import User
from msa.core.registry import registry
from msa.core.services import site_settings


def seed_admin(db) -> User:
    s = get_settings()
    u = db.scalar(select(User).where(User.email == s.admin_email.lower()))
    if u is None:
        u = create_user(db, s.admin_name, s.admin_email, 1990, s.admin_password, role="staff",
                        actor="seed")
    return u


def run(reset: bool = False, approve_all: bool = False) -> list[str]:
    init_db(drop=reset)
    done = []
    with session_scope() as db:
        site_settings(db)
        seed_admin(db)
    done.append("platform")
    for s in sorted(registry.seeders, key=lambda s: s.order):
        with session_scope() as db:
            s.fn(db, approve_all=approve_all)
        done.append(s.name)
    return done


def staff_user(db) -> User:
    """The seed staff account (used by seeders that approve drafts with --approve-all)."""
    return seed_admin(db)
