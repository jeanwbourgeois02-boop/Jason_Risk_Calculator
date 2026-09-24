"""engine/options/ tests, owned by fx-options-pricer (other lanes keep their own
older tests here).

Phase 0: the vendored library is in place but nothing is wired to the schema
yet, so those tests only check the vendor copy imports cleanly. Phase 2
lands FX vanilla/digital pricing + PREMIUM/DELTA marks; Phase 4 lands
American/Asian/barrier/one-touch/no-touch payoffs plus multi-leg structure
combination; Phase 5b lands smile-vol resolution; Phase 7 lands real OIS
rates, calendar-aware year fractions/delta conventions and Position/Portfolio
aggregation (its equity / commodity tests left this file on 2026-09-24 with the
equity index; commodity options are tested in tests/test_listed_options.py).
Follows test_rates_pricing.py's
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
    # Spot, rates and vol are all on file here: the ONLY thing missing is the strike, and
    # the reason says so in words (never priced at strike 0 or at spot instead).
    assert outcome.reason.startswith("no strike")
    assert outcome.result is None

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

    # One-touch + no-touch (same barrier) must sum to the discounted payout -- exactly
    # one of "touched" / "never touched" happens. Payout convention (2026-09-18 units
    # audit, store.CASH_PAYOUT_CCY = 'BASE'): trades.quantity is the payout in the pair's
    # BASE currency and PREMIUM is a fraction of it, so the two premiums sum to the
    # BASE-ccy (foreign-rate) discount factor, and each lies between 0 and it.
    import math
    _, foreign_rate = _dom_for_rates(conn_ot)
    T = (datetime.date.fromisoformat(EXPIRY) - datetime.date.fromisoformat(AS_OF)).days / 365.0
    df_base = math.exp(-foreign_rate * T)
    ot, nt = outcome_ot.result.premium, outcome_nt.result.premium
    assert 0.0 < ot < df_base and 0.0 < nt < df_base
    assert ot + nt == pytest.approx(df_base, abs=1e-6)
    # Before the audit PREMIUM was (1 QUOTE unit paid per base unit) / spot: the pair
    # summed to ~1/spot = 0.09 here. quote_price stays premium x spot.
    assert outcome_ot.result.quote_price == pytest.approx(ot * SPOT)


@needs_quantlib
def test_skipped_when_barrier_level_is_zero():
    from engine.options.store import price_and_store

    conn = _new_db()
    _full_setup(conn, payoff="BARRIER_KI", barrier_level=0.0)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert not outcome.priced
    assert outcome.reason.startswith("no barrier")


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
    from engine.options.pricer import price_fx_vanilla

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


# --------------------------------------------------------------------------- 2026-09-22: bootstrap_note provenance

@needs_quantlib
@pytest.mark.parametrize("note, expect_suffix", [
    ("x", "; x"),
    ("", ""),
])
def test_resolve_ccy_rate_with_source_names_the_curves_bootstrap_note(monkeypatch, note, expect_suffix):
    """A CurveSet built by a fallback bootstrap (rates-pricer records `bootstrap_note` on
    it) has that note appended to RateInput.detail; an empty note leaves the detail as
    it is today ("USD SOFR curve"). Read with getattr, so a CurveSet without the
    attribute is the empty-note case."""
    from engine.options import rates

    class StubCurveSet:
        bootstrap_note = note

    monkeypatch.setattr(rates, "_get_curve", lambda conn, as_of, ccy, cache: (StubCurveSet(), ""))
    monkeypatch.setattr(rates, "zero_rate_to", lambda curve_set, as_of_date, expiry_date: 0.04)

    rate_input, reason = rates.resolve_ccy_rate_with_source(None, AS_OF, "USD", EXPIRY)
    assert reason == "" and rate_input.rate == 0.04 and rate_input.source_kind == rates.OIS_CURVE
    assert rate_input.detail == "USD SOFR curve" + expect_suffix
    if note:
        assert rate_input.detail.endswith(note)


@needs_quantlib
def test_resolve_ccy_rate_with_source_tolerates_a_curve_set_without_bootstrap_note(monkeypatch):
    from engine.options import rates

    class BareCurveSet:  # no bootstrap_note attribute at all (a CurveSet from before the field existed)
        pass

    monkeypatch.setattr(rates, "_get_curve", lambda conn, as_of, ccy, cache: (BareCurveSet(), ""))
    monkeypatch.setattr(rates, "zero_rate_to", lambda curve_set, as_of_date, expiry_date: 0.04)
    rate_input, _ = rates.resolve_ccy_rate_with_source(None, AS_OF, "USD", EXPIRY)
    assert rate_input.detail == "USD SOFR curve"


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


# --------------------------------------------------------------------------- 2026-09-24: options on commodity futures
#
# A CMDTY_OPTION's `bbg_ticker` is its own Bloomberg ticker, so portfolio.py reads the
# underlying future's official FUTURE_PX (the future contract-master names), never a SPOT.
# The outcomes are built by hand: the Greeks come from equity_commodity.py
# (listed-options-pricer's tests), and this module only converts them.

CMDTY_AS_OF = "2026-09-24"
CL_OPTION, CL_FUTURE, CL_FUTURE_EXPIRY, CL_OPTION_EXPIRY = "CLZ26C 80 Comdty", "CLZ26 Comdty", "2026-11-19", "2026-11-16"
CU_OPTION, CU_FUTURE, CU_FUTURE_EXPIRY, CU_OPTION_EXPIRY = "CUZ26C 80000 Comdty", "CUZ26 Comdty", "2026-12-15", "2026-11-24"
_INST_COLS = "(instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, expiry_date)"


def _seed_cmdty_option(conn, option_id, future_id, root, ccy, multiplier, option_expiry, future_expiry,
                       future_price=None, as_of=CMDTY_AS_OF):
    conn.execute(f"INSERT INTO instruments {_INST_COLS} VALUES (?,?,?,?,?,?,?,?)",
                 (future_id, "FUTURE", root, ccy, multiplier, 0, future_id.replace("Z26", "Z6"), future_expiry))
    conn.execute(f"INSERT INTO instruments {_INST_COLS} VALUES (?,?,?,?,?,?,?,?)",
                 (option_id, "CMDTY_OPTION", root, ccy, multiplier, 0, option_id.replace("Z26", "Z6"),
                  option_expiry))
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) VALUES (?,?,?,?)",
                 (option_id, float(option_id.split()[1]), "CALL", "AMERICAN"))
    if future_price is not None:
        conn.execute("INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (as_of, future_id, future_expiry, "FUTURE_PX", future_price, "BBG_BDH",
                      f"{as_of}T17:00:00-04:00"))
    conn.commit()


def _cmdty_outcome(option_id, quantity, delta=0.45, gamma=0.03, theta=-0.02, vega=0.15, rho=-0.01, price=3.2):
    from engine.options.equity_commodity import CommodityOutcome
    from engine.options.pricer import OptionPriceResult

    result = OptionPriceResult(premium=price, delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho,
                               quote_price=price, delta_premium_adjusted=0.0, delta_convention="N/A")
    return CommodityOutcome(trade_id="C1", instrument_id=option_id, package_id="PKG-CL", quantity=quantity,
                            priced=True, result=result, vol_source="IMPLIED", implied_vol=0.35)


@needs_quantlib
def test_cmdty_option_takes_its_underlying_futures_price_not_a_spot_on_its_ticker():
    from engine.options.portfolio import portfolio_summary

    conn = _new_db()
    future, lots, mult = 78.0, 10.0, 1000.0
    _seed_cmdty_option(conn, CL_OPTION, CL_FUTURE, "NYMEX:CL", "USD", mult, CL_OPTION_EXPIRY, CL_FUTURE_EXPIRY,
                       future_price=future)
    outcome = _cmdty_outcome(CL_OPTION, lots)

    portfolio, legs, skipped = portfolio_summary(conn, CMDTY_AS_OF, [outcome])
    assert skipped == [] and [leg.instrument_id for leg in legs] == [CL_OPTION]
    assert legs[0].asset_class == "CMDTY_OPTION"
    total, r, size = portfolio.total(), outcome.result, lots * mult
    assert total["delta"] == pytest.approx(size * r.delta * future)                 # futures-equivalent USD
    assert total["gamma"] == pytest.approx(size * r.gamma * future * future / 100.0)
    assert total["price"] == pytest.approx(size * r.quote_price)
    for greek in ("vega", "theta", "rho"):
        assert total[greek] == pytest.approx(size * getattr(r, greek))


@needs_quantlib
def test_cmdty_option_in_a_non_usd_contract_converts_at_the_currencys_spot():
    from engine.options.portfolio import portfolio_summary

    conn = _new_db()
    future, lots, mult, usdcny = 79_500.0, -4.0, 5.0, 7.10
    _seed_cmdty_option(conn, CU_OPTION, CU_FUTURE, "SHFE:CU", "CNY", mult, CU_OPTION_EXPIRY, CU_FUTURE_EXPIRY,
                       future_price=future)
    conn.execute(f"INSERT INTO instruments {_INST_COLS} VALUES (?,?,?,?,?,?,?,?)",
                 ("USDCNY", "FX", "USD", "CNY", 1.0, 0, "USDCNY Curncy", "9999-12-31"))
    conn.execute("INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (CMDTY_AS_OF, "USDCNY", CMDTY_AS_OF, "SPOT", usdcny, "BBG_BFXFORWARD", f"{CMDTY_AS_OF}T15:00:00-04:00"))
    outcome = _cmdty_outcome(CU_OPTION, lots, delta=0.4, price=1500.0)

    portfolio, legs, skipped = portfolio_summary(conn, CMDTY_AS_OF, [outcome])
    assert skipped == [] and len(legs) == 1
    total = portfolio.total()
    assert total["delta"] == pytest.approx(lots * mult * 0.4 * future / usdcny)
    assert total["price"] == pytest.approx(lots * mult * 1500.0 / usdcny)


@needs_quantlib
@pytest.mark.parametrize("case", ["no price", "spot on the ticker only", "unknown underlying"])
def test_cmdty_option_without_its_underlying_futures_price_is_skipped_with_the_reason(case):
    from engine.options.portfolio import build_positions

    conn = _new_db()
    _seed_cmdty_option(conn, CL_OPTION, CL_FUTURE, "NYMEX:CL", "USD", 1000.0, CL_OPTION_EXPIRY, CL_FUTURE_EXPIRY)
    if case == "spot on the ticker only":      # the old rule's input: never read any more
        conn.execute(f"INSERT INTO instruments {_INST_COLS} VALUES (?,?,?,?,?,?,?,?)",
                     ("CLZ6C 80 Comdty", "CMDTY_OPTION", "NYMEX:CL", "USD", 1000.0, 0, "", CL_OPTION_EXPIRY))
        conn.execute("INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                     "snapped_at) VALUES (?,?,?,?,?,?,?)",
                     (CMDTY_AS_OF, "CLZ6C 80 Comdty", CMDTY_AS_OF, "SPOT", 78.0, "BBG_BFXFORWARD",
                      f"{CMDTY_AS_OF}T15:00:00-04:00"))
    if case == "unknown underlying":
        conn.execute("UPDATE instruments SET base_ccy = 'NOPE:XX' WHERE instrument_id = ?", (CL_OPTION,))
    conn.commit()

    legs, skipped = build_positions(conn, CMDTY_AS_OF, [_cmdty_outcome(CL_OPTION, 10.0)])
    assert legs == []
    assert [s["instrument_id"] for s in skipped] == [CL_OPTION]
    if case == "unknown underlying":
        assert "not known to the contract master" in skipped[0]["reason"]
    else:
        assert skipped[0]["reason"] == (f"no official price of the underlying future {CL_FUTURE} on {CMDTY_AS_OF} "
                                        "to put delta and gamma on a USD basis")


@needs_quantlib
def test_fx_and_cmdty_options_sum_side_by_side_and_fx_still_reads_the_pair_spot():
    from engine.options.portfolio import portfolio_summary
    from engine.options.store import price_and_store

    conn = _new_db()
    _seed_pair_spot(conn)
    _seed_vol(conn)
    _seed_option_trade(conn)
    fx = price_and_store(conn, AS_OF, "T1")
    assert fx.priced, fx.reason
    _seed_cmdty_option(conn, CL_OPTION, CL_FUTURE, "NYMEX:CL", "USD", 1000.0, CL_OPTION_EXPIRY, CL_FUTURE_EXPIRY,
                       future_price=78.0, as_of=AS_OF)
    cmdty = _cmdty_outcome(CL_OPTION, 10.0)

    portfolio, legs, skipped = portfolio_summary(conn, AS_OF, [fx, cmdty])
    assert skipped == []
    by_class = portfolio.by_asset_class()
    assert set(by_class) == {"FX_OPTION", "CMDTY_OPTION"}
    assert by_class["FX_OPTION"]["delta"] == pytest.approx(NOTIONAL * fx.result.delta * SPOT)   # EURUSD: USD quote
    assert by_class["CMDTY_OPTION"]["delta"] == pytest.approx(10.0 * 1000.0 * 0.45 * 78.0)
    assert portfolio.total()["delta"] == pytest.approx(by_class["FX_OPTION"]["delta"]
                                                       + by_class["CMDTY_OPTION"]["delta"])


@needs_quantlib
@pytest.mark.parametrize("ccy", ["CNY", "MYR", "SGD"])
def test_currency_with_no_ois_set_up_resolves_none_with_its_reason_and_a_manual_rate_still_wins(ccy):
    """listed-options-pricer discounts a CNY / MYR / SGD contract on the USD curve when its own
    currency resolves nothing: `resolve_ccy_rate_with_source` must return (None, reason) for
    it, never raise, and still honour a manual rate; USD itself resolves off its curve."""
    from engine.options.rates import MANUAL_FLAT, OIS_CURVE, resolve_ccy_rate_with_source, set_manual_rate

    conn = _new_db()
    _seed_ois_curve(conn, CMDTY_AS_OF, "USD")
    cache: dict = {}
    rate, reason = resolve_ccy_rate_with_source(conn, CMDTY_AS_OF, ccy, CU_OPTION_EXPIRY, cache)
    assert rate is None and reason == f"no curve/rate {ccy}"
    usd, usd_reason = resolve_ccy_rate_with_source(conn, CMDTY_AS_OF, "USD", CU_OPTION_EXPIRY, cache)
    assert usd is not None and usd.source_kind == OIS_CURVE, usd_reason
    assert usd.detail.startswith("USD SOFR curve")

    set_manual_rate(conn, CMDTY_AS_OF, ccy, 0.018)
    manual, _ = resolve_ccy_rate_with_source(conn, CMDTY_AS_OF, ccy, CU_OPTION_EXPIRY, cache)
    assert manual is not None and manual.source_kind == MANUAL_FLAT and manual.rate == pytest.approx(0.018)


def test_price_all_and_store_isolates_one_trades_pricer_exception(monkeypatch):
    """2026-09-18 audit: an exception from the pricer for ONE option must not abort the
    whole options step -- the other trades still price, the bad one is reported with the
    error text as its skip reason."""
    if not HAVE_QUANTLIB:
        pytest.skip("QuantLib not installed")
    from engine.options import store

    conn = _new_db()
    _full_setup(conn, trade_id="T1", instrument_id="EURUSD092326C-1")
    _seed_option_trade(conn, trade_id="T2", instrument_id="EURUSD092326P-2", option_type="PUT")
    real = store._price_row

    def exploding(conn_, as_of, row, **kw):
        if row["trade_id"] == "T1":
            raise RuntimeError("QuantLib blew up")
        return real(conn_, as_of, row, **kw)

    monkeypatch.setattr(store, "_price_row", exploding)
    outcomes = {o.trade_id: o for o in store.price_all_and_store(conn, AS_OF)}
    assert outcomes["T2"].priced
    assert not outcomes["T1"].priced and "QuantLib blew up" in outcomes["T1"].reason


# --------------------------------------------------------------------------- 2026-09-18 units audit
#
# The book's P&L line is quantity x (PREMIUM_mark - premium_fill) x S, which is only right
# if the PREMIUM mark is in the blotter fill's own unit. From the export
# (columns Side / Quantity / Price / Currency / NetInvoice):
#   vanillas  Quantity = BASE notional, Price = premium per 1 unit of it, invoiced in the
#             BASE currency (EURUSD111826C 10,000,000 @ 0.0098 -> EUR 98,000).
#   digitals  Quantity = PAYOUT notional, Price = a fraction of that payout, invoiced in
#             the BASE currency (USDJPY111926P 2,000,000 @ 0.124 -> USD 248,000).
# PAYOUT CURRENCY ASSUMPTION for a digital (and a touch), stated once for every test
# below: the payout is `trades.quantity` units of the pair's BASE currency
# (store.CASH_PAYOUT_CCY = 'BASE') -- USD for USDJPY, EUR for EURUSD / EURSEK. The export
# has no payout-currency column; this follows from the invoice currency and from a premium
# that size being impossible against a payout of the same number of JPY. CONFIRMED BY THE
# USER 2026-09-18: USD on the USDJPY digitals, EUR on the EURSEK one.
#
# The book: the five FX options of the synthetic sample blotter, data/sample/blotter_sample.csv
# (2026-09-24: the macro trader's reference export left the app, CLAUDE.md "Commodity
# conversion plan", Phase 2), seeded inline in the shape data/ingest/blotter.py writes an
# option (FX_OPTION instrument keyed by the Symbol, its instrument_options row, the trade,
# one NOTIONAL leg in the base currency from trade date to expiry, settles_cash 0), so these
# tests stand on this lane's code alone. The sample's dates are day first (19/8/2026).
SAMPLE_OPTIONS = {  # instrument_id: (trade_id, trade_date, pair, expiry, strike, call/put, signed quantity, fill, NetInvoice)
    "EURUSD111826C-500041": ("910000041", "2026-08-19", "EURUSD", "2026-11-18", 1.18, "CALL", 10_000_000.0, 0.0098, 98_000.0),
    "USDJPY121626P-500042": ("910000042", "2026-09-01", "USDJPY", "2026-12-16", 142.5, "PUT", 5_000_000.0, 0.0115, 57_500.0),
    # A digital the export does not mark as one: no strike in its Description, FxOption Type '0'.
    "USDJPY111926P-500043": ("910000043", "2026-08-26", "USDJPY", "2026-11-19", 0.0, "PUT", 2_000_000.0, 0.124, 248_000.0),
    # Bought, then sold back in full: a closed-out group from 2026-09-08 (CLAUDE.md "A closed-out option is not live").
    "EURUSD102126P-500044": ("910000044", "2026-08-10", "EURUSD", "2026-10-21", 1.15, "PUT", 5_000_000.0, 0.0042, 21_000.0),
    "EURUSD102126P-500045": ("910000045", "2026-09-08", "EURUSD", "2026-10-21", 1.15, "PUT", -5_000_000.0, 0.0031, 15_500.0),
}
SAMPLE_CLOSED_OUT = {"EURUSD102126P-500044", "EURUSD102126P-500045"}
SAMPLE_CLOSED_ON = "2026-09-08"   # the sell-back's trade date
AUDIT_AS_OF = "2026-09-15"  # after the close-out, five weeks before the first expiry
# A synthetic but plausible market. Spot sits near the strikes; vols are ordinary for each pair.
AUDIT_SPOT = {"EURUSD": 1.17, "USDJPY": 145.0}
AUDIT_VOL = {"EURUSD": 0.065, "USDJPY": 0.10}
# The strike the export does not carry, typed once on the app (the digital's terms).
SAMPLE_DIGITAL_TERMS = {
    "USDJPY111926P-500043": (152.0, "PUT"),
}


def _sample_book(tmp_path=None):
    """The sample blotter's five FX options, in a throwaway database (see the section note)."""
    conn = _new_db()
    for instrument_id, (trade_id, trade_date, pair, expiry, strike, option_type, quantity, fill, _) in \
            SAMPLE_OPTIONS.items():
        conn.execute(
            "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
            "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
            (instrument_id, "FX_OPTION", pair[:3], pair[3:], 1.0, 0, instrument_id, expiry))
        conn.execute(
            "INSERT INTO instrument_options (instrument_id, strike, option_type, barrier_level, avg_start_date, "
            "payoff) VALUES (?,?,?,?,?,?)", (instrument_id, strike, option_type, 0.0, "9999-12-31", "VANILLA"))
        conn.execute(
            "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
            "account, counterparty, strategy, trader, description, theme) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, "XLSX", instrument_id, "FX_OPTION", trade_id, trade_date, quantity, fill,
             "PB-FX-NMMF", "CPTY-C", "", "JB", "", ""))
        conn.execute(
            "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
            "settles_cash) VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_id, 1, "NOTIONAL", pair[:3], quantity, trade_date, expiry, fill, 0))
    conn.commit()
    return conn


def _seed_audit_market(conn, as_of=AUDIT_AS_OF, spot=True, vol=True):
    from engine.options.inputs import FLAT_TENOR, set_manual_vol

    for pair in AUDIT_SPOT:
        conn.execute(
            "INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
            "is_ndf, bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
            (pair, "FX", pair[:3], pair[3:], 1.0, 0, f"{pair} Curncy", "9999-12-31"),
        )
        if spot:
            conn.execute(
                "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                "snapped_at) VALUES (?,?,?,?,?,?,?)",
                (as_of, pair, as_of, "SPOT", AUDIT_SPOT[pair], "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"),
            )
        _seed_ois_curves_for_pair(conn, as_of, pair)
        if vol:
            set_manual_vol(conn, as_of, pair, FLAT_TENOR, AUDIT_VOL[pair])
    conn.commit()


