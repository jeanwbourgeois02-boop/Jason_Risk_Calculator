"""Tests for engine/rates/: OIS curve bootstrap, IRS instrument/valuation, and the
SQLite glue (bootstrap_and_store / price_and_store).

QuantLib-dependent tests are skipped (not errored) when QuantLib is not installed in
this environment, mirroring how blpapi-dependent tests are skipped elsewhere in this
repo (see tests/test_bloomberg.py). Non-QuantLib glue (reading curve_quotes rows,
picking a source, mark-row shaping) is tested directly without any QuantLib import so
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
# bootstrap and DV01 bucket tests exercise more than one segment.
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


def test_official_mark_source_is_ql_pricer_for_irs_marks():
    from data.ingest.schema import OFFICIAL_MARK_SOURCE

    assert OFFICIAL_MARK_SOURCE["PAR_RATE"] == "QL_PRICER"
    assert OFFICIAL_MARK_SOURCE["PV_USD"] == "QL_PRICER"
    assert OFFICIAL_MARK_SOURCE["DV01_USD"] == "QL_PRICER"


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


# --------------------------------------------------------------------------- valuation

@needs_quantlib
def test_par_swap_prices_to_zero_at_the_curve_implied_par_rate():
    """Reproduces the reference project's own reconciliation check (recon.py):
    a swap struck at its own fair/par rate must price to ~0 NPV. Tolerance matches
    the reference project's par-swap sanity check (well under 1 cent per 10mm
    notional -- see swapcalc/pricing/recon.py's own par-check tolerance)."""
    from engine.rates.curves import build_curve_set
    from engine.rates.valuation import price_swap

    as_of = datetime.date(2026, 8, 17)
    cs = build_curve_set(MOCK_USD_SOFR, as_of, "USD")
    effective = datetime.date(2026, 8, 19)
    maturity = datetime.date(2031, 8, 19)

    # First pass to discover the fair rate, second pass struck exactly at it.
    probe = price_swap(cs, effective, maturity, 0.0410, 10_000_000, True)
    assert probe.par_rate is not None
    par_result = price_swap(cs, effective, maturity, probe.par_rate, 10_000_000, True)
    assert abs(par_result.npv) < 1.0  # USD, 10mm notional


@needs_quantlib
def test_payer_dv01_is_positive_for_a_rate_rise():
    """Sign convention (engine/rates/valuation.py docstring): dv01_parallel =
    NPV(bumped) - NPV(base) for a +1bp bump; a payer benefits from rates rising, so
    this must be positive for pay_fixed=True."""
    from engine.rates.curves import build_curve_set
    from engine.rates.valuation import price_swap

    as_of = datetime.date(2026, 8, 17)
    cs = build_curve_set(MOCK_USD_SOFR, as_of, "USD")
    result = price_swap(cs, datetime.date(2026, 8, 19), datetime.date(2031, 8, 19), 0.0410, 10_000_000, True)
    assert result.dv01_parallel > 0
    # Bucket DV01s should sum close to the parallel DV01 (same bump size, same swap).
    assert sum(result.dv01_buckets.values()) == pytest.approx(result.dv01_parallel, rel=0.05)


@needs_quantlib
def test_receiver_dv01_is_negative_for_a_rate_rise():
    from engine.rates.curves import build_curve_set
    from engine.rates.valuation import price_swap

    as_of = datetime.date(2026, 8, 17)
    cs = build_curve_set(MOCK_USD_SOFR, as_of, "USD")
    result = price_swap(cs, datetime.date(2026, 8, 19), datetime.date(2031, 8, 19), 0.0410, 10_000_000, False)
    assert result.dv01_parallel < 0


# --------------------------------------------------------------------------- store.py glue

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


@needs_quantlib
def test_bootstrap_and_store_raises_when_no_quotes():
    from engine.rates.store import bootstrap_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    with pytest.raises(ValueError):
        bootstrap_and_store(conn, "2026-08-17", "USD")


