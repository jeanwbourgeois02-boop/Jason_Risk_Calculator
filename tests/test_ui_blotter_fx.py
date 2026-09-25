"""ui-blotter-fx's own tests (`ui/tabs/blotter_fx.py`). Older tests of this module still
sit in tests/test_ui.py and tests/test_ui_blotter.py."""
from __future__ import annotations

import pandas as pd

from ui.tabs import blotter_fx


def test_fx_scope_keeps_jasons_fx_hedges_and_leaves_futures_and_options_out():
    """Phase 2 (2026-09-24): the sub-tab stays for the FX hedges (USDCNH, EURUSD, USDJPY,
    GBPUSD, EURGBP, XAUUSD); commodity futures and FX options stay on their own sub-tabs."""
    df = pd.DataFrame({
        "trade_id": ["F1", "S1", "C1", "O1"],
        "product": ["FX_FWD", "FX_SPOT", "FUTURE", "FX_OPTION"],
        "instrument_id": ["USDCNH", "EURUSD", "CLZ26 Comdty", "USDJPY111926P-1"],
    })
    assert blotter_fx._scoped_trade_ids(df) == ["F1", "S1"]
    assert blotter_fx.currency_label("USDCNH", "USD", "CNH") == "CNH"
    assert blotter_fx.currency_label("EURGBP", "EUR", "GBP") == "EURGBP"
    assert blotter_fx.currency_label("XAUUSD", "XAU", "USD") == "XAU"


def test_module_names_no_removed_rates_screen():
    """ui/tabs/rates.py leaves in Phase 2: nothing here may point at it."""
    import inspect

    assert "ui.tabs.rates" not in inspect.getsource(blotter_fx)


# ------------------------------------------------ USD notional can be None (2026-09-24, pnl-series)
def _notional_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_id": "T1", "instrument_id": "USDCNH", "trade_date": "2026-09-01",
         "quantity_usd_notional": 1_000_000.0, "notional_reason": "", "fill": 7.10, "tenor": "2026-12-01",
         "mark_eod": 7.12, "mark_t1": 7.11, "mark_t2": 7.11,
         "pnl_eod": 2_800.0, "pnl_t1": 1_400.0, "pnl_t2": 1_400.0, "reason": ""},
        {"trade_id": "T2", "instrument_id": "EURUSD", "trade_date": "2026-09-01",
         "quantity_usd_notional": None, "notional_reason": "USD notional n/a: no SPOT for USD conversion of EUR on 2026-09-24",
         "fill": 1.10, "tenor": "2026-12-01",
         "mark_eod": None, "mark_t1": None, "mark_t2": None,
         "pnl_eod": None, "pnl_t1": None, "pnl_t2": None, "reason": "no SPOT for EUR on 2026-09-24"},
    ])


def test_a_none_usd_notional_reads_n_a_with_its_reason_on_hover():
    df = _notional_df()
    records, tips = blotter_fx.format_rows(df), blotter_fx.row_tooltips(df)
    assert records[0]["quantity_usd_notional"] == 1_000_000.0 and "quantity_usd_notional" not in tips[0]
    assert records[1]["quantity_usd_notional"] == blotter_fx.UNPRICED_TEXT   # never 0, never blank
    assert tips[1]["quantity_usd_notional"] == {
        "value": "USD notional n/a: no SPOT for USD conversion of EUR on 2026-09-24", "type": "text"}
    # The engine gave no reason: a plain line, never an empty hover.
    no_why = df.assign(notional_reason="")
    assert blotter_fx.row_tooltips(no_why)[1]["quantity_usd_notional"]["value"] == blotter_fx.NO_NOTIONAL
    # Muted italic like every other n/a cell, and the table carries the tooltip.
    table = blotter_fx.fx_blotter_table(df)
    assert any(r["if"].get("column_id") == "quantity_usd_notional" for r in table.style_data_conditional)
    assert table.tooltip_data[1]["quantity_usd_notional"]["value"].startswith("USD notional n/a")


