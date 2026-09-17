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
AS_OF_DATE = datetime.date.fromisoformat(AS_OF)
# Annual dates between the default _seed_swaption_trade window's underlying_start
# (2031-08-17) and underlying_end (2036-08-17), used by every Bermudan test below --
# store.py's own exercise-date parsing (not the vendored library's mechanically
# generated schedule, which this phase no longer uses -- see store.py::_parse_exercise_dates).
EXERCISE_DATES = "2031-08-17;2032-08-17;2033-08-17;2034-08-17;2035-08-17"


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
    exercise_dates: str = "",
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
            exercise_dates=exercise_dates,
        )


# --------------------------------------------------------------------------- pricer.py

def _flat_curve(rate: float, as_of_date: datetime.date = AS_OF_DATE):
    from engine.options.vendor.options_calc.rates._engine import build_curve
    from engine.rates import qlmap

    return build_curve(qlmap.ql_date(as_of_date), rate)


@needs_quantlib
def test_payer_minus_receiver_swaption_equals_forward_swap_npv():
    """The swaption analogue of put-call parity (MODELS.md's own verification for
    rates/swaption.py): payer - receiver == the forward-starting swap's OWN NPV, under
    a curve built from a single flat rate (both discount_curve and forecast_curve --
    single-curve OIS, this package's convention post-2026-09-17, see
    engine/rates_vol/__init__.py's "Curve inputs" section)."""
    from engine.options.vendor.options_calc.rates._engine import build_forward_swap
    from engine.rates import qlmap
    from engine.rates_vol import pricer

    rate = 0.0410
    fixed_rate, expiry_years, swap_tenor_years, notional = 0.0410, 5.0, 10.0, 1_000_000.0
    curve = _flat_curve(rate)

    payer = pricer.price_swaption(notional, fixed_rate, "PAYER", expiry_years, swap_tenor_years,
                                   curve, 0.20, AS_OF_DATE)
    receiver = pricer.price_swaption(notional, fixed_rate, "RECEIVER", expiry_years, swap_tenor_years,
                                      curve, 0.20, AS_OF_DATE)

    _, _, swap, _ = build_forward_swap(fixed_rate, expiry_years, int(swap_tenor_years), rate, notional,
                                        "payer", discount_curve=curve, forecast_curve=curve,
                                        evaluation_date=qlmap.ql_date(AS_OF_DATE))
    assert (payer.npv_total - receiver.npv_total) == pytest.approx(swap.NPV(), abs=1e-6)


@needs_quantlib
def test_long_and_short_swaption_have_opposite_signed_pv():
    from engine.rates_vol import pricer

    curve = _flat_curve(0.04)
    long_payer = pricer.price_swaption(1_000_000.0, 0.04, "PAYER", 5.0, 10.0, curve, 0.20, AS_OF_DATE)
    short_payer = pricer.price_swaption(-1_000_000.0, 0.04, "PAYER", 5.0, 10.0, curve, 0.20, AS_OF_DATE)
    assert long_payer.npv_total > 0
    assert short_payer.npv_total == pytest.approx(-long_payer.npv_total)

    long_receiver = pricer.price_swaption(1_000_000.0, 0.04, "RECEIVER", 5.0, 10.0, curve, 0.20, AS_OF_DATE)
    assert long_receiver.npv_total > 0  # a long option is always a positive value to its holder


@needs_quantlib
def test_price_swaption_dv01_sign_matches_engine_rates_convention():
    """DV01 (curve-spread bump) must be NPV(+1bp) - NPV(base), same sign convention as
    engine/rates/valuation.py::price_swap's dv01_parallel -- a long payer swaption gains
    value as rates rise, so its DV01 must be positive."""
    from engine.rates_vol import pricer

    curve = _flat_curve(0.04)
    long_payer = pricer.price_swaption(1_000_000.0, 0.04, "PAYER", 5.0, 10.0, curve, 0.20, AS_OF_DATE)
    assert long_payer.dv01 > 0

    long_receiver = pricer.price_swaption(1_000_000.0, 0.04, "RECEIVER", 5.0, 10.0, curve, 0.20, AS_OF_DATE)
    assert long_receiver.dv01 < 0  # a receiver LOSES value as rates rise


