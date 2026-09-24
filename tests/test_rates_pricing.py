"""Tests for engine/rates/: the OIS curve bootstrap (curves.py) and its SQLite glue
(store.bootstrap_and_store, store.snapped_at).

Swap valuation, the swap store entry points and the direction flip's mark reversal left
the app on 2026-09-24 (commodity conversion Phase 2); their tests went with them. What
is tested here is the discount curve the option pricers read and the ``curves`` rows the
live pull's curves step writes.

QuantLib-dependent tests are skipped (not errored) when QuantLib is not installed in
this environment, mirroring how blpapi-dependent tests are skipped elsewhere in this
repo (see tests/test_bloomberg.py). Non-QuantLib glue (reading curve_quotes rows,
picking a source, the close stamp) is tested directly without any QuantLib import so
that coverage does not silently vanish just because QuantLib is absent.
"""
from __future__ import annotations

import datetime
import sqlite3

import pytest

try:
    import QuantLib as ql  # noqa: F401
    HAVE_QUANTLIB = True
except ImportError:
    HAVE_QUANTLIB = False

needs_quantlib = pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")

from data.ingest.schema import create_schema

# A small, self-consistent mock USD SOFR OIS snapshot (decimal par rates), loosely
# shaped like a real curve (short end high, belly lower, long end re-steepening) so the
# bootstrap exercises more than one segment.
MOCK_USD_SOFR = [
    ("1W", 0.0530), ("1M", 0.0528), ("3M", 0.0520), ("6M", 0.0505),
    ("1Y", 0.0480), ("2Y", 0.0440), ("5Y", 0.0410), ("10Y", 0.0415), ("30Y", 0.0400),
]


