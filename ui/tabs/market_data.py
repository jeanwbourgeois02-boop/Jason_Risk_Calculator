"""Market data tab: "Can I trust the numbers?" (BUILD_PLAN.md section 5).

User decision 2026-09-15: this tab is organised BY CURRENCY PAIR, not by a flat
inventory table. Layout:

  1. Top bar: as-of date picker, a pair dropdown (every FX instrument with trades or
     marks, default = the pair with the most open trades) -- the only dropdown on the
     tab -- the "Pull now" button and a one-line feed status.
  2. For the selected pair: spot (value/source/snapped time), the forward curve as a
     table (one row per settle_date with a FWD_OUTRIGHT mark on the as-of date, all
     sources, official first), and a line chart of outright vs settle date.
  3. Bottom, compact: the close-completeness strip (`data.bloomberg.inventory
     .close_completeness`) as a row of small squares for the trailing 20 business
     days, and the manual mark-entry form pre-filled with the selected pair.

Removed from the previous version of this tab: the whole-book inventory table and the
Bloomberg diagnostics panel (still reachable via `risk.py doctor`). `feed_headline`
and `backfill_headline` are kept -- and now folded into the single top-bar status
line -- because `ui/tabs/cash_ladder.py` still imports `diagnostics_panel` /
`feed_headline` from this module; both stay defined for that caller even though this
tab's body no longer renders `diagnostics_panel` itself.

No calculation happens in this module beyond plain display formatting (tenor
labelling from calendar-day distance, forward points as an outright/spot difference):
every value is read straight from `marks` / `marks_official` / `trade_legs` /
`trades` / `instruments`, or from `data.bloomberg.inventory` / `data.bloomberg.live` /
`data.bloomberg.manual`. Engine and data imports stay lazy (inside callbacks / render
helpers) so `import ui.app` and `import ui.tabs.market_data` always succeed even if
those modules are mid-edit.

Quirk recorded for memory: the DB on this machine has only BNP_BVAL marks (never
official for any mark_type per CLAUDE.md), so every row here renders with source
label "BNP file" and status "reconciliation only" rather than "official" -- this is
expected, not a bug, until a live Bloomberg pull lands BBG_BFXFORWARD rows.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.tabs.controls import build_date_picker

DATE_PICKER_ID = "market-data-date"
PAIR_DROPDOWN_ID = "market-data-pair"
TOOLBAR_ID = "market-data-toolbar"
STATUS_ID = "market-data-status"
BODY_ID = "market-data-body"
REFRESH_ID = "market-data-refresh"
PULL_NOW_ID = "market-data-pull-now"
PULL_NOW_STATUS_ID = "market-data-pull-now-status"
PULL_REVISION_ID = "market-data-pull-revision"
REFRESH_MS = 120_000  # matches data.bloomberg.live.INTERVAL_SECONDS

CURVE_TABLE_ID = "market-data-curve-table"
CURVE_CHART_ID = "market-data-curve-chart"
COMPLETENESS_STRIP_ID = "market-data-completeness-strip"
COMPLETENESS_DAYS = 20  # trailing business days shown in the calendar strip; not specified in BUILD_PLAN.md

MANUAL_INSTRUMENT_ID = "market-data-manual-instrument"
MANUAL_SETTLE_ID = "market-data-manual-settle"
MANUAL_MARK_TYPE_ID = "market-data-manual-mark-type"
MANUAL_VALUE_ID = "market-data-manual-value"
MANUAL_SUBMIT_ID = "market-data-manual-submit"
MANUAL_STATUS_ID = "market-data-manual-status"

_MONO = {"fontFamily": "Consolas, 'Courier New', monospace", "fontSize": "12px", "padding": "3px 8px",
         "textAlign": "left", "whiteSpace": "nowrap"}
_HEAD = {"fontWeight": "600", "backgroundColor": "#f0f2f5", "borderBottom": "1px solid #d9dee3"}

MANUAL_MARK_TYPE_OPTIONS = [
    {"label": t, "value": t}
    for t in ("SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "PAR_RATE", "PV_USD", "DV01_USD", "DELTA", "PREMIUM")
]

# Standard tenor buckets: label -> (approx calendar days, tolerance). Anything outside
# every bucket's tolerance is labelled "broken" (a broken date, per the module docstring).
_TENORS: List[Tuple[str, int, int]] = [
    ("1W", 7, 2), ("2W", 14, 2), ("1M", 30, 4), ("2M", 61, 4),
    ("3M", 91, 5), ("6M", 182, 6), ("9M", 273, 7), ("1Y", 365, 8),
]

_SOURCE_LABELS = {"BNP_BVAL": "BNP file", "BBG_BFXFORWARD": "Bloomberg", "BBG_INTERP": "Interpolated",
                  "MANUAL": "Manual"}


def source_label(source: Optional[str]) -> str:
    if not source:
        return ""
    return _SOURCE_LABELS.get(source, source)


def tenor_label(as_of: str, settle_date: str) -> str:
    """Standard tenor bucket from calendar days between `as_of` (treated as the spot
    date) and `settle_date`; "broken" when it does not match any bucket's tolerance."""
    try:
        days = (date.fromisoformat(settle_date) - date.fromisoformat(as_of)).days
    except ValueError:
        return "broken"
    for label, target, tol in _TENORS:
        if abs(days - target) <= tol:
            return label
    return "broken"


