from msa.core import training
from msa.core.db import utcnow
from msa.core.models import FloorPlan, PlacedItem
from msa.core.testing import make_approved_doc, make_equipment, make_material_rule, make_user
from msa.safety_tools import tools
from msa.safety_tools.rules import canonical_material, find_categories, find_materials

SOP = "[p.1]\n## PPE\nNever leave the laser unattended.\n"


def laser(db, **kw):
    eq = make_equipment(db, "laser-01", "Laser Cutter", category="laser_cutter", **kw)
    make_approved_doc(db, eq.id, "sop", SOP, statements=[
        {"section": "PPE", "kind": "ppe", "text": "Never leave the laser unattended.",
         "citations": [{"document_id": "m1", "version": 1, "locator": "p.1 PPE", "quote": ""}]}])
    return eq


def test_material_names_normalize():
    assert canonical_material("Plexiglass") == "acrylic"
    assert canonical_material("3 mm Baltic Birch") == "plywood"
    assert canonical_material("unobtainium") is None
    assert find_materials("cut acrylic and some MDF, then glue PVC") == ["acrylic", "mdf", "pvc"]
    assert find_categories("laser-cut a box then 3d print a hinge") == ["laser_cutter", "fdm_printer"]


def test_check_material_allowed_banned_unknown(db):
    eq = laser(db)
    make_material_rule(db, eq.id, "acrylic", "allowed")
    make_material_rule(db, eq.id, "pvc", "allowed")  # a wrong machine record...
    assert tools.check_material(db, eq.id, "cast acrylic").verdict == "allowed"
    v = tools.check_material(db, eq.id, "PVC")
    assert v.verdict == "banned" and v.rule_ids == ["MAT-LASER-PVC"]  # ...global ban still wins
    assert tools.check_material(db, eq.id, "mdf").verdict == "unknown"  # not listed
    v = tools.check_material(db, eq.id, "moon rock")
    assert v.verdict == "unknown" and v.data["codes"] == ["E01"]


def test_can_use_requires_training_waiver_prereq_and_status(db):
    eq = laser(db, prerequisites=["intro-01"])
    make_equipment(db, "intro-01", "Shop intro")
    u = make_user(db)
    v = tools.can_use(db, u.id, eq.id)
    assert v.verdict == "no" and "E05" in v.data["codes"]
    for e in ("intro-01", eq.id):
        training.set_state(db, u.id, e, "quiz_passed", "t")
        training.set_state(db, u.id, e, "certified", "staff")
    assert tools.can_use(db, u.id, eq.id).verdict == "yes"
    teen = make_user(db, birth_year=utcnow().year - 16)
    for e in ("intro-01", eq.id):
        training.set_state(db, teen.id, e, "quiz_passed", "t")
        training.set_state(db, teen.id, e, "certified", "staff")
    assert "E13" in tools.can_use(db, teen.id, eq.id).data["codes"]
    eq.status = "out_of_service"
    assert "E06" in tools.can_use(db, u.id, eq.id).data["codes"]


def test_check_sop_rules(db):
    good = [{"section": s, "kind": "info", "text": "x"} for s in
            ["PPE", "Before you start", "Operation", "Material rules", "Emergency"]]
    good.append({"section": "PPE", "kind": "ppe", "text": "Never leave the laser unattended."})
    assert tools.check_sop_rules("laser_cutter", good).verdict == "pass"
    bad = good[:-1] + [{"section": "Material rules", "kind": "material",
                        "text": "PVC sheet is allowed."}]
    v = tools.check_sop_rules("laser_cutter", bad)
    assert v.verdict == "fail"
    assert "MAT-LASER-PVC" in v.rule_ids and "SOP-PPE-NONE" in v.rule_ids
    never = good + [{"section": "Material rules", "kind": "material", "text": "PVC is never allowed."}]
    assert tools.check_sop_rules("laser_cutter", never).verdict == "pass"


def test_risk_score():
    assert tools.risk_score(2, 2).verdict == "low"
    assert tools.risk_score(4, 5).verdict == "critical"
    assert tools.risk_score(0, 3).verdict == "invalid"


def _plan(db):
    plan = FloorPlan(name="t", width_m=10, height_m=8, exits=[{"x": 5, "y": 8}])
    db.add(plan)
    db.flush()
    return plan


def _place(db, plan, **kw):
    it = PlacedItem(plan_id=plan.id, **kw)
    db.add(it)
    db.flush()
    return it


def test_layout_good_and_bad(db):
    eq = laser(db, clearance={"front": 1.0, "back": 0.3, "left": 0.5, "right": 0.5},
               footprint_w=1.2, footprint_d=0.9)
    plan = _plan(db)
    _place(db, plan, kind="equipment", equipment_id=eq.id, label="Laser", x=1, y=1, w=1.2, h=0.9)
    _place(db, plan, kind="safety", safety_type="extinguisher", label="Ext", x=2.6, y=1, w=0.3, h=0.3)
    _place(db, plan, kind="safety", safety_type="first_aid", label="FA", x=9, y=1, w=0.3, h=0.3)
    v = tools.check_layout(db, plan.id)
    assert v.verdict == "pass", v.reasons

    desk = _place(db, plan, kind="object", label="Desk", x=1.2, y=2.1, w=1, h=0.6)  # in front zone
    v = tools.check_layout(db, plan.id)
    assert "LAY-CLEAR" in v.rule_ids and any(desk.id in f["item_ids"] for f in v.data["findings"])


def test_layout_blocked_exit_and_missing_extinguisher(db):
    eq = laser(db, clearance={"front": 0.5})
    plan = _plan(db)
    _place(db, plan, kind="equipment", equipment_id=eq.id, label="Laser", x=4.5, y=1, w=1, h=1)
    _place(db, plan, kind="object", label="Wall of shelves", x=0, y=5, w=10, h=0.5)  # cuts room
    v = tools.check_layout(db, plan.id)
    assert {"LAY-AISLE", "LAY-EXT-LASER", "LAY-FIRSTAID"} <= set(v.rule_ids)
    assert v.verdict == "fail"
