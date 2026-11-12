"""Answer eval against CUAD's lawyer labels (dev split; one question per contract and clause type).

  present  the lawyers labeled the clause: did we answer, and does a citation land on the labeled text?
  absent   the lawyers found no such clause: did we say we can't find it (correct refusal)?
Also: claims dropped because their quote couldn't be verified, latency, tokens and estimated cost.

Run:  uv run python -m contract_radar.eval_answers --present 30 --absent 20
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime

from contract_radar import cuad
from contract_radar.answer import answer
from contract_radar.db import connect
from contract_radar.eval_retrieval import RUNS, overlaps, split_of
from contract_radar.llm import get_backend

PRICE_IN, PRICE_OUT = 0.10, 0.50  # Claude Haiku 5.5 list price per million tokens (<= 100K-token prompts)

QUESTIONS = {
    "Renewal Term": "Does this agreement renew automatically? On what terms?",
    "Notice Period To Terminate Renewal": "How much notice is needed to stop this agreement from renewing?",
    "Expiration Date": "When does this agreement expire?",
    "Governing Law": "Which jurisdiction's law governs this agreement?",
    "Exclusivity": "Does this agreement grant any exclusivity? To whom and for what?",
    "Non-Compete": "Is there a non-compete restriction? What does it prohibit?",
    "Cap On Liability": "Is liability capped? What is the cap?",
    "Termination For Convenience": "Can a party terminate this agreement without cause? With how much notice?",
    "Minimum Commitment": "Is there a minimum purchase or volume commitment?",
    "Anti-Assignment": "Can this agreement be assigned without the other party's consent?",
    "Change Of Control": "What happens if one party undergoes a change of control?",
    "Audit Rights": "Does either party have the right to audit the other's records?",
    "Insurance": "Is either party required to carry insurance?",
    "Most Favored Nation": "Is there a most-favored-customer or best-price clause?",
    "Liquidated Damages": "Are there liquidated damages or penalties?",
}


def sample(n_present: int, n_absent: int, seed: int = 7):
    rng = random.Random(seed)
    present, absent = [], []
    for c in cuad.load():
        if split_of(c.name) != "dev":
            continue
        for lab in c.labels:
            if lab.category in QUESTIONS:
                (present if lab.spans else absent).append((c, lab))
    rng.shuffle(present)
    rng.shuffle(absent)
    return present[:n_present], absent[:n_absent]


def run(n_present: int, n_absent: int) -> dict:
    conn, backend = connect(), get_backend()
    ids = dict(conn.execute("SELECT name, id FROM contracts").fetchall())
    present, absent = sample(n_present, n_absent)
    rows = []
    for kind, cases in (("present", present), ("absent", absent)):
        for c, lab in cases:
            t0 = time.monotonic()
            try:
                a = answer(conn, QUESTIONS[lab.category], contract_id=ids[c.name], backend=backend,
                           category=lab.category)
            except Exception as e:  # noqa: BLE001 -- one failed call is a failed row, never a dead eval
                rows.append({"kind": kind, "category": lab.category, "contract": c.name, "error": repr(e)[:200]})
                continue
            cites = [ct for cl in a.claims for ct in cl.citations]
            on_gold = any(overlaps(ct.start, ct.end, s.start, s.end) for ct in cites for s in lab.spans)
            rows.append({"kind": kind, "category": lab.category, "contract": c.name, "answerable": a.answerable,
                         "clause_present": a.clause_present, "claims": len(a.claims), "dropped": a.dropped, "citation_on_gold": on_gold,
                         "tokens_in": a.tokens_in, "tokens_out": a.tokens_out,
                         "seconds": round(time.monotonic() - t0, 1)})
            r = rows[-1]
            print(f"{kind:<8} {lab.category:<36} answered={r['answerable']!s:<5} on_gold={on_gold!s:<5} "
                  f"claims={r['claims']} dropped={r['dropped']} {r['seconds']}s", flush=True)
    ok = [r for r in rows if "error" not in r]
    pres = [r for r in ok if r["kind"] == "present"]
    absn = [r for r in ok if r["kind"] == "absent"]
    answered = [r for r in pres if r["answerable"]]
    claims = sum(r["claims"] + r["dropped"] for r in ok)
    summary = {
        "questions": len(rows), "errors": len(rows) - len(ok),
        "answered_when_present": f"{len(answered)}/{len(pres)}",
        "citation_on_lawyer_span": f"{sum(r['citation_on_gold'] for r in answered)}/{len(answered)}",
        "correct_refusal_when_absent": f"{sum(not r['answerable'] for r in absn)}/{len(absn)}",
        "says_absent_when_absent": f"{sum(not r['clause_present'] for r in absn)}/{len(absn)}",
        "claims_dropped_unverified": f"{sum(r['dropped'] for r in ok)}/{claims}",
        "median_seconds": sorted(r["seconds"] for r in ok)[len(ok) // 2] if ok else None,
        "usd_per_question": round(sum(r["tokens_in"] * PRICE_IN + r["tokens_out"] * PRICE_OUT for r in ok)
                                  / 1e6 / max(len(ok), 1), 5),
    }
    out = RUNS / f"answers-dev-{datetime.now():%Y%m%d-%H%M%S}.json"
    RUNS.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1), "->", out.name)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--present", type=int, default=30)
    ap.add_argument("--absent", type=int, default=20)
    args = ap.parse_args()
    run(args.present, args.absent)


if __name__ == "__main__":
    main()
