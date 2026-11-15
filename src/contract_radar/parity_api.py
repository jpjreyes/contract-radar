"""API parity check: the deployed app (Claude API) against the final answer eval (Claude via the CLI on a plan).

Five tasks, chosen by a fixed rule before running: from the final dev answer eval (evals/results/answers-dev.json),
the first 3 "present" rows with distinct clause types and the first 2 "absent" rows. Each question goes through the
deployed API as legal_admin (all contracts), so it takes the real path: sign-in header → row-level security →
masking → retrieval → model → quote check. Per task we compare what the eval scored: answered, clause present, and
(when present) whether a citation lands on the lawyers' span.

Run:  uv run python -m contract_radar.parity_api --api https://<api-url>
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from contract_radar import cuad
from contract_radar.db import connect
from contract_radar.eval_answers import QUESTIONS
from contract_radar.eval_retrieval import RUNS, overlaps

BASELINE = Path(__file__).resolve().parents[2] / "evals" / "results" / "answers-dev.json"


def pick(rows: list[dict]) -> list[dict]:
    present, seen, absent = [], set(), []
    for r in rows:
        if r["kind"] == "present" and r["category"] not in seen and len(present) < 3:
            present.append(r)
            seen.add(r["category"])
        elif r["kind"] == "absent" and len(absent) < 2:
            absent.append(r)
    return present + absent


def ask(api: str, question: str, contract_id: int) -> tuple[dict, float]:
    req = urllib.request.Request(f"{api}/ask", data=json.dumps({"question": question, "contract_id": contract_id}).encode(),
                                 headers={"content-type": "application/json", "X-User": "legal_admin"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=180) as f:
        return json.load(f), round(time.monotonic() - t0, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    args = ap.parse_args()

    tasks = pick(json.loads(BASELINE.read_text())["rows"])
    ids = dict(connect().execute("SELECT name, id FROM contracts").fetchall())
    labels = {(c.name, lab.category): lab for c in cuad.load() for lab in c.labels}
    out = []
    for i, t in enumerate(tasks, 1):
        lab = labels[(t["contract"], t["category"])]
        try:
            a, secs = ask(args.api, QUESTIONS[t["category"]], ids[t["contract"]])
        except urllib.error.HTTPError as e:
            out.append({**{k: t[k] for k in ("kind", "category", "contract")}, "error": f"HTTP {e.code}"})
            print(f"{i}/{len(tasks)} {t['category']}: HTTP {e.code}", flush=True)
            continue
        cites = [c for cl in a["claims"] for c in cl["citations"]]
        on_gold = any(overlaps(c["start"], c["end"], s.start, s.end) for c in cites for s in lab.spans)
        api_row = {"answerable": a["answerable"], "clause_present": a["clause_present"],
                   "citation_on_gold": on_gold if lab.spans else None}
        plan_row = {"answerable": t["answerable"], "clause_present": t["clause_present"],
                    "citation_on_gold": t["citation_on_gold"] if lab.spans else None}
        same = api_row == plan_row
        out.append({"kind": t["kind"], "category": t["category"], "contract": t["contract"], "api": api_row,
                    "plan": plan_row, "same": same, "api_seconds": secs, "plan_seconds": t["seconds"],
                    "claims": len(a["claims"])})
        print(f"{i}/{len(tasks)} {t['kind']:<8} {t['category']:<28} api={api_row} plan={plan_row} "
              f"{'SAME' if same else 'DIFF'} {secs}s", flush=True)
    ok = [r for r in out if "error" not in r]
    secs = sorted(r["api_seconds"] for r in ok)
    summary = {"tasks": len(out), "identical": f"{sum(r['same'] for r in ok)}/{len(out)}",
               "errors": len(out) - len(ok), "api_median_seconds": secs[len(secs) // 2] if secs else None,
               "plan_median_seconds_same_tasks": sorted(r["plan_seconds"] for r in ok)[len(ok) // 2] if ok else None}
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"parity-api-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"summary": summary, "rows": out}, indent=1))
    print(json.dumps(summary, indent=1), "->", path.name)


if __name__ == "__main__":
    main()
