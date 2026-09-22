"""The golden book (tests/golden_book.py): the sample blotter at synthetic marks must value
exactly as tests/golden/book.json says. This is the infra agent's proof that a refactor
changed no behaviour (CLAUDE.md "Infra agent"). A difference here is either a bug or a
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


def test_the_fixture_still_covers_every_product_and_both_statuses(actual):
    last = actual["days"][gb.AS_OF_DATES[-1]]["value_book"]
    products = {r["product"] for r in last}
    statuses = {r["status"] for r in last}
    assert {"FX_FWD", "FX_SPOT", "FUTURE", "IRS", "FX_OPTION"} <= products, products
    assert {"OPEN", "SETTLED"} <= statuses, statuses


def test_the_synthetic_marks_still_price_the_book(actual):
    for d, day in actual["days"].items():
        rows = day["value_book"]
        priced = sum(1 for r in rows if r["pnl_usd"] is not None)
        assert priced >= 0.95 * len(rows), f"{d}: only {priced} of {len(rows)} rows priced; unpriced reasons: " + \
            "; ".join(sorted({r["reason"] for r in rows if r["pnl_usd"] is None})[:5])
    assert actual["days"][gb.AS_OF_DATES[-1]]["ltd"] is not None


def test_compare_tolerates_float_noise_and_reports_real_differences():
    assert gb.compare({"a": [1.0, 2.0]}, {"a": [1.0 + 1e-12, 2.0]}) == []
    diffs = gb.compare({"a": [1.0, 2.0], "b": None}, {"a": [1.0, 2.5], "b": 3.0, "c": 1})
    assert diffs == ["a[1]: 2.0 != 2.5", "b: None != 3.0", "c: not in golden"]