def is_jpy_pair(pair: str) -> bool:
    return "JPY" in (pair or "").upper()


def decimals_for_pair(pair: str) -> int:
    return 2 if is_jpy_pair(pair) else 4


def forward_points(outright: Optional[float], spot: Optional[float], pair: str) -> Optional[float]:
    """Outright minus spot, rounded to the pair's convention (4 dp, 2 dp for JPY)."""
    if outright is None or spot is None:
        return None
    return round(float(outright) - float(spot), decimals_for_pair(pair))


# --------------------------------------------------------------------------- diagnostics panel (unchanged, kept for
# ui/tabs/cash_ladder.py, which still imports feed_headline / diagnostics_panel from here; not rendered by this tab)
def backfill_headline(status: Optional[dict]) -> Optional[str]:
    """'Backfill: n days remaining' from the "backfill" key data.bloomberg.backfill's
    start_auto_backfill writes into the status file; None when there is nothing to say
    (no Terminal ever seen, or history already complete and no run has happened yet)."""
    backfill = (status or {}).get("backfill")
    if not backfill:
        return None
    if backfill.get("running"):
        remaining = backfill.get("remaining")
        return f"Backfill: {remaining} day(s) remaining" if remaining is not None else "Backfill: running"
    if backfill.get("reason"):
        return f"Backfill: not running — {backfill['reason']}"
    if backfill.get("remaining") == 0:
        return "Backfill: history complete"
    return None


def feed_headline(status: Optional[dict]) -> str:
    """One line for the toolbar / summary: connection, last pull, counts."""
    if not status:
        return "Bloomberg: no pull recorded yet"
    if not status.get("connected"):
        return f"Bloomberg: not connected — {status.get('reason', 'unknown reason')}"
    return (f"Bloomberg: connected · last pull {status.get('time', '')} · "
            f"{status.get('written', 0)} marks written, {status.get('failed', 0)} failed · refresh every 2 min")


def top_bar_status(status: Optional[dict]) -> str:
    """The single top-bar status line: feed headline plus backfill progress when
    there is any to report."""
    line = feed_headline(status)
    bf_line = backfill_headline(status)
    if bf_line:
        line = f"{line} | {bf_line}"
    return line


