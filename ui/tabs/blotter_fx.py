"""Blotter "FX" sub-tab: a literal replica of the old xlsx workbook's "All FX trades"
sheet, per user authorisation 2026-09-17 -- this supersedes the earlier
`priced_value_book`/`value_book`-shaped FX table entirely (see `ui.tabs.blotter`'s
module docstring for the general sub-tab pattern and history).

Rows come from `engine.pnl.xlsx_fx_replica.fx_replica`, one row per FX_SPOT/FX_FWD/
FX_SWAP/FUTURE trade, deliberately reproducing the old sheet's known quirks (futures
P&L divided by mark instead of fill, the LTD-2 column's t-1-mark divisor bug, one
shared per-pair valuation date rather than each trade's own settle_date) -- these are
intentional display fidelity to the historical sheet, not bugs to fix or flag here; see
that module's own docstring for the CLAUDE.md "must not replicate" cross-reference.

Like `ui.tabs.rates` (IRS sub-tab), this is a plain always-current table with no P&L
strip, no per-column filter dropdowns and no row-expand detail panel: `fx_replica`'s row
shape (trade_id/instrument_id/quantity_usd_notional/tenor/fill/mark_*/pnl_*) doesn't
match `value_book`'s (trade_id/mark/pnl_usd/...) that the strip/filter/detail scaffolding
in `ui.tabs.blotter` assumes, so this sub-tab is excluded from that generic callback loop
the same way "rates" is.

Any of `fx_replica`'s mark/pnl columns can be `None` when a mark is genuinely missing;
rendered as blank ("") here, never "0.00" or "n/a" (an "n/a" would imply a value was
computed and found unavailable, whereas here nothing was computed at all) -- matching
`ui.tabs.formatting.format_cell`'s existing blank-for-NaN convention used elsewhere in
this file.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
from dash import dash_table, html

from engine.pnl.xlsx_fx_replica import fx_replica
from ui.tabs.formatting import format_cell

DATATABLE_ID = "blotter-fx-replica-datatable"

_DISPLAY_COLUMNS = [
    "trade_date", "instrument_id", "quantity_usd_notional", "tenor", "fill",
    "mark_t1", "mark_eod", "mark_t2", "pnl_t1", "pnl_eod", "pnl_t2",
]
_COLUMN_LABELS = {
    "trade_date": "Date", "instrument_id": "Instrument", "quantity_usd_notional": "Quantity",
    "tenor": "Value date", "fill": "Fill",
    "mark_t1": "T-1 mark", "mark_eod": "EOD mark", "mark_t2": "T-2 mark",
    "pnl_t1": "LTD-1 P&L", "pnl_eod": "LTD P&L", "pnl_t2": "LTD-2 P&L",
}
_RATE_COLS = {"fill", "mark_t1", "mark_eod", "mark_t2"}
_USD_COLS = {"quantity_usd_notional", "pnl_t1", "pnl_eod", "pnl_t2"}


def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def _fmt_rate(value) -> str:
    if _is_missing(value):
        return ""
    return f"{float(value):,.6f}"


def format_rows(df: pd.DataFrame) -> list:
    """Formatted `data` records for `fx_replica_table`, split out so it can be
    unit-tested without Dash, matching `ui.tabs.rates.format_rows`'s convention.
    Missing marks/P&L render blank, never "0.00" or "n/a" -- see module docstring."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    for col in cols:
        if col in _RATE_COLS:
            formatted[col] = formatted[col].map(_fmt_rate)
        elif col in _USD_COLS:
            formatted[col] = formatted[col].map(format_cell)
    return formatted.to_dict("records")


def fx_replica_table(df: pd.DataFrame, table_id: str = DATATABLE_ID) -> dash_table.DataTable:
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns] if not df.empty else list(_DISPLAY_COLUMNS)
    data_records = format_rows(df)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": _COLUMN_LABELS.get(c, c.replace("_", " ").title()), "id": c}
                 for c in cols],
        data=data_records,
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_header={"fontWeight": "bold"},
        page_size=25,
        page_action="native",
    )


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole FX sub-tab body: `fx_replica` rows, plus a short note when there are
    none (table still renders, empty, with the full column set -- "rows must always
    render" rule elsewhere in this app)."""
    df = fx_replica(conn, as_of)
    children = []
    if df.empty:
        children.append(html.P("No FX/futures trades on file for this as-of date.",
                                className="section-kicker", style={"fontStyle": "italic"}))
    children.append(fx_replica_table(df))
    return html.Div(children)
