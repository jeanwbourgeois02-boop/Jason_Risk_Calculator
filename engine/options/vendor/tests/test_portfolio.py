"""Sanity checks for options_calc.portfolio."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from options_calc.portfolio import Position, Portfolio


def _leg(price=10, delta=0.5, gamma=0.01, theta=-0.1, vega=1.0, rho=0.2):
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def test_single_position_scales_by_quantity():
    position = Position(label="A", asset_class="equity", result=_leg(), quantity=100)
    scaled = position.scaled()
    assert scaled["price"] == 1000
    assert abs(scaled["delta"] - 50) < 1e-9


def test_short_position_has_negative_quantity_applied():
    position = Position(label="A", asset_class="equity", result=_leg(), quantity=-100)
    scaled = position.scaled()
    assert scaled["price"] == -1000


def test_portfolio_total_sums_across_positions():
    book = Portfolio([
        Position("A", "equity", _leg(price=10, delta=0.5), quantity=10),
        Position("B", "fx", _leg(price=5, delta=-0.3), quantity=20),
    ])
    total = book.total()
    assert total["price"] == 10 * 10 + 5 * 20
    assert abs(total["delta"] - (0.5 * 10 + -0.3 * 20)) < 1e-9


def test_empty_portfolio_totals_to_zero():
    book = Portfolio([])
    total = book.total()
    assert total["price"] == 0.0
    assert total["delta"] == 0.0


def test_by_asset_class_groups_correctly():
    book = Portfolio([
        Position("A", "equity", _leg(price=10), quantity=1),
        Position("B", "equity", _leg(price=20), quantity=1),
        Position("C", "fx", _leg(price=5), quantity=1),
    ])
    grouped = book.by_asset_class()
    assert set(grouped.keys()) == {"equity", "fx"}
    assert grouped["equity"]["price"] == 30
    assert grouped["fx"]["price"] == 5


def test_asset_class_subtotals_sum_to_grand_total():
    book = Portfolio([
        Position("A", "equity", _leg(price=10, delta=0.4), quantity=3),
        Position("B", "fx", _leg(price=5, delta=-0.2), quantity=7),
    ])
    grouped = book.by_asset_class()
    grand_total = book.total()
    summed_delta = sum(g["delta"] for g in grouped.values())
    assert abs(summed_delta - grand_total["delta"]) < 1e-9


def test_by_label_groups_multiple_lots_of_same_instrument():
    book = Portfolio([
        Position("SPX 5600 Call", "equity", _leg(price=10), quantity=50),
        Position("SPX 5600 Call", "equity", _leg(price=10), quantity=25),
    ])
    grouped = book.by_label()
    assert grouped["SPX 5600 Call"]["price"] == 10 * 75


def test_fx_rate_to_base_defaults_to_one():
    position = Position(label="A", asset_class="equity", result=_leg(price=10), quantity=100)
    assert position.scaled()["price"] == 1000


def test_fx_rate_to_base_converts_position_into_reporting_currency():
    # A position quoted in a foreign currency, converted into the book's
    # reporting currency via an explicit rate -- e.g. a EUR-denominated
    # position reported in a USD book at EURUSD = 1.10.
    position = Position(
        label="EUR position", asset_class="equity", result=_leg(price=10, delta=0.5),
        quantity=100, fx_rate_to_base=1.10,
    )
    scaled = position.scaled()
    assert abs(scaled["price"] - 10 * 100 * 1.10) < 1e-9
    assert abs(scaled["delta"] - 0.5 * 100 * 1.10) < 1e-9


def test_portfolio_total_applies_fx_rate_to_base_per_position():
    book = Portfolio([
        Position("USD leg", "equity", _leg(price=10), quantity=100, fx_rate_to_base=1.0),
        Position("EUR leg", "equity", _leg(price=10), quantity=100, fx_rate_to_base=1.10),
    ])
    total = book.total()
    assert abs(total["price"] - (10 * 100 * 1.0 + 10 * 100 * 1.10)) < 1e-9


def test_extra_fields_like_rho_foreign_survive_portfolio_aggregation():
    # Regression check: combine() must propagate fields beyond the base
    # six (e.g. FX's rho_foreign, delta_premium_adjusted) rather than
    # silently dropping them when building a Position or Portfolio total.
    fx_leg = {
        "price": 5, "delta": 0.3, "gamma": 0.01, "theta": -0.05, "vega": 0.5,
        "rho": 0.1, "rho_foreign": -0.08, "delta_premium_adjusted": 0.27,
    }
    position = Position("FX option", "fx", fx_leg, quantity=10)
    scaled = position.scaled()
    assert scaled["rho_foreign"] == -0.8
    assert abs(scaled["delta_premium_adjusted"] - 2.7) < 1e-9

    book = Portfolio([position])
    total = book.total()
    assert total["rho_foreign"] == -0.8
