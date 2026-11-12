"""OIDC sign-in: token verified (signature, issuer, audience, expiry), person or group mapped to a role in Postgres."""

import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role")

ISS, AUD = "https://login.example.com/tenant/v2.0", "contract-radar-test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(**over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": AUD, "iat": now, "exp": now + 600, "preferred_username": "maria@northline.test",
              **over}
    return jwt.encode(claims, KEY, algorithm="RS256")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from contract_radar.api import app
    from contract_radar.db import connect
    admin = connect()
    admin.execute("DELETE FROM identity_map WHERE value IN ('maria@northline.test', 'grp-marketing-emea')")
    admin.execute("INSERT INTO identity_map VALUES ('user', 'maria@northline.test', 'maria'), "
                  "('group', 'grp-marketing-emea', 'kenji')")
    yield TestClient(app)
    admin.execute("DELETE FROM identity_map WHERE value IN ('maria@northline.test', 'grp-marketing-emea')")


@pytest.fixture(autouse=True)
def oidc(monkeypatch):
    from contract_radar import auth
    monkeypatch.setenv("CC_AUTH", "oidc")
    monkeypatch.setenv("CC_OIDC_ISSUER", ISS)
    monkeypatch.setenv("CC_OIDC_AUDIENCE", AUD)
    monkeypatch.setattr(auth, "signing_key", lambda _t: KEY.public_key())


def get(client, tok):
    return client.get("/contracts", headers={"Authorization": f"Bearer {tok}"} if tok else {})


def test_person_mapping(client):
    assert len(get(client, token()).json()) == 50  # maria's contracts


def test_group_mapping(client):
    r = get(client, token(preferred_username="someone@northline.test", groups=["grp-marketing-emea"]))
    assert len(r.json()) == 46  # kenji's role, via the directory group


@pytest.mark.parametrize("bad", [
    {"aud": "another-app"}, {"iss": "https://evil.example.com"}, {"exp": int(time.time()) - 10},
    {"preferred_username": "stranger@northline.test"},
])
def test_rejected(client, bad):
    assert get(client, token(**bad)).status_code == 401


def test_demo_header_is_ignored_in_oidc_mode(client):
    assert client.get("/contracts", headers={"X-User": "legal_admin"}).status_code == 401
    assert get(client, jwt.encode({"iss": ISS, "aud": AUD}, "x" * 48, algorithm="HS256")).status_code == 401