def test_a_total_over_rows_with_one_none_skips_it_never_as_zero():
    """The rows-shown total sums real numbers only: the row with no notional and no P&L is
    left out and named, never counted as 0; nothing ever sums the "n/a" text."""
    records = blotter_fx.format_rows(_notional_df())
    assert records[1]["pnl_eod_num"] is None
    _rows, total = blotter_fx.shown_currency_rows(records)
    ltd = total["figures"]["ltd"]
    assert ltd["available"] and ltd["value"] == 2_800.0
    assert ltd["excluded_summary"] == "excludes 1 of 2 rows unpriced"
    # No hidden column carries the notional, so no sum can pick up a 0 for it.
    assert not any("notional" in c for c in blotter_fx._HIDDEN_COLUMNS)


def test_the_notional_column_is_labelled_usd():
    columns = blotter_fx.table_columns(["quantity_usd_notional"])
    assert columns[0]["name"] == "Quantity (USD)"


# ------------------------------------------------ k / m on the currency tables (2026-09-25, "Natural units")
def _ccy_row(currency, trades, **figures):
    return {"currency": currency, "trades": trades, "figures": figures}


def test_the_currency_tables_print_k_m_with_the_full_figure_on_hover():
    from ui.tabs import ranking as rk

    periods = (("ltd", "LTD"), ("daily", "Daily"))
    rows = [_ccy_row("JPY", 3, ltd={"value": 1_650_590.4, "available": True},
                     daily={"value": 0.4, "available": True}),
            _ccy_row("MXN", 1, ltd={"value": float("nan"), "available": False, "reason": "no SPOT for MXN"},
                     daily={"value": -2_400.0, "available": True, "excluded_summary": "excludes 1 of 2 trades unpriced"})]
    total = _ccy_row("Total", 4, ltd={"value": 1_650_590.4, "available": True},
                     daily={"value": -2_399.6, "available": True})
    scroll, *_note = blotter_fx.currency_table(rows, total, periods, table_id=blotter_fx.FIXED_TABLE_ID)
    table, footer = scroll.children.children          # the scroll box > ranked table + pinned Total
    formats = {c["id"]: c.get("format") for c in table.columns}
    assert formats["ltd"] == formats["daily"] == rk.amount_short(nully="n/a")
    assert formats["trades"] == rk.count()            # a count, not money

    # Whole units behind the k / M format: 0.4 never prints as "400m"; still numbers, so they rank.
    jpy, mxn = table.data
    assert jpy["ltd"] == 1_650_590.0 and jpy["daily"] == 0.0 and isinstance(jpy["daily"], float)
    assert mxn["ltd"] is None and mxn["daily"] == -2_400.0      # unavailable: "n/a", never 0
    assert footer.data[0]["ltd"] == 1_650_590.0 and footer.data[0]["daily"] == -2_400.0

    # The full figure on each cell's hover; a note under it; an n/a keeps its reason alone.
    jpy_tip, mxn_tip = table.tooltip_data
    assert jpy_tip["ltd"]["value"] == "1,650,590 USD" and jpy_tip["daily"]["value"] == "0 USD"
    assert mxn_tip["ltd"]["value"] == "no SPOT for MXN"
    assert mxn_tip["daily"]["value"] == "(2,400) USD\nexcludes 1 of 2 trades unpriced"
    assert footer.tooltip_data[0]["daily"]["value"] == "(2,400) USD"
    assert "trades" not in jpy_tip


def test_the_trade_table_keeps_full_figures():
    from ui.tabs import ranking as rk

    columns = {c["id"]: c for c in blotter_fx.table_columns(["pnl_eod", "quantity_usd_notional"])}
    assert columns["pnl_eod"]["format"] == rk.amount() == columns["quantity_usd_notional"]["format"]
    records = blotter_fx.format_rows(_notional_df())
    assert records[0]["pnl_eod"] == 2_800.0 and records[0]["pnl_eod_num"] == 2_800.0
