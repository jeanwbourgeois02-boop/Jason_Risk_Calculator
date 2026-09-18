"""engine/options/ tests, owned by options-pricer.

Phase 0: the vendored library is in place but nothing is wired to the schema
yet, so those tests only check the vendor copy imports cleanly. Phase 2
lands FX vanilla/digital pricing + PREMIUM/DELTA marks; Phase 4 lands
American/Asian/barrier/one-touch/no-touch payoffs plus multi-leg structure
combination; Phase 5b lands smile-vol resolution; Phase 7 lands real OIS
rates, calendar-aware year fractions/delta conventions, equity/commodity
pricing and Position/Portfolio aggregation. Follows test_rates_pricing.py's
skip-if-QuantLib-absent convention: every test that needs QuantLib is
marked @needs_quantlib rather than failing outright when it's not
installed.

**Fixture pair note (Phase 7).** The module-level `EURUSD` constant (name
kept from the original EURSEK fixture for git-history continuity) is used
throughout as the default FX_OPTION test pair. It changed from EURSEK to
EURUSD when real OIS-curve rates replaced the illustrative placeholder
(engine/options/rates.py): SEK has no OIS convention anywhere in this
codebase (engine/rates/conventions.py::CCY_RFR), so an EURSEK trade with no
manual_rates entry can never resolve real rates and always skips
"no curve/rate SEK" -- see test_no_curve_skip_reason_for_currency_without_ois_convention
below, which exercises exactly that gap deliberately. EURUSD's own
currencies (EUR, USD) are both covered by CCY_RFR, so it stays the default
fixture even though EURSEK options can now be priced too, via
`set_manual_rate` (Phase 7.1, 2026-09-17) -- see the
`test_manual_rate_*` tests below.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

import pytest

try:
    import QuantLib  # noqa: F401
    HAVE_QUANTLIB = True
except ImportError:
    HAVE_QUANTLIB = False

needs_quantlib = pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")

from data.ingest.schema import create_schema


def test_engine_options_package_imports():
    import engine.options  # noqa: F401


@needs_quantlib
def test_vendored_options_calc_imports():
    import engine.options.vendor.options_calc as options_calc
    assert options_calc.__file__.replace("\\", "/").endswith(
        "engine/options/vendor/options_calc/__init__.py"
    )


@needs_quantlib
def test_vendored_fx_and_rates_subpackages_import():
    from engine.options.vendor.options_calc import fx, rates, equity, commodity
    assert fx and rates and equity and commodity


# --------------------------------------------------------------------------- fixtures

EURUSD = "EURUSD"
AS_OF = "2026-08-21"
SPOT = 11.06
STRIKE = 11.0584
EXPIRY = "2026-09-23"
NOTIONAL = 35_000_000.0
PREMIUM_FILL = 0.00579
VOL = 0.06


def _new_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


# OIS convention / flat-rate table for test curve seeding -- currencies not
# listed here (e.g. SEK, NOK -- no OIS convention anywhere in this codebase,
# see engine/rates/conventions.py::CCY_RFR) are deliberately left unseedable,
# so a pair using one of them exercises the real "no curve <CCY>" skip path.
_OIS_INDEX = {"USD": "SOFR", "EUR": "ESTR", "GBP": "SONIA", "JPY": "TONA",
              "CHF": "SARON", "CAD": "CORRA", "AUD": "AONIA"}
_FLAT_OIS_RATE = {"USD": 0.045, "EUR": 0.030, "GBP": 0.040, "JPY": 0.003,
                   "CHF": 0.010, "CAD": 0.035, "AUD": 0.038}


def _seed_ois_curve(conn, as_of, ccy, rate=None, source="BBG_BDP"):
    """Seed a flat curve_quotes snapshot for one currency -- the same table
    engine/options/rates.py::build_curve_set reads, so store.py's dispatch
    resolves REAL (bootstrapped, not illustrative) domestic/foreign rates.
    No-op (returns False) for a currency with no OIS convention."""
    from data.bloomberg.rates_marketdata import ensure_curve_quotes_table

    index = _OIS_INDEX.get(ccy)
    if index is None:
        return False
    rate = _FLAT_OIS_RATE[ccy] if rate is None else rate
    ensure_curve_quotes_table(conn)
    rows = [
        (as_of, ccy, index, tenor, f"{ccy}{tenor}", rate, "OIS", "PX_LAST", source)
        for tenor in ("1M", "1Y", "2Y")
    ]
    conn.executemany(
        'INSERT OR REPLACE INTO curve_quotes (as_of_date, ccy, "index", tenor, ticker, value, quote_type, field, source) '
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return True


def _seed_ois_curves_for_pair(conn, as_of, pair):
    _seed_ois_curve(conn, as_of, pair[:3])
    _seed_ois_curve(conn, as_of, pair[3:])


def _seed_pair_spot(conn, as_of=AS_OF, pair=EURUSD, spot=SPOT):
    """The plain FX pair instrument + its official SPOT mark, plus flat OIS
    curve_quotes for both currencies (see _seed_ois_curves_for_pair) so
    store.py's real-rates path (engine/options/rates.py) resolves."""
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (pair, "FX", pair[:3], pair[3:], 1.0, 0, f"{pair} Curncy", "9999-12-31"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (as_of, pair, as_of, "SPOT", spot, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"),
    )
    _seed_ois_curves_for_pair(conn, as_of, pair)
    conn.commit()


def _seed_option_trade(
    conn,
    trade_id="T1",
    instrument_id="EURUSD092326C-197727826",
    pair=EURUSD,
    strike=STRIKE,
    option_type="CALL",
    payoff="VANILLA",
    barrier_level=0.0,
    avg_start_date="9999-12-31",
    expiry=EXPIRY,
    quantity=NOTIONAL,
    price=PREMIUM_FILL,
    package_id=None,
):
    base_ccy, quote_ccy = pair[:3], pair[3:]
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "FX_OPTION", base_ccy, quote_ccy, 1.0, 0, instrument_id, expiry),
    )
    conn.execute(
        "INSERT OR REPLACE INTO instrument_options VALUES (?,?,?,?,?,?)",
        (instrument_id, strike, option_type, barrier_level, avg_start_date, payoff),
    )
    conn.execute(
        "INSERT OR REPLACE INTO trades "
        "(trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
        "account, counterparty, strategy, trader, description, theme) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            trade_id, "XLSX", instrument_id, "FX_OPTION", package_id or trade_id, "2026-08-18",
            quantity, price, "BNPP-IPBFX-NMMF", "BNP", "", "JB", "",  "",
        ),
    )
    conn.commit()
    return instrument_id


