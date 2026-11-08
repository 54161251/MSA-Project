from sqlalchemy import select

from msa.agents.review.agent import review_document
from msa.core import orchestrator
from msa.core.models import Approval, Document, Escalation
from msa.core.testing import make_approved_doc, make_equipment

MANUAL = """[p.1]
## Personal protective equipment
- Wear safety glasses.
[p.2]
## Materials
- Allowed: plywood up to 6 mm.
- Never: PVC. It releases chlorine gas.
"""

REQUIRED = ["Before you start", "Operation", "Emergency"]


def setup(db, statements, category="laser_cutter", doc_type="materials"):
    eq = make_equipment(db, "laser-01", "Laser", category=category)
    manual = make_approved_doc(db, eq.id, "manual", MANUAL, "Manual")
    for st in statements:
        for c in st.get("citations", []):
            c.setdefault("document_id", manual.id)
            c.setdefault("version", 1)
    draft = Document(equipment_id=eq.id, type=doc_type, title="Draft", status="in_review",
                     statements=statements)
    db.add(draft)
    db.flush()
    return eq, manual, draft


def cite(locator, quote=""):
    return {"locator": locator, "quote": quote}


def test_uncited_safety_statement_is_e07(db):
    _, _, d = setup(db, [{"section": "PPE", "kind": "ppe", "text": "Wear a respirator."}])
    r = review_document(db, d)
    assert [i["code"] for i in r["issues"]] == ["E07"]


def test_citation_that_does_not_support_is_e07(db):
    _, _, d = setup(db, [{"section": "PPE", "kind": "ppe", "text": "Hearing protection is optional.",
                          "citations": [cite("p.1 Personal protective equipment")]}])
    r = review_document(db, d)
    assert r["issues"][0]["code"] == "E07" and "support" in r["issues"][0]["why"]


def test_bad_locator_is_e07(db):
    _, _, d = setup(db, [{"section": "PPE", "kind": "ppe", "text": "Wear safety glasses.",
                          "citations": [cite("p.9 Nowhere")]}])
    assert review_document(db, d)["issues"][0]["code"] == "E07"


def test_banned_material_listed_as_allowed_is_e09(db):
    _, _, d = setup(db, [{"section": "Material rules", "kind": "material",
                          "text": "Allowed: PVC up to 3 mm.",
                          "citations": [cite("p.2 Materials")]}])
    issues = review_document(db, d)["issues"]
    # the citation doesn't support it either way; the rule check must still catch it
    assert issues and issues[0]["code"] in ("E07", "E09")
    d.statements[0]["text"] = "Allowed: plywood up to 6 mm, and PVC"
    issues = review_document(db, d)["issues"]
    assert issues and issues[0]["code"] == "E09"


def test_staff_note_contradiction_is_e08(db):
    eq, _, d = setup(db, [{"section": "Material rules", "kind": "material",
                           "text": "Allowed: plywood up to 6 mm.",
                           "citations": [cite("p.2 Materials")]}])
    make_approved_doc(db, eq.id, "staff_note", "[p.1]\n## House rules\n- Never: plywood. Our "
                      "extraction can't handle the glue.", "House rules")
    assert review_document(db, d)["issues"][0]["code"] == "E08"


def test_full_flow_revises_then_requests_approval(db):
    eq = make_equipment(db, "laser-01", "Laser Cutter", brand="Lumenic", model_number="Vector 60",
                        category="laser_cutter")
    final, results = orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    agents = [r.agent for r in results]
    assert agents[:4] == ["intake", "review", "intake_revise", "review"]
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    assert not any("polycarbonate" in s["text"].lower() and s["text"].lower().startswith("allowed")
                   for s in sop.statements)
    assert sop.review["removed_in_revision"]
    assert db.scalar(select(Approval).where(Approval.target_id == sop.id))


