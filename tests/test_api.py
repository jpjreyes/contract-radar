"""API (M6): the same permission guarantees, seen from HTTP."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from contract_radar.api import app
    return TestClient(app)


def h(user):
    return {"X-User": user}


def test_unknown_user_is_rejected(client):
    assert client.get("/contracts", headers=h("mallory")).status_code == 401
    assert client.get("/contracts").status_code == 401  # no identity at all


def test_each_user_lists_only_their_contracts(client):
    counts = {u: len(client.get("/contracts", headers=h(u)).json()) for u in ("legal_admin", "maria", "kenji")}
    assert counts == {"legal_admin": 510, "maria": 50, "kenji": 46}


def test_contract_text_is_masked_or_revealed_by_role_and_forbidden_is_404(client):
    from contract_radar.db import connect
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


def test_radar_respects_permissions(client):
    admin = client.get("/radar?as_of=2026-11-11&days=90", headers=h("legal_admin")).json()
    maria = client.get("/radar?as_of=2026-11-11&days=90", headers=h("maria")).json()
    assert admin["contracts"] == 510 and maria["contracts"] == 50
    admin_ids = {i["contract_id"] for i in admin["sections"]["notice_deadlines"]}
    maria_ids = {i["contract_id"] for i in maria["sections"]["notice_deadlines"]}
    assert maria_ids <= admin_ids and len(maria_ids) < len(admin_ids)
    days = [i["days_left"] for i in admin["sections"]["notice_deadlines"]]
    assert days == sorted(days) and all(0 <= d <= 90 for d in days)
    assert client.get("/radar?as_of=not-a-date", headers=h("maria")).status_code == 422


def test_spend_cap_and_rate_limit_stop_live_questions_before_any_model_call(client, monkeypatch):
    monkeypatch.setenv("CC_MONTHLY_BUDGET_USD", "0")
    r = client.post("/portfolio", headers=h("analyst"), json={"question": "Which contracts have exclusivity?"})
    assert r.status_code == 402 and "budget" in r.json()["detail"]
    monkeypatch.setenv("CC_MONTHLY_BUDGET_USD", "1000")
    monkeypatch.setenv("CC_RATE_PER_HOUR", "0")
    r = client.post("/ask", headers=h("analyst"), json={"question": "Who signed?", "contract_id": 1})
    assert r.status_code == 429
    assert client.get("/radar", headers=h("analyst")).status_code == 200  # no model needed: unaffected


def test_replay_mode_serves_recorded_answers_only(client, monkeypatch, tmp_path):
    from contract_radar import limits
    monkeypatch.setenv("CC_MODE", "replay")
    monkeypatch.setattr(limits, "REPLAY_DIR", tmp_path)
    q = "Which contracts have exclusivity?"
    assert client.post("/portfolio", headers=h("maria"), json={"question": q}).status_code == 409
    limits.replay_put("maria", "portfolio", q, None, {"conditions": [], "matches": [], "unreviewed": 0})
    r = client.post("/portfolio", headers=h("maria"), json={"question": q})
    assert r.status_code == 200 and r.json()["replayed"] is True
    # a recording is per user: another user can't read maria's recorded answer
    assert client.post("/portfolio", headers=h("kenji"), json={"question": q}).status_code == 409


def test_hybrid_mode_serves_recordings_and_answers_other_questions_live(client, monkeypatch, tmp_path):
    from contract_radar import api, limits
    monkeypatch.setenv("CC_MODE", "hybrid")
    monkeypatch.setattr(limits, "REPLAY_DIR", tmp_path)
    q = "Which contracts have exclusivity?"
    limits.replay_put("maria", "portfolio", q, None, {"conditions": [], "matches": [], "unreviewed": 0})
    calls = []
    monkeypatch.setattr(api, "ask_portfolio", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(RuntimeError("live")))
    r = client.post("/portfolio", headers=h("maria"), json={"question": q})
    assert r.status_code == 200 and r.json()["replayed"] is True and not calls  # recorded: no model call
    with pytest.raises(RuntimeError, match="live"):  # anything else goes to the live path
        client.post("/portfolio", headers=h("maria"), json={"question": "Which contracts are governed by Texas law?"})
    assert calls
    assert client.get("/mode", headers=h("maria")).json() == {"mode": "hybrid"}


def test_pdf_export_is_the_users_view_and_is_audited(client):
    import io

    from pypdf import PdfReader
    cid = client.get("/contracts", headers=h("analyst")).json()[0]["id"]
    r = client.get(f"/contracts/{cid}/pdf", headers=h("analyst"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "exported by analyst" in text and "[PERSON_" not in text  # bars, not placeholders or names
    hidden = {c["id"] for c in client.get("/contracts", headers=h("legal_admin")).json()} - \
             {c["id"] for c in client.get("/contracts", headers=h("kenji")).json()}
    assert client.get(f"/contracts/{min(hidden)}/pdf", headers=h("kenji")).status_code == 404