def _seed_vol(conn, pair=EURUSD, expiry=EXPIRY, vol=VOL, as_of=AS_OF):
    from engine.options.inputs import set_manual_vol

    set_manual_vol(conn, as_of, pair, expiry, vol)


# --------------------------------------------------------------------------- Phase 5b: vol_quotes / smile fixtures

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"
SMILE_AS_OF = "2026-09-17"  # matches the fixture's own as_of


def _seed_vol_quotes(conn, pairs=("EURUSD", "USDJPY"), as_of=SMILE_AS_OF):
    """Seed vol_quotes from the checked-in fixture via VolFileSource +
    write_vol_quotes -- the same path a real --file pull would take."""
    from data.bloomberg.vol_marketdata import VolFileSource, write_vol_quotes

    source = VolFileSource(str(FIXTURE_PATH))
    result = source.get_vol_quotes(list(pairs), as_of=datetime.date.fromisoformat(as_of))
    write_vol_quotes(conn, result, as_of_date=as_of, source="BBG_BDP")


def _full_setup(conn, **trade_kwargs):
    _seed_pair_spot(conn)
    instrument_id = _seed_option_trade(conn, **trade_kwargs)
    _seed_vol(conn)
    return instrument_id


# --------------------------------------------------------------------------- pricer.py

@needs_quantlib
def test_put_call_parity_on_vendored_wrapper():
    from engine.options.pricer import price_fx_vanilla

    as_of = datetime.date(2026, 8, 21)
    expiry = datetime.date(2026, 9, 23)
    S, K, dr, fr, vol = 11.06, 11.0584, 0.03, 0.035, 0.06

    call = price_fx_vanilla(S, K, expiry, as_of, dr, fr, vol, "call")
    put = price_fx_vanilla(S, K, expiry, as_of, dr, fr, vol, "put")

    T = (expiry - as_of).days / 365.0
    # Put-call parity for Garman-Kohlhagen: C - P = S*exp(-fr*T) - K*exp(-dr*T)
    import math
    expected = S * math.exp(-fr * T) - K * math.exp(-dr * T)
    assert (call.quote_price - put.quote_price) == pytest.approx(expected, abs=1e-6)


@needs_quantlib
def test_premium_unit_conversion_matches_quote_price_over_spot():
    from engine.options.pricer import price_fx_vanilla

    as_of = datetime.date(2026, 8, 21)
    expiry = datetime.date(2026, 9, 23)
    S = 11.06
    result = price_fx_vanilla(S, 11.0584, expiry, as_of, 0.03, 0.035, 0.06, "call")

    assert result.premium == pytest.approx(result.quote_price / S)


@needs_quantlib
def test_long_call_delta_positive_long_put_delta_negative():
    from engine.options.pricer import price_fx_vanilla

    as_of = datetime.date(2026, 8, 21)
    expiry = datetime.date(2026, 9, 23)
    call = price_fx_vanilla(11.06, 11.0584, expiry, as_of, 0.03, 0.035, 0.06, "call")
    put = price_fx_vanilla(11.06, 11.0584, expiry, as_of, 0.03, 0.035, 0.06, "put")

    assert call.delta > 0
    assert put.delta < 0


@needs_quantlib
def test_year_fraction_rejects_expiry_not_after_as_of():
    from engine.options.pricer import year_fraction

    d = datetime.date(2026, 8, 21)
    with pytest.raises(ValueError):
        year_fraction(d, d)


# --------------------------------------------------------------------------- store.py end-to-end

