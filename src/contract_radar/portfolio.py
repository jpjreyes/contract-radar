"""Portfolio questions (M5): "which contracts ...?" is a filter over extracted fields, not a retrieval problem.

Search returns the top-k passages; a question like "which contracts auto-renew with less than 60 days' notice?" needs
EVERY matching contract out of 510. So the model only plans: it turns the question into structured conditions on the
extracted fields (D11). Code compiles them to SQL, which runs as the user (row-level security decides which contracts
exist for them), and each match comes back with the quote behind every condition and whether a person reviewed it.

Run:  uv run python -m contract_radar.portfolio --user legal_admin "Which contracts under Delaware law have exclusivity?"
"""

from __future__ import annotations

import argparse
import hashlib
import time
from dataclasses import dataclass, field

import psycopg

from contract_radar import audit
from contract_radar.extract import FIELDS
from contract_radar.llm import Backend, get_backend
from contract_radar.permissions import app_connect, as_user
from contract_radar.tracing import content_allowed, span

OPS = {
    "present": "the contract has this term",
    "absent": "the contract does not have this term",
    "contains": "the value mentions the given text (e.g. governing_law contains 'Delaware')",
    "lte": "numeric value <= the given number (notice days)",
    "gte": "numeric value >= the given number",
    "before": "date value earlier than the given YYYY-MM-DD",
    "after": "date value later than or equal to the given YYYY-MM-DD",
}
TYPE_FIELD = "contract_type"

SYSTEM = f"""You turn a question about a portfolio of commercial contracts into filter conditions over extracted
fields. All conditions are combined with AND. Fields: {", ".join(FIELDS)}, plus {TYPE_FIELD} (the kind of agreement,
e.g. 'Distributor', 'License', 'Supply', 'Franchise'; use op "contains"). Operators: {"; ".join(f"{k}: {v}" for k, v in OPS.items())}.
Notes: "auto-renews"/"renews" = renewal_term present. "notice to stop renewal"/"non-renewal notice" =
notice_to_terminate_renewal_days. "expires in YEAR" = expiration_date after YEAR-01-01 AND before (YEAR+1)-01-01.
"uncapped liability" = uncapped_liability present; "liability is capped/limited" = cap_on_liability present.
If the question can't be expressed with these fields, return no conditions and say why in `unsupported`."""

PLAN_SCHEMA = {"type": "object", "properties": {
    "conditions": {"type": "array", "items": {"type": "object", "properties": {
        "field": {"type": "string", "enum": [*FIELDS, TYPE_FIELD]},
        "op": {"type": "string", "enum": list(OPS)},
        "value": {"type": "string"}}, "required": ["field", "op", "value"]}},
    "unsupported": {"type": "string"}}, "required": ["conditions", "unsupported"]}


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: str = ""


@dataclass
class Match:
    contract_id: int
    contract: str
    evidence: dict[str, dict]   # field -> {value, quote, start, end, status}


@dataclass
class PortfolioAnswer:
    question: str
    conditions: list[Condition]
    matches: list[Match] = field(default_factory=list)
    unsupported: str = ""
    unreviewed: int = 0        # matches resting on at least one value nobody has reviewed yet
    tokens_in: int = 0
    tokens_out: int = 0


def plan(question: str, backend: Backend | None = None) -> tuple[list[Condition], str]:
    conds, unsupported, _, _ = plan_with_usage(question, backend)
    return conds, unsupported


def plan_with_usage(question: str, backend: Backend | None = None) -> tuple[list[Condition], str, int, int]:
    with span("plan", "CHAIN"):
        res = (backend or get_backend()).complete(SYSTEM, question, PLAN_SCHEMA)
    conds = [Condition(c["field"], c["op"], str(c.get("value", "")).strip()) for c in res.data.get("conditions", [])
             if c.get("field") in (*FIELDS, TYPE_FIELD) and c.get("op") in OPS]
    return conds, str(res.data.get("unsupported", "")), res.input_tokens, res.output_tokens