@needs_quantlib
def test_price_cap_matches_the_vendored_cap_floor_function_directly():
    """Locks our unit-notional pass-through: pricer.price_cap_floor must reproduce
    rates/cap_floor.price_cap's own 'price' field bit-for-bit at notional=1 (task's
    "cap = sum of caplets vs the vendored function" -- the vendored BlackCapFloorEngine
    itself sums the strip; this checks we did not disturb that sum in our wrapper), now
    with a curve object rather than a flat rate."""
    from engine.options.vendor.options_calc.rates import cap_floor as vendor_cap_floor
    from engine.rates import qlmap
    from engine.rates_vol import pricer

    strike, start, tenor, r, sigma = 0.04, 0.5, 5.0, 0.04, 0.20
    curve = _flat_curve(r)
    ours = pricer.price_cap_floor(2_000_000.0, "CAP", strike, start, tenor, curve, sigma, AS_OF_DATE, freq_months=6)
    vendor_raw = vendor_cap_floor.price_cap(strike, start, tenor, r, sigma, notional=1.0, freq_months=6,
                                             discount_curve=curve, forecast_curve=curve,
                                             evaluation_date=qlmap.ql_date(AS_OF_DATE))
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


def _exercise_dates_for(fixed_rate, first_exercise, swap_tenor, curve, exercise_frequency=1.0,
                         as_of_date: datetime.date = AS_OF_DATE):
    """Generate the same evenly-spaced exercise-date schedule the vendored engine's OWN
    mechanically-generated default would (`_generate_exercise_dates`) -- used here only
    to build a REALISTIC, valid explicit `exercise_dates` list for tests, mirroring how
    a real caller would derive one from a deal's own coupon schedule. Never used by
    store.py itself (see `_parse_exercise_dates` there -- store.py reads the trade's OWN
    staged dates, it does not generate them)."""
    import datetime as _dt

    from engine.options.vendor.options_calc.rates._engine import build_forward_swap
    from engine.options.vendor.options_calc.rates.bermudan_swaption import _generate_exercise_dates
    from engine.rates import qlmap

    eval_date = qlmap.ql_date(as_of_date)
    _, start_date, swap, _ = build_forward_swap(fixed_rate, first_exercise, int(swap_tenor), 0.0, 1.0, "payer",
                                                 discount_curve=curve, forecast_curve=curve, evaluation_date=eval_date)
    end_date = swap.fixedSchedule().endDate()
    ql_dates = _generate_exercise_dates(start_date, end_date, exercise_frequency)
    return [_dt.date(d.year(), d.month(), d.dayOfMonth()) for d in ql_dates]


@needs_quantlib
def test_bermudan_swaption_is_worth_at_least_the_european_under_the_same_hull_white_model():
    """The key sanity inequality (MODELS.md's own verification for
    bermudan_swaption.py): more exercise opportunities cannot destroy value, checked
    under the SAME Hull-White model + tree engine on both sides (comparing against
    swaption.py's different Black-76 model would be model-inconsistent -- see
    price_european_swaption_hw's docstring)."""
    from engine.options.vendor.options_calc.rates.bermudan_swaption import price_european_swaption_hw
    from engine.rates import qlmap
    from engine.rates_vol import pricer

    fixed_rate, expiry, swap_tenor, r = 0.04, 5.0, 10.0, 0.04
    hw_a, hw_sigma = 0.03, 0.01
    curve = _flat_curve(r)
    exercise_dates = _exercise_dates_for(fixed_rate, expiry, swap_tenor, curve)

    european_hw = price_european_swaption_hw(fixed_rate, expiry, int(swap_tenor), r, notional=1.0, option_type="payer",
                                              discount_curve=curve, forecast_curve=curve,
                                              evaluation_date=qlmap.ql_date(AS_OF_DATE),
                                              hw_mean_reversion=hw_a, hw_volatility=hw_sigma, tree_steps=80)
    bermudan = pricer.price_bermudan_swaption(1.0, fixed_rate, "PAYER", expiry, swap_tenor, exercise_dates,
                                               curve, hw_a, hw_sigma, AS_OF_DATE, tree_steps=80)
    assert bermudan.npv_total >= european_hw - 1e-6