def _sample_option_trades(conn):
    return conn.execute(
        "SELECT t.trade_id, t.instrument_id, t.quantity, t.price, i.base_ccy || i.quote_ccy, i.expiry_date, "
        "COALESCE(o.strike, 0), COALESCE(o.option_type, ''), COALESCE(o.payoff, '') "
        "FROM trades_official t JOIN instruments i USING (instrument_id) "
        "LEFT JOIN instrument_options o USING (instrument_id) "
        "WHERE t.product = 'FX_OPTION' ORDER BY t.trade_id"
    ).fetchall()


def _official_mark(conn, as_of, instrument_id, mark_type):
    row = conn.execute(
        "SELECT value FROM marks_official WHERE as_of_date = ? AND instrument_id = ? AND mark_type = ?",
        (as_of, instrument_id, mark_type),
    ).fetchone()
    return None if row is None else row[0]


def _base_payout_digital_closed_form(S, K, T, domestic_rate, foreign_rate, vol, option_type):
    """Base-ccy value of 1 BASE unit paid if in the money, as a fraction of that payout:
    exp(-r_f T) N(d1) for a call, exp(-r_f T) N(-d1) for a put (Garman-Kohlhagen)."""
    import math
    from statistics import NormalDist

    d1 = (math.log(S / K) + (domestic_rate - foreign_rate + 0.5 * vol * vol) * T) / (vol * math.sqrt(T))
    return math.exp(-foreign_rate * T) * NormalDist().cdf(d1 if option_type == "call" else -d1)


@needs_quantlib
def test_sample_vanilla_premium_marks_are_in_the_blotter_fill_unit(tmp_path):
    """Task 1. With spot near the strike and an ordinary vol, the model premium of each of
    the book's four struck vanillas lands within a small multiple of its fill -- which a
    pips-vs-fraction or a base-vs-quote unit error (x145 on USDJPY, x10,000 for pips)
    cannot do. The exact unit statement follows: PREMIUM x spot is the vendored
    Garman-Kohlhagen price (quote ccy per 1 base unit), for EURUSD and USDJPY alike."""
    from engine.options.inputs import resolve_market_inputs
    from engine.options.store import price_and_store
    from engine.options.vendor.options_calc.fx import european

    conn = _sample_book(tmp_path)
    _seed_audit_market(conn)
    vanillas = [r for r in _sample_option_trades(conn) if r[6] > 0]
    assert len(vanillas) == 4
    assert {r[4] for r in vanillas} == {"EURUSD", "USDJPY"}

    as_of_date = datetime.date.fromisoformat(AUDIT_AS_OF)
    for trade_id, instrument_id, quantity, fill, pair, expiry, strike, option_type, payoff in vanillas:
        assert payoff == "VANILLA"
        outcome = price_and_store(conn, AUDIT_AS_OF, trade_id)
        assert outcome.priced, (instrument_id, outcome.reason)
        premium = _official_mark(conn, AUDIT_AS_OF, instrument_id, "PREMIUM")
        assert premium == pytest.approx(outcome.result.premium)

        assert fill / 3.0 <= premium <= fill * 3.0, (instrument_id, fill, premium)

        inputs = resolve_market_inputs(conn, AUDIT_AS_OF, pair, expiry, strike=strike).inputs
        T = (datetime.date.fromisoformat(expiry) - as_of_date).days / 365.0
        # 2026-09-21: the vol the engine is handed carries the true hours to the 10:00 New York cut
        from engine.options.store import cut_time_factor
        cut_vol = inputs.vol * cut_time_factor(conn, AUDIT_AS_OF, pair, datetime.date.fromisoformat(expiry))
        vendored = european.price(inputs.spot, strike, T, inputs.domestic_rate, inputs.foreign_rate,
                                  cut_vol, option_type.lower())
        assert premium * inputs.spot == pytest.approx(vendored["price"], rel=1e-12)

        # The book's own arithmetic, in the BASE currency (EUR on EURUSD, USD on USDJPY):
        # starting cost = quantity x fill (the row's NetInvoice), value = quantity x PREMIUM,
        # P&L = the difference. A sold option is a liability.
        cost, value = quantity * fill, quantity * premium
        assert abs(cost) == pytest.approx(SAMPLE_OPTIONS[instrument_id][8])
        assert (cost > 0) == (value > 0) == (quantity > 0)


