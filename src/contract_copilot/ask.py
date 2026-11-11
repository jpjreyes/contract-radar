"""The front door: answer a question AS a user, and record it in the audit log.

Everything runs on the app role inside one transaction with `app.user` set, so row-level security (D4, D8) and
personal-data masking (D9) apply to retrieval, to what the model sees and to what comes back. The audit row is written
in the same transaction, by the database, with the user and time stamped there.

Run:  uv run python -m contract_copilot.ask --user analyst --contract "Development Agreement" "Who signed it?"
"""

from __future__ import annotations

import argparse
import hashlib
import time

import psycopg

from contract_copilot import audit
from contract_copilot.answer import Answer, answer
from contract_copilot.llm import Backend
from contract_copilot.permissions import app_connect, as_user
from contract_copilot.tracing import content_allowed, span


def ask(user: str, question: str, contract_id: int | None = None, category: str | None = None,
        backend: Backend | None = None, conn: psycopg.Connection | None = None) -> tuple[Answer, int]:
    conn = conn or app_connect()
    t0 = time.monotonic()
    with span("ask", "CHAIN", **{"enduser.id": user, "cc.contract_id": contract_id, "cc.question_chars": len(question),
                                 "input.value": question if content_allowed() else None}) as root, \
            as_user(conn, user):
        a = answer(conn, question, contract_id=contract_id, backend=backend, category=category)
        root.set_attribute("cc.answerable", a.answerable)
        root.set_attribute("cc.claims", len(a.claims))
        root.set_attribute("cc.pii_revealed_chunks", len(a.revealed))
        audit_id = _audit(conn, {
            "action": "ask",
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "question_chars": len(question),
            "contract_id": contract_id,
            "retrieved_chunks": [h.chunk_id for h in a.sources],
            "cited_chunks": sorted({c.chunk_id for cl in a.claims for c in cl.citations}),
            "answerable": a.answerable,
            "claims": len(a.claims),
            "claims_dropped_unverified": a.dropped,
            "pii_revealed_chunks": a.revealed,
            "tokens_in": a.tokens_in,
            "tokens_out": a.tokens_out,
            "seconds": round(time.monotonic() - t0, 1),
        })
        root.set_attribute("cc.audit_id", audit_id)
    return a, audit_id


def _audit(conn: psycopg.Connection, payload: dict) -> int:
    with span("audit", "TOOL"):
        return audit.append(conn, payload)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--user", required=True)
    ap.add_argument("--contract", help="part of a contract name (must be one the user can read)")
    a = ap.parse_args()
    conn = app_connect()
    cid = None
    if a.contract:
        with as_user(conn, a.user):
            rows = conn.execute("SELECT id, name FROM contracts WHERE name ILIKE %s ORDER BY id LIMIT 2",
                                (f"%{a.contract}%",)).fetchall()
        if not rows:  # doesn't exist, or exists and this user may not read it: same answer either way
            raise SystemExit(f"No contract you can see matches {a.contract!r}.")
        if len(rows) > 1:
            raise SystemExit(f"Several contracts match {a.contract!r}: be more specific.")
        cid = rows[0][0]
        print(f"contract: {rows[0][1]}")
    ans, audit_id = ask(a.user, a.question, contract_id=cid, conn=conn)
    if not ans.answerable:
        print("I can't find that in the contracts you can see.")
    for c in ans.claims:
        print(f"- {c.text}")
        for ct in c.citations:
            print(f"    [{ct.contract[:50]} @{ct.start}-{ct.end}] \"{ct.quote[:140]}\"")
    print(f"(audit #{audit_id}; personal data revealed in {len(ans.revealed)} chunk(s))")


if __name__ == "__main__":
    main()
