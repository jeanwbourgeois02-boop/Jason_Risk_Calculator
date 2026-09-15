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
import os
import sqlite3
from functools import lru_cache
from typing import Callable, Optional

from dash import Input, Output, State, dcc, html

HEADER_ID = "header-block"
CHART_CONTAINER_ID = "header-ltd-chart-container"
DETAILS_ID = "header-ltd-details"
AS_OF_STORE_ID = "header-as-of-store"

PERIOD_LABELS = (
    ("value" , None),
)

_PERIODS = ("daily", "previous_day", "d5", "mtd", "ytd", "trading")
_PERIOD_TITLES = {
    "daily": "Daily", "previous_day": "Previous day", "d5": "5d", "mtd": "MTD",
    "ytd": "YTD", "trading": "Trading",
}

_CHART_LOOKBACK_DAYS = 20


def _figure_card(title: str, value_text: str, caption: str = "") -> html.Div:
    """Plain informational card (no P&L sign colouring) -- used for LTD's fallback
    caption slot and the as-of/marks-time cards."""
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


def _sign_class(value: float) -> str:
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return "zero"


def _pnl_card(title: str, entry: dict, colour: bool = True) -> html.Div:
    """One header figure for a P&L-shaped `{value, available, reason}` entry (coordinator
    addition 2026-09-15, header compaction): bold value, green/red/white by sign when
    `colour` (True for every signed P&L figure and Net; False for Gross, which is
    always neutral per the user's decision), "n/a" muted with the reason as an HTML
    `title` tooltip when unavailable -- never a blank cell."""
    if not entry.get("available"):
        return html.Div(className="header-figure", children=[
            html.Div(title, className="header-figure-title"),
            html.Div("n/a", className="header-figure-value header-figure-value--muted",
                     title=entry.get("reason", "")),
        ])
    value = entry["value"]
    cls = _sign_class(value) if colour else "neutral"
    return html.Div(className="header-figure", children=[
        html.Div(title, className="header-figure-title"),
        html.Div(_fmt_usd(value), className=f"header-figure-value header-figure-value--{cls}"),
    ])


def _divider() -> html.Div:
    # A single empty child (rather than no children at all) keeps this safe for any
    # caller that walks `card.children[0]` over the whole figure list (e.g.
    # tests/test_ui_blotter.py::test_build_figures_includes_all_periods).
    return html.Div(className="header-divider", children=[html.Div()])


def layout() -> html.Div:
    """Static shell: figure cards populated by the callback, collapsible chart below.
    The chart's `.details` collapses to zero extra margin when closed (ui/assets/
    style.css) so a compact header never leaves an empty band under the figure row."""
    return html.Div(id=HEADER_ID, className="header-block", children=[
        html.Div(id=f"{HEADER_ID}-figures", className="header-figures",
                 children=[_figure_card("LTD", "-")]),
        html.Details(id=DETAILS_ID, className="section section--secondary details", open=False, children=[
            html.Summary("LTD line chart"),
            html.Div(id=CHART_CONTAINER_ID),
        ]),
    ])


def _build_figures(conn: sqlite3.Connection, as_of: str) -> list:
    # Same pricing path as the Blotter: official marks first, BNP file rates as a
    # labelled fallback, so the header shows numbers on a PC without Bloomberg.
    from ui.tabs.blotter_pricing import priced_value_book, scoped_period_pnl
    from ui.tabs.cash_ladder import net_gross_usd

    periods = scoped_period_pnl(conn, as_of)
    ltd_entry = periods.get("ltd", {})
    _, n_fallback, n_total = priced_value_book(conn, as_of)
    fallback_caption = f"{n_fallback} of {n_total} rows on BNP file rates, not Bloomberg" if n_fallback else ""

    cards = [_pnl_card("LTD", ltd_entry)]
    if fallback_caption and ltd_entry.get("available"):
        cards[0].children.append(html.Div(fallback_caption, className="header-figure-caption"))
    for key in _PERIODS:
        cards.append(_pnl_card(_PERIOD_TITLES[key], periods.get(key, {})))

    cards.append(_divider())
    ng = net_gross_usd(conn, as_of)
    if ng["available"]:
        # `ng["net"]` is the engine's net non-USD delta (+ = long foreign currency).
        # The header shows the USD *position* instead (CLAUDE.md sign: + = long USD),
        # so the sign is flipped here and the direction is spelled out in words
        # underneath -- user decision 2026-09-15: a short-USD book must be unmistakable.
        usd_position = -ng["net"]
        direction = "short USD" if usd_position < 0 else ("long USD" if usd_position > 0 else "flat USD")
        net_card = _pnl_card("Net USD delta", {"value": usd_position, "available": True})
        net_card.children.append(html.Div(direction, className="header-figure-caption header-figure-caption--direction"))
        cards.append(net_card)
        cards.append(_pnl_card("Gross USD delta", {"value": ng["gross"], "available": True}, colour=False))
    else:
        reason = ng.get("reason", "")
        cards.append(_pnl_card("Net USD delta", {"available": False, "reason": reason}))
        cards.append(_pnl_card("Gross USD delta", {"available": False, "reason": reason}, colour=False))

    # No "As of" / "Last updated" cards: the as-of date is already in the tab's own
    # title row, and the LTD caption states when rows are on BNP file rates (user
    # decision 2026-09-15).
    return cards


