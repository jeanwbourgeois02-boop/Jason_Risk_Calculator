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
    assert out == [{"trade_id": "TEST-IRS-1", "instrument_id": "IRSOIS-USD-TEST-IRS-1", "ccy": "USD", "ok": True, "error": ""}]
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
