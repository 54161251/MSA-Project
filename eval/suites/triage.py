"""Triage suite (R6): classification and duplicate grouping.

Each case: a list of reports submitted in order. Expected kind per report, and which reports
should land in the same cluster (same group letter).
Metrics: kind accuracy, incident recall (missing an incident is the worst error), grouping accuracy.
"""

from __future__ import annotations

from eval.suites.common import pct, summarize, user
from msa.core import orchestrator
from msa.core.db import session_scope

SUITE = "triage"


def run_suite(cases: list[dict]) -> dict:
    rows, incidents, incident_hits, pairs, pair_hits = [], 0, 0, 0, 0
    for c in cases:
        with session_scope() as db:
            got_kinds, clusters = [], []
            for rep in c["reports"]:
                u = user(db, rep.get("user", "priya@example.org"))
                final, _ = orchestrator.run(db, "triage", {"user_id": u.id, "text": rep["text"],
                                                           "equipment_id": rep.get("equipment_id")}, u.id)
                got_kinds.append(final.output.get("kind"))
                from msa.core.models import Report

                r = db.get(Report, final.output.get("report_id"))
                clusters.append(r.cluster_id if r else None)
            exp_kinds = [r["kind"] for r in c["reports"]]
            groups = [r.get("group") for r in c["reports"]]
            group_ok = True
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    if groups[i] is None or groups[j] is None:
                        continue
                    pairs += 1
                    same_exp = groups[i] == groups[j]
                    same_got = clusters[i] is not None and clusters[i] == clusters[j]
                    if same_exp == same_got:
                        pair_hits += 1
                    else:
                        group_ok = False
            for e, g in zip(exp_kinds, got_kinds, strict=True):
                if e == "incident":
                    incidents += 1
                    incident_hits += g == "incident"
            ok = got_kinds == exp_kinds and group_ok
            rows.append({"id": c["id"], "expected": exp_kinds, "got": got_kinds, "ok": ok})
            db.rollback()
    return summarize(rows, {"incident_recall": pct(incident_hits, incidents),
                            "grouping_accuracy": pct(pair_hits, pairs),
                            "exact": pct(sum(r["ok"] for r in rows), len(rows))})
