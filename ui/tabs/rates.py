"""Rates tab: the Blotter "Rates" sub-tab's real view (2026-09-15, replacing the
placeholder that stood in while no IRS trades/marks existed -- see `ui.tabs.blotter`'s
module docstring for the placeholder history).

`engine/rates` (rates-pricer) now writes `PAR_RATE` / `PV_USD` / `DV01_USD` marks with
`source='QL_PRICER'`, which `data/ingest/schema.py`'s `OFFICIAL_MARK_SOURCE` makes
official for those three mark_types (`BBG_BDH` is reconciliation-only for them now,
mirroring `BNP_BVAL` for FX). This module therefore bypasses
`ui.tabs.blotter_pricing.priced_value_book` entirely -- that pipeline only builds
FX/FUTURE rows (see its own docstring) -- and reads IRS trades plus their official
marks straight from the DB, exactly one instrument per swap (CLAUDE.md "BNP file ->
tables", INTEREST_RATE_SWAP rows), so a plain `instrument_id` join is enough; no
per-trade settle_date disambiguation is needed.

Per CLAUDE.md "official marks": every priced column here reads `marks_official`, never
`marks` directly. The one exception is the recon-status column, which by definition
needs the reconciliation-only `BBG_BDH` PV -- that one read goes straight at `marks`
with an explicit `source = 'BBG_BDH'` filter, never through `marks_official` (which
would return QL_PRICER rows for the same mark_type and silently mask the comparison).

Direction: `trades.quantity > 0` = pay fixed (CLAUDE.md "Leg layouts": a payer has a
negative FIXED leg; `engine/rates/store.py` uses the same sign to build
`ql.Swap.Payer`/`Receiver`).

Recon status: no existing recon-status convention exists elsewhere in this app to
reuse (checked `ui/tabs/blotter.py`, `ui/tabs/reconciliation.py`), so this module
defines its own, simple, stated tolerance (`RECON_ABS_TOLERANCE_USD` /
`RECON_REL_TOLERANCE`, same "max(fixed, relative)" shape as CLAUDE.md's own workbook
reconciliation tolerances, not the same numbers -- those are calibrated to the
workbook's own known rounding, this one is not): OK within tolerance, WARN outside it,
MISSING when no BBG_BDH PV_USD mark exists for that trade's instrument/date, or when
our own official PV_USD mark is itself missing (nothing to compare against). Missing
marks are never defaulted to zero anywhere in this module, per CLAUDE.md.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
from dash import dash_table, html

from ui.tabs.formatting import format_cell

DATATABLE_ID = "rates-datatable"

RECON_ABS_TOLERANCE_USD = 1000.0
RECON_REL_TOLERANCE = 0.005

_MARK_TYPES = ("PAR_RATE", "PV_USD", "DV01_USD")

_DISPLAY_COLUMNS = [
    "trade_id", "instrument_id", "ccy", "direction", "notional",
    "par_rate", "pv_usd", "dv01_usd", "recon_status",
]
_COLUMN_LABELS = {
    "trade_id": "Trade id", "instrument_id": "Swap", "ccy": "Ccy",
    "direction": "Direction", "notional": "Notional",
    "par_rate": "Par rate", "pv_usd": "PV (USD)", "dv01_usd": "DV01 (USD)",
    "recon_status": "Recon (QL_PRICER vs BBG SWPM)",
}


def _direction(quantity: float) -> str:
    """`trades.quantity > 0` -> pay fixed, else receive fixed (CLAUDE.md / engine/rates
    sign convention)."""
    return "Pay fixed" if quantity > 0 else "Receive fixed"


def _is_missing(value) -> bool:
    return value is None or value != value  # NaN != NaN


def recon_status(official_pv_usd, bbg_pv_usd) -> str:
    """OK / WARN / MISSING -- see module docstring for the tolerance and why MISSING
    covers either side being absent."""
    if _is_missing(official_pv_usd) or _is_missing(bbg_pv_usd):
        return "MISSING"
    tolerance = max(RECON_ABS_TOLERANCE_USD, abs(float(official_pv_usd)) * RECON_REL_TOLERANCE)
    return "OK" if abs(float(official_pv_usd) - float(bbg_pv_usd)) <= tolerance else "WARN"


def irs_rows(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """One row per IRS trade on file (regardless of whether it has been priced yet --
    an un-priced trade still shows its trade id/ccy/notional/direction with the mark
    columns blank, matching the "rows must always render" rule elsewhere in this app),
    with the official PAR_RATE/PV_USD/DV01_USD marks for `as_of` and a recon_status
    against the reconciliation-only BBG_BDH PV."""
    empty_cols = ["trade_id", "instrument_id", "ccy", "quantity", "notional", "direction",
                  "par_rate", "pv_usd", "dv01_usd", "bbg_pv_usd", "recon_status"]
    trades = pd.read_sql_query(
        # trades_official (2026-09-16): excludes source='BNP' so an IRS swap loaded
        # from both the once-daily BNP snapshot and the real-time blotter under two
        # different trade_ids (docs/open-questions.md item 55) is not listed, and its
        # PV/DV01 not double-counted, twice.
        "SELECT t.trade_id, t.instrument_id, i.base_ccy AS ccy, t.quantity "
        "FROM trades_official t JOIN instruments i ON i.instrument_id = t.instrument_id "
        "WHERE t.product = 'IRS' ORDER BY t.trade_id",
        conn,
    )
    if trades.empty:
        return pd.DataFrame(columns=empty_cols)

    official = pd.read_sql_query(
        "SELECT instrument_id, mark_type, value FROM marks_official "
        "WHERE as_of_date = ? AND mark_type IN ('PAR_RATE','PV_USD','DV01_USD')",
        conn, params=(as_of,),
    )
    official_pivot = (official.pivot_table(index="instrument_id", columns="mark_type",
                                            values="value", aggfunc="first")
                       if not official.empty else pd.DataFrame())

    bbg = pd.read_sql_query(
        "SELECT instrument_id, value FROM marks "
        "WHERE as_of_date = ? AND mark_type = 'PV_USD' AND source = 'BBG_BDH'",
        conn, params=(as_of,),
    )
    bbg_map = dict(zip(bbg["instrument_id"], bbg["value"]))

    df = trades.copy()
    df["notional"] = df["quantity"].abs()
    df["direction"] = df["quantity"].map(_direction)
    for mark_type in _MARK_TYPES:
        col = mark_type.lower()
        if mark_type in official_pivot.columns:
            df[col] = df["instrument_id"].map(official_pivot[mark_type])
        else:
            df[col] = float("nan")
    df["bbg_pv_usd"] = df["instrument_id"].map(bbg_map)
    df["recon_status"] = [recon_status(o, b) for o, b in zip(df["pv_usd"], df["bbg_pv_usd"])]
    return df


def _fmt_rate(value) -> str:
    if _is_missing(value):
        return "n/a"
    return f"{float(value) * 100:.4f}%"


def _fmt_notional(value) -> str:
    if _is_missing(value):
        return ""
    return f"{round(float(value)):,}"


def _fmt_usd(value) -> str:
    return "n/a" if _is_missing(value) else format_cell(value)


def format_rows(df: pd.DataFrame) -> tuple:
    """`(data_records, style_data_conditional)` for `rates_table`, split out so it can
    be unit-tested without Dash, matching `ui.tabs.blotter._format_rows`'s convention."""
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns]
    formatted = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    for col in cols:
        if col == "notional":
            formatted[col] = formatted[col].map(_fmt_notional)
        elif col == "par_rate":
            formatted[col] = formatted[col].map(_fmt_rate)
        elif col in ("pv_usd", "dv01_usd"):
            formatted[col] = formatted[col].map(_fmt_usd)
    data_records = formatted.to_dict("records")
    style_data_conditional = [
        {"if": {"filter_query": "{recon_status} = 'WARN'", "column_id": "recon_status"},
         "color": "var(--neg)", "fontWeight": "700"},
        {"if": {"filter_query": "{recon_status} = 'OK'", "column_id": "recon_status"},
         "color": "var(--pos)", "fontWeight": "700"},
        {"if": {"filter_query": "{recon_status} = 'MISSING'", "column_id": "recon_status"},
         "color": "var(--muted)", "fontStyle": "italic"},
    ]
    return data_records, style_data_conditional


def rates_table(df: pd.DataFrame, table_id: str = DATATABLE_ID) -> dash_table.DataTable:
    cols = [c for c in _DISPLAY_COLUMNS if c in df.columns] if not df.empty else list(_DISPLAY_COLUMNS)
    data_records, style_data_conditional = format_rows(df)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": _COLUMN_LABELS.get(c, c.replace("_", " ").title()), "id": c}
                 for c in cols],
        data=data_records,
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
                    "minWidth": "80px", "padding": "4px 8px"},
        style_header={"fontWeight": "bold"},
        style_data_conditional=style_data_conditional,
        page_size=25,
        page_action="native",
    )


def build_layout(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """The whole Rates sub-tab body: a short note when there are no IRS trades at all
    (table still renders, empty, with the full column set -- "rows must always render"),
    otherwise the blotter."""
    df = irs_rows(conn, as_of)
    children = []
    if df.empty:
        children.append(html.P("No IRS trades on file for this as-of date.",
                                className="section-kicker", style={"fontStyle": "italic"}))
    children.append(rates_table(df))
    return html.Div(children)