@lru_cache(maxsize=1024)
def _cached_ltd(db_path: str, _mtime: float, as_of: str) -> float:
    """`engine.pnl.ledger.ltd` memoised on (db path, db mtime, as_of): a header-chart
    render used to re-run 20 full `value_book` evaluations (~4s) on every as-of change.
    `_mtime` is part of the key purely to invalidate the cache when the file changes
    (a new upload / Bloomberg write) -- callers pass `os.path.getmtime(db_path)`, never
    a value this function computes itself, so a stale cache never outlives the file it
    was read from. Opens and closes its own read-only connection (the cache key is a
    path, not a connection object, which is unhashable and reopened per request)."""
    from ui.tabs.blotter_pricing import priced_value_book
    from ui.app import connect_readonly
    conn = connect_readonly(db_path)
    try:
        df, _, _ = priced_value_book(conn, as_of)  # same fallback path as the figures
        if df.empty:
            return 0.0
        return float("nan") if df["pnl_usd"].isna().any() else float(df["pnl_usd"].sum())
    finally:
        conn.close()


def _business_days_back(conn: sqlite3.Connection, as_of: str, n: int):
    """Up to `n` business days ending at `as_of` (inclusive), oldest first, skipping
    weekends/holidays (engine.pnl.aggregate's own calendar) and never going earlier than
    the earliest trade_date on record (there is nothing to chart before the book
    existed, and it wastes an evaluation)."""
    from engine.pnl.aggregate import _is_business_day, load_holidays

    holidays = load_holidays()
    end = dt.date.fromisoformat(as_of)
    earliest_row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
    earliest = dt.date.fromisoformat(earliest_row[0]) if earliest_row and earliest_row[0] else None

    days = []
    d = end
    while len(days) < n:
        if earliest is not None and d < earliest:
            break
        if _is_business_day(d, holidays):
            days.append(d)
        d -= dt.timedelta(days=1)
    days.reverse()
    return days


def _build_chart(conn: sqlite3.Connection, as_of: str, db_path=None):
    """`db_path` (optional) enables the `_cached_ltd` memoisation; omitted (e.g. direct
    unit tests against an in-memory/temp connection with no path handy) falls back to
    one `ltd(conn, ...)` call per day, same as before -- correctness is identical
    either way, only the cost of repeated renders differs."""
    from engine.pnl.ledger import ltd

    days = _business_days_back(conn, as_of, _CHART_LOOKBACK_DAYS)
    xs, ys = [], []
    mtime = os.path.getmtime(db_path) if db_path is not None else None
    for d in days:
        xs.append(d.isoformat())
        if db_path is not None:
            ys.append(_cached_ltd(str(db_path), mtime, d.isoformat()))
        else:
            from ui.tabs.blotter_pricing import priced_value_book as _pvb
            _df, _, _ = _pvb(conn, d.isoformat())
            ys.append(0.0 if _df.empty else (float("nan") if _df["pnl_usd"].isna().any() else float(_df["pnl_usd"].sum())))
    figure = {
        "data": [{"x": xs, "y": ys, "type": "scatter", "mode": "lines+markers", "name": "LTD"}],
        "layout": {"margin": {"l": 50, "r": 20, "t": 10, "b": 30}, "height": 260,
                   "yaxis": {"title": "USD"}},
    }
    return dcc.Graph(id="header-ltd-graph", figure=figure)


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Registers two callbacks keyed on a shared `AS_OF_STORE_ID` dcc.Store; the module
    that owns the date picker (e.g. cash_ladder's date picker, or C5's shared control)
    is expected to write the chosen ISO date into that store's `data` field. Until C5
    wires a store, these callbacks simply do nothing (Dash raises no error for an
    unused Output if the Store never appears in the layout -- but the app must include
    an `AS_OF_STORE_ID` dcc.Store somewhere for this to fire).

    Split 2026-09-15 (coordinator perf finding): the figure cards used to share one
    callback with the LTD chart, so every as-of change paid for ~20 `value_book`
    evaluations (~4s) before anything painted. Figures now update immediately on
    `AS_OF_STORE_ID` alone; the chart is a second callback gated on the collapsible's
    own `open` state, so it only runs when the user actually expands it (and again
    whenever as_of changes while it is already open). `_cached_ltd` further memoises
    each day's value on (db path, db mtime) so re-expanding after a figures-only render
    is instant."""

    @app.callback(
        Output(f"{HEADER_ID}-figures", "children"),
        Input(AS_OF_STORE_ID, "data"),
    )
    def _update_figures(as_of: Optional[str]):
        if not as_of:
            return [_figure_card("LTD", "No as-of date available.")]

        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return [_figure_card("LTD", f"Database not available ({exc}).")]
        try:
            return _build_figures(conn, as_of)
        finally:
            conn.close()

    @app.callback(
        Output(CHART_CONTAINER_ID, "children"),
        Input(DETAILS_ID, "open"),
        Input(AS_OF_STORE_ID, "data"),
    )
    def _update_chart(is_open: bool, as_of: Optional[str]):
        if not is_open or not as_of:
            # Collapsed, or no date yet: nothing to compute. Dash keeps whatever was
            # last rendered hidden inside the closed <details>, so this is not a
            # regression versus always rendering -- it is strictly less work.
            from dash import no_update
            return no_update

        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return html.P(f"Database not available ({exc}).")
        try:
            return _build_chart(conn, as_of, db_path=db_path)
        finally:
            conn.close()
