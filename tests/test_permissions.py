"""Row-level security against the real database (skipped without APP_DATABASE_URL). C2: never a forbidden row."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role (permissions.py)")


@pytest.fixture(scope="module")
def conns():
    from contract_copilot.db import connect
    from contract_copilot.permissions import app_connect
    return connect(), app_connect()


def test_app_role_cannot_bypass_rls(conns):
    _, app = conns
    assert app.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()[0] is False


def test_deny_by_default_without_or_with_unknown_user(conns):
    from contract_copilot.permissions import as_user
    _, app = conns
    assert app.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    with as_user(app, "nobody"):
        assert app.execute("SELECT count(*) FROM contracts").fetchone()[0] == 0


def test_forbidden_text_is_unreachable_even_by_exact_search_and_id(conns):
    from contract_copilot.permissions import as_user
    from contract_copilot.retrieve import search
    admin, app = conns
    cid, text = admin.execute(
        "SELECT c.id, k.text FROM contracts c JOIN chunks k ON k.contract_id = c.id "
        "WHERE c.business_unit = 'Marketing' AND length(k.text) > 400 ORDER BY c.id, k.idx LIMIT 1").fetchone()
    with as_user(app, "maria"):  # Procurement / North America only
        assert search(app, text[:300], method="keyword", k=8, contract_id=cid) == []
        assert all(h.contract_id != cid for h in search(app, text[:300], method="keyword", k=8))
    with as_user(app, "legal_admin"):
        assert any(h.contract_id == cid for h in search(app, text[:300], method="keyword", k=8))


def test_setting_ends_with_the_transaction(conns):
    from contract_copilot.permissions import as_user
    _, app = conns
    with as_user(app, "legal_admin"):
        assert app.execute("SELECT count(*) FROM contracts").fetchone()[0] == 510
    assert app.execute("SELECT count(*) FROM contracts").fetchone()[0] == 0
