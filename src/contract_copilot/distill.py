"""M7: a small local model for clause presence, trained from labels, compared head to head with the LLM.

Question per contract and field: does this contract contain it (exclusivity, cap on liability, ...)? Today the LLM
answers it for 13 fields at ~$0.0015 and ~15 s per contract, and the contract text leaves the server. Here a small
encoder (bge-small, 33M parameters, fine-tuned for 13-label classification) reads every chunk (masked text, D9);
contract presence = the highest chunk probability ≥ a per-field threshold.

Two label sources, same model and data:
  gold  chunk overlaps a CUAD lawyer span of that category (what a client with labeled data has)
  llm   chunk holds the quote the LLM extraction cited for that field (true distillation: no human labels needed)

Splits (contract level, fixed by hash): train = half of the retrieval "test" contracts; thresholds = the 95 dev
contracts; **eval = the other half of test**, never seen by the model or the thresholds. The LLM extraction's own
presence numbers are recomputed on the same eval contracts, so the comparison is head to head.

Cascade: on the eval set, the small model decides when it's confident (probability below `low` or above `high`,
chosen on dev so precision stays at the LLM's level); otherwise the LLM's answer is used. Reported: per-field F1 for
small model, LLM and cascade; share of LLM calls avoided; cost and latency per contract.

Run:  uv run python -m contract_copilot.distill --labels gold --epochs 3     (prints progress every 50 steps)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch

from contract_copilot import cuad
from contract_copilot.db import connect
from contract_copilot.embed import EMBED_MODEL, _device
from contract_copilot.eval_extract import gold_answer, gold_present, gold_rows
from contract_copilot.eval_retrieval import RUNS, split_of
from contract_copilot.extract import FIELDS

NAMES = list(FIELDS)
CATS = [FIELDS[n][0] for n in NAMES]


def m7_split(name: str) -> str:
    if split_of(name) == "dev":
        return "dev"
    return "train" if int(hashlib.sha256(f"m7-{name}".encode()).hexdigest(), 16) % 2 == 0 else "eval"


def load_chunks() -> tuple[list[dict], dict[int, dict]]:
    """Every chunk with its contract, masked text and two label vectors (gold and llm)."""
    admin = connect()
    contracts = {r[0]: {"id": r[0], "name": r[1]} for r in admin.execute("SELECT id, name FROM contracts")}
    by_name = {c["name"]: c for c in contracts.values()}
    spans: dict[int, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for c in cuad.load():
        if c.name in by_name:
            for lab in c.labels:
                if lab.category in CATS:
                    spans[by_name[c.name]["id"]][lab.category] += [(s.start, s.end) for s in lab.spans]
    llm_chunk = defaultdict(set)  # (contract, field) -> chunk ids the LLM quoted
    for cid, name, chunk_id in admin.execute("SELECT contract_id, name, chunk_id FROM fields WHERE present "
                                             "AND chunk_id IS NOT NULL"):
        llm_chunk[chunk_id].add(name)
    rows = []
    for kid, cid, s, e, text in admin.execute("SELECT id, contract_id, start_char, end_char, text_masked FROM chunks "
                                              "ORDER BY contract_id, idx"):
        gold = [int(any(a < e and s < b for a, b in spans[cid].get(cat, []))) for cat in CATS]
        llm = [int(n in llm_chunk.get(kid, ())) for n in NAMES]
        rows.append({"chunk": kid, "contract": cid, "text": text, "gold": gold, "llm": llm,
                     "split": m7_split(contracts[cid]["name"])})
    return rows, contracts


def train(rows: list[dict], labels: str, epochs: int, batch: int = 16, lr: float = 5e-5):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(EMBED_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        EMBED_MODEL, num_labels=len(NAMES), problem_type="multi_label_classification")
    dev = _device()
    model.to(dev)
    data = [r for r in rows if r["split"] == "train"]
    y = torch.tensor([r[labels] for r in data], dtype=torch.float)
    pos = y.sum(0).clamp(min=1)
    pos_weight = ((len(data) - pos) / pos).clamp(max=50).to(dev)  # positives are rare: weight them up
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    steps, t0 = epochs * ((len(data) + batch - 1) // batch), time.monotonic()
    step = 0
    rng = np.random.default_rng(0)
    model.train()
    for _ in range(epochs):
        for idx in np.array_split(rng.permutation(len(data)), max(1, len(data) // batch)):
            enc = tok([data[i]["text"] for i in idx], truncation=True, max_length=256, padding=True,
                      return_tensors="pt").to(dev)
            loss = loss_fn(model(**enc).logits, y[idx].to(dev))
            opt.zero_grad()
            loss.backward()
            opt.step()
            step += 1
            if step % 50 == 0 or step == steps:
                el = time.monotonic() - t0
                print(f"  step {step}/{steps}  loss {loss.item():.3f}  {el:.0f}s, ~{el / step * (steps - step):.0f}s left",
                      flush=True)
    model.eval()
    return tok, model


@torch.no_grad()
def predict(tok, model, rows: list[dict], batch: int = 64) -> np.ndarray:
    dev = _device()
    out = []
    for i in range(0, len(rows), batch):
        enc = tok([r["text"] for r in rows[i:i + batch]], truncation=True, max_length=256, padding=True,
                  return_tensors="pt").to(dev)
        out.append(torch.sigmoid(model(**enc).logits).float().cpu().numpy())
    return np.concatenate(out)


def contract_probs(rows: list[dict], probs: np.ndarray) -> dict[int, np.ndarray]:
    agg: dict[int, np.ndarray] = {}
    for r, p in zip(rows, probs):
        agg[r["contract"]] = np.maximum(agg.get(r["contract"], 0), p)
    return agg


def f1(pred: list[bool], gold: list[bool]) -> float:
    tp = sum(p and g for p, g in zip(pred, gold))
    fp = sum(p and not g for p, g in zip(pred, gold))
    fn = sum(g and not p for p, g in zip(pred, gold))
    return round(2 * tp / (2 * tp + fp + fn), 3) if tp else 0.0


def run(labels: str, epochs: int) -> dict:
    rows, contracts = load_chunks()
    print(f"{len(rows)} chunks; train {sum(r['split'] == 'train' for r in rows)}, "
          f"dev {sum(r['split'] == 'dev' for r in rows)}, eval {sum(r['split'] == 'eval' for r in rows)}", flush=True)
    t0 = time.monotonic()
    tok, model = train(rows, labels, epochs)
    train_s = time.monotonic() - t0
    gold = gold_rows()
    truth = {cid: [gold_present(gold_answer(gold[cuad._norm(c["name"])], cat)) for cat in CATS]
             for cid, c in contracts.items() if cuad._norm(c["name"]) in gold}
    admin = connect()
    llm = defaultdict(lambda: [False] * len(NAMES))
    for cid, name, present in admin.execute("SELECT contract_id, name, present FROM fields"):
        llm[cid][NAMES.index(name)] = present
    by_split = {s: [r for r in rows if r["split"] == s] for s in ("dev", "eval")}
    t1 = time.monotonic()
    probs = {s: contract_probs(rs, predict(tok, model, rs)) for s, rs in by_split.items()}
    infer_s = (time.monotonic() - t1) / max(1, len(probs["dev"]) + len(probs["eval"]))
    report = {"labels": labels, "epochs": epochs, "train_seconds": round(train_s),
              "seconds_per_contract_local": round(infer_s, 3), "fields": {}}
    for j, name in enumerate(NAMES):
        dev_ids = [c for c in probs["dev"] if c in truth]
        ev_ids = [c for c in probs["eval"] if c in truth]
        # threshold maximizing F1 on dev; confidence band chosen on dev so the confident decisions are >= 95% right
        grid = np.linspace(0.05, 0.95, 19)
        t = max(grid, key=lambda th: f1([probs["dev"][c][j] >= th for c in dev_ids], [truth[c][j] for c in dev_ids]))
        lo, hi = band(probs["dev"], truth, dev_ids, j)
        small = [probs["eval"][c][j] >= t for c in ev_ids]
        llm_pred = [llm[c][j] for c in ev_ids]
        confident = [probs["eval"][c][j] <= lo or probs["eval"][c][j] >= hi for c in ev_ids]
        cascade = [s if conf else lp for s, lp, conf in zip(small, llm_pred, confident)]
        g = [truth[c][j] for c in ev_ids]
        report["fields"][name] = {"eval_contracts": len(ev_ids), "gold_present": sum(g), "small_f1": f1(small, g),
                                  "llm_f1": f1(llm_pred, g), "cascade_f1": f1(cascade, g),
                                  "llm_calls_avoided": round(sum(confident) / max(1, len(ev_ids)), 3),
                                  "threshold": round(float(t), 2), "band": [round(lo, 2), round(hi, 2)]}
    fs = report["fields"].values()
    report["mean"] = {k: round(float(np.mean([v[k] for v in fs])), 3)
                      for k in ("small_f1", "llm_f1", "cascade_f1", "llm_calls_avoided")}
    print(f"{'field':34} {'gold':>4} {'small':>6} {'llm':>6} {'casc':>6} {'avoid':>6}")
    for n, v in report["fields"].items():
        print(f"{n:34} {v['gold_present']:>4} {v['small_f1']:>6} {v['llm_f1']:>6} {v['cascade_f1']:>6} "
              f"{v['llm_calls_avoided']:>6}")
    print("mean", report["mean"], f"| local {report['seconds_per_contract_local']} s/contract, "
          f"train {report['train_seconds']} s")
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"distill-{labels}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"-> {out.name}")
    return report


def band(probs: dict[int, np.ndarray], truth: dict, ids: list[int], j: int) -> tuple[float, float]:
    """Widest confidence band (p <= lo means absent, p >= hi means present) where those decisions are >= 95% right
    on dev; everything in between goes to the LLM."""
    lo = 0.0
    for cand in np.linspace(0.02, 0.5, 25):
        sel = [c for c in ids if probs[c][j] <= cand]
        if sel and sum(not truth[c][j] for c in sel) / len(sel) >= 0.95:
            lo = float(cand)
    hi = 1.01
    for cand in np.linspace(0.98, 0.5, 25):
        sel = [c for c in ids if probs[c][j] >= cand]
        if sel and sum(truth[c][j] for c in sel) / len(sel) >= 0.95:
            hi = float(cand)
    return lo, hi


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", choices=["gold", "llm"], default="gold")
    ap.add_argument("--epochs", type=int, default=3)
    a = ap.parse_args()
    run(a.labels, a.epochs)


if __name__ == "__main__":
    main()
