from msa.agents.review.agent import meaning_changed

SRC = "Set the blade height about 6 mm above the stock. Never hold small parts by hand."


def test_changed_number_is_caught():
    assert "50" in meaning_changed("Set the blade height about 50 mm above the stock.", SRC)


def test_flipped_negation_is_caught():
    assert meaning_changed("Hold small parts by hand.", SRC)


def test_added_qualifier_is_caught():
    assert "optional" in meaning_changed("Blade height is optional.", "Set the blade height.")


def test_faithful_paraphrase_passes():
    assert meaning_changed("Set the blade about 6 mm above the stock.", SRC) is None
