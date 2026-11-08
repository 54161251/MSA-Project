import pytest

from msa.core.auth import check_password, create_user, login
from msa.core.db import utcnow
from msa.core.services import ServiceError
from msa.core.testing import make_user

THIS_YEAR = utcnow().year


def test_passwords_are_hashed(db):
    u = make_user(db, password="correct horse")
    assert u.password_hash and "correct horse" not in u.password_hash
    assert check_password("correct horse", u.password_hash)
    assert not check_password("wrong", u.password_hash)


def test_login_returns_token_and_rejects_bad_password(db):
    u = make_user(db, password="password123")
    token, who = login(db, u.email, "password123")
    assert token and who.id == u.id
    with pytest.raises(ServiceError):
        login(db, u.email, "nope-nope")


def test_under_15_needs_adult_and_cannot_sign_in(db):
    with pytest.raises(ServiceError):
        create_user(db, "Kid", "kid@example.org", THIS_YEAR - 12, None, agreed_to_terms=True)
    adult = make_user(db, birth_year=1980)
    kid = create_user(db, "Kid", "kid@example.org", THIS_YEAR - 12, None, guardian=adult,
                      agreed_to_terms=True)
    assert kid.guardian_id == adult.id and not kid.can_sign_in


def test_teen_needs_guardian_waiver(db):
    teen = make_user(db, birth_year=THIS_YEAR - 16)
    assert teen.waiver_status == "guardian_required"
    adult = make_user(db, birth_year=1985)
    assert adult.waiver_status == "self_signed"


def test_guardian_must_be_adult(db):
    teen = make_user(db, birth_year=THIS_YEAR - 16)
    with pytest.raises(ServiceError):
        create_user(db, "Kid", "k2@example.org", THIS_YEAR - 10, None, guardian=teen,
                    agreed_to_terms=True)


def test_terms_must_be_accepted(db):
    with pytest.raises(ServiceError):
        create_user(db, "A", "a@example.org", 1990, "password123", agreed_to_terms=False)
