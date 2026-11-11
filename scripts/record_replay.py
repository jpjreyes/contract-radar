"""Record the public demo's answers (replay mode, D16): every example question, for every demo user.

Portfolio examples for each user, and the contract examples on two contracts each user can see (the first two
notice deadlines of their radar, so the demo opens on contracts that matter). Answers are recorded through the API
itself, so the replayed JSON is exactly what live mode returns, permissions and masking included.

Run:  uv run python scripts/record_replay.py        (live model calls; prints one line per question)
"""

import json
import os
import time

os.environ["CC_MODE"] = "live"
os.environ.setdefault("CC_MONTHLY_BUDGET_USD", "1000")
os.environ.setdefault("CC_RATE_PER_HOUR", "1000")

from fastapi.testclient import TestClient

from contract_copilot import limits
from contract_copilot.api import app

PORTFOLIO = [
    "Which contracts auto-renew and need 60 days or less notice to stop it?",
    "Which Delaware-law contracts have uncapped liability?",
    "Which distributor agreements have exclusivity?",
    "Which contracts have no cap on liability at all?",
]
CONTRACT = [
    "Who signed this agreement?",
    "Is there a cap on liability, and what is excluded from it?",
    "Can either party terminate for convenience?",
    "Where should notices be sent?",
]
USERS = ["legal_admin", "maria", "kenji", "analyst"]


def main() -> None:
    client = TestClient(app)
    plan = []
    index: dict[str, dict] = {}
    for user in USERS:
        radar = client.get("/radar", headers={"X-User": user}).json()
        picks = [i["contract_id"] for i in radar["sections"]["notice_deadlines"]][:2]
        if len(picks) < 2:
            picks += [c["id"] for c in client.get("/contracts", headers={"X-User": user}).json()
                      if c["id"] not in picks][: 2 - len(picks)]
        index[user] = {"portfolio": PORTFOLIO, "contract": CONTRACT, "contracts": picks}
        plan += [(user, "portfolio", q, None) for q in PORTFOLIO]
        plan += [(user, "ask", q, cid) for cid in picks for q in CONTRACT]
    t0 = time.monotonic()
    for i, (user, kind, q, cid) in enumerate(plan, 1):
        body = {"question": q} if kind == "portfolio" else {"question": q, "contract_id": cid}
        r = client.post(f"/{kind}", headers={"X-User": user}, json=body)
        if r.status_code == 200:
            limits.replay_put(user, kind, q, cid, r.json())
        el = time.monotonic() - t0
        print(f"  {i}/{len(plan)} {user:11} {kind:9} {r.status_code} {q[:50]:50} "
              f"({el:.0f}s, ~{el / i * (len(plan) - i):.0f}s left)", flush=True)
    (limits.REPLAY_DIR / "index.json").write_text(json.dumps(index, indent=1))
    print(f"recorded into {limits.REPLAY_DIR}")


if __name__ == "__main__":
    main()
