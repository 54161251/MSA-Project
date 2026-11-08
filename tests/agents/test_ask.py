import pytest
from sqlalchemy import select

from msa.core import orchestrator, training
from msa.core.models import Approval, Document
from msa.core.services import decide_approval
from msa.core.testing import make_equipment, make_user


@pytest.fixture
def shop(db):
    eq = make_equipment(db, "laser-01", "Laser Cutter (Lumenic Vector 60)", brand="Lumenic",
                        model_number="Vector 60", category="laser_cutter")
    orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    staff = make_user(db, role="staff")
    for a in db.scalars(select(Approval).where(Approval.kind == "publish_doc",
                                               Approval.status == "pending")).all():
        decide_approval(db, a.id, True, staff)
    trained = make_user(db)
    training.set_state(db, trained.id, eq.id, "quiz_passed", "t")
    training.set_state(db, trained.id, eq.id, "certified", staff.id)
    return eq, trained, make_user(db)


def ask(db, user, q, eid=None):
    final, _ = orchestrator.run(db, "ask", {"user_id": user.id, "question": q, "equipment_id": eid},
                                user.id)
    return final


def test_answers_with_citations_from_approved_docs(db, shop):
    eq, trained, _ = shop
    r = ask(db, trained, "What should I check before I start the laser cutter?")
    assert r.status == "ok" and r.citations
    for c in r.citations:
        assert db.get(Document, c.document_id).status == "approved"


def test_emergency_skips_the_llm_and_alerts_staff(db, shop):
    _, trained, _ = shop
    r = ask(db, trained, "The laser is on fire what do I do")
    assert r.output["emergency"] and "E10" in r.escalations
    assert "911" in r.output["answer"]


def test_high_risk_is_refused(db, shop):
    _, trained, _ = shop
    r = ask(db, trained, "How do I bypass the lid interlock on the laser?")
    assert r.status == "refused" and "E04" in r.escalations


def test_material_questions_use_the_rules_tool(db, shop):
    _, trained, _ = shop
    r = ask(db, trained, "Can I cut PVC on the laser cutter?")
    assert r.output["answer"].startswith("No.")
    r = ask(db, trained, "Can I cut plywood on the laser cutter?")
    assert r.output["answer"].startswith("Yes") and r.citations
    r = ask(db, trained, "Can I cut polycarbonate on the laser cutter?")  # removed by Review
    assert r.output["answer"].startswith("No.")
    r = ask(db, trained, "Can I cut unobtainium on the laser?")
    assert "E01" in r.escalations


def test_how_to_requires_training(db, shop):
    _, _, untrained = shop
    r = ask(db, untrained, "How do I start a job on the laser cutter?")
    assert r.status == "refused" and "E05" in r.escalations


def test_unanswerable_is_refused(db, shop):
    _, trained, _ = shop
    r = ask(db, trained, "What is the wifi password for the office printer?")
    assert r.status == "refused" and "E03" in r.escalations


def test_retrieval_finds_manual_maintenance_and_dedups(db, shop):
    """Regression (real Claude run, 2026-11-07): the same line came back twice (SOP and training
    guide) and the manual's lens-cleaning line was missed, so Claude rightly refused."""
    from msa.core.llm import ScriptedLLM, set_llm

    seen = {}

    def capture(prompt):
        seen["prompt"] = prompt
        return {"answerable": True, "answer": "Clean the lens with lens wipes.", "used": [0]}

    set_llm(ScriptedLLM({"ask.answer": capture}))
    _, trained, _ = shop
    r = ask(db, trained, "How do I clean the lens on the laser cutter?")
    assert r.status == "ok"
    passages = [line for line in seen["prompt"].splitlines() if line.startswith("[")]
    assert any("lens wipes" in p for p in seen["prompt"].split("\n\n"))
    texts = [p.split("] ", 1)[1] for p in passages]
    assert len(texts) == len(set(texts))


def test_stemming_matches_solder_and_soldering():
    from msa.core.textsearch import tokens

    assert tokens("soldering")[0] == tokens("solder")[0]
    assert tokens("glasses")[0] == tokens("glass")[0]