@needs_quantlib
def test_sample_digitals_price_as_a_fraction_of_a_base_currency_payout(tmp_path):
    """Task 1, digitals. PAYOUT CURRENCY ASSUMPTION (section comment above): BASE currency
    -- USD 2,000,000 on the USDJPY 152 put. PREMIUM is then a fraction of that payout, in
    (0, base-ccy discount factor), equal to exp(-r_f T) N(+-d1) -- the same unit as the
    fill 0.124. Before the audit the mark was exp(-r_d T) N(+-d2) / spot: 1/145 of this."""
    import math

    from engine.options.inputs import resolve_market_inputs
    from engine.options.store import CASH_PAYOUT_CCY, price_and_store, set_option_terms
    from engine.options.vendor.options_calc.fx import digital

    assert CASH_PAYOUT_CCY == "BASE"
    conn = _sample_book(tmp_path)
    _seed_audit_market(conn)
    for instrument_id, (strike, option_type) in SAMPLE_DIGITAL_TERMS.items():
        set_option_terms(conn, instrument_id, strike, option_type, "DIGITAL")

    digitals = [r for r in _sample_option_trades(conn) if r[8] == "DIGITAL"]
    assert {r[1] for r in digitals} == set(SAMPLE_DIGITAL_TERMS)

    as_of_date = datetime.date.fromisoformat(AUDIT_AS_OF)
    from engine.options.store import cut_time_factor
    for trade_id, instrument_id, quantity, fill, pair, expiry, strike, option_type, _ in digitals:
        outcome = price_and_store(conn, AUDIT_AS_OF, trade_id)
        assert outcome.priced, (instrument_id, outcome.reason)
        premium = _official_mark(conn, AUDIT_AS_OF, instrument_id, "PREMIUM")

        inputs = resolve_market_inputs(conn, AUDIT_AS_OF, pair, expiry, strike=strike).inputs
        T = (datetime.date.fromisoformat(expiry) - as_of_date).days / 365.0
        df_base = math.exp(-inputs.foreign_rate * T)  # discount factor of the PAYOUT currency
        assert 0.0 < premium < 1.0 * df_base, (instrument_id, premium, df_base)
        assert premium == pytest.approx(
            _base_payout_digital_closed_form(
                inputs.spot, strike, T, inputs.domestic_rate, inputs.foreign_rate,
                inputs.vol * cut_time_factor(conn, AUDIT_AS_OF, pair, datetime.date.fromisoformat(expiry)),  # hours to the cut
                option_type.lower()), abs=1e-9)

        old_mark = digital.price(inputs.spot, strike, T, inputs.domestic_rate, inputs.foreign_rate, inputs.vol,
                                 option_type.lower(), 1.0)["price"] / inputs.spot
        assert premium > 5.0 * old_mark  # the unit error this audit removed

        # Same unit as the fill, so the book's P&L line needs no further conversion:
        # quantity x (PREMIUM - fill) is base-ccy P&L on the payout notional.
        assert 0.0 < fill < 1.0 and abs(quantity * (premium - fill)) < abs(quantity)


