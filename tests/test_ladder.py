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

from data.ingest.common import NDF_CCYS  # the user's NDF list (INR joined it 2026-09-21)

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

    # NDF leg FIXING on as_of (2026-09-21: NDFs are dated on fixing dates, value date
    # less 2 business days -- here value date Wed 2026-08-19, fixing Mon 2026-08-17 =
    # as_of): on the grid under its fixing date; by close it is handled like settled cash
    # (user decision 2026-09-21), so the exposure records carry both legs in Settled at
    # face value and the KRW amount is still KRW delta until the value date.
    ndf = _mk_conn()
    ndf.execute("INSERT INTO instruments VALUES ('USDKRW','FX','USD','KRW',1,1,'USDKRW Curncy','9999-12-31')")
    _insert_trade(ndf, "n1", "USDKRW", "FX_FWD", 100.0)
    ndf.execute("INSERT INTO trade_legs VALUES ('n1',1,'FX_NEAR','USD',100.0,'2026-08-01','2026-08-19',1400.0,0)")
    ndf.execute("INSERT INTO trade_legs VALUES ('n1',2,'FX_NEAR','KRW',-140000.0,'2026-08-01','2026-08-19',1400.0,0)")
    ndf.execute("UPDATE trades SET price = 1400.0 WHERE trade_id = 'n1'")  # a fill the rate check accepts
    ndf.commit()
    ndf_grid, ndf_named = records_from_db(ndf, AS_OF)
    assert any(r["currency"] == "KRW" and r["settlement_date"] == AS_OF and r["value_date"] == "2026-08-19"
               for r in ndf_grid)
    assert ndf_named == []
    assert not any(r["settlement_date"] == SETTLED for r in ndf_grid)
    ndf_exposure, _ = exposure_records_from_db(ndf, AS_OF)
    assert {(r["currency"], r["settlement_date"], r["local_amount"], r["settled_on"], r["value_date"])
            for r in ndf_exposure} == {("USD", SETTLED, 100.0, AS_OF, "2026-08-19"),
                                       ("KRW", SETTLED, -140000.0, AS_OF, "2026-08-19")}
    krw = {"KRW": {"rate": 1400.0, "inverted": True, "source": "TEST", "timestamp": "", "stale": False}}
    totals = portfolio_totals(build_exposure(ndf_exposure, krw))
    assert math.isclose(totals["net_usd"], -100.0) and math.isclose(totals["gross_usd"], 100.0)
    assert totals["missing"] == []

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



# --------------------------------------------------------------------------- NDFs (user decisions 2026-09-21)
# "NDFs, show fixing dates instead" and "always show 1m forward date price, not spot":
# engine/ladder/ndf.py, and what exposure_adapter / usd_marks / exposure do with it.
@pytest.fixture
def weekdays_only(monkeypatch, tmp_path):
    """Monday-to-Friday calendar, so the dates below do not move with config/holidays.txt."""
    import engine.pnl.calendar as cal
    monkeypatch.setattr(cal, "_DEFAULT_HOLIDAYS_PATH", tmp_path / "no-holidays.txt")


def test_ndf_fixing_date_is_value_date_less_two_business_days():
    from engine.ladder.ndf import FIXING_CAPTION, fixing_date

    none = frozenset()
    assert fixing_date("2026-09-18", none) == "2026-09-16"   # Fri -> Wed
    assert fixing_date("2026-09-21", none) == "2026-09-17"   # Mon -> Thu, over the weekend
    assert fixing_date("2026-09-22", none) == "2026-09-18"   # Tue -> Fri
    # the app's only calendar: a holiday is not a business day (Labor Day 2026-09-07)
    assert fixing_date("2026-09-08", {"2026-09-07"}) == "2026-09-03"
    assert fixing_date("2026-09-08", none) == "2026-09-04"
    # without an override it reads config/holidays.txt through engine/pnl/calendar.py
    from engine.pnl.calendar import load_holidays
    assert fixing_date("2026-09-08") == fixing_date("2026-09-08", load_holidays())
    # never guessed at: the settled-cash sentinel or a bad cell comes back unchanged
    assert fixing_date("settled", none) == "settled"
    assert "value date less 2 business days" in FIXING_CAPTION