@needs_quantlib
def test_bermudan_swaption_exercise_dates_actually_change_the_price():
    """A direct check that `exercise_dates` are genuinely consumed (task requirement),
    not silently ignored: a full annual schedule must be worth at least as much as a
    strict subset of it (fewer exercise opportunities cannot add value), and the two
    must price DIFFERENTLY -- proving the pricer is sensitive to which dates are staged,
    not just whether any are."""
    from engine.rates_vol import pricer

    fixed_rate, expiry, swap_tenor, r = 0.04, 5.0, 10.0, 0.04
    hw_a, hw_sigma = 0.03, 0.01
    curve = _flat_curve(r)
    full_schedule = _exercise_dates_for(fixed_rate, expiry, swap_tenor, curve)
    assert len(full_schedule) >= 4, "test needs room for a strict subset"
    subset = full_schedule[::2]

    full = pricer.price_bermudan_swaption(1.0, fixed_rate, "PAYER", expiry, swap_tenor, full_schedule,
                                           curve, hw_a, hw_sigma, AS_OF_DATE, tree_steps=80)
    fewer = pricer.price_bermudan_swaption(1.0, fixed_rate, "PAYER", expiry, swap_tenor, subset,
                                            curve, hw_a, hw_sigma, AS_OF_DATE, tree_steps=80)
    assert full.npv_total >= fewer.npv_total - 1e-6
    assert full.npv_total != pytest.approx(fewer.npv_total, rel=1e-6)


@needs_quantlib
def test_bermudan_swaption_rejects_implausible_hw_sigma():
    from engine.rates_vol import pricer

    curve = _flat_curve(0.04)
    exercise_dates = _exercise_dates_for(0.04, 5.0, 10.0, curve)
    with pytest.raises(ValueError):
        pricer.price_bermudan_swaption(1_000_000.0, 0.04, "PAYER", 5.0, 10.0, exercise_dates, curve,
                                        hw_mean_reversion=0.03, hw_sigma=1.0, as_of_date=AS_OF_DATE)


@needs_quantlib
def test_bermudan_swaption_rejects_empty_exercise_dates():
    from engine.rates_vol import pricer

    curve = _flat_curve(0.04)
    with pytest.raises(ValueError):
        pricer.price_bermudan_swaption(1_000_000.0, 0.04, "PAYER", 5.0, 10.0, [], curve,
                                        hw_mean_reversion=0.03, hw_sigma=0.01, as_of_date=AS_OF_DATE)


@needs_quantlib
def test_price_swaption_rejects_zero_quantity():
    from engine.rates_vol import pricer

    curve = _flat_curve(0.04)
    with pytest.raises(ValueError):
        pricer.price_swaption(0.0, 0.04, "PAYER", 5.0, 10.0, curve, 0.20, AS_OF_DATE)


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
    assert ci.forward_rate == pytest.approx(built.ql_swap.fairRate(), abs=1e-9)
    assert ci.curve is ci.curve_set.discount  # the SAME handle used for both discount_curve/forecast_curve