@needs_quantlib
def test_digital_writes_every_greek_under_the_official_source_and_delta_can_exceed_one():
    """Task 3: DELTA, GAMMA, VEGA, THETA and RHO are written for a digital exactly as for
    a vanilla, official under QL_OPTIONS_PRICER. A digital a little in the money with two
    months to run moves by several times its payout per unit of spot (CLAUDE.md: DELTA
    "may exceed 1 for digitals")."""
    from engine.options.store import price_and_store

    conn = _new_db()
    _seed_pair_spot(conn, pair="USDJPY", spot=151.0)
    instrument_id = _seed_option_trade(conn, pair="USDJPY", instrument_id="USDJPY111926P-1", strike=152.0,
                                       option_type="PUT", payoff="DIGITAL", expiry="2026-11-19",
                                       quantity=2_000_000.0, price=0.124)
    _seed_vol(conn, pair="USDJPY", expiry="2026-11-19", vol=0.10)

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    official = dict(conn.execute(
        "SELECT mark_type, value FROM marks_official WHERE as_of_date = ? AND instrument_id = ?",
        (AS_OF, instrument_id)).fetchall())
    assert {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"} <= set(official)
    sources = {r[0] for r in conn.execute(
        "SELECT DISTINCT source FROM marks WHERE instrument_id = ?", (instrument_id,)).fetchall()}
    assert sources == {"QL_OPTIONS_PRICER"}
    assert 0.0 < official["PREMIUM"] < 1.0
    assert official["DELTA"] < -1.0  # long digital put: short the base ccy, by more than the payout
    for greek in ("DELTA", "GAMMA", "THETA", "VEGA", "RHO"):
        assert official[greek] == pytest.approx(getattr(outcome.result, greek.lower()))


@needs_quantlib
@pytest.mark.parametrize("payoff", ["VANILLA", "DIGITAL"])
def test_greek_units_are_as_documented(payoff):
    """Pins the unit list in store.py's / pricer.py's docstrings by bump-and-reprice, per
    1 unit of quantity: DELTA per 1.0 of spot in BASE units; GAMMA = change in DELTA per
    1.0 of spot (not per 1 %); VEGA per 1 vol POINT, THETA per 1 CALENDAR day, RHO per 1
    PERCENTAGE POINT of the quote-ccy rate -- those three in QUOTE currency. Tolerances
    are loose because the vendored digital Greeks are themselves 1 %-bump differences; a
    unit error is a factor of 100, 365 or spot, not 10 %."""
    from engine.options import pricer

    fn = pricer.price_fx_vanilla if payoff == "VANILLA" else pricer.price_fx_digital
    as_of, expiry = datetime.date(2026, 9, 15), datetime.date(2026, 11, 19)
    S, K, rd, rf, vol = 150.0, 152.0, 0.005, 0.043, 0.10

    def quote_value(spot=S, domestic=rd, sigma=vol, day=as_of):
        return fn(spot, K, expiry, day, domestic, rf, sigma, "put").quote_price

    base = fn(S, K, expiry, as_of, rd, rf, vol, "put")
    assert base.premium == pytest.approx(base.quote_price / S)  # base-ccy fraction

    h = 0.01 * S
    up, mid, down = quote_value(spot=S + h), quote_value(), quote_value(spot=S - h)
    assert base.delta == pytest.approx((up - down) / (2 * h), rel=0.05)
    assert base.gamma == pytest.approx((up - 2 * mid + down) / h ** 2, rel=0.05)
    assert base.vega == pytest.approx((quote_value(sigma=vol + 0.01) - quote_value(sigma=vol - 0.01)) / 2, rel=0.05)
    assert base.theta == pytest.approx(quote_value(day=as_of + datetime.timedelta(days=1)) - mid, rel=0.10)
    assert base.rho == pytest.approx((quote_value(domestic=rd + 0.01) - quote_value(domestic=rd - 0.01)) / 2, rel=0.05)
    assert base.delta_premium_adjusted == pytest.approx(base.delta - base.premium)


@needs_quantlib
def test_base_payout_digital_replication_agrees_with_inverted_pair_symmetry():
    """Two independent routes to a BASE-payout digital, both built only from vendored
    pricers: static replication (what price_fx_digital uses) and the vendored cash
    digital priced in the inverted pair, mapped back by `_flip_to_quote_terms` (what the
    touch wrappers use). Prices agree to rounding, Greeks to bump-size noise."""
    from engine.options import pricer
    from engine.options.vendor.options_calc.fx import digital

    as_of, expiry = datetime.date(2026, 9, 15), datetime.date(2026, 11, 19)
    S, K, rd, rf, vol = 147.0, 152.0, 0.005, 0.043, 0.10
    T = (expiry - as_of).days / 365.0

    replicated = pricer.price_fx_digital(S, K, expiry, as_of, rd, rf, vol, "put")
    # A put on USDJPY struck 152 pays when 1/S finishes ABOVE 1/152: a call on JPYUSD,
    # whose domestic rate is the USD (this pair's base/foreign) rate.
    flipped = pricer._flip_to_quote_terms(digital.price(1 / S, 1 / K, T, rf, rd, vol, "call", 1.0), S)

    assert replicated.quote_price == pytest.approx(flipped["price"], rel=1e-9)
    assert replicated.premium == pytest.approx(_base_payout_digital_closed_form(S, K, T, rd, rf, vol, "put"), abs=1e-12)
    for greek in ("delta", "gamma", "theta", "vega", "rho"):
        assert getattr(replicated, greek) == pytest.approx(flipped[greek], rel=1e-2), greek
    assert replicated.delta_premium_adjusted == pytest.approx(flipped["delta_premium_adjusted"], rel=1e-2)


@needs_quantlib
def test_quote_payout_keeps_the_vendored_meaning_and_bad_payout_ccy_is_refused():
    from engine.options import pricer
    from engine.options.vendor.options_calc.fx import digital

    as_of, expiry = datetime.date(2026, 9, 15), datetime.date(2026, 11, 19)
    S, K, rd, rf, vol = 150.0, 152.0, 0.005, 0.043, 0.10
    T = (expiry - as_of).days / 365.0

    quote = pricer.price_fx_digital(S, K, expiry, as_of, rd, rf, vol, "put", cash_payout=K, payout_ccy="QUOTE")
    vendored = digital.price(S, K, T, rd, rf, vol, "put", K)
    assert quote.quote_price == pytest.approx(vendored["price"])
    assert quote.premium == pytest.approx(vendored["price"] / S)
    # "Base notional converted at the strike" (K JPY per USD of quantity) is worth the
    # BASE-payout digital plus the vanilla put: the payout currency is a real choice.
    base = pricer.price_fx_digital(S, K, expiry, as_of, rd, rf, vol, "put")
    vanilla = pricer.price_fx_vanilla(S, K, expiry, as_of, rd, rf, vol, "put")
    assert quote.premium == pytest.approx(base.premium + vanilla.premium)

    with pytest.raises(ValueError):
        pricer.price_fx_digital(S, K, expiry, as_of, rd, rf, vol, "put", payout_ccy="JPY")


@needs_quantlib
def test_payoff_dispatch_digital_reaches_price_fx_digital_vanilla_reaches_price_fx_vanilla(monkeypatch):
    """Task 2: the stored payoff alone picks the pricer."""
    from engine.options import pricer, store

    calls = []
    real = {name: getattr(pricer, name) for name in ("price_fx_vanilla", "price_fx_digital")}

    def spy(name):
        def wrapped(*args, **kwargs):
            calls.append((name, kwargs.get("payout_ccy")))
            return real[name](*args, **kwargs)
        return wrapped

    for name in real:
        monkeypatch.setattr(pricer, name, spy(name))

    conn = _new_db()
    _seed_pair_spot(conn)
    _seed_vol(conn)
    _seed_option_trade(conn, trade_id="V", instrument_id="EURUSD092326C-V", payoff="VANILLA")
    _seed_option_trade(conn, trade_id="D", instrument_id="EURUSD092326C-D", payoff="DIGITAL")

    assert store.price_and_store(conn, AS_OF, "V").priced
    assert calls == [("price_fx_vanilla", None)]
    calls.clear()
    assert store.price_and_store(conn, AS_OF, "D").priced
    assert calls == [("price_fx_digital", "BASE")]


def test_sample_digitals_arrive_as_vanilla_with_no_strike_and_are_skipped_not_priced(tmp_path):
    """Task 2: the export does not mark its digital as digital (`FxOption Type` is '0';
    no payoff word or strike in the Description), so it lands as payoff VANILLA, strike
    0 -- and with every market input on file it is still SKIPPED "no strike", never
    priced at strike 0 or at spot.

    The 5 sample options split 2 / 1 / 2: 2 priced, the digital skipped "no strike",
    and the 2 EURUSD 1.15 puts (bought 910000044, sold back in full 910000045) a
    closed-out group (CLAUDE.md, "A closed-out option is not live"): not priced,
    ``closed_out=True``, under their own head and never among the skips."""
    if not HAVE_QUANTLIB:
        pytest.skip("QuantLib not installed")
    from engine.options.store import price_all_and_store

    conn = _sample_book(tmp_path)
    _seed_audit_market(conn)
    on_file = {r[1]: (r[6], r[8]) for r in _sample_option_trades(conn)}
    for instrument_id in SAMPLE_DIGITAL_TERMS:
        assert on_file[instrument_id] == (0.0, "VANILLA")

    outcomes = {o.instrument_id: o for o in price_all_and_store(conn, AUDIT_AS_OF)}
    assert len(outcomes) == 5
    for instrument_id in SAMPLE_DIGITAL_TERMS:
        assert not outcomes[instrument_id].priced
        assert not outcomes[instrument_id].closed_out
        assert outcomes[instrument_id].reason.startswith("no strike")
        assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = ?", (instrument_id,)).fetchone()[0] == 0
    assert {i for i, o in outcomes.items() if o.closed_out} == SAMPLE_CLOSED_OUT
    for instrument_id in SAMPLE_CLOSED_OUT:
        assert not outcomes[instrument_id].priced
        assert outcomes[instrument_id].reason.startswith(f"closed out {SAMPLE_CLOSED_ON}:")
        assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = ?", (instrument_id,)).fetchone()[0] == 0
    assert {i for i, o in outcomes.items() if not o.priced and not o.closed_out} == set(SAMPLE_DIGITAL_TERMS)
    assert {i for i, o in outcomes.items() if o.priced} == {"EURUSD111826C-500041", "USDJPY121626P-500042"}


@needs_quantlib
def test_set_option_terms_default_payoff_is_vanilla_and_misprices_a_digital_silently():
    """Task 2, the hazard: `set_option_terms(conn, id, strike, option_type)` with the
    payoff left out WRITES 'VANILLA'. A digital saved that way prices without any error,
    as a vanilla, nowhere near its value -- so a strike-only editor must pass the payoff
    (`on_file_terms` hands back what is on file)."""
    from engine.options.store import on_file_terms, price_and_store, set_option_terms

    conn = _new_db()
    _seed_pair_spot(conn, pair="USDJPY", spot=150.0)
    instrument_id = _seed_option_trade(conn, pair="USDJPY", instrument_id="USDJPY111926P-1", strike=0.0,
                                       option_type="PUT", payoff="VANILLA", expiry="2026-11-19",
                                       quantity=2_000_000.0, price=0.124)
    _seed_vol(conn, pair="USDJPY", expiry="2026-11-19", vol=0.10)

    set_option_terms(conn, instrument_id, 152.0, "PUT")
    assert on_file_terms(conn, instrument_id)["payoff"] == "VANILLA"
    as_vanilla = price_and_store(conn, AS_OF, "T1")
    assert as_vanilla.priced  # no error, no warning: the mis-stored digital just prices

    set_option_terms(conn, instrument_id, 152.0, "PUT", "DIGITAL")
    as_digital = price_and_store(conn, AS_OF, "T1")
    assert as_digital.priced
    assert as_digital.result.premium > 10.0 * as_vanilla.result.premium

    # Editing one term without losing the others: pass back what is on file.
    terms = on_file_terms(conn, instrument_id)
    set_option_terms(conn, instrument_id, 151.0, terms["option_type"], terms["payoff"], terms["barrier_level"])
    assert on_file_terms(conn, instrument_id) == {"strike": 151.0, "option_type": "PUT", "payoff": "DIGITAL",
                                                  "barrier_level": 0.0}
    # ...whereas leaving the payoff out puts VANILLA back.
    set_option_terms(conn, instrument_id, 151.0, "PUT")
    assert on_file_terms(conn, instrument_id)["payoff"] == "VANILLA"


@needs_quantlib
def test_strike_cell_sequence_on_the_sample_book_and_skip_reasons_name_the_missing_input(tmp_path):
    """Task 4: the UI's editable strike cell calls `set_option_terms(conn, instrument_id,
    strike, option_type, ...)` then `price_and_store(conn, as_of, trade_id)`. End to end
    on the sample book for the USDJPY 152 digital put, and every way it can still be
    skipped comes back as a `reason` naming the one missing input (a currency with no
    rate at all: test_no_curve_skip_reason_for_currency_without_ois_convention)."""
    from engine.options.inputs import FLAT_TENOR, set_manual_vol
    from engine.options.store import price_and_store, set_option_terms

    conn = _sample_book(tmp_path)
    _seed_audit_market(conn, spot=False, vol=False)  # curves only
    trades = {r[1]: r[0] for r in _sample_option_trades(conn)}
    usdjpy = "USDJPY111926P-500043"

    assert price_and_store(conn, AUDIT_AS_OF, trades[usdjpy]).reason.startswith("no strike")
    set_option_terms(conn, usdjpy, 152.0, "PUT", "DIGITAL")

    def mark_spot(pair):
        conn.execute(
            "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
            "snapped_at) VALUES (?,?,?,?,?,?,?)",
            (AUDIT_AS_OF, pair, AUDIT_AS_OF, "SPOT", AUDIT_SPOT[pair], "BBG_BFXFORWARD",
             f"{AUDIT_AS_OF}T17:00:00-04:00"))
        conn.commit()

    assert price_and_store(conn, AUDIT_AS_OF, trades[usdjpy]).reason == "no SPOT mark"
    mark_spot("USDJPY")
    assert price_and_store(conn, AUDIT_AS_OF, trades[usdjpy]).reason == "no vol"
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = ?", (usdjpy,)).fetchone()[0] == 0
    set_manual_vol(conn, AUDIT_AS_OF, "USDJPY", FLAT_TENOR, AUDIT_VOL["USDJPY"])

    outcome = price_and_store(conn, AUDIT_AS_OF, trades[usdjpy])
    assert outcome.priced, outcome.reason
    assert outcome.reason == ""
    assert outcome.quantity == 2_000_000.0
    assert 0.0 < _official_mark(conn, AUDIT_AS_OF, usdjpy, "PREMIUM") < 1.0
    for greek in ("DELTA", "GAMMA", "THETA", "VEGA", "RHO"):
        assert _official_mark(conn, AUDIT_AS_OF, usdjpy, greek) is not None

    with pytest.raises(ValueError):
        price_and_store(conn, AUDIT_AS_OF, "no-such-trade")


@needs_quantlib
def test_portfolio_delta_is_a_usd_notional_and_gamma_is_per_one_percent_move():
    """2026-09-18: portfolio.py used to push DELTA and GAMMA through the quote-ccy ->
    USD factor like THETA/VEGA/RHO, which made a USDJPY delta 150x too small (and a sum
    across pairs meaningless). USD is USDJPY's base currency, so the USD delta is simply
    quantity x DELTA; gamma is the change in that for a 1 % spot move; vega/theta/rho and
    the market value convert from JPY at spot."""
    from engine.options.portfolio import portfolio_summary
    from engine.options.store import price_and_store

    conn = _new_db()
    spot, quantity = 150.0, 2_000_000.0
    _seed_pair_spot(conn, pair="USDJPY", spot=spot)
    _seed_option_trade(conn, pair="USDJPY", instrument_id="USDJPY092326P-1", strike=150.0, option_type="PUT",
                       quantity=quantity)
    _seed_vol(conn, pair="USDJPY")

    outcome = price_and_store(conn, AS_OF, "T1")
    assert outcome.priced, outcome.reason
    portfolio, legs, skipped = portfolio_summary(conn, AS_OF, [outcome])
    assert not skipped and len(legs) == 1
    total, r = portfolio.total(), outcome.result

    assert total["delta"] == pytest.approx(quantity * r.delta)                 # USD, b2u = 1
    assert total["gamma"] == pytest.approx(quantity * r.gamma * spot / 100.0)  # USD delta per 1 %
    assert total["price"] == pytest.approx(quantity * r.premium)               # market value, USD
    for greek in ("vega", "theta", "rho"):
        assert total[greek] == pytest.approx(quantity * getattr(r, greek) / spot)  # JPY -> USD


# --------------------------------------------------------------------------- 2026-09-18: stale marks after a terms change
#
# The Options table types a strike / picks Vanilla-Digital in the cell, calls
# set_option_terms and then price_and_store. When that reprice is skipped, the marks
# priced under the OLD terms used to stay official. set_option_terms now deletes them --
# all dates, all seven types, QL_OPTIONS_PRICER only -- in the terms write's transaction.

SECOND_AS_OF = "2026-08-24"
SEVEN_TYPES = {"PREMIUM", "DELTA", "DELTA_PA", "GAMMA", "THETA", "VEGA", "RHO"}


def _price_on_two_dates(conn, **trade_kwargs):
    """One EURUSD option (premium-adjusted pair, so DELTA_PA is written too) priced on
    AS_OF and SECOND_AS_OF. Returns its instrument_id."""
    from engine.options.store import price_and_store

    instrument_id = _seed_option_trade(conn, **trade_kwargs)
    for as_of in (AS_OF, SECOND_AS_OF):
        _seed_pair_spot(conn, as_of=as_of)
        _seed_vol(conn, as_of=as_of)
        outcome = price_and_store(conn, as_of, trade_kwargs.get("trade_id", "T1"))
        assert outcome.priced, outcome.reason
    return instrument_id


def _pricer_marks(conn, instrument_id):
    return conn.execute(
        "SELECT as_of_date, mark_type, value FROM marks WHERE instrument_id = ? AND source = 'QL_OPTIONS_PRICER' "
        "ORDER BY as_of_date, mark_type", (instrument_id,)).fetchall()


@needs_quantlib
def test_terms_change_clears_all_seven_pricer_mark_types_across_both_dates():
    from engine.options.store import PRICER_MARK_TYPES, set_option_terms

    assert set(PRICER_MARK_TYPES) == SEVEN_TYPES
    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    before = _pricer_marks(conn, instrument_id)
    assert {(d, t) for d, t, _ in before} == {(d, t) for d in (AS_OF, SECOND_AS_OF) for t in SEVEN_TYPES}

    set_option_terms(conn, instrument_id, STRIKE, "CALL", "DIGITAL")  # the vanilla was a digital all along

    assert _pricer_marks(conn, instrument_id) == []
    assert conn.execute("SELECT COUNT(*) FROM marks_official WHERE instrument_id = ?", (instrument_id,)).fetchone()[0] == 0
    # The pair's own SPOT marks are not the option's and are untouched.
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = ? AND mark_type = 'SPOT'",
                        (EURUSD,)).fetchone()[0] == 2


@needs_quantlib
@pytest.mark.parametrize("new_terms", [
    (STRIKE + 0.05, "CALL", "VANILLA", 0.0),   # strike
    (STRIKE, "PUT", "VANILLA", 0.0),           # call -> put
    (STRIKE, "CALL", "DIGITAL", 0.0),          # payoff
    (STRIKE, "CALL", "BARRIER_KO", 12.0),      # payoff + barrier
], ids=["strike", "option_type", "payoff", "barrier"])
def test_each_pricing_term_change_clears_the_marks(new_terms):
    from engine.options.store import set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    assert _pricer_marks(conn, instrument_id)
    set_option_terms(conn, instrument_id, *new_terms)
    assert _pricer_marks(conn, instrument_id) == []


@needs_quantlib
def test_barrier_level_change_alone_clears_the_marks_and_first_terms_write_does_too():
    from engine.options.store import set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn, payoff="BARRIER_KO", barrier_level=12.0)
    set_option_terms(conn, instrument_id, STRIKE, "CALL", "BARRIER_KO", 12.5)
    assert _pricer_marks(conn, instrument_id) == []

    # No instrument_options row at all (terms never recorded) but pricer marks on file
    # from some earlier life of the instrument: nothing vouches for them, so they go.
    conn2 = _new_db()
    instrument_id2 = _price_on_two_dates(conn2)
    conn2.execute("DELETE FROM instrument_options WHERE instrument_id = ?", (instrument_id2,))
    conn2.commit()
    set_option_terms(conn2, instrument_id2, STRIKE, "CALL")
    assert _pricer_marks(conn2, instrument_id2) == []


