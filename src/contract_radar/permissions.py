"""Per-user access, enforced by Postgres row-level security (D4).

The app connects as `cc_app`, a login role that is NOT a superuser and does not bypass RLS (superusers always bypass
it). Every request sets `app.user` for its transaction; policies on `contracts` and `chunks` only return rows that user
may read. No user set, unknown user, or no matching grant -> no rows (deny by default). Because the filter runs inside
the search query, a user's top-k is computed over what they can see: nothing forbidden is ranked and dropped later.

Setup (admin connection):  uv run python -m contract_radar.permissions
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import psycopg.sql
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from contract_radar import audit
from contract_radar.db import connect

APP_ROLE = "cc_app"

# Demo users (documented in CLIENT_BRIEF.md). all_access sees every contract; otherwise both region and BU must match.
USERS = [
    ("legal_admin", "Legal operations lead", True, [], [], True),
    ("maria", "Procurement manager, North America", False, ["North America"], ["Procurement"], False),
    ("kenji", "Marketing manager, EMEA and LATAM", False, ["EMEA", "LATAM"], ["Marketing"], False),
    ("analyst", "Analyst, all Legal contracts, no personal data", False,
     ["North America", "EMEA", "LATAM"], ["Legal"], False),
]

SETUP = """
CREATE TABLE IF NOT EXISTS app_users (
    name           text PRIMARY KEY,
    description    text NOT NULL,
    all_access     boolean NOT NULL DEFAULT false,
    regions        text[] NOT NULL DEFAULT '{}',
    business_units text[] NOT NULL DEFAULT '{}',
    pii_access     boolean NOT NULL DEFAULT false
);

CREATE OR REPLACE FUNCTION can_read(c_region text, c_bu text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM app_users u
        WHERE u.name = current_setting('app.user', true)
          AND (u.all_access OR (c_region = ANY (u.regions) AND c_bu = ANY (u.business_units))))
$$;

ALTER TABLE contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE contracts FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS read_contracts ON contracts;
CREATE POLICY read_contracts ON contracts FOR SELECT TO {role} USING (can_read(region, business_unit));

-- The SECURITY DEFINER functions (reveal, review_field, can_read) run as the role that ran this setup. On a managed
-- Postgres that role is NOT a superuser, so FORCE ROW LEVEL SECURITY applies to it too; without these policies the
-- functions would silently see no rows. They check permissions themselves, so the owner may read and write.
DROP POLICY IF EXISTS owner_all ON contracts;
CREATE POLICY owner_all ON contracts FOR ALL TO CURRENT_USER USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS owner_all ON chunks;
CREATE POLICY owner_all ON chunks FOR ALL TO CURRENT_USER USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS read_chunks ON chunks;
CREATE POLICY read_chunks ON chunks FOR SELECT TO {role}
    USING (EXISTS (SELECT 1 FROM contracts c WHERE c.id = chunks.contract_id));

GRANT USAGE ON SCHEMA public TO {role};
GRANT SELECT ON contracts TO {role};
REVOKE ALL ON app_users FROM {role};

-- Personal data (D9): the app role can read every chunk column EXCEPT the original text. Masked text, offsets and
-- the indexes built on masked text are all it gets; a query that names `text` fails with a privilege error.
REVOKE SELECT ON chunks FROM {role};
GRANT SELECT (id, contract_id, idx, start_char, end_char, heading, text_masked, mask_map, embedding, tsv)
    ON chunks TO {role};

-- The only way back to the original: for the current user, only if they have pii_access AND may read the contract.
-- SECURITY DEFINER runs as the owner (who bypasses RLS), so both checks are explicit here.
CREATE OR REPLACE FUNCTION reveal(ids integer[]) RETURNS TABLE (id integer, text text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT k.id, k.text
    FROM chunks k
    JOIN contracts c ON c.id = k.contract_id
    JOIN app_users u ON u.name = current_setting('app.user', true)
    WHERE k.id = ANY (ids) AND u.pii_access AND can_read(c.region, c.business_unit)
$$;
REVOKE ALL ON FUNCTION reveal(integer[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reveal(integer[]) TO {role};
"""


def setup(admin: psycopg.Connection, app_password: str) -> None:
    exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
    pw = psycopg.sql.Literal(app_password).as_string(admin)
    if not exists:
        admin.execute(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT PASSWORD {pw}")
    else:  # managed Postgres admins aren't superusers and can't touch those attributes: check them instead
        su, bypass = admin.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        if su or bypass:
            raise RuntimeError(f"{APP_ROLE} must not be a superuser or bypass row-level security")
        admin.execute(f"ALTER ROLE {APP_ROLE} LOGIN PASSWORD {pw}")
    admin.execute(SETUP.replace("{role}", APP_ROLE))
    with admin.transaction():
        admin.execute("DELETE FROM app_users")
        for u in USERS:
            admin.execute("INSERT INTO app_users VALUES (%s, %s, %s, %s, %s, %s)", u)


def app_connect() -> psycopg.Connection:
    load_dotenv()
    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        raise RuntimeError("Set APP_DATABASE_URL (the cc_app role) in .env")
    conn = psycopg.connect(url, autocommit=True)
    register_vector(conn)
    return conn


@contextmanager
def as_user(conn: psycopg.Connection, user: str) -> Iterator[psycopg.Connection]:
    """Run everything inside the block as `user`; the setting ends with the transaction."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.user', %s, true)", (user,))
        yield conn


def main() -> None:
    load_dotenv()
    pw = os.environ.get("APP_DB_PASSWORD")
    if not pw:
        raise SystemExit("Set APP_DB_PASSWORD in .env first")
    admin = connect()
    setup(admin, pw)
    audit.setup(admin, APP_ROLE)
    from contract_radar import review  # after audit: review_field() writes audit entries
    review.setup(admin, APP_ROLE)
    from contract_radar import auth, limits
    limits.setup(admin, APP_ROLE)
    auth.setup(admin, APP_ROLE)
    print(f"role {APP_ROLE} + RLS policies + {len(USERS)} demo users + audit log + review queue ready")


if __name__ == "__main__":
    main()