@needs_quantlib
def test_curve_input_atm_swaption_parity_and_differs_from_old_flat_approximation():
    """(1) Parity: strike the swaption at engine/rates's OWN forward par rate (the same
    fairRate() the OIS swap pricer itself would quote as the par rate) -- the payer and
    receiver swaptions, priced by this package's curve-input pricer, should come out
    close to each other (a forward swap struck at its own fair rate has ~zero NPV, so by
    the payer-minus-receiver-equals-forward-swap-NPV identity proven above, payer and
    receiver should nearly coincide; not bit-for-bit equal because engine/rates's own
    swap-building conventions -- OIS calendar/day count -- differ slightly from the
    vendored swaption engine's own simplified swap construction).
    (2) On this genuinely NON-FLAT fixture curve (ois_snapshot_v1.json's USD SOFR curve:
    short end ~5.3%, 5Y ~3.9%, 10Y ~3.98%), the REMOVED flat-rate approximation
    (inputs.py::derive_flat_rate_inputs, kept only as a fallback for this test) prices
    the SAME trade to a MATERIALLY DIFFERENT number than the curve-input pricer --
    proving the approximation is actually gone, not merely renamed."""
    from engine.options.vendor.options_calc.rates import swaption as vendor_swaption
    from engine.rates import qlmap
    from engine.rates.instruments import build_instrument
    from engine.rates_vol import pricer
    from engine.rates_vol.inputs import derive_curve_inputs, derive_flat_rate_inputs

    conn = _new_conn()
    _seed_curve(conn)
    expiry_date = datetime.date(2031, 8, 17)
    underlying_start, underlying_end = expiry_date, datetime.date(2041, 8, 17)  # 5Y-into-10Y

    ci = derive_curve_inputs(conn, AS_OF, "USD", "SOFR", expiry_date, underlying_start, underlying_end)
    assert ci is not None

    built = build_instrument(ci.curve_set, underlying_start, underlying_end,
                              fixed_rate=0.0, notional=1.0, pay_fixed=True)
    atm_rate = built.ql_swap.fairRate()
    assert atm_rate == pytest.approx(ci.forward_rate, abs=1e-9)

    payer = pricer.price_swaption(1_000_000.0, atm_rate, "PAYER", 5.0, 10.0, ci.curve, 0.20, AS_OF_DATE)
    receiver = pricer.price_swaption(1_000_000.0, atm_rate, "RECEIVER", 5.0, 10.0, ci.curve, 0.20, AS_OF_DATE)
    assert abs(payer.npv_total - receiver.npv_total) < 0.05 * abs(payer.npv_total)  # approximate ATM parity

    flat = derive_flat_rate_inputs(ci.curve_set, AS_OF_DATE, expiry_date, underlying_start, underlying_end)
    flat_result = vendor_swaption.price(
        atm_rate, 5.0, 10, r=flat.discount_rate, sigma=0.20, notional=1_000_000.0, option_type="payer",
        discount_rate=flat.discount_rate, forecast_rate=flat.forecast_rate, evaluation_date=qlmap.ql_date(AS_OF_DATE),
    )
    assert flat_result["price"] != pytest.approx(payer.npv_total, rel=1e-3)


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
def test_price_and_store_skips_bermudan_with_no_exercise_dates():
    """A Bermudan trade with NO staged exercise_dates is skipped, never mechanically
    generated -- checked BEFORE the Hull-White param check (a trade cannot need HW
    params if it has no exercise schedule to price against)."""
    from engine.rates_vol.inputs import set_rate_model_param
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-BERM-NODATES", "SWPN-BERM-NODATES", quantity=1_000_000.0,
                          payoff="BERMUDAN_SWAPTION")  # exercise_dates="" (default)
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "a", 0.03)
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "sigma", 0.01)

    outcome = price_and_store(conn, AS_OF, "T-BERM-NODATES")
    assert not outcome.priced
    assert outcome.reason == "no exercise dates"
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = 'SWPN-BERM-NODATES'").fetchone()[0] == 0


@needs_quantlib
def test_price_and_store_skips_bermudan_with_no_hull_white_params():
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    _seed_swaption_trade(conn, "T-BERM-NOPARAMS", "SWPN-BERM-1", quantity=1_000_000.0, payoff="BERMUDAN_SWAPTION",
                          exercise_dates=EXERCISE_DATES)

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
    _seed_swaption_trade(conn, "T-BERM-1", instrument_id, quantity=1_000_000.0, payoff="BERMUDAN_SWAPTION",
                          exercise_dates=EXERCISE_DATES)
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "a", 0.03)
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "sigma", 0.01)

    outcome = price_and_store(conn, AS_OF, "T-BERM-1")
    assert outcome.priced, outcome.reason
    rows = dict(conn.execute(
        "SELECT mark_type, value FROM marks WHERE as_of_date = ? AND instrument_id = ?", (AS_OF, instrument_id)
    ).fetchall())
    assert set(rows) == {"PV_USD", "DV01_USD", "VEGA", "GAMMA", "THETA"}


