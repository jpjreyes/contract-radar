"""Public demo: undo visitors' review decisions every night, inside Postgres.

Visitors may confirm, edit or reject extracted key terms, and those edits are shared by everyone using the demo. At
load time the extracted values are copied to `fields_seed`; `demo_reset_reviews()` puts every changed field back and
writes one audit entry saying how many it reverted. The audit log itself is never touched, so each visitor edit stays
on record. pg_cron runs the function nightly (11:00 UTC, 3 am Pacific) when the extension is available. On Azure
Postgres Flexible pg_cron is preloaded and lives in the `postgres` database, so the job is created there and pointed at
this database with `cron.schedule_in_database` (no server restart). Without pg_cron, call the function by hand.

Run:  uv run python -m contract_radar.demo_reset [--now]
"""

from __future__ import annotations

import argparse

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from contract_radar.db import connect

SCHEDULE = "0 11 * * *"

SETUP = """
CREATE TABLE IF NOT EXISTS fields_seed AS
    SELECT contract_id, name, present, value, status, reviewed_by, reviewed_at FROM fields WITH NO DATA;

CREATE OR REPLACE FUNCTION demo_reset_reviews() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    n integer;
BEGIN
    PERFORM set_config('app.user', 'demo-reset', true);
    UPDATE fields f SET present = s.present, value = s.value, status = s.status,
                        reviewed_by = s.reviewed_by, reviewed_at = s.reviewed_at
    FROM fields_seed s
    WHERE s.contract_id = f.contract_id AND s.name = f.name
      AND (f.present, f.value, f.status, f.reviewed_by) IS DISTINCT FROM (s.present, s.value, s.status, s.reviewed_by);
    GET DIAGNOSTICS n = ROW_COUNT;
    PERFORM audit_append(jsonb_build_object('action', 'demo_reset', 'fields_reverted', n));
    RETURN n;
END $$;
REVOKE ALL ON FUNCTION demo_reset_reviews() FROM PUBLIC;
"""


def setup(admin: psycopg.Connection) -> None:
    """Snapshot the current key terms as the state to return to, and schedule the nightly reset if pg_cron exists."""
    admin.execute(SETUP)
    admin.execute("TRUNCATE fields_seed")
    admin.execute("INSERT INTO fields_seed SELECT contract_id, name, present, value, status, reviewed_by, reviewed_at "
                  "FROM fields")
    if not admin.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron'").fetchone():
        print("demo reset: function ready; pg_cron not available here, so no nightly schedule", flush=True)
        return
    info = conninfo_to_dict(admin.info.dsn)
    try:
        with psycopg.connect(make_conninfo(admin.info.dsn, dbname="postgres",
                                                            password=admin.info.password), autocommit=True) as cron_db:
            cron_db.execute("CREATE EXTENSION IF NOT EXISTS pg_cron")
            cron_db.execute("SELECT cron.schedule_in_database('demo-reset-reviews', %s, 'SELECT demo_reset_reviews()', %s)",
                            (SCHEDULE, info["dbname"]))
        print(f"demo reset: scheduled with pg_cron ({SCHEDULE} UTC) in database {info['dbname']}", flush=True)
    except psycopg.Error as e:  # pg_cron not preloaded or not allowed on this server
        print(f"demo reset: function ready; pg_cron schedule failed: {e}".strip(), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="revert reviewed fields now")
    args = ap.parse_args()
    admin = connect()
    if args.now:
        print(f"reverted {admin.execute('SELECT demo_reset_reviews()').fetchone()[0]} fields")
    else:
        setup(admin)


if __name__ == "__main__":
    main()
