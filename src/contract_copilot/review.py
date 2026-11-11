"""Review queue (M4): people accept, correct or reject the fields extraction wasn't sure about.

Fields follow the same row-level security as contracts (you see a contract's fields only if you can read the
contract). Changing one goes through `review_field(...)` only, SECURITY DEFINER: it checks the user may review
(app_users.can_review) AND may read that contract, records who and when on the field, and writes an audit entry with
the old and new value. A re-extraction never overwrites a reviewed field (extract.save).

Run:  uv run python -m contract_copilot.review --user maria            (list the queue)
      uv run python -m contract_copilot.review --user maria --contract 12 --field governing_law --edit "Delaware"
"""

from __future__ import annotations

import argparse

import psycopg

from contract_copilot.extract import SETUP as FIELDS_TABLE
from contract_copilot.permissions import app_connect, as_user

REVIEWERS = {"legal_admin", "maria"}  # legal ops lead; procurement manager for her own contracts

SETUP = """
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS can_review boolean NOT NULL DEFAULT false;

ALTER TABLE fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE fields FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS owner_all ON fields;  -- see permissions.py: definer functions on a non-superuser owner
CREATE POLICY owner_all ON fields FOR ALL TO CURRENT_USER USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS read_fields ON fields;
CREATE POLICY read_fields ON fields FOR SELECT TO {role}
    USING (EXISTS (SELECT 1 FROM contracts c WHERE c.id = fields.contract_id));
REVOKE ALL ON fields FROM {role};
GRANT SELECT ON fields TO {role};

CREATE OR REPLACE FUNCTION review_field(cid integer, fname text, decision text, new_value text DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    who text := current_setting('app.user', true);
    old fields%ROWTYPE;
BEGIN
    IF decision NOT IN ('accept', 'edit', 'reject') THEN
        RAISE EXCEPTION 'decision must be accept, edit or reject';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app_users u JOIN contracts c ON c.id = cid
                   WHERE u.name = who AND u.can_review AND can_read(c.region, c.business_unit)) THEN
        RETURN false;  -- not a reviewer, or can't read this contract: same answer, nothing changes
    END IF;
    SELECT * INTO old FROM fields WHERE contract_id = cid AND name = fname FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    UPDATE fields SET
        present = CASE decision WHEN 'reject' THEN false WHEN 'edit' THEN true ELSE present END,
        value = CASE decision WHEN 'reject' THEN '' WHEN 'edit' THEN new_value ELSE value END,
        status = 'reviewed', reviewed_by = who, reviewed_at = now()
    WHERE contract_id = cid AND name = fname;
    PERFORM audit_append(jsonb_build_object(
        'action', 'review_field', 'contract_id', cid, 'field', fname, 'decision', decision,
        'old_value', old.value, 'old_present', old.present, 'old_status', old.status,
        'new_value', CASE decision WHEN 'reject' THEN '' WHEN 'edit' THEN new_value ELSE old.value END));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION review_field(integer, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION review_field(integer, text, text, text) TO {role};
"""


def setup(admin: psycopg.Connection, role: str) -> None:
    admin.execute(FIELDS_TABLE)
    admin.execute(SETUP.replace("{role}", role))
    admin.execute("UPDATE app_users SET can_review = (name = ANY(%s))", (sorted(REVIEWERS),))


def queue(conn: psycopg.Connection, limit: int = 20) -> list[tuple]:
    """Fields waiting for review that the current user can see, issues first."""
    return conn.execute(
        "SELECT f.contract_id, c.name, f.name, f.present, f.value, f.confidence, f.issues, f.quote, f.start_char, "
        "f.end_char FROM fields f "
        "JOIN contracts c ON c.id = f.contract_id WHERE f.status = 'needs_review' "
        "ORDER BY cardinality(f.issues) DESC, f.contract_id, f.name LIMIT %s", (limit,)).fetchall()


def decide(conn: psycopg.Connection, contract_id: int, field: str, decision: str, value: str | None = None) -> bool:
    return conn.execute("SELECT review_field(%s, %s, %s, %s)", (contract_id, field, decision, value)).fetchone()[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--contract", type=int)
    ap.add_argument("--field")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--accept", action="store_true")
    g.add_argument("--reject", action="store_true")
    g.add_argument("--edit")
    a = ap.parse_args()
    conn = app_connect()
    with as_user(conn, a.user):
        if a.contract is None:
            for cid, name, field, present, value, conf, issues, _quote, _s, _e in queue(conn):
                flag = f" ⚠ {', '.join(issues)}" if issues else ""
                print(f"#{cid} {name[:40]:40} {field:32} {conf:6} {value[:50]!r}{flag}")
            return
        decision = "accept" if a.accept else "reject" if a.reject else "edit"
        ok = decide(conn, a.contract, a.field, decision, a.edit)
        print("saved" if ok else "not allowed (not a reviewer, or not your contract)")


if __name__ == "__main__":
    main()
