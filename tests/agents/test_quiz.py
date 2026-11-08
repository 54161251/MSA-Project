from datetime import timedelta

import pytest
from sqlalchemy import select

from msa.agents.quiz import agent as quiz
from msa.core import orchestrator, training
from msa.core.models import Approval, QuizAttempt, QuizBank
from msa.core.services import ServiceError, decide_approval
from msa.core.testing import make_equipment, make_user


def published_machine(db, eid="drill-01", mode="auto", brand="Ironleaf", model="DP17",
                      cat="drill_press"):
    eq = make_equipment(db, eid, name=f"{eid} machine", brand=brand, model_number=model,
                        category=cat, approval_mode=mode)
    orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    staff = make_user(db, role="staff")
    for _ in range(3):
        for a in db.scalars(select(Approval).where(Approval.status == "pending",
                                                   Approval.kind.in_(["publish_doc", "quiz_bank"]))):
            decide_approval(db, a.id, True, staff)
    return eq, staff


def answer_all(db, attempt_id, correct=True):
    a = db.get(QuizAttempt, attempt_id)
    bank = db.get(QuizBank, a.bank_id)
    qs = {q["id"]: q for q in bank.questions}
    return {qid: (qs[qid]["answer_index"] if correct else (qs[qid]["answer_index"] + 1) %
                  len(qs[qid]["options"])) for qid in a.question_ids}


def test_bank_is_generated_from_approved_docs_and_cites_sources(db):
    eq, _ = published_machine(db)
    bank = quiz.approved_bank(db, eq.id)
    assert bank is not None and len(bank.questions) >= 5
    assert all(q["citations"] for q in bank.questions)
    assert all(0 <= q["answer_index"] < len(q["options"]) for q in bank.questions)


def test_quiz_refused_without_approved_sop(db):
    eq = make_equipment(db, "x-01", category="drill_press")
    res = quiz.generate(db, {"equipment_id": eq.id}, "staff")
    assert res.status == "refused"


def test_auto_mode_certifies_on_pass(db):
    eq, _ = published_machine(db)
    u = make_user(db)
    attempt, questions = quiz.start_attempt(db, u, eq.id)
    assert "answer_index" not in questions[0]
    res = quiz.submit_attempt(db, u, attempt.id, answer_all(db, attempt.id))
    assert res["passed"] and training.current_state(db, u.id, eq.id) == "certified"


def test_competency_mode_waits_for_staff(db):
    eq, staff = published_machine(db, "saw-01", "competency_check", "Kestrel", "TS10", "table_saw")
    u = make_user(db)
    attempt, _ = quiz.start_attempt(db, u, eq.id)
    quiz.submit_attempt(db, u, attempt.id, answer_all(db, attempt.id))
    assert training.current_state(db, u.id, eq.id) == "pending_check"
    a = db.scalar(select(Approval).where(Approval.kind == "competency_check"))
    decide_approval(db, a.id, True, staff)
    assert training.current_state(db, u.id, eq.id) == "certified"


def test_failed_attempt_blocks_retake_for_24h(db):
    eq, _ = published_machine(db)
    u = make_user(db)
    attempt, _ = quiz.start_attempt(db, u, eq.id)
    res = quiz.submit_attempt(db, u, attempt.id, answer_all(db, attempt.id, correct=False))
    assert not res["passed"]
    with pytest.raises(ServiceError):
        quiz.start_attempt(db, u, eq.id)
    attempt.submitted_at = attempt.submitted_at - timedelta(hours=25)
    db.flush()
    quiz.start_attempt(db, u, eq.id)  # allowed again
