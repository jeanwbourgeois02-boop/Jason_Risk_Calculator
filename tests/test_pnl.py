"""Tests for engine/pnl (per-trade LTD P&L). Real-file tests skip if the raw BNP CSV is
absent. See CLAUDE.md "P&L conventions" and "Must not replicate"."""
from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.bloomberg.bnp_marks import load_bnp_marks
from data.bloomberg.marks_csv import MarkRow
from data.ingest import bnp, schema
from engine.pnl.pnl import ltd_per_trade

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "HA_PNL_20260818.csv"
AS_OF = "2026-08-17"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")

OFFICIAL_FX_SOURCE = "BBG_BFXFORWARD"
SNAPPED_AT = "2026-08-17T15:00:00-04:00"


# --------------------------------------------------------------------- synthetic setup
def _make_conn():
    conn = schema.connect(":memory:")
    conn.executemany(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        [
            ("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31"),
            ("AUDUSD", "FX", "AUD", "USD", 1.0, 0, "AUDUSD Curncy", "9999-12-31"),
        ],
    )
    return conn


def _insert_trade(conn, trade_id, instrument_id, quantity, price, settle_date, trade_date="2026-08-01"):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "MANUAL", instrument_id, "FX_FWD", trade_id, trade_date, quantity, price,
         "ACC", "CPTY", "STRAT", "TRADER", "test trade"),
    )
    base_ccy = instrument_id[:3]
    quote_ccy = instrument_id[3:]
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 1, "FX_NEAR", base_ccy, quantity, trade_date, settle_date, price, 1),
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 2, "FX_NEAR", quote_ccy, -quantity * price, trade_date, settle_date, price, 1),
    )
    conn.commit()


def _insert_mark(conn, instrument_id, settle_date, mark_type, value, source=OFFICIAL_FX_SOURCE, as_of=AS_OF):
    conn.execute(
        "INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
        (as_of, instrument_id, settle_date, mark_type, value, source, SNAPPED_AT),
    )
    conn.commit()


# ------------------------------------------------------------------ 1. hand-computed
def test_ltd_usdjpy_long_and_short():
    conn = _make_conn()
    # long USDJPY: Q=+1,000,000, fill=147.00, mark=148.00 -> favourable (long USD)
    _insert_trade(conn, "T1", "USDJPY", 1_000_000, 147.00, "2026-09-01")
    # short USDJPY: Q=-500,000, fill=147.00, mark=148.00 -> unfavourable
    _insert_trade(conn, "T2", "USDJPY", -500_000, 147.00, "2026-09-01")
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148.00)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 147.50)  # quote(JPY)->USD = 1/147.50

    out = ltd_per_trade(conn, AS_OF)
    out = out.set_index("trade_id")

    expected_quote_t1 = 1_000_000 * (148.00 - 147.00)
    expected_usd_t1 = expected_quote_t1 * (1.0 / 147.50)
    assert math.isclose(out.loc["T1", "pnl_quote"], expected_quote_t1)
    assert math.isclose(out.loc["T1", "pnl_usd"], expected_usd_t1)

    expected_quote_t2 = -500_000 * (148.00 - 147.00)
    expected_usd_t2 = expected_quote_t2 * (1.0 / 147.50)
    assert math.isclose(out.loc["T2", "pnl_quote"], expected_quote_t2)
    assert math.isclose(out.loc["T2", "pnl_usd"], expected_usd_t2)
    assert (out["source"] == "OFFICIAL").all()


def test_ltd_audusd_long_and_short():
    conn = _make_conn()
    # long AUDUSD: Q=+2,000,000 AUD, fill=0.6500, mark=0.6600 -> favourable
    _insert_trade(conn, "T3", "AUDUSD", 2_000_000, 0.6500, "2026-09-01")
    # short AUDUSD: Q=-1,000,000 AUD, fill=0.6500, mark=0.6600 -> unfavourable
    _insert_trade(conn, "T4", "AUDUSD", -1_000_000, 0.6500, "2026-09-01")
    _insert_mark(conn, "AUDUSD", "2026-09-01", "FWD_OUTRIGHT", 0.6600)
    # quote_ccy is USD: no SPOT row needed, S = 1.0 trivially.

    out = ltd_per_trade(conn, AS_OF).set_index("trade_id")

    expected_quote_t3 = 2_000_000 * (0.6600 - 0.6500)
    assert math.isclose(out.loc["T3", "pnl_quote"], expected_quote_t3)
    assert math.isclose(out.loc["T3", "pnl_usd"], expected_quote_t3)  # S = 1.0
    assert out.loc["T3", "spot"] == 1.0

    expected_quote_t4 = -1_000_000 * (0.6600 - 0.6500)
    assert math.isclose(out.loc["T4", "pnl_quote"], expected_quote_t4)
    assert math.isclose(out.loc["T4", "pnl_usd"], expected_quote_t4)


# ------------------------------------------------------------- 1b. matured-trade filter
def test_matured_and_same_day_trades_excluded_only_future_settle_kept():
    """Must-not-replicate #4: matured trades must not be marked forever. A trade whose
    settle_date is on or before as_of must be excluded from ltd_per_trade; only the trade
    settling strictly after as_of should appear."""
    conn = _make_conn()
    _insert_trade(conn, "PAST", "USDJPY", 1_000_000, 147.00, "2026-08-16")   # as_of - 1
    _insert_trade(conn, "TODAY", "USDJPY", 1_000_000, 147.00, "2026-08-17")  # = as_of
    _insert_trade(conn, "FUTURE", "USDJPY", 1_000_000, 147.00, "2026-08-18")  # as_of + 1
    _insert_mark(conn, "USDJPY", "2026-08-16", "FWD_OUTRIGHT", 148.00)
    _insert_mark(conn, "USDJPY", "2026-08-17", "FWD_OUTRIGHT", 148.00)
    _insert_mark(conn, "USDJPY", "2026-08-18", "FWD_OUTRIGHT", 148.00)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 147.50)

    out = ltd_per_trade(conn, AS_OF)
    assert out["trade_id"].tolist() == ["FUTURE"]


