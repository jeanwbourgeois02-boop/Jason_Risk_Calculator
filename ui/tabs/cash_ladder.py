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

import datetime as dt
import sqlite3
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from dash import Input, Output, dash_table, dcc, html

from ui.tabs.controls import build_date_picker
from ui.tabs.formatting import format_cell, format_frame as format_ladder_frame

DATE_PICKER_ID = "cash-ladder-date"
TABLE_CONTAINER_ID = "cash-ladder-table-container"
TOOLBAR_ID = "cash-ladder-toolbar"
TITLE_ID = "cash-ladder-title"
TODAY_BUTTON_ID = "cash-ladder-today"
REFRESH_ID = "cash-ladder-refresh"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")

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


FORWARD_PROXY_SOURCE_LABEL = "BNP forward proxy"


def bnp_forward_proxy_rates(conn: sqlite3.Connection, as_of_date: str, needed: set) -> dict:
    """currency -> {rate, inverted, source, timestamp, stale} from the BNP file's own
    BVAL FORWARD outright, used as a spot proxy (coordinator addition 2026-09-15, item
    5): BNP's file carries no SPOT for AUD/EUR/GBP/XAU (its Fx is 1 on those rows), so
    `bnp_bval_rates` alone leaves every XXXUSD currency MISSING. For each currency still
    in `needed` after the SPOT fallback, take the EARLIEST settle_date FWD_OUTRIGHT mark
    (source BNP_BVAL) from the most recent snapshot on or before `as_of_date` -- the
    nearest-dated forward is the closest available proxy for spot. Labelled distinctly
    (`FORWARD_PROXY_SOURCE_LABEL`) so the caller can report it separately from the
    plain BNP_BVAL SPOT fallback, never silently blended into "Bloomberg" or even into
    the SPOT-fallback count."""
    if not needed:
        return {}
    sql = (
        "SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.settle_date, m.as_of_date "
        "FROM marks m JOIN instruments i USING (instrument_id) "
        "WHERE m.mark_type = 'FWD_OUTRIGHT' AND m.source = 'BNP_BVAL' AND i.asset_class = 'FX' "
        "AND m.as_of_date <= ? "
        "ORDER BY m.instrument_id, m.as_of_date DESC, m.settle_date ASC"
    )
    best: dict = {}  # pair -> (base, quote, value, settle_date, snapshot_date); first row per pair wins (best order)
    for pair, base, quote, value, settle_date, snapshot_date in conn.execute(sql, [as_of_date]):
        if pair in best:
            continue
        if "USD" not in (base, quote):
            continue
        ccy = quote if base == "USD" else base
        if ccy not in needed:
            continue
        best[pair] = (base, quote, value, settle_date, snapshot_date)
    out = {}
    for base, quote, value, settle_date, snapshot_date in best.values():
        ccy = quote if base == "USD" else base
        out[ccy] = {"rate": float(value), "inverted": base == "USD",
                    "source": FORWARD_PROXY_SOURCE_LABEL, "timestamp": snapshot_date, "stale": False}
    return out


def net_gross_usd(conn: sqlite3.Connection, as_of_date: str) -> dict:
    """FX-only Net USD / Gross USD (CLAUDE.md "Net USD (FX only)" / "Gross USD"), via the
    exact same records/rates path `_render` uses for the Ladder tab (`records_from_db`
    + `rates_from_marks`, with the BNP_BVAL SPOT fallback then the forward-outright
    proxy) -- so a header figure for a given as_of always matches what the Ladder tab
    would show. Coordinator addition 2026-09-15 (header compaction) so `ui.tabs.header`
    does not need its own copy of this fallback chain. Returns
    `{available, net, gross, reason, fallback_ccys, forward_proxy_ccys}`; `net`/`gross`
    are only present when `available`."""
    from engine.ladder.exposure_adapter import exposure_records_from_db
    from engine.ladder.exposure import build_exposure, portfolio_totals
    from data.bloomberg.live import rates_from_marks

    records, _unresolved = exposure_records_from_db(conn, as_of_date)
    rates = rates_from_marks(conn)
    needed = {r["currency"] for r in records}
    missing = needed - set(rates)
    fallback_ccys = set()
    if missing:
        fb = bnp_bval_rates(conn, as_of_date)
        for ccy in missing:
            if ccy in fb:
                rates[ccy] = fb[ccy]
                fallback_ccys.add(ccy)
    still_missing = needed - set(rates)
    forward_proxy_ccys = set()
    if still_missing:
        proxy = bnp_forward_proxy_rates(conn, as_of_date, still_missing)
        for ccy in still_missing:
            if ccy in proxy:
                rates[ccy] = proxy[ccy]
                forward_proxy_ccys.add(ccy)
    result = build_exposure(records, rates)
    totals = portfolio_totals(result)
    if totals["missing"]:
        return {"available": False, "reason": "no rate: " + ", ".join(sorted(totals["missing"])),
                "fallback_ccys": fallback_ccys, "forward_proxy_ccys": forward_proxy_ccys}
    return {"available": True, "net": totals["net_usd"], "gross": totals["gross_usd"],
            "reason": "", "fallback_ccys": fallback_ccys, "forward_proxy_ccys": forward_proxy_ccys}


