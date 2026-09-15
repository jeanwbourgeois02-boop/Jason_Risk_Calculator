"""Signed cash-leg reconciliation against independently evaluated Excel formulas."""
import math

import pandas as pd
import pytest

from data.ingest import schema
from engine.ladder.valuation import ladder_trade_valuation, ladder_valuation_summary
from engine.ladder.ladder import cash_ladder
from ui.tabs.cash_ladder import valuation_table

AS_OF = "2026-08-17"
MATURITY = "2026-09-08"
PRICING = "2026-08-24"


def add_trade(conn, trade_id, pair, quantity, fill, *, ndf=False,
              trade_date=AS_OF, usd_rounding=0):
    base, quote = pair[:3], pair[3:]
    conn.execute("INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                 (pair, "FX", base, quote, 1, int(ndf), pair + " Curncy", "9999-12-31"))
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, "BNP", pair, "FX_FWD", trade_id, trade_date, quantity, fill,
                  "A", "C", "S", "T", "test", ""))
    for no, ccy, amount in [(1, base, quantity), (2, quote, -quantity * fill + usd_rounding)]:
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (trade_id, no, "FX_NEAR", ccy, amount, trade_date, MATURITY, fill, int(not ndf)))


def mark(conn, pair, value, date=PRICING, mark_type="FWD_OUTRIGHT"):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)",
                 (AS_OF, pair, date, mark_type, value, "BBG_BFXFORWARD", AS_OF + "T20:00:00Z"))


def test_usdjpy_traces_cash_legs_to_workbook_pnl_not_spot_pnl():
    conn = schema.connect(":memory:")
    add_trade(conn, "jpy", "USDJPY", 1_000_000, 150)
    mark(conn, "USDJPY", 155)
    mark(conn, "USDJPY", 140, date=AS_OF, mark_type="SPOT")
    row = ladder_trade_valuation(conn, AS_OF).iloc[0]
    expected = 1_000_000 * (155 - 150) / 155
    assert row.local_amount == -150_000_000
    assert row.usd_entry == 1_000_000
    assert row.pnl_usd == pytest.approx(expected)
    assert row.usd_valuation == pytest.approx(-150_000_000 / 155)
    assert row.usd_entry + row.usd_valuation == pytest.approx(expected)
    assert row.spot_usd_per_local == pytest.approx(1 / 140)
    assert row.valuation_date == PRICING
    assert row.valuation_residual == pytest.approx(0, abs=1e-8)
    conn.execute("UPDATE marks SET value=130 WHERE mark_type='SPOT'")
    changed = ladder_trade_valuation(conn, AS_OF).iloc[0]
    assert changed.pnl_usd == pytest.approx(expected)
    assert changed.spot_usd_per_local == pytest.approx(1 / 130)


def test_audusd_keeps_actual_entry_and_discloses_broker_rounding():
    conn = schema.connect(":memory:")
    add_trade(conn, "aud", "AUDUSD", 1_000_000, .65, usd_rounding=.01)
    mark(conn, "AUDUSD", .66)
    row = ladder_trade_valuation(conn, AS_OF).iloc[0]
    expected = 649_999.99 * (.66 - .65) / .65
    assert row.usd_entry == -649_999.99
    assert row.pnl_usd == pytest.approx(expected)
    assert row.physical_usd_valuation == 660_000
    assert row.usd_entry + row.usd_valuation == pytest.approx(expected)
    assert row.valuation_residual == pytest.approx(row.usd_valuation - 660_000)


def test_summary_retains_missing_price_and_ndf_notional_separation():
    conn = schema.connect(":memory:")
    add_trade(conn, "a", "USDJPY", 100, 150)
    add_trade(conn, "b", "USDJPY", 200, 145)
    add_trade(conn, "ndf", "USDBRL", 100, 5, ndf=True)
    mark(conn, "USDBRL", 5.2)
    detail = ladder_trade_valuation(conn, AS_OF)
    summary = ladder_valuation_summary(detail).set_index("ccy")
    assert math.isnan(summary.loc["JPY", "pnl_usd"])
    assert summary.loc["JPY", "missing_prices"] == 2
    assert summary.loc["JPY", "usd_entry"] == 300
    assert summary.loc["BRL", "settlement"] == "NDF notionals (not cash flows)"
    assert "BRL" not in cash_ladder(conn, AS_OF).ccy.tolist()
    assert detail.set_index("trade_id").loc["a", "fill"] == 150
    assert detail.set_index("trade_id").loc["b", "fill"] == 145


def test_later_imported_trade_not_in_historical_cash_ladder():
    conn = schema.connect(":memory:")
    add_trade(conn, "today", "AUDUSD", 100, .65)
    add_trade(conn, "tomorrow", "AUDUSD", 900, .65, trade_date="2026-08-18")
    ladder = cash_ladder(conn, AS_OF).set_index("ccy")
    assert ladder.loc["AUD", "amount"] == 100
    assert set(ladder_trade_valuation(conn, AS_OF).trade_id) == {"today"}


def test_display_preserves_rate_precision_and_missing_status():
    frame = pd.DataFrame([{"fill": .65123456, "mark": float("nan"),
                           "pnl_usd": float("nan"), "status": "Missing workbook rate"}])
    table = valuation_table(frame, "test-detail")
    assert table.data[0] == {"fill": "0.65123456", "mark": "", "pnl_usd": "",
                             "status": "Missing workbook rate"}
