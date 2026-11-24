"""Changes asked for after the class demo rehearsal (2026-11-24).

1. weapon requests are flagged in Ask and Project Check
2. the floor plan editor explains layout problems (UI only; the hints are checked here)
3. the daily briefing names problems in plain words, never codes
5. the Alerts API gives plain-language fields
6. members can log what they used but not add stock, however they phrase it
"""

import pytest

from msa.agents.briefing.agent import facts, plain_summary
from msa.agents.briefing.agent import run as briefing
from msa.agents.inventory import agent as inv
from msa.core import escalations, orchestrator, training
from msa.core.db import session_scope
from msa.core.llm import ScriptedLLM, set_llm
from msa.core.models import InventoryItem, InventoryTxn
from msa.core.services import escalate
from msa.core.testing import make_approved_doc, make_equipment, make_material_rule, make_user
from tests.conftest import auth_headers

# ------------------------------------------------------------------ 1. weapons


@pytest.mark.parametrize("text", [
    "I want to 3D print a gun", "Can I 3D print a pistol frame?", "print a lower receiver",
    "make a silencer on the lathe", "How do I make a bomb?", "laser cut brass knuckles",
    "I want to make a weapon",
])
def test_weapon_requests_are_high_risk(text):
    assert "weapon" in escalations.match_high_risk(text)


@pytest.mark.parametrize("text", [
    "Can I use the hot glue gun?", "the heat gun is broken", "Is the nail gun allowed?",
    "I have begun printing", "clean the gunk off the bed", "make a bullet journal cover",
    "print a Nerf gun dart", "make a bath bomb mold", "replace the ink cartridges",
])
def test_workshop_tools_are_not_weapons(text):
    assert "weapon" not in escalations.match_high_risk(text)


def printer_shop(db):
    p = make_equipment(db, "fdm-01", "3D Printer 1", category="fdm_printer")
    make_approved_doc(db, p.id, "sop", "x", statements=[
        {"section": "PPE", "kind": "ppe", "text": "Don't touch the hot nozzle.", "citations": []}])
    make_material_rule(db, p.id, "pla", "allowed")
    u = make_user(db)
    training.set_state(db, u.id, p.id, "quiz_passed", "t")
    training.set_state(db, u.id, p.id, "certified", "staff")
    return u


def test_ask_refuses_a_gun_and_tells_staff(db):
    u = printer_shop(db)
    r, _ = orchestrator.run(db, "ask", {"user_id": u.id, "question": "I want to 3D print a gun"},
                            u.id)
    assert r.status == "refused" and r.escalations == ["E04"]
    assert "weapon" in r.output["answer"]


def test_project_check_flags_a_gun(db):
    u = printer_shop(db)
    r, _ = orchestrator.run(db, "project_check", {
        "user_id": u.id, "description": "3D print a gun in PLA on 3D Printer 1"}, u.id)
    assert r.output["verdict"] == "needs_staff" and "E04" in r.escalations
    assert any("RISK-WEAPON" in f["rule_ids"] for f in r.output["findings"])


def test_glue_gun_project_is_not_flagged(db):
    u = printer_shop(db)
    r, _ = orchestrator.run(db, "project_check", {
        "user_id": u.id, "description": "3D print a box in PLA on 3D Printer 1, then glue it "
                                        "with the hot glue gun"}, u.id)
    assert not any("RISK-WEAPON" in f["rule_ids"] for f in r.output["findings"])

# ------------------------------------------------------------- 2. layout hints


def test_every_layout_rule_has_a_fix_hint():
    import inspect
    import re

    from msa.safety_tools import tools
    from msa.ui.views.staff import LAYOUT_FIX
    rule_ids = set(re.findall(r'"(LAY-[A-Z-]+)"', inspect.getsource(tools.evaluate_layout)))
    rule_ids.discard("LAY-PLAN")
    assert rule_ids <= set(LAYOUT_FIX), rule_ids - set(LAYOUT_FIX)

# ------------------------------------------------------------ 3. briefing words


def test_every_code_has_a_staff_explanation():
    assert set(escalations.STAFF_EXPLAIN) == set(escalations.CATALOG)