def today_ny() -> str:
    """Today's date (ISO) in America/New_York -- the ladder's as-of default (2026-09-15
    coordinator addition): open trades are trade_date <= today <= settle_date, evaluated
    against "now" rather than the last BNP snapshot, so the ladder shows what is still
    outstanding today even between uploads."""
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def heading_date_text(iso: Optional[str]) -> str:
    """'2026-09-15' -> 'Monday 15 September 2026' (coordinator addition 2026-09-15,
    item 2 of the Ladder-tab title row); an unparseable/missing date falls back to a
    plain placeholder rather than raising, since this also runs before any date is
    picked."""
    if not iso:
        return "As of - no date selected"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return f"As of {iso}"
    return f"{_WEEKDAY_NAMES[d.weekday()]} {d.day} {_MONTH_NAMES[d.month - 1]} {d.year}"


def message_box(message: str) -> html.P:
    """Grey status text shown in the table container instead of a DataTable (missing
    view module, missing DB, no as_of date, etc)."""
    return html.P(message, style={"color": "gray"})


def reconciliation_panel(conn: sqlite3.Connection, as_of_date: str) -> html.Details:
    """EOD blotter-vs-BNP cross-check (user decision 2026-09-16: the blotter is the
    real-time primary trade source, BNP is the once-daily EOD-Hong-Kong snapshot of
    the same book -- they should agree by end of day). New, purpose-built check, not
    a resurrection of the retired workbook-parity Reconciliation tab -- see
    `engine.pnl.reconcile`'s module docstring for the distinction.

    Collapsed by default (`html.Details` with no `open`), since a clean book (the
    common case) needs nothing more than the one-line summary in its `<summary>`; a
    mismatch is still one click away, not buried.

    Always compares through `engine.pnl.reconcile.bnp_snapshot_date`, not `as_of_date`
    directly -- BNP lags the real-time blotter by at least a day, so the summary names
    the actual date compared (which can be earlier than the Ladder tab's selected
    date) rather than implying today's blotter trades were checked and found clean
    when they simply were not in scope yet."""
    from engine.pnl.reconcile import bnp_snapshot_date, reconcile_blotter_vs_bnp

    snapshot_date = bnp_snapshot_date(conn, as_of_date)
    if snapshot_date is None:
        return html.Details(className="details details--compact", children=[
            html.Summary("EOD reconciliation: blotter vs BNP -- no BNP snapshot loaded yet."),
        ])
    lag_note = "" if snapshot_date == as_of_date else f" (as of BNP's {snapshot_date} snapshot)"
    df = reconcile_blotter_vs_bnp(conn, as_of_date)
    if df.empty:
        return html.Details(className="details details--compact", children=[
            html.Summary(f"EOD reconciliation: blotter vs BNP -- no trades on either side yet{lag_note}."),
        ])
    mismatches = df[~df["within_tolerance"]]
    n_ok, n_total = len(df) - len(mismatches), len(df)
    if mismatches.empty:
        return html.Details(className="details details--compact", children=[
            html.Summary(f"EOD reconciliation: blotter vs BNP -- {n_ok}/{n_total} instruments agree{lag_note}."),
        ])
    display_cols = ["instrument_id", "blotter_usd_notional", "bnp_usd_notional", "diff_usd",
                     "n_blotter_trades", "n_bnp_trades"]
    table = format_ladder_frame(mismatches[display_cols], label_col="instrument_id")
    return html.Details(className="details details--compact", open=True, children=[
        html.Summary(
            f"EOD reconciliation: blotter vs BNP -- {n_ok}/{n_total} agree, "
            f"{len(mismatches)} outside tolerance{lag_note}."
        ),
        dash_table.DataTable(
            id="cash-ladder-reconciliation-table",
            columns=[{"name": c.replace("_", " ").title(), "id": c} for c in table.columns],
            data=table.to_dict("records"),
            style_cell={"textAlign": "right", "fontFamily": "monospace"},
            style_header={"fontWeight": "bold"},
        ),
    ])


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Ladder tab shell (user decision 2026-09-15, items A/C; title row added by the
    coordinator's same-day follow-up, item 2): a heading naming the as-of date in full
    ("Monday 15 September 2026"), the date picker beside it, and a "Today" button that
    resets the picker (and so the header store, which mirrors this picker) to today's
    America/New_York date -- plus an (initially empty) table container. No other
    controls, no dropdowns -- sort/scope toolbars, snapshot cards, metadata, legend and
    alternative views are removed outright by `ui.tabs.exposure.exposure_section`, not
    moved here."""
    return html.Div(className="cash-ladder", children=[
        html.Div(id=TOOLBAR_ID, className="ladder-title-row", children=[
            html.H3("Cash ladder", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(heading_date_text(default_date), id=TITLE_ID, className="section-title"),
                build_date_picker(DATE_PICKER_ID, default_date=default_date),
                html.Button("Today", id=TODAY_BUTTON_ID, n_clicks=0, className="btn"),
            ]),
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

    @app.callback(Output(TITLE_ID, "children"), Input(DATE_PICKER_ID, "date"))
    def _update_title(as_of_date):
        return heading_date_text(as_of_date)

    @app.callback(
        Output(DATE_PICKER_ID, "date", allow_duplicate=True),
        Input(TODAY_BUTTON_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _jump_to_today(_n_clicks):
        return today_ny()

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
                from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db
                from data.bloomberg.live import rates_from_marks
                from ui.tabs.exposure import BOOK_DISPLAY, exposure_section
                records, unresolved = records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
                # Delta/exposure math (Net/Gross headline, risk-and-scenarios table) must
                # use settle_date > as_of, not >= -- a leg settling today carries no delta
                # by close (CLAUDE.md "Six tabs as views"). The grid above stays on
                # records (>=): today's settling leg is still cash that moves today.
                exposure_records, exposure_unresolved = exposure_records_from_db(
                    conn, as_of_date, book_mapping=BOOK_DISPLAY)
                # 2026-09-16 fix: exposure_unresolved was previously discarded here. A
                # trade unresolved only under the exposure calc's settle_date > as_of
                # rule (not the grid's >= rule) -- e.g. a trade whose sole leg settles
                # exactly on as_of -- could silently affect Net/Gross USD with no
                # "Unresolved trades" caption at all, since that caption only ever read
                # the grid's own `unresolved`. Merge both, deduped by trade_id (the
                # grid's own entry for a trade wins if it appears in both, since the
                # two lists usually share the same reason for the same trade_id).
                seen_trade_ids = {u.trade_id for u in unresolved}
                unresolved = list(unresolved) + [
                    u for u in exposure_unresolved if u.trade_id not in seen_trade_ids
                ]
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
                # Still-missing currencies (item 5, coordinator addition): BNP carries no
                # SPOT for AUD/EUR/GBP/XAU (Fx = 1 on those rows), so fall back further to
                # the earliest-settle_date BNP_BVAL forward outright as a spot proxy.
                # Tracked in its own set so the caller can report it distinctly from the
                # plain SPOT fallback, never blended into one count.
                still_missing = needed - set(rates)
                forward_proxy_ccys = set()
                if still_missing:
                    proxy = bnp_forward_proxy_rates(conn, as_of_date, still_missing)
                    for ccy in still_missing:
                        if ccy in proxy:
                            rates[ccy] = proxy[ccy]
                            forward_proxy_ccys.add(ccy)
                # Futures USD delta: engine.ladder.futures_delta.futures_usd_delta (C5
                # wiring). The full dict (value/by_instrument/missing/reason) is passed
                # through so exposure_section's combined risk table and futures block can
                # render a per-instrument Unavailable with the engine's own reason,
                # rather than a single fabricated zero.
                from engine.ladder.futures_delta import futures_usd_delta as _futures_usd_delta
                _fut = _futures_usd_delta(conn, as_of_date)
                exposure = exposure_section(records, unresolved, as_of_date, rates=rates,
                                            futures=_fut, futures_details=(_fut or {}).get("details"),
                                            fallback_ccys=fallback_ccys,
                                            forward_proxy_ccys=forward_proxy_ccys,
                                            exposure_records=exposure_records)
            except ImportError as exc:
                exposure = message_box(f"Exposure ladder not available ({exc}).")
        finally:
            conn.close()
        # `reconciliation_panel` (blotter vs BNP EOD check) is no longer rendered: BNP was
        # removed as an input on 2026-09-17, so the panel could only ever say "no BNP
        # snapshot loaded yet". The function is kept (tests, and a future second source).
        return html.Div([exposure])
