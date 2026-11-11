"""Act this quarter: the decisions the extracted terms put on the calendar, for the contracts a user can see.

The hard part is renewal. A contract signed in 2012 for 3 years that renews automatically every year is still alive
today, and the date that matters is not its expiration but the **last day to send the non-renewal notice**:
the end of the current term minus the notice period. So for each auto-renewing contract we roll the end date
forward term by term (respecting a limited number of renewals) until it passes the as-of date, then subtract the
notice days. Anything we can't compute reliably (unclear renewal length, missing expiration or notice) is listed as
"needs a person", never guessed.

Sections: notice deadlines in the horizon, auto-renewals with a deadline already passed (renews unless terminated
for cause or by agreement), uncapped liability, fixed-term contracts expiring in the next 12 months, and the
contracts that need a person before they can be placed on the calendar.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from contract_copilot.extract import parse_date

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "twelve": 12, "eighteen": 18, "twenty-four": 24, "thirty-six": 36}
NUM = r"(\d+|" + "|".join(WORDS) + r")"


@dataclass(frozen=True)
class Renewal:
    kind: str                     # "automatic" | "evergreen" | "not_automatic" | "unclear"
    months: int | None = None     # length of each renewal term
    max_renewals: int | None = None  # None = unlimited


def _n(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token]


def parse_renewal(value: str) -> Renewal:
    """Read an extracted renewal value ("automatic, successive 1 year", "up to 2 renewal terms of 5 years each")."""
    v = value.lower()
    if re.search(r"\b(not automatic|no automatic|not auto|mutual|by agreement|option|election|unilateral|"
                 r"conditional)\b", v) and not v.startswith("automatic"):
        return Renewal("not_automatic")
    if not v.startswith("automatic"):
        return Renewal("unclear")
    if re.search(r"until terminated|unlimited period|indefinite|evergreen", v):
        return Renewal("evergreen")
    m = re.search(NUM + r"[\s(]*\)?\s*[-\s]?(year|yr|month)s?", v)
    if not m:
        return Renewal("unclear")
    months = _n(m.group(1)) * (12 if m.group(2) in ("year", "yr") else 1)
    limit = None
    if re.search(r"\bone (additional|further|renewal|extension)\b", v):
        limit = 1
    elif lm := re.search(r"up to " + NUM + r"[\s(]*\)?\s*(?:additional |successive |renewal |consecutive )*(?:terms|periods|renewals|times)", v):
        limit = _n(lm.group(1))
    return Renewal("automatic", months, limit)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def current_term_end(expiration: date, renewal: Renewal, as_of: date) -> date | None:
    """End of the term running on `as_of` (or the last one, if the renewals ran out before it)."""
    end, used = expiration, 0
    while end < as_of:
        if renewal.max_renewals is not None and used >= renewal.max_renewals:
            return None  # renewals exhausted: the contract has ended
        end = add_months(end, renewal.months)
        used += 1
    return end


@dataclass
class Item:
    contract_id: int
    contract: str
    headline: str
    when: str | None = None       # ISO date the row is about
    days_left: int | None = None
    evidence: dict[str, dict] = field(default_factory=dict)  # field -> value, status, offsets


FIELDS = ("expiration_date", "renewal_term", "notice_to_terminate_renewal_days", "uncapped_liability")


def compute(conn: psycopg.Connection, as_of: date, horizon_days: int = 90) -> dict:
    """Runs as the caller (RLS decides which contracts exist)."""
    rows = conn.execute(
        "SELECT f.contract_id, c.name, f.name, f.present, f.value, f.status, f.start_char, f.end_char "
        "FROM fields f JOIN contracts c ON c.id = f.contract_id WHERE f.name = ANY(%s)", (list(FIELDS),)).fetchall()
    by: dict[int, dict] = {}
    for cid, cname, fname, present, value, status, s, e in rows:
        rec = by.setdefault(cid, {"name": cname})
        rec[fname] = {"present": present, "value": value, "status": status, "start": s, "end": e}
    horizon = as_of + timedelta(days=horizon_days)
    out: dict[str, list[Item]] = {"notice_deadlines": [], "deadline_passed": [], "evergreen": [], "uncapped": [],
                                  "expiring": [], "needs_person": []}
    for cid, rec in by.items():
        ev = {k: v for k, v in rec.items() if k != "name" and v["present"]}
        name = rec["name"]
        if "uncapped_liability" in ev:
            out["uncapped"].append(Item(cid, name, ev["uncapped_liability"]["value"] or "uncapped liability",
                                        evidence={"uncapped_liability": ev["uncapped_liability"]}))
        exp = parse_date(ev["expiration_date"]["value"]) if "expiration_date" in ev else None
        if "renewal_term" not in ev:
            if exp and as_of <= exp <= as_of + timedelta(days=365):
                out["expiring"].append(Item(cid, name, "fixed term ends", exp.isoformat(), (exp - as_of).days,
                                            {"expiration_date": ev["expiration_date"]}))
            continue
        ren = parse_renewal(ev["renewal_term"]["value"])
        keep = {k: ev[k] for k in ("expiration_date", "renewal_term", "notice_to_terminate_renewal_days") if k in ev}
        if ren.kind == "evergreen":
            out["evergreen"].append(Item(cid, name, "continues until someone terminates", evidence=keep))
            continue
        if ren.kind == "not_automatic":
            if exp and as_of <= exp <= as_of + timedelta(days=365):
                out["expiring"].append(Item(cid, name, "term ends; renewal is not automatic", exp.isoformat(),
                                            (exp - as_of).days, keep))
            continue
        if ren.kind == "unclear" or exp is None:
            why = "renewal term unclear" if ren.kind == "unclear" else "no expiration date extracted"
            out["needs_person"].append(Item(cid, name, why, evidence=keep))
            continue
        end = current_term_end(exp, ren, as_of)
        if end is None:
            continue  # renewals exhausted before as_of
        notice = ev.get("notice_to_terminate_renewal_days")
        days = int(notice["value"]) if notice and notice["value"].isdigit() else None
        if days is None:
            out["needs_person"].append(Item(cid, name, f"renews {end.isoformat()}; notice period not extracted",
                                            end.isoformat(), (end - as_of).days, keep))
            continue
        deadline = end - timedelta(days=days)
        if as_of <= deadline <= horizon:
            out["notice_deadlines"].append(Item(cid, name, f"send non-renewal notice by {deadline.isoformat()} "
                                                f"(term ends {end.isoformat()}, {days} days' notice)",
                                                deadline.isoformat(), (deadline - as_of).days, keep))
        elif deadline < as_of:
            out["deadline_passed"].append(Item(cid, name, f"renews {end.isoformat()}: notice deadline "
                                               f"{deadline.isoformat()} has passed", end.isoformat(),
                                               (end - as_of).days, keep))
    for key in ("notice_deadlines", "deadline_passed", "expiring"):
        out[key].sort(key=lambda i: i.when or "")
    unreviewed = sum(any(v["status"] == "needs_review" for v in i.evidence.values())
                     for items in out.values() for i in items)
    return {"as_of": as_of.isoformat(), "horizon_days": horizon_days, "contracts": len(by),
            "unreviewed_items": unreviewed, "sections": {k: [i.__dict__ for i in v] for k, v in out.items()}}