def diagnostics_panel(status: Optional[dict], rates: Dict[str, dict], open_by_default: bool = False) -> html.Details:
    items: List[dict] = list((status or {}).get("items", []))
    failed = [i for i in items if i.get("status") == "FAILED"]
    rows = [{"status": i.get("status", ""), "instrument_id": i.get("instrument_id", ""),
             "mark_type": i.get("mark_type", ""), "settle_date": i.get("settle_date", ""),
             "value": "" if i.get("value") is None else f"{float(i['value']):.8f}",
             "source": i.get("source", ""), "detail": i.get("detail", "")} for i in items]
    rate_rows = [{"currency": c, "pair": v.get("pair", ""), "rate": f"{v['rate']:.8f}",
                  "inverted": "1/rate" if v["inverted"] else "direct", "source": v["source"],
                  "timestamp": v["timestamp"], "stale": "STALE" if v["stale"] else "fresh"}
                 for c, v in sorted(rates.items())]
    summary_bits = [feed_headline(status)]
    bf_line = backfill_headline(status)
    if bf_line:
        summary_bits.append(bf_line)
    if status and status.get("as_of_date"):
        summary_bits.append(f"requests built for as-of {status['as_of_date']}; live marks stamped {status.get('as_of_marks', '')}")
    if status and status.get("warnings"):
        summary_bits.append(f"{len(status['warnings'])} warning(s) from the pull")
    children = [
        html.Summary(f"Bloomberg diagnostics · {len(items) - len(failed)} OK / {len(failed)} failed"
                     if items else "Bloomberg diagnostics"),
        html.Div(id="market-data-diag-summary", className="status-line", children=" · ".join(summary_bits)),
    ]
    if status and status.get("traceback"):
        children.append(html.Pre(status["traceback"], className="diag-trace"))
    children += [
        html.H4("Requested marks: pulled vs failed"),
        dash_table.DataTable(
            id="market-data-diag-table",
            columns=[{"name": n, "id": i} for n, i in [("Status", "status"), ("Pair", "instrument_id"),
                                                       ("Mark", "mark_type"), ("Settle date", "settle_date"),
                                                       ("Value", "value"), ("Source", "source"), ("Detail", "detail")]],
            data=rows, page_size=40, sort_action="native", filter_action="native",
            style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[
                {"if": {"filter_query": "{status} = 'OK'", "column_id": "status"}, "color": "#1a7f4b", "fontWeight": "600"},
                {"if": {"filter_query": "{status} = 'FAILED'", "column_id": "status"}, "color": "#b42318", "fontWeight": "600"},
                {"if": {"filter_query": "{status} = 'FAILED'"}, "backgroundColor": "#fff4f2"},
                {"if": {"filter_query": "{status} = 'SKIPPED'"}, "color": "#616e7c"},
            ]),
        html.H4("Spot rates the ladder is using (latest official SPOT mark per currency)"),
        dash_table.DataTable(
            id="market-data-rates-table",
            columns=[{"name": n, "id": i} for n, i in [("Currency", "currency"), ("Pair", "pair"), ("Rate", "rate"),
                                                       ("USD per local", "inverted"), ("Source", "source"),
                                                       ("Snapped at", "timestamp"), ("Freshness", "stale")]],
            data=rate_rows, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[{"if": {"filter_query": "{stale} = 'STALE'"}, "color": "#8a4b00",
                                     "backgroundColor": "#fff4e5"}]),
    ]
    if status and status.get("warnings"):
        children += [html.H4("Pull warnings"), html.Ul([html.Li(w, className="status-line") for w in status["warnings"]])]
    return html.Details(id="market-data-diag", className="details details--diag",
                        open=open_by_default or bool(failed), children=children)


# --------------------------------------------------------------------------- pair selection
def pair_options(conn: sqlite3.Connection, as_of: str) -> Tuple[List[dict], Optional[str]]:
    """Every FX instrument with a trade (any date) or a mark on `as_of`, as dropdown
    options, plus the default value: the pair with the most open trades (trade_date
    <= as_of and at least one leg settling >= as_of), ties broken alphabetically.
    Falls back to the pair with any trade, then the pair with any mark, then None."""
    pairs = {row[0] for row in conn.execute(
        "SELECT DISTINCT i.instrument_id FROM instruments i JOIN trades t USING (instrument_id) "
        "WHERE i.asset_class='FX'")}
    pairs |= {row[0] for row in conn.execute(
        "SELECT DISTINCT i.instrument_id FROM instruments i JOIN marks m USING (instrument_id) "
        "WHERE i.asset_class='FX' AND m.as_of_date=?", (as_of,))}
    if not pairs:
        return [], None
    options = [{"label": p, "value": p} for p in sorted(pairs)]

    open_counts = dict(conn.execute(
        "SELECT i.instrument_id, COUNT(DISTINCT t.trade_id) FROM trades t "
        "JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id) "
        "WHERE i.asset_class='FX' AND t.trade_date<=? AND l.settle_date>=? "
        "GROUP BY i.instrument_id", (as_of, as_of)).fetchall())
    if open_counts:
        default = sorted(open_counts, key=lambda p: (-open_counts[p], p))[0]
    else:
        any_trade = dict(conn.execute(
            "SELECT i.instrument_id, COUNT(*) FROM trades t JOIN instruments i USING (instrument_id) "
            "WHERE i.asset_class='FX' GROUP BY i.instrument_id").fetchall())
        default = sorted(any_trade, key=lambda p: (-any_trade[p], p))[0] if any_trade else sorted(pairs)[0]
    return options, default


