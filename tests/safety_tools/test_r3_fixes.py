"""Regressions for the R3 fixes made on 2026-11-16.

Each test covers a case that was wrong before: Ask answered "Yes" where the manual caps the
thickness, a cross-step rule fired on machines the plan never combined, an ordinary English
"print a" was read as 3D printing, and a 45 degree rotation was checked as if it were 0.
"""

import pytest

from msa.core import orchestrator, training
from msa.core.models import FloorPlan, PlacedItem
from msa.core.testing import make_approved_doc, make_equipment, make_material_rule, make_user
from msa.safety_tools import tools
from msa.safety_tools.rules import find_categories
from msa.safety_tools.thickness import limit_mm, over_thickness

# ------------------------------------------------------------ thickness, shared

PLY = "Allowed: plywood and MDF up to 6 mm."


@pytest.mark.parametrize("text,over", [
    # fractions are how sheet stock is usually described, and 1/4 inch is over a 6 mm limit
    ("Laser cut 1/4 inch plywood", True),
    ("Laser cut 1/8 inch plywood", False),
    ("Cut 12 mm plywood", True),
    ("Cut 0.5 in plywood", True),
    ("Cut 6 mm plywood", False),
    ("a 300 mm wide plywood panel, 3 mm thick", False),
    ("a 600 mm plywood board", False),
])
def test_over_thickness_understands_inch_fractions(text, over):
    assert bool(over_thickness(text, "plywood", PLY)) is over


def test_over_thickness_reports_the_sizes_as_written():
    assert over_thickness("Laser cut 1/4 inch plywood", "plywood", PLY) == ("1/4 inch", "6 mm")


def test_limit_mm_reads_the_limit_or_none():
    assert limit_mm(PLY) == (6.0, "6 mm")
    assert limit_mm("Allowed: acrylic") is None


def laser_shop(db):
    laser = make_equipment(db, "laser-01", "Laser Cutter", category="laser_cutter",
                           clearance={"front": 1.0, "back": 0.3, "left": 0.6, "right": 0.6})
    make_approved_doc(db, laser.id, "sop", "x", statements=[
        {"section": "PPE", "kind": "ppe", "text": "Never leave the laser unattended.",
         "citations": []}])
    make_material_rule(db, laser.id, "plywood", "allowed", PLY)
    make_material_rule(db, laser.id, "acrylic", "allowed", "Allowed: acrylic up to 10 mm.")
    u = make_user(db)
    training.set_state(db, u.id, laser.id, "quiz_passed", "t")
    training.set_state(db, u.id, laser.id, "certified", "staff")
    return laser, u


# ------------------------------------------------- ask and project check agree

def ask(db, u, text):
    return orchestrator.run(db, "ask", {"user_id": u.id, "question": text}, u.id)[0]


def check(db, u, text):
    return orchestrator.run(db, "project_check", {"user_id": u.id, "description": text}, u.id)[0]


def test_ask_does_not_say_yes_to_an_over_limit_thickness(db):
    """The manual allows plywood "up to 6 mm". Ask used to answer "Yes" to 12 mm and 1/4 inch
    while Project Check escalated the same question, because the parser lived in the agent."""
    _, u = laser_shop(db)
    r = ask(db, u, "Can I laser cut 12 mm plywood?")
    assert "E01" in r.escalations
    assert r.output["answer"].startswith("No.")
    assert "6 mm" in r.output["answer"]


def test_ask_agrees_with_project_check_on_the_same_thickness(db):
    _, u = laser_shop(db)
    question = "Can I laser cut 12 mm plywood on the laser cutter"
    assert "E01" in ask(db, u, question).escalations
    assert check(db, u, "Laser cut 12 mm plywood.").output["verdict"] == "needs_staff"


@pytest.mark.parametrize("text", ["Can I laser cut 3 mm plywood?", "Can I cut acrylic on the laser?"])
def test_ask_still_says_yes_within_the_limit(db, text):
    _, u = laser_shop(db)
    r = ask(db, u, text)
    assert r.output["answer"].startswith("Yes,") and not r.escalations