def _seed_manual_irs_trade(conn, trade_id="TEST-IRS-1", quantity=10_000_000.0, fixed_rate=0.0410,
                            effective="2026-08-19", maturity="2031-08-19", ccy="USD"):
    instrument_id = f"IRSOIS-{ccy}-{trade_id}"
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, "IRS", ccy, ccy, 1.0, 0, instrument_id, maturity),
    )
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, "IRS", trade_id, effective, quantity, fixed_rate,
         "TEST-ACCT", "TEST-CPTY", "TEST", "TEST-TRADER", "test IRS"),
    )
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
        "settles_cash) VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 1, "FIXED", ccy, -quantity, effective, maturity, fixed_rate, 0),
    )
    conn.execute(
        "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
        "settles_cash) VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 2, "FLOAT", ccy, quantity, effective, maturity, 0.0, 0),
    )
    conn.commit()
    return instrument_id


@needs_quantlib
def test_price_and_store_writes_pv_dv01_par_rate_marks():
    from engine.rates.store import bootstrap_and_store, price_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", MOCK_USD_SOFR)
    instrument_id = _seed_manual_irs_trade(conn)

    result = price_and_store(conn, "2026-08-17", "TEST-IRS-1")
    assert result.par_rate is not None

    rows = conn.execute(
        "SELECT mark_type, value, source, settle_date, snapped_at FROM marks "
        "WHERE as_of_date = ? AND instrument_id = ?",
        ("2026-08-17", instrument_id),
    ).fetchall()
    by_type = {r[0]: r for r in rows}
    assert set(by_type) == {"PV_USD", "DV01_USD", "CASHFLOW_USD", "PAR_RATE"}
    for mark_type, value, source, settle_date, snapped_at in rows:
        assert source == "QL_PRICER"
        assert settle_date == "2031-08-19"
        assert "2026-08-17T15:00:00" in snapped_at  # the official close, 15:00 New York
    assert by_type["DV01_USD"][1] > 0  # payer (quantity > 0), rate rise benefits

    # marks_official must resolve to these QL_PRICER rows (CLAUDE.md "Official marks").
    official = conn.execute(
        "SELECT mark_type, value, source FROM marks_official WHERE as_of_date = ? AND instrument_id = ?",
        ("2026-08-17", instrument_id),
    ).fetchall()
    assert {r[0]: r[2] for r in official} == {"PV_USD": "QL_PRICER", "DV01_USD": "QL_PRICER",
                                              "CASHFLOW_USD": "QL_PRICER", "PAR_RATE": "QL_PRICER"}


@needs_quantlib
def test_price_and_store_receiver_has_negative_dv01():
    from engine.rates.store import price_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-08-17", "USD", "SOFR", MOCK_USD_SOFR)
    instrument_id = _seed_manual_irs_trade(conn, trade_id="TEST-IRS-RECV", quantity=-5_000_000.0)

    result = price_and_store(conn, "2026-08-17", "TEST-IRS-RECV")
    assert result.dv01_parallel < 0

    dv01_row = conn.execute(
        "SELECT value FROM marks WHERE instrument_id = ? AND mark_type = 'DV01_USD'", (instrument_id,)
    ).fetchone()
    assert dv01_row[0] < 0


@needs_quantlib
def test_price_and_store_rejects_non_irs_product():
    from engine.rates.store import price_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
        "bbg_ticker, expiry_date) VALUES ('USDJPY','FX','USD','JPY',1.0,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) VALUES "
        "('T1','BNP','USDJPY','FX_SPOT','T1','2026-08-17',1000000,150.0,'A','C','S','T','desc')"
    )
    conn.commit()
    with pytest.raises(ValueError):
        price_and_store(conn, "2026-08-17", "T1")


# --------------------------------------------------------------------------- real IRS row via data-ingest

