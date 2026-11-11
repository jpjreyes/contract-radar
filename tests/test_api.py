"""API (M6): the same permission guarantees, seen from HTTP."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from contract_copilot.api import app
    return TestClient(app)


def h(user):
    return {"X-User": user}


def test_unknown_user_is_rejected(client):
    assert client.get("/contracts", headers=h("mallory")).status_code == 401
    assert client.get("/contracts").status_code == 422  # no identity at all


def test_each_user_lists_only_their_contracts(client):
    counts = {u: len(client.get("/contracts", headers=h(u)).json()) for u in ("legal_admin", "maria", "kenji")}
    assert counts == {"legal_admin": 510, "maria": 50, "kenji": 46}


def test_contract_text_is_masked_or_revealed_by_role_and_forbidden_is_404(client):
    from contract_copilot.db import connect
    cid = connect().execute(
        "SELECT c.id FROM contracts c JOIN chunks k ON k.contract_id = c.id WHERE c.business_unit = 'Legal' "
        "AND k.text_masked LIKE '%%[PERSON_%%' ORDER BY c.id LIMIT 1").fetchone()[0]
    analyst = client.get(f"/contracts/{cid}", headers=h("analyst")).json()
    admin = client.get(f"/contracts/{cid}", headers=h("legal_admin")).json()
    assert any("[PERSON_" in c["text"] for c in analyst["chunks"])
    assert not any("[PERSON_" in c["text"] for c in admin["chunks"])
    assert client.get(f"/contracts/{cid}", headers=h("maria")).status_code == 404


def test_review_refused_for_non_reviewer(client):
    item = client.get("/review", headers=h("kenji")).json()
    if not item:
        pytest.skip("kenji has nothing in the queue yet")
    r = client.post("/review", headers=h("kenji"),
                    json={"contract_id": item[0]["contract_id"], "field": item[0]["field"], "decision": "accept"})
    assert r.status_code == 403
