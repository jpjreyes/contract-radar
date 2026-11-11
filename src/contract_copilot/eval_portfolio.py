"""Portfolio question eval (M5): 40 "which contracts ...?" questions with known answers.

Each question is written with its intended filter (the gold plan), and its gold answer is that filter applied to the
lawyers' labels (CUAD master_clauses.csv), over all 510 contracts (asked as legal_admin). Three numbers separate
the two ways it can fail:
  planner      the model's plan, applied to the gold labels, gives exactly the gold set (did it understand?)
  end to end   the model's plan run as SQL on the EXTRACTED fields vs. the gold set: set precision / recall / F1
  oracle plan  the gold plan run on the extracted fields: error left when planning is perfect (= extraction error)
Optional baseline (--rag-every N, a fixed sample): plain RAG over the whole portfolio, contracts it cites vs. the gold set.

Run:  uv run python -m contract_copilot.eval_portfolio [--rag-every 4]
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime

from contract_copilot import cuad
from contract_copilot.db import connect
from contract_copilot.eval_extract import gold_answer, gold_date, gold_days, gold_present, gold_rows
from contract_copilot.eval_retrieval import RUNS
from contract_copilot.extract import FIELDS, jurisdictions
from contract_copilot.permissions import app_connect, as_user
from contract_copilot.portfolio import TYPE_FIELD, Condition, plan, run_plan

C = Condition
QUESTIONS: list[tuple[str, list[Condition]]] = [
    ("Which contracts are governed by Delaware law?", [C("governing_law", "contains", "Delaware")]),
    ("Which agreements are under New York law?", [C("governing_law", "contains", "New York")]),
    ("List the contracts governed by California law.", [C("governing_law", "contains", "California")]),
    ("Which contracts use Texas as the governing law?", [C("governing_law", "contains", "Texas")]),
    ("Which contracts are governed by the laws of Nevada?", [C("governing_law", "contains", "Nevada")]),
    ("Which agreements fall under Florida law?", [C("governing_law", "contains", "Florida")]),
    ("Which contracts have an exclusivity clause?", [C("exclusivity", "present")]),
    ("Which contracts include a non-compete?", [C("non_compete", "present")]),
    ("Which contracts can be terminated for convenience?", [C("termination_for_convenience", "present")]),
    ("Where do we have a minimum purchase commitment?", [C("minimum_commitment", "present")]),
    ("Which contracts have uncapped liability?", [C("uncapped_liability", "present")]),
    ("Which contracts cap or limit liability?", [C("cap_on_liability", "present")]),
    ("Which contracts renew automatically or can be extended?", [C("renewal_term", "present")]),
    ("Which contracts have no cap on liability at all?", [C("cap_on_liability", "absent")]),
    ("Which contracts have no exclusivity?", [C("exclusivity", "absent")]),
    ("Which contracts need 30 days' notice or less to stop renewal?",
     [C("notice_to_terminate_renewal_days", "lte", "30")]),
    ("Which contracts let us stop renewal with at most 60 days' notice?",
     [C("notice_to_terminate_renewal_days", "lte", "60")]),
    ("Which contracts require at least 90 days' notice to prevent renewal?",
     [C("notice_to_terminate_renewal_days", "gte", "90")]),
    ("Which contracts expire before 2010?", [C("expiration_date", "before", "2010-01-01")]),
    ("Which contracts expire in 2020 or later?", [C("expiration_date", "after", "2020-01-01")]),
    ("Which contracts expire during 2015?",
     [C("expiration_date", "after", "2015-01-01"), C("expiration_date", "before", "2016-01-01")]),
    ("Which contracts were signed before 2005?", [C("agreement_date", "before", "2005-01-01")]),
    ("Which agreements are dated 2019 or later?", [C("agreement_date", "after", "2019-01-01")]),
    ("Which distributor agreements have exclusivity?",
     [C(TYPE_FIELD, "contains", "Distributor"), C("exclusivity", "present")]),
    ("Which license agreements have uncapped liability?",
     [C(TYPE_FIELD, "contains", "License"), C("uncapped_liability", "present")]),
    ("Which supply agreements include a minimum commitment?",
     [C(TYPE_FIELD, "contains", "Supply"), C("minimum_commitment", "present")]),
    ("Which franchise agreements have a non-compete?",
     [C(TYPE_FIELD, "contains", "Franchise"), C("non_compete", "present")]),
    ("Which Delaware-law contracts have uncapped liability?",
     [C("governing_law", "contains", "Delaware"), C("uncapped_liability", "present")]),
    ("Which New York law contracts can be terminated for convenience?",
     [C("governing_law", "contains", "New York"), C("termination_for_convenience", "present")]),
    ("Which auto-renewing contracts are governed by California law?",
     [C("renewal_term", "present"), C("governing_law", "contains", "California")]),
    ("Which contracts have both exclusivity and a non-compete?",
     [C("exclusivity", "present"), C("non_compete", "present")]),
    ("Which contracts have exclusivity but no liability cap?",
     [C("exclusivity", "present"), C("cap_on_liability", "absent")]),
    ("Which contracts auto-renew and need 60 days or less notice to stop it?",
     [C("renewal_term", "present"), C("notice_to_terminate_renewal_days", "lte", "60")]),
    ("Which contracts with a minimum commitment can be terminated for convenience?",
     [C("minimum_commitment", "present"), C("termination_for_convenience", "present")]),
    ("Which contracts have uncapped liability and a liability cap?",
     [C("uncapped_liability", "present"), C("cap_on_liability", "present")]),
    ("Which reseller agreements renew automatically?",
     [C(TYPE_FIELD, "contains", "Reseller"), C("renewal_term", "present")]),
    ("Which service agreements cap liability?",
     [C(TYPE_FIELD, "contains", "Service"), C("cap_on_liability", "present")]),
    ("Which joint venture agreements have a non-compete?",
     [C(TYPE_FIELD, "contains", "Joint Venture"), C("non_compete", "present")]),
    ("Which Delaware-law contracts expire in 2020 or later?",
     [C("governing_law", "contains", "Delaware"), C("expiration_date", "after", "2020-01-01")]),
    ("Which contracts without exclusivity can be terminated for convenience?",
     [C("exclusivity", "absent"), C("termination_for_convenience", "present")]),
]


def gold_table() -> dict[int, dict]:
    """contract id -> {field: label answer, contract_type}, from the lawyers' labels."""
    admin = connect()
    rows = gold_rows()
    out = {}
    for cid, name, ctype in admin.execute("SELECT id, name, contract_type FROM contracts"):
        g = rows.get(cuad._norm(name))
        if g is not None:
            out[cid] = {TYPE_FIELD: ctype, **{f: gold_answer(g, cat) for f, (cat, _) in FIELDS.items()}}
    return out