@needs_quantlib
def test_price_and_store_writes_premium_and_delta_marks_visible_via_marks_official():
    from engine.options.store import price_and_store

    conn = _new_db()
    instrument_id = _full_setup(conn)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result is not None
    assert outcome.result.delta > 0  # long call

    # All six pricer outputs are official from QL_OPTIONS_PRICER (schema.py
    # OFFICIAL_MARK_SOURCE, extended with the Greeks 2026-09-17) and so are
    # visible via marks_official.
    official_rows = dict(
        conn.execute(
            "SELECT mark_type, value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? "
            "AND settle_date = ?",
            (AS_OF, instrument_id, EXPIRY),
        ).fetchall()
    )
    # The six pricer outputs are always official; DELTA_PA joins them (also
    # QL_OPTIONS_PRICER, per data/ingest/schema.py's OFFICIAL_MARK_SOURCE)
    # only for a premium-adjusted-convention pair -- EURUSD is one (see
    # test_delta_pa_written_only_for_premium_adjusted_pair below) -- so
    # assert the six as a floor and DELTA_PA as the only allowed extra,
    # rather than an exact set that breaks the moment a pair's convention
    # changes.
    assert {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"} <= set(official_rows)
    assert set(official_rows) <= {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO", "DELTA_PA"}

    rows = dict(
        conn.execute(
            "SELECT mark_type, value FROM marks WHERE as_of_date = ? AND instrument_id = ? "
            "AND settle_date = ? AND source = 'QL_OPTIONS_PRICER'",
            (AS_OF, instrument_id, EXPIRY),
        ).fetchall()
    )
    assert set(rows) >= {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"}

    sources = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM marks WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'PREMIUM'",
            (AS_OF, instrument_id),
        ).fetchall()
    }
    assert sources == {"QL_OPTIONS_PRICER"}

    # Premium mark should be in the same "fraction of base notional" unit as
    # the blotter's own fill (0.00579) -- same order of magnitude, not an
    # exact match (fill != today's fair value).
    assert 0.0 < rows["PREMIUM"] < 0.05


@needs_quantlib
def test_price_all_and_store_covers_all_fx_option_trades():
    from engine.options.store import price_all_and_store

    conn = _new_db()
    _full_setup(conn, trade_id="T1", instrument_id="EURUSD092326C-1")
    _seed_option_trade(
        conn, trade_id="T2", instrument_id="EURUSD092326P-2", option_type="PUT",
    )

    outcomes = price_all_and_store(conn, AS_OF)
    assert {o.trade_id for o in outcomes} == {"T1", "T2"}
    assert all(o.priced for o in outcomes)


def test_skipped_when_strike_is_zero():
    """No QuantLib needed for the skip path itself, but price_and_store still
    imports pricer.py lazily inside _dispatch, which is only reached once
    inputs resolve -- the strike check short-circuits before that, so this
    test does not require QuantLib. Still gated for consistency/documentation."""
    if not HAVE_QUANTLIB:
        pytest.skip("QuantLib not installed")
    from engine.options.store import price_and_store

    conn = _new_db()
    _full_setup(conn, strike=0.0)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert not outcome.priced
    assert "strike is 0" in outcome.reason

    n_marks = conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
    assert n_marks == 1  # only the seeded SPOT mark; nothing written for the option


@needs_quantlib
def test_skipped_when_no_spot_mark():
    from engine.options.store import price_and_store

    conn = _new_db()
    # No _seed_pair_spot call -- SPOT mark is missing.
    _seed_option_trade(conn)
    _seed_vol(conn)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert not outcome.priced
    assert outcome.reason == "no SPOT mark"


@needs_quantlib
def test_skipped_when_no_vol():
    from engine.options.store import price_and_store

    conn = _new_db()
    _seed_pair_spot(conn)
    _seed_option_trade(conn)
    # No _seed_vol call.

    outcome = price_and_store(conn, AS_OF, "T1")
    assert not outcome.priced
    assert outcome.reason == "no vol"


@needs_quantlib
def test_manual_vol_flat_fallback():
    from engine.options.inputs import set_manual_vol, get_manual_vol, FLAT_TENOR

    conn = _new_db()
    set_manual_vol(conn, AS_OF, EURUSD, FLAT_TENOR, 0.07)
    assert get_manual_vol(conn, AS_OF, EURUSD, "2027-01-01") == pytest.approx(0.07)

    set_manual_vol(conn, AS_OF, EURUSD, "2027-01-01", 0.09)
    assert get_manual_vol(conn, AS_OF, EURUSD, "2027-01-01") == pytest.approx(0.09)
    assert get_manual_vol(conn, AS_OF, EURUSD, "2027-06-01") == pytest.approx(0.07)


# --------------------------------------------------------------------------- Phase 4: remaining payoffs

@needs_quantlib
def test_american_payoff_prices_and_stores():
    from engine.options.store import price_and_store

    conn = _new_db()
    _full_setup(conn, payoff="AMERICAN")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result.premium > 0


@needs_quantlib
def test_asian_payoff_prices_and_stores():
    from engine.options.store import price_and_store

    conn = _new_db()
    _full_setup(conn, payoff="ASIAN")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result.premium > 0


@needs_quantlib
def test_barrier_knock_out_payoff_derives_up_direction_and_prices():
    from engine.options.store import price_and_store

    conn = _new_db()
    # Barrier above spot (11.06) -> derived direction should be "up".
    _full_setup(conn, payoff="BARRIER_KO", barrier_level=12.0)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    # A knock-out barrier vanilla must be worth no more than the equivalent
    # European vanilla (it can only extinguish value, never add to it).
    # Uses the SAME real curve-derived rates and calendar-aware T as the
    # outcome above (both via `pair=EURUSD`) so the comparison is
    # apples-to-apples -- a stale illustrative-rate comparison would not be.
    from engine.options.pricer import price_fx_vanilla
    import datetime as dt
    dom, for_ = _dom_for_rates(conn)
    vanilla = price_fx_vanilla(
        SPOT, STRIKE, dt.date(2026, 9, 23), dt.date(2026, 8, 21), dom, for_, VOL, "call", pair=EURUSD,
    )
    assert outcome.result.quote_price <= vanilla.quote_price + 1e-9


def _dom_for_rates(conn, as_of=AS_OF, pair=EURUSD, expiry_iso=EXPIRY):
    from engine.options.rates import resolve_fx_rates

    result, reason = resolve_fx_rates(conn, as_of, pair, expiry_iso)
    assert result is not None, reason
    return result.domestic_rate, result.foreign_rate


@needs_quantlib
def test_one_touch_and_no_touch_are_complementary():
    from engine.options.store import price_and_store

    conn_ot = _new_db()
    _full_setup(conn_ot, payoff="ONE_TOUCH", barrier_level=12.0)
    outcome_ot = price_and_store(conn_ot, AS_OF, "T1")
    assert outcome_ot.priced, outcome_ot.reason

    conn_nt = _new_db()
    _full_setup(conn_nt, payoff="NO_TOUCH", barrier_level=12.0)
    outcome_nt = price_and_store(conn_nt, AS_OF, "T1")
    assert outcome_nt.priced, outcome_nt.reason

    # One-touch + no-touch (same barrier/cash_payout) must sum to the
    # discounted cash payout (default cash_payout=1.0) -- exactly one of
    # "touched" / "never touched" happens.
    assert outcome_ot.result.quote_price + outcome_nt.result.quote_price == pytest.approx(1.0, abs=0.05)


@needs_quantlib
def test_skipped_when_barrier_level_is_zero():
    from engine.options.store import price_and_store

    conn = _new_db()
    _full_setup(conn, payoff="BARRIER_KI", barrier_level=0.0)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert not outcome.priced
    assert "barrier_level is 0" in outcome.reason


# --------------------------------------------------------------------------- Phase 4: structures.py

@needs_quantlib
def test_combine_package_sums_two_legs_of_a_synthetic_straddle():
    from engine.options.structures import combine_package

    conn = _new_db()
    _seed_pair_spot(conn)
    _seed_vol(conn)
    _seed_option_trade(
        conn, trade_id="LEG1", instrument_id="EURUSD092326C-LEG1", option_type="CALL",
        package_id="PKG-1",
    )
    _seed_option_trade(
        conn, trade_id="LEG2", instrument_id="EURUSD092326P-LEG2", option_type="PUT",
        package_id="PKG-1",
    )

    summary = combine_package(conn, AS_OF, "PKG-1")
    assert summary.leg_count == 2
    assert summary.priced_leg_count == 2
    assert summary.label == "Two Leg"

    # A long straddle (long call + long put, same strike/expiry) is long
    # gamma and long vega -- both legs contribute positively.
    assert summary.combined["gamma"] > 0
    assert summary.combined["vega"] > 0

    # Sanity: the combined price should equal the sum of each leg's own
    # (quantity-scaled) quote price -- combine() is plain linear addition.
    call_outcome, put_outcome = summary.outcomes
    expected_price = call_outcome.raw["price"] * call_outcome.quantity + put_outcome.raw["price"] * put_outcome.quantity
    assert summary.combined["price"] == pytest.approx(expected_price)


# --------------------------------------------------------------------------- Phase 5b: smile vol resolution

@needs_quantlib
def test_smile_vol_used_and_within_atm_bf_rr_band_for_eursek_1m():
    """(i) EURUSD vanilla priced with the smile reports SMILE and the vol
    used lies within the fixture's 1M tenor's ATM +/- (BF + |RR|/2) band."""
    from engine.options.inputs import resolve_market_inputs, SMILE
    from engine.options.store import price_and_store

    conn = _new_db()
    smile_spot = 11.20
    expiry = "2026-10-17"  # exactly 30 days after SMILE_AS_OF -> the fixture's own '1M' node, no cross-tenor blend
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, spot=smile_spot)
    _seed_vol_quotes(conn)
    _seed_option_trade(conn, expiry=expiry)

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURUSD, expiry, strike=STRIKE)
    assert result.inputs is not None, result.reason
    assert result.inputs.vol_source.source_kind == SMILE

    # Fixture EURUSD 1M: ATM 6.60, RR25 -0.15, BF25 0.18 (vol points).
    atm, rr, bf = 6.60 / 100.0, -0.15 / 100.0, 0.18 / 100.0
    band = bf + abs(rr) / 2
    # Loosened to 2x the nominal 25-delta RR/BF band: STRIKE (11.0584) is
    # not exactly the 25-delta strike for EURUSD's own (smaller) vol level,
    # so it can land slightly beyond that nominal envelope while still
    # being comfortably close to ATM, not some unrelated blown-up number.
    assert atm - 2 * band <= result.inputs.vol <= atm + 2 * band

    outcome = price_and_store(conn, SMILE_AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.vol_source_kind == SMILE


@needs_quantlib
def test_otm_call_and_put_get_different_smile_vols_when_rr_nonzero():
    """(ii) An OTM put and OTM call on the same expiry get different vols
    when RR != 0 -- proof the smile (not just ATM) is actually being used."""
    from engine.options.inputs import resolve_market_inputs

    conn = _new_db()
    smile_spot = 11.20
    expiry = "2026-10-17"
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, spot=smile_spot)
    _seed_vol_quotes(conn)

    call_strike = smile_spot * 1.05  # OTM call, above spot
    put_strike = smile_spot * 0.95   # OTM put, below spot

    call_result = resolve_market_inputs(conn, SMILE_AS_OF, EURUSD, expiry, strike=call_strike)
    put_result = resolve_market_inputs(conn, SMILE_AS_OF, EURUSD, expiry, strike=put_strike)

    assert call_result.inputs is not None, call_result.reason
    assert put_result.inputs is not None, put_result.reason
    assert call_result.inputs.vol != pytest.approx(put_result.inputs.vol)
    # Fixture EURUSD RR25 is NEGATIVE at every tenor (puts richer than
    # calls) -> the lower-strike (put-side) vol should be the larger one.
    assert put_result.inputs.vol > call_result.inputs.vol