def test_ndf_ticket_is_decided_by_currency_not_only_by_the_stored_flag():
    from engine.ladder.ndf import currency_label, is_ndf_pair

    for pair in ("USDKRW", "USDIDR", "USDINR", "USDTWD", "USDBRL"):
        assert is_ndf_pair(pair, 0)            # INR joined 2026-09-21: an old row still says is_ndf = 0
    assert is_ndf_pair("EURBRL", 0)
    assert not is_ndf_pair("USDJPY", 0) and not is_ndf_pair("USDTRY", 0) and not is_ndf_pair("USDMXN", 0)
    assert is_ndf_pair("USDJPY", 1)            # the stored flag still counts
    assert not is_ndf_pair("ESU6 Index", 0) and not is_ndf_pair("", 0)
    assert currency_label("KRW") == "KRW (NDF)" and currency_label("JPY") == "JPY"


def _ndf_book(is_ndf=1, settles_cash=0, pair="USDKRW", ccy="KRW"):
    """One NDF (value date Mon 2026-09-21 -> fixing Thu 2026-09-17) and one deliverable
    USDJPY forward with the same value date."""
    conn = _mk_conn()
    conn.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 (pair, "FX", "USD", ccy, 1.0, is_ndf, f"{pair} Curncy", "9999-12-31"))
    _insert_instrument(conn, "USDJPY", "USD", "JPY")
    _insert_trade(conn, "n1", pair, "FX_FWD", -1_000_000.0)
    _insert_leg(conn, "n1", 1, "FX_NEAR", "USD", -1_000_000.0, "2026-09-21", settles_cash, rate=1400.0)
    _insert_leg(conn, "n1", 2, "FX_NEAR", ccy, 1_400_000_000.0, "2026-09-21", settles_cash, rate=1400.0)
    _insert_trade(conn, "j1", "USDJPY", "FX_FWD", 1_000_000.0)
    _insert_leg(conn, "j1", 1, "FX_NEAR", "USD", 1_000_000.0, "2026-09-21", rate=150.0)
    _insert_leg(conn, "j1", 2, "FX_NEAR", "JPY", -150_000_000.0, "2026-09-21", rate=150.0)
    conn.commit()
    return conn


def _realise(conn, trade_id, pair, ccy, settle_date, pnl_usd):
    conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount,"
                 " usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd,"
                 " frozen_at, note) VALUES (?,?,'FX_FWD',?,?,0.0,0.0,'SPOT',0.000714,?,'BBG_BFXFORWARD',?,"
                 " '2026-09-22T00:00:00+00:00','')", (trade_id, pair, ccy, settle_date, settle_date, pnl_usd))
    conn.commit()


def test_ndf_records_sit_under_their_fixing_date_and_the_rules_read_it(weekdays_only):
    from engine.ladder.exposure_adapter import SETTLED, exposure_records_from_db, records_from_db

    conn = _ndf_book()
    # before the fixing: on the grid and in the delta, under the FIXING date
    for fn in (records_from_db, exposure_records_from_db):
        records, unresolved = fn(conn, "2026-09-16")
        assert unresolved == []
        ndf = [r for r in records if r["trade_id"] == "n1"]
        assert {r["currency"] for r in ndf} == {"USD", "KRW"}      # every record of the ticket, USD leg too
        assert all((r["settlement_date"], r["fixing_date"], r["value_date"]) ==
                   ("2026-09-17", "2026-09-17", "2026-09-21") for r in ndf)
        assert all(r["is_ndf"] == 1 and r["settles_cash"] == 0 for r in ndf)
        # a deliverable ticket is untouched: its own value date, no fixing date
        jpy = [r for r in records if r["trade_id"] == "j1"]
        assert all((r["settlement_date"], r["fixing_date"], r["value_date"]) ==
                   ("2026-09-21", "", "2026-09-21") for r in jpy)

    # on the fixing date: the grid still shows it under that date (fixing >= as_of); by
    # close it is settled cash, so the delta records carry it in Settled (fixing <= as_of)
    fixed = [("KRW", 1_400_000_000.0, SETTLED, "2026-09-17", "2026-09-21", 1, 0),
             ("USD", -1_000_000.0, SETTLED, "2026-09-17", "2026-09-21", 1, 0)]

    def ticket(records):
        return sorted((r["currency"], r["local_amount"], r["settlement_date"], r["settled_on"], r["value_date"],
                       r["is_ndf"], r["settles_cash"]) for r in records if r["trade_id"] == "n1")

    grid, named = records_from_db(conn, "2026-09-17")
    assert {r["settlement_date"] for r in grid if r["trade_id"] == "n1"} == {"2026-09-17"} and named == []
    exposure, named = exposure_records_from_db(conn, "2026-09-17")
    assert ticket(exposure) == fixed and named == []

    # fixed, value date not passed (user decision 2026-09-21: "handled like settled cash,
    # where the Local amnt stays in 'settled'"): both legs at face value in Settled cash,
    # on the grid and in the delta, each counted once, nothing named -- up to and including
    # the value date itself (the ledger realises the day after)
    for day in ("2026-09-18", "2026-09-21"):
        for fn in (records_from_db, exposure_records_from_db):
            records, named = fn(conn, day)
            assert ticket(records) == fixed and named == []

    # value date passed, no realised row: named once, by the existing wording
    records, named = records_from_db(conn, "2026-09-22")
    assert [u.trade_id for u in named] == ["n1"]
    assert named[0].reason.startswith("settled 2026-09-21 (FX_FWD), USD settlement unknown")
    assert not any(r["currency"] == "KRW" for r in records)

    # realised: the USD settlement is READ from realised_pnl into Settled cash, dated on
    # the value date the cash arrives; nothing KRW-denominated, nothing named
    _realise(conn, "n1", "USDKRW", "KRW", "2026-09-21", 4_321.0)
    for fn in (records_from_db, exposure_records_from_db):
        records, named = fn(conn, "2026-09-22")
        assert named == []
        ndf = [r for r in records if r["trade_id"] == "n1"]
        assert [(r["currency"], r["local_amount"], r["settlement_date"], r["settled_on"], r["is_ndf"])
                for r in ndf] == [("USD", 4_321.0, SETTLED, "2026-09-21", 1)]


