"""A digital is priced ON THE SMILE (user decision 2026-09-21: "yes, price off the smile").

User, 2026-09-21: "the usdjpy options the calculation is wrong somehow". Both USDJPY options
on file are digital puts, strike 152. `pricer.price_fx_digital` matched the closed form to
five decimals, but it takes ONE vol, read at the strike: a digital is the strike-derivative
of a vanilla, so the slope of the smile is part of its price, and USDJPY has the steepest
smile in the book. Kept in its own file: tests/test_options_pricing.py is being edited by
another session (SPX listed options) on the same day.
"""
from __future__ import annotations

import datetime
import math

import pytest

from engine.options import pricer
from tests.test_options_pricing import (SMILE_AS_OF, _new_db, _seed_option_trade, _seed_pair_spot,
                                        _seed_vol, _seed_vol_quotes, needs_quantlib)

AS_OF = datetime.date(2026, 9, 21)
EXPIRY = datetime.date(2026, 11, 19)
T = (EXPIRY - AS_OF).days / 365.0
JPY_RATE, USD_RATE = 0.009, 0.038      # domestic = quote ccy (JPY), foreign = base ccy (USD)
STRIKE, VOL = 152.0, 0.095
GREEKS = ("delta", "gamma", "theta", "vega", "rho")


def _flat(spot, option_type, payout_ccy=pricer.PAYOUT_BASE, vol=VOL):
    return pricer.price_fx_digital(spot, STRIKE, EXPIRY, AS_OF, JPY_RATE, USD_RATE, vol, option_type,
                                   pair="USDJPY", payout_ccy=payout_ccy)


def _on_smile(spot, option_type, vol_at, payout_ccy=pricer.PAYOUT_BASE):
    return pricer.price_fx_digital_on_smile(spot, STRIKE, EXPIRY, AS_OF, JPY_RATE, USD_RATE, vol_at, option_type,
                                            pair="USDJPY", payout_ccy=payout_ccy)


@needs_quantlib
@pytest.mark.parametrize("payout_ccy", [pricer.PAYOUT_BASE, pricer.PAYOUT_QUOTE])
@pytest.mark.parametrize("option_type", ["CALL", "PUT"])
@pytest.mark.parametrize("spot", [147.0, 152.0, 158.0])
def test_on_a_flat_smile_the_digital_is_the_single_vol_digital(spot, option_type, payout_ccy):
    flat = _flat(spot, option_type, payout_ccy)
    smile = _on_smile(spot, option_type, lambda k: VOL, payout_ccy)
    assert smile.premium == pytest.approx(flat.premium, abs=2e-6)   # central difference over 1 bp of strike
    # The vendored digital bumps spot and the clock for its Greeks; the legs here carry the
    # vanilla's analytic ones, so the two agree to a per cent or so (exact values: next test).
    for greek in GREEKS:
        assert getattr(smile, greek) == pytest.approx(getattr(flat, greek), rel=2e-2, abs=1e-4), greek
    assert smile.delta_convention == flat.delta_convention


@needs_quantlib
@pytest.mark.parametrize("spot", [147.0, 158.0])
def test_delta_and_gamma_on_a_flat_smile_are_the_closed_form(spot):
    """BASE payout, value in quote ccy S exp(-r_f T) N(+-d1): its spot delta and gamma."""
    from scipy.stats import norm

    sd = VOL * math.sqrt(T)
    d1 = (math.log(spot / STRIKE) + (JPY_RATE - USD_RATE + 0.5 * VOL ** 2) * T) / sd
    df = math.exp(-USD_RATE * T)
    gamma = df * norm.pdf(d1) / (spot * sd) * (1.0 - d1 / sd)
    expected = {"CALL": (df * (norm.cdf(d1) + norm.pdf(d1) / sd), gamma),
                "PUT": (df * (norm.cdf(-d1) - norm.pdf(d1) / sd), -gamma)}
    for option_type, (delta, gamma) in expected.items():
        smile = _on_smile(spot, option_type, lambda k: VOL)
        assert smile.delta == pytest.approx(delta, rel=1e-5)
        assert smile.gamma == pytest.approx(gamma, rel=1e-4)


@needs_quantlib
@pytest.mark.parametrize("spot", [148.0, 154.0, 158.0])
def test_the_slope_of_the_smile_is_in_the_price(spot):
    """cash digital put = dP/dK along the smile = single-vol digital + vega x dvol/dK; the
    BASE payout is K of those less the vanilla put, in base ccy. A USDJPY-shaped smile (vol
    rising as the strike falls) makes the digital put CHEAPER than the single-vol price."""
    slope = -0.0018                                   # vol per 1 JPY of strike
    vol_at = lambda k: VOL + slope * (k - STRIKE)     # noqa: E731 -- same vol AT the strike as the flat price
    vanilla = pricer.price_fx_vanilla(spot, STRIKE, EXPIRY, AS_OF, JPY_RATE, USD_RATE, VOL, "PUT", pair="USDJPY")
    vega_per_unit_vol = vanilla.vega * 100.0          # `vega` is per vol POINT

    cash = _on_smile(spot, "PUT", vol_at, pricer.PAYOUT_QUOTE)
    assert cash.quote_price == pytest.approx(_flat(spot, "PUT", pricer.PAYOUT_QUOTE).quote_price
                                             + vega_per_unit_vol * slope, rel=1e-4)
    base = _on_smile(spot, "PUT", vol_at)
    flat = _flat(spot, "PUT")
    assert base.premium == pytest.approx(flat.premium + STRIKE * vega_per_unit_vol * slope / spot, rel=1e-4)
    assert base.premium < flat.premium - 0.01         # points of payout, not rounding
    assert 0.0 < base.premium < math.exp(-USD_RATE * T)