@needs_quantlib
def test_missing_strike_skips_smile_falls_back_to_atm_interp():
    """(iii) case 1/2: no usable strike -> SMILE is never attempted (its
    priority-(a) precondition), expiry is inside the quoted tenor range, so
    ATM_INTERP is the resolved source."""
    from engine.options.inputs import resolve_market_inputs, ATM_INTERP

    conn = _new_db()
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, spot=11.20)
    _seed_vol_quotes(conn)
    expiry = "2026-10-17"  # inside the quoted tenor range

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURUSD, expiry, strike=0.0)
    assert result.inputs is not None, result.reason
    assert result.inputs.vol_source.source_kind == ATM_INTERP


@needs_quantlib
def test_expiry_beyond_longest_quoted_tenor_falls_back_to_manual():
    """(iii) case 2/2: expiry beyond the fixture's longest tenor (1Y) puts
    it out of range for BOTH the smile and atm_vol_for_expiry (same
    ATM-bearing-tenor day range underlies both, per inputs.py's docstring),
    so priority falls all the way through to MANUAL."""
    from engine.options.inputs import resolve_market_inputs, set_manual_vol, MANUAL

    conn = _new_db()
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, spot=11.20)
    _seed_vol_quotes(conn)
    far_expiry = "2029-01-01"
    set_manual_vol(conn, SMILE_AS_OF, EURUSD, far_expiry, 0.09)

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURUSD, far_expiry, strike=STRIKE)
    assert result.inputs is not None, result.reason
    assert result.inputs.vol_source.source_kind == MANUAL
    assert result.inputs.vol == pytest.approx(0.09)


@needs_quantlib
def test_no_quotes_and_no_manual_vol_skips_with_no_vol_reason():
    """(iv) With no vol_quotes staged and no manual entry, resolution
    returns None and the reason is exactly "no vol" -- same skip path as
    before smile vol existed."""
    from engine.options.inputs import resolve_market_inputs

    conn = _new_db()
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, spot=11.20)
    # No _seed_vol_quotes call, no set_manual_vol call.

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURUSD, "2026-10-17", strike=STRIKE)
    assert result.inputs is None
    assert result.reason == "no vol"


# --------------------------------------------------------------------------- Phase 7: real OIS rates (engine/options/rates.py)

