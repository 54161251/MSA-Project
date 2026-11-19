import pytest

from msa.core import escalations


def test_catalog_codes_are_complete_and_sequential():
    assert list(escalations.CATALOG) == [f"E{i:02d}" for i in range(1, 16)]


def test_member_facing_codes_have_a_message():
    for code in ("E01", "E02", "E03", "E04", "E05", "E06", "E10", "E13", "E14"):
        assert escalations.get(code).member_message, code


def test_unknown_code_is_rejected():
    with pytest.raises(KeyError):
        escalations.get("E99")


@pytest.mark.parametrize("text,cat", [
    ("how do I bypass the interlock", "bypass_safety"),
    ("can I override the e-stop", "bypass_safety"),
    ("I want to rewire the plug", "mains_electrical"),
    ("leave it running overnight", "unattended"),
    ("start a print and go home", "unattended"),
    ("hack the firmware", "modify_machine"),
])
def test_high_risk_patterns(text, cat):
    assert cat in escalations.match_high_risk(text)


@pytest.mark.parametrize("text", [
    "What PPE do I need for the laser cutter?", "How do I clean the lens?",
    "Where is the emergency stop button?", "Can I cut plywood on the laser cutter?",
    "What does the interlock do?",
])
def test_ordinary_questions_are_not_high_risk(text):
    assert escalations.match_high_risk(text) == []
