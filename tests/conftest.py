import os

os.environ["MSA_CHECKPOINT_DB"] = "memory"
os.environ["MSA_LLM_PROVIDER"] = "offline"
os.environ["MSA_SCHEDULER"] = "off"
os.environ["MSA_TELEMETRY_DB"] = "memory"

import pytest  # noqa: E402

from msa.core.llm import set_llm  # noqa: E402
from msa.core.testing import fresh_db  # noqa: E402


@pytest.fixture
def db():
    session = fresh_db()
    set_llm(None)
    yield session
    session.rollback()
    session.close()
    set_llm(None)


@pytest.fixture
def client(db):
    """API client sharing the in-memory database."""
    from fastapi.testclient import TestClient

    from msa.api.main import create_app

    app = create_app(start_scheduler=False)
    with TestClient(app) as c:
        yield c


def auth_headers(client, email, password="password123"):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}