@needs_quantlib
def test_real_curve_rates_close_to_flat_rate_wrapper_price():
    """A flat OIS curve (same par rate at every quoted tenor) should yield
    a zero rate close to that same flat rate, and a price close to what
    price_fx_vanilla gives when fed that flat rate directly. Not an exact
    identity -- annual OIS compounding vs. continuous zero-rate compounding
    differ by a few bp -- but this is a flat-input APPROXIMATION check, not
    a contradiction of rates.py's own "exact for Garman-Kohlhagen given the
    REAL curve" claim (see that module's docstring)."""
    from engine.options.rates import resolve_fx_rates
    from engine.options.pricer import price_fx_vanilla
    import datetime as dt

    conn = _new_db()
    flat = 0.03
    _seed_ois_curve(conn, AS_OF, "EUR", rate=flat)
    _seed_ois_curve(conn, AS_OF, "USD", rate=flat)

    result, reason = resolve_fx_rates(conn, AS_OF, EURUSD, EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate == pytest.approx(flat, abs=0.005)
    assert result.foreign_rate == pytest.approx(flat, abs=0.005)

    as_of_date, expiry_date = dt.date.fromisoformat(AS_OF), dt.date.fromisoformat(EXPIRY)
    curve_priced = price_fx_vanilla(SPOT, STRIKE, expiry_date, as_of_date, result.domestic_rate, result.foreign_rate, VOL, "call")
    flat_priced = price_fx_vanilla(SPOT, STRIKE, expiry_date, as_of_date, flat, flat, VOL, "call")
    assert curve_priced.quote_price == pytest.approx(flat_priced.quote_price, rel=0.02)


@needs_quantlib
def test_no_curve_skip_reason_for_currency_without_ois_convention():
    """EURSEK: SEK has no OIS convention anywhere in this codebase
    (engine/rates/conventions.py::CCY_RFR) and no manual_rates row is set
    -> skip 'no curve/rate SEK', never a placeholder rate."""
    from engine.options.rates import resolve_fx_rates

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    # No SEK curve seeded -- and none is possible; SEK has no OIS convention.
    # No set_manual_rate call either.

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is None
    assert reason == "no curve/rate SEK"


@needs_quantlib
def test_manual_rate_flat_fallback_prices_with_manual_flat_provenance():
    """SEK has no OIS convention -- set_manual_rate's flat '*' entry lets an
    EURSEK option resolve rates and price, with SEK's rate provenance
    recorded as MANUAL_FLAT and EUR's as OIS_CURVE (curve always wins over
    manual when it exists)."""
    from engine.options.rates import resolve_fx_rates, set_manual_rate, OIS_CURVE, MANUAL_FLAT

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    set_manual_rate(conn, AS_OF, "SEK", 0.02)

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate == pytest.approx(0.02)
    assert result.domestic_rate_source.source_kind == MANUAL_FLAT
    assert result.foreign_rate_source.source_kind == OIS_CURVE


@needs_quantlib
def test_manual_rate_exact_expiry_wins_over_flat():
    from engine.options.rates import resolve_fx_rates, set_manual_rate, MANUAL_EXPIRY, FLAT_EXPIRY

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    set_manual_rate(conn, AS_OF, "SEK", 0.02, expiry=FLAT_EXPIRY)
    set_manual_rate(conn, AS_OF, "SEK", 0.025, expiry=EXPIRY)

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate == pytest.approx(0.025)
    assert result.domestic_rate_source.source_kind == MANUAL_EXPIRY

    # A different expiry with no exact-match row still falls back to the flat entry.
    other, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", "2027-01-01")
    assert other is not None, reason
    assert other.domestic_rate == pytest.approx(0.02)


@needs_quantlib
def test_ois_curve_ignores_manual_rate_for_same_currency():
    """A currency with a real OIS curve never falls through to a manual
    rate, even if one happens to be set -- the curve always wins."""
    from engine.options.rates import resolve_fx_rates, set_manual_rate, OIS_CURVE

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR", rate=0.03)
    _seed_ois_curve(conn, AS_OF, "USD", rate=0.045)
    set_manual_rate(conn, AS_OF, "EUR", 0.99)  # deliberately absurd -- must never be used

    result, reason = resolve_fx_rates(conn, AS_OF, EURUSD, EXPIRY)
    assert result is not None, reason
    assert result.foreign_rate_source.source_kind == OIS_CURVE
    assert result.foreign_rate != pytest.approx(0.99)
    assert result.foreign_rate == pytest.approx(0.03, abs=0.005)


@needs_quantlib
def test_manual_rate_lets_eursek_option_price_via_store():
    """End-to-end: an EURSEK option that previously always skipped now
    prices once a manual SEK rate is set (the biggest-position pair this
    follow-up exists for)."""
    from engine.options.store import price_and_store
    from engine.options.rates import set_manual_rate

    conn = _new_db()
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        ("EURSEK", "FX", "EUR", "SEK", 1.0, 0, "EURSEK Curncy", "9999-12-31"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (AS_OF, "EURSEK", AS_OF, "SPOT", 11.06, "BBG_BFXFORWARD", f"{AS_OF}T17:00:00-04:00"),
    )
    conn.commit()
    _seed_ois_curve(conn, AS_OF, "EUR")
    set_manual_rate(conn, AS_OF, "SEK", 0.02)
    _seed_option_trade(conn, pair="EURSEK", instrument_id="EURSEK092326C-1")
    _seed_vol(conn, pair="EURSEK")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result.premium > 0


def test_no_curve_skip_via_store_price_and_store():
    if not HAVE_QUANTLIB:
        pytest.skip("QuantLib not installed")
    from engine.options.store import price_and_store

    conn = _new_db()
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        ("EURSEK", "FX", "EUR", "SEK", 1.0, 0, "EURSEK Curncy", "9999-12-31"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (AS_OF, "EURSEK", AS_OF, "SPOT", 11.06, "BBG_BFXFORWARD", f"{AS_OF}T17:00:00-04:00"),
    )
    conn.commit()
    # No curve_quotes for either EUR or SEK.
    _seed_option_trade(conn, pair="EURSEK", instrument_id="EURSEK092326C-1")
    _seed_vol(conn, pair="EURSEK")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert not outcome.priced
    assert outcome.reason.startswith("no curve")


# --------------------------------------------------------------------------- Phase 7.2: implied-forward (CIP) rate fallback

def _seed_bare_pair(conn, pair, spot, as_of=AS_OF):
    """Just the plain FX pair instrument + its official SPOT mark -- NO
    curve_quotes seeding (unlike _seed_pair_spot), so the implied-forward
    fallback tests control exactly which currency has a real OIS curve."""
    base_ccy, quote_ccy = pair[:3], pair[3:]
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (pair, "FX", base_ccy, quote_ccy, 1.0, 0, f"{pair} Curncy", "9999-12-31"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (as_of, pair, as_of, "SPOT", spot, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"),
    )
    conn.commit()


def _seed_fwd_outright(conn, pair, settle_date, value, as_of=AS_OF):
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (as_of, pair, settle_date, "FWD_OUTRIGHT", value, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"),
    )
    conn.commit()


@needs_quantlib
def test_implied_forward_rate_matches_covered_interest_parity_by_hand():
    """SEK has no OIS convention and no manual_rates row, but EURSEK's own
    official SPOT + an exact FWD_OUTRIGHT at the option's own expiry, plus
    EUR's real OIS rate, let SEK's rate be implied via covered interest
    parity. Checked against an independent by-hand computation of the same
    r_domestic = r_foreign + ln(F/S)/T formula (not by calling any of
    rates.py's own helpers)."""
    import math
    from engine.options.rates import resolve_fx_rates, resolve_ccy_rate_with_source, IMPLIED_FORWARD, OIS_CURVE

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")  # SEK gets no curve -- it has none, ever
    spot, forward = 11.06, 11.10
    _seed_bare_pair(conn, "EURSEK", spot)
    _seed_fwd_outright(conn, "EURSEK", EXPIRY, forward)

    eur_input, reason = resolve_ccy_rate_with_source(conn, AS_OF, "EUR", EXPIRY)
    assert eur_input is not None, reason
    assert eur_input.source_kind == OIS_CURVE

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate_source.source_kind == IMPLIED_FORWARD
    assert result.foreign_rate_source.source_kind == OIS_CURVE

    as_of_date, expiry_date = datetime.date.fromisoformat(AS_OF), datetime.date.fromisoformat(EXPIRY)
    T = (expiry_date - as_of_date).days / 365.0
    expected_sek_rate = eur_input.rate + math.log(forward / spot) / T
    assert result.domestic_rate == pytest.approx(expected_sek_rate)
    assert result.domestic_rate_source.detail == f"implied from EURSEK forward {EXPIRY} and {eur_input.detail}"
    assert eur_input.detail == "EUR ESTR curve"


@needs_quantlib
def test_implied_forward_interpolates_between_bracketing_marks():
    """The forward fed into CIP is linearly interpolated in forward points
    between the two nearest FWD_OUTRIGHT marks when there is no exact match
    at the option's own expiry -- checked by hand against the same linear
    interpolation formula CLAUDE.md documents for BBG_INTERP."""
    import math
    from engine.options.rates import resolve_fx_rates, resolve_ccy_rate_with_source, IMPLIED_FORWARD

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    spot = 11.06
    d0, d1 = "2026-09-01", "2026-10-01"  # bracket EXPIRY (2026-09-23)
    v0, v1 = 11.08, 11.14
    _seed_bare_pair(conn, "EURSEK", spot)
    _seed_fwd_outright(conn, "EURSEK", d0, v0)
    _seed_fwd_outright(conn, "EURSEK", d1, v1)

    eur_input, reason = resolve_ccy_rate_with_source(conn, AS_OF, "EUR", EXPIRY)
    assert eur_input is not None, reason

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate_source.source_kind == IMPLIED_FORWARD

    d0_date, d1_date, expiry_date = (
        datetime.date.fromisoformat(d0), datetime.date.fromisoformat(d1), datetime.date.fromisoformat(EXPIRY),
    )
    w = (expiry_date - d0_date).days / (d1_date - d0_date).days
    expected_forward = v0 + w * (v1 - v0)
    T = (expiry_date - datetime.date.fromisoformat(AS_OF)).days / 365.0
    expected_rate = eur_input.rate + math.log(expected_forward / spot) / T
    assert result.domestic_rate == pytest.approx(expected_rate)


@needs_quantlib
def test_curve_beats_implied_even_when_a_forward_mark_exists():
    """Precedence: a currency with a real OIS curve never falls through to
    an implied rate, even when a FWD_OUTRIGHT mark is staged that would
    imply something very different -- the curve always wins (same
    precedence rule as test_ois_curve_ignores_manual_rate_for_same_currency,
    one rung further down the fallback chain)."""
    from engine.options.rates import resolve_fx_rates, OIS_CURVE

    conn = _new_db()
    _seed_pair_spot(conn)  # EURUSD SPOT + real OIS curves for both EUR and USD
    # A forward that would imply a wildly different USD rate if it were ever
    # used -- it must not be, since USD already resolves via its own curve.
    _seed_fwd_outright(conn, EURUSD, EXPIRY, 999.0)

    result, reason = resolve_fx_rates(conn, AS_OF, EURUSD, EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate_source.source_kind == OIS_CURVE
    assert result.foreign_rate_source.source_kind == OIS_CURVE
    assert result.domestic_rate == pytest.approx(_FLAT_OIS_RATE["USD"], abs=0.005)


@needs_quantlib
def test_manual_beats_implied_forward():
    """Precedence: a currency with a manual_rates entry never falls through
    to an implied rate either, even when a FWD_OUTRIGHT mark is staged that
    would imply something very different."""
    from engine.options.rates import resolve_fx_rates, set_manual_rate, MANUAL_FLAT

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    set_manual_rate(conn, AS_OF, "SEK", 0.02)
    _seed_bare_pair(conn, "EURSEK", 11.06)
    # Would imply a very different SEK rate if manual didn't win first.
    _seed_fwd_outright(conn, "EURSEK", EXPIRY, 20.0)

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is not None, reason
    assert result.domestic_rate_source.source_kind == MANUAL_FLAT
    assert result.domestic_rate == pytest.approx(0.02)


@needs_quantlib
def test_implied_forward_rejects_when_no_forward_brackets_expiry():
    """Two FWD_OUTRIGHT marks staged, both well short of the option's
    expiry, with a gap far smaller than the overshoot -- capped
    extrapolation refuses to reach that far, so the pair falls back to its
    ORIGINAL "no curve/rate SEK" skip reason, not a CIP-specific one."""
    from engine.options.rates import resolve_fx_rates

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    _seed_bare_pair(conn, "EURSEK", 11.06)
    as_of_date = datetime.date.fromisoformat(AS_OF)
    near1 = (as_of_date + datetime.timedelta(days=7)).isoformat()
    near2 = (as_of_date + datetime.timedelta(days=14)).isoformat()
    _seed_fwd_outright(conn, "EURSEK", near1, 11.07)
    _seed_fwd_outright(conn, "EURSEK", near2, 11.08)
    # EXPIRY is 2026-09-23, 33 days after AS_OF -- 19 days past the last
    # point (14d), which is more than that pair's 7-day tenor gap.

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is None
    assert reason == "no curve/rate SEK"


@needs_quantlib
def test_implied_forward_rejects_when_spot_missing():
    """A FWD_OUTRIGHT exists at the exact expiry, but the pair's own SPOT is
    never staged -- CIP needs both, so this rejects and keeps the original
    skip reason."""
    from engine.options.rates import resolve_fx_rates

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        ("EURSEK", "FX", "EUR", "SEK", 1.0, 0, "EURSEK Curncy", "9999-12-31"),
    )
    # Deliberately no SPOT mark.
    _seed_fwd_outright(conn, "EURSEK", EXPIRY, 11.10)

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is None
    assert reason == "no curve/rate SEK"


@needs_quantlib
def test_implied_forward_rejects_with_fewer_than_two_marks_and_no_exact_match():
    """A single FWD_OUTRIGHT mark, nowhere near the option's own expiry, is
    not enough to interpolate OR extrapolate from (no second point defines a
    slope or a tenor gap) -- rejects, original skip reason kept."""
    from engine.options.rates import resolve_fx_rates

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    _seed_bare_pair(conn, "EURSEK", 11.06)
    _seed_fwd_outright(conn, "EURSEK", "2026-09-01", 11.07)  # only one point, not at EXPIRY

    result, reason = resolve_fx_rates(conn, AS_OF, "EURSEK", EXPIRY)
    assert result is None
    assert reason == "no curve/rate SEK"


@needs_quantlib
def test_eursek_option_prices_end_to_end_via_implied_forward_no_manual_entry():
    """The task's own motivating scenario: an EURSEK option that previously
    always skipped 'no curve/rate SEK' now prices with NO manual_rates entry
    at all, purely from the pair's own official SPOT + FWD_OUTRIGHT and EUR's
    real OIS curve -- store.py's PricingOutcome records the implied
    provenance for diagnostics."""
    from engine.options.store import price_and_store
    from engine.options.rates import IMPLIED_FORWARD

    conn = _new_db()
    _seed_ois_curve(conn, AS_OF, "EUR")
    _seed_bare_pair(conn, "EURSEK", 11.06)
    _seed_fwd_outright(conn, "EURSEK", EXPIRY, 11.10)
    _seed_option_trade(conn, pair="EURSEK", instrument_id="EURSEK092326C-1")
    _seed_vol(conn, pair="EURSEK")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result.premium > 0
    assert outcome.domestic_rate_source_kind == IMPLIED_FORWARD
    assert outcome.domestic_rate_detail.startswith("implied from EURSEK forward")
    assert outcome.foreign_rate_source_kind == "OIS_CURVE"


# --------------------------------------------------------------------------- Phase 7: calendars.py

@needs_quantlib
def test_calendar_year_fraction_differs_from_plain_days_across_holiday():
    from engine.options.calendars import calendar_year_fraction
    from engine.options.pricer import year_fraction

    as_of = datetime.date(2026, 12, 18)
    expiry = datetime.date(2026, 12, 30)  # spans Christmas: EUR (TARGET) and US both closed Dec 25

    plain_T = year_fraction(as_of, expiry)
    calendar_T = calendar_year_fraction("EURUSD", as_of, expiry)
    assert calendar_T > 0
    assert calendar_T != pytest.approx(plain_T, rel=1e-6)


@needs_quantlib
def test_pricer_uses_plain_calendar_days_regardless_of_pair_or_flag():
    """2026-09-17 audit: the vendored engine quantises T to whole calendar days, so a
    Business252 T only mis-rounded the count (a one-year option lost six days). T is
    Act/365 calendar days whatever `pair` / `calendar_aware` say; `pair` still sets the
    delta convention."""
    from engine.options.pricer import price_fx_vanilla

    as_of = datetime.date(2026, 9, 17)
    expiry = datetime.date(2027, 9, 17)  # 365 calendar days; Business252 would say ~359

    with_pair = price_fx_vanilla(1.10, 1.10, expiry, as_of, 0.04, 0.03, 0.08, "call", pair="EURUSD")
    flag_off = price_fx_vanilla(1.10, 1.10, expiry, as_of, 0.04, 0.03, 0.08, "call", pair="EURUSD", calendar_aware=False)
    no_pair = price_fx_vanilla(1.10, 1.10, expiry, as_of, 0.04, 0.03, 0.08, "call")
    assert with_pair.quote_price == pytest.approx(no_pair.quote_price)
    assert flag_off.quote_price == pytest.approx(no_pair.quote_price)
    assert with_pair.delta == pytest.approx(no_pair.delta)
    assert with_pair.delta_convention == "PREMIUM_ADJUSTED"  # EURUSD's market convention
    assert no_pair.delta_convention == "UNKNOWN"


@needs_quantlib
def test_non_g10_pair_falls_back_to_plain_days_and_unknown_convention():
    from engine.options.pricer import price_fx_vanilla, year_fraction

    as_of = datetime.date(2026, 8, 21)
    expiry = datetime.date(2026, 9, 23)
    result = price_fx_vanilla(50.0, 50.0, expiry, as_of, 0.06, 0.05, 0.10, "call", pair="USDTWD")

    assert result.delta_convention == "UNKNOWN"
    assert result.quote_price == pytest.approx(
        price_fx_vanilla(50.0, 50.0, expiry, as_of, 0.06, 0.05, 0.10, "call").quote_price
    )


# --------------------------------------------------------------------------- Phase 7: delta_convention / DELTA_PA

@needs_quantlib
def test_delta_pa_written_only_for_premium_adjusted_pair():
    from engine.options.store import price_and_store

    conn = _new_db()
    instrument_id = _full_setup(conn)  # EURUSD -- premium_currency='base' -> PREMIUM_ADJUSTED

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result.delta_convention == "PREMIUM_ADJUSTED"

    row = conn.execute(
        "SELECT value FROM marks WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'DELTA_PA'",
        (AS_OF, instrument_id),
    ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(outcome.result.delta_premium_adjusted)


@needs_quantlib
def test_delta_pa_not_written_for_raw_convention_pair():
    from engine.options.store import price_and_store

    conn = _new_db()
    # USDJPY -- premium conventionally paid in USD (the quote ccy here) ->
    # RAW convention, no DELTA_PA mark.
    _seed_pair_spot(conn, pair="USDJPY", spot=150.0)
    instrument_id = _seed_option_trade(conn, pair="USDJPY", instrument_id="USDJPY092326C-1", strike=150.0)
    _seed_vol(conn, pair="USDJPY")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    assert outcome.result.delta_convention == "RAW"

    count = conn.execute(
        "SELECT COUNT(*) FROM marks WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'DELTA_PA'",
        (AS_OF, instrument_id),
    ).fetchone()[0]
    assert count == 0


# --------------------------------------------------------------------------- Phase 7: equity / commodity pricing

def _seed_equity_underlying(conn, as_of=AS_OF, underlying="SPX Index", spot=5500.0, quote_ccy="USD"):
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (underlying, "EQUITY_INDEX", quote_ccy, quote_ccy, 1.0, 0, underlying, "9999-12-31"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (as_of, underlying, as_of, "SPOT", spot, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"),
    )
    _seed_ois_curve(conn, as_of, quote_ccy)
    conn.commit()


def _seed_eq_cmdty_option_trade(
    conn, trade_id, instrument_id, underlying, asset_class, product, strike, option_type,
    payoff="VANILLA", barrier_level=0.0, expiry="2026-12-18", quantity=100.0, price=50.0,
    quote_ccy="USD", multiplier=100.0, package_id=None,
):
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, asset_class, quote_ccy, quote_ccy, multiplier, 0, underlying, expiry),
    )
    conn.execute(
        "INSERT OR REPLACE INTO instrument_options VALUES (?,?,?,?,?,?)",
        (instrument_id, strike, option_type, barrier_level, "9999-12-31", payoff),
    )
    conn.execute(
        "INSERT OR REPLACE INTO trades "
        "(trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
        "account, counterparty, strategy, trader, description, theme) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, product, package_id or trade_id, AS_OF, quantity, price,
         "TEST", "TEST", "", "JB", "", ""),
    )
    conn.commit()
    return instrument_id


@needs_quantlib
def test_equity_european_prices_and_stores():
    from engine.options.equity_commodity import price_and_store_equity, set_dividend_yield
    from engine.options.inputs import set_manual_vol

    conn = _new_db()
    _seed_equity_underlying(conn)
    instrument_id = _seed_eq_cmdty_option_trade(
        conn, "EQ1", "SPX 5600 Call 2026-12-18", "SPX Index", "EQ_OPTION", "EQ_OPTION", 5600.0, "CALL",
    )
    set_dividend_yield(conn, AS_OF, "SPX Index", 0.015)
    set_manual_vol(conn, AS_OF, "SPX Index", "2026-12-18", 0.18)

    outcome = price_and_store_equity(conn, AS_OF, "EQ1")
    assert outcome.priced, outcome.reason
    assert outcome.result.premium > 0

    premium_mark = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'PREMIUM'",
        (AS_OF, instrument_id),
    ).fetchone()[0]
    # PREMIUM mark = pricer's unscaled per-unit price x multiplier (100) --
    # NOT the FX base-notional-fraction conversion.
    assert premium_mark == pytest.approx(outcome.result.premium * 100.0)


