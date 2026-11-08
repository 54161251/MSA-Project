from msa.core.db import session_scope
from msa.core.testing import make_equipment, make_user
from tests.conftest import auth_headers


def test_signup_login_me(client):
    r = client.post("/auth/signup", json={"name": "Ana", "email": "ana@example.org",
                                          "password": "password123", "birth_year": 1999,
                                          "agreed_to_terms": True})
    assert r.status_code == 200, r.text
    assert "password_hash" not in r.json()
    h = auth_headers(client, "ana@example.org")
    assert client.get("/auth/me", headers=h).json()["email"] == "ana@example.org"


def test_staff_routes_check_role_on_server(client):
    with session_scope() as db:
        m = make_user(db)
        email = m.email
    h = auth_headers(client, email)
    assert client.get("/users", headers=h).status_code == 403
    assert client.get("/approvals", headers=h).status_code == 403
    assert client.get("/users").status_code == 401


def test_member_cannot_see_retired_equipment_and_usage_blocked_when_out_of_service(client):
    with session_scope() as db:
        make_equipment(db, "old-01", status="retired")
        make_equipment(db, "saw-01", status="out_of_service")
        m = make_user(db)
        email = m.email
    h = auth_headers(client, email)
    ids = [e["id"] for e in client.get("/equipment", headers=h).json()]
    assert "old-01" not in ids and "saw-01" in ids
    r = client.post("/usage/start", json={"equipment_id": "saw-01"}, headers=h)
    assert r.status_code == 400 and "out of service" in r.json()["detail"]


def test_health_lists_plugins(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"]
