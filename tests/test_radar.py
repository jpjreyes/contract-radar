"""Act this quarter: reading renewal terms and rolling auto-renewals forward to today's notice deadline."""

from datetime import date

import pytest

from contract_radar.radar import Renewal, add_months, current_term_end, parse_renewal


@pytest.mark.parametrize("value, expected", [
    ("automatic, successive 1 year", Renewal("automatic", 12, None)),
    ("automatic, successive 6 months", Renewal("automatic", 6, None)),
    ("automatic, one additional 12 month period", Renewal("automatic", 12, 1)),
    ("automatic, one additional 3 year period", Renewal("automatic", 36, 1)),
    ("automatic, up to 2 renewal terms of 5 years each", Renewal("automatic", 60, 2)),
    ("automatic, successive twelve (12) months", Renewal("automatic", 12, None)),
    ("automatic, successive until terminated by either Party on 30 calendar days' prior written notice",
     Renewal("evergreen")),
    ("automatic, unlimited period", Renewal("evergreen")),
    ("not automatic; renewable only by mutual consent", Renewal("not_automatic")),
    ("by mutual agreement of the parties, not automatic", Renewal("not_automatic")),
    ("at MBE's election, successive 2 year Renewal Periods", Renewal("not_automatic")),
    ("automatic, successive * years (length blank in source)", Renewal("unclear")),
])
def test_parse_renewal(value, expected):
    assert parse_renewal(value) == expected


def test_add_months_clamps_to_month_end():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_rolls_forward_unlimited_renewals():
    # signed for a term ending 2012-12-31, renews yearly: on 2026-11-11 the current term ends 2026-12-31
    assert current_term_end(date(2012, 12, 31), Renewal("automatic", 12, None), date(2026, 11, 11)) == date(2026, 12, 31)


def test_limited_renewals_run_out():
    # one 3-year renewal after 2015: ended 2018, nothing to act on in 2026
    assert current_term_end(date(2015, 6, 30), Renewal("automatic", 36, 1), date(2026, 11, 11)) is None
    assert current_term_end(date(2025, 6, 30), Renewal("automatic", 36, 1), date(2026, 11, 11)) == date(2028, 6, 30)