@needs_quantlib
def test_equity_missing_dividend_yield_skips():
    from engine.options.equity_commodity import price_and_store_equity
    from engine.options.inputs import set_manual_vol

    conn = _new_db()
    _seed_equity_underlying(conn)
    _seed_eq_cmdty_option_trade(
        conn, "EQ1", "SPX 5600 Call 2026-12-18", "SPX Index", "EQ_OPTION", "EQ_OPTION", 5600.0, "CALL",
    )
    set_manual_vol(conn, AS_OF, "SPX Index", "2026-12-18", 0.18)
    # No set_dividend_yield call.

    outcome = price_and_store_equity(conn, AS_OF, "EQ1")
    assert not outcome.priced
    assert outcome.reason == "no dividend yield"


@needs_quantlib
def test_commodity_black76_prices_and_stores():
    from engine.options.equity_commodity import price_and_store_commodity
    from engine.options.inputs import set_manual_vol

    conn = _new_db()
    _seed_equity_underlying(conn, underlying="GC1 Comdty", spot=2000.0)  # reused helper: underlying + curve
    instrument_id = _seed_eq_cmdty_option_trade(
        conn, "CM1", "GC 2050 Call 2026-12-18", "GC1 Comdty", "CMDTY_OPTION", "CMDTY_OPTION",
        2050.0, "CALL", quantity=10.0, price=30.0,
    )
    set_manual_vol(conn, AS_OF, "GC1 Comdty", "2026-12-18", 0.20)

    outcome = price_and_store_commodity(conn, AS_OF, "CM1")
    assert outcome.priced, outcome.reason
    assert outcome.result.premium > 0

    premium_mark = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = 'PREMIUM'",
        (AS_OF, instrument_id),
    ).fetchone()[0]
    assert premium_mark == pytest.approx(outcome.result.premium * 100.0)