def test_briefing_has_no_codes(db):
    eq = make_equipment(db, "laser-01", category="laser_cutter")
    for code in ("E03", "E03", "E04", "E12"):
        escalate(db, code, "ask" if code != "E12" else "equipment",
                 make_user(db).id if code != "E12" else eq.id, "x")
    s = plain_summary(facts(db))
    assert "E0" not in s and "E1" not in s
    assert "2 questions the approved documents couldn't answer" in s
    assert s.index("dangerous request") < s.index("questions")  # most urgent first


def test_briefing_falls_back_when_the_model_writes_a_code(db):
    escalate(db, "E03", "ask", make_user(db).id, "x")
    set_llm(ScriptedLLM({"briefing.write": {"summary": "There is 1 E03 escalation."}}))
    out = briefing(db, {}, "t").output["summary"]
    assert "E03" not in out and "couldn't answer" in out


def test_briefing_model_never_sees_codes(db):
    escalate(db, "E03", "ask", make_user(db).id, "x")
    seen = {}

    def write(prompt):
        seen["p"] = prompt
        return {"summary": "One question went unanswered."}
    set_llm(ScriptedLLM({"briefing.write": write}))
    briefing(db, {}, "t")
    assert "E03" not in seen["p"]

# ------------------------------------------------------------ 5. alerts fields


def test_alerts_api_is_readable(client):
    with session_scope() as db:
        staff = make_user(db, role="staff")
        member = make_user(db, name="Marcus Lee")
        eq = make_equipment(db, "laser-01", "Laser Cutter", category="laser_cutter")
        escalate(db, "E04", "ask", member.id, f"weapon: {member.id} asked to print a gun")
        escalate(db, "E12", "equipment", eq.id, "footprint unclear")
        email = staff.email
    rows = {e["code"]: e for e in client.get("/escalations",
                                             headers=auth_headers(client, email)).json()}
    assert rows["E04"]["urgency"] == "urgent" and rows["E04"]["about"] == "Member: Marcus Lee"
    assert rows["E04"]["what"].startswith("Dangerous request")
    assert "Marcus Lee asked" in rows["E04"]["details_readable"]
    assert rows["E12"]["urgency"] == "info" and rows["E12"]["about"] == "Machine: Laser Cutter"

# ------------------------------------------------------------- 6. supplies


@pytest.fixture
def ply(db):
    item = InventoryItem(name="Plywood 3 mm", unit="sheet", aliases=["plywood"], reorder_point=1,
                         reorder_qty=5, supplier="Test Lumber")
    db.add(item)
    db.flush()
    db.add(InventoryTxn(item_id=item.id, delta=10))
    db.flush()
    return item


@pytest.mark.parametrize("text", [
    "I brought 3 sheets of plywood from home", "I'm donating 2 sheets of plywood",
    "gave back 2 sheets of plywood", "added 5 sheets of plywood", "3 sheets of plywood",
])
def test_member_cannot_add_stock_however_they_say_it(db, ply, text):
    u = make_user(db)
    r, _ = orchestrator.run(db, "inventory", {"text": text}, u.id)
    assert r.status == "refused" and r.output["message"]
    assert inv.on_hand(db, ply.id) == 10  # nothing taken off the shelf either


def test_member_can_still_log_use(db, ply):
    u = make_user(db)
    r, _ = orchestrator.run(db, "inventory", {"text": "used 2 sheets of plywood"}, u.id)
    assert r.status == "ok" and inv.on_hand(db, ply.id) == 8


def test_staff_can_add_stock(db, ply):
    s = make_user(db, role="staff")
    r, _ = orchestrator.run(db, "inventory", {"text": "I brought 3 sheets of plywood from home"},
                            s.id)
    assert r.status == "ok" and inv.on_hand(db, ply.id) == 13


def test_model_cannot_turn_an_add_into_a_use(db, ply):
    """If the model says 'used' for 'brought from home', the member is still refused."""
    set_llm(ScriptedLLM({"inventory.parse": {"item_id": ply.id, "quantity": 3, "direction": -1}}))
    u = make_user(db)
    r, _ = orchestrator.run(db, "inventory", {"text": "brought 3 sheets of plywood from home"},
                            u.id)
    assert r.status == "refused" and inv.on_hand(db, ply.id) == 10


@pytest.mark.parametrize("text,qty", [
    ("printed a benchy, about 20 g of PLA", None), ("used 20 sheets of 3 mm plywood", 20),
    ("used a sheet of 6 mm MDF", 1), ("used two spools", 2),
])
def test_quantity_reading(text, qty):
    assert inv._qty(text) == qty