@needs_quantlib
def test_price_and_store_on_a_real_irs_trade_from_the_reference_csv():
    """Loads a real IRS trade produced by data-ingest's blotter.py from
    data/raw/new_sample_trades.csv via the full ingest pipeline, then prices it against a
    mock/synthetic curve_quotes snapshot (no live Bloomberg data exists in this
    environment) and confirms price_and_store writes plausible marks without crashing.
    """
    import os

    from data.ingest.blotter import load as blotter_load

    csv_path = os.path.join("data", "raw", "new_sample_trades.csv")
    if not os.path.exists(csv_path):
        pytest.skip("reference CSV not present in this checkout")

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    blotter_load(csv_path, conn, strict=False)

    irs_trades = conn.execute(
        "SELECT trade_id, instrument_id, quantity, trade_date FROM trades WHERE product = 'IRS'"
    ).fetchall()
    assert irs_trades, "expected at least one IRS trade parsed from the reference CSV"

    trade_id, instrument_id, quantity, as_of = irs_trades[0]

    # Mock USD SOFR snapshot dated the same as_of used for pricing (curve_quotes has no
    # real data in this environment -- see module docstring).
    _seed_curve_quotes(conn, as_of, "USD", "SOFR", MOCK_USD_SOFR)

    from engine.rates.store import price_and_store

    result = price_and_store(conn, as_of, trade_id)
    assert result.par_rate is not None
    assert abs(result.dv01_parallel) > 0

    rows = conn.execute(
        "SELECT mark_type, value FROM marks WHERE as_of_date = ? AND instrument_id = ?",
        (as_of, instrument_id),
    ).fetchall()
    by_type = dict(rows)
    assert set(by_type) == {"PV_USD", "DV01_USD", "CASHFLOW_USD", "PAR_RATE"}
    # Plausibility: notional is hundreds of millions (see the CSV sample), so DV01 should
    # be a material USD amount, and PV should not be absurdly large relative to notional.
    assert abs(by_type["DV01_USD"]) > 100
    assert abs(by_type["PV_USD"]) < abs(quantity) * 2


# --------------------------------------------------------------------------- fixings, cashflows, book-wide pricing (2026-09-17)
def _seed_fixings(conn, index, start: datetime.date, end: datetime.date, value=0.0530):
    rows = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            rows.append((index, d.isoformat(), value, "BBG_BDH"))
        d += datetime.timedelta(days=1)
    with conn:
        conn.executemany('INSERT OR REPLACE INTO index_fixings ("index", fixing_date, value, source) VALUES (?,?,?,?)', rows)


@needs_quantlib
def test_seasoned_swap_fails_without_fixings_and_prices_with_them():
    from engine.rates.store import price_all_and_store, price_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    as_of = "2026-09-17"
    _seed_curve_quotes(conn, as_of, "USD", "SOFR", MOCK_USD_SOFR)
    _seed_manual_irs_trade(conn, effective="2026-08-12", maturity="2027-02-11")
    with pytest.raises(RuntimeError, match="fixing"):
        price_and_store(conn, as_of, "TEST-IRS-1")
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 0   # nothing invented
    # price_all_and_store reports the failure per trade instead of raising
    out = price_all_and_store(conn, as_of)
    assert out[0]["ok"] is False and "fixing" in out[0]["error"]
    _seed_fixings(conn, "SOFR", datetime.date(2026, 8, 10), datetime.date(2026, 9, 17))
    out = price_all_and_store(conn, as_of)
    assert out == [{"trade_id": "TEST-IRS-1", "instrument_id": "IRSOIS-USD-TEST-IRS-1", "ccy": "USD", "ok": True, "error": "",
                    "interpolation": "LogLinear", "note": ""}]
    by_type = dict(conn.execute("SELECT mark_type, value FROM marks_official WHERE as_of_date = ?", (as_of,)).fetchall())
    assert set(by_type) == {"PV_USD", "DV01_USD", "CASHFLOW_USD", "PAR_RATE"}
    assert by_type["CASHFLOW_USD"] == 0.0    # single payment at maturity: nothing settled yet
    assert by_type["PV_USD"] != 0.0


@needs_quantlib
def test_expired_swap_has_zero_pv_and_its_result_in_cashflows():
    from engine.rates.store import price_all_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    as_of = "2027-03-01"
    _seed_curve_quotes(conn, as_of, "USD", "SOFR", MOCK_USD_SOFR)
    _seed_manual_irs_trade(conn, effective="2026-08-12", maturity="2027-02-11", fixed_rate=0.05)
    _seed_fixings(conn, "SOFR", datetime.date(2026, 8, 10), datetime.date(2027, 3, 1), value=0.0530)
    assert price_all_and_store(conn, as_of)[0]["ok"] is True
    by_type = dict(conn.execute("SELECT mark_type, value FROM marks_official WHERE as_of_date = ?", (as_of,)).fetchall())
    assert by_type["PV_USD"] == 0.0 and by_type["DV01_USD"] == 0.0
    # payer at 5% vs ~5.3% compounded SOFR over 183 days on 10mm: received roughly 10mm * 0.3% * 0.51
    assert 10_000 < by_type["CASHFLOW_USD"] < 25_000


