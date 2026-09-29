"""The golden book (tests/golden_book.py): the sample blotter at synthetic marks must value
exactly as tests/golden/book.json says. This is the infra agent's proof that a refactor
changed no behaviour (CLAUDE.md "Working mode"). A difference here is either a bug or a
P&L change, and a P&L change needs the user's yes (hard rule 7) before the file is re-pinned
with `python -m tests.golden_book --write`."""
import json

import pytest

from tests import golden_book as gb


@pytest.fixture(scope="module")
def actual():
    return gb.snapshot(gb.build_book())


def test_the_golden_file_is_pinned():
    assert gb.GOLDEN.exists(), "tests/golden/book.json is missing: python -m tests.golden_book --write (with the user's approval)"


def test_the_book_values_exactly_as_the_golden_says(actual):
    expected = json.loads(gb.GOLDEN.read_text(encoding="utf-8"))
    diffs = gb.compare(expected, actual)
    assert not diffs, (
        f"{len(diffs)} difference(s) from tests/golden/book.json; the first are:\n  " + "\n  ".join(diffs[:25])
        + "\nA change to what the book is worth needs the user's yes (CLAUDE.md hard rule 7) before "
          "`python -m tests.golden_book --write` re-pins it.")


def test_every_pinned_trade_values_exactly_as_pinned(actual):
    """The proof behind a re-pin: whatever the book gains, no trade the golden file pins moves."""
    result = gb.golden_diff(json.loads(gb.GOLDEN.read_text(encoding="utf-8")), actual)
    assert result["pinned"]["trades"] > 0
    assert not result["changed"], gb.format_diff(result)


def test_golden_diff_reports_moved_missing_and_new_trades():
    row = {"trade_id": "1", "pnl_usd": 10.0, "product": "FUTURE", "instrument_id": "X", "status": "OPEN"}
    expected = {"sample": "s", "days": {"d1": {"value_book": [row, dict(row, trade_id="2")], "ltd": 20.0}}}
    actual = {"sample": "s", "days": {"d1": {"value_book": [dict(row, pnl_usd=11.0), dict(row, trade_id="3")],
                                             "ltd": 21.0}}}
    result = gb.golden_diff(expected, actual)
    assert result["counts"] == {"d1": {"ltd": 1, "value_book": 2}}
    assert result["changed"] == [("1", "d1", ["pnl_usd: 10.0 != 11.0"]), ("2", "d1", ["row missing from the new book"])]
    assert result["pinned"] == {"trades": 2, "rows": 2}
    assert [t["trade_id"] for t in result["new"]] == ["3"] and result["new"][0]["ltd"] == {"d1": 10.0}
    text = gb.format_diff(result)
    assert "2 pinned row(s) differ" in text and "3 FUTURE X: LTD USD d1 10.00 (OPEN)" in text


def test_the_fixture_still_covers_every_product_and_both_statuses(actual):
    last_day = actual["days"][gb.AS_OF_DATES[-1]]
    last = last_day["value_book"]
    products = {r["product"] for r in last}
    statuses = {(r["product"], r["status"]) for r in last}
    assert {"FX_FWD", "FX_SPOT", "FUTURE", "FX_OPTION", "CMDTY_OPTION", "LME_FWD"} <= products, products
    # an expired future, a settled FX forward, a closed-out option pair, an expired option on a future
    # and an LME forward past its prompt, beside the open book
    assert {("FUTURE", "OPEN"), ("FUTURE", "SETTLED"), ("FX_FWD", "OPEN"), ("FX_FWD", "SETTLED"),
            ("FX_OPTION", "OPEN"), ("FX_OPTION", "CLOSED"), ("CMDTY_OPTION", "OPEN"), ("CMDTY_OPTION", "SETTLED"),
            ("LME_FWD", "OPEN"), ("LME_FWD", "SETTLED")} <= statuses, statuses
    by_id = {r["trade_id"]: r for r in last}
    assert by_id["910000027"]["instrument_id"] == "CLQ26 Comdty" and by_id["910000027"]["status"] == "SETTLED"
    # futures in every currency of the sample, each converted at its own USD pair
    assert {r["currency"] for r in last_day["curve_positions"]["rows"]} >= {"USD", "CNY", "EUR", "GBP", "JPY"}
    assert last_day["expiry_schedule"]["rows"], "the expiry schedule lists no contract"


