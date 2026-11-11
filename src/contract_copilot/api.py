"""HTTP API for the web app (M6). Every request runs as a user, on the app role, inside row-level security.

Identity: the demo picks a user with the `X-User` header (a user switcher in the UI). In a real deployment this
header is replaced by the identity provider's verified token (e.g. Cognito/OIDC); nothing below trusts the client
for anything else: permissions, masking and reviewer rights are all decided in Postgres.

Run:  uv run uvicorn contract_copilot.api:app --port 8071
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from contract_copilot import review as review_mod
from contract_copilot import tracing
from contract_copilot.ask import ask
from contract_copilot.permissions import USERS, app_connect, as_user
from contract_copilot.portfolio import ask_portfolio
from contract_copilot.radar import compute as radar_compute
from contract_copilot.retrieve import _hit
from contract_copilot.serve import serve

tracing.setup()
app = FastAPI(title="Contract Radar", version="0.1")
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
KNOWN = {u[0] for u in USERS}


def conn() -> Iterator[psycopg.Connection]:
    c = app_connect()
    try:
        yield c
    finally:
        c.close()


def user(x_user: str = Header(...)) -> str:
    if x_user not in KNOWN:
        raise HTTPException(401, "unknown user")
    return x_user


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/radar")
def radar(as_of: str | None = None, days: int = 90, who: str = Depends(user),
          db: psycopg.Connection = Depends(conn)) -> dict:
    """Act this quarter: notice deadlines, renewals, uncapped liability, expirations (RLS applies)."""
    try:
        when = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError as e:
        raise HTTPException(422, "as_of must be YYYY-MM-DD") from e
    with as_user(db, who):
        return radar_compute(db, when, max(1, min(days, 365)))


@app.get("/users")
def users() -> list[dict]:
    return [{"name": n, "description": d, "pii_access": pii, "can_review": n in review_mod.REVIEWERS}
            for n, d, _, _, _, pii in USERS]


@app.get("/contracts")
def contracts(who: str = Depends(user), db: psycopg.Connection = Depends(conn)) -> list[dict]:
    with as_user(db, who):
        rows = db.execute(
            "SELECT c.id, c.name, c.contract_type, c.region, c.business_unit, c.pii_counts, "
            "count(f.*) FILTER (WHERE f.status = 'needs_review') AS to_review "
            "FROM contracts c LEFT JOIN fields f ON f.contract_id = c.id GROUP BY c.id ORDER BY c.name").fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "region": r[3], "business_unit": r[4], "pii": r[5],
             "to_review": r[6]} for r in rows]


@app.get("/contracts/{cid}")
def contract(cid: int, who: str = Depends(user), db: psycopg.Connection = Depends(conn)) -> dict:
    with as_user(db, who):
        meta = db.execute("SELECT id, name, contract_type, region, business_unit FROM contracts WHERE id = %s",
                          (cid,)).fetchone()
        if meta is None:  # missing or not yours: same reply
            raise HTTPException(404, "not found")
        rows = db.execute("SELECT id, contract_id, start_char, end_char, text_masked, 0.0, mask_map FROM chunks "
                          "WHERE contract_id = %s ORDER BY idx", (cid,)).fetchall()
        chunks = serve(db, [_hit(r) for r in rows])
        fields = db.execute("SELECT name, present, value, quote, start_char, end_char, confidence, issues, status, "
                            "reviewed_by FROM fields WHERE contract_id = %s ORDER BY name", (cid,)).fetchall()
    return {"id": meta[0], "name": meta[1], "type": meta[2], "region": meta[3], "business_unit": meta[4],
            "chunks": [{"id": h.chunk_id, "start": h.start, "end": h.end, "text": h.text, "masked": bool(h.masks),
                        "masks": [list(m) for m in h.masks]} for h in chunks],
            "fields": [dict(zip(("name", "present", "value", "quote", "start", "end", "confidence", "issues",
                                 "status", "reviewed_by"), f)) for f in fields]}


class AskIn(BaseModel):
    question: str
    contract_id: int | None = None


@app.post("/ask")
def ask_endpoint(body: AskIn, who: str = Depends(user), db: psycopg.Connection = Depends(conn)) -> dict:
    a, audit_id = ask(who, body.question, contract_id=body.contract_id, conn=db)
    owner = {h.chunk_id: h.contract_id for h in a.sources}
    return {"answerable": a.answerable, "clause_present": a.clause_present, "audit_id": audit_id,
            "seconds": a.seconds, "pii_revealed_chunks": a.revealed,
            "claims": [{"text": c.text, "citations": [{**ct.__dict__, "contract_id": owner.get(ct.chunk_id)}
                                                      for ct in c.citations]} for c in a.claims]}


class PortfolioIn(BaseModel):
    question: str


@app.post("/portfolio")
def portfolio_endpoint(body: PortfolioIn, who: str = Depends(user), db: psycopg.Connection = Depends(conn)) -> dict:
    ans, audit_id = ask_portfolio(who, body.question, conn=db)
    return {"conditions": [c.__dict__ for c in ans.conditions], "unsupported": ans.unsupported,
            "unreviewed": ans.unreviewed, "audit_id": audit_id,
            "matches": [{"contract_id": m.contract_id, "contract": m.contract, "evidence": m.evidence}
                        for m in ans.matches]}


@app.get("/review")
def review_queue(limit: int = 50, who: str = Depends(user), db: psycopg.Connection = Depends(conn)) -> list[dict]:
    with as_user(db, who):
        rows = review_mod.queue(db, limit)
    return [dict(zip(("contract_id", "contract", "field", "present", "value", "confidence", "issues", "quote",
                      "start", "end"), r)) for r in rows]


class ReviewIn(BaseModel):
    contract_id: int
    field: str
    decision: str
    value: str | None = None


@app.post("/review")
def review_decide(body: ReviewIn, who: str = Depends(user), db: psycopg.Connection = Depends(conn)) -> dict:
    with as_user(db, who):
        ok = review_mod.decide(db, body.contract_id, body.field, body.decision, body.value)
    if not ok:
        raise HTTPException(403, "not allowed: not a reviewer, or not your contract")
    return {"saved": True}