# --------------------------------------------------------------------------- spot + curve
def spot_info(conn: sqlite3.Connection, as_of: str, pair: str) -> Optional[dict]:
    """Official SPOT mark for the pair on `as_of`, falling back to the latest row of
    any source (labelled with that source, e.g. "BNP file" for BNP_BVAL-only DBs)."""
    row = conn.execute(
        "SELECT value, source, snapped_at FROM marks_official WHERE as_of_date=? "
        "AND instrument_id=? AND mark_type='SPOT'", (as_of, pair)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT value, source, snapped_at FROM marks WHERE as_of_date=? AND instrument_id=? "
            "AND mark_type='SPOT' ORDER BY snapped_at DESC LIMIT 1", (as_of, pair)).fetchone()
    if row is None:
        return None
    return {"value": row[0], "source": row[1], "snapped_at": row[2]}


def _official_settle_dates(conn: sqlite3.Connection, as_of: str, pair: str) -> set:
    return {row[0] for row in conn.execute(
        "SELECT settle_date FROM marks_official WHERE as_of_date=? AND instrument_id=? "
        "AND mark_type='FWD_OUTRIGHT'", (as_of, pair))}


def used_by_book(conn: sqlite3.Connection, as_of: str, pair: str) -> Dict[str, int]:
    """settle_date -> number of open trades (trade_date <= as_of, a leg settling on
    that date) in this pair, for the curve table's "used by book" marker."""
    rows = conn.execute(
        "SELECT l.settle_date, COUNT(DISTINCT t.trade_id) FROM trade_legs l "
        "JOIN trades t USING (trade_id) JOIN instruments i USING (instrument_id) "
        "WHERE i.instrument_id=? AND t.trade_date<=? GROUP BY l.settle_date", (pair, as_of)).fetchall()
    return {settle: count for settle, count in rows}


def forward_curve(conn: sqlite3.Connection, as_of: str, pair: str) -> pd.DataFrame:
    """One row per (settle_date, source) FWD_OUTRIGHT mark for `pair` on `as_of`,
    official rows first, columns: settle_date, tenor, outright, points, source,
    status, used_by_book (trade count, blank when none)."""
    rows = conn.execute(
        "SELECT settle_date, value, source FROM marks WHERE as_of_date=? AND instrument_id=? "
        "AND mark_type='FWD_OUTRIGHT' ORDER BY settle_date", (as_of, pair)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["settle_date", "tenor", "outright", "points", "source", "status", "used_by_book"])
    official = _official_settle_dates(conn, as_of, pair)
    spot = spot_info(conn, as_of, pair)
    spot_value = spot["value"] if spot else None
    book = used_by_book(conn, as_of, pair)
    out = []
    for settle_date, value, source in rows:
        is_official = settle_date in official
        out.append({
            "settle_date": settle_date,
            "tenor": tenor_label(as_of, settle_date),
            "outright": value,
            "points": forward_points(value, spot_value, pair),
            "source": source_label(source),
            "status": "official" if is_official else "reconciliation only",
            "used_by_book": book.get(settle_date, 0) or "",
            "_official": is_official,
        })
    out.sort(key=lambda r: (r["settle_date"], not r["_official"]))
    for r in out:
        del r["_official"]
    return pd.DataFrame(out, columns=["settle_date", "tenor", "outright", "points", "source", "status", "used_by_book"])