def test_missing_sections_block_publishing(db):
    eq, _, d = setup(db, [{"section": "PPE", "kind": "ppe", "text": "Wear safety glasses.",
                           "citations": [cite("p.1 Personal protective equipment")]}],
                     category="drill_press", doc_type="sop")
    final, _ = orchestrator.run(db, "review", {"doc_ids": [d.id]}, "staff")
    assert final.status == "escalated" and "E09" in final.escalations
    assert db.scalar(select(Approval).where(Approval.target_id == d.id)) is None
    assert db.scalar(select(Escalation).where(Escalation.target_id == d.id))


def test_llm_style_drafts_still_publish(db):
    """A model rewords lines, shortens section labels and paraphrases quotes. Citations must be
    repointed at the real manual text and the SOP must still reach staff approval."""
    from msa.agents.intake.drafting import deterministic_statements
    from msa.core.llm import ScriptedLLM, set_llm

    eq = make_equipment(db, "saw-01", "Table Saw", brand="Kestrel", model_number="TS10",
                        category="table_saw")

    def draft(prompt):
        manual = db.scalar(select(Document).where(Document.equipment_id == eq.id,
                                                  Document.type == "manual"))
        out = []
        for s in deterministic_statements([manual]):
            c = s["citations"][0]
            page, heading = c["locator"].split(" ", 1)
            out.append({"section": s["section"].upper(), "kind": s["kind"],
                        "text": "Please remember: " + s["text"].lower(),
                        "citations": [{"document_id": c["document_id"], "version": "1",
                                       "locator": f"{page} {heading.split()[0]}",
                                       "quote": "roughly what it says"}]})
        return {"statements": out}

    set_llm(ScriptedLLM({"intake.draft_sop": draft,
                         "review.support": {"supported": True, "why": "same rule"}}))
    final, results = orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    assert all(st["citations"] for st in sop.statements if st["kind"] != "info")
    assert db.scalar(select(Approval).where(Approval.target_id == sop.id)), sop.review


def test_unusable_llm_citations_fall_back_to_the_manual(db):
    """If the model's citations can't be matched at all, Review removes those lines and the
    revise step refills the emptied sections with the manual's own cited lines."""
    from msa.core.llm import ScriptedLLM, set_llm

    eq = make_equipment(db, "drill-01", "Drill", brand="Ironleaf", model_number="DP17",
                        category="drill_press")
    bogus = {"statements": [
        {"section": sec, "kind": kind, "text": f"Something about {sec}.",
         "citations": [{"document_id": "nope", "version": 1, "locator": "page 99", "quote": ""}]}
        for sec, kind in [("PPE", "ppe"), ("Before you start", "step"), ("Operation", "step"),
                          ("Material rules", "material"), ("Emergency", "emergency")]]}
    set_llm(ScriptedLLM({"intake.draft_sop": bogus}))
    final, results = orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    assert "intake_revise" in [r.agent for r in results]
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    assert not any(s["text"].startswith("Something about") for s in sop.statements)
    assert db.scalar(select(Approval).where(Approval.target_id == sop.id)), sop.review


def test_real_claude_draft_publishes(db):
    """Regression: a real reply from Claude (2026-11-07) writes citation versions as "v1" and
    copies some manual lines word for word. Its citations must survive and the SOP publish."""
    import json
    from pathlib import Path

    from msa.core.llm import ScriptedLLM, set_llm

    reply = json.loads((Path(__file__).parent / "fixtures" / "claude_draft_sop_laser.json").read_text())
    set_llm(ScriptedLLM({"intake.draft_sop": reply}))
    eq = make_equipment(db, "laser-01", "Laser Cutter", brand="Lumenic", model_number="Vector 60",
                        category="laser_cutter")
    orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    claude_lines = [s for s in sop.statements if s["text"] in {x["text"] for x in reply["statements"]}]
    assert claude_lines and all(s["citations"] for s in claude_lines)
    assert db.scalar(select(Approval).where(Approval.target_id == sop.id)), sop.review