@needs_quantlib
def test_commodity_unsupported_payoff_skipped_not_approximated():
    """No barrier/digital/one-touch commodity pricer exists upstream
    (MODELS.md's own documented gap) -- a BARRIER_KO instrument_options row
    is skipped, never silently priced some other way."""
    from engine.options.equity_commodity import price_and_store_commodity
    from engine.options.inputs import set_manual_vol

    conn = _new_db()
    _seed_equity_underlying(conn, underlying="GC1 Comdty", spot=2000.0)
    _seed_eq_cmdty_option_trade(
        conn, "CM1", "GC 2050 KO 2026-12-18", "GC1 Comdty", "CMDTY_OPTION", "CMDTY_OPTION",
        2050.0, "CALL", payoff="BARRIER_KO", barrier_level=2200.0,
    )
    set_manual_vol(conn, AS_OF, "GC1 Comdty", "2026-12-18", 0.20)

    outcome = price_and_store_commodity(conn, AS_OF, "CM1")
    assert not outcome.priced
    assert "not supported" in outcome.reason


@needs_quantlib
def test_vol_surface_points_round_trip():
    from engine.options.equity_commodity import write_vol_surface_points, _build_vol_surface

    conn = _new_db()
    points = [
        (30, 5400, 0.16), (30, 5600, 0.14),
        (90, 5400, 0.17), (90, 5600, 0.15),
    ]
    write_vol_surface_points(conn, AS_OF, "SPX Index", points)

    surface = _build_vol_surface(conn, AS_OF, "SPX Index")
    assert surface is not None
    assert surface.get_vol(5400, 30 / 365.0) == pytest.approx(0.16)
    assert surface.get_vol(5600, 90 / 365.0) == pytest.approx(0.15)


