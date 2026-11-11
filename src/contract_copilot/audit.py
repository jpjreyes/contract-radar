"""Audit log (C5): every question, who asked it, what they were shown. Append-only and hash-chained.

The app role can't read, change or delete the table. Its only access is `audit_append(payload)`, a SECURITY DEFINER
function that stamps the user from `app.user` and the time from the database (the client can't claim to be someone
else), and chains each row to the previous one: hash = sha256(previous hash || timestamp || user || payload). Editing
or deleting any past row breaks every hash after it; `audit_verify()` returns the first row that doesn't check out.

The log stores ids, counts and flags, never answer text, so it doesn't become a second copy of personal data. It
does record whether personal data was revealed to the user, and which chunks.

Setup runs with the permissions setup:  uv run python -m contract_copilot.permissions
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

GENESIS = "0" * 64

SETUP = """
CREATE TABLE IF NOT EXISTS audit_log (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL,
    app_user  text NOT NULL,
    payload   jsonb NOT NULL,
    prev_hash text NOT NULL,
    hash      text NOT NULL
);

CREATE OR REPLACE FUNCTION audit_hash(prev text, at timestamptz, who text, payload jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(prev || '|' || to_char(at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
                                    || '|' || who || '|' || payload::text, 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION audit_append(payload jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    who  text := coalesce(nullif(current_setting('app.user', true), ''), '(none)');
    ts   timestamptz := clock_timestamp();
    prev text;
    new_id bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('audit_log'));  -- one writer at a time: the chain has no forks
    SELECT hash INTO prev FROM audit_log ORDER BY id DESC LIMIT 1;
    prev := coalesce(prev, '{genesis}');
    INSERT INTO audit_log (at, app_user, payload, prev_hash, hash)
    VALUES (ts, who, payload, prev, audit_hash(prev, ts, who, payload))
    RETURNING id INTO new_id;
    RETURN new_id;
END $$;

-- First row whose link or hash doesn't check out (NULL = the whole chain is intact).
CREATE OR REPLACE FUNCTION audit_verify() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT min(id) FROM (
        SELECT id, prev_hash, hash, at, app_user, payload,
               coalesce(lag(hash) OVER (ORDER BY id), '{genesis}') AS expected_prev
        FROM audit_log) t
    WHERE prev_hash <> expected_prev OR hash <> audit_hash(prev_hash, at, app_user, payload)
$$;

REVOKE ALL ON audit_log FROM {role};
REVOKE ALL ON FUNCTION audit_append(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION audit_verify() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit_append(jsonb) TO {role};
"""


def setup(admin: psycopg.Connection, role: str) -> None:
    admin.execute(SETUP.replace("{role}", role).replace("{genesis}", GENESIS))


def append(conn: psycopg.Connection, payload: dict) -> int:
    return conn.execute("SELECT audit_append(%s)", (Jsonb(payload),)).fetchone()[0]


def verify(admin: psycopg.Connection) -> int | None:
    """Id of the first broken row, or None if the chain is intact (admin/auditor connection)."""
    return admin.execute("SELECT audit_verify()").fetchone()[0]
