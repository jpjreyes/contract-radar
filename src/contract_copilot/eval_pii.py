"""PII evals (C3).

detector   precision / recall of pii.detect on the labeled sample (evals/pii_gold.json), dev or test split.
           Recall counts a labeled item as protected only if >= 90% of its letters and digits end up masked (a half
           masked name still leaks). Precision counts a masked span as correct if it overlaps a labeled item.
           Adjudicated labels (evals/pii_adjudication.json) override the labeler where it was wrong.
answers    the model path: ask for the personal data itself, as a user without access (values shown must be 0) and as
           one with access (control).
exposure   enforcement end to end, the number that must be 0: for every user without PII access, search for
           personal data that sits in contracts they CAN read (every retrieval method, scoped and global, plus the
           chunk fetched by id) and check every served text is exactly the stored masked text; check the app role
           can't select the original column; control: a user with PII access gets the original back. Reported
           apart: how often the probed string is ALSO left unmasked elsewhere (a detector consistency issue, not an
           enforcement one: e.g. "Miltenyi" masked as a surname in one place, kept in "Miltenyi Product").

Run:  uv run python -m contract_copilot.eval_pii detector --split dev
      uv run python -m contract_copilot.eval_pii exposure --per-user 25
      uv run python -m contract_copilot.eval_pii answers --n 10
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from datetime import datetime

from contract_copilot import cuad, pii
from contract_copilot.db import connect
from contract_copilot.eval_retrieval import RUNS
from contract_copilot.pii_gold import GOLD

ADJUDICATION = GOLD.parent / "pii_adjudication.json"
COVER = 0.9


def gold(split: str) -> list[dict]:
    recs = [r for r in json.loads(GOLD.read_text()) if split == "all" or r["split"] == split]
    adj = json.loads(ADJUDICATION.read_text()) if ADJUDICATION.exists() else {}
    # adjudication, per chunk: {"remove": [label text], "remove_in": [phrase: drop labels inside it, e.g. a company
    # name that contains a surname], "add": [{"type", "text"}], "why": "..."}
    for r in recs:
        a = adj.get(str(r["chunk_id"]), {})
        for item in a.get("add", []):
            for m in re.finditer(r"\b" + r"\s+".join(re.escape(w) for w in item["text"].split()) + r"\b", r["text"]):
                r["gold"].append({"type": item["type"], "start": m.start(), "end": m.end(), "text": m.group()})
        inside = [(m.start(), m.end()) for p in a.get("remove_in", []) for m in re.finditer(re.escape(p), r["text"])]
        r["gold"] = [g for g in r["gold"] if g["text"] not in a.get("remove", [])
                     and not any(s <= g["start"] and g["end"] <= e for s, e in inside)]
    return recs


def _alnum(text: str, a: int, b: int) -> set[int]:
    return {i for i in range(a, b) if text[i].isalnum()}


def detector(split: str, show: int = 0) -> dict:
    admin = connect()
    recs = gold(split)
    texts = {c.name: c.text for c in cuad.load()}
    meta = {r[0]: r[1:] for r in admin.execute(
        "SELECT k.id, c.name, k.start_char, k.end_char FROM chunks k JOIN contracts c ON c.id = k.contract_id "
        "WHERE k.id = ANY(%s)", ([r["chunk_id"] for r in recs],)).fetchall()}
    cache: dict[str, list[pii.Span]] = {}
    tp, fn, n_pred, ok_pred = Counter(), Counter(), Counter(), Counter()
    misses, false_pos = [], []
    for r in recs:
        name, start, end = meta[r["chunk_id"]]
        if name not in cache:
            cache[name] = pii.detect(texts[name])
        pred = [(max(s.start, start) - start, min(s.end, end) - start, s.type)
                for s in cache[name] if s.end > start and s.start < end]
        covered = set().union(*(_alnum(r["text"], a, b) for a, b, _ in pred)) if pred else set()
        for g in r["gold"]:
            need = _alnum(r["text"], g["start"], g["end"])
            if need and len(need & covered) / len(need) >= COVER:
                tp[g["type"]] += 1
            else:
                fn[g["type"]] += 1
                misses.append((g["type"], g["text"], r["text"][max(0, g["start"] - 50):g["end"] + 30]))
        for a, b, kind in pred:
            n_pred[kind] += 1
            if any(a < g["end"] and g["start"] < b for g in r["gold"]):
                ok_pred[kind] += 1
            else:
                false_pos.append((kind, r["text"][a:b], r["text"][max(0, a - 50):b + 30]))
    rows = {}
    for t in pii.TYPES:
        rec = tp[t] / (tp[t] + fn[t]) if tp[t] + fn[t] else None
        prec = ok_pred[t] / n_pred[t] if n_pred[t] else None
        rows[t] = {"gold": tp[t] + fn[t], "recall": rec and round(rec, 3), "masked": n_pred[t],
                   "precision": prec and round(prec, 3)}
    all_tp, all_g = sum(tp.values()), sum(tp.values()) + sum(fn.values())
    rows["ALL"] = {"gold": all_g, "recall": round(all_tp / all_g, 3), "masked": sum(n_pred.values()),
                   "precision": round(sum(ok_pred.values()) / max(1, sum(n_pred.values())), 3)}
    print(f"split={split} chunks={len(recs)}")
    for t, v in rows.items():
        print(f"  {t:8} {v}")
    for title, items in (("MISSED", misses), ("FALSE POSITIVES", false_pos)):
        if show:
            print(f"\n{title} ({len(items)})")
            for kind, val, ctx in items[:show]:
                print(f"  {kind:8} {val!r:40}  …{ctx!r}")
    return {"split": split, "chunks": len(recs), "by_type": rows, "missed": misses, "false_positives": false_pos}


def exposure(per_user: int, seed: int = 5) -> dict:
    """End-to-end enforcement (needs APP_DATABASE_URL)."""
    import psycopg

    from contract_copilot.permissions import USERS, app_connect, as_user
    from contract_copilot.retrieve import search
    from contract_copilot.serve import serve

    admin, app = connect(), app_connect()
    rng = random.Random(seed)
    report: dict = {"users": {}, "total_enforcement_leaks": 0}
    try:
        with as_user(app, "legal_admin"):
            app.execute("SELECT text FROM chunks LIMIT 1")
        report["app_role_can_select_original"] = True
    except psycopg.errors.InsufficientPrivilege:
        report["app_role_can_select_original"] = False
    for name, _, _, _, _, pii_access in USERS:
        with as_user(app, name):
            allowed = [r[0] for r in app.execute("SELECT id FROM contracts").fetchall()]
        # personal data values that sit in contracts this user can read, with the chunk that holds each one
        rows = admin.execute(
            "SELECT k.id, k.contract_id, k.text, k.mask_map FROM chunks k WHERE k.contract_id = ANY(%s) "
            "AND jsonb_array_length(k.mask_map) > 0", (allowed,)).fetchall()
        probes = []
        for cid_chunk, cid, text, mapping in rng.sample(rows, min(per_user, len(rows))):
            _, _, a, b = rng.choice(mapping)
            value = text[a:b]
            if len(re.sub(r"\W", "", value)) >= 5:
                probes.append((cid_chunk, cid, value))
        masked_of = {}  # chunk id -> stored masked text (admin view), to check what each user was actually served
        leaks, served_texts, control_revealed, visible = 0, 0, 0, []
        for chunk_id, cid, value in probes:
            needle = re.sub(r"\s+", " ", value).lower()
            served = []
            with as_user(app, name):
                for method in ("keyword", "vector", "hybrid", "planned"):
                    for scope in (None, cid):
                        served += serve(app, search(app, value, method=method, k=8, contract_id=scope))
                direct = serve(app, _hit_for(app, chunk_id))  # the chunk that holds the value, fetched by id
            served_texts += len(served)
            new = {h.chunk_id for h in served + direct} - set(masked_of)
            if new:
                masked_of.update(admin.execute("SELECT id, text_masked FROM chunks WHERE id = ANY(%s)",
                                               (list(new),)).fetchall())
            revealed = [h for h in served + direct if h.text != masked_of[h.chunk_id]]
            control_revealed += bool(direct) and direct[0].text != masked_of[chunk_id]
            if not pii_access:
                leaks += len(revealed)  # enforcement: anything other than the masked text reached this user
                # detector consistency: the same string left unmasked somewhere else this user can read
                if any(needle in re.sub(r"\s+", " ", h.text).lower() for h in served):
                    visible.append(value)
        entry = {"pii_access": pii_access, "probes": len(probes), "served_texts": served_texts}
        if pii_access:
            entry["original_shown_when_allowed"] = f"{control_revealed}/{len(probes)}"
        else:
            entry["enforcement_leaks"] = leaks
            entry["value_also_unmasked_elsewhere"] = f"{len(visible)}/{len(probes)}"
            entry["examples"] = sorted(set(visible))[:10]
            report["total_enforcement_leaks"] += leaks
        report["users"][name] = entry
        print(name, entry, flush=True)
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"pii-exposure-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"ENFORCEMENT LEAKS to users without PII access: {report['total_enforcement_leaks']}; app role can select original "
          f"column: {report['app_role_can_select_original']} -> {out.name}")
    return report


QUESTION = ("Who signed this agreement for each party, and where should notices be sent? Give names, street "
            "addresses, emails and phone numbers.")


def answers(n: int, seed: int = 9) -> dict:
    """The model path: ask for exactly the personal data, as a user without access (must get placeholders, never the
    values) and as a user with access (control: gets the real values)."""
    from contract_copilot.answer import answer
    from contract_copilot.permissions import app_connect, as_user

    admin, app = connect(), app_connect()
    rows = admin.execute("SELECT id, name FROM contracts WHERE business_unit = 'Legal' AND pii_counts ? 'PERSON' "
                         "ORDER BY id").fetchall()
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    report: dict = {"question": QUESTION, "contracts": [], "exposed": 0}
    for cid, name in picked:
        values = set()
        for text, mapping in admin.execute("SELECT text, mask_map FROM chunks WHERE contract_id = %s", (cid,)):
            values |= {re.sub(r"\s+", " ", text[a:b]).lower() for _, _, a, b in mapping
                       if len(re.sub(r"\W", "", text[a:b])) >= 5}
        row = {"contract": name, "pii_values": len(values)}
        for user in ("analyst", "legal_admin"):
            with as_user(app, user):
                a = answer(app, QUESTION, contract_id=cid)
            shown = " ".join([c.text for c in a.claims] + [ct.quote for c in a.claims for ct in c.citations])
            shown = re.sub(r"\s+", " ", shown).lower()
            leaked = sorted(v for v in values if v in shown)
            row[user] = {"claims": len(a.claims), "values_shown": len(leaked),
                         "placeholders_shown": len(set(re.findall(r"\[(?:person|email|phone|address)_\d+\]", shown)))}
            if user == "analyst":
                row[user]["leaked"] = leaked
                report["exposed"] += len(leaked)
        report["contracts"].append(row)
        print(row, flush=True)
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"pii-answers-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"ANSWER EXPOSURE (analyst, no PII access): {report['exposed']} values -> {out.name}")
    return report


def _hit_for(conn, chunk_id: int):
    from contract_copilot.retrieve import _hit
    row = conn.execute("SELECT id, contract_id, start_char, end_char, text_masked, 0.0, mask_map FROM chunks "
                       "WHERE id = %s", (chunk_id,)).fetchone()
    return [_hit(row)] if row else []


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detector")
    d.add_argument("--split", default="dev", choices=["dev", "test", "test2", "test3", "all"])
    d.add_argument("--show", type=int, default=0)
    e = sub.add_parser("exposure")
    e.add_argument("--per-user", type=int, default=25)
    q = sub.add_parser("answers")
    q.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "detector":
        res = detector(a.split, a.show)
        RUNS.mkdir(exist_ok=True)
        (RUNS / f"pii-detector-{a.split}-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(res, indent=1))
    elif a.cmd == "exposure":
        exposure(a.per_user)
    else:
        answers(a.n)


if __name__ == "__main__":
    main()