@needs_quantlib
def test_identical_resave_of_terms_clears_nothing():
    """The UI re-saves unchanged terms routinely: same strike (even after a trip through a
    text box), same call/put and payoff in any letter case, same barrier -> no delete."""
    from engine.options.store import on_file_terms, set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    before = _pricer_marks(conn, instrument_id)
    assert len(before) == 14

    set_option_terms(conn, instrument_id, STRIKE, "CALL", "VANILLA", 0.0)
    set_option_terms(conn, instrument_id, float(f"{STRIKE:.6f}"), "call", "vanilla")
    set_option_terms(conn, instrument_id, STRIKE, "CALL")  # payoff / barrier left at their defaults = what is on file
    terms = on_file_terms(conn, instrument_id)
    set_option_terms(conn, instrument_id, terms["strike"], terms["option_type"], terms["payoff"], terms["barrier_level"])

    assert _pricer_marks(conn, instrument_id) == before


@needs_quantlib
def test_terms_change_never_touches_manual_marks_or_another_instruments_marks():
    from engine.options.store import price_and_store, set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    other_id = _seed_option_trade(conn, trade_id="T2", instrument_id="EURUSD092326P-OTHER", option_type="PUT")
    assert price_and_store(conn, AS_OF, "T2").priced
    other_before = _pricer_marks(conn, other_id)

    manual_rows = [
        (AS_OF, instrument_id, EXPIRY, "PREMIUM", 0.0061, "MANUAL", f"{AS_OF}T17:00:00-04:00"),
        (AS_OF, instrument_id, EXPIRY, "DELTA", 0.52, "MANUAL", f"{AS_OF}T17:00:00-04:00"),
        (SECOND_AS_OF, instrument_id, EXPIRY, "PREMIUM", 0.0058, "MANUAL", f"{SECOND_AS_OF}T17:00:00-04:00"),
    ]
    conn.executemany(
        "INSERT INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)", manual_rows)
    conn.commit()

    set_option_terms(conn, instrument_id, STRIKE, "CALL", "DIGITAL")

    assert _pricer_marks(conn, instrument_id) == []
    survivors = conn.execute(
        "SELECT as_of_date, mark_type, value, source FROM marks WHERE instrument_id = ? ORDER BY as_of_date, mark_type",
        (instrument_id,)).fetchall()
    assert survivors == [(AS_OF, "DELTA", 0.52, "MANUAL"), (AS_OF, "PREMIUM", 0.0061, "MANUAL"),
                         (SECOND_AS_OF, "PREMIUM", 0.0058, "MANUAL")]
    assert _pricer_marks(conn, other_id) == other_before  # scoped to the one instrument


@needs_quantlib
def test_refused_terms_write_deletes_nothing():
    """Same transaction: a terms write that is refused (validation) or fails leaves the
    marks exactly as they were."""
    from engine.options.store import set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    before = _pricer_marks(conn, instrument_id)
    with pytest.raises(ValueError):
        set_option_terms(conn, instrument_id, 0.0, "CALL", "DIGITAL")  # a digital needs a strike
    with pytest.raises(ValueError):
        set_option_terms(conn, instrument_id, STRIKE, "CALL", "STRADDLE")
    assert _pricer_marks(conn, instrument_id) == before


@needs_quantlib
def test_skipped_reprice_after_a_terms_change_leaves_no_official_premium():
    """The case that prompted this: priced as a vanilla, the user corrects it to a
    digital in the cell, and the reprice that follows is SKIPPED (the vol is gone). The
    option must show as unpriced with that skip reason -- no official PREMIUM or Greek on
    ANY date -- not keep the vanilla's numbers."""
    from engine.options.store import price_and_store, set_option_terms

    conn = _new_db()
    instrument_id = _price_on_two_dates(conn)
    stale = _official_mark(conn, SECOND_AS_OF, instrument_id, "PREMIUM")
    assert stale is not None and stale > 0

    conn.execute("DELETE FROM option_vols")  # today's reprice will have no vol to use
    conn.commit()
    set_option_terms(conn, instrument_id, STRIKE, "CALL", "DIGITAL")
    outcome = price_and_store(conn, SECOND_AS_OF, "T1")

    assert not outcome.priced
    assert outcome.reason == "no vol"
    for as_of in (AS_OF, SECOND_AS_OF):
        for mark_type in SEVEN_TYPES:
            assert _official_mark(conn, as_of, instrument_id, mark_type) is None, (as_of, mark_type)

    # Once the input is back, the digital prices under its own terms -- a different number.
    _seed_vol(conn, as_of=SECOND_AS_OF)
    repriced = price_and_store(conn, SECOND_AS_OF, "T1")
    assert repriced.priced, repriced.reason
    fresh = _official_mark(conn, SECOND_AS_OF, instrument_id, "PREMIUM")
    assert fresh == pytest.approx(repriced.result.premium) and fresh > 10 * stale
    assert _official_mark(conn, AS_OF, instrument_id, "PREMIUM") is None  # the earlier date is not repriced by this


# --------------------------------------------------------------------------- 2026-09-18: frozen realised P&L after a terms change
#
# engine/pnl/ledger.py::realise_settled freezes an expired option ONCE, from the last
# official PREMIUM on or before expiry, and never revisits a trade that has a row. A row
# frozen from a premium priced under the wrong terms is not a realised P&L, so
# set_option_terms drops the instrument's realised_pnl rows under the same condition and
# in the same transaction as the pricer marks (mirrors data/ingest/irs_direction.py).

def _insert_realised_row(conn, trade_id, instrument_id, pnl_usd=-12_345.0):
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, instrument_id, "FX_OPTION", "EUR", EXPIRY, NOTIONAL, NOTIONAL * PREMIUM_FILL * 1.17, "PREMIUM",
         0.0045, EXPIRY, "QL_OPTIONS_PRICER", pnl_usd, "2026-09-24T09:00:00+00:00", f"premium dated {EXPIRY}"))
    conn.commit()


def _realised_trade_ids(conn):
    return [r[0] for r in conn.execute("SELECT trade_id FROM realised_pnl ORDER BY trade_id").fetchall()]


def test_terms_change_drops_that_instruments_realised_row_and_leaves_another_instruments():
    from engine.options.store import set_option_terms

    conn = _new_db()
    mine = _seed_option_trade(conn, trade_id="T1", instrument_id="EURUSD092326C-MINE")
    mine_too = "T1b"  # a second ticket on the SAME instrument goes with it
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
        "account, counterparty, strategy, trader, description, theme) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mine_too, "XLSX", mine, "FX_OPTION", mine_too, "2026-08-19", -NOTIONAL, 0.0061, "A", "C", "", "JB", "", ""))
    other = _seed_option_trade(conn, trade_id="T2", instrument_id="EURUSD092326P-OTHER", option_type="PUT")
    for trade_id, instrument_id in (("T1", mine), (mine_too, mine), ("T2", other)):
        _insert_realised_row(conn, trade_id, instrument_id)
    assert _realised_trade_ids(conn) == ["T1", "T1b", "T2"]

    set_option_terms(conn, mine, STRIKE, "CALL", "DIGITAL")  # payoff changes

    assert _realised_trade_ids(conn) == ["T2"]
    assert conn.execute("SELECT pnl_usd FROM realised_pnl WHERE trade_id = 'T2'").fetchone()[0] == -12_345.0


def test_identical_resave_and_refused_write_leave_the_realised_row():
    from engine.options.store import set_option_terms

    conn = _new_db()
    instrument_id = _seed_option_trade(conn)
    _insert_realised_row(conn, "T1", instrument_id)

    set_option_terms(conn, instrument_id, STRIKE, "CALL", "VANILLA", 0.0)      # exactly what is on file
    set_option_terms(conn, instrument_id, float(f"{STRIKE:.6f}"), "call")      # same, via text and defaults
    with pytest.raises(ValueError):
        set_option_terms(conn, instrument_id, 0.0, "CALL", "DIGITAL")          # refused: a digital needs a strike
    assert _realised_trade_ids(conn) == ["T1"]

    set_option_terms(conn, instrument_id, STRIKE + 0.01, "CALL")               # a real change
    assert _realised_trade_ids(conn) == []


def test_terms_change_without_a_realised_pnl_table_does_not_raise():
    """A database that has never been through the app's startup has no ledger table:
    nothing is frozen there, so there is nothing to drop -- and nothing is created."""
    from engine.options.store import on_file_terms, set_option_terms

    conn = _new_db()
    instrument_id = _seed_option_trade(conn)
    conn.execute("DROP TABLE realised_pnl")
    conn.commit()

    set_option_terms(conn, instrument_id, STRIKE, "CALL", "DIGITAL")

    assert on_file_terms(conn, instrument_id)["payoff"] == "DIGITAL"
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'realised_pnl'").fetchone()[0] == 0


@needs_quantlib
def test_expired_option_is_frozen_again_from_the_corrected_premium_end_to_end():
    """engine/pnl/ledger.py used READ-ONLY. An option priced as a vanilla expires and is
    frozen by `realise_settled`; the user then corrects it to a digital. The frozen row
    goes with the stale marks; with no corrected PREMIUM on or before expiry the ledger
    names the trade as unrealisable rather than inventing a figure; once one is written
    the next pass freezes the trade afresh, from the corrected premium."""
    from engine.options.store import price_and_store, set_option_terms
    from engine.pnl.ledger import realise_settled

    pair, spot, strike, expiry, after_expiry = "EURUSD", 1.17, 1.17, "2026-08-28", "2026-08-31"
    quantity, fill = 1_000_000.0, 0.12
    conn = _new_db()
    _seed_pair_spot(conn, pair=pair, spot=spot)  # also the EUR -> USD conversion the ledger needs on AS_OF
    instrument_id = _seed_option_trade(conn, instrument_id="EURUSD082826C-1", strike=strike, expiry=expiry,
                                       quantity=quantity, price=fill)
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, settles_cash) "
        "VALUES (?,?,?,?,?,?,?,?,?)", ("T1", 1, "NOTIONAL", "EUR", quantity, "2026-08-18", expiry, 0.0, 0))
    conn.commit()
    _seed_vol(conn, expiry=expiry)

    as_vanilla = price_and_store(conn, AS_OF, "T1")
    assert as_vanilla.priced, as_vanilla.reason
    assert realise_settled(conn, after_expiry)["realised"] == 1
    frozen = conn.execute("SELECT pnl_usd, spot_as_of_date, spot_source FROM realised_pnl WHERE trade_id = 'T1'").fetchone()
    assert frozen[0] == pytest.approx(quantity * (as_vanilla.result.premium - fill) * spot)
    assert frozen[1:] == (AS_OF, "QL_OPTIONS_PRICER")

    set_option_terms(conn, instrument_id, strike, "CALL", "DIGITAL")
    assert _realised_trade_ids(conn) == []
    # No corrected PREMIUM on or before expiry yet: named, not valued.
    pending = realise_settled(conn, after_expiry)
    assert pending["realised"] == 0
    assert [u["trade_id"] for u in pending["unrealisable"]] == ["T1"]
    assert "no official PREMIUM" in pending["unrealisable"][0]["reason"]

    as_digital = price_and_store(conn, AS_OF, "T1")  # the corrected premium, dated before expiry
    assert as_digital.priced, as_digital.reason
    assert as_digital.result.premium > 10 * as_vanilla.result.premium

    assert realise_settled(conn, after_expiry)["realised"] == 1
    refrozen = conn.execute("SELECT pnl_usd, spot_as_of_date FROM realised_pnl WHERE trade_id = 'T1'").fetchone()
    assert refrozen[0] == pytest.approx(quantity * (as_digital.result.premium - fill) * spot)
    assert refrozen[0] != pytest.approx(frozen[0])
    assert refrozen[1] == AS_OF
    # A third pass changes nothing: frozen once, from the corrected marks.
    assert realise_settled(conn, after_expiry)["realised"] == 0


# --------------------------------------------------------------------------- 2026-09-18: expiry-day intrinsic mark
#
# On its expiry date an option is marked at its PAYOFF at the pair's official SPOT for
# that date -- same unit as every other PREMIUM -- so the ledger, which freezes an option
# the day AFTER expiry at the last official PREMIUM on or before expiry, freezes the
# payoff and not a T-1 model premium. The sample book's four struck tickets (the digital
# has no strike on file) expire 21 Oct, 18 Nov and 16 Dec 2026.