def compile_sql(conds: list[Condition]) -> tuple[str, list]:
    """Conditions -> one SQL query over contracts and fields. Values are always bound parameters."""
    where, args = [], []
    for c in conds:
        if c.field == TYPE_FIELD:
            where.append("c.contract_type ILIKE %s")
            args.append(f"%{c.value}%")
            continue
        sub = "SELECT 1 FROM fields f WHERE f.contract_id = c.id AND f.name = %s AND f.present"
        if c.op == "absent":
            where.append(f"NOT EXISTS ({sub})")
            args.append(c.field)
            continue
        extra, extra_args = {
            "present": ("", []),
            "contains": (" AND f.value ILIKE %s", [f"%{c.value}%"]),
            "lte": (r" AND f.value ~ '^\d+$' AND f.value::int <= %s", [_int(c.value)]),
            "gte": (r" AND f.value ~ '^\d+$' AND f.value::int >= %s", [_int(c.value)]),
            "before": (r" AND f.value ~ '^\d{4}-\d{2}-\d{2}$' AND f.value::date < %s::date", [c.value[:10]]),
            "after": (r" AND f.value ~ '^\d{4}-\d{2}-\d{2}$' AND f.value::date >= %s::date", [c.value[:10]]),
        }[c.op]
        where.append(f"EXISTS ({sub}{extra})")
        args += [c.field, *extra_args]
    sql = "SELECT c.id, c.name FROM contracts c" + (" WHERE " + " AND ".join(where) if where else " WHERE false")
    return sql + " ORDER BY c.name", args


def _int(v: str) -> int:
    digits = "".join(ch for ch in v if ch.isdigit())
    return int(digits) if digits else 0


def run_plan(conn: psycopg.Connection, conds: list[Condition]) -> list[Match]:
    sql, args = compile_sql(conds)
    with span("sql", "TOOL", **{"db.system": "postgresql", "cc.conditions": len(conds)}) as s:
        rows = conn.execute(sql, args).fetchall()
        s.set_attribute("cc.rows", len(rows))
    names = [c.field for c in conds if c.field != TYPE_FIELD and c.op != "absent"]
    ev: dict[int, dict] = {r[0]: {} for r in rows}
    if rows and names:
        for cid, name, value, quote, s, e, status in conn.execute(
                "SELECT contract_id, name, value, quote, start_char, end_char, status FROM fields "
                "WHERE contract_id = ANY(%s) AND name = ANY(%s)", ([r[0] for r in rows], names)):
            ev[cid][name] = {"value": value, "quote": quote, "start": s, "end": e, "status": status}
    return [Match(cid, name, ev[cid]) for cid, name in rows]


def ask_portfolio(user: str, question: str, backend: Backend | None = None,
                  conn: psycopg.Connection | None = None) -> tuple[PortfolioAnswer, int]:
    conn = conn or app_connect()
    t0 = time.monotonic()
    with span("portfolio", "CHAIN", **{"enduser.id": user, "cc.question_chars": len(question),
                                       "input.value": question if content_allowed() else None}) as root:
        return _ask_portfolio(conn, user, question, backend, t0, root)


def _ask_portfolio(conn, user, question, backend, t0, root) -> tuple[PortfolioAnswer, int]:
    conds, unsupported, tin, tout = plan_with_usage(question, backend)
    root.set_attribute("cc.plan", [f"{c.field} {c.op} {c.value}".strip() for c in conds])
    with as_user(conn, user):
        matches = run_plan(conn, conds) if conds else []
        ans = PortfolioAnswer(question, conds, matches, unsupported,
                              sum(any(v["status"] == "needs_review" for v in m.evidence.values()) for m in matches),
                              tin, tout)
        audit_id = audit.append(conn, {
            "action": "portfolio", "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "conditions": [c.__dict__ for c in conds], "matched_contracts": [m.contract_id for m in matches],
            "unreviewed_matches": ans.unreviewed, "seconds": round(time.monotonic() - t0, 1)})
    root.set_attribute("cc.matches", len(ans.matches))
    root.set_attribute("cc.audit_id", audit_id)
    return ans, audit_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--user", required=True)
    a = ap.parse_args()
    ans, audit_id = ask_portfolio(a.user, a.question)
    print("plan:", " AND ".join(f"{c.field} {c.op} {c.value}".strip() for c in ans.conditions) or ans.unsupported)
    print(f"{len(ans.matches)} contracts ({ans.unreviewed} rest on values not yet reviewed):")
    for m in ans.matches[:25]:
        bits = "; ".join(f"{k}={v['value'][:40]!r}{'' if v['status'] != 'needs_review' else ' (unreviewed)'}"
                         for k, v in m.evidence.items())
        print(f"  #{m.contract_id} {m.contract[:60]}  {bits}")
    print(f"(audit #{audit_id})")


if __name__ == "__main__":
    main()