# --------------------------------------------------------------------------- Phase 7: portfolio.py

@needs_quantlib
def test_portfolio_sums_two_packages_and_skips_missing_quote_ccy_spot():
    from engine.options.store import price_and_store
    from engine.options.portfolio import portfolio_summary

    conn = _new_db()
    _seed_pair_spot(conn)  # EURUSD
    _seed_vol(conn)
    _seed_option_trade(conn, trade_id="P1L1", instrument_id="EURUSD-P1L1", package_id="PKG-A")
    _seed_option_trade(conn, trade_id="P2L1", instrument_id="EURUSD-P2L1", package_id="PKG-B")

    # EURGBP prices fine (EUR+GBP both have OIS curves, and its own SPOT is
    # seeded), but the PORTFOLIO layer additionally needs GBP -> USD
    # (a GBPUSD or USDGBP SPOT), which is deliberately never seeded here --
    # exercises the "priced but can't USD-convert" skip path.
    _seed_pair_spot(conn, pair="EURGBP", spot=0.85)
    _seed_vol(conn, pair="EURGBP")
    _seed_option_trade(conn, trade_id="P3L1", instrument_id="EURGBP-P3L1", pair="EURGBP", package_id="PKG-C")

    outcomes = [
        price_and_store(conn, AS_OF, "P1L1"),
        price_and_store(conn, AS_OF, "P2L1"),
        price_and_store(conn, AS_OF, "P3L1"),
    ]
    assert all(o.priced for o in outcomes), [o.reason for o in outcomes]

    portfolio, legs, skipped = portfolio_summary(conn, AS_OF, outcomes)
    assert {leg.package_id for leg in legs} == {"PKG-A", "PKG-B"}
    assert len(skipped) == 1
    assert skipped[0]["instrument_id"] == "EURGBP-P3L1"
    assert "GBP" in skipped[0]["reason"]

    totals = portfolio.by_label()
    assert set(totals) == {"PKG-A", "PKG-B"}
    grand_total = portfolio.total()
    summed_delta = sum(totals[k]["delta"] for k in totals)
    assert grand_total["delta"] == pytest.approx(summed_delta)