STRUCK_TICKETS = {  # instrument_id: (pair, expiry, strike, call/put, signed quantity, fill)
    instrument_id: (pair, expiry, strike, option_type, quantity, fill)
    for instrument_id, (_, _, pair, expiry, strike, option_type, quantity, fill, _) in SAMPLE_OPTIONS.items()
    if strike > 0
}
SOLD_PUT = "EURUSD102126P-500045"
EXPIRY_SPOTS = {"above": {"EURUSD": 1.2000, "USDJPY": 147.0}, "below": {"EURUSD": 1.1300, "USDJPY": 140.0}}
ZERO_GREEKS = ("GAMMA", "THETA", "VEGA", "RHO")


def _mark_spot(conn, as_of, pair, spot):
    """ONLY a SPOT mark -- no curve, no vol: the expiry-day mark needs nothing else."""
    conn.execute(
        "INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
        (pair, "FX", pair[:3], pair[3:], 1.0, 0, f"{pair} Curncy", "9999-12-31"))
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)", (as_of, pair, as_of, "SPOT", spot, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"))
    conn.commit()


def _option_marks(conn, instrument_id):
    """{as_of_date: {mark_type: value}} for the option's QL_OPTIONS_PRICER marks."""
    out = {}
    for as_of, mark_type, value in _pricer_marks(conn, instrument_id):
        out.setdefault(as_of, {})[mark_type] = value
    return out


def _payoff_fraction(option_type, S, K):
    return (max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)) / S


@needs_quantlib
@pytest.mark.parametrize("where", ["above", "below"])
def test_expiry_day_mark_is_the_payoff_for_each_of_the_sample_books_struck_tickets(tmp_path, where):
    """The four struck tickets, spot above and below the strike. PREMIUM is the
    hand-computed payoff as a fraction of base notional (EUR / USD), and quantity x
    PREMIUM x S is (S-K) x notional in the QUOTE currency. There is no vol and no curve anywhere in
    this database: nothing but the day's SPOT is needed, and no model is involved."""
    from engine.options.store import price_and_store

    conn = _sample_book(tmp_path)
    trades = {r[1]: r for r in _sample_option_trades(conn)}
    for instrument_id, (pair, expiry, strike, option_type, quantity, fill) in STRUCK_TICKETS.items():
        trade_id, _, qty_on_file, fill_on_file, pair_on_file, expiry_on_file, strike_on_file, type_on_file, _ = trades[instrument_id]
        assert (qty_on_file, fill_on_file, pair_on_file, expiry_on_file, strike_on_file, type_on_file) == \
               (quantity, fill, pair, expiry, strike, option_type)
        S = EXPIRY_SPOTS[where][pair]
        _mark_spot(conn, expiry, pair, S)

        outcome = price_and_store(conn, expiry, trade_id)
        assert outcome.priced, (instrument_id, outcome.reason)
        assert (outcome.mark_basis, outcome.mark_date) == ("INTRINSIC", expiry)
        assert outcome.vol_source_kind is None and outcome.domestic_rate_source_kind is None  # no model inputs

        marks = _option_marks(conn, instrument_id)
        assert list(marks) == [expiry]  # dated the expiry date, source QL_OPTIONS_PRICER, nothing else
        today = marks[expiry]
        in_the_money = (S > strike) if option_type == "CALL" else (S < strike)

        assert today["PREMIUM"] == pytest.approx(_payoff_fraction(option_type, S, strike), abs=1e-15)
        assert _official_mark(conn, expiry, instrument_id, "PREMIUM") == today["PREMIUM"]
        intrinsic_quote_ccy = (S - strike) if option_type == "CALL" else (strike - S)
        expected_value = quantity * intrinsic_quote_ccy if in_the_money else 0.0
        assert quantity * today["PREMIUM"] * S == pytest.approx(expected_value, abs=1e-3)  # USD / JPY

        assert today["DELTA"] == ((1.0 if option_type == "CALL" else -1.0) if in_the_money else 0.0)
        for greek in ZERO_GREEKS:
            assert today[greek] == 0.0
        if outcome.result.delta_convention == "PREMIUM_ADJUSTED":
            assert today["DELTA_PA"] == pytest.approx(today["DELTA"] - today["PREMIUM"])
        else:
            assert "DELTA_PA" not in today

        # The book's line, base ccy: quantity x (PREMIUM - fill). The SOLD put keeps its
        # whole premium when it dies out of the money and owes the payoff when not.
        pnl_base = quantity * (today["PREMIUM"] - fill)
        if instrument_id == SOLD_PUT:
            assert quantity < 0 and quantity * today["PREMIUM"] <= 0.0
            if where == "above":   # out of the money: +EUR 15,500, the premium received
                assert pnl_base == pytest.approx(5_000_000 * 0.0031)
            else:                  # in the money: premium received minus the payoff owed
                assert pnl_base == pytest.approx(5_000_000 * 0.0031 - 5_000_000 * (strike - S) / S)
        elif not in_the_money:
            assert pnl_base == pytest.approx(-quantity * fill)  # a bought option that dies worthless loses its premium


@needs_quantlib
def test_expiry_day_digital_in_and_out_of_the_money_and_everything_is_zero_at_the_strike():
    """A BASE-payout digital is worth 1.0 x its payout in the money, else 0.0. At S == K
    nothing pays -- the vendored pricers' (QuantLib's) own strict convention, call pays
    if S > K, put if S < K -- for the digital and the vanilla alike."""
    import QuantLib as ql

    from engine.options.store import price_and_store

    K, expiry = 152.0, "2026-11-19"
    assert ql.CashOrNothingPayoff(ql.Option.Put, K, 1.0)(K) == 0.0
    assert ql.CashOrNothingPayoff(ql.Option.Call, K, 1.0)(K) == 0.0
    assert ql.PlainVanillaPayoff(ql.Option.Put, K)(K) == 0.0

    conn = _new_db()
    digital = _seed_option_trade(conn, trade_id="D", pair="USDJPY", instrument_id="USDJPY111926P-D", strike=K,
                                 option_type="PUT", payoff="DIGITAL", expiry=expiry, quantity=2_000_000.0, price=0.124)
    vanilla = _seed_option_trade(conn, trade_id="V", pair="USDJPY", instrument_id="USDJPY111926P-V", strike=K,
                                 option_type="PUT", payoff="VANILLA", expiry=expiry, quantity=2_000_000.0, price=0.02)
    for spot, digital_premium, digital_delta, vanilla_delta in ((150.0, 1.0, 1.0, -1.0),   # in the money
                                                                (155.0, 0.0, 0.0, 0.0),    # out of the money
                                                                (K, 0.0, 0.0, 0.0)):       # S == K: nothing pays
        _mark_spot(conn, expiry, "USDJPY", spot)
        for trade_id in ("D", "V"):
            assert price_and_store(conn, expiry, trade_id).priced
        d, v = _option_marks(conn, digital)[expiry], _option_marks(conn, vanilla)[expiry]
        assert d["PREMIUM"] == digital_premium            # a fraction of the USD 2,000,000 payout
        assert 2_000_000.0 * d["PREMIUM"] in (0.0, 2_000_000.0)
        # In the money the holder is about to receive one BASE unit: worth S in quote ccy,
        # so d(PREMIUM x S)/dS = +1 (put and call alike); nothing otherwise.
        assert d["DELTA"] == digital_delta
        assert v["PREMIUM"] == pytest.approx(max(K - spot, 0.0) / spot) and v["DELTA"] == vanilla_delta
        for greek in ZERO_GREEKS:
            assert d[greek] == 0.0 and v[greek] == 0.0
        assert "DELTA_PA" not in d  # USDJPY quotes raw delta

    # A premium-adjusted pair: the in-the-money digital's DELTA_PA = DELTA - PREMIUM = 0.
    call = _seed_option_trade(conn, trade_id="C", pair="EURSEK", instrument_id="EURSEK112526C-C", strike=11.4,
                              option_type="CALL", payoff="DIGITAL", expiry="2026-11-25", quantity=1_000_000.0, price=0.121)
    _mark_spot(conn, "2026-11-25", "EURSEK", 11.55)
    outcome = price_and_store(conn, "2026-11-25", "C")
    c = _option_marks(conn, call)["2026-11-25"]
    assert (c["PREMIUM"], c["DELTA"]) == (1.0, 1.0)
    if outcome.result.delta_convention == "PREMIUM_ADJUSTED":
        assert c["DELTA_PA"] == 0.0


@needs_quantlib
def test_expiry_day_greeks_continue_the_model_greeks_of_the_day_before():
    """Why the expiry-day DELTA is what it is: deep in the money with one day left, the
    MODEL already says +1 for a call, -1 for a put -- and +1 for a BASE-payout digital,
    put or call (one base unit about to be received is worth S in quote ccy), with the
    other Greeks at ~0. The expiry-day marks are the limit of those, not a new convention."""
    from engine.options import pricer

    eve, expiry = datetime.date(2026, 11, 18), datetime.date(2026, 11, 19)
    rd, rf, vol, K = 0.005, 0.043, 0.10, 152.0
    for payoff, option_type, S in (("VANILLA", "call", 165.0), ("VANILLA", "put", 140.0),
                                   ("DIGITAL", "put", 140.0), ("DIGITAL", "call", 165.0)):
        model_fn = pricer.price_fx_vanilla if payoff == "VANILLA" else pricer.price_fx_digital
        model = model_fn(S, K, expiry, eve, rd, rf, vol, option_type, pair="USDJPY")
        at_expiry = pricer.price_fx_at_expiry(payoff, S, K, option_type, pair="USDJPY")
        assert model.delta == pytest.approx(at_expiry.delta, abs=1e-3), (payoff, option_type)
        assert model.premium == pytest.approx(at_expiry.premium, abs=1e-3)
        assert model.gamma == pytest.approx(0.0, abs=1e-6) and model.vega == pytest.approx(0.0, abs=1e-6)
        assert (at_expiry.gamma, at_expiry.theta, at_expiry.vega, at_expiry.rho) == (0.0, 0.0, 0.0, 0.0)
    # A QUOTE-payout digital is a fixed amount of quote ccy: no spot delta at all.
    assert pricer.price_fx_at_expiry("DIGITAL", 140.0, K, "put", payout_ccy="QUOTE").delta == 0.0
    with pytest.raises(ValueError):
        pricer.price_fx_at_expiry("ONE_TOUCH", 140.0, K, "put")


@needs_quantlib
def test_expiry_day_without_a_spot_is_skipped_and_never_falls_back_to_the_model():
    """Yesterday's spot, both curves and a vol are all on file -- everything a model price
    needs -- but not today's SPOT: skipped with the existing reason, nothing written."""
    from engine.options.store import price_and_store

    conn = _new_db()
    instrument_id = _full_setup(conn)
    _seed_ois_curves_for_pair(conn, EXPIRY, EURUSD)
    _seed_vol(conn, as_of=EXPIRY)
    conn.commit()

    outcome = price_and_store(conn, EXPIRY, "T1")
    assert not outcome.priced
    assert outcome.reason == "no SPOT mark"
    assert _pricer_marks(conn, instrument_id) == []


@needs_quantlib
def test_expiry_day_mark_follows_the_days_last_spot_and_the_day_after_writes_nothing_new():
    from engine.options.store import price_all_and_store, price_and_store

    conn = _new_db()
    instrument_id = _seed_option_trade(conn)  # EURUSD fixture: strike 11.0584 CALL, expiry 2026-09-23
    day_after = "2026-09-24"

    _mark_spot(conn, EXPIRY, EURUSD, 11.10)
    assert price_and_store(conn, EXPIRY, "T1").priced
    first = _official_mark(conn, EXPIRY, instrument_id, "PREMIUM")
    assert first == pytest.approx((11.10 - STRIKE) / 11.10)

    _mark_spot(conn, EXPIRY, EURUSD, 11.25)  # a later pull the same day
    assert price_all_and_store(conn, EXPIRY)[0].priced
    last = _official_mark(conn, EXPIRY, instrument_id, "PREMIUM")
    assert last == pytest.approx((11.25 - STRIKE) / 11.25)
    assert len(_pricer_marks(conn, instrument_id)) == 7  # still one row per type: rewritten, not added

    before = _pricer_marks(conn, instrument_id)
    _mark_spot(conn, day_after, EURUSD, 11.40)  # the next day's own spot is not the option's business
    single = price_and_store(conn, day_after, "T1")
    assert not single.priced and "is not after as_of" in single.reason
    book = price_all_and_store(conn, day_after)[0]
    # The mark on file agrees with the SPOT on file for the expiry date: plain skip, nothing rewritten.
    assert not book.priced and book.reason == single.reason
    assert _pricer_marks(conn, instrument_id) == before


