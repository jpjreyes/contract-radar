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
    with as_user(app, "legal_admin"):  # control: the same search reaches it for a user who may read it
        assert search(app, text[:300], method="keyword", k=8, contract_id=cid)


def test_setting_ends_with_the_transaction(conns):
    from contract_copilot.permissions import as_user
    _, app = conns
    with as_user(app, "legal_admin"):
        assert app.execute("SELECT count(*) FROM contracts").fetchone()[0] == 510
    assert app.execute("SELECT count(*) FROM contracts").fetchone()[0] == 0


# ---- personal data (C3, D9)

def _pii_chunk(admin, business_unit="Legal"):
    """A Legal chunk with a masked person in it (the analyst can read it but must not see the name)."""
    return admin.execute(
        "SELECT k.id, k.text, k.text_masked, k.mask_map FROM chunks k JOIN contracts c ON c.id = k.contract_id "
        "WHERE c.business_unit = %s AND k.text_masked LIKE '%%[PERSON_%%' ORDER BY k.id LIMIT 1",
        (business_unit,)).fetchone()


def test_app_role_cannot_select_the_original_column(conns):
    import psycopg

    from contract_copilot.permissions import as_user
    _, app = conns
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_user(app, "legal_admin"):
        app.execute("SELECT text FROM chunks LIMIT 1")


def test_original_is_revealed_only_to_users_with_pii_access(conns):
    from contract_copilot.permissions import as_user
    from contract_copilot.retrieve import _hit
    from contract_copilot.serve import serve
    admin, app = conns
    chunk_id, original, masked, mapping = _pii_chunk(admin)
    assert masked != original and mapping
    _, _, a, b = mapping[0]
    value = original[a:b]
    with as_user(app, "analyst"):  # may read Legal contracts, may not see personal data
        row = app.execute("SELECT id, contract_id, start_char, end_char, text_masked, 0.0, mask_map FROM chunks "
                          "WHERE id = %s", (chunk_id,)).fetchone()
        assert row is not None
        assert app.execute("SELECT * FROM reveal(%s)", ([chunk_id],)).fetchall() == []
        assert value not in serve(app, [_hit(row)])[0].text
    with as_user(app, "legal_admin"):
        assert serve(app, [_hit(row)])[0].text == original
    with as_user(app, "maria"):  # Procurement / North America: can't read Legal contracts at all
        assert app.execute("SELECT * FROM reveal(%s)", ([chunk_id],)).fetchall() == []


def test_searching_for_a_masked_name_finds_nothing(conns):
    from contract_copilot.permissions import as_user
    from contract_copilot.retrieve import search
    from contract_copilot.serve import serve
    admin, app = conns
    _chunk_id, original, _, mapping = _pii_chunk(admin)
    value = original[mapping[0][2]:mapping[0][3]]
    with as_user(app, "analyst"):
        for method in ("keyword", "hybrid"):
            assert all(value.lower() not in h.text.lower() for h in serve(app, search(app, value, method=method)))
