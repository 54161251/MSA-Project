from sqlalchemy import select

from msa.core import orchestrator
from msa.core.contracts import AgentResult, Handoff
from msa.core.models import Escalation
from msa.core.registry import registry


def test_handoff_chain_runs_in_order(db):
    @registry.agent("t_first")
    def first(db, payload, actor):
        return AgentResult(agent="t_first", status="ok",
                           handoff=Handoff(agent="t_second", payload={"n": 1}))

    @registry.agent("t_second")
    def second(db, payload, actor):
        return AgentResult(agent="t_second", status="ok", output={"got": payload["n"]})

    final, all_results = orchestrator.run(db, "t_first", {}, "tester")
    assert [r.agent for r in all_results] == ["t_first", "t_second"]
    assert final.output == {"got": 1}


def test_loop_guard_raises_e14(db):
    @registry.agent("t_loop")
    def loop(db, payload, actor):
        return AgentResult(agent="t_loop", status="ok", handoff=Handoff(agent="t_loop"))

    final, results = orchestrator.run(db, "t_loop", {}, "tester")
    assert final.status == "error" and "E14" in final.escalations
    assert len(results) == orchestrator.MAX_RUNS_PER_AGENT + 1
    assert db.scalar(select(Escalation).where(Escalation.code == "E14"))


def test_agent_crash_becomes_escalation(db):
    @registry.agent("t_crash")
    def crash(db, payload, actor):
        raise RuntimeError("boom")

    final, _ = orchestrator.run(db, "t_crash", {}, "tester")
    assert final.status == "error" and "E14" in final.escalations
