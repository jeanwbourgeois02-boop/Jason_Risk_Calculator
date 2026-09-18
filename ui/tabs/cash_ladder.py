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
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs.controls import build_date_picker
from ui.tabs.formatting import format_cell, format_frame as format_ladder_frame

DATE_PICKER_ID = "cash-ladder-date"
TABLE_CONTAINER_ID = "cash-ladder-table-container"
TOOLBAR_ID = "cash-ladder-toolbar"
TITLE_ID = "cash-ladder-title"
TODAY_BUTTON_ID = "cash-ladder-today"
REFRESH_ID = "cash-ladder-refresh"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS
# View controls (user's cash-ladder spec, 2026-09-18, "Cash ladder tab"): currency
# multiselect, From/To value dates, "Settled dates one by one", "Show table in USD
# equivalent", and the two downloads. They shape the grid only (ui.tabs.exposure.
# LadderView); the headline card and the risk table always show the whole book.
FILTERS_ID = "cash-ladder-filters"
CCY_FILTER_ID = "cash-ladder-ccy-filter"
DATE_RANGE_ID = "cash-ladder-date-range"
SETTLED_TOGGLE_ID = "cash-ladder-settled-one-by-one"
USD_TOGGLE_ID = "cash-ladder-show-usd"
DOWNLOAD_LADDER_BTN_ID = "cash-ladder-download-ladder"
DOWNLOAD_LEGS_BTN_ID = "cash-ladder-download-legs"
DOWNLOAD_LADDER_ID = "cash-ladder-download-ladder-file"
DOWNLOAD_LEGS_ID = "cash-ladder-download-legs-file"
TOGGLE_ON = "on"

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")

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


def net_gross_usd(conn: sqlite3.Connection, as_of_date: str) -> dict:
    """FX-only Net USD / Gross USD (CLAUDE.md "Net USD (FX only)" / "Gross USD"), via the
    exact same records/rates path `_render` uses for the Ladder tab
    (`exposure_records_from_db` + `rates_from_marks`, OFFICIAL SPOT marks only) -- so a
    header figure for a given as_of always matches what the Ladder tab would show.
    Coordinator addition 2026-09-15 (header compaction) so `ui.tabs.header` does not need
    its own copy of this rates path. Returns
    `{available, net, gross, reason, fallback_ccys, forward_proxy_ccys, commodities}`;
    `net`/`gross` are only present when `available`.

    2026-09-17 ("no bnp fall back" -- user decision, `docs/bnp-excel-removal.md`): the
    BNP_BVAL SPOT fallback and the BNP forward-outright proxy (`bnp_bval_rates` /
    `bnp_forward_proxy_rates`, formerly here) are REMOVED outright, not merely unused --
    rates come from `rates_from_marks` (official SPOT, `marks_official`) only. A
    currency with no official SPOT stays missing; `reason` names it as
    "no official SPOT for <as_of_date>: <ccy, ...>", never substituted from any other
    source. `fallback_ccys`/`forward_proxy_ccys` are kept in the return shape as always-
    empty sets purely so a caller still destructuring those keys doesn't KeyError.

    Sign convention (unchanged 2026-09-17 audit -- verified correct, not touched): `net`
    is `engine.ladder.exposure.portfolio_totals`'s own `net_usd`, i.e. the net NON-USD
    delta (+ = long foreign currency), NOT the USD position. Every caller of this
    function (`ui/tabs/header.py::_build_figures`, `ui/tabs/exposure.py::
    headline_numbers` / `combined_risk_table`) negates it themselves before display as
    "Net USD, + = long USD" -- do not negate it here too, or every caller's own negation
    would silently cancel out back to the wrong (non-USD) sign.

    `commodities` (2026-09-17, "the XAU does not work well"): passed straight through
    from `portfolio_totals` -- gold/metals (XAU etc.) are already excluded from `net`/
    `gross` by `portfolio_totals` itself (CLAUDE.md: FX Net/Gross excludes gold), this
    is just so a future header card can show "+ $X gold" alongside "Net USD" without
    re-deriving it. Present (possibly empty) in both the available and unavailable
    branches."""
    from engine.ladder.exposure_adapter import exposure_records_from_db
    from engine.ladder.exposure import build_exposure, portfolio_totals
    from data.bloomberg.live import rates_from_marks

    records, _unresolved = exposure_records_from_db(conn, as_of_date)
    rates = rates_from_marks(conn)
    result = build_exposure(records, rates)
    totals = portfolio_totals(result)
    commodities = totals.get("commodities", [])
    if totals["missing"]:
        reason = f"no official SPOT for {as_of_date}: " + ", ".join(sorted(totals["missing"]))
        return {"available": False, "reason": reason,
                "fallback_ccys": set(), "forward_proxy_ccys": set(), "commodities": commodities}
    return {"available": True, "net": totals["net_usd"], "gross": totals["gross_usd"],
            "reason": "", "fallback_ccys": set(), "forward_proxy_ccys": set(),
            "commodities": commodities}


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


