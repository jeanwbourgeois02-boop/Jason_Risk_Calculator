"""Tests for engine/ladder (cash ladder + delta-per-currency). Real-file tests skip if
the raw blotter CSV is absent."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.ingest import blotter, schema
from engine.ladder import (
    cash_ladder, delta_per_ccy, spot_table, convert_to_usd, ladder_table,
    per_pair_delta, pair_delta_totals,
)

REPO = Path(__file__).resolve().parents[1]
# 2026-09-17 ("no bnp fall back", docs/bnp-excel-removal.md): the real-file fixtures
# below used to load data/raw/HA_PNL_20260818.csv via the retired data/ingest/bnp.py.
# BNP is no longer a trade source at all, so they now load the blotter's own reference
# sample (the app's only trade source) via data/ingest/blotter.py instead.
RAW = REPO / "data" / "raw" / "new_sample_trades.csv"
AS_OF = "2026-08-17"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")

NDF_CCYS = {"BRL", "TWD", "KRW", "IDR"}

# A handful of the real file's pairs, marked with synthetic non-official SPOT values so
# `ladder_table(..., source=...)`'s reconciliation-only spot-source filter (still live,
# generic functionality -- see `spot_table`'s docstring) has some priced and some
# unpriced currencies to exercise, without depending on the retired BNP_BVAL extractor.
MARKED_PAIRS = {"USDJPY": 150.0, "USDCAD": 1.35, "USDCHF": 0.88, "USDMXN": 18.5,
                "USDTRY": 34.0, "USDSEK": 10.5}
MARKED_CCYS = {"JPY", "CAD", "CHF", "MXN", "TRY", "SEK"}
RECON_SOURCE = "MANUAL"


@pytest.fixture(scope="module")
def real_conn():
    conn = schema.connect(":memory:")
    blotter.load(RAW, conn, strict=False)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def real_conn_marks():
    """Real blotter file (source already 'XLSX', trades_official-visible with no
    relabelling needed) plus a small set of synthetic reconciliation-only SPOT marks
    (source='MANUAL') for `ladder_table(..., source='MANUAL')`'s spot-source filter
    (never `marks_official`). Replaces the pre-2026-09-17 fixture that loaded the
    retired BNP file's own BNP_BVAL marks via data/bloomberg/bnp_marks.py."""
    conn = schema.connect(":memory:")
    blotter.load(RAW, conn, strict=False)
    for pair, value in MARKED_PAIRS.items():
        conn.execute(
            "INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
            (AS_OF, pair, AS_OF, "SPOT", value, RECON_SOURCE, AS_OF + "T17:00:00-04:00"),
        )
    conn.commit()
    yield conn
    conn.close()



# --------------------------------------------------------------------------- real file
@needs_raw
def test_ladder_ccys_come_from_legs(real_conn):
    # 2026-09-17: the ladder's CASH/`positions` column is gone (see
    # test_cash_ladder_never_reads_positions_table below); every ccy in the ladder must
    # be traceable to a trade_legs row.
    ladder = cash_ladder(real_conn, AS_OF)
    leg_ccys = {r[0] for r in real_conn.execute("SELECT DISTINCT ccy FROM trade_legs")}
    assert set(ladder["ccy"]) <= leg_ccys


@needs_raw
def test_ladder_usd_leg_amount_matches_trade_legs(real_conn):
    ladder = cash_ladder(real_conn, AS_OF)
    ladder_usd_legs = ladder[(ladder["ccy"] == "USD") & (ladder["kind"] == "LEG")]["amount"].sum()
    expected = real_conn.execute(
        "SELECT SUM(l.amount) FROM trade_legs l JOIN trades_official t USING (trade_id) "
        "WHERE l.ccy = 'USD' AND l.settles_cash = 1 AND l.settle_date >= :as_of AND t.trade_date <= :as_of",
        {"as_of": AS_OF},
    ).fetchone()[0]
    assert math.isclose(ladder_usd_legs, expected, abs_tol=1e-6)


@needs_raw
def test_ladder_excludes_ndf_ccys_includes_try(real_conn):
    ladder = cash_ladder(real_conn, AS_OF)
    ccys = set(ladder["ccy"])
    assert ccys.isdisjoint(NDF_CCYS)
    assert "TRY" in ccys


@needs_raw
def test_ladder_no_settle_date_before_as_of(real_conn):
    # Vacuous on the real file (every leg settles 2026-09-08..09-21); kept as a sanity
    # check, but the real boundary behaviour is pinned by
    # test_ladder_settle_date_boundary_is_inclusive below (synthetic).
    ladder = cash_ladder(real_conn, AS_OF)
    assert (ladder["settle_date"] >= AS_OF).all()


@needs_raw
def test_delta_per_ccy_real_file_marks_official_empty(real_conn):
    # No marks are loaded for the real file, so marks_official is empty: only the
    # forward-leg UNION branch of the delta SQL contributes.
    n_marks = real_conn.execute("SELECT COUNT(*) FROM marks_official").fetchone()[0]
    assert n_marks == 0

    delta = delta_per_ccy(real_conn, AS_OF)
    # delta_usd is NaN for every non-USD ccy (no SPOT marks available)
    non_usd = delta[delta["ccy"] != "USD"]
    assert non_usd["delta_usd"].isna().all()

    usd_row = delta[delta["ccy"] == "USD"]
    assert len(usd_row) == 1
    # Note the delta SQL uses settle_date > :as_of (strictly greater), not >=.
    expected = real_conn.execute(
        "SELECT SUM(l.amount) FROM trade_legs l JOIN trades t USING (trade_id) "
        "WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE') AND l.ccy = 'USD' AND l.settle_date > :as_of",
        {"as_of": AS_OF},
    ).fetchone()[0]
    assert math.isclose(usd_row["delta"].iloc[0], expected, abs_tol=1e-6)
    assert math.isclose(usd_row["delta_usd"].iloc[0], expected, abs_tol=1e-6)


# --------------------------------------------------------------------------- synthetic
def _mk_conn():
    return schema.connect(":memory:")


def _insert_instrument(conn, instrument_id, base_ccy, quote_ccy, asset_class="FX"):
    conn.execute(
        "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        (instrument_id, asset_class, base_ccy, quote_ccy, 1.0, 0, f"{instrument_id} Curncy", "9999-12-31"),
    )


def _insert_trade(conn, trade_id, instrument_id, product, quantity, account="ACC", package_id=None):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, product, package_id or trade_id, AS_OF, quantity, 1.0,
         account, "CPTY", "STRAT", "TRADER", "synthetic", ""),
    )


