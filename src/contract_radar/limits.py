"""Limits for a public demo: a monthly spend cap, a per-user rate limit, and replay mode.

- **Spend cap:** every model call made through the API is written to `usage_ledger` with its cost; before a new
  call, this month's total is checked against CC_MONTHLY_BUDGET_USD (default $10). Over the cap, live questions stop
  with a clear message (402); the rest of the app (radar, contracts, review) keeps working, it needs no model.
- **Rate limit:** CC_RATE_PER_HOUR live questions per user per rolling hour (default 30) → 429.
- **Replay mode (CC_MODE=replay):** answers to the example questions are recorded once (scripts/record_replay.py)
  and served from `replay/*.json` without calling the model; anything else gets 409 "live answers are off". The
  demo can run at $0.
- **Hybrid mode (CC_MODE=hybrid):** the example questions are served from the recordings (instant, no model call);
  any other question is answered live, behind the spend cap and the rate limit. This is the public demo's setting.

The ledger holds user, time, tokens and cost only (no question text), and the app role can insert and read it but not
change or delete rows.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import psycopg

REPLAY_DIR = Path(__file__).resolve().parents[2] / "replay"

SETUP = """
CREATE TABLE IF NOT EXISTS usage_ledger (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    app_user  text NOT NULL,
    action    text NOT NULL,
    tokens_in integer NOT NULL,
    tokens_out integer NOT NULL,
    usd       numeric(12, 6) NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_ledger_at ON usage_ledger (at);
REVOKE ALL ON usage_ledger FROM {role};
GRANT SELECT, INSERT ON usage_ledger TO {role};
GRANT USAGE ON SEQUENCE usage_ledger_id_seq TO {role};
"""


class LimitExceeded(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def setup(admin: psycopg.Connection, role: str) -> None:
    admin.execute(SETUP.replace("{role}", role))


def mode() -> str:
    return os.environ.get("CC_MODE", "live")


def budget_usd() -> float:
    return float(os.environ.get("CC_MONTHLY_BUDGET_USD", "10"))


def rate_per_hour() -> int:
    return int(os.environ.get("CC_RATE_PER_HOUR", "30"))


def check(conn: psycopg.Connection, user: str) -> None:
    """Raise LimitExceeded before a live model call if the month's budget or the user's hourly rate is used up."""
    spent = conn.execute("SELECT coalesce(sum(usd), 0) FROM usage_ledger "
                         "WHERE at >= date_trunc('month', now())").fetchone()[0]
    if float(spent) >= budget_usd():
        raise LimitExceeded(402, f"This demo's monthly model budget (${budget_usd():.0f}) is used up; the radar, "
                                 "contracts and review queue still work.")
    recent = conn.execute("SELECT count(*) FROM usage_ledger WHERE app_user = %s AND at > now() - interval '1 hour'",
                          (user,)).fetchone()[0]
    if recent >= rate_per_hour():
        raise LimitExceeded(429, f"Limit of {rate_per_hour()} questions per hour reached; try again later.")


def record(conn: psycopg.Connection, user: str, action: str, tokens_in: int, tokens_out: int, usd: float) -> None:
    conn.execute("INSERT INTO usage_ledger (app_user, action, tokens_in, tokens_out, usd) VALUES (%s,%s,%s,%s,%s)",
                 (user, action, tokens_in, tokens_out, usd))


def replay_key(user: str, kind: str, question: str, contract_id: int | None) -> str:
    raw = json.dumps([user, kind, question.strip().lower(), contract_id])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def replay_find(user: str, kind: str, question: str, contract_id: int | None) -> dict | None:
    path = REPLAY_DIR / f"{replay_key(user, kind, question, contract_id)}.json"
    return json.loads(path.read_text()) if path.exists() else None


def replay_get(user: str, kind: str, question: str, contract_id: int | None) -> dict:
    hit = replay_find(user, kind, question, contract_id)
    if hit is None:
        raise LimitExceeded(409, "Live answers are off in this public demo: pick one of the example questions.")
    return hit


def replay_put(user: str, kind: str, question: str, contract_id: int | None, response: dict) -> Path:
    REPLAY_DIR.mkdir(exist_ok=True)
    path = REPLAY_DIR / f"{replay_key(user, kind, question, contract_id)}.json"
    path.write_text(json.dumps({**response, "replayed": True}, indent=1))
    return path
