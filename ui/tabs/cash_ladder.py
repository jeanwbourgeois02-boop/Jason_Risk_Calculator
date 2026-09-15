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
REFRESH_ID = "cash-ladder-refresh"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS

BNP_BVAL_SOURCE_LABEL = "BNP file rate (not Bloomberg)"

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


def bnp_bval_rates(conn: sqlite3.Connection, as_of_date: str) -> dict:
    """currency -> {rate, inverted, source, timestamp, stale} from the BNP file's own
    BVAL SPOT marks (user decision 2026-09-15, item D). Read from `marks` directly with
    `source = 'BNP_BVAL'` (never `marks_official`: BNP_BVAL is reconciliation-only per
    CLAUDE.md "Official marks") for `as_of_date`, falling back to the LATEST such mark
    on or before it. This exists only to fill the delta/ladder display when Bloomberg
    has not written an official SPOT for a currency; it must never be used for P&L
    (this tab has none) and never overrides an official rate -- the caller only
    consults this for currencies missing from `rates_from_marks`."""
    sql = (
        "SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.as_of_date "
        "FROM marks m JOIN instruments i USING (instrument_id) "
        "WHERE m.mark_type = 'SPOT' AND m.source = 'BNP_BVAL' AND i.asset_class = 'FX' "
        "AND m.as_of_date <= ? ORDER BY m.instrument_id, m.as_of_date"
    )
    latest: dict = {}
    for pair, base, quote, value, snapshot_date in conn.execute(sql, [as_of_date]):
        if "USD" not in (base, quote):
            continue
        latest[pair] = (base, quote, value, snapshot_date)  # ascending as_of_date: last wins
    out = {}
    for pair, (base, quote, value, snapshot_date) in latest.items():
        ccy = quote if base == "USD" else base
        out[ccy] = {"rate": float(value), "inverted": base == "USD",
                    "source": BNP_BVAL_SOURCE_LABEL, "timestamp": snapshot_date, "stale": False}
    return out


def message_box(message: str) -> html.P:
    """Grey status text shown in the table container instead of a DataTable (missing
    view module, missing DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Ladder tab shell (user decision 2026-09-15, items A/C): only the as-of date
    picker plus an (initially empty) table container. No other controls, no dropdowns
    -- sort/scope toolbars, snapshot cards, metadata, legend and alternative views are
    removed outright by `ui.tabs.exposure.exposure_section`, not moved here."""
    return html.Div(className="cash-ladder", children=[
        html.H3("Ladder"),
        html.P("What am I long/short and when is it cash?", className="section-kicker"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
        ]),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        html.Div(id=TABLE_CONTAINER_ID),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callback that re-renders the Ladder tab body whenever the date
    picker changes (this tab has no other controls -- user decision 2026-09-15, item C).

    `get_db_path` is a zero-arg callable returning the resolved DB path (typically
    `ui.app.get_db_path`, or a closure over the path `create_app` resolved for an
    explicit `db_path` override) -- passed in rather than imported at module scope so
    tests can supply a stub without touching the real DB / env / RISK_DB.

    2026-09-15 (docs/BUILD_PLAN.md Task C split, tightened same day): the workbook
    mark-to-market panel, ledger cards, Exposure P&L card, the workbook FX rates grid,
    the Bloomberg diagnostics/pull-now controls, the sort/scope toolbar and the
    settlement-cash transpose table are all REMOVED from this tab outright (not moved
    into a collapsed section). The body is exactly `exposure_section`'s three headline
    numbers and three tables. `engine.ladder.views.ladder_table` /
    `transpose_ladder` are kept in this module only for their own unit tests.
    """

    @app.callback(
        Output(TABLE_CONTAINER_ID, "children"),
        Input(DATE_PICKER_ID, "date"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update_table(as_of_date, _n_intervals=0):
        """Re-runs every REFRESH_MS so the ladder follows the 2-minute Bloomberg feed
        (data.bloomberg.live)."""
        return _render(as_of_date)

    def _render(as_of_date):
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
            # Delta view (docs/BUILD_PLAN.md section 5, "Ladder"): local delta, spot, USD
            # delta rows, Net/Gross, futures delta line, stress block -- from
            # engine.ladder.exposure via records_from_db. Never P&L.
            try:
                from engine.ladder.exposure_adapter import records_from_db
                from data.bloomberg.live import rates_from_marks
                from ui.tabs.exposure import BOOK_DISPLAY, exposure_section
                records, unresolved = records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
                # Rates: latest official SPOT marks written by the Bloomberg feed, first
                # choice. Currencies still missing a rate fall back to the BNP file's own
                # BVAL SPOT (user decision 2026-09-15, item D) -- never silently, and
                # never for P&L (this tab has none).
                rates = rates_from_marks(conn)
                needed = {r["currency"] for r in records}
                missing = needed - set(rates)
                fallback_ccys = set()
                if missing:
                    fallback = bnp_bval_rates(conn, as_of_date)
                    for ccy in missing:
                        if ccy in fallback:
                            rates[ccy] = fallback[ccy]
                            fallback_ccys.add(ccy)
                # Futures USD delta: engine.ladder.futures_delta.futures_usd_delta (C5
                # wiring). The full dict (value/by_instrument/missing/reason) is passed
                # through so exposure_section's combined risk table and futures block can
                # render a per-instrument Unavailable with the engine's own reason,
                # rather than a single fabricated zero.
                from engine.ladder.futures_delta import futures_usd_delta as _futures_usd_delta
                _fut = _futures_usd_delta(conn, as_of_date)
                exposure = exposure_section(records, unresolved, as_of_date, rates=rates,
                                            futures=_fut, futures_details=(_fut or {}).get("details"),
                                            fallback_ccys=fallback_ccys)
            except ImportError as exc:
                exposure = message_box(f"Exposure ladder not available ({exc}).")
        finally:
            conn.close()
        return html.Div([exposure])