def curve_table(df: pd.DataFrame, pair: str) -> dash_table.DataTable:
    dp = decimals_for_pair(pair)
    formatted = df.copy()
    if "outright" in formatted.columns:
        formatted["outright"] = formatted["outright"].map(lambda v: "" if pd.isna(v) else f"{float(v):.{dp}f}")
    if "points" in formatted.columns:
        formatted["points"] = formatted["points"].map(lambda v: "" if v is None or pd.isna(v) else f"{float(v):.{dp}f}")
    columns = [{"name": n, "id": i} for n, i in [
        ("Settle date", "settle_date"), ("Tenor", "tenor"), ("Outright", "outright"),
        ("Fwd points", "points"), ("Source", "source"), ("Status", "status"),
        ("Used by book", "used_by_book"),
    ]]
    return dash_table.DataTable(
        id=CURVE_TABLE_ID, columns=columns, data=formatted.to_dict("records"),
        page_size=25, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
        style_data_conditional=[
            {"if": {"filter_query": "{status} = 'official'", "column_id": "status"}, "color": "#1a7f4b", "fontWeight": "600"},
            {"if": {"filter_query": "{used_by_book} > 0"}, "backgroundColor": "#eef4ff"},
        ],
    )


def curve_chart(df: pd.DataFrame, spot: Optional[dict], pair: str, as_of: str):
    """dcc.Graph of outright vs settle date: official marks as a line, other sources
    as markers, spot as the first point; empty (hidden) when there is no curve."""
    import plotly.graph_objects as go

    if df.empty:
        return html.Div()
    dp = decimals_for_pair(pair)
    fig = go.Figure()
    x = list(df["settle_date"])
    y = list(df["outright"])
    if spot is not None:
        x = [as_of] + x
        y = [spot["value"]] + y
    official_mask = list(df["status"] == "official")
    fig.add_trace(go.Scatter(x=[as_of] + [d for d, o in zip(df["settle_date"], official_mask) if o],
                             y=([spot["value"]] if spot is not None else []) + [v for v, o in zip(df["outright"], official_mask) if o],
                             mode="lines+markers", name="official"))
    other_x = [d for d, o in zip(df["settle_date"], official_mask) if not o]
    other_y = [v for v, o in zip(df["outright"], official_mask) if not o]
    if other_x:
        fig.add_trace(go.Scatter(x=other_x, y=other_y, mode="markers",
                                 marker_symbol="diamond", name="interp/manual/BNP"))
    used_dates = list(df.loc[df["used_by_book"] != "", "settle_date"])
    fig.update_layout(
        title=f"{pair} forward curve, {as_of}",
        yaxis=dict(tickformat=f".{dp}f"),
        xaxis=dict(tickangle=-90, tickvals=used_dates or None, showgrid=False),
        yaxis_showgrid=False,
        showlegend=True,
        margin=dict(t=40, b=60),
    )
    return dcc.Graph(id=CURVE_CHART_ID, figure=fig, config={"displayModeBar": False})


def pair_body(conn: sqlite3.Connection, as_of: str, pair: str) -> html.Div:
    spot = spot_info(conn, as_of, pair)
    df = forward_curve(conn, as_of, pair)
    spot_line = (message_box(f"No spot mark for {pair} on {as_of}.") if spot is None else
                html.P(f"Spot: {spot['value']:.{decimals_for_pair(pair)}f} · {source_label(spot['source'])} · "
                       f"{spot.get('snapped_at', '')}", className="status-line"))
    if df.empty:
        return html.Div([spot_line, message_box(f"No forward marks for {pair} on {as_of}")])
    return html.Div([spot_line, curve_table(df, pair), curve_chart(df, spot, pair, as_of)])


