"""engine/options/ tests, owned by options-pricer.

Phase 0: the vendored library is in place but nothing is wired to the schema
yet, so those tests only check the vendor copy imports cleanly. Phase 2
lands FX vanilla/digital pricing + PREMIUM/DELTA marks; Phase 4 lands
American/Asian/barrier/one-touch/no-touch payoffs plus multi-leg structure
combination. Follows test_rates_pricing.py's skip-if-QuantLib-absent
convention: every test that needs QuantLib is marked @needs_quantlib rather
than failing outright when it's not installed.
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

EURSEK = "EURSEK"
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


def _seed_pair_spot(conn, as_of=AS_OF, pair=EURSEK, spot=SPOT):
    """The plain FX pair instrument + its official SPOT mark."""
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (pair, "FX", pair[:3], pair[3:], 1.0, 0, f"{pair} Curncy", "9999-12-31"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, snapped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (as_of, pair, as_of, "SPOT", spot, "BBG_BFXFORWARD", f"{as_of}T17:00:00-04:00"),
    )
    conn.commit()


def _seed_option_trade(
    conn,
    trade_id="T1",
    instrument_id="EURSEK092326C-197727826",
    pair=EURSEK,
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


def _seed_vol(conn, pair=EURSEK, expiry=EXPIRY, vol=VOL, as_of=AS_OF):
    from engine.options.inputs import set_manual_vol

    set_manual_vol(conn, as_of, pair, expiry, vol)


# --------------------------------------------------------------------------- Phase 5b: vol_quotes / smile fixtures

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "bloomberg" / "fixtures" / "fx_vol_snapshot_v1.json"
SMILE_AS_OF = "2026-09-17"  # matches the fixture's own as_of


def _seed_vol_quotes(conn, pairs=("EURSEK", "EURUSD", "USDJPY"), as_of=SMILE_AS_OF):
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
    assert set(official_rows) == {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"}

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
    _full_setup(conn, trade_id="T1", instrument_id="EURSEK092326C-1")
    _seed_option_trade(
        conn, trade_id="T2", instrument_id="EURSEK092326P-2", option_type="PUT",
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
    set_manual_vol(conn, AS_OF, EURSEK, FLAT_TENOR, 0.07)
    assert get_manual_vol(conn, AS_OF, EURSEK, "2027-01-01") == pytest.approx(0.07)

    set_manual_vol(conn, AS_OF, EURSEK, "2027-01-01", 0.09)
    assert get_manual_vol(conn, AS_OF, EURSEK, "2027-01-01") == pytest.approx(0.09)
    assert get_manual_vol(conn, AS_OF, EURSEK, "2027-06-01") == pytest.approx(0.07)


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
    from engine.options.pricer import price_fx_vanilla
    import datetime as dt
    vanilla = price_fx_vanilla(SPOT, STRIKE, dt.date(2026, 9, 23), dt.date(2026, 8, 21), *_dom_for_rates(), VOL, "call")
    assert outcome.result.quote_price <= vanilla.quote_price + 1e-9


def _dom_for_rates():
    from engine.options.vendor.options_calc.fx.rate_curves import get_domestic_and_foreign_rates
    return get_domestic_and_foreign_rates(EURSEK)


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
        conn, trade_id="LEG1", instrument_id="EURSEK092326C-LEG1", option_type="CALL",
        package_id="PKG-1",
    )
    _seed_option_trade(
        conn, trade_id="LEG2", instrument_id="EURSEK092326P-LEG2", option_type="PUT",
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
    """(i) EURSEK vanilla priced with the smile reports SMILE and the vol
    used lies within the fixture's 1M tenor's ATM +/- (BF + |RR|/2) band."""
    from engine.options.inputs import resolve_market_inputs, SMILE
    from engine.options.store import price_and_store

    conn = _new_db()
    smile_spot = 11.20
    expiry = "2026-10-17"  # exactly 30 days after SMILE_AS_OF -> the fixture's own '1M' node, no cross-tenor blend
    _seed_pair_spot(conn, as_of=SMILE_AS_OF, spot=smile_spot)
    _seed_vol_quotes(conn)
    _seed_option_trade(conn, expiry=expiry)

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURSEK, expiry, strike=STRIKE)
    assert result.inputs is not None, result.reason
    assert result.inputs.vol_source.source_kind == SMILE

    # Fixture EURSEK 1M: ATM 8.00, RR25 0.25, BF25 0.28 (vol points).
    atm, rr, bf = 8.00 / 100.0, 0.25 / 100.0, 0.28 / 100.0
    band = bf + abs(rr) / 2
    assert atm - band <= result.inputs.vol <= atm + band

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

    call_result = resolve_market_inputs(conn, SMILE_AS_OF, EURSEK, expiry, strike=call_strike)
    put_result = resolve_market_inputs(conn, SMILE_AS_OF, EURSEK, expiry, strike=put_strike)

    assert call_result.inputs is not None, call_result.reason
    assert put_result.inputs is not None, put_result.reason
    assert call_result.inputs.vol != pytest.approx(put_result.inputs.vol)
    # Fixture EURSEK RR25 is positive at every tenor (calls richer than
    # puts) -> the higher-strike (call-side) vol should be the larger one.
    assert call_result.inputs.vol > put_result.inputs.vol


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

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURSEK, expiry, strike=0.0)
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
    set_manual_vol(conn, SMILE_AS_OF, EURSEK, far_expiry, 0.09)

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURSEK, far_expiry, strike=STRIKE)
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

    result = resolve_market_inputs(conn, SMILE_AS_OF, EURSEK, "2026-10-17", strike=STRIKE)
    assert result.inputs is None
    assert result.reason == "no vol"
