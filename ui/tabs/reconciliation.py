"""Reconciliation tab: "Do I agree with BNP and the Excel?" (docs/BUILD_PLAN.md section 5).

Two independent checks live here, side by side, neither feeding the header:

1. Ours vs BNP per instrument: `positions` rows with `source = 'BNP'` for `as_of_date`
   next to our own `engine.pnl.valuation.value_book(conn, as_of)`, grouped by
   `instrument_id`. Like-with-like (per coordinator correction 2026-09-15): BNP's
   `mv_usd` for an open FX forward is `Quantity x (Price - rate) x Fx`, i.e. the
   broker's own unrealised LTD in USD -- the same quantity as our `pnl_usd` on OPEN
   `value_book` rows, not `pnl_dtd_usd`. So `ours_ltd_usd` sums `pnl_usd` over
   `status == 'OPEN'` rows only (rows with a non-empty `reason`, i.e. a missing mark,
   are excluded and counted in `unpriced` instead), and `break_usd = ours_ltd_usd -
   bnp_mv_usd`, both per instrument_id. BNP's `pnl_dtd_usd` is shown alongside as a
   separate informational column (not part of the break) next to our own daily figure
   from `engine.pnl.ledger.period_pnl_by(conn, as_of, 'instrument_id')['daily']`.

2. The workbook panel: the HA-portfolio xlsx's own literal formulas, unchanged, absorbed
   from the former `ui/tabs/pnl.py` (deleted by this change -- see docs/BUILD_PLAN.md
   Task C4) and from the workbook mark-to-market block formerly on the Cash ladder tab
   (`engine/ladder/valuation.py`'s `ladder_trade_valuation` / `ladder_valuation_summary`,
   re-created here rather than imported from `ui/tabs/cash_ladder.py` because C1 deletes
   those functions from that module concurrently with this change). Plus the manual
   workbook-rates entry grid (`ui/workbook_rates.py`, which stays put; its
   `layout(default_date)` / `register(app, get_db_path)` are now called from this tab
   instead of top-level `ui/app.py` -- C5 must remove the old top-level wiring in
   `ui/app.py` when it rewires tabs, or the rates grid will render twice).

Engine imports (`engine.pnl.pnl`, `engine.pnl.aggregate`, `engine.pnl.valuation`,
`engine.ladder.valuation`) are lazy, inside the callback, same pattern as
`ui/tabs/cash_ladder.py` and the former `ui/tabs/pnl.py`: this module -- and `ui.app` --
must always import successfully even if an engine module is absent or mid-change.

Number formatting: `ui.tabs.formatting.format_cell` for USD amounts everywhere, per the
whole-unit / thousands-separator / parens-negative / blank-for-NaN convention used by
every other tab. Source dropdown / date picker reuse `ui.tabs.controls` with this tab's
own component ids so callbacks never collide with other tabs.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs.controls import build_date_picker, build_source_dropdown, source_value_to_param
from ui.tabs.formatting import format_cell

SOURCE_DROPDOWN_ID = "reconciliation-source"
DATE_PICKER_ID = "reconciliation-date"
CONTENT_CONTAINER_ID = "reconciliation-content-container"

# Workbook futures-fill import (user decision 2026-09-15, item B): its own small
# choose/confirm/status control, separate from the BNP report upload strip above the
# tabs. data.ingest.xlsx_futures.load_futures_fills(xlsx_path, conn) takes a file PATH,
# not bytes, so the uploaded contents are written to a temp file before calling it.
FUTURES_UPLOAD_ID = "reconciliation-futures-file"
FUTURES_STAGE_ID = "reconciliation-futures-stage"
FUTURES_FILENAME_ID = "reconciliation-futures-filename"
FUTURES_CONFIRM_ID = "reconciliation-futures-confirm"
FUTURES_RESULT_ID = "reconciliation-futures-result"

BREAK_TABLE_ID = "reconciliation-break-datatable"
PAIR_TABLE_ID = "reconciliation-pnl-pair-datatable"
TOTALS_TABLE_ID = "reconciliation-pnl-totals-datatable"
PERIOD_TABLE_ID = "reconciliation-pnl-period-datatable"

BREAK_COLUMNS = [
    "instrument_id", "bnp_mv_usd", "ours_ltd_usd", "unpriced", "break_usd",
    "bnp_pnl_dtd_usd", "ours_daily_usd",
]
PAIR_COLUMNS = ["instrument_id", "usd_notional", "ltd_usd", "n_trades"]
TOTALS_KEYS = ["net_usd", "gross_usd", "gold_usd", "futures_usd"]
PERIOD_KEYS = ["ltd", "daily", "d5", "mtd", "ytd"]
PERIOD_LABELS = {"ltd": "LTD", "daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}

DETAIL_COLUMNS = [
    "settle_date", "ccy", "trade_id", "instrument_id", "settlement", "local_amount",
    "other_ccy", "other_amount", "fill", "valuation_date", "mark", "spot_usd_per_local",
    "usd_entry", "usd_valuation", "physical_usd_valuation", "valuation_residual",
    "workbook_quantity", "denominator", "pnl_usd", "status",
]


def message_box(message: str) -> html.P:
    """Grey status text shown in place of the tables (missing engine module, missing
    DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def break_table_from_frames(bnp: pd.DataFrame, value_book: pd.DataFrame,
                             daily_by_instrument: Optional[dict] = None) -> dash_table.DataTable:
    """Pure function of a BNP-positions frame (`instrument_id, mv_usd, pnl_dtd_usd`) and
    a `value_book`-shaped frame (`instrument_id, status, pnl_usd, reason`). Grouped,
    joined outer on `instrument_id` so an instrument on only one side still shows (the
    other side's columns blank -- that is itself a break worth seeing).

    `ours_ltd_usd` sums `pnl_usd` over OPEN rows only (BNP's `mv_usd` for an open FX
    forward is its own unrealised LTD, not a DTD figure -- see module docstring);
    `break_usd = ours_ltd_usd - bnp_mv_usd`. Rows with a non-empty `reason` (missing
    mark) are excluded from `ours_ltd_usd` and counted in `unpriced` instead.
    `bnp_pnl_dtd_usd` / `ours_daily_usd` are informational only, not part of the break;
    `daily_by_instrument` is an optional `{instrument_id: daily_usd}` map, typically from
    `engine.pnl.ledger.period_pnl_by(conn, as_of, 'instrument_id')['daily']`."""
    if bnp.empty:
        bnp_g = pd.DataFrame(columns=["instrument_id", "bnp_mv_usd", "bnp_pnl_dtd_usd"])
    else:
        bnp_g = (
            bnp.groupby("instrument_id")
            .agg(bnp_mv_usd=("mv_usd", "sum"), bnp_pnl_dtd_usd=("pnl_dtd_usd", "sum"))
            .reset_index()
        )

    if value_book.empty:
        ours_g = pd.DataFrame(columns=["instrument_id", "ours_ltd_usd", "unpriced"])
    else:
        vb = value_book.copy()
        vb["_unpriced"] = vb["reason"].fillna("").astype(str).str.len() > 0
        vb["_open"] = vb.get("status", "OPEN") == "OPEN"

        def _agg(g):
            open_priced = g[g["_open"] & ~g["_unpriced"]]
            return pd.Series({
                "ours_ltd_usd": open_priced["pnl_usd"].sum(skipna=False) if len(open_priced) else float("nan"),
                "unpriced": int((g["_open"] & g["_unpriced"]).sum()),
            })

        ours_g = vb.groupby("instrument_id").apply(_agg).reset_index()

    merged = bnp_g.merge(ours_g, on="instrument_id", how="outer")
    if merged.empty:
        merged = pd.DataFrame(columns=BREAK_COLUMNS)
    else:
        merged["break_usd"] = merged["ours_ltd_usd"] - merged["bnp_mv_usd"]
        daily_by_instrument = daily_by_instrument or {}
        merged["ours_daily_usd"] = merged["instrument_id"].map(daily_by_instrument)
        merged = merged.reindex(columns=BREAK_COLUMNS).sort_values("instrument_id", kind="mergesort")

    formatted = merged.copy()
    for col in ("bnp_mv_usd", "ours_ltd_usd", "break_usd", "bnp_pnl_dtd_usd", "ours_daily_usd"):
        formatted[col] = formatted[col].map(format_cell)
    formatted["unpriced"] = formatted["unpriced"].map(format_cell)
    return dash_table.DataTable(
        id=BREAK_TABLE_ID,
        columns=[{"name": col, "id": col} for col in BREAK_COLUMNS],
        data=formatted.to_dict("records"),
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
        page_size=25,
    )


def pairs_table_from_by_pair(by_pair: pd.DataFrame) -> dash_table.DataTable:
    """DataTable from an `aggregate_by_pair`-shaped frame (instrument_id, usd_notional,
    ltd_usd, n_trades). Pure function of the frame -- does not touch the DB."""
    if by_pair.empty:
        data = []
    else:
        formatted = by_pair.copy()
        for col in ("usd_notional", "ltd_usd", "n_trades"):
            formatted[col] = formatted[col].map(format_cell)
        data = formatted[PAIR_COLUMNS].to_dict("records")
    columns = [{"name": col, "id": col} for col in PAIR_COLUMNS]
    return dash_table.DataTable(
        id=PAIR_TABLE_ID,
        columns=columns,
        data=data,
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def totals_table_from_book_totals(totals: dict) -> dash_table.DataTable:
    """DataTable from a `book_totals`-shaped dict (net_usd, gross_usd, gold_usd,
    futures_usd). Pure function of the dict."""
    data = [{"metric": key, "usd": format_cell(totals.get(key))} for key in TOTALS_KEYS]
    return dash_table.DataTable(
        id=TOTALS_TABLE_ID,
        columns=[{"name": "metric", "id": "metric"}, {"name": "usd", "id": "usd"}],
        data=data,
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def period_table_from_period_pnl(period: dict) -> dash_table.DataTable:
    """DataTable from a `period_pnl`-shaped dict (ltd, daily, d5, mtd, ytd, as_of_date,
    plus a `<key>_ref_date` per period). Pure function of the dict."""
    data = []
    for key in PERIOD_KEYS:
        ref_date = period.get(f"{key}_ref_date", "") if key != "ltd" else ""
        data.append({"period": PERIOD_LABELS[key], "usd": format_cell(period.get(key)), "ref_date": ref_date})
    return dash_table.DataTable(
        id=PERIOD_TABLE_ID,
        columns=[{"name": "period", "id": "period"}, {"name": "usd", "id": "usd"}, {"name": "ref_date", "id": "ref_date"}],
        data=data,
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def valuation_table(df: pd.DataFrame, table_id: str) -> dash_table.DataTable:
    """Preserve rate precision and text labels while formatting dollar amounts.
    Re-created from `ui/tabs/cash_ladder.py` (owned there by C1, which deletes it)."""
    rates = {"fill", "mark", "spot_usd_per_local", "denominator"}
    labels = {
        "ccy": "Local currency", "settle_date": "Settlement date",
        "local_amount": "Signed local amount", "fill": "Entry FX",
        "mark": "Workbook valuation FX", "valuation_date": "Workbook pricing date",
        "usd_entry": "Signed USD entry", "usd_valuation": "Workbook USD valuation",
        "pnl_usd": "USD P&L", "spot_usd_per_local": "General spot (USD/local; reference)",
        "physical_usd_valuation": "Actual local leg at valuation FX (USD)",
        "valuation_residual": "Workbook less actual leg value (USD)",
        "denominator": "Workbook divisor", "workbook_quantity": "Workbook quantity C",
    }
    formatted = df.copy()
    for col in formatted:
        if col in rates:
            formatted[col] = formatted[col].map(lambda value: "" if pd.isna(value) else f"{value:,.8f}")
        elif pd.api.types.is_numeric_dtype(formatted[col]):
            formatted[col] = formatted[col].map(format_cell)
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": labels.get(col, col.replace("_", " ").title()), "id": col} for col in formatted],
        data=formatted.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "right", "fontFamily": "monospace", "minWidth": "110px"},
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        page_size=25,
    )


def futures_import_control() -> html.Div:
    """Choose/confirm/status control for the workbook futures-fill import (item B).
    Nothing is written until Confirm is pressed, same pattern as the BNP upload strip."""
    return html.Div(className="source-strip", children=[
        html.Div(className="source-row", children=[
            dcc.Upload(id=FUTURES_UPLOAD_ID, className="source-upload",
                       children=html.Button("Import futures fills from workbook", className="btn"),
                       accept=".xlsx,.xlsm", multiple=False, max_size=25 * 1024 * 1024),
            html.Div(id=FUTURES_FILENAME_ID, className="source-line"),
        ]),
        html.Div(id=FUTURES_STAGE_ID, className="source-row source-row--stage", style={"display": "none"}, children=[
            html.Button("Confirm import", id=FUTURES_CONFIRM_ID, n_clicks=0, className="btn"),
        ]),
        dcc.Loading(type="dot", color="#1f5fbf", children=html.Div(id=FUTURES_RESULT_ID, role="status", className="source-result")),
    ])


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Controls + an (initially empty) content container. Tables are filled in by the
    callback registered in register_callbacks. Includes the workbook manual-rates grid
    (`ui/workbook_rates.py`), moved here per docs/BUILD_PLAN.md Task C4, and the
    workbook futures-fill import control (item B)."""
    from ui import workbook_rates

    return html.Div([
        html.H3("Reconciliation"),
        html.P("Ours vs BNP, and ours vs the Excel workbook. Neither feeds the header figures."),
        build_source_dropdown(SOURCE_DROPDOWN_ID, label="Workbook MTM rates"),
        build_date_picker(DATE_PICKER_ID, default_date=default_date),
        workbook_rates.layout(default_date),
        futures_import_control(),
        html.Div(id=CONTENT_CONTAINER_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callback that re-queries `positions` / `value_book` / the workbook
    engine whenever the source dropdown, date picker or saved rates change. Also
    registers `ui/workbook_rates.py`'s own callbacks (moved here from `ui/app.py`)."""
    from ui import workbook_rates

    workbook_rates.register(app, get_db_path)

    @app.callback(
        Output(FUTURES_STAGE_ID, "style"), Output(FUTURES_FILENAME_ID, "children"),
        Output(FUTURES_RESULT_ID, "children", allow_duplicate=True),
        Input(FUTURES_UPLOAD_ID, "contents"), Input(FUTURES_UPLOAD_ID, "filename"),
        prevent_initial_call=True,
    )
    def _futures_selected(contents, filename):
        if not contents:
            return {"display": "none"}, "", ""
        return {}, filename, ""

    @app.callback(
        Output(FUTURES_RESULT_ID, "children"),
        Input(FUTURES_CONFIRM_ID, "n_clicks"),
        State(FUTURES_UPLOAD_ID, "contents"), State(FUTURES_UPLOAD_ID, "filename"),
        prevent_initial_call=True,
    )
    def _futures_confirm(clicks, contents, filename):
        if not contents:
            return html.Span("Choose a workbook first.", className="source-result--error")
        try:
            from data.ingest.upload import decode
            from data.ingest.xlsx_futures import load_futures_fills
            from data.ingest import schema
            import tempfile
            from pathlib import Path

            payload = decode(contents)
            with tempfile.TemporaryDirectory() as tmp:
                xlsx_path = Path(tmp) / (filename or "workbook.xlsx")
                xlsx_path.write_bytes(payload)
                conn = schema.connect(get_db_path())
                try:
                    inserted = load_futures_fills(xlsx_path, conn)
                finally:
                    conn.close()
            return f"Imported {inserted} new futures fill(s) from {filename}."
        except Exception as exc:
            return html.Span(f"Import failed; no futures fills saved. {exc}", className="source-result--error")

    @app.callback(
        Output(CONTENT_CONTAINER_ID, "children"),
        Input(SOURCE_DROPDOWN_ID, "value"),
        Input(DATE_PICKER_ID, "date"),
        Input("rates-revision", "data"),
    )
    def _update_content(source_value, as_of_date, rates_revision=None):
        if not as_of_date:
            return message_box("No as-of date available.")

        # Local import: keeps this module importable even if ui.app changes shape.
        from ui.app import connect_readonly

        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc}).")

        try:
            sections = [
                html.H4("Ours vs BNP, per instrument"),
                _break_section(conn, as_of_date),
                html.Hr(),
                html.H4("Workbook (HA-portfolio vJean.xlsx), unchanged formulas"),
                _workbook_section(conn, as_of_date, source_value),
            ]
        finally:
            conn.close()
        return html.Div(sections)

    def _break_section(conn, as_of_date):
        try:
            from engine.pnl.valuation import value_book
        except ImportError as exc:
            return message_box(f"value_book not available yet ({exc}).")
        bnp = pd.read_sql_query(
            "SELECT instrument_id, mv_usd, pnl_dtd_usd FROM positions "
            "WHERE source = 'BNP' AND as_of_date = ?", conn, params=[as_of_date],
        )
        vb = value_book(conn, as_of_date)
        daily_by_instrument = {}
        try:
            from engine.pnl.ledger import period_pnl_by
            by_instrument = period_pnl_by(conn, as_of_date, "instrument_id")
            daily_by_instrument = {
                instrument_id: periods["daily"]["value"]
                for instrument_id, periods in by_instrument.items()
            }
        except ImportError:
            pass  # ours_daily_usd column left blank; period_pnl_by not available yet
        return html.Div([
            html.P("break_usd = our LTD P&L on OPEN trades minus BNP's mv_usd (BNP's own "
                   "unrealised LTD for an open forward): like-with-like. "
                   "'unpriced' counts our open rows with a missing mark, excluded from "
                   "ours_ltd_usd. bnp_pnl_dtd_usd and ours_daily_usd are informational only, "
                   "not part of the break."),
            break_table_from_frames(bnp, vb, daily_by_instrument),
        ])

    def _workbook_section(conn, as_of_date, source_value):
        try:
            from engine.pnl.pnl import ltd_per_trade
            from engine.pnl.aggregate import aggregate_by_pair, book_totals, period_pnl
        except ImportError as exc:
            return message_box(f"Workbook P&L engine not available yet ({exc}).")

        param_source = source_value_to_param(source_value)
        per_trade = ltd_per_trade(conn, as_of_date, source=param_source, strict=False)
        by_pair = aggregate_by_pair(per_trade, conn)
        totals = book_totals(by_pair, conn)
        period = period_pnl(conn, as_of_date, source=param_source)

        rollup = html.Div([
            html.P("HA-portfolio vJean formulas, applied to recorded trades. Missing inputs are "
                   "blank, not zero. This is the All FX trades calculation; the Portfolio sheet "
                   "has additional product inputs and manual adjustments."),
            html.P(f"Shared FX valuation date: {period.get('valuation_date', '')}"),
            html.H5("P&L by pair"),
            pairs_table_from_by_pair(by_pair),
            html.H5("Book totals"),
            message_box(totals.get("status", "")),
            totals_table_from_book_totals(totals),
            html.H5("Period P&L"),
            html.P("Daily follows All FX trades M4. 5d, MTD and YTD remain unavailable because "
                   "this workbook does not define those calculations."),
            period_table_from_period_pnl(period),
        ])

        try:
            from engine.ladder.valuation import ladder_trade_valuation, ladder_valuation_summary
            detail = ladder_trade_valuation(conn, as_of_date, param_source)
            summary = ladder_valuation_summary(detail)
        except ImportError as exc:
            return html.Div([rollup, message_box(f"Workbook mark-to-market not available yet ({exc}).")])

        missing = int(detail["pnl_usd"].isna().sum()) if not detail.empty else 0
        status = (f"USD P&L incomplete: {missing} of {len(detail)} open forwards lack workbook pricing."
                  if missing else f"{len(detail)} open forwards priced using workbook formulas.")
        mtm = html.Div([
            html.H5("Currency and settlement date: workbook P&L"),
            html.P(status),
            html.P("Signed USD entry + workbook USD valuation = USD P&L. Rates are shown "
                   "separately for each trade below. Cash balances are excluded from P&L. "
                   "NDF rows show notionals, not gross cash settlement amounts."),
            valuation_table(summary, "reconciliation-valuation-summary"),
            html.H5("Trade calculation detail"),
            valuation_table(detail, "reconciliation-valuation-detail"),
        ])
        return html.Div([rollup, mtm])