def _insert_leg(conn, trade_id, leg_no, leg_type, ccy, amount, settle_date, settles_cash=1, start_date=AS_OF, rate=0.0):
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, settles_cash),
    )


def _insert_mark(conn, as_of, instrument_id, settle_date, mark_type, value, source, snapped_at="2026-08-17T15:00:00-04:00"):
    conn.execute(
        "INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
        (as_of, instrument_id, settle_date, mark_type, value, source, snapped_at),
    )


def test_offsetting_legs_net_to_zero_or_absent():
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 100.0)
    _insert_trade(conn, "t2", "AUDUSD", "FX_FWD", -100.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 100.0, "2026-08-20")
    _insert_leg(conn, "t2", 1, "FX_NEAR", "AUD", -100.0, "2026-08-20")
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    rows = ladder[(ladder["ccy"] == "AUD") & (ladder["settle_date"] == "2026-08-20") & (ladder["kind"] == "LEG")]
    # Pinned behaviour: the LEG query is a GROUP BY ccy, settle_date SUM(amount), so
    # offsetting legs on the same (ccy, settle_date) always produce exactly one row with
    # amount 0.0 (SQL SUM never drops a zero-sum group), never an absent row.
    assert len(rows) == 1
    assert math.isclose(rows["amount"].iloc[0], 0.0, abs_tol=1e-9)


def test_same_ccy_different_dates_do_not_net():
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 100.0)
    _insert_trade(conn, "t2", "AUDUSD", "FX_FWD", 50.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 100.0, "2026-08-20")
    _insert_leg(conn, "t2", 1, "FX_NEAR", "AUD", 50.0, "2026-08-25")
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    rows = ladder[(ladder["ccy"] == "AUD") & (ladder["kind"] == "LEG")]
    assert len(rows) == 2
    d20 = rows[rows["settle_date"] == "2026-08-20"]["amount"].iloc[0]
    d25 = rows[rows["settle_date"] == "2026-08-25"]["amount"].iloc[0]
    assert math.isclose(d20, 100.0)
    assert math.isclose(d25, 50.0)


def test_ladder_settle_date_boundary_is_inclusive():
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 100.0)
    _insert_trade(conn, "t2", "AUDUSD", "FX_FWD", 200.0)
    # settles before as_of: excluded
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 100.0, "2026-08-16")
    # settles exactly on as_of: included (>= boundary)
    _insert_leg(conn, "t2", 1, "FX_NEAR", "AUD", 200.0, AS_OF)
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    rows = ladder[(ladder["ccy"] == "AUD") & (ladder["kind"] == "LEG")]
    assert set(rows["settle_date"]) == {AS_OF}
    assert math.isclose(rows["amount"].iloc[0], 200.0)


# 2026-09-17 ("no bnp fall back" -- user decision, docs/bnp-excel-removal.md): the
# ladder's CASH-balance column (a `positions` row with instrument_id LIKE 'CASH-%',
# source='BNP'/'CALC') was fed exclusively by the retired BNP daily snapshot -- always
# empty once BNP stopped being ingested, so removed outright rather than left inert,
# and the `positions` table itself is dropped from the schema. `cash_ladder` no longer
# reads any such table and no longer takes a `source` parameter.
def test_cash_ladder_never_reads_positions_table():
    conn = _mk_conn()
    _insert_instrument(conn, "CASH-USD", "USD", "USD", asset_class="CASH")
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    assert "USD" not in set(ladder["ccy"])
    assert ladder.empty

    with pytest.raises(TypeError):
        cash_ladder(conn, AS_OF, source="BNP")


def test_cash_ladder_columns_are_leg_only():
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 100.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 100.0, "2026-08-20")
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    assert list(ladder.columns) == ["ccy", "settle_date", "kind", "amount"]
    assert set(ladder["kind"]) == {"LEG"}


