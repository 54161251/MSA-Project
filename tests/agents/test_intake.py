from sqlalchemy import select

from msa.agents.intake.library import find_manual, read_upload
from msa.agents.intake.specs import parse_dims
from msa.core import orchestrator
from msa.core.docs import verify_citation
from msa.core.models import Approval, Document, Escalation, MaterialRule
from msa.core.services import decide_approval
from msa.core.testing import make_equipment, make_user


def intake(db, eid, **kw):
    eq = make_equipment(db, eid, **kw)
    final, results = orchestrator.run(db, "intake", {"equipment_id": eq.id}, "staff")
    return eq, final, results


def test_parse_dims_units():
    assert parse_dims("1.45 m x 0.95 m") == (1.45, 0.95)
    assert parse_dims("600 mm x 300 mm") == (0.6, 0.3)
    assert parse_dims("24 in x 12 in") == (0.61, 0.305)
    assert parse_dims("big") is None


def test_find_manual_by_brand_and_model():
    assert find_manual("Lumenic", "Vector 60").name == "lumenic-vector-60.md"
    assert find_manual("Thimble & Co.", "T900").name == "thimble-t900.md"
    assert find_manual("Nobody", "X1") is None


def test_intake_drafts_cited_docs_and_sets_footprint(db):
    eq, final, results = intake(db, "laser-01", name="Laser", brand="Lumenic",
                                model_number="Vector 60", category="laser_cutter")
    assert results[0].agent == "intake"
    assert (eq.footprint_w, eq.footprint_d) == (1.45, 0.95)
    docs = db.scalars(select(Document).where(Document.equipment_id == eq.id,
                                             Document.type == "sop")).all()
    assert len(docs) == 1
    sop = docs[0]
    sections = {s["section"] for s in sop.statements}
    assert {"PPE", "Before you start", "Operation", "Material rules", "Emergency"} <= sections
    for st in sop.statements:
        assert st["citations"], st
        for c in st["citations"]:
            assert verify_citation(db, c, require_approved=False) is None


def test_missing_footprint_raises_e12(db):
    intake(db, "sew-01", name="Sewing", brand="Thimble & Co.", model_number="T900",
           category="sewing")
    assert db.scalar(select(Escalation).where(Escalation.code == "E12",
                                              Escalation.target_id == "sew-01"))


def test_unknown_manual_escalates_e02(db):
    _, final, _ = intake(db, "mystery-01", name="Mystery", brand="Acme", model_number="Z9")
    assert final.status == "escalated" and "E02" in final.escalations


def test_staff_upload_is_used_and_staff_notes_become_sources(db):
    eq = make_equipment(db, "glue-01", name="Hot glue station", category="other")
    text = read_upload("manual.md", b"[p.1]\n## Personal protective equipment\n- Do not touch "
                       b"the nozzle.\n[p.2]\n## Emergency\n- Cool burns under water.\n")
    payload = {"equipment_id": eq.id, "manual_text": text,
               "staff_notes": [{"title": "Personal protective equipment",
                                "text": "- Use the silicone mat."}]}
    final, results = orchestrator.run(db, "intake", payload, "staff")
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    texts = [s["text"] for s in sop.statements]
    assert "Use the silicone mat." in texts and "Do not touch the nozzle." in texts


def test_publishing_materials_doc_builds_material_rules(db):
    eq, final, results = intake(db, "drill-01", name="Drill", brand="Ironleaf",
                                model_number="DP17", category="drill_press")
    staff = make_user(db, role="staff")
    for a in db.scalars(select(Approval).where(Approval.kind == "publish_doc")).all():
        decide_approval(db, a.id, True, staff)
    rules = {r.material: r.verdict for r in
             db.scalars(select(MaterialRule).where(MaterialRule.equipment_id == eq.id))}
    assert rules["acrylic"] == "allowed" and rules["steel"] == "allowed"
    manual = db.scalar(select(Document).where(Document.equipment_id == eq.id,
                                              Document.type == "manual"))
    assert manual.status == "approved"  # approving the SOP confirmed its source manual


