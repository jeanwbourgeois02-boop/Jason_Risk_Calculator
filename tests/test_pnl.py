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
from engine.pnl.aggregate import aggregate_by_pair, book_totals, period_pnl

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


# ============================================================== engine.pnl.aggregate

# ------------------------------------------------------------- aggregate_by_pair
def test_aggregate_by_pair_usd_notional_ltd_and_count():
    conn = _make_conn()
    _insert_trade(conn, "T1", "USDJPY", 1_000_000, 147.00, "2026-09-01")
    _insert_trade(conn, "T2", "USDJPY", -500_000, 147.00, "2026-09-01")
    _insert_trade(conn, "T3", "AUDUSD", 2_000_000, 0.6500, "2026-09-01")
    _insert_trade(conn, "T4", "AUDUSD", -1_000_000, 0.6500, "2026-09-01")
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148.00)
    _insert_mark(conn, "USDJPY", AS_OF, "SPOT", 147.50)
    _insert_mark(conn, "AUDUSD", "2026-09-01", "FWD_OUTRIGHT", 0.6600)

    per_trade = ltd_per_trade(conn, AS_OF)
    by_pair = aggregate_by_pair(per_trade, conn).set_index("instrument_id")

    # usd_notional: per trade sign(quantity) x |USD leg amount|, summed by pair.
    # USDJPY's base_ccy IS USD, so the "USD leg" is leg 1 itself: amount = quantity.
    #   T1: |USD leg| = |1,000,000| = 1,000,000, sign(+1,000,000) = +1 -> +1,000,000
    #   T2: |USD leg| = |-500,000| = 500,000, sign(-500,000) = -1 -> -500,000
    expected_usdjpy_notional = 1 * abs(1_000_000) + (-1) * abs(-500_000)
    assert math.isclose(by_pair.loc["USDJPY", "usd_notional"], expected_usdjpy_notional)
    assert by_pair.loc["USDJPY", "n_trades"] == 2

    # AUDUSD: T3 leg USD amount = -2,000,000 x 0.65 = -1,300,000, sign(+2,000,000)=+1 -> +1,300,000
    #         T4 leg USD amount = -(-1,000,000) x 0.65 = 650,000, sign(-1,000,000)=-1 -> -650,000
    expected_audusd_notional = 1 * abs(2_000_000 * 0.6500) + (-1) * abs(1_000_000 * 0.6500)
    assert math.isclose(by_pair.loc["AUDUSD", "usd_notional"], expected_audusd_notional)
    assert by_pair.loc["AUDUSD", "n_trades"] == 2

    pt = per_trade.set_index("trade_id")
    expected_usdjpy_ltd = pt.loc["T1", "pnl_usd"] + pt.loc["T2", "pnl_usd"]
    expected_audusd_ltd = pt.loc["T3", "pnl_usd"] + pt.loc["T4", "pnl_usd"]
    assert math.isclose(by_pair.loc["USDJPY", "ltd_usd"], expected_usdjpy_ltd)
    assert math.isclose(by_pair.loc["AUDUSD", "ltd_usd"], expected_audusd_ltd)


def test_aggregate_by_pair_empty_input():
    conn = _make_conn()
    empty = ltd_per_trade(conn, AS_OF)  # no trades inserted -> empty per_trade
    out = aggregate_by_pair(empty, conn)
    assert list(out.columns) == ["instrument_id", "usd_notional", "ltd_usd", "n_trades"]
    assert len(out) == 0


# ------------------------------------------------------------------ book_totals
def test_book_totals_net_and_gross_exclude_gold_and_futures():
    conn = schema.connect(":memory:")
    conn.executemany(
        "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
        [
            ("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31"),
            ("AUDUSD", "FX", "AUD", "USD", 1.0, 0, "AUDUSD Curncy", "9999-12-31"),
            ("XAUUSD", "FX", "XAU", "USD", 1.0, 0, "XAUUSD Curncy", "9999-12-31"),
            ("ESU6 Index", "FUTURE", "ES", "USD", 50.0, 0, "ESU6 Index", "2026-09-18"),
        ],
    )
    conn.commit()
    by_pair = pd.DataFrame(
        {
            "instrument_id": ["USDJPY", "AUDUSD", "XAUUSD", "ESU6 Index"],
            "usd_notional": [1_000_000.0, -2_000_000.0, 500_000.0, 250_000.0],
            "ltd_usd": [0.0, 0.0, 0.0, 0.0],
            "n_trades": [1, 1, 1, 1],
        }
    )

    totals = book_totals(by_pair, conn)

    # net: USDJPY sign +1 (base_ccy USD) -> +1,000,000; AUDUSD sign -1 (base_ccy AUD) -> +2,000,000
    expected_net = 1.0 * 1_000_000.0 + (-1.0) * (-2_000_000.0)
    expected_gross = abs(1_000_000.0) + abs(-2_000_000.0)
    assert math.isclose(totals["net_usd"], expected_net)
    assert math.isclose(totals["gross_usd"], expected_gross)
    assert math.isclose(totals["gold_usd"], 500_000.0)
    assert math.isclose(totals["futures_usd"], 250_000.0)