@needs_quantlib
def test_expiry_day_path_dependent_payoff_is_skipped_with_a_reason():
    from engine.options.store import price_all_and_store, price_and_store

    conn = _new_db()
    instrument_id = _seed_option_trade(conn, payoff="BARRIER_KO", barrier_level=12.0)
    _mark_spot(conn, EXPIRY, EURUSD, 11.30)

    outcome = price_and_store(conn, EXPIRY, "T1")
    assert not outcome.priced
    assert "path" in outcome.reason and "BARRIER_KO" in outcome.reason
    assert _pricer_marks(conn, instrument_id) == []
    # ...and the catch-up leaves it alone as well.
    late = price_all_and_store(conn, "2026-09-25")[0]
    assert not late.priced and "is not after as_of" in late.reason
    assert _pricer_marks(conn, instrument_id) == []


@needs_quantlib
def test_catch_up_writes_the_expiry_mark_once_only_with_a_spot_and_clears_a_stale_frozen_row():
    """The app did not run on expiry day. On file: a model PREMIUM from the day before and
    a realised row frozen from it -- the error this mark removes."""
    from engine.options.store import price_all_and_store, price_and_store

    conn = _new_db()
    eve, later = "2026-09-22", "2026-09-25"
    instrument_id = _seed_option_trade(conn)  # EURUSD fixture, expiry 2026-09-23
    other = _seed_option_trade(conn, trade_id="T2", instrument_id="EURUSD092326P-OTHER", option_type="PUT")
    _seed_pair_spot(conn, as_of=eve)
    _seed_vol(conn, as_of=eve)
    assert price_and_store(conn, eve, "T1").priced  # the T-1 model premium
    model_premium = _official_mark(conn, eve, instrument_id, "PREMIUM")

    def freeze(trade_id, inst, dated):
        conn.execute(
            "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
            "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, inst, "FX_OPTION", "EUR", EXPIRY, NOTIONAL, 1.0, "PREMIUM", model_premium, dated,
             "QL_OPTIONS_PRICER", -1.0, "2026-09-24T09:00:00+00:00", f"premium dated {dated} (last before expiry)"))
        conn.commit()

    freeze("T1", instrument_id, eve)  # frozen from the older premium
    freeze("T2", other, eve)          # another instrument's row: its own catch-up, not this one's

    # (a) No SPOT for the expiry date: nothing written, nothing unfrozen, and the reason says why.
    outcomes = {o.trade_id: o for o in price_all_and_store(conn, later)}
    assert not outcomes["T1"].priced
    assert "no official SPOT for EURUSD on 2026-09-23" in outcomes["T1"].reason
    assert list(_option_marks(conn, instrument_id)) == [eve]
    assert _realised_trade_ids(conn) == ["T1", "T2"]

    # (b) The backfill lands the expiry date's closing SPOT: written once, DATED the expiry date.
    _mark_spot(conn, EXPIRY, EURUSD, 11.20)
    conn.execute("DELETE FROM instrument_options WHERE instrument_id = ?", (other,))  # T2 cannot be caught up: no terms
    conn.commit()
    outcomes = {o.trade_id: o for o in price_all_and_store(conn, later)}
    caught = outcomes["T1"]
    assert caught.priced and (caught.mark_basis, caught.mark_date) == ("INTRINSIC", EXPIRY)
    marks = _option_marks(conn, instrument_id)
    assert sorted(marks) == [eve, EXPIRY]  # nothing is dated the run's as_of
    assert marks[EXPIRY]["PREMIUM"] == pytest.approx((11.20 - STRIKE) / 11.20)
    assert marks[EXPIRY]["DELTA"] == 1.0 and all(marks[EXPIRY][g] == 0.0 for g in ZERO_GREEKS)
    assert marks[eve]["PREMIUM"] == model_premium  # the older model mark is history, left as it was
    assert _realised_trade_ids(conn) == ["T2"]      # T1's stale frozen row is gone; T2's is not this instrument's
    assert not outcomes["T2"].priced and outcomes["T2"].reason.startswith("no strike")

    # (c) Once a row is frozen FROM the expiry-dated mark, mark and row are both left alone for good.
    freeze("T1", instrument_id, EXPIRY)
    _mark_spot(conn, EXPIRY, EURUSD, 10.00)  # even if that date's spot is revised afterwards
    again = {o.trade_id: o for o in price_all_and_store(conn, later)}["T1"]
    assert not again.priced and again.reason == f"expiry {EXPIRY} is not after as_of {later}"
    assert _option_marks(conn, instrument_id)[EXPIRY]["PREMIUM"] == pytest.approx((11.20 - STRIKE) / 11.20)
    assert _realised_trade_ids(conn) == ["T1", "T2"]

    # price_and_store (one trade, the UI's path) never catches up: as_of after expiry is a plain skip.
    conn.execute("DELETE FROM marks WHERE instrument_id = ? AND as_of_date = ?", (instrument_id, EXPIRY))
    conn.commit()
    assert not price_and_store(conn, later, "T1").priced
    assert list(_option_marks(conn, instrument_id)) == [eve]


@needs_quantlib
def test_expiry_week_end_to_end_the_sample_tickets_freeze_at_their_intrinsic_in_dollars(tmp_path):
    """The EURUSD call's expiry week on the sample book, engine/pnl/ledger.py READ-ONLY.
    Wed 18 Nov: the call is marked at its payoff; the ledger does NOT freeze it that day
    (spot is still moving). The two EURUSD 1.15 puts (bought, then sold back in full on
    2026-09-08) are a closed-out group: the bulk pass does not price them (CLAUDE.md "A
    closed-out option is not live"), and having expired on 21 Oct they are already frozen
    at their closing fill, in dollars at the close-out date's EURUSD. Thu 19 Nov: the call
    is frozen at Wednesday's intrinsic, EUR P&L brought back to dollars at Wednesday's
    EURUSD."""
    from engine.options.store import price_all_and_store
    from engine.pnl.ledger import realise_settled

    conn = _sample_book(tmp_path)
    ids = {r[1]: r[0] for r in _sample_option_trades(conn)}
    call = "EURUSD111826C-500041"
    wed, thu = "2026-11-18", "2026-11-19"

    def frozen(instrument_id):
        return conn.execute("SELECT pnl_usd, spot_as_of_date, mark_type, spot_source, note FROM realised_pnl "
                            "WHERE trade_id = ?", (ids[instrument_id],)).fetchone()

    eurusd_wed, eurusd_closed = 1.1900, 1.1600   # expiry day's EURUSD and the sell-back day's
    _mark_spot(conn, SAMPLE_CLOSED_ON, "EURUSD", eurusd_closed)
    _mark_spot(conn, wed, "EURUSD", eurusd_wed)
    wed_outcomes = {o.instrument_id: o for o in price_all_and_store(conn, wed)}
    assert {i for i, o in wed_outcomes.items() if o.priced} == {call}   # no USDJPY spot, the digital no strike
    assert (wed_outcomes[call].mark_basis, wed_outcomes[call].mark_date) == ("INTRINSIC", wed)
    for instrument_id in SAMPLE_CLOSED_OUT:   # never priced, whatever the date
        assert instrument_id not in wed_outcomes or not wed_outcomes[instrument_id].priced
        assert _pricer_marks(conn, instrument_id) == []
    realise_settled(conn, wed)
    assert frozen(call) is None  # expiry day is not over: not frozen
    for instrument_id in SAMPLE_CLOSED_OUT:   # not live: the closing fill and the close-out date's spot
        assert frozen(instrument_id)[1:3] == (SAMPLE_CLOSED_ON, "CLOSE_OUT")

    realise_settled(conn, thu)
    _, _, strike, option_type, quantity, fill = STRUCK_TICKETS[call]
    intrinsic = _payoff_fraction(option_type, eurusd_wed, strike)
    row = frozen(call)
    assert row[0] == pytest.approx(quantity * (intrinsic - fill) * eurusd_wed)  # EUR -> USD at that day's EURUSD
    assert row[1:4] == (wed, "PREMIUM", "QL_OPTIONS_PRICER") and row[4] == f"premium dated {wed}"
    # The bought and the sold put are the same option: what is left is the premium difference, in
    # dollars at the EURUSD of the day it was sold back (user, 2026-09-21: "of course you freeze the usd converstion").
    net = sum(frozen(i)[0] for i in SAMPLE_CLOSED_OUT)
    assert net == pytest.approx(5_000_000 * (0.0031 - 0.0042) * eurusd_closed)


# --------------------------------------------------------------------------- reviewer W-2: expiry mark vs the official SPOT on file
#
# The approved rule is "the payoff at the pair's official SPOT of that date". The last
# live pull of expiry day and the close that later lands for the same date can differ, so
# until the option is frozen the catch-up recomputes the expiry-dated intrinsic from the
# SPOT on file NOW and rewrites the marks when the payoff differs.

def _seed_usd_jpy_expiring(conn, payoff, fill, trade_id="T1"):
    """USDJPY 152 put, USD 2,000,000, expiring 2026-11-19, with the NOTIONAL leg the ledger reads."""
    instrument_id = _seed_option_trade(conn, trade_id=trade_id, pair="USDJPY", instrument_id=f"USDJPY111926P-{trade_id}",
                                       strike=152.0, option_type="PUT", payoff=payoff, expiry="2026-11-19",
                                       quantity=2_000_000.0, price=fill)
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, settles_cash) "
        "VALUES (?,?,?,?,?,?,?,?,?)", (trade_id, 1, "NOTIONAL", "USD", 2_000_000.0, "2026-08-27", "2026-11-19", 0.0, 0))
    conn.commit()
    return instrument_id


@needs_quantlib
def test_digital_expiry_mark_is_recomputed_from_the_close_on_file_before_the_first_freeze():
    """The reviewer's scenario. USDJPY 152 put digital: the last pull of expiry day sees
    151.90 -> PREMIUM 1.0 (pays). Before anything is frozen the close on file for that
    date becomes 152.30. The next pull rewrites PREMIUM 0.0 and the ledger, run right
    after the options step as in live.pull_once, freezes at 0.0 - fill."""
    from engine.options.store import price_all_and_store, purge_old_unit_cash_payoff_marks
    from engine.pnl.ledger import realise_settled

    conn = _new_db()
    purge_old_unit_cash_payoff_marks(conn)  # the one-time purge has long since run on this database
    expiry, next_day, fill, quantity = "2026-11-19", "2026-11-20", 0.124, 2_000_000.0
    instrument_id = _seed_usd_jpy_expiring(conn, "DIGITAL", fill)

    _mark_spot(conn, expiry, "USDJPY", 151.90)
    assert price_all_and_store(conn, expiry)[0].priced
    assert _official_mark(conn, expiry, instrument_id, "PREMIUM") == 1.0
    assert realise_settled(conn, expiry)["realised"] == 0  # expiry day is not over

    _mark_spot(conn, expiry, "USDJPY", 152.30)  # the official close replaces the last live spot of that date
    _mark_spot(conn, next_day, "USDJPY", 152.10)
    outcome = price_all_and_store(conn, next_day)[0]  # options step ...
    assert outcome.priced and (outcome.mark_basis, outcome.mark_date) == ("INTRINSIC", expiry)
    marks = _option_marks(conn, instrument_id)
    assert list(marks) == [expiry]
    assert marks[expiry]["PREMIUM"] == 0.0 and marks[expiry]["DELTA"] == 0.0

    assert realise_settled(conn, next_day)["realised"] == 1  # ... then the ledger
    pnl_usd, dated = conn.execute("SELECT pnl_usd, spot_as_of_date FROM realised_pnl WHERE trade_id = 'T1'").fetchone()
    assert pnl_usd == pytest.approx(quantity * (0.0 - fill))  # USD is the base ccy: -248,000
    assert dated == expiry

    # Frozen from the expiry-dated mark: a later revision of that date's spot changes neither.
    _mark_spot(conn, expiry, "USDJPY", 151.50)
    again = price_all_and_store(conn, next_day)[0]
    assert not again.priced and "is not after as_of" in again.reason
    assert _official_mark(conn, expiry, instrument_id, "PREMIUM") == 0.0
    assert conn.execute("SELECT pnl_usd FROM realised_pnl WHERE trade_id = 'T1'").fetchone()[0] == pytest.approx(pnl_usd)


