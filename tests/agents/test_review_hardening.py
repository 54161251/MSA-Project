"""Adversarial drafts for the Review agent: each must be flagged (or, for the clean ones, not)."""

import pytest

from msa.agents.review.agent import review_document
from tests.agents.test_review import cite, setup

P1, P2 = "p.1 Personal protective equipment", "p.2 Materials"

FLAGGED = [
    ("ppe", "Wear safety glasses for 60 minutes.", P1),                      # invented number
    ("ppe", "Never wear safety glasses.", P1),                               # flipped meaning
    ("ppe", "Safety glasses are not necessary.", P1),                        # flipped meaning
    ("ppe", "Skip the safety glasses if you wear contacts.", P1),            # risky qualifier
    ("material", "Allowed: plywood up to 6 cm.", P2),                        # unit changed
    ("material", "Allowed: plywood up to 60 mm.", P2),                       # number changed
    ("material", "Allowed: plywood up to 6 mm and 12 mm.", P2),              # extra number
    ("material", "Allowed: PVC.", P2),                                       # banned material
    ("material", "Allowed: pvc", P2),                                        # lowercase
    ("material", "Allowed: vinyl up to 3 mm.", P2),                          # alias of a banned one
    ("material", "Allowed: polyvinyl chloride.", P2),                        # full name of PVC
    ("material", "Approved: plywood and PVC up to 6 mm.", P2),               # mixed list
    ("material", "Never: plywood up to 6 mm.", P2),                          # flips the manual
]
CLEAN = [
    ("ppe", "Wear safety glasses.", P1),
    ("material", "Allowed: plywood up to 6 mm.", P2),
    ("material", "Never: PVC. It releases chlorine gas.", P2),
]


@pytest.mark.parametrize("kind,text,loc", FLAGGED)
def test_tampered_statement_is_flagged(db, kind, text, loc):
    _, _, d = setup(db, [{"section": "S", "kind": kind, "text": text, "citations": [cite(loc)]}])
    assert review_document(db, d)["issues"], text


@pytest.mark.parametrize("kind,text,loc", CLEAN)
def test_faithful_statement_is_not_flagged(db, kind, text, loc):
    _, _, d = setup(db, [{"section": "S", "kind": kind, "text": text, "citations": [cite(loc)]}])
    assert review_document(db, d)["issues"] == [], text


@pytest.mark.parametrize("bad", [None, [], "p.1", ["p.1"], [None], [{}], [{"locator": None}], 5])
def test_malformed_citations_are_flagged_not_crashes(db, bad):
    _, _, d = setup(db, [])
    d.statements = [{"section": "PPE", "kind": "ppe", "text": "Wear safety glasses.",
                     "citations": bad}]
    issues = review_document(db, d)["issues"]
    assert [i["code"] for i in issues] == ["E07"]


@pytest.mark.parametrize("text", [None, "", "   ", 5, ["Wear safety glasses."]])
def test_malformed_statement_text_is_flagged_not_crashes(db, text):
    _, manual, d = setup(db, [])
    d.statements = [{"section": "PPE", "kind": "ppe", "text": text,
                     "citations": [{**cite(P1), "document_id": manual.id, "version": 1}]}]
    assert [i["code"] for i in review_document(db, d)["issues"]] == ["E07"]


def test_statement_that_is_not_a_dict_does_not_crash(db):
    _, _, d = setup(db, [])
    d.statements = ["just a string", None, 5]
    assert len(review_document(db, d)["issues"]) == 3
