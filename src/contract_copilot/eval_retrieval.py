"""Retrieval eval against CUAD's lawyer labels.

For every labeled clause (a contract + a clause type with at least one gold span), the query is the clause type and
its description, scoped to that contract. A query is a hit at k if a top-k chunk overlaps any gold span; span recall
counts how many gold spans are covered. Split by contract (hash): dev 20% for iteration, test 80% for the numbers.

Run:  uv run python -m contract_copilot.eval_retrieval --split test --methods keyword vector hybrid hybrid+rerank
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from contract_copilot import cuad
from contract_copilot.db import connect
from contract_copilot.retrieve import search

KS = (1, 3, 5, 8)
RUNS = Path(__file__).resolve().parents[2] / "runs"


def split_of(name: str) -> str:
    return "dev" if int(hashlib.sha256(name.encode()).hexdigest(), 16) % 5 == 0 else "test"


def query_for(label: cuad.Label) -> str:
    details = re.sub(r"^.*Details:\s*", "", label.question, flags=re.DOTALL)
    return f"{label.category}: {details}"


def overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def run(split: str, methods: list[str], limit: int | None = None) -> dict:
    conn = connect()
    ids = dict(conn.execute("SELECT name, id FROM contracts").fetchall())
    cases = [(c, lab) for c in cuad.load() if split_of(c.name) == split and c.name in ids
             for lab in c.labels if lab.spans][:limit]
    report = {"split": split, "queries": len(cases), "contracts": len({c.name for c, _ in cases}), "methods": {}}
    for method in methods:
        t0 = time.monotonic()
        hits_at = {k: 0 for k in KS}
        span_cov = {k: 0 for k in KS}
        spans_total, rr = 0, 0.0
        per_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for c, lab in cases:
            hits = search(conn, query_for(lab), method=method, k=max(KS), contract_id=ids[c.name],
                          category=lab.category)
            first = next((r for r, h in enumerate(hits, 1)
                          if any(overlaps(h.start, h.end, s.start, s.end) for s in lab.spans)), None)
            rr += 1 / first if first else 0
            for k in KS:
                top = hits[:k]
                covered = sum(any(overlaps(h.start, h.end, s.start, s.end) for h in top) for s in lab.spans)
                span_cov[k] += covered
                hits_at[k] += covered > 0
            spans_total += len(lab.spans)
            per_cat[lab.category][0] += first is not None and first <= 5
            per_cat[lab.category][1] += 1
        n = len(cases)
        report["methods"][method] = {
            **{f"hit@{k}": round(hits_at[k] / n, 3) for k in KS},
            **{f"span_recall@{k}": round(span_cov[k] / spans_total, 3) for k in KS},
            "mrr": round(rr / n, 3),
            "seconds": round(time.monotonic() - t0, 1),
            "hit@5_by_category": {cat: round(h / t, 3) for cat, (h, t) in sorted(per_cat.items())},
        }
        m = report["methods"][method]
        print(f"{method:<15} hit@1 {m['hit@1']:.3f}  hit@5 {m['hit@5']:.3f}  hit@8 {m['hit@8']:.3f}  "
              f"span_recall@8 {m['span_recall@8']:.3f}  mrr {m['mrr']:.3f}  ({m['seconds']}s)", flush=True)
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"retrieval-{split}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"{n} queries over {report['contracts']} contracts -> {out.name}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--methods", nargs="+", default=["keyword", "vector", "hybrid", "hybrid+rerank"])
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    run(args.split, args.methods, args.limit)


if __name__ == "__main__":
    main()