def test_usdinr_row_stored_as_deliverable_is_still_treated_as_the_ndf_it_is(weekdays_only):
    """INR joined NDF_CCYS on 2026-09-21: a database loaded before that carries
    is_ndf = 0 / settles_cash = 1 on USDINR until the next upload."""
    from engine.ladder.exposure_adapter import SETTLED, exposure_records_from_db, records_from_db

    conn = _ndf_book(is_ndf=0, settles_cash=1, pair="USDINR", ccy="INR")
    records, named = records_from_db(conn, "2026-09-16")
    inr = [r for r in records if r["trade_id"] == "n1"]
    assert named == [] and len(inr) == 2
    assert all(r["settlement_date"] == "2026-09-17" and r["is_ndf"] == 1 and r["settles_cash"] == 0 for r in inr)

    # after the value date no INR is "delivered" into Settled cash in either mode; the
    # ticket is named until the ledger realises it, then settles in USD
    for fn in (records_from_db, exposure_records_from_db):
        records, named = fn(conn, "2026-09-23")
        assert not any(r["trade_id"] == "n1" for r in records)
        assert [u.trade_id for u in named] == ["n1"] and named[0].reason.startswith("settled 2026-09-21")
    # the deliverable JPY forward next to it still settles as its legs
    assert {(r["currency"], r["settlement_date"]) for r in records} == {("USD", SETTLED), ("JPY", SETTLED)}
    _realise(conn, "n1", "USDINR", "INR", "2026-09-21", -250.0)
    records, named = records_from_db(conn, "2026-09-23")
    assert named == []
    assert [(r["currency"], r["local_amount"]) for r in records if r["trade_id"] == "n1"] == [("USD", -250.0)]


def test_ndf_legs_sharing_one_fixing_date_become_one_record(weekdays_only):
    """Value dates Sat 2026-09-19 and Mon 2026-09-21 both fix on Thu 2026-09-17: one
    record per currency, or build_exposure's (trade, currency, date) key would clash."""
    from engine.ladder.exposure import build_exposure
    from engine.ladder.exposure_adapter import records_from_db

    conn = _mk_conn()
    _insert_instrument(conn, "USDKRW", "USD", "KRW")
    _insert_trade(conn, "s1", "USDKRW", "FX_SWAP", 1_000_000.0)
    _insert_leg(conn, "s1", 1, "FX_NEAR", "KRW", -1_400_000_000.0, "2026-09-19", 0)
    _insert_leg(conn, "s1", 2, "FX_FAR", "KRW", 1_401_000_000.0, "2026-09-21", 0)
    conn.commit()
    records, _ = records_from_db(conn, "2026-09-16")
    assert [(r["currency"], r["settlement_date"], r["local_amount"]) for r in records] == \
        [("KRW", "2026-09-17", 1_000_000.0)]
    assert build_exposure(records, {}).ladder.loc["2026-09-17", "KRW"] == 1_000_000.0