@needs_quantlib
def test_vanilla_expiry_mark_is_recomputed_from_the_close_on_file_and_only_on_a_difference():
    from engine.options.store import price_all_and_store
    from engine.pnl.ledger import realise_settled

    conn = _new_db()
    expiry, next_day, fill, quantity, K = "2026-11-19", "2026-11-20", 0.02, 2_000_000.0, 152.0
    instrument_id = _seed_usd_jpy_expiring(conn, "VANILLA", fill)

    _mark_spot(conn, expiry, "USDJPY", 151.90)
    assert price_all_and_store(conn, expiry)[0].priced
    live_rows = _pricer_marks(conn, instrument_id)
    assert _official_mark(conn, expiry, instrument_id, "PREMIUM") == pytest.approx((K - 151.90) / 151.90)

    # Same SPOT on file the next day: mark and SPOT agree, so NOTHING is rewritten.
    same = price_all_and_store(conn, next_day)[0]
    assert not same.priced and "is not after as_of" in same.reason
    assert _pricer_marks(conn, instrument_id) == live_rows

    # The close lands at 151.20: the put is worth more, and every expiry-dated row follows.
    _mark_spot(conn, expiry, "USDJPY", 151.20)
    outcome = price_all_and_store(conn, next_day)[0]
    assert outcome.priced and outcome.mark_date == expiry
    marks = _option_marks(conn, instrument_id)[expiry]
    assert marks["PREMIUM"] == pytest.approx((K - 151.20) / 151.20)
    assert marks["DELTA"] == -1.0 and all(marks[g] == 0.0 for g in ZERO_GREEKS)
    assert len(_pricer_marks(conn, instrument_id)) == len(live_rows)  # rewritten in place, nothing added

    assert realise_settled(conn, next_day)["realised"] == 1
    pnl_usd = conn.execute("SELECT pnl_usd FROM realised_pnl WHERE trade_id = 'T1'").fetchone()[0]
    assert pnl_usd == pytest.approx(quantity * ((K - 151.20) / 151.20 - fill))

    # No SPOT left on file for the expiry date and not frozen: nothing to recompute from, the mark stands.
    conn2 = _new_db()
    inst2 = _seed_usd_jpy_expiring(conn2, "VANILLA", fill)
    _mark_spot(conn2, expiry, "USDJPY", 151.90)
    assert price_all_and_store(conn2, expiry)[0].priced
    conn2.execute("DELETE FROM marks WHERE instrument_id = 'USDJPY' AND as_of_date = ?", (expiry,))
    conn2.commit()
    assert not price_all_and_store(conn2, next_day)[0].priced
    assert _official_mark(conn2, expiry, inst2, "PREMIUM") == pytest.approx((K - 151.90) / 151.90)


# --------------------------------------------------------------------------- reviewer W-1: one-time purge of old-unit digital / touch marks
#
# Every DIGITAL / ONE_TOUCH / NO_TOUCH mark written before the units audit is 1/S of the
# truth and stays official for its own date; a re-pull only overwrites today's and an
# identical re-save of terms deletes nothing. price_all_and_store starts with a purge
# that runs ONCE per database (marker row in options_migrations).

def _old_unit_rows(instrument_id, as_of, expiry, premium):
    stamp = f"{as_of}T17:00:00-04:00"
    return [(as_of, instrument_id, expiry, mark_type, value, "QL_OPTIONS_PRICER", stamp)
            for mark_type, value in (("PREMIUM", premium), ("DELTA", -0.05), ("DELTA_PA", -0.06), ("GAMMA", 0.004),
                                     ("THETA", 0.0005), ("VEGA", -0.018), ("RHO", -0.015))]


def _insert_mark_rows(conn, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()


@needs_quantlib
def test_one_time_purge_clears_old_unit_cash_payoff_marks_once_and_only_those():
    from engine.options.store import (CASH_PAYOFF_UNIT_PURGE, price_all_and_store, price_and_store,
                                      purge_old_unit_cash_payoff_marks)

    conn = _new_db()
    expiry, d1, d2, today = "2026-11-19", "2026-09-16", "2026-09-17", "2026-09-18"
    digital = _seed_option_trade(conn, trade_id="D", pair="USDJPY", instrument_id="USDJPY111926P-D", strike=152.0,
                                 option_type="PUT", payoff="DIGITAL", expiry=expiry, quantity=2_000_000.0, price=0.124)
    touch = _seed_option_trade(conn, trade_id="OT", pair="USDJPY", instrument_id="USDJPY111926-OT", strike=0.0,
                               option_type="CALL", payoff="ONE_TOUCH", barrier_level=160.0, expiry=expiry,
                               quantity=1_000_000.0, price=0.30)
    vanilla = _seed_option_trade(conn, trade_id="V", pair="USDJPY", instrument_id="USDJPY111926P-V", strike=152.0,
                                 option_type="PUT", payoff="VANILLA", expiry=expiry, quantity=2_000_000.0, price=0.02)
    american = _seed_option_trade(conn, trade_id="A", pair="USDJPY", instrument_id="USDJPY111926P-A", strike=152.0,
                                  option_type="PUT", payoff="AMERICAN", expiry=expiry, quantity=2_000_000.0, price=0.02)
    # Marks of the old code on two dates: the digital's and the touch's are 1/S of the truth.
    for as_of in (d1, d2):
        _insert_mark_rows(conn, _old_unit_rows(digital, as_of, expiry, 0.0057) + _old_unit_rows(touch, as_of, expiry, 0.0021)
                          + _old_unit_rows(vanilla, as_of, expiry, 0.021) + _old_unit_rows(american, as_of, expiry, 0.022))
    manual = (d1, digital, expiry, "PREMIUM", 0.13, "MANUAL", f"{d1}T17:00:00-04:00")
    _insert_mark_rows(conn, [manual])
    _insert_realised_row(conn, "D", digital)   # frozen from an old-unit mark
    _insert_realised_row(conn, "V", vanilla)

    # Today's market, so the same run that purges also rewrites today's marks in the right unit.
    _seed_pair_spot(conn, as_of=today, pair="USDJPY", spot=150.0)
    _seed_vol(conn, pair="USDJPY", expiry=expiry, vol=0.10, as_of=today)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'options_migrations'").fetchone()[0] == 0

    outcomes = {o.trade_id: o for o in price_all_and_store(conn, today)}
    assert all(o.priced for o in outcomes.values()), {k: o.reason for k, o in outcomes.items()}

    assert conn.execute("SELECT name FROM options_migrations").fetchall() == [(CASH_PAYOFF_UNIT_PURGE,)]
    for instrument_id in (digital, touch):
        assert list(_option_marks(conn, instrument_id)) == [today]          # both old dates gone, all seven types
    assert _option_marks(conn, digital)[today]["PREMIUM"] > 100 * 0.0057    # and today's is in the right unit
    for instrument_id in (vanilla, american):
        marks = _option_marks(conn, instrument_id)
        assert sorted(marks) == [d1, d2, today]                             # their unit was always right: untouched
        assert marks[d1]["PREMIUM"] in (0.021, 0.022) and len(marks[d1]) == 7
    assert conn.execute("SELECT value FROM marks WHERE source = 'MANUAL' AND instrument_id = ?",
                        (digital,)).fetchall() == [(0.13,)]                 # the user's own row
    assert _realised_trade_ids(conn) == ["V"]                               # the digital's frozen row went, the vanilla's stayed
    assert conn.execute("SELECT COUNT(*) FROM marks WHERE instrument_id = 'USDJPY' AND mark_type = 'SPOT'").fetchone()[0] == 1

    # It never runs twice: marks written after the marker survive every later call.
    correct = _pricer_marks(conn, digital)
    tomorrow = "2026-09-21"
    _seed_pair_spot(conn, as_of=tomorrow, pair="USDJPY", spot=150.5)
    _seed_vol(conn, pair="USDJPY", expiry=expiry, vol=0.10, as_of=tomorrow)
    assert purge_old_unit_cash_payoff_marks(conn) == {"ran": False, "instruments": [], "marks_deleted": 0,
                                                      "realised_deleted": 0}
    price_all_and_store(conn, tomorrow)
    price_all_and_store(conn, tomorrow)
    assert sorted(_option_marks(conn, digital)) == [today, tomorrow]
    assert [r for r in _pricer_marks(conn, digital) if r[0] == today] == correct
    # A digital's own marks written through the UI path the day after are just as safe.
    assert price_and_store(conn, tomorrow, "D").priced
    price_all_and_store(conn, "2026-09-22")
    assert today in _option_marks(conn, digital) and tomorrow in _option_marks(conn, digital)


def test_one_time_purge_reports_what_it_did_and_tolerates_a_missing_realised_pnl_table():
    from engine.options.store import purge_old_unit_cash_payoff_marks

    conn = _new_db()
    digital = _seed_option_trade(conn, trade_id="D", pair="USDJPY", instrument_id="USDJPY111926P-D", strike=152.0,
                                 option_type="PUT", payoff="DIGITAL", expiry="2026-11-19")
    lower = _seed_option_trade(conn, trade_id="NT", pair="USDJPY", instrument_id="USDJPY111926-NT", strike=0.0,
                               option_type="CALL", payoff="no_touch", barrier_level=140.0, expiry="2026-11-19")
    # A listed option's digital-payoff row (not FX_OPTION) never went through the FX spot
    # division: not the purge's business.
    listed = "GCZ6C 2600 Digital 2026-12-18"
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                 (listed, "CMDTY_OPTION", "USD", "USD", 100.0, 0, "GCZ6 Comdty", "2026-12-18"))
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, barrier_level, avg_start_date, "
                 "payoff) VALUES (?,?,?,?,?,?)", (listed, 2600.0, "CALL", 0.0, "9999-12-31", "DIGITAL"))
    for as_of in ("2026-09-16", "2026-09-17"):
        _insert_mark_rows(conn, _old_unit_rows(digital, as_of, "2026-11-19", 0.0057)
                          + _old_unit_rows(lower, as_of, "2026-11-19", 0.004)
                          + _old_unit_rows(listed, as_of, "2026-12-18", 41.0))
    conn.execute("DROP TABLE realised_pnl")
    conn.commit()

    first = purge_old_unit_cash_payoff_marks(conn)
    assert first == {"ran": True, "instruments": sorted([digital, lower]), "marks_deleted": 28, "realised_deleted": 0}
    assert _pricer_marks(conn, digital) == [] and _pricer_marks(conn, lower) == []
    assert len(_pricer_marks(conn, listed)) == 14
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'realised_pnl'").fetchone()[0] == 0

    # Idempotent: rows that appear later are not the purge's business.
    _insert_mark_rows(conn, _old_unit_rows(digital, "2026-09-18", "2026-11-19", 0.68))
    assert purge_old_unit_cash_payoff_marks(conn)["ran"] is False
    assert len(_pricer_marks(conn, digital)) == 7


def test_time_to_expiry_runs_to_the_1000_new_york_cut_in_hours_not_whole_days():
    """User decision 2026-09-21. A pull at 15:00 New York for an option cut at 10:00 tomorrow has
    19 hours to go, not a day: the engine's whole-day count is corrected through the vol."""
    import math
    from data.ingest import schema
    from engine.options.store import cut_time_factor
    conn = schema.connect()
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, "
                 "expiry_date) VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO marks VALUES ('2026-09-21','EURUSD','2026-09-21','SPOT',1.17,'BBG_BFXFORWARD',"
                 "'2026-09-21T15:00:00-04:00')")
    conn.commit()
    one_day = cut_time_factor(conn, "2026-09-21", "EURUSD", datetime.date(2026, 9, 22))
    assert one_day == pytest.approx(math.sqrt((19 / 24) / 1))                 # 15:00 -> 10:00 next day
    two_months = cut_time_factor(conn, "2026-09-21", "EURUSD", datetime.date(2026, 11, 20))
    assert 0.998 < two_months < 1.0                                           # nothing on a long option
    assert cut_time_factor(conn, "2026-09-18", "EURUSD", datetime.date(2026, 9, 22)) == pytest.approx(
        math.sqrt((4 - 5 / 24) / 4))                                          # no SPOT time on file: 15:00 New York
