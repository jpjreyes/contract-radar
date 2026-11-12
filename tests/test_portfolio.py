"""Portfolio planning (M5): conditions compile to parameterized SQL with the same meaning as the gold evaluator."""

from contract_radar.eval_portfolio import holds
from contract_radar.portfolio import Condition as C
from contract_radar.portfolio import compile_sql


def test_values_are_bound_parameters_never_spliced_into_sql():
    sql, args = compile_sql([C("governing_law", "contains", "Delaware'; DROP TABLE contracts; --")])
    assert "DROP" not in sql and any("DROP" in str(a) for a in args)


def test_no_conditions_returns_nothing_rather_than_everything():
    sql, _ = compile_sql([])
    assert sql.endswith("WHERE false ORDER BY c.name")


def test_absent_and_numeric_conditions():
    sql, args = compile_sql([C("cap_on_liability", "absent"), C("notice_to_terminate_renewal_days", "lte", "60 days")])
    assert "NOT EXISTS" in sql and 60 in args


def test_gold_evaluator_semantics():
    rec = {"governing_law": "Ontario, Canada", "notice_to_terminate_renewal_days": "3 months",
           "expiration_date": "12/31/14", "exclusivity": "No", "contract_type": "Distributor"}
    assert holds(C("governing_law", "contains", "Ontario"), rec)
    assert not holds(C("governing_law", "contains", "New York"), rec)
    assert holds(C("notice_to_terminate_renewal_days", "lte", "90"), rec)
    assert holds(C("expiration_date", "before", "2015-01-01"), rec)
    assert holds(C("exclusivity", "absent"), rec) and not holds(C("exclusivity", "present"), rec)
    assert holds(C("contract_type", "contains", "distributor"), rec)
