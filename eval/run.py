"""Evaluation runner (R5).

    python -m eval.run                 # every suite, offline (deterministic) mode
    python -m eval.run --suite ask     # one suite
    MSA_LLM_PROVIDER=anthropic python -m eval.run   # same cases against the real model
    python -m eval.run --offline-build              # build without the LLM; suites use it

Builds a fresh in-memory copy of the demo makerspace (seed with --approve-all), then runs each
suite in eval/suites/. Expected results are written in the case files BEFORE running (G13):
do not edit a case to make it pass; record the failure instead.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys
from pathlib import Path

os.environ.setdefault("MSA_CHECKPOINT_DB", "memory")
os.environ.setdefault("MSA_SCHEDULER", "off")
os.environ.setdefault("MSA_TELEMETRY_DB", "memory")

ROOT = Path(__file__).resolve().parent
SETS = ROOT / "sets"
RESULTS = ROOT / "results"


def build_makerspace(offline_build: bool = False) -> dict:
    """Seed a fresh in-memory makerspace and report what didn't publish."""
    import time

    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from msa.core import db as dbmod
    from msa.core import seed
    from msa.core.db import session_scope
    from msa.core.docs import approved_docs
    from msa.core.llm import telemetry_engine
    from msa.core.models import Equipment, Escalation, LLMCall

    t0 = time.time()
    print("Building the demo makerspace (with a real LLM this takes a while)...", flush=True)
    dbmod.configure("sqlite://")
    if offline_build:
        from msa.core.llm import LLM, set_llm

        set_llm(LLM())  # deterministic drafting: only the suites below use the real model
    seed.run(reset=True, approve_all=True)
    if offline_build:
        set_llm(None)
    report: dict = {"seconds": round(time.time() - t0), "offline_build": offline_build}
    with session_scope() as db:
        report["machines_without_approved_sop"] = [
            e.id for e in db.scalars(select(Equipment)) if not approved_docs(db, e.id, ["sop"])]
        report["open_escalations"] = [
            {"code": e.code, "target": e.target_id, "details": e.details[:300]}
            for e in db.scalars(select(Escalation).where(Escalation.status == "open"))]
    with Session(telemetry_engine()) as t:
        report["llm_calls"] = t.scalar(select(func.count()).select_from(LLMCall)) or 0
        report["llm_failures"] = t.scalar(select(func.count()).select_from(LLMCall)
                                          .where(LLMCall.ok.is_(False))) or 0
        last = t.scalar(select(LLMCall.error).where(LLMCall.ok.is_(False)).limit(1))
        report["first_llm_error"] = (last or "")[:300]
    print(f"  built in {report['seconds']} s, {report['llm_calls']} LLM calls, "
          f"{report['llm_failures']} failed", flush=True)
    if report["llm_failures"]:
        print(f"  first LLM error: {report['first_llm_error']}", flush=True)
    if report["machines_without_approved_sop"]:
        print(f"  WARNING: no approved SOP for {', '.join(report['machines_without_approved_sop'])}",
              flush=True)
    return report


def load_suites() -> dict:
    import eval.suites as pkg

    suites = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"eval.suites.{info.name}")
        if hasattr(mod, "run_suite"):
            suites[getattr(mod, "SUITE", info.name)] = mod
    return suites


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--suite", default=None)
    p.add_argument("--offline-build", action="store_true",
                   help="build the demo makerspace without the LLM (about 100 LLM calls instead "
                        "of about 1,000); the suites still use the configured LLM")
    args = p.parse_args(argv)
    from msa.core.config import get_settings

    build = build_makerspace(args.offline_build)
    suites = load_suites()
    report = {"llm_provider": get_settings().llm_provider, "build": build, "suites": {}}
    for name, mod in sorted(suites.items()):
        if args.suite and name != args.suite:
            continue
        case_file = SETS / f"{name}.json"
        if not case_file.exists():
            continue
        cases = json.loads(case_file.read_text(encoding="utf-8"))
        print(f"Running suite {name} ({len(cases)} cases)...", flush=True)
        try:
            report["suites"][name] = mod.run_suite(cases)
        except Exception as exc:  # one broken suite must not hide the others' results
            import traceback

            report["suites"][name] = {"total": len(cases), "passed": 0,
                                      "metrics": {"error": f"{type(exc).__name__}: {exc}"[:200]},
                                      "failures": [], "trace": traceback.format_exc()[-2000:]}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_table(report)
    return 0


def print_table(report: dict) -> None:
    print(f"\nEvaluation results (LLM provider: {report['llm_provider']})\n")
    b = report.get("build", {})
    missing = ", ".join(b.get("machines_without_approved_sop", [])) or "none"
    print(f"Build: {b.get('llm_calls', 0)} LLM calls, {b.get('llm_failures', 0)} failed, "
          f"machines without an approved SOP: {missing}")
    for e in b.get("open_escalations", [])[:12]:
        print(f"    {e['code']} {e['target'][:12]}: {e['details'][:140]}")
    print()
    print(f"{'suite':<16}{'cases':>7}{'passed':>8}  metrics")
    for name, r in report["suites"].items():
        m = ", ".join(f"{k}={v}" for k, v in r["metrics"].items())
        print(f"{name:<16}{r['total']:>7}{r['passed']:>8}  {m}")
        for f in r["failures"]:
            print(f"    FAIL {f['id']}: expected {f['expected']}, got {f['got']}")
    print(f"\nDetails: {RESULTS / 'latest.json'}")


if __name__ == "__main__":
    sys.exit(main())
