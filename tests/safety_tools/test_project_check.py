from sqlalchemy import select

from msa.core import orchestrator, training
from msa.core.llm import ScriptedLLM, set_llm
from msa.core.models import Approval
from msa.core.testing import make_approved_doc, make_equipment, make_material_rule, make_user


def shop(db):
    sop = [{"section": "PPE", "kind": "ppe", "text": "Never leave the laser unattended.",
            "citations": []}]
    laser = make_equipment(db, "laser-01", "Laser Cutter", category="laser_cutter")
    make_approved_doc(db, laser.id, "sop", "x", statements=sop)
    make_material_rule(db, laser.id, "acrylic", "allowed")
    make_material_rule(db, laser.id, "plywood", "allowed")
    resin = make_equipment(db, "resin-01", "Resin Printer", category="resin_printer")
    make_approved_doc(db, resin.id, "sop", "x", statements=[])
    make_material_rule(db, resin.id, "standard_resin", "allowed")
    return laser, resin


def certify(db, u, *eqs):
    for e in eqs:
        training.set_state(db, u.id, e.id, "quiz_passed", "t")
        training.set_state(db, u.id, e.id, "certified", "staff")


def run(db, u, text):
    final, _ = orchestrator.run(db, "project_check", {"user_id": u.id, "description": text}, u.id)
    return final


def test_safe_project_is_clear_with_conditions(db):
    laser, _ = shop(db)
    u = make_user(db)
    certify(db, u, laser)
    r = run(db, u, "Laser-cut a small box from 3 mm plywood.")
    assert r.output["verdict"] == "clear_with_conditions", r.output["findings"]
    assert r.status == "ok"


def test_banned_material_needs_staff_and_opens_approval(db):
    laser, _ = shop(db)
    u = make_user(db)
    certify(db, u, laser)
    r = run(db, u, "Laser cut a PVC sign for my door.")
    assert r.output["verdict"] == "needs_staff" and "E01" in r.escalations
    assert db.scalar(select(Approval).where(Approval.kind == "project_check"))


def test_untrained_member_gets_e05(db):
    shop(db)
    u = make_user(db)
    r = run(db, u, "Laser cut an acrylic keychain.")
    assert "E05" in r.escalations and r.output["verdict"] == "needs_staff"


def test_cross_step_resin_sanding(db):
    laser, resin = shop(db)
    u = make_user(db)
    certify(db, u, laser, resin)
    r = run(db, u, "Resin print a figurine, then sand it smooth.")
    assert any("X-RESIN-SAND" in f["rule_ids"] for f in r.output["findings"])


def test_high_risk_text_escalates_e04(db):
    laser, _ = shop(db)
    u = make_user(db)
    certify(db, u, laser)
    r = run(db, u, "Laser cut plywood and bypass the lid interlock so it runs faster.")
    assert "E04" in r.escalations


def test_llm_steps_are_rechecked_deterministically(db):
    laser, _ = shop(db)
    u = make_user(db)
    certify(db, u, laser)
    # the LLM "forgets" the material; the deterministic re-scan still catches PVC
    set_llm(ScriptedLLM({"project_check.plan": {"steps": [
        {"text": "Laser cut the PVC panel", "category": "laser_cutter", "materials": []}]}}))
    r = run(db, u, "Laser cut the PVC panel for the robot.")
    assert r.output["verdict"] == "needs_staff" and "E01" in r.escalations


def test_components_are_not_unknown_materials(db):
    """Regression (real Claude reply, 2026-11-07): it listed "circuit board" and "header pins"
    as materials for a soldering step."""
    from msa.core.testing import make_equipment as mk

    solder = mk(db, "solder-01", "Soldering Station", category="soldering")
    make_approved_doc(db, solder.id, "sop", "x", statements=[])
    make_material_rule(db, solder.id, "lead_free_solder", "allowed")
    u = make_user(db)
    certify(db, u, solder)
    set_llm(ScriptedLLM({"project_check.plan": {"steps": [
        {"text": "Solder header pins onto a circuit board with lead-free solder.",
         "category": "soldering", "equipment_id": None,
         "materials": ["circuit board", "header pins", "lead-free solder"], "processes": []}]}}))
    r = run(db, u, "Solder header pins onto a circuit board with lead-free solder.")
    assert r.output["verdict"] == "clear_with_conditions", r.output["findings"]
    assert "E01" not in r.escalations