@needs_quantlib
def test_call_and_put_digitals_still_add_up_to_the_discounted_payout_on_any_smile():
    """One BASE unit paid either way: call + put = the base-ccy discount factor, whatever the smile."""
    vol_at = lambda k: VOL - 0.0018 * (k - STRIKE) + 0.00002 * (k - STRIKE) ** 2   # noqa: E731
    for spot in (148.0, 158.0):
        total = _on_smile(spot, "CALL", vol_at).premium + _on_smile(spot, "PUT", vol_at).premium
        assert total == pytest.approx(math.exp(-USD_RATE * T), abs=1e-7)


# --------------------------------------------------------------------------- store.py, end to end

def _usdjpy_digital(conn, strike, expiry_iso):
    return _seed_option_trade(conn, trade_id="D1", instrument_id="USDJPY111926P-197571137", pair="USDJPY",
                              strike=strike, option_type="PUT", payoff="DIGITAL", expiry=expiry_iso,
                              quantity=1_000_000.0, price=0.1425)


def _premium_mark(conn, as_of, instrument_id):
    return conn.execute("SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
                        "AND mark_type = 'PREMIUM'", (as_of, instrument_id)).fetchone()[0]


@needs_quantlib
def test_store_prices_a_digital_on_the_smile_when_the_vol_came_off_one():
    from engine.options import store
    from engine.options.inputs import resolve_market_inputs

    conn = _new_db()
    spot, strike, expiry_iso = 148.0, 146.0, "2026-11-19"
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, pair="USDJPY", spot=spot)
    _seed_vol_quotes(conn)
    instrument_id = _usdjpy_digital(conn, strike, expiry_iso)

    outcome = store.price_and_store(conn, SMILE_AS_OF, "D1")
    assert outcome.priced and outcome.vol_source_kind == "SMILE"
    assert outcome.vol_detail.endswith(store.SMILE_DIGITAL_DETAIL)

    inputs = resolve_market_inputs(conn, SMILE_AS_OF, "USDJPY", expiry_iso, strike=strike).inputs
    as_of, expiry = datetime.date.fromisoformat(SMILE_AS_OF), datetime.date.fromisoformat(expiry_iso)
    factor = store.cut_time_factor(conn, SMILE_AS_OF, "USDJPY", expiry)
    args = (spot, strike, expiry, as_of, inputs.domestic_rate, inputs.foreign_rate)
    on_smile = pricer.price_fx_digital_on_smile(*args, lambda k: inputs.vol_source.vol_at(k) * factor, "PUT",
                                                pair="USDJPY")
    single_vol = pricer.price_fx_digital(*args, inputs.vol * factor, "PUT", pair="USDJPY")
    mark = _premium_mark(conn, SMILE_AS_OF, instrument_id)
    assert mark == pytest.approx(on_smile.premium, rel=1e-12)
    assert abs(mark - single_vol.premium) > 1e-4      # the fixture's smile is not flat at this strike


@needs_quantlib
def test_store_keeps_the_single_vol_digital_when_no_smile_is_on_file():
    """A manual vol is one number: no slope is known, so nothing is invented for one."""
    from engine.options import store

    conn = _new_db()
    spot, strike, expiry_iso, as_of_iso = 148.0, 146.0, "2026-11-19", "2026-09-21"
    _seed_pair_spot(conn, as_of=as_of_iso, pair="USDJPY", spot=spot)
    _seed_vol(conn, pair="USDJPY", expiry=expiry_iso, vol=VOL, as_of=as_of_iso)
    instrument_id = _usdjpy_digital(conn, strike, expiry_iso)

    outcome = store.price_and_store(conn, as_of_iso, "D1")
    assert outcome.priced and outcome.vol_source_kind == "MANUAL"
    assert store.SMILE_DIGITAL_DETAIL not in outcome.vol_detail
    assert _premium_mark(conn, as_of_iso, instrument_id) == pytest.approx(outcome.result.premium)
    # ... and it is still the closed form exp(-r_f T) N(-d1) at that one vol
    from scipy.stats import norm
    from engine.options.inputs import resolve_market_inputs
    inputs = resolve_market_inputs(conn, as_of_iso, "USDJPY", expiry_iso, strike=strike).inputs
    factor = store.cut_time_factor(conn, as_of_iso, "USDJPY", datetime.date.fromisoformat(expiry_iso))
    vol, rd, rf = inputs.vol * factor, inputs.domestic_rate, inputs.foreign_rate
    d1 = (math.log(spot / strike) + (rd - rf + 0.5 * vol ** 2) * T) / (vol * math.sqrt(T))
    assert outcome.result.premium == pytest.approx(math.exp(-rf * T) * norm.cdf(-d1), rel=1e-6)