def _ndf_marks_conn():
    conn = _mk_conn()
    for pair, quote in (("USDKRW", "KRW"), ("USDBRL", "BRL"), ("USDJPY", "JPY")):
        _insert_instrument(conn, pair, "USD", quote)
    for pair, value in (("USDKRW", 1380.0), ("USDBRL", 5.4), ("USDJPY", 150.0)):
        _insert_mark(conn, AS_OF, pair, AS_OF, "SPOT", value, "BBG_BFXFORWARD")
    # NDF_1M as the Bloomberg pull writes it: on the USD pair, settle_date = as_of_date,
    # value as quoted, official source. Latest as_of_date, then latest snapped_at, wins.
    _insert_mark(conn, "2026-08-14", "USDKRW", "2026-08-14", "NDF_1M", 1390.0, "BBG_BFXFORWARD",
                 snapped_at="2026-08-14T17:00:00-04:00")
    _insert_mark(conn, AS_OF, "USDKRW", AS_OF, "NDF_1M", 1394.5, "BBG_BFXFORWARD")
    # never official: a hand-typed 1M price for BRL does not make BRL priced
    _insert_mark(conn, AS_OF, "USDBRL", AS_OF, "NDF_1M", 5.45, "MANUAL")
    conn.commit()
    return conn


def test_ndf_currencies_are_priced_at_the_1m_ndf_mark_never_spot():
    import datetime as dt
    from data.bloomberg.live import rates_from_marks
    from engine.ladder.exposure import RATE_FIELDS, build_exposure, portfolio_totals
    from engine.ladder.ndf import apply_ndf_1m_rates, missing_rate_reason

    conn = _ndf_marks_conn()
    now = dt.datetime(2026, 8, 17, 19, 1, tzinfo=dt.timezone.utc)   # one minute after the snap
    spot = rates_from_marks(conn, now=now)
    assert spot["KRW"]["rate"] == 1380.0 and "BRL" in spot           # what the tab used before
    rates = apply_ndf_1m_rates(conn, spot, now=now)

    krw = rates["KRW"]
    assert krw["rate"] == 1394.5 and krw["inverted"] is True and krw["pair"] == "USDKRW"
    assert (krw["mark_type"], krw["ticker"], krw["label"]) == ("NDF_1M", "KWN+1M Curncy", "KWN+1M")
    assert krw["source"] == "BBG_BFXFORWARD" and krw["as_of_date"] == AS_OF and krw["stale"] is False
    assert all(f in krw for f in RATE_FIELDS)                        # the shape build_exposure validates
    assert apply_ndf_1m_rates(conn, spot, now=now + dt.timedelta(days=3))["KRW"]["stale"] is True
    # no official NDF_1M mark: NO rate, although an official SPOT is on file
    assert "BRL" not in rates
    # every other currency passes through, and the caller's dict is not touched
    assert rates["JPY"] == spot["JPY"] and spot["KRW"]["rate"] == 1380.0

    records = [
        {"trade_id": "k", "settlement_date": "2026-09-17", "book": "B", "currency": "KRW", "local_amount": 1_394_500.0},
        {"trade_id": "b", "settlement_date": "2026-09-17", "book": "B", "currency": "BRL", "local_amount": 5_400.0},
        {"trade_id": "j", "settlement_date": "2026-09-21", "book": "B", "currency": "JPY", "local_amount": 150_000.0},
    ]
    result = build_exposure(records, rates)
    summary = result.summary.set_index("currency")
    assert math.isclose(summary.loc["KRW", "usd_delta"], 1_000.0)    # 1,394,500 / 1,394.5, not / 1,380
    assert summary.loc["BRL", "status"] == "MISSING_RATE" and math.isnan(summary.loc["BRL", "usd_delta"])
    message = result.status.set_index("currency").loc["BRL", "message"]
    assert "1M NDF price for BRL (BCN+1M) is missing" in message and '"Pull Bloomberg now"' in message
    assert "SPOT" not in message
    totals = portfolio_totals(result)
    assert totals["missing"] == ["BRL"] and math.isnan(totals["net_usd"])
    # the sentence for the header / headline: NDF and spot currencies named apart
    assert missing_rate_reason(["BRL"], AS_OF) == (
        'the 1M NDF price is missing for BRL (BCN+1M): press "Pull Bloomberg now" to fetch it '
        "(NDF currencies are never valued at spot)")
    assert missing_rate_reason(["BRL", "NOK"], AS_OF).startswith(f"no official SPOT for {AS_OF}: NOK; the 1M NDF")
    assert missing_rate_reason([]) == ""


