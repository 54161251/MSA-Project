"""Helpers shared by suites."""

from __future__ import annotations

from sqlalchemy import select

from msa.core.models import User


def user(db, email: str) -> User:
    u = db.scalar(select(User).where(User.email == email))
    if u is None:
        raise KeyError(f"eval user {email} not in seed data")
    return u


def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.0f}%" if d else "n/a"


def summarize(rows: list[dict], metrics: dict) -> dict:
    return {"total": len(rows), "passed": sum(r["ok"] for r in rows), "metrics": metrics,
            "failures": [{"id": r["id"], "expected": r["expected"], "got": r["got"]}
                         for r in rows if not r["ok"]],
            "cases": rows}