@needs_quantlib
def test_price_and_store_prefers_manual_hull_white_params_over_calibrated():
    """inputs.py::get_rate_model_params's MANUAL-over-CALIBRATED preference, exercised
    through the full store.py pricing path: a CALIBRATED sigma staged alongside a MANUAL
    one must be ignored."""
    from engine.rates_vol.inputs import set_rate_model_param
    from engine.rates_vol.store import price_and_store

    conn = _new_conn()
    _seed_curve(conn)
    instrument_id = "SWPN-BERM-PREF"
    _seed_swaption_trade(conn, "T-BERM-PREF", instrument_id, quantity=1_000_000.0, payoff="BERMUDAN_SWAPTION",
                          exercise_dates=EXERCISE_DATES)
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "a", 0.03, source="MANUAL")
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "sigma", 0.01, source="MANUAL")
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "a", 0.10, source="CALIBRATED")
    set_rate_model_param(conn, AS_OF, "USD", "SOFR", "HULL_WHITE", "sigma", 0.049, source="CALIBRATED")

    manual_outcome = price_and_store(conn, AS_OF, "T-BERM-PREF")
    assert manual_outcome.priced, manual_outcome.reason

    conn2 = _new_conn()
    _seed_curve(conn2)
    _seed_swaption_trade(conn2, "T-BERM-PREF", instrument_id, quantity=1_000_000.0, payoff="BERMUDAN_SWAPTION",
                          exercise_dates=EXERCISE_DATES)
    set_rate_model_param(conn2, AS_OF, "USD", "SOFR", "HULL_WHITE", "a", 0.10, source="CALIBRATED")
    set_rate_model_param(conn2, AS_OF, "USD", "SOFR", "HULL_WHITE", "sigma", 0.049, source="CALIBRATED")
    calibrated_only_outcome = price_and_store(conn2, AS_OF, "T-BERM-PREF")
    assert calibrated_only_outcome.priced, calibrated_only_outcome.reason

    # different (a, sigma) actually used -> different price, proving MANUAL really won
    manual_pv = dict(conn.execute(
        "SELECT mark_type, value FROM marks WHERE as_of_date=? AND instrument_id=?", (AS_OF, instrument_id)
    ).fetchall())["PV_USD"]
    calibrated_pv = dict(conn2.execute(
        "SELECT mark_type, value FROM marks WHERE as_of_date=? AND instrument_id=?", (AS_OF, instrument_id)
    ).fetchall())["PV_USD"]
    assert manual_pv != pytest.approx(calibrated_pv, rel=1e-6)


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


# --------------------------------------------------------------------------- calibration.py -- SABR

@needs_quantlib
def test_calibrate_sabr_recovers_known_parameters_from_generated_quotes():
    """Round-trip: generate strike/vol quotes from KNOWN SABR params via the vendored
    sabr_swaption_vol, stage them as rate_vols, then check calibrate_sabr recovers the
    same (alpha, rho, nu) within tight tolerance -- beta is fixed/given, not fit."""
    from engine.options.vendor.options_calc.rates.sabr import sabr_swaption_vol
    from engine.rates_vol.calibration import calibrate_sabr, _tenor_years
    from engine.rates_vol.inputs import _forward_par_rate, _read_curve_quotes, get_rate_model_params, set_manual_rate_vol
    from engine.rates.curves import build_curve_set

    conn = _new_conn()
    _seed_curve(conn)

    expiry_iso, underlying_tenor = "2031-08-17", "10Y"
    expiry_date = datetime.date.fromisoformat(expiry_iso)
    underlying_end = expiry_date + datetime.timedelta(days=round(_tenor_years(underlying_tenor) * 365.25))
    quotes, _src = _read_curve_quotes(conn, AS_OF, "USD", "SOFR")
    curve_set = build_curve_set(quotes, AS_OF_DATE, "USD", "SOFR")
    # forward MUST match exactly what calibrate_sabr will independently compute from the
    # same curve -- otherwise the 'ATM' quote generated below isn't actually at-the-money
    # from calibrate_sabr's point of view, and the fit compensates via skew (rho).
    forward = _forward_par_rate(curve_set, AS_OF_DATE, expiry_date, underlying_end)
    expiry_years = 5.0

    alpha0, beta, rho0, nu0 = 0.05, 0.5, -0.25, 0.35
    strikes = [forward - 0.025, forward - 0.015, forward, forward + 0.015, forward + 0.035]

    for k in strikes:
        vol = sabr_swaption_vol(k, forward, expiry_years, alpha0, beta, rho0, nu0)
        key = "ATM" if k == forward else k
        set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor, key, vol, vol_type="LOGNORMAL")

    result = calibrate_sabr(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor, beta=beta)
    assert result.success, result.reason
    assert result.alpha == pytest.approx(alpha0, rel=0.05)
    assert result.rho == pytest.approx(rho0, abs=0.05)
    assert result.nu == pytest.approx(nu0, rel=0.10)
    assert result.rmse < 1e-4

    staged = get_rate_model_params(conn, AS_OF, "USD", "SOFR", "SABR")
    assert staged["alpha"] == pytest.approx(result.alpha)
    assert staged["beta"] == pytest.approx(beta)
    assert staged["rho"] == pytest.approx(result.rho)
    assert staged["nu"] == pytest.approx(result.nu)

    rows = conn.execute(
        "SELECT DISTINCT source FROM rate_model_params WHERE as_of_date=? AND ccy=? AND model='SABR'",
        (AS_OF, "USD"),
    ).fetchall()
    assert rows == [("CALIBRATED",)]


