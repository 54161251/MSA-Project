"""LangGraph orchestrator: runs an agent, follows its handoffs, applies loop guards.

Agents register with `@registry.agent("name")` and return an AgentResult. If the result
carries a `handoff`, the orchestrator runs that agent next (e.g. Intake -> Review ->
Intake revise -> Review). Guards (Assumptions doc, Escalation catalog):
  * at most 3 revise cycles: no agent may run more than 4 times in one request
  * at most 10 steps in one request
Hitting a guard raises E14 and stops the request.

State is checkpointed per request (thread id) so a run can be inspected afterwards.
"""

from __future__ import annotations

import contextvars
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from msa.core.config import get_settings
from msa.core.contracts import AgentResult
from msa.core.db import new_id
from msa.core.plugins import load_plugins
from msa.core.registry import registry

MAX_RUNS_PER_AGENT = 4  # first run + 3 revise cycles
MAX_STEPS = 10

_db: contextvars.ContextVar[Session] = contextvars.ContextVar("msa_db")


class FlowState(TypedDict, total=False):
    next_agent: str
    payload: dict[str, Any]
    actor: str
    results: list[dict[str, Any]]
    runs: dict[str, int]
    stopped: str


def _run_node(state: FlowState) -> FlowState:
    from msa.core.services import escalate

    db = _db.get()
    agent = state["next_agent"]
    runs = dict(state.get("runs", {}))
    runs[agent] = runs.get(agent, 0) + 1
    results = list(state.get("results", []))
    if runs[agent] > MAX_RUNS_PER_AGENT or len(results) >= MAX_STEPS:
        escalate(db, "E14", "agent", agent,
                 f"Loop guard: {agent} ran {runs[agent]} times / {len(results)} steps",
                 "orchestrator")
        res = AgentResult(agent=agent, status="error", escalations=["E14"],
                          output={"error": "loop guard hit"})
        return {"results": results + [res.model_dump()], "runs": runs, "stopped": "loop_guard"}
    fn = registry.agents.get(agent)
    if fn is None:
        res = AgentResult(agent=agent, status="error", output={"error": f"no agent {agent!r}"})
        return {"results": results + [res.model_dump()], "runs": runs, "stopped": "unknown"}
    try:
        connection = db.connection()
        if connection.dialect.name == "sqlite":
            # SQLite's legacy transaction mode can release the first savepoint
            # as a commit unless a real outer transaction already exists.
            driver = connection.connection.driver_connection
            if not driver.in_transaction:
                connection.exec_driver_sql("BEGIN")
        with db.begin_nested():
            res = fn(db, state.get("payload", {}), state.get("actor", "system"))
    except Exception as exc:  # an agent crash becomes an escalation, never a silent pass
        escalate(db, "E14", "agent", agent, f"{type(exc).__name__}: {exc}"[:400], "orchestrator")
        res = AgentResult(agent=agent, status="error", escalations=["E14"],
                          output={"error": str(exc)[:400]})
    out: FlowState = {"results": results + [res.model_dump()], "runs": runs}
    if res.handoff is not None:
        out["next_agent"] = res.handoff.agent
        out["payload"] = res.handoff.payload
    else:
        out["stopped"] = "done"
    return out


def _route(state: FlowState) -> str:
    return END if state.get("stopped") else "run"


_graph = None


def _checkpointer():
    path = get_settings().checkpoint_db
    if path in ("", "memory"):
        return MemorySaver()
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    except Exception:  # package missing or unwritable path: still run, just not persisted
        return MemorySaver()


def graph():
    global _graph
    if _graph is None:
        g = StateGraph(FlowState)
        g.add_node("run", _run_node)
        g.add_edge(START, "run")
        g.add_conditional_edges("run", _route, {"run": "run", END: END})
        _graph = g.compile(checkpointer=_checkpointer())
    return _graph


def run(db: Session, agent: str, payload: dict, actor: str) -> tuple[AgentResult, list[AgentResult]]:
    """Run `agent` (and any handoffs). Returns (final result, every result in order)."""
    load_plugins()
    token = _db.set(db)
    try:
        thread = {"configurable": {"thread_id": f"{agent}-{new_id()}"}}
        final = graph().invoke(
            {"next_agent": agent, "payload": payload, "actor": actor, "results": [], "runs": {}},
            thread,
        )
    finally:
        _db.reset(token)
    results = [AgentResult(**r) for r in final.get("results", [])]
    return results[-1], results