def test_the_synthetic_marks_still_price_the_book(actual):
    for d, day in actual["days"].items():
        rows = day["value_book"]
        priced = sum(1 for r in rows if r["pnl_usd"] is not None)
        assert priced >= 0.95 * len(rows), f"{d}: only {priced} of {len(rows)} rows priced; unpriced reasons: " + \
            "; ".join(sorted({r["reason"] for r in rows if r["pnl_usd"] is None})[:5])
        # the only trades the marks leave unpriced are the sample's rows the parser could not
        # recognise (the "needs a fix" rows, blank P&L with their reason): every real trade prices
        unpriced = {r["trade_id"]: r["product"] for r in rows if r["pnl_usd"] is None}
        assert set(unpriced.values()) <= {"UNRECOGNISED"}, unpriced
        assert day["ltd_priced"]["excluded_ids"] == sorted(unpriced)
        assert day["ltd_priced"]["value"] is not None


def test_the_priced_figures_equal_the_whole_book_where_every_trade_is_priced(actual):
    """ltd_priced / period_pnl_priced are the book's LTD and periods over the priced trades; where
    nothing is left out they are exactly the ledger's own figures, on every pinned date."""
    checked = 0
    for d, day in actual["days"].items():
        if day["ltd"] is not None:
            assert day["ltd_priced"]["excluded"] == 0, d
            assert gb.compare(day["ltd"], day["ltd_priced"]["value"]) == [], d
            checked += 1
        for key, period in day["period_pnl"].items():
            priced = day["period_pnl_priced"][key]
            assert priced["ref_date"] == period["ref_date"], (d, key)
            if period["available"]:
                assert priced["excluded"] == 0, (d, key, priced["excluded_ids"])
                assert gb.compare(period["value"], priced["value"]) == [], (d, key)
                checked += 1
    assert checked, "no date of the fixture has every trade priced: the check proves nothing"


def test_priced_figures_leave_out_a_trade_unpriced_on_either_date():
    import sqlite3
    from unittest import mock

    import pandas as pd

    frames = {
        "2026-09-18": pd.DataFrame({"trade_id": ["a", "b", "c"], "pnl_usd": [10.0, float("nan"), 5.0],
                                    "trade_date": ["2026-09-01", "2026-09-18", "2026-09-18"]}),
        "2026-09-17": pd.DataFrame({"trade_id": ["a", "b"], "pnl_usd": [4.0, 1.0],
                                    "trade_date": ["2026-09-01", "2026-09-18"]}),
    }
    refs = {"daily": "2026-09-17", "previous_day": "2026-09-16"}
    with mock.patch("engine.pnl.valuation.value_book", lambda conn, day: frames.get(day, frames["2026-09-17"])), \
            mock.patch("engine.pnl.ledger.period_reference_dates", lambda as_of: refs):
        out = gb.priced_figures(sqlite3.connect(":memory:"), "2026-09-18")
    assert out["ltd_priced"] == {"value": 15.0, "excluded": 1, "excluded_ids": ["b"]}
    assert out["period_pnl_priced"]["daily"] == {"ref_date": "2026-09-17", "value": 15.0 - 4.0,
                                                 "excluded": 1, "excluded_ids": ["b"]}
    assert "previous_day" not in out["period_pnl_priced"]
    assert out["period_pnl_priced"]["trading"] == {"ref_date": "2026-09-18", "value": 5.0,
                                                   "excluded": 1, "excluded_ids": ["b"]}


def test_compare_tolerates_float_noise_and_reports_real_differences():
    assert gb.compare({"a": [1.0, 2.0]}, {"a": [1.0 + 1e-12, 2.0]}) == []
    diffs = gb.compare({"a": [1.0, 2.0], "b": None}, {"a": [1.0, 2.5], "b": 3.0, "c": 1})
    assert diffs == ["a[1]: 2.0 != 2.5", "b: None != 3.0", "c: not in golden"]
