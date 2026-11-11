"""Audit log (C5): append-only for the app, user stamped by the database, tampering detected."""

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role")


@pytest.fixture(scope="module")
def conns():
    from contract_copilot.db import connect
    from contract_copilot.permissions import app_connect
    return connect(), app_connect()


def test_append_stamps_the_user_from_the_database(conns):
    from contract_copilot import audit
    from contract_copilot.permissions import as_user
    admin, app = conns
    with as_user(app, "kenji"):
        rid = audit.append(app, {"action": "test", "app_user": "legal_admin"})  # a claim in the payload is ignored
    who, payload = admin.execute("SELECT app_user, payload FROM audit_log WHERE id = %s", (rid,)).fetchone()
    assert who == "kenji" and payload["app_user"] == "legal_admin"
    assert audit.verify(admin) is None


def test_app_role_cannot_read_change_or_delete_the_log(conns):
    from contract_copilot.permissions import as_user
    _, app = conns
    for sql in ("SELECT * FROM audit_log", "UPDATE audit_log SET app_user = 'x'", "DELETE FROM audit_log",
                "SELECT audit_verify()"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_user(app, "legal_admin"):
            app.execute(sql)


def test_editing_a_past_row_breaks_the_chain_at_that_row(conns):
    from contract_copilot import audit
    from contract_copilot.permissions import as_user
    admin, app = conns
    with as_user(app, "analyst"):
        first = audit.append(app, {"action": "test", "n": 1})
        audit.append(app, {"action": "test", "n": 2})
    with admin.transaction():  # even an admin edit shows up; rolled back so the real log stays intact
        admin.execute("UPDATE audit_log SET payload = payload || '{\"n\": 99}' WHERE id = %s", (first,))
        assert audit.verify(admin) == first
        raise psycopg.Rollback()
    assert audit.verify(admin) is None