def holds(c: Condition, rec: dict) -> bool:
    """A condition on the gold labels, with the same meaning as portfolio.compile_sql."""
    v = rec.get(c.field, "")
    if c.field == TYPE_FIELD:
        return c.value.lower() in v.lower()
    if c.op == "present":
        return gold_present(v)
    if c.op == "absent":
        return not gold_present(v)
    if not gold_present(v):
        return False
    if c.op == "contains":
        want = set(jurisdictions(c.value)) or {c.value.lower()}
        return bool(want & set(jurisdictions(v))) if c.field == "governing_law" else c.value.lower() in v.lower()
    if c.op in ("lte", "gte"):
        d = gold_days(v)
        n = int("".join(ch for ch in c.value if ch.isdigit()) or 0)
        return d is not None and (d <= n if c.op == "lte" else d >= n)
    if c.op in ("before", "after"):
        d = gold_date(v)
        try:
            ref = date.fromisoformat(c.value[:10])
        except ValueError:
            return False
        return d is not None and (d < ref if c.op == "before" else d >= ref)
    return False


def gold_set(conds: list[Condition], table: dict[int, dict]) -> set[int]:
    return {cid for cid, rec in table.items() if conds and all(holds(c, rec) for c in conds)}


def prf(got: set[int], gold: set[int]) -> dict:
    tp = len(got & gold)
    p = tp / len(got) if got else (1.0 if not gold else 0.0)
    r = tp / len(gold) if gold else 1.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "exact": got == gold}


