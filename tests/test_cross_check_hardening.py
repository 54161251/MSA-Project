"""Regressions from the cross-team check on 2026-11-15 (each case used to get a wrong result)."""

import pytest

from msa.agents.ask.agent import is_emergency
from msa.agents.project_check.agent import over_thickness
from msa.agents.triage.agent import deterministic_classify
from msa.core import escalations, orchestrator, training
from msa.core.db import session_scope
from msa.core.models import FloorPlan, InventoryItem
from msa.core.testing import make_approved_doc, make_equipment, make_material_rule, make_user
from msa.safety_tools.rules import find_materials
from tests.conftest import auth_headers

# ------------------------------------------------------------------ project check


def laser_shop(db):
    laser = make_equipment(db, "laser-01", "Laser Cutter", category="laser_cutter")
    make_approved_doc(db, laser.id, "sop", "x", statements=[
        {"section": "PPE", "kind": "ppe", "text": "Never leave the laser unattended.", "citations": []}])
    make_material_rule(db, laser.id, "plywood", "allowed", "Allowed: plywood and MDF up to 6 mm.")
    make_material_rule(db, laser.id, "leather_veg_tan", "allowed")
    u = make_user(db)
    training.set_state(db, u.id, laser.id, "quiz_passed", "t")
    training.set_state(db, u.id, laser.id, "certified", "staff")
    return u


def check(db, u, text):
    return orchestrator.run(db, "project_check", {"user_id": u.id, "description": text}, u.id)[0]


def test_material_thicker_than_the_manual_allows_needs_staff(db):
    u = laser_shop(db)
    r = check(db, u, "Cut 12 mm plywood on the laser cutter.")
    assert r.output["verdict"] == "needs_staff" and "E01" in r.escalations
    assert any("MAT-THICKNESS" in f["rule_ids"] for f in r.output["findings"])
    assert check(db, u, "Cut 3 mm plywood on the laser cutter.").output["verdict"] == \
        "clear_with_conditions"


@pytest.mark.parametrize("text,over", [
    ("Cut 12 mm plywood", True), ("plywood, 1 cm thick", True), ("half sheet of 0.5 in plywood", True),
    ("Cut 6 mm plywood", False), ("a 300 mm wide plywood panel, 3 mm thick", False),
    ("a 600 mm plywood board", False), ("plywood box 120 mm by 80 mm", False),
])
def test_over_thickness_only_reads_thicknesses(text, over):
    assert bool(over_thickness(text, "plywood", "Allowed: plywood and MDF up to 6 mm.")) is over


def test_plan_with_nothing_recognized_is_not_called_fine(db):
    u = laser_shop(db)
    r = check(db, u, "Cut some stuff on the big machine in the corner")
    assert r.status == "refused" and "verdict" not in r.output
    assert "couldn't tell" in r.output["message"]


@pytest.mark.parametrize("text", [
    "I'll tape down the lid switch so I can engrave a tall object on the laser cutter",
    "Rip a board on the table saw without the riving knife because it gets in the way",
    "Take the guard off the bandsaw to cut a thick block",
])
def test_bypass_phrasings_are_high_risk(text):
    assert "bypass_safety" in escalations.match_high_risk(text)


def test_chrome_tanned_leather_is_not_read_as_plain_leather(db):
    assert "leather_chrome_tan" in find_materials("I want to cut leather (chrome tanned) on the laser")
    u = laser_shop(db)
    r = check(db, u, "I want to cut leather (chrome tanned) on the laser cutter")
    assert r.output["verdict"] == "needs_staff" and "E01" in r.escalations

# ------------------------------------------------------------------------- triage


@pytest.mark.parametrize("text,kind", [
    ("There was a small flame inside the laser but it went out", "incident"),
    ("sparks came out of the drill press motor", "near_miss"),
    ("I smelled burning plastic near the 3D printer", "near_miss"),
    ("The table saw blade guard is missing", "feedback"),
    ("Great class yesterday, thanks!", "feedback"),
])
def test_triage_kinds(text, kind):
    assert deterministic_classify(text)["kind"] == kind


@pytest.mark.parametrize("text,issue", [
    ("sparks came out of the drill press motor", "other"),  # "out of" is not "out of stock"
    ("We are out of plywood", "out_of_stock"),
    ("The saw is making a grinding noise", "not_working"),
    ("the e-stop on the cnc doesnt work", "not_working"),
    ("The resin printer leaked resin all over the bench", "not_working"),
])
def test_triage_issue_types(text, issue):
    assert deterministic_classify(text)["issue_type"] == issue

# ---------------------------------------------------------------- ask: emergencies


@pytest.mark.parametrize("text,urgent", [
    ("my arm got caught in the machine", True), ("her hair is stuck in the drill press", True),
    ("I crushed my finger", True), ("there is blood everywhere", True),
    ("The filament got caught in the extruder", False), ("Can I cut bloodwood on the laser?", False),
    ("Can I engrave crushed velvet?", False), ("Is a little smoke from the laser normal?", False),
    ("How do I bleed the air line?", False),
])
def test_emergency_detection_has_few_false_alarms(text, urgent):
    assert is_emergency(text) is urgent

# ---------------------------------------------------------------------------- API


def staff_headers(client):
    with session_scope() as db:
        email = make_user(db, role="staff").email
    return auth_headers(client, email)


def test_duplicate_inventory_name_is_a_400_not_a_crash(client):
    h = staff_headers(client)
    assert client.post("/inventory/items", json={"name": "Blue tape"}, headers=h).status_code == 200
    r = client.post("/inventory/items", json={"name": "Blue tape"}, headers=h)
    assert r.status_code == 400 and "already exists" in r.json()["detail"]
    with session_scope() as db:
        other = InventoryItem(name="Red tape", created_by="t")
        db.add(other)
        db.flush()
        oid = other.id
    r = client.patch(f"/inventory/items/{oid}", json={"name": "Blue tape"}, headers=h)
    assert r.status_code == 400


def test_floorplan_object_with_a_bad_machine_id_is_not_a_crash(client):
    h = staff_headers(client)
    with session_scope() as db:
        plan = FloorPlan(name="T", width_m=10, height_m=8, exits=[{"x": 5, "y": 8}])
        db.add(plan)
        db.flush()
        pid = plan.id
    r = client.post(f"/floorplans/{pid}/items", json={"equipment_id": "nope"}, headers=h)
    assert r.status_code == 200
    r = client.post(f"/floorplans/{pid}/items", json={"kind": "spaceship"}, headers=h)
    assert r.status_code == 400