@needs_quantlib
def test_calibrate_sabr_skips_with_insufficient_strikes():
    from engine.rates_vol.calibration import calibrate_sabr
    from engine.rates_vol.inputs import ensure_rate_model_params_table, set_manual_rate_vol

    conn = _new_conn()
    _seed_curve(conn)
    expiry_iso, underlying_tenor = "2031-08-17", "10Y"
    # Only 2 strikes staged (one of them ATM) -- below the >= 3 requirement.
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor, "ATM", 0.20)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor, 0.03, 0.22)

    result = calibrate_sabr(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor)
    assert not result.success
    assert "strike" in result.reason

    ensure_rate_model_params_table(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM rate_model_params WHERE as_of_date=? AND ccy=? AND model='SABR'", (AS_OF, "USD")
    ).fetchone()[0] == 0


@needs_quantlib
def test_calibrate_sabr_skips_without_atm_anchor():
    from engine.rates_vol.calibration import calibrate_sabr
    from engine.rates_vol.inputs import set_manual_rate_vol

    conn = _new_conn()
    _seed_curve(conn)
    expiry_iso, underlying_tenor = "2031-08-17", "10Y"
    # 3 off-ATM strikes, no 'ATM'-keyed row.
    for k, v in [(0.02, 0.24), (0.03, 0.22), (0.06, 0.21)]:
        set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor, k, v)

    result = calibrate_sabr(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor)
    assert not result.success
    assert "ATM" in result.reason


@needs_quantlib
def test_calibrate_sabr_skips_without_curve_quotes():
    from engine.rates_vol.calibration import calibrate_sabr
    from engine.rates_vol.inputs import set_manual_rate_vol

    conn = _new_conn()  # no _seed_curve
    expiry_iso, underlying_tenor = "2031-08-17", "10Y"
    for k, v in [("ATM", 0.20), (0.02, 0.24), (0.06, 0.21)]:
        set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor, k, v)

    result = calibrate_sabr(conn, AS_OF, "USD", "SOFR", expiry_iso, underlying_tenor)
    assert not result.success
    assert "curve_quotes" in result.reason


# --------------------------------------------------------------------------- calibration.py -- Hull-White

