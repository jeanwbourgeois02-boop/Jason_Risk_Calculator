"""Tests for engine/rates_vol/: swaption / cap-floor / SABR / Bermudan-swaption pricing
and the SQLite glue (price_and_store / price_all_and_store), priced from the vendored
``options_calc.rates`` library.

QuantLib-dependent tests are skipped (not errored) when QuantLib is not installed,
mirroring tests/test_rates_pricing.py's own skip-if-QuantLib-absent pattern.
"""
from __future__ import annotations

import datetime
import os
import sqlite3

import pytest

try:
    import QuantLib as ql  # noqa: F401
    HAVE_QUANTLIB = True
except ImportError:
    HAVE_QUANTLIB = False

needs_quantlib = pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")

from data.ingest.schema import create_schema

FIXTURE = os.path.join("data", "bloomberg", "fixtures", "ois_snapshot_v1.json")
AS_OF = "2026-08-17"


def _seed_curve(conn: sqlite3.Connection, ccy: str = "USD", as_of: str = AS_OF) -> None:
    """Seed curve_quotes from the shared OIS fixture, per task instructions (reuse
    RatesFileSource + write_curve_quotes, don't invent a curve)."""
    from data.bloomberg.rates_marketdata import RatesFileSource, write_curve_quotes

    source = RatesFileSource(FIXTURE)
    snapshot = source.get_curve_quotes(ccy, datetime.date.fromisoformat(as_of))
    write_curve_quotes(conn, snapshot, as_of)


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _seed_swaption_trade(
    conn: sqlite3.Connection,
    trade_id: str,
    instrument_id: str,
    quantity: float,
    strike: float = 0.04,
    ccy: str = "USD",
    index: str = "SOFR",
    underlying_start: str = "2031-08-17",
    underlying_end: str = "2036-08-17",
    expiry_date: str = "2031-08-17",
    option_type: str = "PAYER",
    payoff: str = "SWAPTION",
    product: str = "SWAPTION",
    price: float = 0.0,
) -> None:
    from engine.rates_vol.store import write_instrument_rate_option

    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "IRS_OPTION", ccy, ccy, 1.0, 0, instrument_id, expiry_date),
    )
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, product, trade_id, AS_OF, quantity, price,
         "TEST-ACCT", "TEST-CPTY", "TEST", "TEST-TRADER", "test rate option"),
    )
    conn.commit()
    if payoff:
        write_instrument_rate_option(
            conn, instrument_id, payoff=payoff, strike=strike, index=index,
            underlying_start=underlying_start, underlying_end=underlying_end, option_type=option_type,
        )


# --------------------------------------------------------------------------- pricer.py

@needs_quantlib
def test_payer_minus_receiver_swaption_equals_forward_swap_npv():
    """The swaption analogue of put-call parity (MODELS.md's own verification for
    rates/swaption.py): payer - receiver == the forward-starting swap's OWN NPV, under
    the SAME flat-curve model the vendored engine itself uses (comparing against
    engine/rates's bootstrapped-curve NPV would not be apples-to-apples -- see
    engine/rates_vol/__init__.py's "Flat-curve approximation")."""
    from engine.options.vendor.options_calc.rates._engine import build_forward_swap
    from engine.rates_vol import pricer

    discount_rate, forecast_rate = 0.0410, 0.0420
    fixed_rate, expiry_years, swap_tenor_years, notional = 0.0410, 5.0, 10.0, 1_000_000.0

    payer = pricer.price_swaption(notional, fixed_rate, "PAYER", expiry_years, swap_tenor_years,
                                   discount_rate, forecast_rate, 0.20)
    receiver = pricer.price_swaption(notional, fixed_rate, "RECEIVER", expiry_years, swap_tenor_years,
                                      discount_rate, forecast_rate, 0.20)

    _, _, swap, _ = build_forward_swap(fixed_rate, expiry_years, int(swap_tenor_years), discount_rate, notional,
                                        "payer", discount_rate=discount_rate, forecast_rate=forecast_rate)
    assert (payer.npv_total - receiver.npv_total) == pytest.approx(swap.NPV(), abs=1e-6)


@needs_quantlib
def test_long_and_short_swaption_have_opposite_signed_pv():
    from engine.rates_vol import pricer

    long_payer = pricer.price_swaption(1_000_000.0, 0.04, "PAYER", 5.0, 10.0, 0.04, 0.04, 0.20)
    short_payer = pricer.price_swaption(-1_000_000.0, 0.04, "PAYER", 5.0, 10.0, 0.04, 0.04, 0.20)
    assert long_payer.npv_total > 0
    assert short_payer.npv_total == pytest.approx(-long_payer.npv_total)

    long_receiver = pricer.price_swaption(1_000_000.0, 0.04, "RECEIVER", 5.0, 10.0, 0.04, 0.04, 0.20)
    assert long_receiver.npv_total > 0  # a long option is always a positive value to its holder


