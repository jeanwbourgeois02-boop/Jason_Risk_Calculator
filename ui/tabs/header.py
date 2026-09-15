"""Header block shown above every tab: docs/BUILD_PLAN.md section 5 "Header (all
tabs)". Pure view over `engine.pnl.ledger.period_pnl` and `engine.pnl.ledger.ltd`; no
calculation happens here (CLAUDE.md: "ui/ ... never recomputes P&L or delta itself").

Exposes `layout()` (static shell, no DB access, so it is cheap to place on every tab)
and `register_callbacks(app, get_db_path)` (same signature convention as
`ui.tabs.cash_ladder.register_callbacks`, so `ui.app` / C5 wires it identically).

Design choices (no one to ask, so noted here):
  - The five figures + trading are read via `period_pnl(conn, as_of)`, one call, since
    the engine already returns daily/d5/mtd/ytd/trading together; LTD itself is a
    separate `ltd(conn, as_of)` call (period_pnl does not return raw LTD).
  - The LTD line chart is a `dcc.Graph` inside a collapsible `html.Details`, defaulting
    to *closed* (`open=False`) to keep the header compact on tabs that do not need it.
  - "Recent business days" for the chart = the calendar's own daily-period reference
    walk is not reusable as a list, so the chart instead re-derives a simple list of the
    last N calendar days (default 20) ending at `as_of` and calls `ltd` once per day.
    This is O(N) value_book evaluations; fine for a header chart, not for a hot path.
  - Unavailable periods (NaN) render as the literal string "Unavailable" with the
    engine's `reason` as a tooltip-less caption underneath the figure, per CLAUDE.md
    ("Unavailable shows its reason in place").
  - `as_of` with no date picked yet renders "No as-of date available." and skips all
    engine calls.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Callable, Optional

from dash import Input, Output, dcc, html

HEADER_ID = "header-block"
CHART_CONTAINER_ID = "header-ltd-chart-container"
DETAILS_ID = "header-ltd-details"
AS_OF_STORE_ID = "header-as-of-store"

PERIOD_LABELS = (
    ("value" , None),
)

_PERIODS = ("daily", "d5", "mtd", "ytd", "trading")
_PERIOD_TITLES = {
    "daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "trading": "Trading",
}

_CHART_LOOKBACK_DAYS = 20


def _figure_card(title: str, value_text: str, caption: str = "") -> html.Div:
    return html.Div(className="header-figure", children=[
        html.Div(title, className="header-figure-title"),
        html.Div(value_text, className="header-figure-value"),
        html.Div(caption, className="header-figure-caption") if caption else None,
    ])


def _fmt_usd(value: float) -> str:
    if value != value:  # NaN
        return "Unavailable"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def layout() -> html.Div:
    """Static shell: figure cards populated by the callback, collapsible chart below."""
    return html.Div(id=HEADER_ID, className="header-block", children=[
        html.Div(id=f"{HEADER_ID}-figures", className="header-figures",
                 children=[_figure_card("LTD", "-")]),
        html.Details(id=DETAILS_ID, className="section section--secondary details", open=False, children=[
            html.Summary("LTD line chart"),
            html.Div(id=CHART_CONTAINER_ID),
        ]),
    ])


def _build_figures(conn: sqlite3.Connection, as_of: str) -> list:
    from engine.pnl.ledger import ltd, period_pnl

    ltd_value = ltd(conn, as_of)
    cards = [_figure_card("LTD", _fmt_usd(ltd_value),
                           "" if ltd_value == ltd_value else "a trade has a missing mark; see value_book reason")]
    periods = period_pnl(conn, as_of)
    for key in _PERIODS:
        entry = periods.get(key, {})
        if entry.get("available"):
            cards.append(_figure_card(_PERIOD_TITLES[key], _fmt_usd(entry["value"])))
        else:
            cards.append(_figure_card(_PERIOD_TITLES[key], "Unavailable", entry.get("reason", "")))
    return cards


def _build_chart(conn: sqlite3.Connection, as_of: str):
    from engine.pnl.ledger import ltd

    end = dt.date.fromisoformat(as_of)
    days = [end - dt.timedelta(days=i) for i in range(_CHART_LOOKBACK_DAYS - 1, -1, -1)]
    xs, ys = [], []
    for d in days:
        xs.append(d.isoformat())
        ys.append(ltd(conn, d.isoformat()))
    figure = {
        "data": [{"x": xs, "y": ys, "type": "scatter", "mode": "lines+markers", "name": "LTD"}],
        "layout": {"margin": {"l": 50, "r": 20, "t": 10, "b": 30}, "height": 260,
                   "yaxis": {"title": "USD"}},
    }
    return dcc.Graph(id="header-ltd-graph", figure=figure)


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Registers a callback keyed on a shared `AS_OF_STORE_ID` dcc.Store; the module
    that owns the date picker (e.g. cash_ladder's date picker, or C5's shared control)
    is expected to write the chosen ISO date into that store's `data` field. Until C5
    wires a store, this callback simply does nothing (Dash raises no error for an
    unused Output if the Store never appears in the layout -- but the app must include
    an `AS_OF_STORE_ID` dcc.Store somewhere for this to fire)."""

    @app.callback(
        Output(f"{HEADER_ID}-figures", "children"),
        Output(CHART_CONTAINER_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
    )
    def _update_header(as_of: Optional[str]):
        if not as_of:
            return [_figure_card("LTD", "No as-of date available.")], None

        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return [_figure_card("LTD", f"Database not available ({exc}).")], None
        try:
            figures = _build_figures(conn, as_of)
            chart = _build_chart(conn, as_of)
        finally:
            conn.close()
        return figures, chart