def _seed_curve_quotes(conn: sqlite3.Connection, as_of: str, ccy: str, index: str, quotes, source="BBG_BDP"):
    from data.bloomberg.rates_marketdata import ensure_curve_quotes_table

    ensure_curve_quotes_table(conn)
    rows = [
        (as_of, ccy, index, tenor, f"{ccy}{tenor}", value, "OIS", "PX_LAST", source)
        for tenor, value in quotes
    ]
    with conn:
        conn.executemany(
            'INSERT OR REPLACE INTO curve_quotes (as_of_date, ccy, "index", tenor, ticker, value, '
            "quote_type, field, source) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )


# --------------------------------------------------------------------------- non-QuantLib glue

def test_read_curve_quotes_prefers_bbg_bdp_source():
    from engine.rates.store import _read_curve_quotes

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", MOCK_USD_SOFR, source="MANUAL")
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", MOCK_USD_SOFR, source="BBG_BDP")

    picked = _read_curve_quotes(conn, "2026-08-17", "USD", "SOFR")
    assert len(picked) == len(MOCK_USD_SOFR)
    assert set(picked) == set(MOCK_USD_SOFR)


def test_read_curve_quotes_falls_back_alphabetically_when_no_bbg_bdp():
    from engine.rates.store import _read_curve_quotes

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", MOCK_USD_SOFR, source="MANUAL")
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", [("1Y", 0.9)], source="ZZZ_SOURCE")

    picked = _read_curve_quotes(conn, "2026-08-17", "USD", "SOFR")
    assert set(picked) == set(MOCK_USD_SOFR)  # 'MANUAL' < 'ZZZ_SOURCE'


def test_read_curve_quotes_empty_when_missing():
    from engine.rates.store import _read_curve_quotes

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    assert _read_curve_quotes(conn, "2026-08-17", "USD", "SOFR") == []


def test_snapped_at_is_the_15_00_new_york_close_with_that_days_offset():
    from engine.rates.store import snapped_at

    assert snapped_at(datetime.date(2026, 9, 22)) == "2026-09-22T15:00:00-04:00"   # EDT
    assert snapped_at(datetime.date(2026, 12, 15)) == "2026-12-15T15:00:00-05:00"  # EST


def test_package_exposes_only_the_curve_path():
    """The swap pricer left on 2026-09-24: nothing of it is importable any more, and the
    names other lanes import are still there."""
    import importlib

    import engine.rates as rates
    import engine.rates.store as store

    assert set(rates.__all__) == {"CurveSet", "build_curve_set", "bootstrap_and_store", "snapped_at"}
    for gone in ("price_and_store", "price_all_and_store", "recalc_on_file", "reverse_direction_marks",
                 "load_fixings", "DIRECTIONAL_MARK_TYPES", "PRICER_SOURCE"):
        assert not hasattr(store, gone), gone
    for module in ("engine.rates.valuation", "engine.rates.instruments"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)
    from engine.rates.conventions import CCY_RFR

    assert CCY_RFR == {"USD": "SOFR", "EUR": "ESTR", "GBP": "SONIA", "JPY": "TONA", "CHF": "SARON",
                       "CAD": "CORRA", "AUD": "AONIA"}


# --------------------------------------------------------------------------- curve bootstrap

@needs_quantlib
def test_build_curve_set_bootstraps_usd_sofr():
    from engine.rates.curves import build_curve_set

    as_of = datetime.date(2026, 8, 17)
    cs = build_curve_set(MOCK_USD_SOFR, as_of, "USD")
    assert cs.ccy == "USD"
    assert cs.index == "SOFR"
    assert len(cs.quotes) == len(MOCK_USD_SOFR)
    # A 1Y discount factor should be a sensible discount (< 1, > 0.9 given ~5% rates).
    one_year = cs.discount.referenceDate() + 365
    df = cs.discount.discount(one_year)
    assert 0.90 < df < 1.0


@needs_quantlib
def test_build_curve_set_rejects_unsupported_currency():
    from engine.rates.curves import build_curve_set
    from engine.rates.errors import PricingConfigError

    as_of = datetime.date(2026, 8, 17)
    with pytest.raises(PricingConfigError):
        build_curve_set(MOCK_USD_SOFR, as_of, "XXX")


# --------------------------------------------------------------------------- store.bootstrap_and_store

@needs_quantlib
def test_bootstrap_and_store_writes_curves_rows():
    from engine.rates.store import bootstrap_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", MOCK_USD_SOFR)

    curve_set = bootstrap_and_store(conn, "2026-08-17", "USD")
    assert curve_set.ccy == "USD"

    rows = conn.execute(
        "SELECT node_date, discount_factor, par_rate, source FROM curves "
        "WHERE curve_id = ? AND as_of_date = ?",
        ("USD-SOFR-OIS", "2026-08-17"),
    ).fetchall()
    assert len(rows) == len(MOCK_USD_SOFR)
    for node_date, df, par_rate, source in rows:
        assert source == "QL_PRICER"
        assert 0.0 < df <= 1.0
        assert 0.0 < par_rate < 1.0
        datetime.date.fromisoformat(node_date)  # does not raise
    # It writes curves only: no mark of any kind.
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0


@needs_quantlib
def test_bootstrap_and_store_raises_when_no_quotes():
    from engine.rates.store import bootstrap_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    with pytest.raises(ValueError):
        bootstrap_and_store(conn, "2026-08-17", "USD")


def test_bootstrap_and_store_refuses_a_currency_with_no_ois_index():
    """Glue only: raised before any QuantLib call."""
    from engine.rates.store import bootstrap_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    with pytest.raises(ValueError, match="No canonical OIS index"):
        bootstrap_and_store(conn, "2026-08-17", "SEK")


# --------------------------------------------------------------------------- flat forwards by default (2026-09-22)
# The USD SOFR quotes of the Bloomberg PC's 2026-09-22 pull, transcribed here from that
# day's curve_quotes (the marks snapshot that carried them has since been emptied from
# the working tree; git history keeps it). At a 2026-09-22 evaluation date QuantLib
# 1.43's log-cubic bootstrap does not converge on them ("convergence not reached after
# 99 iterations; last improvement 0.0178479 ..."), which failed every curve-dependent
# price on that day's pull; at 2026-09-21 it converges. Flat forwards (log-linear
# discount) converge on every date and are the default since then (user, 2026-09-22:
# "yes switch to flat forwards and rerun the past days").
USD_SOFR_2026_09_22 = [
    ("1W", 0.03892), ("2W", 0.03895), ("3W", 0.038935), ("1M", 0.03899), ("2M", 0.03969), ("3M", 0.04036),
    ("6M", 0.042117), ("9M", 0.043745), ("1Y", 0.045035), ("2Y", 0.046271), ("3Y", 0.046244), ("5Y", 0.045755),
    ("7Y", 0.045485), ("10Y", 0.04571), ("15Y", 0.046584), ("20Y", 0.04695), ("30Y", 0.0461833),
]


def _discount_factors(cs, dates):
    return [cs.discount_curve.discount(ql.Date(d.day, d.month, d.year)) for d in dates]


@needs_quantlib
def test_default_interpolation_is_flat_forwards():
    from engine.rates.curves import DEFAULT_INTERPOLATION, INTERPOLATIONS, CurveSet, build_curve_set

    assert DEFAULT_INTERPOLATION == "LogLinear"
    assert set(INTERPOLATIONS) == {"LogLinear", "LogCubicDiscount"}
    cs = build_curve_set(MOCK_USD_SOFR, datetime.date(2026, 8, 17), "USD")
    assert type(cs.discount_curve).__name__ == "PiecewiseLogLinearDiscount"
    assert cs.interpolation == "LogLinear" and cs.bootstrap_note == ""
    bare = CurveSet(cs.valuation_date, cs.ccy, cs.index, cs.discount, cs.discount_curve, cs.ql_index)
    assert bare.interpolation == "LogLinear" and bare.bootstrap_note == ""
    # Flat forwards: the overnight forward is constant between two pillars. Between the 1Y
    # and 2Y nodes the one-day forward read at three dates is the same rate.
    one_year = cs.discount.referenceDate() + 400
    fwds = [cs.discount.forwardRate(one_year + n, one_year + n + 1, ql.Actual365Fixed(), ql.Simple).rate()
            for n in (0, 60, 120)]
    assert fwds[0] == pytest.approx(fwds[1], abs=1e-12) and fwds[1] == pytest.approx(fwds[2], abs=1e-12)


@needs_quantlib
def test_regression_2026_09_22_usd_sofr_bootstraps_under_flat_forwards():
    """The 17 quotes of the failed pull, at the failing evaluation date, with the default
    interpolation: the bootstrap is forced (every discount factor read) and the curve is
    sane: finite, strictly decreasing, below 1."""
    import math

    from engine.rates.curves import build_curve_set

    cs = build_curve_set(USD_SOFR_2026_09_22, datetime.date(2026, 9, 22), "USD", "SOFR")
    assert cs.interpolation == "LogLinear" and cs.bootstrap_note == ""
    dates = [datetime.date(2026 + n, 9, 24) for n in range(1, 31)]
    dfs = _discount_factors(cs, dates)
    assert all(math.isfinite(df) and 0.0 < df < 1.0 for df in dfs)
    assert all(a > b for a, b in zip(dfs, dfs[1:]))
    df_1y_from_as_of = _discount_factors(cs, [datetime.date(2027, 9, 22)])[0]
    assert df_1y_from_as_of == pytest.approx(0.956377, abs=2e-6)   # the log-linear 1Y df of the repro


@needs_quantlib
def test_bootstrap_and_store_pins_the_2026_09_22_usd_sofr_nodes_and_reruns_in_place():
    """The curves rows the live pull's curves step writes for the failing day's quotes:
    one per tenor, node dates from the SOFR calendar off the spot date, discount factors
    pinned; no interpolation column. A second run replaces the same keys (a stale node
    is overwritten, the row count does not grow)."""
    from engine.rates.store import bootstrap_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    as_of = "2026-09-22"
    _seed_curve_quotes(conn, as_of, "USD", "SOFR", USD_SOFR_2026_09_22)
    conn.execute("INSERT INTO curves VALUES ('USD-SOFR-OIS', ?, '2027-09-24', 0.5, 0.045035, 'QL_PRICER')", (as_of,))
    conn.commit()

    cs = bootstrap_and_store(conn, as_of, "USD")
    assert cs.interpolation == "LogLinear"
    cols = [r[1] for r in conn.execute("PRAGMA table_info(curves)").fetchall()]
    assert "interpolation" not in cols
    nodes = [r[0] for r in conn.execute(
        "SELECT node_date FROM curves WHERE curve_id = 'USD-SOFR-OIS' AND as_of_date = ? ORDER BY node_date", (as_of,))]
    assert nodes == ["2026-10-01", "2026-10-08", "2026-10-15", "2026-10-26", "2026-11-24", "2026-12-24",
                     "2027-03-24", "2027-06-24", "2027-09-24", "2028-09-25", "2029-09-24", "2031-09-24",
                     "2033-09-26", "2036-09-24", "2041-09-24", "2046-09-24", "2056-09-25"]
    pinned = dict((d, (df, par)) for d, df, par in conn.execute(
        "SELECT node_date, discount_factor, par_rate FROM curves WHERE as_of_date = ? AND node_date IN "
        "('2027-09-24', '2031-09-24', '2056-09-25')", (as_of,)))
    assert pinned["2027-09-24"][0] == pytest.approx(0.9561266741823093, abs=1e-12)   # stale 0.5 replaced
    assert pinned["2031-09-24"][0] == pytest.approx(0.7969601241236476, abs=1e-12)
    assert pinned["2056-09-25"][0] == pytest.approx(0.25523827201533517, abs=1e-12)
    assert [p for _df, p in (pinned[d] for d in ("2027-09-24", "2031-09-24", "2056-09-25"))] == \
        [0.045035, 0.045755, 0.0461833]                                              # the raw quote, as quoted

    bootstrap_and_store(conn, as_of, "USD")
    assert conn.execute("SELECT COUNT(*) FROM curves WHERE as_of_date = ?", (as_of,)).fetchone()[0] == len(nodes)


@needs_quantlib
def test_bootstrap_and_store_raises_on_a_curve_that_does_not_converge_and_writes_nothing():
    from engine.rates.errors import CurveBuildError
    from engine.rates.store import bootstrap_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-09-23", "USD", "SOFR", [("1M", 0.04), ("1Y", -5.0), ("5Y", 0.04)])
    with pytest.raises(CurveBuildError, match="did not converge"):
        bootstrap_and_store(conn, "2026-09-23", "USD")
    assert conn.execute("SELECT COUNT(*) FROM curves").fetchone()[0] == 0


@needs_quantlib
def test_regression_2026_09_22_explicit_log_cubic_still_fails_and_says_why():
    """Documents the reason for the switch: the same quotes on the same date under the old
    default do not converge in QuantLib 1.43, and build_curve_set raises (no silent curve,
    no fallback) naming the interpolation, the date and QuantLib's own message. If a later
    QuantLib build converges here, this test (not the switch) is what to revisit."""
    from engine.rates.curves import build_curve_set
    from engine.rates.errors import CurveBuildError

    with pytest.raises(CurveBuildError) as exc:
        build_curve_set(USD_SOFR_2026_09_22, datetime.date(2026, 9, 22), "USD", "SOFR",
                        interpolation="LogCubicDiscount")
    message = str(exc.value)
    assert "('USD', 'SOFR')" in message and "log-cubic" in message and "2026-09-22" in message
    assert "convergence not reached" in message      # QuantLib's own message is carried


@needs_quantlib
def test_explicit_log_cubic_on_a_converging_day_keeps_its_discount_factors():
    """The same quotes at 2026-09-21 converge under log-cubic, which stays available on
    request with the discount factors pinned before the switch; the default now gives the
    flat-forward curve, which differs from it by a few 1e-6 in DF."""
    from engine.rates.curves import build_curve_set

    cubic = build_curve_set(USD_SOFR_2026_09_22, datetime.date(2026, 9, 21), "USD", "SOFR",
                            interpolation="LogCubicDiscount")
    assert cubic.interpolation == "LogCubicDiscount" and cubic.bootstrap_note == ""
    dates = [datetime.date(2027, 9, 23), datetime.date(2031, 9, 23)]
    df_1y, df_5y = _discount_factors(cubic, dates)
    assert df_1y == pytest.approx(0.9561267457564275, abs=1e-12)
    assert df_5y == pytest.approx(0.796969896675867, abs=1e-12)
    flat = build_curve_set(USD_SOFR_2026_09_22, datetime.date(2026, 9, 21), "USD", "SOFR")
    assert flat.interpolation == "LogLinear"
    ll_1y, ll_5y = _discount_factors(flat, dates)
    assert ll_1y != df_1y and abs(ll_1y - df_1y) < 1e-4
    assert ll_5y != df_5y and abs(ll_5y - df_5y) < 1e-4


@needs_quantlib
def test_unknown_interpolation_is_a_config_error():
    from engine.rates.curves import build_curve_set
    from engine.rates.errors import PricingConfigError

    with pytest.raises(PricingConfigError):
        build_curve_set(MOCK_USD_SOFR, datetime.date(2026, 8, 17), "USD", interpolation="Cubic")


@needs_quantlib
def test_non_converging_curve_raises_curve_build_error_never_a_silent_curve(caplog):
    import logging

    from engine.rates.curves import build_curve_set
    from engine.rates.errors import CurveBuildError

    # An impossible curve (a −500% 1Y rate) fails under flat forwards too: it raises, with
    # a warning logged, and no curve object is returned.
    bad = [("1M", 0.04), ("1Y", -5.0), ("5Y", 0.04)]
    with caplog.at_level(logging.WARNING, logger="engine.rates.curves"):
        with pytest.raises(CurveBuildError) as exc:
            build_curve_set(bad, datetime.date(2026, 9, 22), "USD", "SOFR")
    assert "log-linear (flat forwards) bootstrap on 2026-09-22 did not converge" in str(exc.value)
    assert any("USD SOFR" in r.getMessage() for r in caplog.records)