def build_filters() -> html.Div:
    """The spec's view controls in one toolbar row: currencies, From/To value dates,
    the two toggles and the two downloads. Currency options are filled by a callback
    from the book on the selected as-of date."""
    return html.Div(id=FILTERS_ID, className="toolbar ladder-filters", children=[
        html.Div(className="toolbar-group", children=[
            html.Label("Currencies (grid only)"),
            dcc.Dropdown(id=CCY_FILTER_ID, options=[], value=[], multi=True, placeholder="All currencies",
                         style={"minWidth": "280px"}),
        ]),
        html.Div(className="toolbar-group", children=[
            html.Label("Value dates (unsettled rows)"),
            dcc.DatePickerRange(id=DATE_RANGE_ID, display_format="D MMM YYYY", clearable=True,
                                first_day_of_week=1, minimum_nights=0),
        ]),
        html.Div(className="toolbar-group", children=[
            html.Label("View"),
            dcc.Checklist(id=SETTLED_TOGGLE_ID, value=[],
                          options=[{"label": " Settled dates one by one", "value": TOGGLE_ON}]),
            dcc.Checklist(id=USD_TOGGLE_ID, value=[],
                          options=[{"label": " Show table in USD equivalent", "value": TOGGLE_ON}]),
        ]),
        html.Div(className="toolbar-group", children=[
            html.Label("Download"),
            html.Div(children=[
                html.Button("Ladder CSV", id=DOWNLOAD_LADDER_BTN_ID, n_clicks=0, className="btn"),
                html.Button("Legs CSV", id=DOWNLOAD_LEGS_BTN_ID, n_clicks=0, className="btn",
                            style={"marginLeft": "8px"}),
            ]),
            dcc.Download(id=DOWNLOAD_LADDER_ID),
            dcc.Download(id=DOWNLOAD_LEGS_ID),
        ]),
    ])


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Ladder tab shell (user decision 2026-09-15, items A/C; title row added by the
    coordinator's same-day follow-up, item 2): a heading naming the as-of date in full
    ("Monday 15 September 2026"), the date picker beside it, and a "Today" button that
    resets the picker (and so the header store, which mirrors this picker) to today's
    America/New_York date -- then (2026-09-18, user's cash-ladder spec) the view
    controls row (`build_filters`) and the table container. The sort/scope toolbars,
    snapshot cards, metadata, legend and alternative views retired on 2026-09-15 stay
    retired; the filters here are the spec's own, and they shape the grid only."""
    return html.Div(className="cash-ladder", children=[
        html.Div(id=TOOLBAR_ID, className="ladder-title-row", children=[
            html.H3("Cash ladder", className="ladder-title-row-heading"),
            html.Div(className="ladder-title-row-right", children=[
                html.H4(heading_date_text(default_date), id=TITLE_ID, className="section-title"),
                build_date_picker(DATE_PICKER_ID, default_date=default_date),
                html.Button("Today", id=TODAY_BUTTON_ID, n_clicks=0, className="btn"),
            ]),
        ]),
        build_filters(),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        html.Div(id=TABLE_CONTAINER_ID),
    ])


def view_from_controls(ccys=None, start=None, end=None, settled=None, usd=None):
    """LadderView from the raw control values (Dash hands dates as ISO strings, the
    checklists as lists of selected values)."""
    from ui.tabs.exposure import LadderView
    return LadderView(
        currencies=frozenset(ccys) if ccys else None,
        date_from=str(start)[:10] if start else None,
        date_to=str(end)[:10] if end else None,
        settled_one_by_one=bool(settled and TOGGLE_ON in settled),
        show_usd=bool(usd and TOGGLE_ON in usd),
    )


def load_inputs(conn: sqlite3.Connection, as_of_date: str) -> dict:
    """Everything the Ladder tab body needs for one as-of date, read once: grid records
    (`>=` rule) and exposure records (`>` rule) with their merged unresolved list,
    official SPOT rates, the per-(currency, value date) forward USD marks
    (engine.ladder.usd_marks, spec 2026-09-18), the futures delta dict and the per-pair
    Position frame. Raises ImportError if an engine module is missing (the caller
    reports it); everything else is the engine's own reasons/blanks, never a substitute."""
    from engine.ladder.exposure_adapter import records_from_db, exposure_records_from_db
    from engine.ladder.futures_delta import futures_usd_delta
    from engine.ladder.ladder import per_pair_delta
    from engine.ladder.usd_marks import forward_usd_rates
    from data.bloomberg.live import rates_from_marks
    from ui.tabs.exposure import BOOK_DISPLAY

    records, unresolved = records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
    # Delta/exposure math (Net/Gross headline, risk-and-scenarios table) must use
    # settle_date > as_of, not >= -- a leg settling today carries no delta by close
    # (CLAUDE.md "Six tabs as views"). The grid stays on records (>=): today's
    # settling leg is still cash that moves today.
    exposure_records, exposure_unresolved = exposure_records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
    # 2026-09-16 fix: a trade unresolved only under the exposure rule (e.g. its sole
    # leg settles exactly on as_of) must still reach the "Unresolved trades" caption.
    # Merge both lists, deduped by trade_id (the grid's own entry wins).
    seen_trade_ids = {u.trade_id for u in unresolved}
    unresolved = list(unresolved) + [u for u in exposure_unresolved if u.trade_id not in seen_trade_ids]
    # Rates: latest official SPOT marks written by the Bloomberg feed -- the ONLY
    # source (2026-09-17, "no bnp fall back"). A currency with no official SPOT stays
    # missing, never substituted.
    rates = rates_from_marks(conn)
    # USD-equivalent marks per (currency, value date): spot up to the spot date, then
    # each date's own official forward outright (user's cash-ladder spec, 2026-09-18).
    needed = {(r["currency"], r["settlement_date"]) for r in records}
    needed |= {(r["currency"], r["settled_on"]) for r in records if r.get("settled_on")}
    forward_rates = forward_usd_rates(conn, rates, needed)
    return {
        "records": records, "unresolved": unresolved, "exposure_records": exposure_records,
        "rates": rates, "forward_rates": forward_rates,
        "futures": futures_usd_delta(conn, as_of_date), "pairs": per_pair_delta(conn, as_of_date),
    }


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
        Input(CCY_FILTER_ID, "value"),
        Input(DATE_RANGE_ID, "start_date"),
        Input(DATE_RANGE_ID, "end_date"),
        Input(SETTLED_TOGGLE_ID, "value"),
        Input(USD_TOGGLE_ID, "value"),
    )
    def _update_table(as_of_date, _n_intervals=0, ccys=None, start=None, end=None, settled=None, usd=None):
        """Re-runs every REFRESH_MS so the ladder follows the 2-minute Bloomberg feed
        (data.bloomberg.live), and on every view-control change (spec 2026-09-18)."""
        return _render(as_of_date, view_from_controls(ccys, start, end, settled, usd))

    @app.callback(Output(CCY_FILTER_ID, "options"), Input(DATE_PICKER_ID, "date"))
    def _currency_options(as_of_date):
        """Currencies with any record on the as-of date, for the multiselect."""
        if not as_of_date:
            return []
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(get_db_path())
        except sqlite3.OperationalError:
            return []
        try:
            from engine.ladder.exposure_adapter import records_from_db
            from ui.tabs.exposure import BOOK_DISPLAY
            records, _ = records_from_db(conn, as_of_date, book_mapping=BOOK_DISPLAY)
        except ImportError:
            return []
        finally:
            conn.close()
        return [{"label": c, "value": c} for c in sorted({r["currency"] for r in records})]

    _view_states = [State(DATE_PICKER_ID, "date"), State(CCY_FILTER_ID, "value"),
                    State(DATE_RANGE_ID, "start_date"), State(DATE_RANGE_ID, "end_date"),
                    State(SETTLED_TOGGLE_ID, "value"), State(USD_TOGGLE_ID, "value")]

    @app.callback(Output(DOWNLOAD_LADDER_ID, "data"), Input(DOWNLOAD_LADDER_BTN_ID, "n_clicks"),
                  *_view_states, prevent_initial_call=True)
    def _download_ladder(_n, as_of_date, ccys=None, start=None, end=None, settled=None, usd=None):
        frame = _export(as_of_date, view_from_controls(ccys, start, end, settled, usd), "ladder")
        return None if frame is None else dcc.send_data_frame(frame.to_csv, "cash_ladder.csv", index=False)

    @app.callback(Output(DOWNLOAD_LEGS_ID, "data"), Input(DOWNLOAD_LEGS_BTN_ID, "n_clicks"),
                  *_view_states, prevent_initial_call=True)
    def _download_legs(_n, as_of_date, ccys=None, start=None, end=None, settled=None, usd=None):
        frame = _export(as_of_date, view_from_controls(ccys, start, end, settled, usd), "legs")
        return None if frame is None else dcc.send_data_frame(frame.to_csv, "legs.csv", index=False)

    def _export(as_of_date, view, which: str):
        """The spec's two downloads: the displayed ladder (`cash_ladder.csv`) and every
        leg with its mark and USD equivalent (`legs.csv`), from the same inputs the
        grid renders. None when there is no date or no database."""
        if not as_of_date:
            return None
        from ui.app import connect_readonly
        try:
            conn = connect_readonly(get_db_path())
        except sqlite3.OperationalError:
            return None
        try:
            from engine.ladder.exposure import build_exposure
            from ui.tabs.exposure import (combined_frame, grid_records, ladder_export_frame,
                                          legs_export_frame)
            inputs = load_inputs(conn, as_of_date)
        except ImportError:
            return None
        finally:
            conn.close()
        grid = grid_records(inputs["records"], view)
        if which == "legs":
            return legs_export_frame(grid, inputs["rates"], inputs["forward_rates"])
        result = build_exposure(grid, inputs["rates"])
        if result.ladder.empty:
            return ladder_export_frame(result, inputs["forward_rates"], view)
        _frame, ccys = combined_frame(result, grid, rates=inputs["rates"],
                                      forward_rates=inputs["forward_rates"], view=view)
        return ladder_export_frame(result, inputs["forward_rates"], view, ccys)

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

    def _render(as_of_date, view=None):
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
            # engine.ladder.exposure via records_from_db. Never P&L. Every input comes
            # from `load_inputs` (one read per render, shared with the downloads).
            try:
                from ui.tabs.exposure import exposure_section
                inputs = load_inputs(conn, as_of_date)
                exposure = exposure_section(
                    inputs["records"], inputs["unresolved"], as_of_date, rates=inputs["rates"],
                    futures=inputs["futures"], futures_details=(inputs["futures"] or {}).get("details"),
                    exposure_records=inputs["exposure_records"], pair_positions=inputs["pairs"],
                    forward_rates=inputs["forward_rates"], view=view)
            except ImportError as exc:
                exposure = message_box(f"Exposure ladder not available ({exc}).")
        finally:
            conn.close()
        # `reconciliation_panel` (blotter vs BNP EOD check) was deleted outright
        # 2026-09-17 ("no bnp fall back" -- user decision, docs/bnp-excel-removal.md):
        # it existed purely to call engine.pnl.reconcile, which compares the blotter
        # against a BNP snapshot that no longer exists to compare against.
        return html.Div([exposure])
