"""R2: floor plan API, layout suggestions, briefing."""

from sqlalchemy import select

from msa.agents.briefing.agent import facts, plain_summary
from msa.agents.layout.agent import suggest_positions
from msa.core.db import session_scope
from msa.core.models import Approval, FloorPlan, PlacedItem
from msa.core.testing import make_equipment, make_user
from msa.safety_tools.tools import check_layout
from tests.conftest import auth_headers


def make_plan(db):
    plan = FloorPlan(name="Test", width_m=10, height_m=8, exits=[{"x": 5, "y": 8}])
    db.add(plan)
    db.flush()
    for st, x in (("extinguisher", 4.0), ("first_aid", 6.0), ("eyewash", 7.0)):
        db.add(PlacedItem(plan_id=plan.id, kind="safety", safety_type=st, label=st, x=x, y=0.05,
                          w=0.3, h=0.3))
    db.flush()
    return plan


def test_suggestions_pass_the_layout_check(db):
    plan = make_plan(db)
    make_equipment(db, "laser-01", category="laser_cutter", footprint_w=1.4, footprint_d=0.9,
                   clearance={"front": 1.0, "back": 0.3, "left": 0.5, "right": 0.5})
    make_equipment(db, "saw-01", category="table_saw", footprint_w=1.0, footprint_d=0.8,
                   clearance={"front": 2.4, "back": 2.4, "left": 0.9, "right": 0.9})
    out = suggest_positions(db, plan)
    assert {p["equipment_id"] for p in out["proposals"]} == {"laser-01", "saw-01"}
    for p in out["proposals"]:
        db.add(PlacedItem(plan_id=plan.id, kind="equipment", equipment_id=p["equipment_id"], x=p["x"],
                          y=p["y"], w=p["w"], h=p["h"], rotation=p["rotation"]))
    db.flush()
    v = check_layout(db, plan.id)
    assert v.verdict == "pass", v.reasons


def test_floorplan_api_save_and_suggest_flow(client):
    with session_scope() as db:
        plan = make_plan(db)
        make_equipment(db, "drill-01", category="drill_press", footprint_w=0.6, footprint_d=0.45,
                       clearance={"front": 0.9})
        staff = make_user(db, role="staff")
        member = make_user(db)
        pid, s_email, m_email = plan.id, staff.email, member.email
    sh, mh = auth_headers(client, s_email), auth_headers(client, m_email)
    r = client.post(f"/floorplans/{pid}/items", headers=mh, json={"kind": "object", "label": "x"})
    assert r.status_code == 403  # members can't edit
    r = client.post(f"/floorplans/{pid}/items", headers=sh,
                    json={"kind": "object", "shape": "circle", "label": "Table", "w": 4, "h": 4,
                          "units": "ft", "x": 10, "y": 10})
    assert r.status_code == 200
    table = next(i for i in r.json()["items"] if i["label"] == "Table")
    assert abs(table["w"] - 4 * 0.3048) < 1e-6
    r = client.post(f"/floorplans/{pid}/save", headers=sh,
                    json={"items": [{"id": table["id"], "x": 1.0, "y": 2.0, "rotation": 90}]})
    assert r.status_code == 200 and r.json()["moved"] == 1
    r = client.post(f"/floorplans/{pid}/suggest", headers=sh)
    assert r.json()["status"] == "needs_approval"
    with session_scope() as db:
        a = db.scalar(select(Approval).where(Approval.kind == "layout_change"))
        aid = a.id
    r = client.post(f"/approvals/{aid}/decide", headers=sh, json={"approve": True})
    assert r.status_code == 200
    plan = client.get(f"/floorplans/{pid}", headers=mh).json()
    assert any(i.get("equipment_id") == "drill-01" for i in plan["items"])
    assert "drill-01" not in plan["unplaced"]


def test_briefing_puts_problems_first(db):
    eq = make_equipment(db, "laser-01", category="laser_cutter")
    eq.status = "out_of_service"
    eq.status_reason = "Incident reported"
    f = facts(db)
    s = plain_summary(f)
    assert "Laser" in s or "laser-01" in s
    assert f["machines_not_available"][0]["id"] == "laser-01"
