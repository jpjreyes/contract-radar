"""Review queue (M4): who may change an extracted field, and that every change is audited."""

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("APP_DATABASE_URL"), reason="needs the cc_app role")


@pytest.fixture(scope="module")
def conns():
    from contract_radar.db import connect
    from contract_radar.permissions import app_connect
    return connect(), app_connect()


def _field_of(admin, business_unit: str, region: str):
    row = admin.execute("SELECT f.contract_id, f.name FROM fields f JOIN contracts c ON c.id = f.contract_id "
                        "WHERE c.business_unit = %s AND c.region = %s ORDER BY f.contract_id, f.name LIMIT 1",
                        (business_unit, region)).fetchone()
    if row is None:
        pytest.skip(f"no extracted fields for a {business_unit}/{region} contract yet")
    return row


def test_reviewer_edits_own_contract_and_it_is_audited(conns):
    from contract_radar import audit, review
    from contract_radar.permissions import as_user
    admin, app = conns
    cid, name = _field_of(admin, "Procurement", "North America")  # maria's scope
    with admin.transaction():  # rolled back: the test leaves the real queue as it was
        before = admin.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        app_ok = None
        with as_user(app, "maria"):
            app_ok = review.decide(app, cid, name, "edit", "test value")
        assert app_ok
        status, who, value = admin.execute("SELECT status, reviewed_by, value FROM fields WHERE contract_id = %s "
                                           "AND name = %s", (cid, name)).fetchone()
        assert (status, who, value) == ("reviewed", "maria", "test value")
        last = admin.execute("SELECT app_user, payload FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        assert admin.execute("SELECT count(*) FROM audit_log").fetchone()[0] == before + 1
        assert last[0] == "maria" and last[1]["action"] == "review_field" and last[1]["new_value"] == "test value"
        assert audit.verify(admin) is None
        raise psycopg.Rollback()


def test_non_reviewer_and_out_of_scope_reviews_are_refused(conns):
    from contract_radar import review
    from contract_radar.permissions import as_user
    admin, app = conns
    cid, name = _field_of(admin, "Procurement", "North America")
    other_cid, other_name = _field_of(admin, "Marketing", "EMEA")
    with as_user(app, "kenji"):  # can read Marketing/EMEA, but is not a reviewer
        assert review.decide(app, other_cid, other_name, "accept") is False
    with as_user(app, "maria"):  # a reviewer, but not of Marketing contracts
        assert review.decide(app, other_cid, other_name, "accept") is False
    with as_user(app, "analyst"):
        assert review.decide(app, cid, name, "reject") is False


def test_app_role_cannot_write_fields_directly(conns):
    from contract_radar.permissions import as_user
    _, app = conns
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_user(app, "legal_admin"):
        app.execute("UPDATE fields SET value = 'x'")