# ------------------------------------------------------- cross-step conditions

def two_machine_shop(db):
    laser, u = laser_shop(db)
    saw = make_equipment(db, "saw-01", "Table Saw", category="table_saw")
    make_approved_doc(db, saw.id, "sop", "x", statements=[
        {"section": "PPE", "kind": "ppe", "text": "Wear safety glasses.", "citations": []}])
    make_material_rule(db, saw.id, "softwood", "allowed")
    for e in (laser, saw):
        training.set_state(db, u.id, e.id, "quiz_passed", "t")
        training.set_state(db, u.id, e.id, "certified", "staff")
    return u


def test_acrylic_saw_rule_needs_acrylic_in_the_plan(db):
    """The rule is about cutting the same acrylic on both machines. A plan that uses both
    machines on other materials must not trigger it."""
    u = two_machine_shop(db)
    r = check(db, u, "Rip a pine board on the table saw, then laser cut a cardboard tag.")
    assert not any("X-LASER-ACRYLIC-SAW" in f["rule_ids"] for f in r.output["findings"])


def test_acrylic_saw_rule_fires_when_acrylic_is_present(db):
    u = two_machine_shop(db)
    r = check(db, u, "Laser cut acrylic parts, then rip pine on the table saw.")
    assert any("X-LASER-ACRYLIC-SAW" in f["rule_ids"] for f in r.output["findings"])


# ----------------------------------------------------------- machine keywords

@pytest.mark.parametrize("text", ["Print a copy of the SOP", "print a photo",
                                  "Print the instructions"])
def test_print_is_not_always_3d_printing(text):
    assert find_categories(text) == []


@pytest.mark.parametrize("text,cats", [
    ("3D print a phone stand", ["fdm_printer"]),
    ("print a phone stand in PLA", ["fdm_printer"]),  # the filament names the machine
    ("Print a vinyl banner with the vinyl cutter", ["vinyl_cutter"]),
    ("print a resin figurine", ["resin_printer"]),
    ("cut a vinyl decal", ["vinyl_cutter"]),
])
def test_machine_keywords_still_match_real_jobs(text, cats):
    assert find_categories(text) == cats


def test_printing_a_document_does_not_apply_printer_ppe(db):
    _, u = laser_shop(db)
    make_equipment(db, "fdm-01", "3D Printer", category="fdm_printer")
    r = check(db, u, "Print a copy of the SOP, then laser cut a cardboard tag.")
    assert not any("PPE-FDM" in f["rule_ids"] for f in r.output["findings"])


# -------------------------------------------------------- layout rotation

def test_layout_flags_rotation_it_cannot_model(db):
    eq, _ = laser_shop(db)
    plan = FloorPlan(name="t", width_m=20, height_m=20, exits=[{"x": 10, "y": 19}])
    db.add(plan)
    db.flush()
    it = PlacedItem(plan_id=plan.id, kind="equipment", equipment_id=eq.id, label="Laser",
                    x=5, y=5, w=1.0, h=0.8, rotation=45)
    db.add(it)
    db.flush()
    v = tools.check_layout(db, plan.id)
    assert "LAY-ROTATION" in v.rule_ids
    assert any(it.id in f["item_ids"] for f in v.data["findings"] if f["rule_id"] == "LAY-ROTATION")


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_right_angle_rotations_are_not_flagged(db, rot):
    eq, _ = laser_shop(db)
    plan = FloorPlan(name="t", width_m=20, height_m=20, exits=[{"x": 10, "y": 19}])
    db.add(plan)
    db.flush()
    db.add(PlacedItem(plan_id=plan.id, kind="equipment", equipment_id=eq.id, label="Laser",
                      x=5, y=5, w=1.0, h=0.8, rotation=rot))
    db.flush()
    assert "LAY-ROTATION" not in tools.check_layout(db, plan.id).rule_ids