@needs_quantlib
def test_calibrate_hull_white_recovers_sigma_within_10pct_from_synthetic_grid():
    """Round-trip on the fixture curve: price a small ATM swaption grid under a KNOWN
    (a0, sigma0) via QuantLib's own Jamshidian engine (the same closed-form HW European
    engine calibrate_hull_white itself calibrates against), stage those as ATM rate_vols,
    then check calibrate_hull_white recovers sigma within 10% and stays in the vendored
    engine's plausible (0, 0.05] range."""
    import QuantLib as ql

    from engine.rates import qlmap
    from engine.rates.curves import build_curve_set
    from engine.rates_vol.calibration import calibrate_hull_white
    from engine.rates_vol.inputs import _read_curve_quotes, get_rate_model_params, set_manual_rate_vol

    conn = _new_conn()
    _seed_curve(conn)

    quotes, _source = _read_curve_quotes(conn, AS_OF, "USD", "SOFR")
    curve_set = build_curve_set(quotes, AS_OF_DATE, "USD", "SOFR")
    ql.Settings.instance().evaluationDate = qlmap.ql_date(AS_OF_DATE)
    curve_handle = curve_set.discount

    from engine.options.vendor.options_calc.rates._engine import build_index

    gen_index = build_index(curve_handle)
    a0, sigma0 = 0.03, 0.012
    true_model = ql.HullWhite(curve_handle, a0, sigma0)
    true_engine = ql.JamshidianSwaptionEngine(true_model)

    grid = [(1, 10, "2027-08-17"), (3, 10, "2029-08-17"), (5, 10, "2031-08-17"), (7, 5, "2033-08-17")]
    for expiry_years, tenor_years, expiry_iso in grid:
        vol_quote = ql.SimpleQuote(0.20)
        helper = ql.SwaptionHelper(
            ql.Period(expiry_years, ql.Years), ql.Period(tenor_years, ql.Years), ql.QuoteHandle(vol_quote),
            gen_index, ql.Period(1, ql.Years), ql.Actual365Fixed(), ql.Actual365Fixed(), curve_handle,
        )
        helper.setPricingEngine(true_engine)
        target_price = helper.modelValue()
        implied = helper.impliedVolatility(target_price, 1e-6, 1000, 1e-4, 2.0)
        set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", expiry_iso, f"{tenor_years}Y", "ATM", implied,
                             vol_type="LOGNORMAL")

    result = calibrate_hull_white(conn, AS_OF, "USD", "SOFR")
    assert result.success, result.reason
    assert result.sigma == pytest.approx(sigma0, rel=0.10)
    assert 0 < result.sigma <= 0.05
    assert result.n_helpers == len(grid)

    staged = get_rate_model_params(conn, AS_OF, "USD", "SOFR", "HULL_WHITE")
    assert staged["a"] == pytest.approx(result.a)
    assert staged["sigma"] == pytest.approx(result.sigma)
    rows = conn.execute(
        "SELECT DISTINCT source FROM rate_model_params WHERE as_of_date=? AND ccy=? AND model='HULL_WHITE'",
        (AS_OF, "USD"),
    ).fetchall()
    assert rows == [("CALIBRATED",)]


@needs_quantlib
def test_calibrate_hull_white_skips_with_fewer_than_two_grid_points():
    from engine.rates_vol.calibration import calibrate_hull_white
    from engine.rates_vol.inputs import set_manual_rate_vol

    conn = _new_conn()
    _seed_curve(conn)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "10Y", "ATM", 0.20)

    result = calibrate_hull_white(conn, AS_OF, "USD", "SOFR")
    assert not result.success
    assert "2" in result.reason or "grid" in result.reason

    assert conn.execute(
        "SELECT COUNT(*) FROM rate_model_params WHERE as_of_date=? AND ccy=? AND model='HULL_WHITE'", (AS_OF, "USD")
    ).fetchone()[0] == 0


@needs_quantlib
def test_calibrate_hull_white_skips_without_curve_quotes():
    from engine.rates_vol.calibration import calibrate_hull_white
    from engine.rates_vol.inputs import set_manual_rate_vol

    conn = _new_conn()  # no _seed_curve
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "10Y", "ATM", 0.20)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2033-08-17", "5Y", "ATM", 0.19)

    result = calibrate_hull_white(conn, AS_OF, "USD", "SOFR")
    assert not result.success
    assert "curve_quotes" in result.reason


# --------------------------------------------------------------------------- book.py CLI

def _run_book_main(argv):
    from engine.rates_vol.book import main

    return main(argv)


