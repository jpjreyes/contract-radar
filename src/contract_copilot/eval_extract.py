"""Field extraction eval (M4) against the lawyers' labels in CUAD master_clauses.csv.

Per field:
  presence   does the contract have it? precision / recall / F1 vs. the label (Yes/No columns, or a non-empty answer)
  value      for typed fields, when both sides say present: dates equal, notice days equal, governing law names the
             same jurisdiction, parties: at least half of the labeled parties found (fuzzy)
  correct    presence right AND (value right, where a value is checked)
Routing (what the product promises): precision of AUTO-ACCEPTED fields (target >= 95%) and the share sent to review,
for the shipped policy and two alternatives, so the threshold is a measured choice.

Run (after extract.py):  uv run python -m contract_copilot.eval_extract --split dev
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date, datetime

from rapidfuzz import fuzz

from contract_copilot import cuad
from contract_copilot.db import connect
from contract_copilot.eval_retrieval import RUNS, split_of
from contract_copilot.extract import FIELDS, jurisdictions, parse_date

CSV_ANSWER = {"Notice Period To Terminate Renewal": "Notice Period To Terminate Renewal- Answer"}
VALUE_CHECKED = {"parties", "agreement_date", "effective_date", "expiration_date",
                 "notice_to_terminate_renewal_days", "governing_law"}


def gold_rows() -> dict[str, dict]:
    with open(cuad.CUAD_DIR / "master_clauses.csv", encoding="utf-8") as fh:
        return {cuad._norm(r["Filename"].rsplit(".", 1)[0]): r for r in csv.DictReader(fh)}


def gold_answer(row: dict, category: str) -> str:
    return (row.get(CSV_ANSWER.get(category, f"{category}-Answer")) or "").strip()


def gold_present(answer: str) -> bool:
    return answer not in ("", "No", "[]")


def gold_date(text: str) -> date | None:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", text.strip())
    if not m:
        return None
    mo, d, y = map(int, m.groups())
    y = y if y > 100 else (2000 + y if y <= 40 else 1900 + y)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def gold_days(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(day|week|month|year)", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]


def value_ok(name: str, value: str, gold: str) -> bool | None:
    """True/False when the value can be checked against the label, None when the label isn't checkable."""
    if name in ("agreement_date", "effective_date", "expiration_date"):
        if gold.lower() == "perpetual":
            return value.strip().lower() == "perpetual"
        g = gold_date(gold)
        return None if g is None else parse_date(value) == g
    if name == "notice_to_terminate_renewal_days":
        g = gold_days(gold)
        if g is None or not value.strip().isdigit():
            return None if g is None else False
        return abs(int(value) - g) <= 2  # "3 months" labeled vs 90 days extracted
    if name == "governing_law":
        g, v = set(jurisdictions(gold)), set(jurisdictions(value))
        return None if not g else bool(g & v)
    if name == "parties":
        labeled = [re.sub(r"\(.*?\)|[\"“”]", "", p).strip() for p in re.split(r";", gold) if p.strip()]
        labeled = [p for p in labeled if len(p) > 2]
        got = [p.strip() for p in value.split(";") if p.strip()]
        if not labeled:
            return None
        found = sum(any(fuzz.token_set_ratio(p, q) >= 80 for q in got) for p in labeled)
        return found / len(labeled) >= 0.5
    return None


def run(split: str) -> dict:
    conn = connect()
    gold = gold_rows()
    rows = conn.execute(
        "SELECT c.name, f.name, f.present, f.value, f.confidence, f.status, f.issues FROM fields f "
        "JOIN contracts c ON c.id = f.contract_id").fetchall()
    stats: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    records = []
    contracts = set()
    for cname, fname, present, value, conf, status, issues in rows:
        if split != "all" and split_of(cname) != split:
            continue
        g = gold.get(cuad._norm(cname))
        if g is None:
            continue
        contracts.add(cname)
        category = FIELDS[fname][0]
        ans = gold_answer(g, category)
        gp = gold_present(ans)
        s = stats[fname]
        s["tp"] += present and gp
        s["fp"] += present and not gp
        s["fn"] += (not present) and gp
        s["tn"] += (not present) and not gp
        vok = value_ok(fname, value, ans) if (present and gp and fname in VALUE_CHECKED) else None
        if vok is not None:
            s["value_checked"] += 1
            s["value_ok"] += vok
        correct = (present == gp) and vok is not False
        s["n"] += 1
        s["correct"] += correct
        s["auto"] += status == "auto_accepted"
        s["auto_correct"] += status == "auto_accepted" and correct
        records.append({"contract": cname, "field": fname, "present": present, "gold_present": gp, "value": value,
                        "gold": ans[:200], "value_ok": vok, "correct": correct, "confidence": conf,
                        "issues": issues, "status": status})
    table = {}
    for fname in FIELDS:
        s = stats[fname]
        if not s["n"]:
            continue
        p = s["tp"] / max(1, s["tp"] + s["fp"])
        r = s["tp"] / max(1, s["tp"] + s["fn"])
        table[fname] = {
            "gold_present": s["tp"] + s["fn"], "presence_f1": round(2 * p * r / max(p + r, 1e-9), 3),
            "precision": round(p, 3), "recall": round(r, 3),
            "value_acc": f"{s['value_ok']}/{s['value_checked']}" if s["value_checked"] else "-",
            "accuracy": round(s["correct"] / s["n"], 3),
            "auto_accepted": f"{s['auto']}/{s['n']}",
            "auto_precision": round(s["auto_correct"] / s["auto"], 3) if s["auto"] else None}
    policies = {
        "shipped: high + all checks pass": lambda r: r["status"] == "auto_accepted",
        "high, ignore checks": lambda r: r["confidence"] == "high",
        "high or medium + checks pass": lambda r: r["confidence"] in ("high", "medium") and not r["issues"],
    }
    routing = {}
    for label, accept in policies.items():
        acc = [r for r in records if accept(r)]
        routing[label] = {"auto_accept_precision": round(sum(r["correct"] for r in acc) / max(1, len(acc)), 3),
                          "sent_to_review": round(1 - len(acc) / max(1, len(records)), 3)}
    report = {"split": split, "contracts": len(contracts), "fields": len(records),
              "overall_accuracy": round(sum(r["correct"] for r in records) / max(1, len(records)), 3),
              "by_field": table, "routing": routing}
    print(f"split={split} contracts={len(contracts)} fields={len(records)} accuracy={report['overall_accuracy']}")
    print(f"{'field':34} {'gold':>4} {'F1':>6} {'P':>6} {'R':>6} {'value':>8} {'acc':>6} {'auto':>8} {'autoP':>6}")
    for f, t in table.items():
        print(f"{f:34} {t['gold_present']:>4} {t['presence_f1']:>6} {t['precision']:>6} {t['recall']:>6} "
              f"{t['value_acc']:>8} {t['accuracy']:>6} {t['auto_accepted']:>8} {t['auto_precision']!s:>6}")
    for label, v in routing.items():
        print(f"  routing [{label}]: auto-accept precision {v['auto_accept_precision']}, "
              f"sent to review {v['sent_to_review']}")
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"extract-eval-{split}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({**report, "records": records}, indent=1, default=str))
    print(f"-> {out.name}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    run(ap.parse_args().split)


if __name__ == "__main__":
    main()