def test_records_from_db_settle_on_as_of_grid_vs_exposure_boundary():
    """CLAUDE.md 'Six tabs as views': the grid keeps settle_date >= as_of (a leg
    settling today is cash that moves today, shown on today's date row). For the
    delta/exposure path the rule depends on deliverability (2026-09-18, settled cash):
    a DELIVERABLE leg settling on as_of is cash by close and still carries its
    currency's delta, so it reaches portfolio_totals through the settled records; an
    NDF leg settling on as_of delivers nothing and carries no delta by close, so it is
    excluded from the exposure path -- and that exclusion must actually reach
    portfolio_totals, not just the raw record list."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    from engine.ladder.exposure_adapter import SETTLED, exposure_records_from_db, records_from_db

    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 100.0)
    # deliverable, settles exactly on as_of
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 100.0, AS_OF)
    _insert_leg(conn, "t1", 2, "FX_NEAR", "USD", -70.0, AS_OF)
    conn.commit()

    grid_records, _ = records_from_db(conn, AS_OF)
    assert any(r["currency"] == "AUD" and r["settlement_date"] == AS_OF for r in grid_records)
    assert not any(r["settlement_date"] == SETTLED for r in grid_records)

    exposure_records, _ = records_from_db(conn, AS_OF, for_exposure=True)
    assert {(r["currency"], r["settlement_date"]) for r in exposure_records} == {("AUD", SETTLED), ("USD", SETTLED)}
    assert exposure_records_from_db(conn, AS_OF)[0] == exposure_records

    rate = {"AUD": {"rate": 0.6, "inverted": False, "source": "TEST", "timestamp": "", "stale": False}}
    totals = portfolio_totals(build_exposure(exposure_records, rate))
    assert math.isclose(totals["net_usd"], 100.0 * 0.6)
    assert totals["missing"] == []

    # NDF leg settling on as_of: no delivery, no delta by close, nothing in either
    # settled or open exposure records; zero exposure is a real 0.0, never NaN.
    ndf = _mk_conn()
    ndf.execute("INSERT INTO instruments VALUES ('USDKRW','FX','USD','KRW',1,1,'USDKRW Curncy','9999-12-31')")
    _insert_trade(ndf, "n1", "USDKRW", "FX_FWD", 100.0)
    ndf.execute("INSERT INTO trade_legs VALUES ('n1',1,'FX_NEAR','USD',100.0,'2026-08-01',?,1400.0,0)", (AS_OF,))
    ndf.execute("INSERT INTO trade_legs VALUES ('n1',2,'FX_NEAR','KRW',-140000.0,'2026-08-01',?,1400.0,0)", (AS_OF,))
    ndf.commit()
    assert any(r["currency"] == "KRW" and r["settlement_date"] == AS_OF for r in records_from_db(ndf, AS_OF)[0])
    ndf_exposure, _ = exposure_records_from_db(ndf, AS_OF)
    assert ndf_exposure == []
    result = build_exposure(ndf_exposure, rate)
    assert result.summary.empty
    totals = portfolio_totals(result)
    assert totals["net_usd"] == 0.0
    assert totals["gross_usd"] == 0.0
    assert totals["missing"] == []
    assert totals["currencies"] == 0

    # A leg settling one day later than as_of must still be included on both paths.
    _insert_trade(conn, "t2", "AUDUSD", "FX_FWD", 50.0)
    _insert_leg(conn, "t2", 1, "FX_NEAR", "AUD", 50.0, "2026-08-18")
    conn.commit()
    exposure_records2, _ = records_from_db(conn, AS_OF, for_exposure=True)
    assert any(r["currency"] == "AUD" and r["settlement_date"] == "2026-08-18" for r in exposure_records2)
    result2 = build_exposure(exposure_records2, rate)
    totals2 = portfolio_totals(result2)
    # settled AUD 100 (cash by close, still delta) + the open AUD 50
    assert math.isclose(totals2["net_usd"], 150.0 * 0.6)


def test_portfolio_totals_empty_record_set_returns_zero_not_nan():
    """Empty record set (e.g. every leg settles exactly on as_of and is filtered out
    by the exposure '>' rule) means genuinely zero exposure, not missing data --
    portfolio_totals must return 0.0, not NaN, so the UI never shows "NaN" on the
    Net/Gross USD cards."""
    from engine.ladder.exposure import build_exposure, portfolio_totals

    result = build_exposure([], {})
    totals = portfolio_totals(result)
    assert totals["net_usd"] == 0.0
    assert totals["gross_usd"] == 0.0
    assert totals["currencies"] == 0
    assert totals["missing"] == []


def test_settles_cash_zero_excluded():
    conn = _mk_conn()
    _insert_instrument(conn, "USDBRL", "USD", "BRL")
    _insert_trade(conn, "t1", "USDBRL", "FX_FWD", 100.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "BRL", -500.0, "2026-08-20", settles_cash=0)
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    assert "BRL" not in set(ladder["ccy"])


def test_convert_to_usd_ignores_non_official_source():
    conn = _mk_conn()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 999.0, "MANUAL")
    conn.commit()

    spot = spot_table(conn, AS_OF)
    jpy_row = spot[spot["ccy"] == "JPY"]
    assert len(jpy_row) == 1
    assert math.isclose(jpy_row["spot"].iloc[0], 1.0 / 150.0)

    ladder = pd.DataFrame({
        "ccy": ["JPY", "XXX", "USD"],
        "settle_date": [AS_OF, AS_OF, AS_OF],
        "kind": ["LEG", "LEG", "LEG"],
        "amount": [1000.0, 500.0, 200.0],
    })
    out = convert_to_usd(ladder, spot)
    jpy_out = out[out["ccy"] == "JPY"]["amount_usd"].iloc[0]
    assert math.isclose(jpy_out, 1000.0 / 150.0)
    xxx_out = out[out["ccy"] == "XXX"]["amount_usd"].iloc[0]
    assert math.isnan(xxx_out)
    usd_out = out[out["ccy"] == "USD"]["amount_usd"].iloc[0]
    assert math.isclose(usd_out, 200.0)


def test_spot_table_prefers_direct_quote_on_conflict():
    # Genuine conflict: both AUDUSD (direct) and USDAUD (indirect) give an AUD rate on
    # the same as_of_date. The XXXUSD (direct) instrument must win.
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_instrument(conn, "USDAUD", "USD", "AUD")
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "SPOT", 0.65, "BBG_BFXFORWARD")
    _insert_mark(conn, AS_OF, "USDAUD", AS_OF, "SPOT", 1.6, "BBG_BFXFORWARD")
    conn.commit()

    spot = spot_table(conn, AS_OF)
    aud_row = spot[spot["ccy"] == "AUD"]
    assert len(aud_row) == 1
    # direct AUDUSD value (0.65), not the indirect 1/1.6 from USDAUD
    assert math.isclose(aud_row["spot"].iloc[0], 0.65)


def test_spot_table_zero_or_negative_spot_yields_nan_not_error():
    conn = _mk_conn()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 0.0, "BBG_BFXFORWARD")
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "SPOT", -0.5, "BBG_BFXFORWARD")
    conn.commit()

    # Must not raise (no ZeroDivisionError from 1.0 / 0.0).
    spot = spot_table(conn, AS_OF)
    assert "JPY" not in set(spot["ccy"])
    assert "AUD" not in set(spot["ccy"])

    ladder = pd.DataFrame({
        "ccy": ["JPY", "AUD"],
        "settle_date": [AS_OF, AS_OF],
        "kind": ["LEG", "LEG"],
        "amount": [1000.0, 1000.0],
    })
    out = convert_to_usd(ladder, spot)
    assert math.isnan(out[out["ccy"] == "JPY"]["amount_usd"].iloc[0])
    assert math.isnan(out[out["ccy"] == "AUD"]["amount_usd"].iloc[0])


def test_spot_table_ignores_marks_not_dated_as_of_settle():
    # settle_date must equal as_of_date for SPOT; a stray row with a different
    # settle_date (e.g. mis-tagged) must be excluded, not silently used.
    conn = _mk_conn()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_mark(conn, AS_OF, "USDJPY", "2026-09-08", "SPOT", 150.0, "BBG_BFXFORWARD")
    conn.commit()

    spot = spot_table(conn, AS_OF)
    assert "JPY" not in set(spot["ccy"])


def test_delta_per_ccy_fx_option():
    # DELTA's official source is schema.OFFICIAL_MARK_SOURCE['DELTA'] (QL_OPTIONS_PRICER
    # as of 2026-09-17, not MANUAL -- read from the mapping rather than hard-coded so this
    # test tracks any future re-mapping automatically).
    delta_source = schema.OFFICIAL_MARK_SOURCE["DELTA"]
    spot_source = schema.OFFICIAL_MARK_SOURCE["SPOT"]
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD", asset_class="FX_OPTION")
    _insert_trade(conn, "o1", "AUDUSD", "FX_OPTION", 1_000_000.0)
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "DELTA", 0.5, delta_source)
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "SPOT", 0.65, spot_source)
    conn.commit()

    delta = delta_per_ccy(conn, AS_OF)
    aud_row = delta[delta["ccy"] == "AUD"]
    usd_row = delta[delta["ccy"] == "USD"]
    assert math.isclose(aud_row["delta"].iloc[0], 1_000_000.0 * 0.5)
    # quote (USD) leg: -quantity * delta * spot
    assert math.isclose(usd_row["delta"].iloc[0], -1_000_000.0 * 0.5 * 0.65)
    assert math.isclose(aud_row["delta_usd"].iloc[0], 1_000_000.0 * 0.5 * 0.65)
    assert math.isclose(usd_row["delta_usd"].iloc[0], -1_000_000.0 * 0.5 * 0.65)


def test_delta_per_ccy_fx_option_instrument_id_differs_from_pair():
    # Real options carry their own contract instrument_id (e.g. a digital/vanilla name),
    # not the bare 6-letter pair -- the SPOT mark is always keyed on the pair. The join
    # must resolve the pair via instruments.base_ccy || instruments.quote_ccy, not via
    # the option's own instrument_id (which can never match a SPOT row).
    delta_source = schema.OFFICIAL_MARK_SOURCE["DELTA"]
    spot_source = schema.OFFICIAL_MARK_SOURCE["SPOT"]
    conn = _mk_conn()
    option_id = "USDJPY111926P-1"
    _insert_instrument(conn, option_id, "USD", "JPY", asset_class="FX_OPTION")
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_trade(conn, "o1", option_id, "FX_OPTION", 1_000_000.0)
    _insert_mark(conn, AS_OF, option_id, AS_OF, "DELTA", 0.4, delta_source)
    # SPOT mark keyed on the pair, not on the option's own instrument_id.
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, spot_source)
    conn.commit()

    delta = delta_per_ccy(conn, AS_OF)
    usd_row = delta[delta["ccy"] == "USD"]
    jpy_row = delta[delta["ccy"] == "JPY"]
    assert math.isclose(usd_row["delta"].iloc[0], 1_000_000.0 * 0.4)
    assert math.isclose(jpy_row["delta"].iloc[0], -1_000_000.0 * 0.4 * 150.0)


def test_delta_per_ccy_fx_option_missing_spot_raises():
    # Per CLAUDE.md: a missing SPOT mark for an FX_OPTION's quote-ccy delta must raise,
    # never silently drop the leg out of the per-currency table.
    delta_source = schema.OFFICIAL_MARK_SOURCE["DELTA"]
    conn = _mk_conn()
    option_id = "USDJPY111926P-1"
    _insert_instrument(conn, option_id, "USD", "JPY", asset_class="FX_OPTION")
    _insert_trade(conn, "o1", option_id, "FX_OPTION", 1_000_000.0)
    _insert_mark(conn, AS_OF, option_id, AS_OF, "DELTA", 0.4, delta_source)
    # No SPOT mark for USDJPY at all.
    conn.commit()

    with pytest.raises(ValueError, match=option_id):
        delta_per_ccy(conn, AS_OF)


def test_delta_per_ccy_fx_option_non_official_delta_mark_ignored():
    # A DELTA mark whose source is MANUAL only (not QL_OPTIONS_PRICER, the official
    # source) is not official: marks_official excludes it, the option contributes
    # nothing, and this must not raise (there is no official DELTA row to trigger the
    # missing-SPOT check in the first place).
    conn = _mk_conn()
    option_id = "USDJPY111926P-1"
    _insert_instrument(conn, option_id, "USD", "JPY", asset_class="FX_OPTION")
    _insert_trade(conn, "o1", option_id, "FX_OPTION", 1_000_000.0)
    _insert_mark(conn, AS_OF, option_id, AS_OF, "DELTA", 0.4, "MANUAL")
    # No SPOT mark either -- must still not raise, since the DELTA mark isn't official.
    conn.commit()

    delta = delta_per_ccy(conn, AS_OF)
    assert "USD" not in set(delta["ccy"])
    assert "JPY" not in set(delta["ccy"])


# --------------------------------------------------------------------------- ladder_table
# 2026-09-17 ("no bnp fall back"): these four tests run against `real_conn_marks`, which
# loads the live blotter parser's real sample data plus a small synthetic set of
# reconciliation-only SPOT marks (`MARKED_PAIRS`/`MARKED_CCYS` above) -- `ladder_table`'s
# own `source=` parameter is untouched (still a valid, generic reconciliation lookup
# into the raw `marks` table, unrelated to the live app's rate path -- see
# `ladder_table`'s docstring); only the fixture that feeds it changed, since it used to
# derive real BNP_BVAL marks from the retired BNP CSV via data/bloomberg/bnp_marks.py.


@needs_raw
def test_ladder_table_each_ccy_appears_once(real_conn_marks):
    ladder = cash_ladder(real_conn_marks, AS_OF)
    table = ladder_table(real_conn_marks, AS_OF, source=RECON_SOURCE)
    assert set(table["ccy"]) == set(ladder["ccy"])
    assert table["ccy"].is_unique


@needs_raw
def test_ladder_table_total_matches_ladder_sum_per_ccy(real_conn_marks):
    ladder = cash_ladder(real_conn_marks, AS_OF)
    table = ladder_table(real_conn_marks, AS_OF, source=RECON_SOURCE)
    expected_totals = ladder.groupby("ccy")["amount"].sum()
    for _, row in table.iterrows():
        assert math.isclose(row["total"], expected_totals[row["ccy"]], abs_tol=1e-6), row["ccy"]


@needs_raw
def test_ladder_table_usd_nan_exactly_for_ccys_without_bnp_spot(real_conn_marks):
    table = ladder_table(real_conn_marks, AS_OF, source=RECON_SOURCE)
    nan_ccys = set(table.loc[table["usd"].isna(), "ccy"])
    # USD itself always converts at 1.0, never NaN.
    assert "USD" not in nan_ccys
    usd_row = table[table["ccy"] == "USD"]
    assert math.isclose(usd_row["usd"].iloc[0], usd_row["total"].iloc[0])
    # Exactly the currencies with no synthetic mark inserted by the fixture.
    assert nan_ccys == (set(table["ccy"]) - MARKED_CCYS - {"USD"})


@needs_raw
def test_ladder_table_rows_with_usd_come_first_sorted_by_abs_usd_desc(real_conn_marks):
    table = ladder_table(real_conn_marks, AS_OF, source=RECON_SOURCE)
    has_usd = table["usd"].notna()
    n_with = has_usd.sum()
    # all "with usd" rows precede all "without usd" rows
    assert has_usd.iloc[:n_with].all()
    assert not has_usd.iloc[n_with:].any()
    with_usd_abs = table.loc[has_usd, "usd"].abs().tolist()
    assert with_usd_abs == sorted(with_usd_abs, reverse=True)


@needs_raw
def test_ladder_table_has_no_cash_only_currency(real_conn_marks):
    """No currency should appear in `ladder_table` solely because of a `positions` CASH
    row any more -- every ccy present must be traceable to at least one `trade_legs`
    row (the removed CASH column had no such backing)."""
    ladder = cash_ladder(real_conn_marks, AS_OF)
    leg_ccys = {r[0] for r in real_conn_marks.execute(
        "SELECT DISTINCT l.ccy FROM trade_legs l JOIN trades_official t USING (trade_id) "
        "WHERE l.settles_cash = 1 AND l.settle_date >= :as_of AND t.trade_date <= :as_of",
        {"as_of": AS_OF},
    )}
    assert set(ladder["ccy"]) == leg_ccys


# --------------------------------------------------------------------------- synthetic (spot source)
def test_spot_table_source_param_filters_to_one_source():
    conn = _mk_conn()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BNP_BVAL")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 999.0, "MANUAL")
    conn.commit()

    bnp_spot = spot_table(conn, AS_OF, source="BNP_BVAL")
    jpy_row = bnp_spot[bnp_spot["ccy"] == "JPY"]
    assert len(jpy_row) == 1
    assert math.isclose(jpy_row["spot"].iloc[0], 1.0 / 150.0)

    other_spot = spot_table(conn, AS_OF, source="MANUAL")
    jpy_row2 = other_spot[other_spot["ccy"] == "JPY"]
    assert len(jpy_row2) == 1
    assert math.isclose(jpy_row2["spot"].iloc[0], 1.0 / 999.0)

    # source=None still reads marks_official (empty here: neither BNP_BVAL nor MANUAL is
    # the official source for SPOT, which is BBG_BFXFORWARD).
    official_spot = spot_table(conn, AS_OF)
    assert official_spot.empty


def test_ladder_table_columns_and_synthetic_pivot():
    conn = _mk_conn()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", 100.0)
    _insert_trade(conn, "t2", "AUDUSD", "FX_FWD", 50.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "JPY", -15000.0, "2026-08-20")
    _insert_leg(conn, "t2", 1, "FX_NEAR", "AUD", 50.0, "2026-08-25")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    conn.commit()

    table = ladder_table(conn, AS_OF)
    assert list(table.columns) == ["ccy", "2026-08-20", "2026-08-25", "total", "usd"]
    assert set(table["ccy"]) == {"JPY", "AUD"}

    jpy_row = table[table["ccy"] == "JPY"].iloc[0]
    assert math.isclose(jpy_row["2026-08-20"], -15000.0)
    assert math.isclose(jpy_row["2026-08-25"], 0.0)
    assert math.isclose(jpy_row["total"], -15000.0)
    assert math.isclose(jpy_row["usd"], -15000.0 / 150.0)

    aud_row = table[table["ccy"] == "AUD"].iloc[0]
    assert math.isclose(aud_row["2026-08-25"], 50.0)
    assert math.isclose(aud_row["2026-08-20"], 0.0)
    assert math.isnan(aud_row["usd"])  # no SPOT mark for AUD

    # JPY has a defined usd and must come before AUD (no usd).
    assert list(table["ccy"]) == ["JPY", "AUD"]


# --------------------------------------------------------------------------- item 1: dollar convention (per_pair_delta)
# User complaint 2026-09-17: "for aud, eur and gbp - convention adjusted for dollar
# convention - it needs to be done." Interpretation implemented (engine/ladder/ladder.py
# per_pair_delta docstring has the full formulas): AUDUSD/EURUSD/GBPUSD/XAUUSD
# (quote_ccy == 'USD') are quoted the opposite way round from USDJPY-style pairs
# (base_ccy == 'USD'). `notional_base` is CLAUDE.md's own "Display notional" (sign =
# base currency's own direction); `notional_usd` is the "dollar convention" (sign = USD
# direction itself, i.e. the trade's own USD leg amount unchanged) -- the two sign-flip
# relative to each other for quote_ccy == 'USD' pairs and agree for base_ccy == 'USD'
# pairs. `move_1pct_usd` always follows `notional_base`'s sign (a 1% rise in the pair's
# own quote benefits a positive notional_base position for every pair, by construction).

def test_per_pair_delta_usdxxx_pair_no_sign_flip():
    # USDJPY: long USD (base_ccy == 'USD') -- notional_base and notional_usd must agree.
    conn = _mk_conn()
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_trade(conn, "t1", "USDJPY", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "USD", 1_000_000.0, "2026-08-20")
    _insert_leg(conn, "t1", 2, "FX_NEAR", "JPY", -150_000_000.0, "2026-08-20")
    _insert_mark(conn, AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    conn.commit()

    df = per_pair_delta(conn, AS_OF)
    row = df[df["instrument_id"] == "USDJPY"].iloc[0]
    assert bool(row["cross"]) is False and bool(row["commodity"]) is False
    assert math.isclose(row["spot"], 150.0)
    assert math.isclose(row["notional_base"], 1_000_000.0)
    assert math.isclose(row["notional_usd"], 1_000_000.0)  # no flip: base_ccy == USD
    assert math.isclose(row["move_1pct_usd"], 10_000.0)


def test_per_pair_delta_xxxusd_pair_sign_flips():
    # AUDUSD: long AUD (quote_ccy == 'USD') -- notional_base (+, bought AUD) must be the
    # MIRROR sign of notional_usd (-, sold USD to buy that AUD): the "dollar convention"
    # flip the user asked for.
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 1_000_000.0, "2026-08-20")
    _insert_leg(conn, "t1", 2, "FX_NEAR", "USD", -660_000.0, "2026-08-20")
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "SPOT", 0.66, "BBG_BFXFORWARD")
    conn.commit()

    df = per_pair_delta(conn, AS_OF)
    row = df[df["instrument_id"] == "AUDUSD"].iloc[0]
    assert bool(row["cross"]) is False and bool(row["commodity"]) is False
    assert math.isclose(row["spot"], 0.66)  # market quote, never inverted
    assert math.isclose(row["notional_base"], 660_000.0)    # + = bought AUD (Display notional)
    assert math.isclose(row["notional_usd"], -660_000.0)    # - = sold USD (dollar convention)
    # 1% rise in AUDUSD benefits a long-AUD position: must use notional_base's sign, not
    # notional_usd's (which would invert the P&L sign for this pair).
    assert math.isclose(row["move_1pct_usd"], 6_600.0)


def test_per_pair_delta_gbp_and_eur_also_flip_like_aud():
    # Same check for EUR and GBP explicitly, since the user named all three by pair.
    conn = _mk_conn()
    for pair, base, ccy_amt, usd_amt, spot in [
        ("EURUSD", "EUR", 2_000_000.0, -2_200_000.0, 1.10),
        ("GBPUSD", "GBP", 500_000.0, -650_000.0, 1.30),
    ]:
        _insert_instrument(conn, pair, base, "USD")
        _insert_trade(conn, f"t_{pair}", pair, "FX_FWD", ccy_amt)
        _insert_leg(conn, f"t_{pair}", 1, "FX_NEAR", base, ccy_amt, "2026-08-20")
        _insert_leg(conn, f"t_{pair}", 2, "FX_NEAR", "USD", usd_amt, "2026-08-20")
        _insert_mark(conn, AS_OF, pair, AS_OF, "SPOT", spot, "BBG_BFXFORWARD")
    conn.commit()

    df = per_pair_delta(conn, AS_OF).set_index("instrument_id")
    assert math.isclose(df.loc["EURUSD", "notional_base"], 2_200_000.0)
    assert math.isclose(df.loc["EURUSD", "notional_usd"], -2_200_000.0)
    assert math.isclose(df.loc["GBPUSD", "notional_base"], 650_000.0)
    assert math.isclose(df.loc["GBPUSD", "notional_usd"], -650_000.0)


def test_pair_delta_totals_sums_notional_usd_excluding_commodities():
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_instrument(conn, "XAUUSD", "XAU", "USD")
    _insert_trade(conn, "t1", "AUDUSD", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "AUD", 1_000_000.0, "2026-08-20")
    _insert_leg(conn, "t1", 2, "FX_NEAR", "USD", -660_000.0, "2026-08-20")
    _insert_trade(conn, "t2", "USDJPY", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t2", 1, "FX_NEAR", "USD", 1_000_000.0, "2026-08-20")
    _insert_leg(conn, "t2", 2, "FX_NEAR", "JPY", -150_000_000.0, "2026-08-20")
    _insert_trade(conn, "t3", "XAUUSD", "FX_FWD", 500.0)
    _insert_leg(conn, "t3", 1, "FX_NEAR", "XAU", 500.0, "2026-08-20")
    _insert_leg(conn, "t3", 2, "FX_NEAR", "USD", -1_750_000.0, "2026-08-20")
    conn.commit()

    df = per_pair_delta(conn, AS_OF)
    totals = pair_delta_totals(df)
    # AUD -660,000 + USDJPY +1,000,000 = 340,000; XAU's -1,750,000 must be excluded.
    assert totals["pairs"] == 2
    assert math.isclose(totals["net_usd"], -660_000.0 + 1_000_000.0)
    assert math.isclose(totals["gross_usd"], 660_000.0 + 1_000_000.0)


def test_per_pair_delta_empty_when_no_open_pairs():
    conn = _mk_conn()
    df = per_pair_delta(conn, AS_OF)
    assert df.empty
    assert list(df.columns) == ["instrument_id", "base_ccy", "quote_ccy", "cross",
                                "commodity", "spot", "notional_base", "notional_usd",
                                "move_1pct_usd"]
    totals = pair_delta_totals(df)
    assert totals == {"net_usd": 0.0, "gross_usd": 0.0, "pairs": 0}


# --------------------------------------------------------------------------- item 2: XAU (gold)
# User complaint 2026-09-17: "the XAU does not work well." XAUUSD (base_ccy='XAU',
# quote_ccy='USD') must show its own line (ounces, USD notional at spot) but must be
# EXCLUDED from FX Net USD / Gross USD (CLAUDE.md: "Gold and equity futures are reported
# separately"), which previously summed it in like any other currency.

def test_portfolio_totals_excludes_xau_from_fx_net_gross():
    from engine.ladder.exposure import build_exposure, portfolio_totals

    def rec(tid, date, ccy, local):
        return {"trade_id": tid, "settlement_date": date, "book": "HA", "currency": ccy, "local_amount": local}

    def rate(value):
        return {"rate": value, "inverted": False, "source": "TEST", "timestamp": "t", "stale": False}

    recs = [rec("G1", "2026-10-01", "XAU", 500.0), rec("G1", "2026-10-01", "USD", -1_750_000.0),
            rec("A1", "2026-10-01", "AUD", 1_000_000.0)]
    res = build_exposure(recs, {"XAU": rate(3500.0), "AUD": rate(0.66)})

    xau_row = res.summary.set_index("currency").loc["XAU"]
    assert math.isclose(xau_row["local_delta"], 500.0)          # ounces, not USD
    assert math.isclose(xau_row["usd_delta"], 1_750_000.0)      # USD notional at spot

    totals = portfolio_totals(res)
    # AUD only (660,000): XAU's +1,750,000 must not be in net/gross.
    assert math.isclose(totals["net_usd"], 660_000.0)
    assert math.isclose(totals["gross_usd"], 660_000.0)
    assert totals["missing"] == []
    assert len(totals["commodities"]) == 1
    assert totals["commodities"][0]["currency"] == "XAU"
    assert math.isclose(totals["commodities"][0]["usd_delta"], 1_750_000.0)
    assert totals["commodities"][0]["status"] == "OK"


def test_portfolio_totals_missing_xau_rate_does_not_block_fx_totals():
    from engine.ladder.exposure import build_exposure, portfolio_totals

    def rec(tid, date, ccy, local):
        return {"trade_id": tid, "settlement_date": date, "book": "HA", "currency": ccy, "local_amount": local}

    def rate(value):
        return {"rate": value, "inverted": False, "source": "TEST", "timestamp": "t", "stale": False}

    recs = [rec("G1", "2026-10-01", "XAU", 500.0), rec("A1", "2026-10-01", "AUD", 1_000_000.0)]
    res = build_exposure(recs, {"AUD": rate(0.66)})  # no XAU rate at all
    totals = portfolio_totals(res)
    assert math.isclose(totals["net_usd"], 660_000.0)
    assert totals["missing"] == []  # a missing gold rate never blocks the FX-only totals
    assert len(totals["commodities"]) == 1
    assert math.isnan(totals["commodities"][0]["usd_delta"])
    assert totals["commodities"][0]["status"] == "MISSING_RATE"


def test_per_pair_delta_xauusd_flagged_commodity_not_cross():
    conn = _mk_conn()
    _insert_instrument(conn, "XAUUSD", "XAU", "USD")
    _insert_trade(conn, "t1", "XAUUSD", "FX_FWD", 500.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "XAU", 500.0, "2026-08-20")
    _insert_leg(conn, "t1", 2, "FX_NEAR", "USD", -1_750_000.0, "2026-08-20")
    _insert_mark(conn, AS_OF, "XAUUSD", AS_OF, "SPOT", 3500.0, "BBG_BFXFORWARD")
    conn.commit()

    df = per_pair_delta(conn, AS_OF)
    row = df[df["instrument_id"] == "XAUUSD"].iloc[0]
    assert bool(row["commodity"]) is True
    assert bool(row["cross"]) is False  # XAUUSD has a USD leg like any other XXXUSD pair
    assert math.isclose(row["notional_base"], 1_750_000.0)
    # 2026-09-18 ("gold the sign is the wrong one"): a metal is not flipped into the
    # dollar convention -- long gold is + in both columns, not "short USD".
    assert math.isclose(row["notional_usd"], 1_750_000.0)
    assert math.isclose(row["move_1pct_usd"], 17_500.0)
    # ...while an ordinary XXXUSD pair in the same book still flips.
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_trade(conn, "t2", "AUDUSD", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t2", 1, "FX_NEAR", "AUD", 1_000_000.0, "2026-08-20")
    _insert_leg(conn, "t2", 2, "FX_NEAR", "USD", -650_000.0, "2026-08-20")
    conn.commit()
    df = per_pair_delta(conn, AS_OF).set_index("instrument_id")
    assert math.isclose(df.loc["AUDUSD", "notional_usd"], -650_000.0)
    assert math.isclose(df.loc["XAUUSD", "notional_usd"], 1_750_000.0)


def test_ui_exposure_combined_risk_frame_tags_xau_as_commodity_kind():
    from engine.ladder.exposure import build_exposure
    from ui.tabs.exposure import combined_risk_frame

    def rec(tid, date, ccy, local):
        return {"trade_id": tid, "settlement_date": date, "book": "HA", "currency": ccy, "local_amount": local}

    def rate(value):
        return {"rate": value, "inverted": False, "source": "TEST", "timestamp": "t", "stale": False}

    recs = [rec("G1", "2026-10-01", "XAU", 500.0), rec("A1", "2026-10-01", "AUD", 1_000_000.0)]
    res = build_exposure(recs, {"XAU": rate(3500.0), "AUD": rate(0.66)})
    frame = combined_risk_frame(res, scenarios={})
    xau_row = frame[frame["name"] == "XAU"].iloc[0]
    aud_row = frame[frame["name"] == "AUD"].iloc[0]
    assert xau_row["kind"] == "commodity"
    assert aud_row["kind"] == "currency"


# --------------------------------------------------------------------------- item 3: EURSEK split
# User complaint 2026-09-17: "the eursek has not been split well - needs to be broken
# down into sek and eur." Audited engine/ladder/ladder.py (_DELTA_SQL, cash_ladder),
# engine/ladder/exposure_adapter.py (records_from_db) and engine/ladder/exposure.py
# (build_exposure): all three already key off trade_legs.ccy directly, so a EURSEK
# forward's two legs were already landing as independent 'EUR' and 'SEK' rows -- these
# tests pin that behaviour end-to-end (DB -> delta_per_ccy / exposure_adapter) so it
# cannot silently regress, and confirm the two currencies convert to USD off their OWN
# pair spots (EURUSD, USDSEK), never a EURSEK spot (which converts EUR<->SEK, not to
# USD, and must never be required for either currency to appear).

def test_eursek_splits_into_eur_and_sek_never_one_pair_line():
    from engine.ladder.exposure_adapter import exposure_records_from_db

    conn = _mk_conn()
    _insert_instrument(conn, "EURSEK", "EUR", "SEK")
    _insert_trade(conn, "t1", "EURSEK", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "EUR", 1_000_000.0, "2026-09-25")
    _insert_leg(conn, "t1", 2, "FX_NEAR", "SEK", -11_000_000.0, "2026-09-25")
    conn.commit()

    # 1. The per-currency delta table: never a single 'EURSEK' line, never dropped for
    #    lack of a EURSEK->USD conversion (delta itself needs no spot at all).
    delta = delta_per_ccy(conn, AS_OF)
    assert "EURSEK" not in set(delta["ccy"])
    eur_row = delta[delta["ccy"] == "EUR"]
    sek_row = delta[delta["ccy"] == "SEK"]
    assert len(eur_row) == 1 and len(sek_row) == 1
    assert math.isclose(eur_row["delta"].iloc[0], 1_000_000.0)
    assert math.isclose(sek_row["delta"].iloc[0], -11_000_000.0)
    # No EURUSD/USDSEK marks yet: both delta_usd are NaN (never estimated from a EURSEK
    # cross rate), not silently dropped rows.
    assert math.isnan(eur_row["delta_usd"].iloc[0])
    assert math.isnan(sek_row["delta_usd"].iloc[0])

    # 2. The exposure_adapter record set (feeds the UI grid and build_exposure): two
    #    leg records, currencies EUR and SEK, never attributed only to EUR.
    records, unresolved = exposure_records_from_db(conn, AS_OF)
    assert unresolved == []
    t1_ccys = {r["currency"] for r in records if r["trade_id"] == "t1"}
    assert t1_ccys == {"EUR", "SEK"}

    # 3. Each currency's OWN pair spot (EURUSD, USDSEK) -- not a EURSEK spot -- lets
    #    delta_per_ccy convert them independently and correctly.
    _insert_instrument(conn, "EURUSD", "EUR", "USD")
    _insert_instrument(conn, "USDSEK", "USD", "SEK")
    _insert_mark(conn, AS_OF, "EURUSD", AS_OF, "SPOT", 1.10, "BBG_BFXFORWARD")
    _insert_mark(conn, AS_OF, "USDSEK", AS_OF, "SPOT", 10.50, "BBG_BFXFORWARD")
    conn.commit()
    delta2 = delta_per_ccy(conn, AS_OF)
    eur_row2 = delta2[delta2["ccy"] == "EUR"]
    sek_row2 = delta2[delta2["ccy"] == "SEK"]
    assert math.isclose(eur_row2["delta_usd"].iloc[0], 1_000_000.0 * 1.10)
    assert math.isclose(sek_row2["delta_usd"].iloc[0], -11_000_000.0 / 10.50)


def test_eursek_cash_ladder_rows_are_per_currency_not_per_pair():
    conn = _mk_conn()
    _insert_instrument(conn, "EURSEK", "EUR", "SEK")
    _insert_trade(conn, "t1", "EURSEK", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "t1", 1, "FX_NEAR", "EUR", 1_000_000.0, "2026-09-25")
    _insert_leg(conn, "t1", 2, "FX_NEAR", "SEK", -11_000_000.0, "2026-09-25")
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    assert "EURSEK" not in set(ladder["ccy"])
    assert {"EUR", "SEK"} <= set(ladder["ccy"])
    eur_leg = ladder[(ladder["ccy"] == "EUR") & (ladder["kind"] == "LEG")]
    sek_leg = ladder[(ladder["ccy"] == "SEK") & (ladder["kind"] == "LEG")]
    assert math.isclose(eur_leg["amount"].iloc[0], 1_000_000.0)
    assert math.isclose(sek_leg["amount"].iloc[0], -11_000_000.0)


def test_eursek_build_exposure_both_legs_priced_independently():
    """engine.ladder.exposure.build_exposure level (already covered for EURSEK in
    tests/test_exposure.py::test_cross_currency_trade_no_usd_leg_priced_at_spot); pinned
    here too since it is the function ui/tabs/exposure.py actually renders from."""
    from engine.ladder.exposure import build_exposure

    recs = [
        {"trade_id": "X1", "settlement_date": "2026-09-25", "book": "HA", "currency": "EUR", "local_amount": 1_000_000.0},
        {"trade_id": "X1", "settlement_date": "2026-09-25", "book": "HA", "currency": "SEK", "local_amount": -11_000_000.0},
    ]
    rates = {
        "EUR": {"rate": 1.10, "inverted": False, "source": "TEST", "timestamp": "t", "stale": False},
        "SEK": {"rate": 10.50, "inverted": True, "source": "TEST", "timestamp": "t", "stale": False},
    }
    res = build_exposure(recs, rates)
    assert set(res.summary["currency"]) == {"EUR", "SEK"}
    assert "EURSEK" not in set(res.summary["currency"])
    eur = res.summary.set_index("currency").loc["EUR"]
    sek = res.summary.set_index("currency").loc["SEK"]
    assert math.isclose(eur["usd_delta"], 1_100_000.0)
    assert math.isclose(sek["usd_delta"], -11_000_000.0 / 10.50)


# --------------------------------------------------------------------------- ui/tabs/exposure.py: Position table
def test_pair_position_frame_labels_cross_and_commodity_and_keeps_spot_precision():
    from ui.tabs.exposure import pair_position_frame

    df = pd.DataFrame([
        {"instrument_id": "AUDUSD", "base_ccy": "AUD", "quote_ccy": "USD", "cross": False,
         "commodity": False, "spot": 0.66, "notional_base": 660_000.0, "notional_usd": -660_000.0,
         "move_1pct_usd": 6_600.0},
        {"instrument_id": "EURSEK", "base_ccy": "EUR", "quote_ccy": "SEK", "cross": True,
         "commodity": False, "spot": 11.05, "notional_base": 1_100_000.0, "notional_usd": 1_100_000.0,
         "move_1pct_usd": 11_000.0},
        {"instrument_id": "XAUUSD", "base_ccy": "XAU", "quote_ccy": "USD", "cross": False,
         "commodity": True, "spot": float("nan"), "notional_base": 1_750_000.0,
         "notional_usd": -1_750_000.0, "move_1pct_usd": 17_500.0},
    ])
    frame = pair_position_frame(df)
    labels = dict(zip(df["instrument_id"], frame["pair"]))
    assert labels["AUDUSD"] == "AUDUSD"
    assert labels["EURSEK"] == "EURSEK (cross)"
    assert labels["XAUUSD"] == "XAUUSD (metal)"
    # spot kept at 6 dp, never thousands-rounded to an integer (0.66 -> "0" would lose
    # all information for a sub-1.0 quote).
    aud_spot = frame.loc[frame["pair"] == "AUDUSD", "spot"].iloc[0]
    assert aud_spot == "0.660000"
    xau_spot = frame.loc[frame["pair"] == "XAUUSD (metal)", "spot"].iloc[0]
    assert xau_spot == ""  # NaN spot renders blank, never a fabricated rate


def test_pair_position_frame_empty_and_none_render_placeholder_not_crash():
    from ui.tabs.exposure import pair_position_frame, pair_position_table

    assert pair_position_frame(pd.DataFrame()).empty
    div_none = pair_position_table(None)
    div_empty = pair_position_table(pd.DataFrame())
    assert "Position (per pair, dollar convention)" in str(div_none)
    assert "No open FX forward/spot/swap pairs" in str(div_none)
    assert "No open FX forward/spot/swap pairs" in str(div_empty)


def test_pair_position_table_renders_both_notional_columns_labelled():
    from ui.tabs.exposure import pair_position_table

    df = pd.DataFrame([
        {"instrument_id": "AUDUSD", "base_ccy": "AUD", "quote_ccy": "USD", "cross": False,
         "commodity": False, "spot": 0.66, "notional_base": 660_000.0, "notional_usd": -660_000.0,
         "move_1pct_usd": 6_600.0},
    ])
    section = pair_position_table(df)
    names = [c["name"] for c in section.children[1].columns]
    assert "Notional (base ccy)" in names
    assert "USD notional (USD sign: + long USD)" in names
    row = section.children[1].data[0]
    assert row["notional_base"] == "660,000"
    assert row["notional_usd"] == "(660,000)"


def test_net_gross_usd_passes_through_commodities(tmp_path):
    from data.ingest import schema as ingest_schema
    from ui.tabs.cash_ladder import net_gross_usd

    conn = ingest_schema.connect(str(tmp_path / "risk.db"))
    _insert_instrument(conn, "AUDUSD", "AUD", "USD")
    _insert_instrument(conn, "XAUUSD", "XAU", "USD")
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("g1", "XLSX", "XAUUSD", "FX_FWD", "g1", AS_OF, 500.0, 3500.0,
         "A", "C", "S", "T", "synthetic", ""),
    )
    _insert_leg(conn, "g1", 1, "FX_NEAR", "XAU", 500.0, "2026-08-20")
    _insert_leg(conn, "g1", 2, "FX_NEAR", "USD", -1_750_000.0, "2026-08-20")
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("a1", "XLSX", "AUDUSD", "FX_FWD", "a1", AS_OF, 1_000_000.0, 0.66,
         "A", "C", "S", "T", "synthetic", ""),
    )
    _insert_leg(conn, "a1", 1, "FX_NEAR", "AUD", 1_000_000.0, "2026-08-20")
    _insert_leg(conn, "a1", 2, "FX_NEAR", "USD", -660_000.0, "2026-08-20")
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "SPOT", 0.66, "BBG_BFXFORWARD")
    _insert_mark(conn, AS_OF, "XAUUSD", AS_OF, "SPOT", 3500.0, "BBG_BFXFORWARD")
    conn.commit()

    result = net_gross_usd(conn, AS_OF)
    assert result["available"] is True
    assert math.isclose(result["net"], 660_000.0)  # AUD only; XAU excluded
    assert len(result["commodities"]) == 1
    assert result["commodities"][0]["currency"] == "XAU"
    assert math.isclose(result["commodities"][0]["usd_delta"], 1_750_000.0)
