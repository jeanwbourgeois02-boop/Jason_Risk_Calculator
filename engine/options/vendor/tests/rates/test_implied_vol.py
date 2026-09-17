"""Sanity checks for options_calc.rates.implied_vol -- round-trip a known
vol through the pricer and back through the solver, the same pattern as
tests/equity/test_implied_vol.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from options_calc.rates.swaption import price as swaption_price
from options_calc.rates.cap_floor import price_cap, price_floor
from options_calc.rates.implied_vol import (
    implied_volatility_swaption,
    implied_volatility_cap,
    implied_volatility_floor,
)


def test_swaption_implied_vol_round_trips():
    true_vol = 0.22
    result = swaption_price(0.04, 5, 10, 0.04, true_vol, option_type="payer")
    recovered = implied_volatility_swaption(result["price"], 0.04, 5, 10, 0.04, option_type="payer")
    assert abs(recovered - true_vol) < 1e-4


def test_receiver_swaption_implied_vol_round_trips():
    true_vol = 0.18
    result = swaption_price(0.045, 3, 7, 0.035, true_vol, option_type="receiver")
    recovered = implied_volatility_swaption(result["price"], 0.045, 3, 7, 0.035, option_type="receiver")
    assert abs(recovered - true_vol) < 1e-4


def test_cap_implied_vol_round_trips():
    true_vol = 0.25
    result = price_cap(0.04, 1.0, 5, 0.04, true_vol)
    recovered = implied_volatility_cap(result["price"], 0.04, 1.0, 5, 0.04)
    assert abs(recovered - true_vol) < 1e-4


def test_floor_implied_vol_round_trips():
    true_vol = 0.15
    result = price_floor(0.04, 1.0, 5, 0.04, true_vol)
    recovered = implied_volatility_floor(result["price"], 0.04, 1.0, 5, 0.04)
    assert abs(recovered - true_vol) < 1e-4