@needs_quantlib
def test_price_cap_matches_the_vendored_cap_floor_function_directly():
    """Locks our unit-notional pass-through: pricer.price_cap_floor must reproduce
    rates/cap_floor.price_cap's own 'price' field bit-for-bit at notional=1 (task's
    "cap = sum of caplets vs the vendored function" -- the vendored BlackCapFloorEngine
    itself sums the strip; this checks we did not disturb that sum in our wrapper)."""
    from engine.options.vendor.options_calc.rates import cap_floor as vendor_cap_floor
    from engine.rates_vol import pricer

    strike, start, tenor, r, sigma = 0.04, 0.5, 5.0, 0.04, 0.20
    ours = pricer.price_cap_floor(2_000_000.0, "CAP", strike, start, tenor, r, r, sigma, freq_months=6)
    vendor_raw = vendor_cap_floor.price_cap(strike, start, tenor, r, sigma, notional=1.0, freq_months=6,
                                             discount_rate=r, forecast_rate=r)
    assert ours.npv_per_unit == pytest.approx(vendor_raw["price"], abs=1e-10)
    assert ours.npv_total == pytest.approx(vendor_raw["price"] * 2_000_000.0, rel=1e-9)


@needs_quantlib
def test_sabr_vol_at_atm_is_close_to_alpha_and_smile_is_asymmetric_with_nonzero_rho():
    from engine.options.vendor.options_calc.rates.sabr import sabr_swaption_vol

    forward, expiry = 0.04, 5.0
    alpha, beta, nu = 0.04, 0.5, 0.4

    # ATM (strike == forward): SABR's ATM vol should sit close to the alpha-driven
    # backbone level (Hagan's ATM approximation is alpha / forward^(1-beta) to leading
    # order) -- checked as "same order of magnitude", not an exact closed form here.
    v_atm = sabr_swaption_vol(forward, forward, expiry, alpha, beta, rho=0.0, nu=0.0)
    assert v_atm == pytest.approx(alpha / forward ** (1 - beta), rel=0.05)

    # Non-zero (negative) rho drives skew: low-strike (put-side) vol should exceed
    # high-strike (call-side) vol for a symmetric strike spread around the forward.
    rho = -0.3
    v_low = sabr_swaption_vol(0.02, forward, expiry, alpha, beta, rho, nu)
    v_high = sabr_swaption_vol(0.06, forward, expiry, alpha, beta, rho, nu)
    assert v_low > v_atm > v_high or v_low > v_high  # skew is asymmetric, not flat
    assert v_low != pytest.approx(v_high, rel=1e-6)


@needs_quantlib
def test_bermudan_swaption_is_worth_at_least_the_european_under_the_same_hull_white_model():
    """The key sanity inequality (MODELS.md's own verification for
    bermudan_swaption.py): more exercise opportunities cannot destroy value, checked
    under the SAME Hull-White model + tree engine on both sides (comparing against
    swaption.py's different Black-76 model would be model-inconsistent -- see
    price_european_swaption_hw's docstring)."""
    from engine.options.vendor.options_calc.rates.bermudan_swaption import price_european_swaption_hw
    from engine.rates_vol import pricer

    fixed_rate, expiry, swap_tenor, r = 0.04, 5.0, 10.0, 0.04
    hw_a, hw_sigma = 0.03, 0.01

    european_hw = price_european_swaption_hw(fixed_rate, expiry, int(swap_tenor), r, notional=1.0, option_type="payer",
                                              discount_rate=r, forecast_rate=r,
                                              hw_mean_reversion=hw_a, hw_volatility=hw_sigma, tree_steps=80)
    bermudan = pricer.price_bermudan_swaption(1.0, fixed_rate, "PAYER", expiry, swap_tenor, 1.0, r, r,
                                               hw_a, hw_sigma, tree_steps=80)
    assert bermudan.npv_total >= european_hw - 1e-6


@needs_quantlib
def test_bermudan_swaption_rejects_implausible_hw_sigma():
    from engine.rates_vol import pricer

    with pytest.raises(ValueError):
        pricer.price_bermudan_swaption(1_000_000.0, 0.04, "PAYER", 5.0, 10.0, 1.0, 0.04, 0.04,
                                        hw_mean_reversion=0.03, hw_sigma=1.0)


@needs_quantlib
def test_price_swaption_rejects_zero_quantity():
    from engine.rates_vol import pricer

    with pytest.raises(ValueError):
        pricer.price_swaption(0.0, 0.04, "PAYER", 5.0, 10.0, 0.04, 0.04, 0.20)