@needs_quantlib
def test_non_usd_swap_is_converted_at_spot_or_refused():
    from engine.rates.store import price_and_store

    eur_quotes = [("1W", 0.0315), ("1M", 0.0313), ("3M", 0.0310), ("1Y", 0.0290), ("5Y", 0.0255), ("10Y", 0.0260)]
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_curve_quotes(conn, "2026-08-17", "EUR", "ESTR", eur_quotes)
    _seed_manual_irs_trade(conn, ccy="EUR")
    with pytest.raises(ValueError, match="SPOT"):
        price_and_store(conn, "2026-08-17", "TEST-IRS-1")
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO marks VALUES ('2026-08-17','EURUSD','2026-08-17','SPOT',1.25,'BBG_BFXFORWARD','t')")
    conn.commit()
    result = price_and_store(conn, "2026-08-17", "TEST-IRS-1")
    pv_usd = conn.execute("SELECT value FROM marks_official WHERE mark_type = 'PV_USD'").fetchone()[0]
    assert pv_usd == pytest.approx(result.npv * 1.25)


def test_price_all_and_store_skips_realised_and_future_dated_swaps():
    """Glue only (no QuantLib needed): a swap already frozen in realised_pnl, or dealt after
    as_of, is not priced at all -- the returned list is empty."""
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_manual_irs_trade(conn, trade_id="LATER", effective="2026-12-01", maturity="2027-12-01")
    _seed_manual_irs_trade(conn, trade_id="DONE", effective="2025-01-01", maturity="2026-01-01")
    conn.execute("INSERT INTO realised_pnl VALUES ('DONE','IRSOIS-USD-DONE','IRS','USD','2026-01-01',1,0,'PV_USD',1,"
                 "'2026-01-01','QL_PRICER',1,'t','')")
    conn.commit()
    from engine.rates.store import price_all_and_store

    assert price_all_and_store(conn, "2026-08-17") == []


