"""Permission leak test (C2): the number that must always be 0.

For every non-admin demo user, pick contracts they may NOT read and search for sentences copied verbatim from them
(the strongest retrieval attack: exact text), with every method, globally and scoped to the forbidden contract id.
A leak is any returned chunk from a contract outside the user's grant. As a control, the same attack on contracts
the user MAY read must still find them (so 0 leaks isn't just "returns nothing").

Run:  uv run python -m contract_copilot.eval_permissions --per-user 25
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime

from contract_copilot.db import connect
from contract_copilot.eval_retrieval import RUNS
from contract_copilot.permissions import USERS, app_connect, as_user
from contract_copilot.retrieve import search

METHODS = ("keyword", "vector", "hybrid", "planned")


def probes(admin, contract_ids: list[int], rng: random.Random, n: int) -> list[tuple[int, str]]:
    out = []
    for cid in rng.sample(contract_ids, min(n, len(contract_ids))):
        rows = admin.execute("SELECT text FROM chunks WHERE contract_id = %s AND length(text) > 400 "
                             "ORDER BY idx LIMIT 5", (cid,)).fetchall()
        if rows:
            text = rng.choice(rows)[0]
            s = rng.randrange(0, max(1, len(text) - 300))
            out.append((cid, text[s:s + 300]))
    return out


def run(per_user: int, seed: int = 11) -> dict:
    admin, app = connect(), app_connect()
    rng = random.Random(seed)
    all_ids = [r[0] for r in admin.execute("SELECT id FROM contracts").fetchall()]
    report = {"users": {}, "total_leaks": 0, "total_returned_chunks": 0}
    for name, _, all_access, *_ in USERS:
        if all_access:
            continue
        with as_user(app, name):
            allowed = {r[0] for r in app.execute("SELECT id FROM contracts").fetchall()}
        forbidden = [i for i in all_ids if i not in allowed]
        leaks, returned, control_found, control_total = 0, 0, 0, 0
        for cid, text in probes(admin, forbidden, rng, per_user):
            with as_user(app, name):
                for method in METHODS:
                    for scope in (None, cid):
                        hits = search(app, text, method=method, k=8, contract_id=scope)
                        returned += len(hits)
                        leaks += sum(h.contract_id not in allowed for h in hits)
        for cid, text in probes(admin, sorted(allowed), rng, min(per_user, 10)):
            with as_user(app, name):
                hits = search(app, text, method="hybrid", k=8, contract_id=None)
            control_total += 1
            control_found += any(h.contract_id == cid for h in hits)
        report["users"][name] = {"allowed_contracts": len(allowed), "forbidden_probes": per_user * len(METHODS) * 2,
                                 "chunks_returned": returned, "leaks": leaks,
                                 "control_found_own_contract": f"{control_found}/{control_total}"}
        report["total_leaks"] += leaks
        report["total_returned_chunks"] += returned
        print(name, report["users"][name], flush=True)
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"permissions-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"TOTAL LEAKS: {report['total_leaks']} (of {report['total_returned_chunks']} chunks returned) -> {out.name}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-user", type=int, default=25)
    run(ap.parse_args().per_user)


if __name__ == "__main__":
    main()