# --------------------------------------------------------------------------- inputs.py

@needs_quantlib
def test_derive_curve_inputs_returns_none_without_curve_quotes():
    from engine.rates_vol.inputs import derive_curve_inputs

    conn = _new_conn()
    result = derive_curve_inputs(conn, AS_OF, "USD", "SOFR", datetime.date(2031, 8, 17),
                                  datetime.date(2031, 8, 17), datetime.date(2036, 8, 17))
    assert result is None


@needs_quantlib
def test_derive_curve_inputs_forecast_rate_matches_forward_par_swap_rate():
    from engine.rates.instruments import build_instrument
    from engine.rates_vol.inputs import derive_curve_inputs

    conn = _new_conn()
    _seed_curve(conn)
    ci = derive_curve_inputs(conn, AS_OF, "USD", "SOFR", datetime.date(2031, 8, 17),
                              datetime.date(2031, 8, 17), datetime.date(2036, 8, 17))
    assert ci is not None
    built = build_instrument(ci.curve_set, datetime.date(2031, 8, 17), datetime.date(2036, 8, 17),
                              fixed_rate=0.0, notional=1.0, pay_fixed=True)
    assert ci.forecast_rate == pytest.approx(built.ql_swap.fairRate(), abs=1e-9)


def test_get_rate_vol_prefers_lognormal_over_normal_and_falls_back_to_atm():
    from engine.rates_vol.inputs import set_manual_rate_vol, get_rate_vol

    conn = _new_conn()
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 0.19, vol_type="LOGNORMAL")
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "0.040000", 0.10, vol_type="NORMAL")

    exact = get_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", 0.04)
    assert exact.vol_type == "NORMAL"  # exact strike only has a NORMAL entry -- returned as-is

    atm = get_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", 0.09)  # no exact match -> ATM fallback
    assert atm.vol_type == "LOGNORMAL"
    assert atm.vol == pytest.approx(0.19)


def test_resolve_swaption_vol_rejects_normal_vol_explicitly():
    from engine.rates_vol.inputs import set_manual_rate_vol, resolve_swaption_vol

    conn = _new_conn()
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 80.0, vol_type="NORMAL")
    res = resolve_swaption_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", 0.04, 5.0, 0.04)
    assert res.vol is None
    assert "NORMAL" in res.reason


# --------------------------------------------------------------------------- store.py glue

@needs_quantlib
def test_price_and_store_writes_pv_dv01_vega_marks_for_a_payer_swaption():
    from engine.rates_vol.inputs import set_manual_rate_vol
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    instrument_id = "SWPN-USD-TEST1"
    _seed_swaption_trade(conn, "T-SWPN-1", instrument_id, quantity=10_000_000.0, strike=0.04)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 0.20)

    outcome = price_and_store(conn, AS_OF, "T-SWPN-1")
    assert outcome.priced, outcome.reason
    assert outcome.vol_source_kind == "FLAT"

    rows = conn.execute(
        "SELECT mark_type, value, source FROM marks WHERE as_of_date = ? AND instrument_id = ?",
        (AS_OF, instrument_id),
    ).fetchall()
    by_type = {r[0]: (r[1], r[2]) for r in rows}
    assert set(by_type) == {"PV_USD", "DV01_USD", "VEGA", "GAMMA", "THETA"}
    assert by_type["PV_USD"][1] == "QL_PRICER"
    assert by_type["DV01_USD"][1] == "QL_PRICER"
    assert by_type["VEGA"][1] == "QL_OPTIONS_PRICER"
    assert by_type["GAMMA"][1] == "QL_OPTIONS_PRICER"
    assert by_type["THETA"][1] == "QL_OPTIONS_PRICER"
    assert by_type["PV_USD"][0] > 0  # long payer, positive vol -> positive value

    official = conn.execute(
        "SELECT mark_type, source FROM marks_official WHERE as_of_date = ? AND instrument_id = ?",
        (AS_OF, instrument_id),
    ).fetchall()
    assert {r[0]: r[1] for r in official} == {
        "PV_USD": "QL_PRICER", "DV01_USD": "QL_PRICER",
        "VEGA": "QL_OPTIONS_PRICER", "GAMMA": "QL_OPTIONS_PRICER", "THETA": "QL_OPTIONS_PRICER",
    }


@needs_quantlib
def test_price_and_store_skips_with_no_vol():
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-NOVOL", "SWPN-NOVOL", quantity=1_000_000.0)
    outcome = price_and_store(conn, AS_OF, "T-NOVOL")
    assert not outcome.priced
    assert "no vol" in outcome.reason

    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = 'SWPN-NOVOL'").fetchone()[0] == 0