@needs_quantlib
def test_book_cli_books_and_prices_a_swaption_end_to_end(tmp_path):
    from data.ingest.schema import connect as schema_connect
    from engine.rates_vol.inputs import set_manual_rate_vol
    from engine.rates_vol.store import price_and_store

    db_path = str(tmp_path / "book_test.db")
    conn = schema_connect(db_path)
    conn.close()

    rc = _run_book_main([
        "--db", db_path, "--trade-id", "T-CLI-1", "--payoff", "SWAPTION",
        "--ccy", "USD", "--index", "SOFR", "--notional", "10000000", "--long",
        "--side", "PAYER", "--strike", "0.04",
        "--expiry", "2031-08-17", "--start", "2031-08-17", "--end", "2036-08-17",
    ])
    assert rc == 0

    conn = sqlite3.connect(db_path)
    trade_row = conn.execute(
        "SELECT source, product, quantity, instrument_id FROM trades WHERE trade_id = ?", ("T-CLI-1",)
    ).fetchone()
    assert trade_row == ("MANUAL", "SWAPTION", 10_000_000.0, "T-CLI-1")

    leg_row = conn.execute(
        "SELECT leg_type, ccy, amount, settles_cash FROM trade_legs WHERE trade_id = ?", ("T-CLI-1",)
    ).fetchone()
    assert leg_row == ("NOTIONAL", "USD", 10_000_000.0, 0)

    _seed_curve(conn)
    set_manual_rate_vol(conn, AS_OF, "USD", "SOFR", "2031-08-17", "5Y", "ATM", 0.20)
    outcome = price_and_store(conn, AS_OF, "T-CLI-1")
    assert outcome.priced, outcome.reason
    assert outcome.result.npv_total > 0  # long payer, positive vol -> positive value


@needs_quantlib
def test_book_cli_refuses_to_overwrite_existing_trade_id(tmp_path):
    from data.ingest.schema import connect as schema_connect

    db_path = str(tmp_path / "book_test_dup.db")
    conn = schema_connect(db_path)
    conn.close()

    base_args = [
        "--db", db_path, "--trade-id", "T-CLI-DUP", "--payoff", "CAP",
        "--ccy", "USD", "--index", "SOFR", "--notional", "5000000", "--long",
        "--strike", "0.04", "--expiry", "2031-08-17", "--start", "2026-08-17", "--end", "2031-08-17",
    ]
    assert _run_book_main(base_args) == 0
    assert _run_book_main(base_args) == 1  # second call must refuse, not overwrite

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE trade_id = 'T-CLI-DUP'").fetchone()[0] == 1


@needs_quantlib
def test_book_cli_requires_side_for_swaption_and_rejects_it_for_cap(tmp_path):
    db_path = str(tmp_path / "book_test_side.db")
    from data.ingest.schema import connect as schema_connect

    conn = schema_connect(db_path)
    conn.close()

    # SWAPTION with no --side -> refused (BookingError -> exit code 1, not a raised
    # SystemExit -- main() only ever raises via argparse's own required/choices errors).
    missing_side_rc = _run_book_main([
        "--db", db_path, "--trade-id", "T-X", "--payoff", "SWAPTION",
        "--ccy", "USD", "--index", "SOFR", "--notional", "1000000", "--long",
        "--strike", "0.04", "--expiry", "2031-08-17", "--start", "2031-08-17", "--end", "2036-08-17",
    ])
    assert missing_side_rc == 1
    assert conn_count_trades(db_path) == 0

    # CAP with --side given -> also refused.
    side_on_cap_rc = _run_book_main([
        "--db", db_path, "--trade-id", "T-Y", "--payoff", "CAP",
        "--ccy", "USD", "--index", "SOFR", "--notional", "1000000", "--long", "--side", "PAYER",
        "--strike", "0.04", "--expiry", "2031-08-17", "--start", "2031-08-17", "--end", "2036-08-17",
    ])
    assert side_on_cap_rc == 1
    assert conn_count_trades(db_path) == 0


def conn_count_trades(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()


@needs_quantlib
def test_book_cli_requires_exercise_dates_for_bermudan(tmp_path):
    db_path = str(tmp_path / "book_test_berm.db")
    from data.ingest.schema import connect as schema_connect

    conn = schema_connect(db_path)
    conn.close()

    rc = _run_book_main([
        "--db", db_path, "--trade-id", "T-BERM-CLI", "--payoff", "BERMUDAN_SWAPTION",
        "--ccy", "USD", "--index", "SOFR", "--notional", "1000000", "--long", "--side", "PAYER",
        "--strike", "0.04", "--expiry", "2031-08-17", "--start", "2031-08-17", "--end", "2036-08-17",
    ])
    assert rc == 1
