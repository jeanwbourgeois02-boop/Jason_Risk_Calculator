"""Tests for engine/ladder (cash ladder + delta-per-currency). Real-file tests skip if
the raw BNP CSV is absent."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.bloomberg.bnp_marks import load_bnp_marks
from data.ingest import bnp, schema
from engine.ladder import cash_ladder, delta_per_ccy, spot_table, convert_to_usd, ladder_table

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "HA_PNL_20260818.csv"
AS_OF = "2026-08-17"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")

NDF_CCYS = {"BRL", "TWD", "KRW", "IDR"}


@pytest.fixture(scope="module")
def real_conn():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def real_conn_with_marks():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=False)
    yield conn
    conn.close()


# Currencies whose only forwards in the real file are XXXUSD pairs (AUD, EUR, GBP) or
# the metal (XAU): BNP_BVAL's Fx column is quote_ccy->USD, which is always 1.0 on an
# XXXUSD row, so no SPOT is derivable for these ccys from the BNP file at all (see
# data/bloomberg/bnp_marks.py::extract_bnp_marks SPOT-derivation docstring). Verified
# empirically against the real file, not assumed.
NO_BNP_SPOT_CCYS = {"AUD", "EUR", "GBP", "XAU"}


# --------------------------------------------------------------------------- real file
@needs_raw
def test_ladder_ccys_come_from_legs_or_cash_positions(real_conn):
    ladder = cash_ladder(real_conn, AS_OF)
    leg_ccys = {r[0] for r in real_conn.execute("SELECT DISTINCT ccy FROM trade_legs")}
    cash_ccys = {r[0][len("CASH-"):] for r in
                 real_conn.execute("SELECT DISTINCT instrument_id FROM positions WHERE instrument_id LIKE 'CASH-%'")}
    allowed = leg_ccys | cash_ccys
    assert set(ladder["ccy"]) <= allowed


@needs_raw
def test_ladder_usd_leg_amount_matches_trade_legs(real_conn):
    ladder = cash_ladder(real_conn, AS_OF)
    ladder_usd_legs = ladder[(ladder["ccy"] == "USD") & (ladder["kind"] == "LEG")]["amount"].sum()
    expected = real_conn.execute(
        "SELECT SUM(amount) FROM trade_legs WHERE ccy = 'USD' AND settles_cash = 1 AND settle_date >= :as_of",
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


def test_cash_rows_grouped_by_ccy_settle_date_and_filtered_by_source():
    conn = _mk_conn()
    _insert_instrument(conn, "CASH-USD", "USD", "USD", asset_class="CASH")
    _insert_instrument(conn, "CASH-EUR", "EUR", "EUR", asset_class="CASH")

    def _insert_position(as_of, source, account, instrument_id, settle_date, quantity):
        conn.execute(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (as_of, source, account, instrument_id, settle_date, quantity,
             0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        )

    # Multiple BNP accounts for USD on as_of: should sum to one row.
    _insert_position(AS_OF, "BNP", "ACC1", "CASH-USD", AS_OF, 1_000_000.0)
    _insert_position(AS_OF, "BNP", "ACC2", "CASH-USD", AS_OF, 500_000.0)
    _insert_position(AS_OF, "BNP", "ACC3", "CASH-USD", AS_OF, -200_000.0)
    # A CALC row for USD: must be excluded by the default source='BNP' filter.
    _insert_position(AS_OF, "CALC", "ACC1", "CASH-USD", AS_OF, 999_999.0)
    # A different as_of_date for EUR: must be excluded.
    _insert_position("2026-08-16", "BNP", "ACC1", "CASH-EUR", "2026-08-16", 123.0)
    conn.commit()

    ladder = cash_ladder(conn, AS_OF)
    cash_rows = ladder[ladder["kind"] == "CASH"]
    usd_rows = cash_rows[cash_rows["ccy"] == "USD"]
    assert len(usd_rows) == 1
    assert usd_rows["settle_date"].iloc[0] == AS_OF
    assert math.isclose(usd_rows["amount"].iloc[0], 1_000_000.0 + 500_000.0 - 200_000.0)
    assert "EUR" not in set(cash_rows["ccy"])

    # source='CALC' surfaces the CALC row instead.
    calc_ladder = cash_ladder(conn, AS_OF, source="CALC")
    calc_usd = calc_ladder[(calc_ladder["kind"] == "CASH") & (calc_ladder["ccy"] == "USD")]
    assert len(calc_usd) == 1
    assert math.isclose(calc_usd["amount"].iloc[0], 999_999.0)


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
    conn = _mk_conn()
    _insert_instrument(conn, "AUDUSD", "AUD", "USD", asset_class="FX_OPTION")
    _insert_trade(conn, "o1", "AUDUSD", "FX_OPTION", 1_000_000.0)
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "DELTA", 0.5, "MANUAL")
    _insert_mark(conn, AS_OF, "AUDUSD", AS_OF, "SPOT", 0.65, "BBG_BFXFORWARD")
    conn.commit()

    delta = delta_per_ccy(conn, AS_OF)
    aud_row = delta[delta["ccy"] == "AUD"]
    usd_row = delta[delta["ccy"] == "USD"]
    assert math.isclose(aud_row["delta"].iloc[0], 1_000_000.0 * 0.5)
    # quote (USD) leg: -quantity * delta * spot
    assert math.isclose(usd_row["delta"].iloc[0], -1_000_000.0 * 0.5 * 0.65)
    assert math.isclose(aud_row["delta_usd"].iloc[0], 1_000_000.0 * 0.5 * 0.65)
    assert math.isclose(usd_row["delta_usd"].iloc[0], -1_000_000.0 * 0.5 * 0.65)


# --------------------------------------------------------------------------- ladder_table
@needs_raw
def test_ladder_table_each_ccy_appears_once(real_conn_with_marks):
    ladder = cash_ladder(real_conn_with_marks, AS_OF, source="BNP")
    table = ladder_table(real_conn_with_marks, AS_OF, source="BNP_BVAL")
    assert set(table["ccy"]) == set(ladder["ccy"])
    assert table["ccy"].is_unique


@needs_raw
def test_ladder_table_total_matches_ladder_sum_per_ccy(real_conn_with_marks):
    ladder = cash_ladder(real_conn_with_marks, AS_OF, source="BNP")
    table = ladder_table(real_conn_with_marks, AS_OF, source="BNP_BVAL")
    expected_totals = ladder.groupby("ccy")["amount"].sum()
    for _, row in table.iterrows():
        assert math.isclose(row["total"], expected_totals[row["ccy"]], abs_tol=1e-6), row["ccy"]


@needs_raw
def test_ladder_table_usd_nan_exactly_for_ccys_without_bnp_spot(real_conn_with_marks):
    table = ladder_table(real_conn_with_marks, AS_OF, source="BNP_BVAL")
    nan_ccys = set(table.loc[table["usd"].isna(), "ccy"])
    # USD itself always converts at 1.0, never NaN.
    assert "USD" not in nan_ccys
    usd_row = table[table["ccy"] == "USD"]
    assert math.isclose(usd_row["usd"].iloc[0], usd_row["total"].iloc[0])
    # The exact set with no derivable BNP_BVAL spot, determined empirically above.
    assert nan_ccys == (NO_BNP_SPOT_CCYS & set(table["ccy"]))


@needs_raw
def test_ladder_table_rows_with_usd_come_first_sorted_by_abs_usd_desc(real_conn_with_marks):
    table = ladder_table(real_conn_with_marks, AS_OF, source="BNP_BVAL")
    has_usd = table["usd"].notna()
    n_with = has_usd.sum()
    # all "with usd" rows precede all "without usd" rows
    assert has_usd.iloc[:n_with].all()
    assert not has_usd.iloc[n_with:].any()
    with_usd_abs = table.loc[has_usd, "usd"].abs().tolist()
    assert with_usd_abs == sorted(with_usd_abs, reverse=True)


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
