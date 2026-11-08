"""Project Check suite: unsafe projects (should need staff) and safe controls.

Metrics: catch rate on unsafe projects, false-alarm rate on safe ones, expected codes found.
"""

from __future__ import annotations

from eval.suites.common import pct, summarize, user
from msa.core import orchestrator
from msa.core.db import session_scope

SUITE = "project_check"


def run_suite(cases: list[dict]) -> dict:
    rows = []
    for c in cases:
        with session_scope() as db:
            u = user(db, c["user"])
            final, _ = orchestrator.run(db, "project_check",
                                        {"user_id": u.id, "description": c["description"]}, u.id)
            verdict = final.output.get("verdict", final.status)
            codes = set(final.escalations)
            rules = {r for f in final.output.get("findings", []) for r in f.get("rule_ids", [])}
            ok = verdict == c["expect"] and set(c.get("codes", [])) <= codes \
                and set(c.get("rules", [])) <= rules
            rows.append({"id": c["id"], "expected": [c["expect"], c.get("codes", []),
                                                     c.get("rules", [])],
                         "got": [verdict, sorted(codes)], "ok": ok,
                         "unsafe": c["expect"] == "needs_staff"})
            db.rollback()
    unsafe = [r for r in rows if r["unsafe"]]
    safe = [r for r in rows if not r["unsafe"]]
    return summarize(rows, {
        "catch_rate": pct(sum(r["got"][0] == "needs_staff" for r in unsafe), len(unsafe)),
        "false_alarm_rate": pct(sum(r["got"][0] == "needs_staff" for r in safe), len(safe)),
        "exact": pct(sum(r["ok"] for r in rows), len(rows)),
    })