def completeness_strip(df: pd.DataFrame) -> html.Div:
    """Compact row of small squares, one per business day, coloured by completeness,
    with a tooltip (title attribute) giving the counts for that day."""
    if df.empty:
        return message_box("No completeness data.")
    squares = []
    for _, row in df.iterrows():
        complete = bool(row.get("complete"))
        title = f"{row['as_of_date']}: {row['present']}/{row['needed']} official SPOT marks"
        squares.append(html.Div(title=title, className="completeness-square",
                                style={"display": "inline-block", "width": "14px", "height": "14px",
                                       "margin": "1px", "backgroundColor": "#1a7f4b" if complete else "#b42318"}))
    return html.Div(id=COMPLETENESS_STRIP_ID, children=squares)


def manual_entry_form(default_pair: Optional[str] = None) -> html.Div:
    """Manual mark entry calling `data.bloomberg.manual.write_manual_mark`, pre-filled
    with the pair currently selected at the top of the tab."""
    return html.Div(className="market-data-manual-entry", children=[
        html.H4("Manual mark entry"),
        html.P("MANUAL is official only for DELTA and PREMIUM; for SPOT / FWD_OUTRIGHT / FUTURE_PX / "
               "PAR_RATE / PV_USD / DV01_USD a manual row is visible here but is never used by valuation "
               "unless explicitly requested (marks_source='MANUAL')."),
        html.Div(className="toolbar", children=[
            html.Div([html.Label("Instrument"), dcc.Input(id=MANUAL_INSTRUMENT_ID, type="text", value=default_pair)]),
            html.Div([html.Label("Settle date"), dcc.Input(id=MANUAL_SETTLE_ID, type="text", placeholder="YYYY-MM-DD")]),
            html.Div([html.Label("Mark type"), dcc.Dropdown(id=MANUAL_MARK_TYPE_ID, options=MANUAL_MARK_TYPE_OPTIONS,
                                                             value="SPOT", clearable=False, style={"width": "160px"})]),
            html.Div([html.Label("Value"), dcc.Input(id=MANUAL_VALUE_ID, type="number")]),
            html.Div([html.Button("Save", id=MANUAL_SUBMIT_ID, n_clicks=0, className="btn")]),
        ]),
        html.Div(id=MANUAL_STATUS_ID, className="status-line"),
    ])


