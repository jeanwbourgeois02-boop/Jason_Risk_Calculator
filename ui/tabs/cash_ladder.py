"""Cash ladder tab: source/date controls + a DataTable rendering a transposed view of
`engine.ladder.views.ladder_table`.

`engine/ladder/views.py` (owned by cash-ladder) is expected to expose
`ladder_table(conn, as_of_date, source=None) -> DataFrame` with columns `ccy`, one
column per ISO settle-date string (ascending), `total`, `usd`. That module may not exist
yet / may still be in flux while this file is written, so it is imported lazily *inside*
the callback (never at module import time) and wrapped in try/except ImportError so
`import ui.app` and `import ui.tabs.cash_ladder` always succeed regardless of whether
engine/ladder/views.py is present.

Display is a TRANSPOSE of `ladder_table`'s shape (`ladder_table` itself is untouched --
we do not own it and its contract, "one row per currency", is unchanged):
  - Rows: settle dates ascending, plus a `Total` row at the bottom (from `ladder_table`'s
    `total` / `usd` columns).
  - Columns: one per currency, ordered by |usd| descending first (currencies with a
    defined `usd`), then currencies with `usd` = NaN afterwards sorted alphabetically --
    the same order `ladder_table` already returns its rows in; `transpose_ladder`
    recomputes this order itself so it stays correct even when fed a hand-built frame
    that is not pre-sorted. Plus a `usd_equivalent` column on the right.
  - `usd_equivalent` per date row = sum over currencies of (amount on that date x that
    currency's implied spot), where implied spot for a currency = `usd / total` from the
    ladder frame (this is exact, since `ladder_table` computes `usd` as `total x spot`).
    BLANK RULE (documented here since it is a judgement call, not in the CLAUDE.md
    contract): the `usd_equivalent` cell for a date is blank if either (a) any currency
    with a non-zero, non-NaN amount on that date has no defined spot (its `usd` is NaN),
    or (b) any currency with a non-zero, non-NaN amount on that date has `total == 0`
    (spot cannot be recovered from 0/0 even though `usd` may itself be defined as 0 in
    that degenerate case), or (c) the amount itself is NaN (unknown flow, not "no flow" --
    distinct from the explicit-zero convention `ladder_table` otherwise guarantees).
    Currencies with a zero or NaN amount on that date never block the sum. The `Total`
    row's `usd_equivalent` uses the same rule against the `total` / `usd` columns and
    equals the sum of the `usd` column when defined.

Number formatting (`ui.tabs.formatting.format_cell` / `format_frame`): every non-label
cell is rounded to whole units and rendered with thousands separators, negatives in
parentheses (e.g. -1234567.8 -> "(1,234,568)"), NaN/None -> "" (blank). This is a
display-only transform; storage/precision live entirely in engine/ and data/, per
CLAUDE.md ("ui/ ... never recomputes P&L or delta itself").
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

import pandas as pd
from dash import Input, Output, dash_table, dcc, html

from ui.tabs.controls import build_date_picker
from ui.tabs.formatting import format_cell, format_frame as format_ladder_frame

DATE_PICKER_ID = "cash-ladder-date"
TABLE_CONTAINER_ID = "cash-ladder-table-container"
TOOLBAR_ID = "cash-ladder-toolbar"
SORT_ID = "cash-ladder-summary-sort"   # value: 'top' | 'all' (summary scope)
STATUS_ID = "cash-ladder-status"
REFRESH_ID = "cash-ladder-refresh"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS

TRANSPOSED_LABEL_COL = "settle_date"
USD_EQUIVALENT_COL = "usd_equivalent"


def _ccy_order(df: pd.DataFrame) -> list:
    """Currency order: |usd| descending first (usd defined), then alphabetical (usd
    NaN) -- same rule as `ladder_table`'s own row order, recomputed here so this
    function is correct even for a hand-built frame that is not pre-sorted."""
    has_usd = df["usd"].notna()
    with_usd = (
        df[has_usd]
        .assign(_abs_usd=lambda d: d["usd"].abs())
        .sort_values("_abs_usd", ascending=False, kind="mergesort")
        .drop(columns="_abs_usd")
    )
    without_usd = df[~has_usd].sort_values("ccy", kind="mergesort")
    return pd.concat([with_usd, without_usd], ignore_index=True)["ccy"].tolist()


def _implied_spot(total: float, usd: float) -> float:
    """usd / total, i.e. the per-unit rate implied by `ladder_table`'s own `usd = total
    x spot` computation. NaN if usd is undefined or total is 0 (0/0 is not recoverable
    even where usd happens to be defined as 0 in that degenerate case)."""
    if pd.isna(usd):
        return float("nan")
    if total == 0:
        return float("nan")
    return usd / total


def transpose_ladder(df: pd.DataFrame) -> pd.DataFrame:
    """Pure transform: `ladder_table`'s (ccy, dates..., total, usd) frame ->
    (settle_date, currencies..., usd_equivalent), rows = dates ascending + a `Total`
    row. See module docstring for the exact column order and the `usd_equivalent`
    blank rule."""
    if df.empty or "ccy" not in df.columns:
        return pd.DataFrame(columns=[TRANSPOSED_LABEL_COL, USD_EQUIVALENT_COL])

    date_cols = sorted(c for c in df.columns if c not in ("ccy", "total", "usd"))
    ccy_order = _ccy_order(df)

    by_ccy = df.set_index("ccy")
    spot = {
        ccy: _implied_spot(by_ccy.loc[ccy, "total"], by_ccy.loc[ccy, "usd"])
        for ccy in ccy_order
    }

    def usd_equivalent(amounts: dict) -> float:
        total = 0.0
        for ccy, amount in amounts.items():
            if amount is None or pd.isna(amount):
                return float("nan")
            if amount == 0:
                continue
            s = spot[ccy]
            if pd.isna(s):
                return float("nan")
            total += amount * s
        return total

    rows = []
    for d in date_cols:
        amounts = {ccy: by_ccy.loc[ccy, d] for ccy in ccy_order}
        row = {TRANSPOSED_LABEL_COL: d, **amounts}
        row[USD_EQUIVALENT_COL] = usd_equivalent(amounts)
        rows.append(row)

    totals = {ccy: by_ccy.loc[ccy, "total"] for ccy in ccy_order}
    total_row = {TRANSPOSED_LABEL_COL: "Total", **totals}
    total_row[USD_EQUIVALENT_COL] = usd_equivalent(totals)
    rows.append(total_row)

    columns = [TRANSPOSED_LABEL_COL] + ccy_order + [USD_EQUIVALENT_COL]
    return pd.DataFrame(rows, columns=columns)


def table_from_ladder(
    df: pd.DataFrame,
    label_col: str = "ccy",
    table_id: str = "cash-ladder-datatable",
) -> dash_table.DataTable:
    """Build the DataTable component from an already-fetched (or already-transposed)
    ladder DataFrame. Pure function of the frame -- does not touch the DB -- so it is
    unit-testable with a hand-built frame."""
    formatted = format_ladder_frame(df, label_col=label_col)
    columns = [{"name": col, "id": col} for col in formatted.columns]
    return dash_table.DataTable(
        id=table_id,
        columns=columns,
        data=formatted.to_dict("records"),
        style_cell={"textAlign": "right", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def message_box(message: str) -> html.P:
    """Grey status text shown in the table container instead of a DataTable (missing
    view module, missing DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Controls + an (initially empty) table container for the Cash ladder tab. The
    table itself is filled in by the callback registered in register_callbacks."""
    return html.Div(className="cash-ladder", children=[
        html.H3("Ladder"),
        html.P("What am I long/short and when is it cash?", className="section-kicker"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
            html.Div(className="toolbar-group", children=[
                html.Label("Currency order"),
                dcc.RadioItems(id=SORT_ID, value="usd", inline=True,
                               options=[{"label": "|USD delta|", "value": "usd"},
                                        {"label": "A-Z", "value": "alpha"}]),
            ]),
            html.Div(className="toolbar-group toolbar-group--exposure", children=[
                html.Label("Status"),
                html.Div(id=STATUS_ID, className="toolbar-static",
                         children="Loading..."),
            ]),
        ]),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        html.Div(id=TABLE_CONTAINER_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callback that re-queries `ladder_table` / the exposure delta view
    whenever the date picker or sort control changes.

    `get_db_path` is a zero-arg callable returning the resolved DB path (typically
    `ui.app.get_db_path`, or a closure over the path `create_app` resolved for an
    explicit `db_path` override) -- passed in rather than imported at module scope so
    tests can supply a stub without touching the real DB / env / RISK_DB.

    2026-09-15 (docs/BUILD_PLAN.md Task C split): the workbook mark-to-market panel,
    ledger cards, Exposure P&L card, the workbook FX rates grid and the Bloomberg
    diagnostics/pull-now controls are REMOVED from this tab (they move to the
    Reconciliation and Market data tabs). This callback no longer takes
    `SOURCE_DROPDOWN_ID` / `rates-revision` / a pull-now revision as inputs.
    """

    @app.callback(
        Output(TABLE_CONTAINER_ID, "children"),
        Output(STATUS_ID, "children"),
        Input(DATE_PICKER_ID, "date"),
        Input(SORT_ID, "value"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update_table(as_of_date, sort="usd", _n_intervals=0):
        """Returns (tab body, toolbar status text). Re-runs every REFRESH_MS so the ladder
        follows the 2-minute Bloomberg feed (data.bloomberg.live)."""
        return _render(as_of_date, sort or "usd")

    def _render(as_of_date, sort):
        toolbar_status = "Status unknown"
        if not as_of_date:
            return message_box("No as-of date available."), toolbar_status

        try:
            from engine.ladder.views import ladder_table
        except ImportError as exc:
            return message_box(f"Cash ladder view not available yet ({exc}).", ), toolbar_status

        # Local import: keeps this module importable even if ui.app changes shape.
        from ui.app import connect_readonly

        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc})."), toolbar_status
        try:
            df = ladder_table(conn, as_of_date, None)
            # Delta view (docs/BUILD_PLAN.md section 5, "Ladder"): local delta, spot, USD
            # delta rows, Net/Gross, futures delta line, stress block -- from
            # engine.ladder.exposure via records_from_db. Never P&L.
            try:
                from engine.ladder.exposure_adapter import records_from_db
                from data.bloomberg.live import rates_from_marks
                from ui.tabs.exposure import BOOK_DISPLAY, exposure_section, rate_status_text
                from engine.ladder.exposure import build_exposure
                records, unresolved = records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
                # Rates: latest official SPOT marks written by the Bloomberg feed. Never mock.
                rates = rates_from_marks(conn)
                # Futures USD delta: no engine query exists yet for open-futures USD
                # delta (docs/BUILD_PLAN.md section 4 names it as a stress input without
                # specifying the source); pass None through so it renders Unavailable
                # rather than a fabricated zero. See report to C5 / cash-ladder.
                exposure = exposure_section(records, unresolved, as_of_date, rates=rates,
                                            sort=sort, futures_usd_delta=None)
                books = ", ".join(sorted({r["book"] for r in records})) or "none"
                result = build_exposure(records, rates)
                toolbar_status = f"Book {books} · {rate_status_text(result)}"
            except ImportError as exc:
                exposure = message_box(f"Exposure ladder not available ({exc}).")
        finally:
            conn.close()
        transposed = transpose_ladder(df)
        return html.Div([
            exposure,
            html.Details(className="section section--secondary details", open=False, children=[
                html.Summary("Cash settlement amounts and current balances"),
                html.P("Settled-cash legs and BNP cash balances at this as-of date, transposed date x "
                       "currency. USD equivalent uses spot and is a cash value, not P&L."),
                table_from_ladder(transposed, label_col=TRANSPOSED_LABEL_COL),
            ]),
        ]), toolbar_status