# ------------------------------------------------------------------ 2. missing marks
def test_missing_fwd_outright_is_nan_and_strict_raises():
    conn = _make_conn()
    _insert_trade(conn, "T5", "USDJPY", 1_000_000, 147.00, "2026-09-01")
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 147.50)
    # no FWD_OUTRIGHT inserted

    out = ltd_per_trade(conn, AS_OF, strict=False)
    row = out.set_index("trade_id").loc["T5"]
    assert math.isnan(row["mark"])
    assert math.isnan(row["pnl_quote"])
    assert math.isnan(row["pnl_usd"])

    with pytest.raises(ValueError, match="T5"):
        ltd_per_trade(conn, AS_OF, strict=True)


def test_missing_spot_for_usdxxx_pnl_quote_populated_pnl_usd_nan():
    conn = _make_conn()
    _insert_trade(conn, "T6", "USDJPY", 1_000_000, 147.00, "2026-09-01")
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148.00)
    # no SPOT inserted at all

    out = ltd_per_trade(conn, AS_OF, strict=False).set_index("trade_id")
    expected_quote = 1_000_000 * (148.00 - 147.00)
    assert math.isclose(out.loc["T6", "pnl_quote"], expected_quote)
    assert math.isnan(out.loc["T6", "spot"])
    assert math.isnan(out.loc["T6", "pnl_usd"])

    with pytest.raises(ValueError, match="T6"):
        ltd_per_trade(conn, AS_OF, strict=True)


# --------------------------------------------------------------------------- real file
@pytest.fixture(scope="module")
def real_conn():
    conn = schema.connect(":memory:")
    bnp.load(RAW, conn, as_of_date=AS_OF)
    load_bnp_marks(RAW, conn, as_of_date=AS_OF, strict=False)
    yield conn
    conn.close()


def _bnp_reference(as_of=AS_OF):
    df = pd.read_csv(RAW)
    df = df[(df["Fund"] == "NMMF") & (df["Financial Type"] == "FORWARD")]
    out = {}
    for _, row in df.iterrows():
        symbol = str(row["Symbol"])
        m = re.match(r"^([A-Z]{6})(\d{6})-(\d+)$", symbol)
        trade_id = m.group(3)
        out[trade_id] = {
            "mv_local": float(row["Market Value Local"]),
            "mv_base": float(row["Market Value Base"]),
        }
    return out


@needs_raw
def test_real_file_no_derivable_spot_count_is_zero(real_conn):
    """Every 08/18 pair is USDXXX or XXXUSD (no crosses): a USDXXX pair's SPOT is its own
    instrument's mark, and an XXXUSD pair needs no SPOT at all (S = 1.0 trivially). So no
    trade should lack a derivable spot. This assertion makes that fact explicit rather
    than vacuous."""
    out = ltd_per_trade(real_conn, AS_OF, source="BNP_BVAL", strict=False)
    no_spot = out[out["spot"].isna()]
    assert len(no_spot) == 0, f"expected 0 trades without a derivable spot, got {len(no_spot)}: " \
                              f"{no_spot['trade_id'].tolist()}"


@needs_raw
def test_real_file_pnl_quote_matches_mv_local(real_conn):
    out = ltd_per_trade(real_conn, AS_OF, source="BNP_BVAL", strict=False)
    ref = _bnp_reference()
    assert len(out) == 229
    for _, row in out.iterrows():
        expected = ref[row["trade_id"]]["mv_local"]
        assert abs(row["pnl_quote"] - expected) <= 0.05, \
            f"trade {row['trade_id']}: pnl_quote={row['pnl_quote']} MV Local={expected}"


@needs_raw
def test_real_file_pnl_usd_matches_mv_base_when_spot_derivable(real_conn):
    out = ltd_per_trade(real_conn, AS_OF, source="BNP_BVAL", strict=False)
    ref = _bnp_reference()
    derivable = out[out["spot"].notna()]
    assert len(derivable) == 229  # all 229 trades have a derivable spot (see count test)
    for _, row in derivable.iterrows():
        r = ref[row["trade_id"]]
        tol = max(0.01, abs(r["mv_local"]) * 0.5e-6)
        assert abs(row["pnl_usd"] - r["mv_base"]) <= tol, \
            f"trade {row['trade_id']}: pnl_usd={row['pnl_usd']} MV Base={r['mv_base']} tol={tol}"


@needs_raw
def test_real_file_source_none_marks_official_empty_so_all_nan(real_conn):
    out = ltd_per_trade(real_conn, AS_OF, source=None, strict=False)
    assert len(out) == 229
    assert out["mark"].isna().all()
    assert out["pnl_usd"].isna().all()

    with pytest.raises(ValueError):
        ltd_per_trade(real_conn, AS_OF, source=None, strict=True)


# --------------------------------------------------------------------------- grep test
def test_no_division_by_mark_m_or_spot_variable():
    pnl_dir = REPO / "engine" / "pnl"
    pattern = re.compile(r"/\s*(mark|m|spot)\b")
    offenders = []
    for path in pnl_dir.glob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if pattern.search(code):
                offenders.append(f"{path.name}:{i}: {line}")
    assert not offenders, "division by mark/m/spot found:\n" + "\n".join(offenders)
