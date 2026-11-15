"""The nightly demo reset puts reviewed key terms back and leaves the audit log intact."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


def test_reset_reverts_review_edits_and_keeps_the_audit_trail():
    from contract_radar import demo_reset
    from contract_radar.db import connect
    from contract_radar.permissions import app_connect, as_user
    from contract_radar.review import decide

    admin = connect()
    cid, name, value = admin.execute("SELECT contract_id, name, value FROM fields WHERE status = 'needs_review' "
                                     "AND present ORDER BY contract_id, name LIMIT 1").fetchone()
    demo_reset.setup(admin)
    audit_before = admin.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    app = app_connect()
    with as_user(app, "legal_admin"):
        assert decide(app, cid, name, "edit", "a visitor's edit")
    assert admin.execute("SELECT value, status FROM fields WHERE contract_id = %s AND name = %s",
                         (cid, name)).fetchone() == ("a visitor's edit", "reviewed")
    assert admin.execute("SELECT demo_reset_reviews()").fetchone()[0] >= 1
    assert admin.execute("SELECT value, status FROM fields WHERE contract_id = %s AND name = %s",
                         (cid, name)).fetchone() == (value, "needs_review")
    actions = [r[0] for r in admin.execute("SELECT payload->>'action' FROM audit_log WHERE id > "
                                           "(SELECT max(id) - 2 FROM audit_log) ORDER BY id")]
    assert actions[-1] == "demo_reset"
    assert admin.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audit_before + 2  # edit + reset, none removed
    assert admin.execute("SELECT audit_verify()").fetchone()[0] is None