def test_a_mis_scaled_1m_ndf_mark_is_named_as_such_not_as_a_spot():
    from engine.ladder.exposure import build_exposure

    records = [{"trade_id": "k1", "settlement_date": "2026-09-17", "book": "HA", "currency": "KRW",
                "local_amount": 1_413_138_000.0, "currency_pair": "USDKRW", "entry_rate": 1413.138,
                "product_type": "FX_FWD"}]
    rates = {"KRW": {"rate": 1.3945, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "t",
                     "stale": False, "pair": "USDKRW", "mark_type": "NDF_1M", "label": "KWN+1M"}}
    result = build_exposure(records, rates)
    assert result.summary.set_index("currency").loc["KRW", "status"] == "SUSPECT_RATE"
    assert result.status.set_index("currency").loc["KRW", "message"].startswith(
        "official NDF 1M KWN+1M 1.3945 is 1,013x away")


def test_usd_equivalent_of_an_ndf_currency_is_the_1m_ndf_rate_on_open_dates_and_spot_for_settled_cash():
    from data.bloomberg.live import rates_from_marks
    from engine.ladder.exposure import build_exposure, ladder_usd_cells
    from engine.ladder.exposure_adapter import SETTLED
    from engine.ladder.ndf import apply_ndf_1m_rates
    from engine.ladder.usd_marks import BASIS_NDF_1M, forward_usd_rates

    conn = _ndf_marks_conn()
    # forward pillars on file for both pairs: JPY uses its own, KRW must ignore them
    _insert_mark(conn, AS_OF, "USDKRW", "2026-11-19", "FWD_OUTRIGHT", 1401.0, "BBG_BFXFORWARD")
    _insert_mark(conn, AS_OF, "USDJPY", "2026-11-19", "FWD_OUTRIGHT", 148.0, "BBG_BFXFORWARD")
    conn.commit()
    rates = apply_ndf_1m_rates(conn, rates_from_marks(conn))
    days = [AS_OF, "2026-09-17", "2026-11-19", "2027-03-01", SETTLED]
    marks = forward_usd_rates(conn, rates, [(c, d) for c in ("KRW", "BRL", "JPY") for d in days])

    for day in days:
        entry = marks[("KRW", day)]
        if day in (AS_OF, SETTLED):
            # a fixed NDF ticket's amount (user decision 2026-09-21: "handled like settled
            # cash ... priced using spot rate"): the official spot, never the 1M price
            assert math.isclose(entry["rate"], 1 / 1380.0) and entry["quoted"] == 1380.0
            assert (entry["basis"], entry["pair"]) == ("spot", "USDKRW")
        else:
            assert math.isclose(entry["rate"], 1 / 1394.5) and entry["quoted"] == 1394.5
            assert (entry["basis"], entry["pair"], entry["ticker"]) == (BASIS_NDF_1M, "USDKRW", "KWN+1M Curncy")
        assert ("BRL", day) not in marks                  # 1M price missing: blank, never spot
    # no spot on file: the settled cell is blank, the open dates keep the 1M price
    no_spot = {"KRW": {k: v for k, v in rates["KRW"].items() if k != "spot_rate"}}
    marks_no_spot = forward_usd_rates(conn, no_spot, [("KRW", SETTLED), ("KRW", "2026-11-19")])
    assert ("KRW", SETTLED) not in marks_no_spot and marks_no_spot[("KRW", "2026-11-19")]["basis"] == BASIS_NDF_1M
    assert BASIS_NDF_1M == "NDF 1M"
    assert marks[("JPY", "2026-11-19")]["basis"] == "outright" and marks[("JPY", AS_OF)]["basis"] == "spot"

    records = [
        {"trade_id": "k", "settlement_date": "2026-11-19", "book": "B", "currency": "KRW", "local_amount": 1_394_500.0},
        {"trade_id": "b", "settlement_date": "2026-11-19", "book": "B", "currency": "BRL", "local_amount": 5_400.0},
    ]
    cells = ladder_usd_cells(build_exposure(records, rates), marks)
    assert math.isclose(cells.loc["2026-11-19", "KRW"], 1_000.0)
    assert math.isnan(cells.loc["2026-11-19", "BRL"])