# --------------------------------------------------------------------------- flat forwards by default (2026-09-22)
# The Bloomberg PC's USD SOFR quotes of 2026-09-22 (data/bbg_snapshot/curve_quotes.csv). At a
# 2026-09-22 evaluation date QuantLib 1.43's log-cubic bootstrap does not converge on them
# ("convergence not reached after 99 iterations; last improvement 0.0178479 ..."), which failed
# every IRS and every FX option needing the USD curve on that day's pull; at 2026-09-21 it converges.
# Flat forwards (log-linear discount) converge on every date and are the default since then
# (user, 2026-09-22: "yes switch to flat forwards and rerun the past days").
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
    interpolation: the bootstrap is forced (every discount factor read), the curve is sane
    and the swap pricer and its DV01 bump work on it."""
    import math

    from engine.rates.curves import build_curve_set
    from engine.rates.valuation import price_swap

    cs = build_curve_set(USD_SOFR_2026_09_22, datetime.date(2026, 9, 22), "USD", "SOFR")
    assert cs.interpolation == "LogLinear" and cs.bootstrap_note == ""
    dates = [datetime.date(2026 + n, 9, 24) for n in range(1, 31)]
    dfs = _discount_factors(cs, dates)
    assert all(math.isfinite(df) and 0.0 < df < 1.0 for df in dfs)
    assert all(a > b for a, b in zip(dfs, dfs[1:]))
    df_1y_from_as_of = _discount_factors(cs, [datetime.date(2027, 9, 22)])[0]
    assert df_1y_from_as_of == pytest.approx(0.956377, abs=2e-6)   # the log-linear 1Y df of the repro
    result = price_swap(cs, datetime.date(2026, 9, 24), datetime.date(2031, 9, 24), 0.0457, 10_000_000, True)
    assert math.isfinite(result.npv) and result.dv01_parallel > 0 and result.par_rate is not None
    assert sum(result.dv01_buckets.values()) == pytest.approx(result.dv01_parallel, rel=0.05)


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


@needs_quantlib
def test_price_all_and_store_reports_interpolation_and_note_per_trade():
    from engine.rates.store import price_all_and_store

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    as_of = "2026-09-22"
    _seed_curve_quotes(conn, as_of, "USD", "SOFR", USD_SOFR_2026_09_22)
    # trade_date = effective date in this helper, so the swap must start on or before as_of to be priced
    _seed_manual_irs_trade(conn, effective="2026-09-22", maturity="2031-09-22", fixed_rate=0.0457)
    out = price_all_and_store(conn, as_of)
    assert len(out) == 1 and out[0]["ok"] is True, out
    assert out[0]["interpolation"] == "LogLinear" and out[0]["note"] == ""
    # bootstrap_and_store wrote the same curves rows as before (no interpolation column).
    cols = [r[1] for r in conn.execute("PRAGMA table_info(curves)").fetchall()]
    assert "interpolation" not in cols
    n = conn.execute("SELECT COUNT(*) FROM curves WHERE curve_id = 'USD-SOFR-OIS' AND as_of_date = ?", (as_of,)).fetchone()[0]
    assert n == len(USD_SOFR_2026_09_22)
    assert conn.execute("SELECT COUNT(*) FROM marks_official WHERE as_of_date = ? AND mark_type = 'PV_USD'", (as_of,)).fetchone()[0] == 1
    # A trade whose curve could not be built carries empty interpolation / note.
    _seed_manual_irs_trade(conn, trade_id="TEST-IRS-EUR", ccy="EUR")
    eur = [e for e in price_all_and_store(conn, as_of) if e["ccy"] == "EUR"][0]
    assert eur["ok"] is False and eur["interpolation"] == "" and eur["note"] == ""


# --------------------------------------------------------------------------- recalc_on_file (2026-09-22)

def _seed_recalc_book(conn):
    """Quotes on 09-18, 09-21, 09-22 (good) and 09-23 (impossible curve); one USD swap dealt
    09-21; SOFR fixings around it so the seasoned days price."""
    for day in ("2026-09-18", "2026-09-21", "2026-09-22"):
        _seed_curve_quotes(conn, day, "USD", "SOFR", USD_SOFR_2026_09_22)
    _seed_curve_quotes(conn, "2026-09-23", "USD", "SOFR", [("1M", 0.04), ("1Y", -5.0), ("5Y", 0.04)])
    _seed_manual_irs_trade(conn, effective="2026-09-21", maturity="2031-09-21", fixed_rate=0.0457)
    _seed_fixings(conn, "SOFR", datetime.date(2026, 9, 14), datetime.date(2026, 9, 25), value=0.0389)


@needs_quantlib
def test_recalc_on_file_reprices_every_day_on_file_and_replaces_the_old_marks():
    from engine.rates.store import recalc_on_file

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_recalc_book(conn)
    # A stale mark and a stale curve node from the old interpolation, under the same keys.
    conn.execute("INSERT INTO marks VALUES ('2026-09-21','IRSOIS-USD-TEST-IRS-1','2031-09-21','PV_USD',123456.0,'QL_PRICER','old')")
    conn.execute("INSERT INTO curves VALUES ('USD-SOFR-OIS','2026-09-21','2027-09-23',0.5,0.045035,'QL_PRICER')")
    conn.commit()

    out = recalc_on_file(conn, "2026-09-22")
    assert "error" not in out, out
    assert out["as_of"] == "2026-09-22" and out["since"] == "2026-09-18"   # earliest quotes on file
    assert [d["day"] for d in out["days"]] == ["2026-09-18", "2026-09-21", "2026-09-22"]   # 09-23 > as_of: not run
    assert out["days"][0] == {"day": "2026-09-18", "priced": 0, "failed": []}   # swap not dealt yet
    assert out["days"][1] == {"day": "2026-09-21", "priced": 1, "failed": []}
    assert out["days"][2] == {"day": "2026-09-22", "priced": 1, "failed": []}
    assert out["priced"] == 2 and out["failed"] == 0

    rows = conn.execute(
        "SELECT as_of_date, mark_type, value, snapped_at FROM marks_official "
        "WHERE instrument_id = 'IRSOIS-USD-TEST-IRS-1' ORDER BY as_of_date, mark_type"
    ).fetchall()
    by_day = {}
    for day, mark_type, value, snapped in rows:
        by_day.setdefault(day, {})[mark_type] = (value, snapped)
    assert set(by_day) == {"2026-09-21", "2026-09-22"}
    for day in by_day:
        assert set(by_day[day]) == {"PV_USD", "DV01_USD", "CASHFLOW_USD", "PAR_RATE"}
        assert all(snapped == f"{day}T15:00:00-04:00" for _v, snapped in by_day[day].values())   # the day's own close
    assert by_day["2026-09-21"]["PV_USD"][0] != 123456.0      # the stale mark was replaced
    assert by_day["2026-09-21"]["DV01_USD"][0] > 0
    # The stale curve node was replaced under the same key; each priced day's curve is on
    # file (09-18 had no swap to price, so price_all_and_store built no curve for it).
    df = conn.execute("SELECT discount_factor FROM curves WHERE curve_id='USD-SOFR-OIS' AND as_of_date='2026-09-21' "
                      "AND node_date='2027-09-23' AND source='QL_PRICER'").fetchone()[0]
    assert 0.9 < df < 1.0
    for day in ("2026-09-21", "2026-09-22"):
        n = conn.execute("SELECT COUNT(*) FROM curves WHERE curve_id='USD-SOFR-OIS' AND as_of_date=?", (day,)).fetchone()[0]
        assert n == len(USD_SOFR_2026_09_22), day
    assert conn.execute("SELECT COUNT(*) FROM curves WHERE as_of_date='2026-09-18'").fetchone()[0] == 0
    # Idempotent: the rerun writes the same values again.
    again = recalc_on_file(conn, "2026-09-22")
    assert again["priced"] == 2 and again["failed"] == 0
    pv = conn.execute("SELECT value FROM marks_official WHERE as_of_date='2026-09-21' AND mark_type='PV_USD'").fetchone()[0]
    assert pv == by_day["2026-09-21"]["PV_USD"][0]


@needs_quantlib
def test_recalc_on_file_reports_a_day_that_cannot_build_and_respects_since():
    from engine.rates.store import recalc_on_file

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    _seed_recalc_book(conn)

    out = recalc_on_file(conn, "2026-09-23", since="2026-09-22")
    assert "error" not in out, out
    assert out["since"] == "2026-09-22"
    assert [d["day"] for d in out["days"]] == ["2026-09-22", "2026-09-23"]   # 09-18 and 09-21 left alone
    assert out["days"][0] == {"day": "2026-09-22", "priced": 1, "failed": []}
    bad = out["days"][1]
    assert bad["day"] == "2026-09-23" and bad["priced"] == 0
    assert [f["trade_id"] for f in bad["failed"]] == ["TEST-IRS-1"]
    assert bad["failed"][0]["error"].startswith("CurveBuildError:") and "did not converge" in bad["failed"][0]["error"]
    assert out["priced"] == 1 and out["failed"] == 1
    # The days before `since` were not priced, the bad day wrote nothing.
    days = {r[0] for r in conn.execute("SELECT DISTINCT as_of_date FROM marks_official WHERE mark_type='PV_USD'").fetchall()}
    assert days == {"2026-09-22"}
    assert conn.execute("SELECT COUNT(*) FROM curves WHERE as_of_date='2026-09-23'").fetchone()[0] == 0


def test_recalc_on_file_never_raises():
    """Glue only (no QuantLib needed): a bad date, or no quotes at all, is reported, never raised."""
    from engine.rates.store import recalc_on_file

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    out = recalc_on_file(conn, "not-a-date")
    assert out["error"].startswith("ValueError(") and out["days"] == [] and out["priced"] == 0 and out["failed"] == 0
    out = recalc_on_file(conn, "2026-09-22", since="yesterday")
    assert out["error"].startswith("ValueError(") and out["days"] == []
    # No curve_quotes on file: nothing to run, since falls back to as_of, no error.
    out = recalc_on_file(conn, "2026-09-22")
    assert out == {"as_of": "2026-09-22", "since": "2026-09-22", "days": [], "priced": 0, "failed": 0}
