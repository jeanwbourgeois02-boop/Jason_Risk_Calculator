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
  - Unavailable periods (NaN) keep the muted "n/a" value (existing behaviour/tests),
    but the engine's `reason` is now also a **visible** caption under the figure, not
    only an HTML `title` tooltip -- a hover-only reason is invisible on first glance,
    which is exactly what the user reported as "the headline ... doesn't work" for a
    Bloomberg-less database (2026-09-17 investigation: every figure was in fact
    computing correctly and showing a reason, but only on hover). Per CLAUDE.md
    ("Unavailable shows its reason in place").
  - The generic engine-level reasons ("today's LTD unavailable", "LTD on <date>
    unavailable") name a blocking date but not *why* that date has no LTD. `_resolve_reason`
    turns that into a concrete, actionable sentence -- which mark_type is missing, how
    many, and "run the Bloomberg pull" -- built from `data.bloomberg.inventory.
    mark_inventory` (a cheap, DB-only read: no Bloomberg connection is opened here).
    When nothing is missing (the gap is something else, e.g. an unrealisable settled
    trade), the engine's own reason passes through unchanged.
  - Net/Gross USD delta and the "Trades" count need no marks at all (CLAUDE.md: Net/
    Gross USD notional is computable from trade legs and spot alone), so they are
    always shown, even on a database with zero official marks -- this is the
    "figure that IS computable" half of the same fix.
  - `as_of` with no date picked yet renders "No as-of date available." and skips all
    engine calls.
"""
from __future__ import annotations

import datetime as dt
import os
import re
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
    `title` tooltip when unavailable -- never a blank cell. The reason is also rendered
    as a plain, always-visible caption line underneath (2026-09-17: a hover-only tooltip
    was reported as the headline figures "not working" -- they were computing correctly,
    the explanation was just invisible until the mouse found it)."""
    if not entry.get("available"):
        reason = entry.get("reason", "")
        children = [
            html.Div(title, className="header-figure-title"),
            html.Div("n/a", className="header-figure-value header-figure-value--muted",
                     title=reason),
        ]
        if reason:
            children.append(html.Div(reason, className="header-figure-caption header-figure-caption--reason"))
        return html.Div(className="header-figure", children=children)
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


# The "Check Bloomberg connection" button, its results panel, and the diagnostics
# entry point/placeholder/renderer moved to ui/tabs/market_data.py on 2026-09-16
# (user decision: the check belongs on the Market Data tab only, not on every tab).
# See that module for `BBG_CHECK_BUTTON_ID`, `BBG_RESULTS_ID`,
# `_bbg_diagnostics_entry_point`, `_run_bloomberg_diagnostics_placeholder`,
# `_render_bbg_results`, and `run_bloomberg_diagnostics_safe`.


_LTD_ON_RE = re.compile(r"^LTD on (\d{4}-\d{2}-\d{2}) unavailable$")


def _missing_marks_reason(conn: sqlite3.Connection, as_of: str) -> str:
    """Plain-English, actionable reason for `as_of` built from `data.bloomberg.
    inventory.mark_inventory` (a DB-only read -- it never opens a Bloomberg session,
    so it is safe to call from a UI callback per CLAUDE.md/the perf rule against
    synchronous Bloomberg probes on the request path): which mark_type(s) the book
    needs but does not have an official mark for, and how many. Returns "" when the
    book needs no marks at all, or needs marks and already has every one of them --
    in either case the caller should keep the engine's own (more specific) reason
    instead, e.g. "settled trade X: no official mark on or before its settlement"."""
    try:
        from data.bloomberg.inventory import mark_inventory, STATUS_OFFICIAL
        df = mark_inventory(conn, as_of)
    except Exception:
        return ""
    if df.empty:
        return ""
    not_official = df[df["status"] != STATUS_OFFICIAL]
    if not_official.empty:
        return ""
    mark_types = "/".join(sorted(not_official["mark_type"].unique()))
    return (f"no official {mark_types} for {as_of} "
            f"({len(not_official)} of {len(df)} needed marks) — run the Bloomberg pull")


def _resolve_reason(entry: dict, ltd_reason: str, missing_reason_for) -> str:
    """Replace a generic cross-period reason ("today's LTD unavailable" / "LTD on
    <date> unavailable") with the concrete `_missing_marks_reason` for whichever date
    is actually blocking it, so every unavailable card explains itself without making
    the user hover over LTD to find out why. `missing_reason_for(date)` is a callable
    (usually `_missing_marks_reason` bound to `conn`) so this function stays easy to
    unit test with a stub. Anything this cannot make more specific (e.g. the "a trade
    dated today has no mark from any source" trading reason) passes through as-is."""
    reason = entry.get("reason", "")
    if reason == "today's LTD unavailable":
        return ltd_reason or reason
    m = _LTD_ON_RE.match(reason)
    if m:
        return missing_reason_for(m.group(1)) or reason
    return reason


def _build_figures(conn: sqlite3.Connection, as_of: str) -> list:
    # Same pricing path as the Blotter: official marks only (2026-09-17 user decision,
    # "no bnp fall back" -- engine.pnl.valuation.value_book no longer has a BNP_BVAL
    # retry pass at all, see that module's docstring; ui.tabs.blotter_pricing's second
    # pass over the book is a no-op pending that module's own cleanup, so `df`/`n_total`
    # below are still the right numbers to read, just never "fallback-priced" any more).
    from ui.tabs.blotter_pricing import priced_value_book, scoped_period_pnl
    from ui.tabs.cash_ladder import net_gross_usd

    periods = scoped_period_pnl(conn, as_of)
    ltd_entry = dict(periods.get("ltd", {}))
    if not ltd_entry.get("available"):
        ltd_entry["reason"] = _missing_marks_reason(conn, as_of) or ltd_entry.get("reason", "")
    df, _n_fallback, n_total = priced_value_book(conn, as_of)

    cards = [_pnl_card("LTD", ltd_entry)]
    ltd_reason = ltd_entry.get("reason", "")
    for key in _PERIODS:
        entry = dict(periods.get(key, {}))
        if not entry.get("available"):
            entry["reason"] = _resolve_reason(entry, ltd_reason, lambda d: _missing_marks_reason(conn, d))
        cards.append(_pnl_card(_PERIOD_TITLES[key], entry))

    # Always computable, marks or no marks (CLAUDE.md: trade counts/positions need only
    # the blotter, not a mark) -- so the header still shows *something* concrete on a
    # database with zero official marks, per the 2026-09-17 investigation into "the
    # headline doesn't work" (root cause was missing marks alone, not a callback bug;
    # see module docstring).
    n_open = int((df["status"] == "OPEN").sum()) if not df.empty else 0
    n_settled = n_total - n_open
    cards.append(_figure_card("Trades", f"{n_total:,}", f"{n_open:,} open, {n_settled:,} settled"))

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
    # title row (user decision 2026-09-15). The "N rows on BNP file rates" note that
    # used to sit here (CSS margin-left:auto) is retired along with the BNP_BVAL
    # fallback pass itself (2026-09-17, "no bnp fall back") -- there is no other source
    # a row can be priced from any more, so the note could only ever say "0 of N".
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
    weekends/holidays (engine.pnl.calendar's own calendar) and never going earlier than
    the earliest trade_date on record (there is nothing to chart before the book
    existed, and it wastes an evaluation)."""
    from engine.pnl.calendar import _is_business_day, load_holidays

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