def test_book_totals_empty_input():
    conn = _make_conn()
    by_pair = pd.DataFrame(columns=["instrument_id", "usd_notional", "ltd_usd", "n_trades"])
    totals = book_totals(by_pair, conn)
    assert totals == {"net_usd": 0.0, "gross_usd": 0.0, "gold_usd": 0.0, "futures_usd": 0.0}


# ------------------------------------------------------------------- period_pnl
def test_period_pnl_daily_crosses_weekend_others_nan():
    # AS_OF_MON = Monday 2026-08-17; previous business day = Friday 2026-08-14 (weekend crossed).
    # Marks exist only on those two dates, so d5 / mtd / ytd reference dates have no marks
    # at all -> NaN, per the "no marks at all -> NaN" rule.
    AS_OF_MON = "2026-08-17"
    PREV_FRI = "2026-08-14"
    conn = _make_conn()
    _insert_trade(conn, "P1", "USDJPY", 1_000_000, 147.00, "2026-09-01")
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 148.00, as_of=AS_OF_MON)
    _insert_mark(conn, "USDJPY", AS_OF_MON, "SPOT", 147.00, as_of=AS_OF_MON)
    _insert_mark(conn, "USDJPY", "2026-09-01", "FWD_OUTRIGHT", 147.50, as_of=PREV_FRI)
    _insert_mark(conn, "USDJPY", PREV_FRI, "SPOT", 146.50, as_of=PREV_FRI)

    result = period_pnl(conn, AS_OF_MON)

    expected_ltd_mon = 1_000_000 * (148.00 - 147.00) * (1.0 / 147.00)
    expected_ltd_fri = 1_000_000 * (147.50 - 147.00) * (1.0 / 146.50)
    assert math.isclose(result["ltd"], expected_ltd_mon)
    assert result["daily_ref_date"] == PREV_FRI
    assert math.isclose(result["daily"], expected_ltd_mon - expected_ltd_fri)

    assert math.isnan(result["d5"])
    assert math.isnan(result["mtd"])
    assert math.isnan(result["ytd"])
    # reference dates never land back on the two dates that do have marks
    assert result["d5_ref_date"] not in (AS_OF_MON, PREV_FRI)
    assert result["mtd_ref_date"] not in (AS_OF_MON, PREV_FRI)
    assert result["ytd_ref_date"] not in (AS_OF_MON, PREV_FRI)


def test_daily_pnl_excludes_trades_dated_after_reference_date():
    """C-1 regression: a trade dated as_of must never be valued (via marks that happen
    to exist) inside LTD(ref_date) for a ref_date before its trade_date -- _TRADE_SQL
    must filter t.trade_date <= :as_of, not just l.settle_date > :as_of. An OLD trade
    (trade_date well before both dates, marked flat so its LTD is 0 on both dates) is
    included purely to keep both reference-date LTD sums non-empty/non-NaN so daily can
    be compared directly to ltd."""
    AS_OF_MON = "2026-08-17"
    PREV_FRI = "2026-08-14"
    OLD_SETTLE = "2026-09-01"
    NEW_SETTLE = "2026-10-01"
    conn = _make_conn()

    _insert_trade(conn, "OLD", "USDJPY", 1_000_000, 147.00, OLD_SETTLE, trade_date="2026-08-01")
    _insert_mark(conn, "USDJPY", OLD_SETTLE, "FWD_OUTRIGHT", 147.00, as_of=AS_OF_MON)  # = fill -> 0 pnl
    _insert_mark(conn, "USDJPY", OLD_SETTLE, "FWD_OUTRIGHT", 147.00, as_of=PREV_FRI)   # = fill -> 0 pnl
    _insert_mark(conn, "USDJPY", AS_OF_MON, "SPOT", 147.50, as_of=AS_OF_MON)
    _insert_mark(conn, "USDJPY", PREV_FRI, "SPOT", 146.50, as_of=PREV_FRI)

    # NEW trade dated exactly as_of; marks present even on the earlier date so the old,
    # unfiltered SQL would have wrongly picked it up there too.
    _insert_trade(conn, "NEW", "USDJPY", 1_000_000, 147.00, NEW_SETTLE, trade_date=AS_OF_MON)
    _insert_mark(conn, "USDJPY", NEW_SETTLE, "FWD_OUTRIGHT", 148.00, as_of=AS_OF_MON)
    _insert_mark(conn, "USDJPY", NEW_SETTLE, "FWD_OUTRIGHT", 999.00, as_of=PREV_FRI)

    ref_out = ltd_per_trade(conn, PREV_FRI, strict=False)
    assert "NEW" not in ref_out["trade_id"].tolist()
    assert "OLD" in ref_out["trade_id"].tolist()

    result = period_pnl(conn, AS_OF_MON)
    assert result["daily_ref_date"] == PREV_FRI
    assert not math.isnan(result["ltd"])
    assert not math.isnan(result["daily"])
    assert math.isclose(result["daily"], result["ltd"])


def test_period_pnl_no_open_trades_all_nan():
    conn = _make_conn()  # no trades at all -> ltd_per_trade empty on every date
    result = period_pnl(conn, "2026-08-17")
    assert math.isnan(result["ltd"])
    assert math.isnan(result["daily"])
    assert math.isnan(result["d5"])
    assert math.isnan(result["mtd"])
    assert math.isnan(result["ytd"])