@needs_quantlib
def test_price_and_store_skips_with_normal_vol():
    from engine.rates_vol.inputs import set_manual_rate_vol
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-NORMAL", "SWPN-NORMAL", quantity=1_000_000.0)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 80.0, vol_type="NORMAL")

    outcome = price_and_store(conn, AS_OF, "T-NORMAL")
    assert not outcome.priced
    assert "NORMAL" in outcome.reason


@needs_quantlib
def test_price_and_store_skips_with_no_curve_quotes():
    from engine.rates_vol.inputs import set_manual_rate_vol
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()  # no _seed_curve call
    _seed_swaption_trade(conn, "T-NOCURVE", "SWPN-NOCURVE", quantity=1_000_000.0)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 0.20)

    outcome = price_and_store(conn, AS_OF, "T-NOCURVE")
    assert not outcome.priced
    assert "curve_quotes" in outcome.reason


@needs_quantlib
def test_price_and_store_skips_bermudan_with_no_hull_white_params():
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-BERM-NOPARAMS", "SWPN-BERM-1", quantity=1_000_000.0, payoff="BERMUDAN_SWAPTION")

    outcome = price_and_store(conn, AS_OF, "T-BERM-NOPARAMS")
    assert not outcome.priced
    assert "Hull-White" in outcome.reason


@needs_quantlib
def test_price_and_store_prices_bermudan_swaption_with_hull_white_params():
    from engine.rates_vol.inputs import set_rate_model_param
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    instrument_id = "SWPN-BERM-2"
    _seed_swaption_trade(conn, "T-BERM-1", instrument_id, quantity=1_000_000.0, payoff="BERMUDAN_SWAPTION")
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "a", 0.03)
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "sigma", 0.01)

    outcome = price_and_store(conn, AS_OF, "T-BERM-1")
    assert outcome.priced, outcome.reason
    rows = dict(conn.execute(
        "SELECT mark_type, value FROM marks WHERE as_of_date = ? AND instrument_id = ?", (AS_OF, instrument_id)
    ).fetchall())
    assert set(rows) == {"PV_USD", "DV01_USD", "VEGA", "GAMMA", "THETA"}


@needs_quantlib
def test_price_and_store_skips_unknown_payoff():
    from engine.rates_vol.store import price_and_store, ensure_instrument_rate_options_table

    conn = _new_conn()
    _seed_curve(conn)
    instrument_id = "SWPN-BADPAYOFF"
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "IRS_OPTION", "USD", "USD", 1.0, 0, instrument_id, "2031-08-17"),
    )
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("T-BADPAYOFF", "MANUAL", instrument_id, "SWAPTION", "T-BADPAYOFF", AS_OF, 1_000_000.0, 0.0,
         "A", "C", "S", "T", "desc"),
    )
    ensure_instrument_rate_options_table(conn)
    conn.execute(
        'INSERT INTO instrument_rate_options (instrument_id, payoff, option_type, strike, "index", '
        "underlying_start, underlying_end) VALUES (?,?,?,?,?,?,?)",
        (instrument_id, "BOGUS", "PAYER", 0.04, "SOFR", "2031-08-17", "2036-08-17"),
    )
    conn.commit()

    outcome = price_and_store(conn, AS_OF, "T-BADPAYOFF")
    assert not outcome.priced
    assert "unknown payoff" in outcome.reason


@needs_quantlib
def test_price_and_store_skips_zero_quantity():
    from engine.rates_vol.inputs import set_manual_rate_vol
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-ZERO", "SWPN-ZERO", quantity=0.0)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 0.20)

    outcome = price_and_store(conn, AS_OF, "T-ZERO")
    assert not outcome.priced
    assert "quantity is 0" in outcome.reason


@needs_quantlib
def test_write_instrument_rate_option_rejects_bad_option_type():
    from engine.rates_vol.store import write_instrument_rate_option

    conn = _new_conn()
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES ('X','IRS_OPTION','USD','USD',1.0,0,'X','2031-08-17')"
    )
    with pytest.raises(ValueError):
        write_instrument_rate_option(conn, "X", payoff="SWAPTION", strike=0.04, index="SOFR",
                                      underlying_start="2031-08-17", underlying_end="2036-08-17", option_type="")


@needs_quantlib
def test_price_all_and_store_prices_multiple_trades_sharing_one_curve():
    from engine.rates_vol.inputs import set_manual_rate_vol
    from engine.rates_vol.store import price_all_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-ALL-1", "SWPN-ALL-1", quantity=1_000_000.0)
    _seed_swaption_trade(conn, "T-ALL-2", "SWPN-ALL-2", quantity=-2_000_000.0, option_type="RECEIVER")
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 0.20)

    outcomes = price_all_and_store(conn, AS_OF)
    assert len(outcomes) == 2
    assert all(o.priced for o in outcomes)