def run(rag_every: int) -> dict:
    table = gold_table()
    app = app_connect()
    rows = []
    t0 = time.monotonic()
    for i, (q, gold_plan) in enumerate(QUESTIONS, 1):
        gold = gold_set(gold_plan, table)
        conds, unsupported = plan(q)
        with as_user(app, "legal_admin"):
            e2e = {m.contract_id for m in run_plan(app, conds)} if conds else set()
            oracle = {m.contract_id for m in run_plan(app, gold_plan)}
        row = {"question": q, "gold_n": len(gold), "plan": [c.__dict__ for c in conds], "unsupported": unsupported,
               "planner_ok": gold_set(conds, table) == gold, "e2e": prf(e2e, gold), "oracle_plan": prf(oracle, gold)}
        if rag_every and (i - 1) % rag_every == 0:  # the baseline is slow (~45 s each): a fixed sample
            row["rag"] = _rag(app, q, gold)
        rows.append(row)
        el = time.monotonic() - t0
        rag_note = f" rag F1={row['rag']['f1']:.2f}" if "rag" in row else ""
        print(f"  {i}/{len(QUESTIONS)} gold={len(gold):3} planner={'ok ' if row['planner_ok'] else 'BAD'} "
              f"e2e F1={row['e2e']['f1']:.2f} oracle F1={row['oracle_plan']['f1']:.2f}{rag_note}  "
              f"{q[:55]}  ({el:.0f}s, ~{el / i * (len(QUESTIONS) - i):.0f}s left)", flush=True)
    n = len(rows)

    def mean(key: str, sub: str) -> float:
        return round(sum(r[key][sub] for r in rows) / n, 3)

    summary = {"questions": n, "planner_accuracy": round(sum(r["planner_ok"] for r in rows) / n, 3),
               "e2e_f1": mean("e2e", "f1"), "e2e_precision": mean("e2e", "precision"),
               "e2e_recall": mean("e2e", "recall"), "e2e_exact": sum(r["e2e"]["exact"] for r in rows),
               "oracle_plan_f1": mean("oracle_plan", "f1"), "oracle_plan_exact": sum(r["oracle_plan"]["exact"]
                                                                                     for r in rows)}
    sampled = [r for r in rows if "rag" in r]
    if sampled:
        summary["rag_sample"] = len(sampled)
        summary["rag_f1_on_sample"] = round(sum(r["rag"]["f1"] for r in sampled) / len(sampled), 3)
        summary["e2e_f1_on_same_sample"] = round(sum(r["e2e"]["f1"] for r in sampled) / len(sampled), 3)
    print(json.dumps(summary, indent=1))
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"portfolio-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(f"-> {out.name}")
    return summary


def _rag(app, q: str, gold: set[int]) -> dict:
    """Baseline: ask the cited-answer pipeline across the whole portfolio; the contracts it cites are its answer."""
    from contract_copilot.answer import answer
    with as_user(app, "legal_admin"):
        a = answer(app, q)
    got = {h.contract_id for c in a.claims for ct in c.citations for h in a.sources if h.chunk_id == ct.chunk_id}
    return prf(got, gold)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rag-every", type=int, default=0,
                    help="also run the plain-RAG baseline on every Nth question (0 = off; 4 = 10 of 40)")
    run(ap.parse_args().rag_every)


if __name__ == "__main__":
    main()