def test_messy_model_output_is_coerced_and_completed(db):
    """Different key names, unknown kinds, string citations, a missing section, paraphrased
    materials: the SOP still has every required section and the manual's exact material lines."""
    from msa.agents.intake.drafting import coerce_statement
    from msa.core.llm import ScriptedLLM, set_llm

    s = coerce_statement({"statement": "Wear safety glasses.", "type": "Personal Protective Equipment",
                          "heading": "Personal Protective Equipment",
                          "sources": ["[abc12345 v1 | p.2 Personal protective equipment]"]})
    assert s.kind == "ppe" and s.section == "PPE" and s.citations[0].document_id == "abc12345"

    messy = {"statements": [
        {"section": "Safety Gear", "kind": "PPE", "statement": "Always wear safety glasses.",
         "sources": [{"doc_id": "x", "page": "p.2 PPE", "excerpt": "glasses"}]},
        {"section": "Materials", "kind": "material", "text": "Wood and plastics are fine."}]}
    set_llm(ScriptedLLM({"intake.draft_sop": messy,
                         "intake.extract_specs": {"specs": {}, "footprint_m": None,
                                                  "confidence": {"footprint": 0.1}}}))
    eq, final, results = intake(db, "drill-01", name="Drill", brand="Ironleaf", model_number="DP17",
                                category="drill_press")
    assert (eq.footprint_w, eq.footprint_d) == (0.6, 0.45)  # spec table value wins
    assert not db.scalar(select(Escalation).where(Escalation.code == "E12"))
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    sections = {s["section"] for s in sop.statements}
    assert {"PPE", "Before you start", "Operation", "Material rules", "Emergency"} <= sections
    mats = [s["text"] for s in sop.statements if s["section"] == "Material rules"]
    assert "Wood and plastics are fine." not in mats
    assert "Allowed: acrylic at low speed." in mats


def _publish_all(db, staff):
    for a in db.scalars(select(Approval).where(Approval.kind == "publish_doc",
                                               Approval.status == "pending")).all():
        decide_approval(db, a.id, True, staff)


def _rules(db, eid):
    return {r.material: r for r in
            db.scalars(select(MaterialRule).where(MaterialRule.equipment_id == eid))}


def test_material_rules_come_from_the_subject_not_the_qualifier(db):
    """'Never: metal with the wood blade.' bans metals on the bandsaw, not wood."""
    eq, _, _ = intake(db, "band-01", name="Bandsaw", brand="Kestrel", model_number="BX14",
                      category="bandsaw")
    _publish_all(db, make_user(db, role="staff"))
    rules = _rules(db, eq.id)
    assert "wood" not in rules
    assert rules["aluminum"].verdict == "banned" and rules["steel"].verdict == "banned"
    assert rules["softwood"].verdict == "allowed" and rules["acrylic"].verdict == "allowed"


def test_house_rules_in_staff_notes_reach_the_sop_and_win(db):
    eq = make_equipment(db, "drill-01", name="Drill", brand="Ironleaf", model_number="DP17",
                        category="drill_press")
    note = "- Never: acrylic. It cracks in our vise.\n- Book the drill before use."
    orchestrator.run(db, "intake", {"equipment_id": eq.id, "staff_notes": [
        {"title": "House rules", "text": note}]}, "staff")
    sop = db.scalar(select(Document).where(Document.equipment_id == eq.id, Document.type == "sop"))
    texts = {s["text"]: s["section"] for s in sop.statements}
    assert texts.get("Never: acrylic. It cracks in our vise.") == "Material rules"
    assert texts.get("Book the drill before use.") == "Warnings"
    _publish_all(db, make_user(db, role="staff"))
    rules = _rules(db, eq.id)
    assert rules["acrylic"].verdict == "banned"
    assert rules["steel"].verdict == "allowed"  # the rest of the manual's list still applies


def test_pure_helpers():
    from msa.agents.intake.drafting import rule_materials

    assert parse_dims("800 x 600 x 400 mm") == (0.8, 0.6)
    assert find_manual("Kestrel", "10") is None  # no partial-name matches
    assert find_manual("Kestrel", "TS10").name == "kestrel-ts10.md"
    assert rule_materials("Never: metal of any kind.") == ["aluminum", "steel", "brass", "copper"]
    for bad in [("m.md", b""), ("m.pdf", b"%PDF-1.4 broken"), ("m.docx", b"PK\x03\x04\x00\x00")]:
        try:
            read_upload(*bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad[0]} should be rejected")


def test_new_equipment_ids_are_url_safe_and_modes_checked(db):
    from fastapi import HTTPException

    from msa.agents.intake.plugin import IntakeIn, ensure_equipment

    eq = ensure_equipment(db, IntakeIn(name="Laser Cutter #2", id=""), "staff")
    assert eq.id == "laser-cutter-2"
    eq = ensure_equipment(db, IntakeIn(name="Saw", id="Saw/02"), "staff")
    assert eq.id == "saw-02"
    for body in (IntakeIn(name="X", approval_mode="sometimes"), IntakeIn(name="   ")):
        try:
            ensure_equipment(db, body, "staff")
        except HTTPException as e:
            assert e.status_code == 400
        else:
            raise AssertionError("expected a 400")