def _connect_readonly(path) -> sqlite3.Connection:
    """Open the database read-only without importing `ui.app` (which pulls in the
    other tabs and can be mid-edit while this module is developed/tested)."""
    from pathlib import Path
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """Top bar (date, pair dropdown, pull button, status) + an (initially empty) body
    container filled in by the callback registered in register_callbacks, plus the
    static bottom block (completeness strip placeholder + manual entry form)."""
    return html.Div(className="market-data", children=[
        html.H3("Market data"),
        html.Div(id=TOOLBAR_ID, className="toolbar", children=[
            build_date_picker(DATE_PICKER_ID, default_date=default_date),
            html.Div(className="toolbar-group", children=[
                html.Label("Pair"),
                dcc.Dropdown(id=PAIR_DROPDOWN_ID, options=[], value=None, clearable=False,
                            style={"width": "140px"}),
            ]),
            html.Div(className="toolbar-group", children=[
                html.Label("Bloomberg"),
                html.Div([html.Button("Pull now", id=PULL_NOW_ID, n_clicks=0, className="btn"),
                          html.Span(id=PULL_NOW_STATUS_ID, className="status-line", style={"marginLeft": "8px"})]),
                dcc.Store(id=PULL_REVISION_ID),
            ]),
            html.Div(id=STATUS_ID, className="status-line"),
        ]),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        html.Div(id=BODY_ID),
        html.H4("Close completeness"),
        html.Div(id="market-data-completeness-container"),
        manual_entry_form(),  # static: its ids are callback inputs and must exist on first render
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Register the callbacks: pair dropdown population, main body refresh (spot +
    curve table + chart), completeness strip, the "Pull now" button, and the
    manual-entry submit button. `get_db_path` is a zero-arg callable returning the
    resolved DB path, same convention as `ui/tabs/cash_ladder.py::register_callbacks`.
    """

    @app.callback(
        Output(PAIR_DROPDOWN_ID, "options"),
        Output(PAIR_DROPDOWN_ID, "value"),
        Input(DATE_PICKER_ID, "date"),
    )
    def _update_pairs(as_of_date):
        if not as_of_date:
            return [], None
        try:
            conn = _connect_readonly(get_db_path())
        except sqlite3.OperationalError:
            return [], None
        try:
            return pair_options(conn, as_of_date)
        finally:
            conn.close()

    @app.callback(
        Output(BODY_ID, "children"),
        Output("market-data-completeness-container", "children"),
        Output(STATUS_ID, "children"),
        Output(MANUAL_INSTRUMENT_ID, "value"),
        Input(DATE_PICKER_ID, "date"),
        Input(PAIR_DROPDOWN_ID, "value"),
        Input(REFRESH_ID, "n_intervals"),
        Input(PULL_REVISION_ID, "data"),
        Input(MANUAL_STATUS_ID, "children"),
    )
    def _update_body(as_of_date, pair, _n_intervals=0, _pull_rev=None, _manual_status=None):
        return _render(as_of_date, pair)

    def _render(as_of_date, pair):
        if not as_of_date:
            return message_box("No as-of date available."), html.Div(), "Bloomberg: status unknown", pair

        db_path = get_db_path()
        try:
            conn = _connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return message_box(f"Database not available ({exc})."), html.Div(), "Bloomberg: status unknown", pair

        try:
            body = pair_body(conn, as_of_date, pair) if pair else message_box("No FX pair available.")
        except Exception as exc:
            body = message_box(f"Market data not available ({exc}).")

        try:
            from data.bloomberg.inventory import close_completeness
            end = date.fromisoformat(as_of_date)
            start = end - timedelta(days=int(COMPLETENESS_DAYS * 1.6) + 5)  # generous calendar padding for bd count
            completeness = close_completeness(conn, start.isoformat(), end.isoformat()).tail(COMPLETENESS_DAYS)
            strip = completeness_strip(completeness)
        except ImportError as exc:
            strip = message_box(f"Completeness not available yet ({exc}).")

        try:
            from data.bloomberg.live import read_status
            feed_status = read_status(db_path)
        except ImportError:
            feed_status = None
        finally:
            conn.close()

        return body, strip, top_bar_status(feed_status), pair

    @app.callback(
        Output(PULL_NOW_STATUS_ID, "children"),
        Output(PULL_REVISION_ID, "data"),
        Input(PULL_NOW_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _pull_now(n_clicks):
        """Synchronous single Bloomberg pull (data.bloomberg.live.pull_once)."""
        import time
        from data.bloomberg.live import pull_once
        status = pull_once(get_db_path())
        if status.get("connected"):
            text = f"Pulled {status.get('time', '')}: {status.get('written', 0)} marks written, {status.get('failed', 0)} failed"
        else:
            text = f"Not pulled: {status.get('reason', 'unknown')}"
        return text, str(time.time())

    @app.callback(
        Output(MANUAL_STATUS_ID, "children"),
        Input(MANUAL_SUBMIT_ID, "n_clicks"),
        State(DATE_PICKER_ID, "date"),
        State(MANUAL_INSTRUMENT_ID, "value"),
        State(MANUAL_SETTLE_ID, "value"),
        State(MANUAL_MARK_TYPE_ID, "value"),
        State(MANUAL_VALUE_ID, "value"),
        prevent_initial_call=True,
    )
    def _submit_manual(n_clicks, as_of_date, instrument_id, settle_date, mark_type, value):
        if not as_of_date or not instrument_id or not settle_date or not mark_type or value is None:
            return "Fill in every field before saving."
        try:
            from data.bloomberg.manual import write_manual_mark
            from data.ingest.schema import connect
        except ImportError as exc:
            return f"Manual entry not available yet ({exc})."
        db_path = get_db_path()
        conn = connect(db_path)
        try:
            write_manual_mark(conn, as_of_date, instrument_id, settle_date, mark_type, float(value))
        except Exception as exc:
            return f"Save failed: {exc!r}"
        finally:
            conn.close()
        return f"Saved MANUAL {mark_type} {instrument_id} {settle_date} = {float(value)!r}."
