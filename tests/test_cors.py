"""CORS for the web app hosted on Vercel: only the configured origin may call the API from a browser."""

import importlib

import pytest
from fastapi.testclient import TestClient

WEB = "https://contract-radar.vercel.app"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CC_CORS_ORIGINS", WEB)
    from contract_radar import api
    yield TestClient(importlib.reload(api).app)
    monkeypatch.delenv("CC_CORS_ORIGINS")
    importlib.reload(api)


def preflight(client, origin):
    return client.options("/contracts", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-user,content-type"})


def test_configured_origin_may_call_with_the_user_header(client):
    r = preflight(client, WEB)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == WEB
    assert "x-user" in r.headers["access-control-allow-headers"].lower()


def test_other_origins_are_refused(client):
    r = preflight(client, "https://evil.example")
    assert "access-control-allow-origin" not in r.headers


def test_healthz_response_carries_the_header_for_the_web_origin(client):
    r = client.get("/healthz", headers={"Origin": WEB})
    assert r.headers.get("access-control-allow-origin") == WEB
