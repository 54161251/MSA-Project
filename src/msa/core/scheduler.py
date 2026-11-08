"""Daily check: runs every job registered with `@registry.daily_job(name)`."""

from __future__ import annotations

import logging

from msa.core.db import session_scope
from msa.core.plugins import load_plugins
from msa.core.registry import registry

log = logging.getLogger("msa.scheduler")
_scheduler = None


def run_daily() -> dict[str, dict]:
    load_plugins()
    results: dict[str, dict] = {}
    for name, fn in sorted(registry.daily_jobs.items()):
        try:
            with session_scope() as db:
                results[name] = fn(db) or {}
        except Exception as exc:  # one failing job must not stop the others
            log.exception("daily job %s failed", name)
            results[name] = {"error": str(exc)}
    return results


def start(hour: int = 7) -> None:
    global _scheduler
    if _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(run_daily, "cron", hour=hour, minute=0, id="msa-daily",
                       replace_existing=True)
    _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
