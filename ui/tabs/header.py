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
    Dash never reports a native <details> toggle back to the `open` prop, so a clientside
    callback mirrors the element's DOM state into it on every click of the summary
    (`SUMMARY_ID`, `_SUMMARY_OPEN_MIRROR_JS`; see `register_callbacks`): without it the
    chart callback below never hears the collapsible open (2026-09-22).
  - The chart spans every business day from the book's first trade date (MIN(trade_date)
    in `trades`) to `as_of`, oldest first (user decision 2026-09-22: "yes I want to see
    the ltd line chart, which requires all the previous closes"; it showed the last 20
    business days before). One `_cached_ltd` evaluation per day, memoised on the database
    path and mtime, and the Details is collapsed by default with the chart built only
    while it is open, so the first open after a database change is the only time the
    full span is evaluated. The cost grows with the book: one `priced_value_book` per
    business day since the first trade (44 on the 2026-09-22 book), not a hot path.
  - Unavailable periods (NaN) keep the muted "n/a" value (existing behaviour/tests),
    but the engine's `reason` is now also a **visible** caption under the figure, not
    only an HTML `title` tooltip -- a hover-only reason is invisible on first glance,
    which is exactly what the user reported as "the headline ... doesn't work" for a
    Bloomberg-less database (2026-09-17 investigation: every figure was in fact
    computing correctly and showing a reason, but only on hover). Per CLAUDE.md
    ("Unavailable shows its reason in place").
  - `_root_reason`/`_missing_marks_reason` turn a bare "unavailable" into a concrete,
    actionable sentence -- which mark_type is missing, how many, and "run the Bloomberg
    pull" -- built from `data.bloomberg.inventory.mark_inventory` (a cheap, DB-only
    read: no Bloomberg connection is opened here). When nothing is missing (the gap is
    something else, e.g. an unrealisable settled trade), a plain fallback sentence is
    used instead (see "Partial pricing" below for the superseding aggregation logic;
    the older `scoped_period_pnl`-based "today's LTD unavailable" / "LTD on <date>
    unavailable" generic reasons this bullet originally described no longer exist).
  - The "Trades" count needs no marks at all, so it is always shown, even on a database
    with zero official marks -- the "figure that IS computable" half of the same fix.
    (FX Net / Gross USD delta were here too until 2026-09-25; see "Slim header" below.)
  - `as_of` with no date picked yet renders "No as-of date available." and skips all
    engine calls.

  Partial pricing (2026-09-17, live-Bloomberg-PC follow-up): spots/most forwards/swaps
  now price, but a handful of trades never will (options with no strike typed in yet,
  one same-day forward, one future) -- and `engine.pnl.ledger.period_pnl`/`ltd` (and
  this module's own earlier `scoped_period_pnl`-based implementation) "poison" an
  entire period to NaN the moment ANY one trade in the book is unpriced, so the whole
  headline still showed nothing. `_build_figures` no longer calls
  `ui.tabs.blotter_pricing.scoped_period_pnl` / `engine.pnl.ledger` at all; it builds
  each figure itself from `priced_value_book` frames via `_priced_single` (one date,
  LTD/Trading) and `_priced_diff` (two dates, Daily/5d/MTD/YTD/Previous day):
    - An unpriced trade contributes nothing -- never zeroed, never invented -- exactly
      per-trade behaviour is untouched (`engine/pnl/valuation.py` is not touched by
      this change at all, only how this module aggregates its already-computed
      `pnl_usd` column).
    - A period figure sums PRICED trades only and shows a visible caption "excludes N
      of M trades unpriced" with a tooltip breakdown by product and reason (e.g. "5
      options: no PREMIUM; 1 forward: no FWD_OUTRIGHT", built from value_book's own
      `reason` text via `_reason_tag`/`_unpriced_breakdown` -- nothing here invents a
      reason, it only summarises the ones already given).
    - A period DIFFERENCE (`_priced_diff`) additionally excludes any trade that exists
      in both dates' books but is priced on only one of the two -- crediting it with
      its full one-sided value would fake a one-period jump the size of its whole LTD
      the moment a mark happens to appear or vanish. A trade that is new since the
      reference date (traded after it) is not "excluded"; its full current value flows
      through normally, same as ordinary trading P&L.
    - Only when EVERY trade that could possibly contribute is unpriced does a card
      fall back to the old single "n/a" + reason card; an empty book (no trades at
      all, or none dated on/after the reference date) is 0.0/available, per
      BUILD_PLAN's "a first-day book has ltd(ref) = 0, not Unavailable".
  This is aggregation-only, scoped to the headline cards. `ui/tabs/blotter*.py`'s own
  P&L strips (via `ui.tabs.blotter_pricing`'s `priced_value_book`/`scoped_period_pnl`/
  `row_scoped_headline`, all still poisoning) are a different lane's files -- not
  edited here, see this agent's handoff report for the equivalent change they need.
  The collapsible LTD line chart (`_build_chart`/`_cached_ltd`/`_priced_day`) follows
  the same rule since 2026-09-18: a day is plotted at the sum of its priced trades with
  the excluded count in the hover text; only a day with nothing priced is a gap.

  Reference-date gap (2026-09-18, first day on the Bloomberg PC): today's book prices
  but yesterday's has no marks at all (the live pull writes today only; history comes
  from the backfill), so every trade is "priced now but unpriced on t-1" and Daily /
  Previous day / 5d / MTD showed "$0 -- excludes 771 of 772", i.e. a figure that was
  really nothing. `_priced_diff` now reports such a period as unavailable, and the
  caption (`_reference_reason`) names the REFERENCE date and what it is missing --
  "Daily needs the 2026-09-16 close: no official FWD_OUTRIGHT/SPOT for 2026-09-16 (N of
  M needed marks) — <why>" -- instead of a sentence about today.

  Why the close is missing (2026-09-21): the caption used to end "run the Bloomberg
  backfill (Market data tab)", which no one can do -- no screen has such a control; the
  backfill runs by itself after every feed cycle. It now ends with what the backfill
  itself reports in the Bloomberg status file (`backfill_status` /
  `past_close_explanation`): filling now with the days remaining, what it recorded for
  that date (no closes from Bloomberg, or the marks it could not fill and why), the
  terminal not being reachable, or that it has not reached the date yet. Text only: no
  figure changes.

  Commodity strip (commodity conversion Phase 3, 2026-09-24): gross commodity notional and
  net outright (book-positions' `commodities` block, the sectors on hover), open spreads and
  groups waiting for review (spreads-engine's `book_spreads`, memoised on the database's
  mtime, see `_spread_summary`), and the next first notice / last trade (expiry-monitor's
  `expiry_schedule`, worst first). Rendered as given; a book with no commodity futures gets
  one plain "Commodities: none" card with its reason on hover.

  Slim header (screens redesign plan, Phase A, user 2026-09-25): one row, one title line and
  one value line per card (`_header_card`). The seven P&L figures in k / m (`short_money`,
  "−$51.0k", the full figure on hover), the trade count small, then the commodity strip,
  then a "Data" chip counting the marks the book needs on the as-of date with no official
  mark (`_marks_card`, from `needed_marks`, read once per render and memoised on the
  database revision). Every sentence that used to be a visible caption is now a short marker
  beside its figure with the sentence on hover (`ui.tabs.formatting.marker`): "excl. 3" for a
  sum that leaves unpriced trades out, "filled 2" for trades valued at an earlier close,
  "ref 16 Sep" for a period stepped back from its own reference close, "review 2" for the
  groups left for review, "est." for an estimated expiry date; an n/a carries its reason on
  hover. FX Net / Gross USD delta left the header for the FX & cash tab's headline card
  ("one place per number"), so the header no longer negates the engine's net non-USD delta.

  Risk chip (screens redesign plan, Phase B, 2026-09-25): after the Data chip, the book's 1y 95%
  VaR (1-day) in k / m ("VaR $33.6k") with its blended vol as a share of the vol target beside it
  ("6.7% of target", red when over), "excl. N" when the book's series leaves positions with no
  history out, the definitions and the target (a placeholder while config/risk.yaml holds the
  macro fund's figure) on hover; "VaR n/a" with the reason on hover when risk-metrics has none.
  Every figure is `book_risk(conn, as_of)["book"]` as given. `book_risk` takes seconds, so it is
  worked out by the chip's own callback, after the figures, memoised on the database revision,
  the as-of and the history's identity (`risk_summary`); the figures show "VaR …" until then.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import threading
from functools import lru_cache
from typing import Callable, Optional

from dash import Input, Output, State, dcc, html

from ui.tabs.formatting import marker, short_money

HEADER_ID = "header-block"
CHART_CONTAINER_ID = "header-ltd-chart-container"
DETAILS_ID = "header-ltd-details"
# The <summary> inside the Details. Its n_clicks is the only thing Dash's html bundle
# reports about a click on it (dash 4.4.1 wires `n_clicks`, never the `open` prop), so
# it is what the clientside mirror in `register_callbacks` listens to.
SUMMARY_ID = f"{DETAILS_ID}-summary"
AS_OF_STORE_ID = "header-as-of-store"
# True once a date picker (Blotter or Ladder) was set to a day other than today: the header
# then stays on that day; otherwise it follows the New York calendar (user, 2026-09-22:
# "by default, always price pnl as of today, so that the top bar numbers all reflect todays
# numbers, unless changed specifically otherwise").
AS_OF_PICKED_ID = "header-as-of-picked"


def as_of_after_pick(date_value: Optional[str], today: str) -> tuple:
    """(store value, picked) after a Blotter or Ladder date picker changed to `date_value`:
    the header follows the picker; a pick of today itself (the Today buttons) is not a
    departure from the default, so the day still rolls over at New York midnight."""
    if not date_value:
        return today, False
    return date_value, date_value != today


def as_of_after_tick(store: Optional[str], picked: bool, today: str) -> Optional[str]:
    """The header's as-of after a poll tick: `today` when the store has fallen behind the New
    York calendar and no date was picked, else None (nothing to change)."""
    if picked or store == today:
        return None
    return today

PERIOD_LABELS = (
    ("value" , None),
)

# The clientside callback registered in `register_callbacks`: after each click on the
# summary it returns the Details element's own DOM `open` state, which Dash then writes
# to the `open` prop the chart callback is gated on. By the time it runs the browser's
# default toggle has completed, so the value read is the new state (keyboard activation
# of a summary fires click too). `n_clicks` is 0 (the component's default) or null on
# the initial call: nothing was clicked, nothing to mirror.
_SUMMARY_OPEN_MIRROR_JS = (
    "function(n_clicks) {\n"
    "    if (!n_clicks) { return window.dash_clientside.no_update; }\n"
    f"    var details = document.getElementById({DETAILS_ID!r});\n"
    "    if (!details) { return window.dash_clientside.no_update; }\n"
    "    return Boolean(details.open);\n"
    "}"
)

_PERIODS = ("daily", "previous_day", "d5", "mtd", "ytd", "trading")
_PERIOD_TITLES = {
    "daily": "Daily", "previous_day": "Previous day", "d5": "5d", "mtd": "MTD",
    "ytd": "YTD", "trading": "Trading",
}


# ------------------------------------------------------------------ one slim row (2026-09-25)
# Screens redesign plan, Phase A: every card is [title, value] and, only when something is
# left out, filled or stepped back, a third child holding its short markers ("excl. 3",
# "filled 2", "ref 16 Sep") with the sentence on hover. The card is a two-column grid so the
# markers sit beside the value, on the value's line: the row stays one title line and one
# value line high. Money is `short_money` ("−$51.0k"), the full figure on the value's hover.
_CARD_STYLE = {"display": "grid", "gridTemplateColumns": "auto auto", "justifyContent": "start",
               "alignItems": "baseline", "minWidth": "0", "whiteSpace": "nowrap"}
_TITLE_STYLE = {"gridColumn": "1 / -1"}
_SMALL_VALUE_STYLE = {"fontSize": "13px"}
MARKERS_CLASS = "header-markers"


def _styled_marker(short: str, sentence: str, style: Optional[dict] = None):
    m = marker(short, sentence)
    if style:
        m.style = style
    return m


def _header_card(title: str, value, value_class: str = "header-figure-value", hover: str = "",
                 value_style: Optional[dict] = None, markers=(), card_id: Optional[str] = None) -> html.Div:
    """One header card. `markers` are (short, sentence) pairs, or (short, sentence, style) for a
    marker coloured by what it says; empty shorts are dropped. `card_id` gives the card an id (the
    risk chip, whose children its own callback fills)."""
    value_props = {"className": value_class}
    if hover:
        value_props["title"] = hover
    if value_style:
        value_props["style"] = value_style
    children = [html.Div(title, className="header-figure-title", style=_TITLE_STYLE),
                html.Div(value, **value_props)]
    shown = [_styled_marker(*m) for m in markers if m[0]]
    if shown:
        children.append(html.Span(shown, className=MARKERS_CLASS))
    props = {"id": card_id} if card_id else {}
    return html.Div(className="header-figure", style=_CARD_STYLE, children=children, **props)


def _figure_card(title: str, value_text: str, caption: str = "") -> html.Div:
    """Plain informational card (no P&L sign colouring): the placeholders and the plain
    "none" cards. The caption, when there is one, is the value's hover."""
    return _header_card(title, value_text, hover=caption)


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


_EXCLUDED_COUNT_RE = re.compile(r"excludes (\d+)")


def _joined(*parts) -> str:
    return "\n".join(str(p) for p in parts if p)


def _entry_markers(entry: dict) -> list:
    """(short, sentence) markers of an available figure: "excl. N" for a sum that leaves
    unpriced trades out (the engine-side sentence, then its breakdown by product and reason,
    on hover), then any the caller built (`entry["markers"]`: the fill, a step-back)."""
    out = []
    summary = str(entry.get("excluded_summary") or "")
    if summary:
        m = _EXCLUDED_COUNT_RE.match(summary)
        out.append((f"excl. {m.group(1)}" if m else "excl.", _joined(summary, entry.get("excluded_detail"))))
    out.extend(entry.get("markers") or ())
    return out


def _pnl_card(title: str, entry: dict, colour: bool = True) -> html.Div:
    """One header figure for a P&L-shaped `{value, available, reason}` entry: the value in
    k / m (`short_money`, "−$51.0k"), green / red / white by sign when `colour` (False for a
    figure that is not a P&L, always neutral), the full figure on hover. Unavailable: a muted
    "n/a" with the reason on hover, never a blank cell and never a zero.

    Since the screens redesign (user, 2026-09-25: "a short visible marker ... with its
    sentence on hover", replacing the always-visible caption sentences of 2026-09-17), what a
    figure leaves out or borrows is a short marker beside it (`_entry_markers`): "excl. 3"
    with "excludes 3 of 12 trades unpriced" and the breakdown by product and reason on hover,
    "filled 2" and "ref 16 Sep" as `_build_figures` gives them. The figure itself is the
    engine's sum over priced trades, kept as it is."""
    if not entry.get("available"):
        return _header_card(title, "n/a", "header-figure-value header-figure-value--muted",
                            hover=str(entry.get("reason") or ""))
    value = entry["value"]
    cls = _sign_class(value) if colour else "neutral"
    return _header_card(title, short_money(value, "$"), f"header-figure-value header-figure-value--{cls}",
                        hover=_joined(_fmt_usd(value), entry.get("value_hover")),
                        markers=_entry_markers(entry))


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
            html.Summary("LTD chart", id=SUMMARY_ID, title="The LTD line over every business day since the first trade"),
            html.Div(id=CHART_CONTAINER_ID),
        ]),
    ])


# The "Check Bloomberg connection" button, its results panel, and the diagnostics
# entry point/placeholder/renderer moved to ui/tabs/market_data.py on 2026-09-16
# (user decision: the check belongs on the Market Data tab only, not on every tab).
# See that module for `BBG_CHECK_BUTTON_ID`, `BBG_RESULTS_ID`,
# `_bbg_diagnostics_entry_point`, `_run_bloomberg_diagnostics_placeholder`,
# `_render_bbg_results`, and `run_bloomberg_diagnostics_safe`.


def _missing_marks_reason(conn: sqlite3.Connection, as_of: str,
                          action: str = "run the Bloomberg pull", needs: Optional[tuple] = None) -> str:
    """Plain-English, actionable reason for `as_of` built from `data.bloomberg.
    inventory.mark_inventory` (a DB-only read -- it never opens a Bloomberg session,
    so it is safe to call from a UI callback per CLAUDE.md/the perf rule against
    synchronous Bloomberg probes on the request path): which mark_type(s) the book
    needs but does not have an official mark for, and how many. Returns "" when the
    book needs no marks at all, or needs marks and already has every one of them --
    in either case the caller should keep the engine's own (more specific) reason
    instead, e.g. "settled trade X: no official mark on or before its settlement".

    2026-09-21: the count comes from `needed_marks`, which asks a PAST date what a past
    close needed, not what a live pull would request. `needs` is `needed_marks(conn, as_of)`
    when the caller already has it (the header reads it once per render, `_needs_cached`)."""
    try:
        needed, missing = needs if needs is not None else needed_marks(conn, as_of)
    except Exception:
        return ""
    if not needed or not missing:
        return ""
    mark_types = "/".join(sorted({m["mark_type"] for m in missing}))
    return (f"no official {mark_types} for {as_of} "
            f"({len(missing)} of {needed} needed marks) — {action}")


def needed_marks(conn: sqlite3.Connection, as_of: str) -> tuple:
    """(how many marks the book needs on `as_of`, the ones with no official mark as
    [{instrument_id, settle_date, mark_type}]). DB-only reads, never a Bloomberg session.

    Two needs lists exist in `data.bloomberg.inventory`, and the date decides which applies:
      - today (the New York book date) or later: `mark_inventory`, the LIVE request list;
      - a date before today: `close_completeness(as_of, as_of)`, the PAST-close list the
        backfill fills. The live list also asks for a forward at every open option's
        expiry, which the backfill never writes for a past date by design, so counting a
        past date with it left "(N of M needed marks)" too high for good (2026-09-21).
        A past date that is not a weekday has no close row and keeps the live list.
    The Market data tab's "What is missing" and "Past closes the header needs" panels read
    this same function, so they cannot disagree with the header's sentence."""
    from data.bloomberg.inventory import STATUS_OFFICIAL, close_completeness, mark_inventory
    from data.bloomberg.live import book_today
    if as_of < book_today().isoformat():
        closes = close_completeness(conn, as_of, as_of)
        if not closes.empty:
            row = closes.iloc[0]
            return int(row["needed"]), [dict(m) for m in row["missing"]]
    df = mark_inventory(conn, as_of)
    missing = df[df["status"] != STATUS_OFFICIAL]
    return len(df), missing[["instrument_id", "settle_date", "mark_type"]].to_dict("records")


def backfill_status(conn: Optional[sqlite3.Connection]) -> dict:
    """The "backfill" block of the Bloomberg status file that sits beside the database
    `conn` has open (`data.bloomberg.live.read_status`: one small local JSON read, never a
    Bloomberg session). {} when there is nothing to read: no connection, an in-memory
    database, no status file yet, a file written before the backfill published anything,
    or the feed module mid-edit. Never raises."""
    try:
        path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        if not path:
            return {}
        from data.bloomberg.live import read_status
        block = (read_status(path) or {}).get("backfill")
        return block if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001 -- a reason sentence must never take a figure down
        return {}


_BACKFILL_REASONS_SHOWN = 2


def _plural(n, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _backfill_day_words(entry: dict, day: str) -> str:
    """What the backfill recorded for one past date (`backfill["days"][day]`:
    `{"status": DONE | NO_CLOSES | INCOMPLETE, "missing_count": n, "missing": [...]}`),
    in plain words. Repeats the backfill's own reasons, the first two only."""
    status = str(entry.get("status") or "").upper()
    if status == "NO_CLOSES":
        return f"Bloomberg returned no closes for {day} (a holiday, or no data for that date)"
    if status == "DONE":
        return f"the Bloomberg backfill reports {day} as filled, yet these marks are not on file"
    missing = entry.get("missing")
    reasons = [str(m) for m in missing if m] if isinstance(missing, (list, tuple)) else []
    try:
        count = int(entry.get("missing_count"))
    except (TypeError, ValueError):
        count = len(reasons)
    if not reasons:
        what = _plural(count, "mark") if count else "every mark"
        return f"the Bloomberg backfill reached {day} but could not fill {what} (no reason recorded)"
    shown = reasons[:_BACKFILL_REASONS_SHOWN]
    more = f" (and {count - len(shown)} more)" if count > len(shown) else ""
    what = _plural(count, "mark") if count else "every mark"
    return f"the Bloomberg backfill reached {day} but could not fill {what}: {'; '.join(shown)}{more}"


def past_close_explanation(backfill: Optional[dict], day: str) -> str:
    """Why a PAST date's close is not on file, from the backfill's own report in the
    status file (`backfill_status`). A past close only ever arrives through the backfill
    (the live pull writes today's marks), and the backfill runs by itself after every feed
    cycle (`data.bloomberg.live.LiveFeed` -> `backfill.start_auto_backfill`); no screen has
    a control that runs it, so the sentence says what is happening, never "run the
    backfill" (user, 2026-09-21: the old caption told them to do something they cannot).

    Every key may be absent (an older status file, a feed that never started). What is
    known is said, in this order, joined with "; ":
      - running           -> it is filling past closes now, with the days remaining;
      - an entry for `day` -> what the backfill recorded for that date (`_backfill_day_words`);
      - not running, with a reason -> past closes come from Bloomberg and the terminal is
        not reachable (or the last run failed), with that reason;
      - nothing known     -> it runs by itself after each pull and has not reached this date."""
    backfill = backfill if isinstance(backfill, dict) else {}
    running = bool(backfill.get("running"))
    parts = []
    if running:
        remaining = backfill.get("remaining")
        left = f" ({_plural(remaining, 'day')} remaining)" if isinstance(remaining, int) and remaining > 0 else ""
        parts.append(f"the Bloomberg backfill is filling past closes now{left}")
    days = backfill.get("days")
    entry = days.get(day) if isinstance(days, dict) else None
    if isinstance(entry, dict):
        parts.append(_backfill_day_words(entry, day))
    reason = str(backfill.get("reason") or "").strip()
    if not running and reason:
        if "failed" in reason.lower():
            parts.append(f"past closes come from the Bloomberg backfill, and its last run failed ({reason})")
        else:
            parts.append(f"past closes come from Bloomberg, and the terminal is not reachable ({reason})")
    if not parts:
        parts.append("the backfill fills past closes by itself after each Bloomberg pull; "
                     "none has reached this date yet")
    return "; ".join(parts)


def _reference_reason(conn: sqlite3.Connection, ref_iso: str, period_title: str,
                      backfill: Optional[dict] = None) -> Callable[[int, int], str]:
    """Why a period DIFFERENCE cannot be formed (2026-09-18): most trades open on the
    reference date are unpriced THERE, while today's book may be fully priced. Returns
    a `(n_blocked, n_open_then) -> sentence` builder for `_priced_diff`, naming the
    reference date, the count, and -- when the inventory can tell -- which mark types
    and how many are missing on it; it ends with what the backfill itself reports about
    that date (`past_close_explanation`), since a past day's close only ever arrives that
    way (the live pull only writes today's marks). `backfill` is the status block when the
    caller has already read it (one read for all the cards), else it is read here."""
    explanation = past_close_explanation(backfill_status(conn) if backfill is None else backfill, ref_iso)
    detail = _missing_marks_reason(conn, ref_iso, explanation)

    def build(n_blocked: int, n_open: int) -> str:
        head = (f"{period_title} needs the {ref_iso} close: {n_blocked} of {n_open} trades open that day "
                f"have no official mark dated {ref_iso}")
        return f"{head} ({detail})" if detail else f"{head} — {explanation}"
    return build


_MISSING_TAG_RE = re.compile(r"no (\S+) mark")

_PRODUCT_LABELS = {
    "FX_SPOT": "spot", "FX_FWD": "forward", "FX_SWAP": "swap",
    "FUTURE": "future", "FX_OPTION": "option",
}


def _reason_tag(reason: str) -> str:
    """Short tag extracted from one of `value_book`'s own `reason` strings, for the
    unpriced-trade breakdown tooltip -- e.g. "no PREMIUM" from "no PREMIUM mark for
    ... expiry ... on ...". Never invents a reason, only summarises the one
    `engine.pnl.valuation` already gave; anything this regex does not recognise
    (e.g. the settled-trade "cannot be frozen" reason) gets a plain fallback tag."""
    if not reason:
        return "unpriced"
    # Checked before the generic regex below: the settled-trade "cannot be frozen"
    # reason also contains the literal text "no official mark", which would otherwise
    # match _MISSING_TAG_RE first and produce the much less informative tag "no official".
    if "cannot be frozen" in reason:
        return "no historical mark at settlement"
    from ui.tabs.blotter_pricing import (
        BAD_VALUE_TAG, RETIRED_PRODUCT_MARKER, RETIRED_PRODUCT_TAG, is_bad_value_reason,
    )
    if is_bad_value_reason(reason):
        # 2026-09-18: a trade `value_book` left unpriced because a STORED figure is not a
        # number ("trade <id>: trades.price is not a number ('24-Jul')"), not because a
        # mark is missing. `_unpriced_breakdown` repeats those reasons in full.
        return BAD_VALUE_TAG
    if RETIRED_PRODUCT_MARKER in reason:
        # A leftover of a product that left on 2026-09-24 (an old database's open swap):
        # tagged as the Blotter tags it, so both "excludes" hovers say the same thing.
        return RETIRED_PRODUCT_TAG
    if "SPOT for USD conversion" in reason:
        return "no SPOT (USD conversion)"
    m = _MISSING_TAG_RE.search(reason)
    if m:
        return f"no {m.group(1)}"
    return "unpriced"


def _product_label(product: str, count: int) -> str:
    label = _PRODUCT_LABELS.get(product, str(product).lower() or "trade")
    return label if count == 1 else f"{label}s"


def _unpriced_breakdown(unpriced) -> str:
    """"5 options: no PREMIUM; 1 forward: no FWD_OUTRIGHT" -- grouped by (product, a
    short reason tag), most-affected group first. "" for no unpriced rows. A trade
    unpriced because of a bad stored value has `value_book`'s full reason appended (trade
    id, table.column, offending value -- `blotter_pricing.bad_value_detail`): that one is
    fixed in the data, not by a Bloomberg pull, so the tooltip says exactly where."""
    if unpriced.empty:
        return ""
    from ui.tabs.blotter_pricing import bad_value_detail
    tags = unpriced["reason"].map(_reason_tag)
    groups = unpriced.groupby([unpriced["product"], tags]).size().sort_values(ascending=False)
    return "; ".join(f"{count} {_product_label(product, count)}: {tag}"
                      for (product, tag), count in groups.items()) + bad_value_detail(unpriced)


_EMPTY_PRICED = {"value": 0.0, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}


def _priced_single(df, root_reason: str) -> dict:
    """{value, available, reason, excluded_summary, excluded_detail} for ONE date's
    book (LTD, Trading): the sum over PRICED trades only -- CLAUDE.md "Missing values
    stay missing": an unpriced trade contributes nothing, it is never zeroed or
    invented. All trades unpriced (book non-empty) keeps the old single "n/a" +
    `root_reason` card; an empty book is 0.0/available (BUILD_PLAN: "a first-day book
    with no prior trades has ltd(ref) = 0, not Unavailable")."""
    total = len(df)
    if total == 0:
        return dict(_EMPTY_PRICED)
    priced = df[df["reason"] == ""]
    unpriced = df[df["reason"] != ""]
    if priced.empty:
        return {"value": float("nan"), "available": False, "reason": root_reason,
                "excluded_summary": "", "excluded_detail": ""}
    value = float(priced["pnl_usd"].sum())
    if unpriced.empty:
        return {"value": value, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}
    n = len(unpriced)
    from ui.tabs.blotter_pricing import bad_value_note
    return {"value": value, "available": True, "reason": "",
            "excluded_summary": f"excludes {n} of {total} trades unpriced" + bad_value_note(unpriced),
            "excluded_detail": _unpriced_breakdown(unpriced)}


def _priced_diff(df_a, df_b, root_reason: str, ref_label: str,
                 ref_reason: Optional[Callable[[int, int], str]] = None) -> dict:
    """{value, available, reason, excluded_summary, excluded_detail} for LTD(a) -
    LTD(b), `b` the earlier reference date's book. "Nothing invented, nothing faked"
    (2026-09-17 live-Bloomberg-PC follow-up):
      - a trade only present in `a` (traded after `b`) contributes its full `a` value
        when priced -- it is a new trade entering the book, not a pricing artefact, so
        it is not "excluded".
      - a trade present in both books contributes normally only when priced in BOTH.
      - a trade present in both but priced in only one of the two is EXCLUDED from the
        diff outright (contributes nothing) rather than credited with its full
        one-sided value, which would fake a jump the size of its whole LTD on
        whichever single day a mark happened to appear or vanish.
      - (2026-09-18) when the blocked trades OUTNUMBER the trades priced at both ends --
        i.e. most of what was open on the reference date is unpriced there -- the
        difference is anchored by a minority of the book (on the first Bloomberg day: one
        settled trade against 768 blocked), so the figure is unavailable with
        `ref_reason(n_blocked, n_open_then)` (naming the reference date, see
        `_reference_reason`) rather than a "$0, excludes 771 of 772" that reads like a
        number but is really nothing. A minority of blocked trades keeps the partial
        figure with its caption, as before."""
    total = len(df_a)
    if total == 0:
        return dict(_EMPTY_PRICED)

    a_priced = df_a[df_a["reason"] == ""]
    a_unpriced = df_a[df_a["reason"] != ""]
    a_priced_ids = set(a_priced["trade_id"])

    if df_b.empty:
        b_priced_ids, b_unpriced_ids, b_pnl = set(), set(), {}
    else:
        b_priced = df_b[df_b["reason"] == ""]
        b_priced_ids = set(b_priced["trade_id"])
        b_unpriced_ids = set(df_b[df_b["reason"] != ""]["trade_id"])
        b_pnl = dict(zip(b_priced["trade_id"], b_priced["pnl_usd"]))

    blocked_ids = a_priced_ids & b_unpriced_ids  # priced now, unpriced back then -- excluded
    contributing_a_ids = a_priced_ids - blocked_ids
    contributing_b_ids = a_priced_ids & b_priced_ids  # priced at both ends

    if blocked_ids and len(blocked_ids) > len(contributing_b_ids):
        n_blocked, n_open = len(blocked_ids), len(blocked_ids) + len(contributing_b_ids)
        reason = (ref_reason(n_blocked, n_open) if ref_reason is not None else
                  f"{n_blocked} of {n_open} trades open on {ref_label} have no mark there; the reference close is missing")
        return {"value": float("nan"), "available": False, "reason": reason,
                "excluded_summary": "", "excluded_detail": ""}

    if not contributing_a_ids:
        return {"value": float("nan"), "available": False, "reason": root_reason,
                "excluded_summary": "", "excluded_detail": ""}

    a_sum = float(a_priced[a_priced["trade_id"].isin(contributing_a_ids)]["pnl_usd"].sum())
    b_sum = sum(b_pnl[t] for t in contributing_b_ids)
    value = a_sum - b_sum

    a_unpriced_ids = set(a_unpriced["trade_id"])
    n_excluded = len(a_unpriced_ids) + len(blocked_ids)
    if n_excluded == 0:
        return {"value": value, "available": True, "reason": "", "excluded_summary": "", "excluded_detail": ""}

    detail = _unpriced_breakdown(a_unpriced)
    if blocked_ids:
        note = f"{len(blocked_ids)} priced now but unpriced on {ref_label}"
        detail = f"{detail}; {note}" if detail else note
    from ui.tabs.blotter_pricing import bad_value_note
    return {"value": value, "available": True, "reason": "",
            "excluded_summary": (f"excludes {n_excluded} of {total} trades unpriced" if not blocked_ids else
                                 f"excludes {n_excluded} of {total} trades: {len(a_unpriced_ids)} unpriced today, "
                                 f"{len(blocked_ids)} with no price on {ref_label}") + bad_value_note(a_unpriced),
            "excluded_detail": detail}


def _failure_reason(what: str, exc: Exception, conn: Optional[sqlite3.Connection]) -> str:
    """"<what> (ValueError: could not convert string to float: '24-Jul'). Stored values
    that are not numbers: trade_legs.amount: 1 value ... '24-Jul' (trade_id 928825333
    leg_no 1); to fix: re-upload the blotter" -- the exception, plus every stored figure
    that is not a number named by table.column, row and value
    (`blotter_pricing.describe_bad_stored_values`). The bare float message the user got on
    2026-09-18 named neither the trade nor the column. The scan never raises."""
    text = f"{what} ({type(exc).__name__}: {exc})"
    try:
        from ui.tabs.blotter_pricing import describe_bad_stored_values
        found = describe_bad_stored_values(conn) if conn is not None else ""
    except Exception:  # noqa: BLE001 -- diagnosis only
        found = ""
    return f"{text}. Stored values that are not numbers: {found}" if found else text


def _root_reason(conn: sqlite3.Connection, as_of: str, needs: Optional[tuple] = None) -> str:
    """`_missing_marks_reason` when that can explain the gap, else a plain fallback --
    used as the Unavailable-card reason when nothing at all is priced/contributing for
    a given date."""
    return (_missing_marks_reason(conn, as_of, needs=needs)
            or f"every trade on {as_of} is missing a mark from any source")


_NEEDS_MEMO: dict = {}
_NEEDS_MEMO_MAX = 64


def _needs_cached(conn: sqlite3.Connection, as_of: str) -> tuple:
    """`needed_marks(conn, as_of)`, memoised on (database path, mtime, as_of, the New York
    book date) like `_spread_summary`: the book date is in the key because it decides which
    needs list applies (a past close's or the live pull's). An in-memory database is not
    memoised. Raises what `needed_marks` raises; nothing is memoised then."""
    key = _db_revision(conn)
    if key is None:
        return needed_marks(conn, as_of)
    from data.bloomberg.live import book_today
    memo_key = (*key, as_of, book_today().isoformat())
    hit = _NEEDS_MEMO.get(memo_key)
    if hit is None:
        if len(_NEEDS_MEMO) >= _NEEDS_MEMO_MAX:
            _NEEDS_MEMO.clear()
        hit = _NEEDS_MEMO[memo_key] = needed_marks(conn, as_of)
    return hit


def _build_figures(conn: sqlite3.Connection, as_of: str) -> list:
    # Official marks only (2026-09-17 user decision, "no bnp fall back" --
    # engine.pnl.valuation.value_book no longer has a BNP_BVAL retry pass at all, see
    # that module's docstring). Builds every figure itself from priced_value_book
    # frames (never engine.pnl.ledger / ui.tabs.blotter_pricing.scoped_period_pnl,
    # both of which poison an entire period to NaN if any one trade is unpriced) --
    # see this module's docstring, "Partial pricing".
    from engine.pnl.calendar import (
        _last_business_day_of_prev_month, _last_business_day_of_prev_year,
        _n_business_days_back, _prev_business_day, load_holidays,
    )
    from ui.tabs.blotter_pricing import priced_value_book

    holidays = load_holidays()
    df_today, _n_filled, n_total = priced_value_book(conn, as_of)
    # The marks the book needs on `as_of`, read once per render (and memoised on the database
    # revision): the root reason of the cards valued on `as_of` and the "Data" chip.
    try:
        needs_today = _needs_cached(conn, as_of)
    except Exception:  # noqa: BLE001 -- the chip says why; the reasons fall back as before
        needs_today = None
    root_reasons = {as_of: _root_reason(conn, as_of, needs_today)}

    def _root(iso: str) -> str:
        if iso not in root_reasons:
            root_reasons[iso] = _root_reason(conn, iso)
        return root_reasons[iso]

    ltd_entry = _priced_single(df_today, root_reasons[as_of])
    # The fill (user decision 2026-09-21): trades with no price on `as_of` are valued at their
    # last earlier close. "filled N" beside the figure, the sentence and each trade's own note
    # on hover -- never silent.
    from engine.pnl.reference import fill_caption
    n_filled_today = _filled_count(df_today)
    if n_filled_today:
        ltd_entry["markers"] = [(f"filled {n_filled_today}",
                                 _joined(fill_caption(df_today, as_of), _fill_notes(df_today)))]
    cards = [_pnl_card("LTD", ltd_entry)]

    d = dt.date.fromisoformat(as_of)
    t1 = _prev_business_day(d, holidays)
    t2 = _prev_business_day(t1, holidays)
    t1_iso, t2_iso = t1.isoformat(), t2.isoformat()
    df_t1, _, _ = priced_value_book(conn, t1_iso)

    ref_dates = {
        "daily": t1_iso,
        "d5": _n_business_days_back(d, 5, holidays).isoformat(),
        "mtd": _last_business_day_of_prev_month(d, holidays).isoformat(),
        "ytd": _last_business_day_of_prev_year(d, holidays).isoformat(),
    }
    entries = {}
    backfill = backfill_status(conn)  # one read of the status file for every card's missing-close reason
    # A reference close with no value steps back to the previous business day that has
    # one (user decision 2026-09-21, engine.pnl.reference): the date the period is
    # measured from moves and the card says so ("ref 16 Sep"); no mark is copied for any trade.
    from engine.pnl.reference import annotate, resolve_reference

    def _book(iso: str):
        return priced_value_book(conn, iso)[0]  # per-date cached

    def _period(df_a, a_iso: str, ref_iso: str, key: str) -> dict:
        title = _PERIOD_TITLES[key]
        choice = resolve_reference(df_a, ref_iso, _book, holidays, frames_filled=True)  # `_book` frames carry the fill
        entry = _priced_diff(df_a, choice.frame, _root(a_iso), choice.ref_date_used,
                             _reference_reason(conn, ref_iso, title, backfill))
        entry = annotate(entry, choice,
                         lambda s: _reference_reason(conn, s.date, title, backfill)(s.n_blocked, s.n_open_then))
        if entry.get("available"):
            entry["markers"] = _reference_markers(entry, choice)
        return entry

    for key in ("daily", "d5", "mtd", "ytd"):
        entries[key] = _period(df_today, as_of, ref_dates[key], key)
    entries["previous_day"] = _period(df_t1, t1_iso, t2_iso, "previous_day")

    trading_rows = df_today[df_today["trade_date"] == as_of] if not df_today.empty else df_today
    entries["trading"] = _priced_single(
        trading_rows, "a trade dated today has no mark from any source")

    for key in _PERIODS:
        cards.append(_pnl_card(_PERIOD_TITLES[key], entries[key]))

    # Always computable, marks or no marks (CLAUDE.md: trade counts need only the blotter,
    # not a mark). Small, with open / settled on hover.
    n_open = int((df_today["status"] == "OPEN").sum()) if not df_today.empty else 0
    n_settled = n_total - n_open
    cards.append(_header_card("Trades", f"{n_total:,}", "header-figure-value header-figure-value--neutral",
                              hover=f"{n_open:,} open, {n_settled:,} settled", value_style=_SMALL_VALUE_STYLE))

    # FX Net / Gross USD delta left the header on 2026-09-25 (screens redesign plan: "one
    # place per number"; they are the FX & cash tab's headline card). The commodity strip
    # follows the P&L, then the chip for the marks the book is missing on the as-of date.
    cards.append(_divider())
    cards.extend(_commodity_cards(conn, as_of))
    marks = _marks_card(conn, as_of, needs_today)
    if marks is not None:
        cards.append(marks)
    # The risk chip (screens redesign Phase B): never worked out here, since `book_risk` reads the
    # history (seconds): the memoised result when this database revision and as-of already have
    # one, else "VaR …" until the chip's own callback, chained after this one, fills it in.
    cards.append(_risk_card(_risk_summary_if_ready(conn, as_of)))
    return cards


def _fill_notes(frame, trade_ids=None) -> str:
    """Each filled trade's own note ("<id>: no price on <date>: value of the <earlier> close
    (<why>)"), one per line, the first 40 and a count of the rest."""
    from engine.pnl.reference import filled_from
    if frame is None or frame.empty or "note" not in frame.columns:
        return ""
    notes = [f"{r.trade_id}: {r.note}" for r in frame.itertuples()
             if filled_from(r.note) and (trade_ids is None or r.trade_id in trade_ids)]
    return "\n".join(notes[:40] + ([f"and {len(notes) - 40} more"] if len(notes) > 40 else []))


def _short_date(iso: str) -> str:
    """'16 Sep' from '2026-09-16'; the ISO text as it is when it is not a date."""
    try:
        d = dt.date.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{d.day} {d:%b}"


def _reference_markers(entry: dict, choice) -> list:
    """The markers of a period figure measured from a reference close (`resolve_reference`'s
    choice, `annotate`'s entry): "filled N" when trades took their value from an earlier close
    than the one used (the close and each trade's note on hover), and "ref 16 Sep" when the
    period stepped back from its own reference date (the full sentence and each skipped
    date's reason on hover). Read off the choice and the frame's notes, nothing recomputed."""
    from engine.pnl.reference import filled_days
    markers = []
    ids = choice.split.contributing_b_ids
    filled = choice.filled or filled_days(choice.frame, ids)
    n = sum(count for _day, count in filled)
    if n:
        sentence = choice.fill_note or (
            f"{_plural(n, 'trade')} with no price on {choice.ref_date_used} measured from "
            f"{'their' if n != 1 else 'its'} last earlier close (back to {filled[-1][0]})")
        markers.append((f"filled {n}", _joined(sentence, _fill_notes(choice.frame, ids))))
    if choice.stepped_back:
        markers.append((f"ref {_short_date(choice.ref_date_used)}",
                        _joined(entry.get("ref_note"), entry.get("ref_note_detail"))))
    return markers


# ------------------------------------------------------------------ the commodity strip
# Commodity conversion Phase 3 (2026-09-24), compacted for the slim header on 2026-09-25
# (screens redesign plan: "gross commodity notional, net outright, open spreads, and chips for
# the next expiry event, the marks missing"). Each figure is its engine's output as given,
# nothing recomputed:
#   1. gross commodity notional (USD): book-positions' `commodities.gross_usd`;
#   2. net outright: its `net_usd`, each sector's `net_usd` (the Blotter's Positions table's
#      figures) and each commodity's on hover;
#   3. open spreads: spreads-engine's `book_spreads` positions with status 'open', with "review N"
#      for the groups waiting for review, the names on hover;
#   4. a chip for the next first notice / last trade: expiry-monitor's worst-first first row,
#      coloured by its level.
# A book with no commodity futures gets one plain "Commodities: none" card with its reason on
# hover. Then (`_marks_card`) a chip for the marks the book needs on the as-of date that have
# no official mark.

COMMODITY_EMPTY_TITLE = "Commodities"
GROSS_NOTIONAL_TITLE = "Gross notional"
NET_BY_SECTOR_TITLE = "Net outright"
OPEN_SPREADS_TITLE = "Open spreads"
NEXT_EXPIRY_TITLE = "Next expiry"
MARKS_TITLE = "Data"

_SECTOR_LABELS = {"agriculture": "Ags"}
# A chip: a small outlined pill on the navy header, coloured by what it says.
_CHIP_STYLE = {"display": "inline-block", "fontSize": "12px", "fontWeight": 700, "lineHeight": "18px",
               "padding": "0 7px", "borderRadius": "9px", "border": "1px solid currentColor"}
# Level colours on the dark header (the Expiries tab's own palette is for a light table).
_LEVEL_STYLES = {
    "EXPIRED": {"color": "#ffffff", "backgroundColor": "#7f1d1d", "padding": "0 4px", "borderRadius": "3px"},
    "RED": {"color": "#f87171"},
    "AMBER": {"color": "#fbbf24"},
    "GREEN": {"color": "#4ade80"},
}
_MUTED_CHIP = {"color": "rgba(255,255,255,.6)"}
_EXPIRY_ROWS_ON_HOVER = 5
_SPREAD_NAMES_ON_HOVER = 30
_MISSING_MARKS_ON_HOVER = 12


def _chip_style(colours: dict) -> dict:
    return {**_CHIP_STYLE, **colours}


def _sector_label(sector: str) -> str:
    return _SECTOR_LABELS.get(str(sector), str(sector).replace("_", " ").capitalize())


def _fmt_compact(value) -> str:
    """'+12.3m', '−4.1m', '+812k', 'n/a' for None / NaN: the sector line's short form."""
    if value is None or value != value:
        return "n/a"
    a = abs(float(value))
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    for div, suffix in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if round(a / div, 1) >= 1:
            return f"{sign}{a / div:.1f}{suffix}"
    return f"{sign}{a:.0f}"


def _fmt_usd_or_na(value) -> str:
    return "n/a" if value is None or value != value else _fmt_usd(float(value))


def _short_reason(reason: str) -> str:
    """The head of a book-positions reason: 'excludes 1 of 3 commodities with no USD figure'
    out of '... : COMEX copper (COMEX:HG): <why>'."""
    return str(reason or "").split(": ", 1)[0]


def _commodity_detail(block: dict) -> str:
    """Hover text: each sector's net and gross, then each commodity's lots and net USD, with the
    reason of any figure that is n/a."""
    lines = []
    for s in block.get("sectors") or []:
        line = (f"{_sector_label(s.get('sector'))}: net {_fmt_usd_or_na(s.get('net_usd'))}, "
                f"gross {_fmt_usd_or_na(s.get('gross_usd'))}")
        lines.append(line + (f" ({s['reason']})" if s.get("reason") else ""))
        for c in s.get("commodities") or []:
            lots = c.get("net_lots")
            lots_text = f"{lots:+,.0f} lots" if lots is not None else "lots n/a"
            text = f"  {c.get('name') or c.get('root_id')}: {lots_text}, net {_fmt_usd_or_na(c.get('net_usd'))}"
            if c.get("net_usd") is None and c.get("reason"):
                text += f" ({c['reason']})"
            lines.append(text)
    return "\n".join(lines)


def _sector_line(block: dict) -> str:
    """'Metals −928.8k · Energy +32.6k · Ags +334.7k', each sector's own net_usd in
    book-positions' order."""
    return " · ".join(f"{_sector_label(s.get('sector'))} {_fmt_compact(s.get('net_usd'))}"
                      for s in block.get("sectors") or [])


def _commodity_entry(block: dict, key: str, value_hover: str) -> dict:
    """A `_pnl_card` entry for book-positions' `key` figure: n/a with its reason when the
    engine has none, else the figure with "excl. N" (the commodities with no USD figure, the
    engine's own sentence on hover)."""
    value, reason = block.get(key), str(block.get("reason") or "")
    if not block.get("available") or value is None:
        return {"available": False, "reason": reason or "no commodity position has a USD figure"}
    entry = {"value": float(value), "available": True, "value_hover": value_hover}
    missing = block.get("missing") or []
    if reason and missing:
        entry["markers"] = [(f"excl. {len(missing)}", reason)]
    elif reason:
        entry["markers"] = [("excl.", reason)]
    return entry


def _gross_notional_card(block: dict) -> html.Div:
    return _pnl_card(GROSS_NOTIONAL_TITLE, _commodity_entry(block, "gross_usd", _commodity_detail(block)),
                     colour=False)


def _net_by_sector_card(block: dict) -> html.Div:
    """Net outright, the sector breakdown on hover (user, 2026-09-25)."""
    return _pnl_card(NET_BY_SECTOR_TITLE,
                     _commodity_entry(block, "net_usd", _joined(_sector_line(block), _commodity_detail(block))))


def _db_revision(conn: sqlite3.Connection) -> Optional[tuple]:
    """(path, mtime) of the connection's main database file, None for an in-memory one or when
    it cannot be read (then nothing is memoised). The mtime changes on every write
    (journal_mode=delete, CLAUDE.md "Guard rails"), as for `_cached_ltd`."""
    try:
        path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        return (str(path), os.path.getmtime(path)) if path else None
    except Exception:  # noqa: BLE001 -- no memo, never a failure
        return None


_SPREADS_MEMO: dict = {}
_SPREADS_MEMO_MAX = 64


def _spread_summary_uncached(conn: sqlite3.Connection, as_of: str) -> dict:
    """{open: [label], review: [reason], reasons: [sentence], error: ''} from `book_spreads`:
    `open` is one label per open *position* (its `positions` list, 2026-09-25), not per spread,
    valued through the header's own reader (`priced_value_book`, cached per date, so the
    closes the P&L cards already read are not valued again). Only the counts and names are
    shown; the spreads' P&L is the Spreads tab's."""
    try:
        from engine.spreads import book_spreads
        from ui.tabs.blotter_pricing import priced_value_book
        out = book_spreads(conn, as_of, value_fn=priced_value_book)
    except Exception as exc:  # noqa: BLE001 -- the card says why, the other cards still show
        import logging
        logging.getLogger(__name__).exception("header open spreads failed for as_of=%s", as_of)
        return {"open": [], "review": [], "reasons": [],
                "error": _failure_reason("spreads could not be grouped", exc, conn)}
    opened = []
    for p in out.get("positions") or []:
        # one per position (the same spread put on over several trade dates is one), as the
        # Spreads tab's main table shows them (screens redesign plan, "One place per number")
        if p.get("status") != "open":
            continue
        label = f"{p.get('name') or p.get('position_id')}"
        if p.get("direction"):
            label += f" {p['direction']}"
        members = len(p.get("spread_ids") or [])
        where = ", ".join(p.get("trade_dates") or [])
        detail = [f"{members} entries"] if members > 1 else []
        if where:
            detail.append(f"traded {where}")
        opened.append(label + (f" ({', '.join(detail)})" if detail else ""))
    return {"open": opened, "review": [str(r.get("reason") or r.get("review_id") or "") for r in out.get("review") or []],
            "reasons": [str(r) for r in out.get("reasons") or []], "error": ""}


def _spread_summary(conn: sqlite3.Connection, as_of: str) -> dict:
    """`_spread_summary_uncached`, memoised on (database path, mtime, as_of) like the LTD chart's
    `_cached_ltd`: `book_spreads` values every group's P&L periods, about 1 s on the synthetic
    sample against 0.15 s for the whole header warm (measured 2026-09-24), so it runs once per
    database revision and as-of, not on every render."""
    key = _db_revision(conn)
    if key is None:
        return _spread_summary_uncached(conn, as_of)
    memo_key = (*key, as_of)
    hit = _SPREADS_MEMO.get(memo_key)
    if hit is None:
        if len(_SPREADS_MEMO) >= _SPREADS_MEMO_MAX:
            _SPREADS_MEMO.clear()
        hit = _SPREADS_MEMO[memo_key] = _spread_summary_uncached(conn, as_of)
    return hit


def _open_spreads_card(summary: dict) -> html.Div:
    """The count of open spread positions, the names on hover, and "review N" for the groups the rule
    left for review (their reasons on hover)."""
    if summary.get("error"):
        return _pnl_card(OPEN_SPREADS_TITLE, {"available": False, "reason": summary["error"]}, colour=False)
    opened, review, reasons = summary.get("open") or [], summary.get("review") or [], summary.get("reasons") or []
    lines = []
    if opened:
        shown = opened[:_SPREAD_NAMES_ON_HOVER]
        lines += ["Open spreads:"] + [f"- {n}" for n in shown]
        if len(opened) > len(shown):
            lines.append(f"- and {len(opened) - len(shown)} more")
    else:
        lines.append("No open spread in the book.")
    review_lines = []
    if review:
        review_lines = ([f"{_plural(len(review), 'group')} waiting for review "
                         "(left as outrights until bundled):"] + [f"- {r}" for r in review])
    hover = "\n".join(lines + review_lines + reasons)
    markers = [(f"review {len(review)}", "\n".join(review_lines))] if review else []
    return _header_card(OPEN_SPREADS_TITLE, f"{len(opened):,}", "header-figure-value header-figure-value--neutral",
                        hover=hover, markers=markers)


def _contract_label(contract_id: str) -> str:
    text = str(contract_id or "")
    return text[:-len(" Comdty")] if text.endswith(" Comdty") else text


def _business_days_words(row: dict) -> str:
    """'in 8 business days', or 'alert 2026-10-01, in 8 business days' when expiry-monitor
    counts to an alert date held earlier than the event (an estimated physical contract);
    '' for EXPIRED, whose count runs to a date already past."""
    n, alert, event_date = row.get("business_days"), row.get("alert_date"), row.get("next_event_date")
    if row.get("level") == "EXPIRED":
        return ""
    if n is None:
        return "business days not countable"
    words = "today" if n == 0 else f"in {_plural(n, 'business day')}"
    return f"alert {alert}, {words}" if alert and alert != event_date else words


def _business_days_short(row: dict) -> str:
    """The chip's count: '8 bd', 'today', 'expired', 'bd n/a' (the reason on hover)."""
    n = row.get("business_days")
    if row.get("level") == "EXPIRED":
        return "expired"
    if n is None:
        return "bd n/a"
    return "today" if n == 0 else f"{n} bd"


def _next_expiry_card(schedule: dict) -> html.Div:
    """The chip for expiry-monitor's worst-first first row: 'HGZ26 first notice · 2 bd',
    coloured by its level, the dates, the level, the next rows and the counts on hover; "est."
    beside it while the date is contract-master's estimate."""
    rows = schedule.get("rows") or []
    if not rows:
        return _figure_card(NEXT_EXPIRY_TITLE, "none",
                            schedule.get("note") or "no open commodity futures position")
    r = rows[0]   # worst first: level, then fewest business days (expiry-monitor's order)
    level = str(r.get("level") or "")
    event_date = r.get("next_event_date") or "date unknown"
    lines = [f"{r.get('contract_id')}: {r.get('next_event')} {event_date}"
             + (" (estimated)" if r.get("estimated") else "") + f", {level}"
             + "".join(f", {p}" for p in (_business_days_words(r),) if p)]
    if r.get("alert_date"):
        lines.append(f"counted to the alert date {r['alert_date']} ({r.get('alert_basis') or r.get('next_event')})")
    if r.get("reason"):
        lines.append(str(r["reason"]))
    counts = schedule.get("counts") or {}
    if counts:
        lines.append("Open contracts by level: " + ", ".join(f"{n} {lvl}" for lvl, n in counts.items()))
    later = rows[1:1 + _EXPIRY_ROWS_ON_HOVER]
    if later:
        lines.append("Next:")
        lines += [f"- {x.get('contract_id')}: {x.get('next_event')} {x.get('next_event_date') or 'date unknown'}"
                  + (" (est.)" if x.get("estimated") else "")
                  + "".join(f", {p}" for p in (_business_days_words(x), x.get("level")) if p) for x in later]
    settled = schedule.get("settled_expired") or []
    if settled:
        lines.append(f"{_plural(len(settled), 'expired contract')} settled by the ledger: not alerts")
    lines.append("The Expiries tab lists every open contract.")
    hover = "\n".join(lines)
    text = " · ".join(p for p in (f"{_contract_label(r.get('contract_id'))} {r.get('next_event') or ''}".strip(),
                                  _business_days_short(r)) if p)
    markers = [("est.", f"{event_date} is contract-master's estimate until Bloomberg's contract dates are "
                        "on file")] if r.get("estimated") else []
    return _header_card(NEXT_EXPIRY_TITLE, text,
                        f"header-figure-value header-figure-value--level-{level.lower() or 'unknown'}",
                        hover=hover, value_style=_chip_style(_LEVEL_STYLES.get(level, _MUTED_CHIP)),
                        markers=markers)


def _marks_card(conn: sqlite3.Connection, as_of: str, needs: Optional[tuple]) -> Optional[html.Div]:
    """The chip for the marks the book needs on `as_of` with no official mark on file
    (`needed_marks`, read once per render and memoised on the database revision,
    `_needs_cached`): "31 marks missing" (amber), "marks complete" (green), "marks n/a" with the
    reason when the list could not be read. None when the book needs no mark at all. The Data
    tab lists each one; this only counts them."""
    if needs is None:
        try:
            needs = _needs_cached(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- the chip says why
            return _header_card(MARKS_TITLE, "marks n/a", "header-figure-value", value_style=_chip_style(_MUTED_CHIP),
                                hover=_failure_reason(f"the marks the book needs on {as_of} could not be listed",
                                                      exc, conn))
    needed, missing = needs
    if not needed:
        return None
    if not missing:
        return _header_card(MARKS_TITLE, "marks complete", "header-figure-value",
                            value_style=_chip_style(_LEVEL_STYLES["GREEN"]),
                            hover=f"every one of the {needed:,} marks the book needs on {as_of} is on file (official)")
    by_type: dict = {}
    for m in missing:
        by_type[m["mark_type"]] = by_type.get(m["mark_type"], 0) + 1
    kinds = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0])))
    shown = missing[:_MISSING_MARKS_ON_HOVER]
    lines = [f"{len(missing):,} of {needed:,} marks the book needs on {as_of} have no official mark ({kinds})."]
    lines += [f"- {m['instrument_id']} {m['mark_type']} {m['settle_date']}" for m in shown]
    if len(missing) > len(shown):
        lines.append(f"- and {len(missing) - len(shown)} more")
    lines.append("The Data tab lists each one; the figures above say which trades they leave out.")
    return _header_card(MARKS_TITLE, f"{len(missing):,} {'mark' if len(missing) == 1 else 'marks'} missing",
                        "header-figure-value", value_style=_chip_style(_LEVEL_STYLES["AMBER"]),
                        hover="\n".join(lines))


# ------------------------------------------------------------------ the risk chip (Phase B)
# Screens redesign plan, Phase B (user, 2026-09-25): "chips for ... (Phase B) VaR against the vol
# target". The book's 1y VaR (1-day) and its blended vol as a share of the vol target, exactly as
# risk-metrics' `book_risk(conn, as_of)["book"]` gives them (the Risk tab's own cards); nothing
# here computes a metric. `book_risk` reads the history files and takes seconds (about 7 s on the
# golden book, mostly the research history's constant-maturity series), so:
#   - `_build_figures` never calls it: it shows the memoised result when there is one for this
#     database revision, as-of and history, else "VaR …" (pending, the reason on hover);
#   - the chip's own callback (`register_callbacks`), chained on the figures' output so it runs
#     after them and never competes with their first paint, works it out once per key and fills
#     the chip in place (`VAR_CHIP_ID`).

RISK_TITLE = "Risk"
VAR_CHIP_ID = "header-var-chip"
_RISK_MEMO: dict = {}
_RISK_MEMO_MAX = 16
# (path, mtime, as_of) -> the last summary worked out for it, whatever the history's identity:
# what `_build_figures` shows without touching the history files (its first load is ~0.4 s), the
# chained callback then checking it against the history as it is now.
_RISK_LATEST: dict = {}
_RISK_LOCK = threading.Lock()   # one `book_risk` at a time: a second request for the same key waits, then hits
_NEUTRAL_CHIP = {"color": "#ffffff"}
_OVER_TARGET_MARKER = {"color": "#ffffff", "background": "#b91c1c", "fontWeight": 700}
_LEFT_OUT_ON_HOVER = 12
_BOOK_KEYS = ("var95_1d_usd", "vol_blended_ann_usd", "vol_trailing_ann_usd", "vol_crisis_ann_usd",
              "vol_vs_target_pct", "over_vol_target", "vol_note", "lag2_date", "reason", "reasons",
              "rows_in_series", "first_date", "last_date", "days")
_CONFIG_KEYS = ("vol_target_usd", "vol_target_placeholder", "vol_target_note", "var_window_bd",
                "var_confidence", "blended")


def _risk_inputs() -> tuple:
    """(history, commodity history, config, identity): risk-metrics' own loaders (each cached on
    its files' mtimes, about 10 ms warm), so the memo key moves when the history or
    config/risk.yaml changes, not only when the book does."""
    from engine.risk.commodity_history import load_commodity_history
    from engine.risk.config import load_config
    from engine.risk.history import load_history
    history, commodity, config = load_history(), load_commodity_history(), load_config()
    ident = (history.path, history.last_date, commodity.path, commodity.first_date, commodity.last_date,
             json.dumps(config, sort_keys=True, default=str))
    return history, commodity, config, ident


def _slim_risk(result: dict) -> dict:
    """What the chip reads of `book_risk`'s result, as given: the book's figures, the config's
    target and definitions, and the positions its series leaves out (the parts with no history
    series, and the contracts left out of a commodity's series), read off the engine's own flags."""
    book = result.get("book") or {}
    in_series = set(book.get("rows_in_series") or [])
    parts = [u for u in result.get("underlyers") or [] if u.get("role") == "part"]
    left_out = [(str(u.get("underlyer")), str(u.get("reason") or "no history series"))
                for u in parts if u.get("underlyer") not in in_series]
    contracts_out = [(str(c.get("contract_id")), str(c.get("reason") or "no history series"))
                     for u in parts if u.get("kind") == "COMMODITY" and u.get("underlyer") in in_series
                     for c in u.get("contracts") or [] if not c.get("in_series", True)]
    config = result.get("config") or {}
    return {"as_of": result.get("as_of"), "book": {k: book.get(k) for k in _BOOK_KEYS},
            "config": {k: config.get(k) for k in _CONFIG_KEYS},
            "left_out": left_out, "contracts_out": contracts_out, "error": ""}


def _risk_summary_uncached(conn: sqlite3.Connection, as_of: str, inputs: tuple) -> dict:
    history, commodity, config, _ident = inputs
    try:
        from engine.risk import book_risk
        from ui.tabs.blotter_pricing import pricing_snapshot
        with pricing_snapshot(conn, "Header risk chip"):   # one view of the marks for the whole result
            result = book_risk(conn, as_of, history=history, commodity_history=commodity, config=config)
    except Exception as exc:  # noqa: BLE001 -- the chip says why, the other cards still show
        logging.getLogger(__name__).exception("header risk chip failed for as_of=%s", as_of)
        return {"error": _failure_reason("the book's risk could not be computed", exc, conn)}
    return _slim_risk(result)


def _risk_key(conn: sqlite3.Connection, as_of: str) -> tuple:
    """(memo key or None for an in-memory database, the inputs)."""
    inputs = _risk_inputs()
    rev = _db_revision(conn)
    return ((*rev, as_of, inputs[3]) if rev is not None else None), inputs


def risk_summary(conn: sqlite3.Connection, as_of: str) -> dict:
    """The chip's reading of `book_risk(conn, as_of)` (`_slim_risk`), memoised on (database path,
    mtime, as_of, the history's and config's identity). A failure is returned as {"error":
    sentence} and not memoised, so the next render tries again."""
    try:
        key, inputs = _risk_key(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- an unreadable config or history is a reason
        return {"error": _failure_reason("the risk history or config/risk.yaml could not be read", exc, conn)}
    if key is None:
        return _risk_summary_uncached(conn, as_of, inputs)
    hit = _RISK_MEMO.get(key)
    if hit is not None:
        return hit
    with _RISK_LOCK:
        hit = _RISK_MEMO.get(key)
        if hit is None:
            hit = _risk_summary_uncached(conn, as_of, inputs)
            if not hit.get("error"):
                if len(_RISK_MEMO) >= _RISK_MEMO_MAX:
                    _RISK_MEMO.clear()
                    _RISK_LATEST.clear()
                _RISK_MEMO[key] = hit
                _RISK_LATEST[key[:3]] = hit
    return hit


def _risk_summary_if_ready(conn: sqlite3.Connection, as_of: str) -> Optional[dict]:
    """The last `risk_summary` worked out for this database revision and as-of, or None when
    there is none yet. Never computes and never reads the history files (a stat of the database
    only), so the figures' render costs nothing more; the chip's callback, which runs next,
    refreshes it if the history or the config changed since."""
    rev = _db_revision(conn)
    return _RISK_LATEST.get((*rev, as_of)) if rev is not None else None


def risk_summary_if_ready(conn: sqlite3.Connection, as_of: str) -> Optional[dict]:
    """Public name of `_risk_summary_if_ready` for other screens (the Book tab): the header's
    last risk summary for this database revision and as-of, or None; never computes. Delegates
    at call time, so a patch of the private name is followed."""
    return _risk_summary_if_ready(conn, as_of)


def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _pct_text(pct: float) -> str:
    return f"{pct:.1f}%" if abs(pct) < 100 else f"{pct:,.0f}%"


def _weight_text(w) -> str:
    from fractions import Fraction
    f = _num(w)
    if f is None:
        return "?"
    frac = Fraction(f).limit_denominator(12)
    return f"{frac.numerator}/{frac.denominator}" if abs(float(frac) - f) < 1e-6 else f"{f:.2f}"


def _target_words(config: dict) -> str:
    """'$4,500,000, a placeholder: the macro fund's 4.5m, until ...' while config/risk.yaml
    marks it a placeholder."""
    target = _num(config.get("vol_target_usd"))
    words = _fmt_usd(target) if target is not None else "not set"
    if config.get("vol_target_placeholder"):
        note = str(config.get("vol_target_note") or "").strip()
        words += (f", a {note}" if note.lower().startswith("placeholder") else
                  ", a placeholder while config/risk.yaml holds the macro fund's figure" + (f" ({note})" if note else ""))
    return words


def _grouped_reasons(items: list) -> list:
    """'EUR, JPY, CNH: <reason>' lines, one per distinct reason, in first-seen order."""
    by_reason: dict = {}
    for name, why in items:
        by_reason.setdefault(why, []).append(name)
    return [f"- {', '.join(names)}: {why}" for why, names in by_reason.items()]


def _left_out_sentence(summary: dict) -> tuple:
    """(count, sentence) of the positions the book's series leaves out: the underlyers with no
    history series and the contracts left out of a commodity's series."""
    left_out, contracts_out = summary.get("left_out") or [], summary.get("contracts_out") or []
    n = len(left_out) + len(contracts_out)
    if not n:
        return 0, ""
    what = " and ".join(p for p in (_plural(len(left_out), "underlyer") if left_out else "",
                                    _plural(len(contracts_out), "contract") if contracts_out else "") if p)
    lines = [f"The VaR and the vol sum the positions that have a history series: they exclude {what} "
             "with none."]
    grouped = _grouped_reasons(left_out + contracts_out)
    lines += grouped[:_LEFT_OUT_ON_HOVER]
    if len(grouped) > _LEFT_OUT_ON_HOVER:
        lines.append(f"- and {len(grouped) - _LEFT_OUT_ON_HOVER} more")
    return n, "\n".join(lines)


def _risk_definitions(summary: dict) -> list:
    """The chip's hover: each figure with its definition, then the target (and that it is a
    placeholder). Text only: every number is the engine's."""
    book, config = summary.get("book") or {}, summary.get("config") or {}
    conf = (_num(config.get("var_confidence")) or 0.95) * 100
    window = config.get("var_window_bd") or 252
    b = config.get("blended") or {}
    var, vol, pct = _num(book.get("var95_1d_usd")), _num(book.get("vol_blended_ann_usd")), _num(book.get("vol_vs_target_pct"))
    var_words = _fmt_usd(var) if var is not None else "n/a"
    vol_words = _fmt_usd(vol) if vol is not None else f"n/a ({_metric_reason(book, 'vol_blended_ann_usd')})"
    lines = [
        f"1y {conf:g}% VaR (1-day): {var_words}. Minus the {100 - conf:g}th percentile of the book's last "
        f"{window} daily $ P&Ls, today's positions held constant over the history: a typical bad day, "
        "positive = a loss.",
        f"Blended annual vol: {vol_words}" + (f", {_pct_text(pct)} of the vol target" if pct is not None else "")
        + (" (over the target)" if book.get("over_vol_target") else "") + ". "
        f"{_weight_text(b.get('w_trail'))} x trailing {b.get('trail_window_bd', 500)}-day vol + "
        f"{_weight_text(b.get('w_stress'))} x crisis vol ({b.get('stress_start', '2008-01-01')} to "
        f"{b.get('stress_end', '2010-12-31')}), each the daily $ P&L's standard deviation x sqrt(252), "
        f"at the lag-2 date {book.get('lag2_date') or 'n/a'}"
        + (f"; {book['vol_note']}" if book.get("vol_note") else "") + ".",
        f"Vol target: {_target_words(config)}.",
        "The Risk tab shows each figure by underlyer.",
    ]
    return lines


def _metric_reason(book: dict, key: str) -> str:
    reasons = book.get("reasons") or {}
    return str(reasons.get(key) or reasons.get("all") or book.get("reason") or "not available")


def _risk_card(summary: Optional[dict]) -> html.Div:
    """The risk chip, after the Data chip: "VaR $33.6k" with "6.7% of target" beside it (red when
    the blended vol is over the target) and "excl. N" when the book's series leaves positions
    out; the definitions and the target on hover. "VaR n/a" with its reason on hover when
    risk-metrics has no VaR (never a zero); "VaR …" while it is being worked out."""
    if summary is None:
        return _header_card(RISK_TITLE, "VaR …", "header-figure-value", card_id=VAR_CHIP_ID,
                            value_style=_chip_style(_MUTED_CHIP),
                            hover="The book's VaR and vol against the target are being worked out from the risk "
                                  "history; they show here in a few seconds.")
    if summary.get("error"):
        return _header_card(RISK_TITLE, "VaR n/a", "header-figure-value header-figure-value--muted",
                            card_id=VAR_CHIP_ID, value_style=_chip_style(_MUTED_CHIP), hover=summary["error"])
    book, config = summary.get("book") or {}, summary.get("config") or {}
    var, vol, pct = _num(book.get("var95_1d_usd")), _num(book.get("vol_blended_ann_usd")), _num(book.get("vol_vs_target_pct"))
    definitions = _risk_definitions(summary)
    markers = []
    n_out, out_sentence = _left_out_sentence(summary)
    if var is not None and n_out:
        markers.append((f"excl. {n_out}", out_sentence))
    target = _target_words(config)
    if pct is not None:
        over = bool(book.get("over_vol_target"))
        sentence = (f"blended annual vol {_fmt_usd(vol) if vol is not None else 'n/a'} is {_pct_text(pct)} of the "
                    f"vol target ({target})" + (": over the target" if over else ""))
        markers.append((f"{_pct_text(pct)} of target", sentence, _OVER_TARGET_MARKER if over else None))
    elif var is not None:
        markers.append(("vol n/a", f"blended annual vol n/a: {_metric_reason(book, 'vol_blended_ann_usd')}"))
    if var is None:
        why = _metric_reason(book, "var95_1d_usd")
        return _header_card(RISK_TITLE, "VaR n/a", "header-figure-value header-figure-value--muted",
                            card_id=VAR_CHIP_ID, value_style=_chip_style(_MUTED_CHIP),
                            hover=_joined(f"1y VaR n/a: {why}", *definitions[1:]), markers=markers)
    hover = _joined(*definitions[:3], out_sentence, definitions[3])
    return _header_card(RISK_TITLE, f"VaR {short_money(var, '$')}", "header-figure-value", card_id=VAR_CHIP_ID,
                        value_style=_chip_style(_NEUTRAL_CHIP), hover=hover, markers=markers)


def _commodity_cards(conn: sqlite3.Connection, as_of: str) -> list:
    """The commodity strip: the four cards, or one plain card when the book holds no
    commodity futures. Each engine call that fails costs its own card only, with the reason."""
    try:
        from engine.ladder.positions import book_positions
        block = book_positions(conn, as_of).get("commodities") or {}
    except Exception as exc:  # noqa: BLE001
        block = {"available": False, "reason": _failure_reason("commodity positions could not be computed", exc, conn)}
    try:
        from engine.expiry import expiry_schedule
        schedule, schedule_error = expiry_schedule(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        schedule, schedule_error = {}, _failure_reason("roll calendar could not be computed", exc, conn)

    if block.get("available") and not block.get("sectors") and not schedule_error and not schedule.get("rows"):
        return [_figure_card(COMMODITY_EMPTY_TITLE, "none",
                             block.get("reason") or schedule.get("note") or f"no open commodity futures on {as_of}")]

    expiry_card = (_pnl_card(NEXT_EXPIRY_TITLE, {"available": False, "reason": schedule_error}, colour=False)
                   if schedule_error else _next_expiry_card(schedule))
    return [_gross_notional_card(block), _net_by_sector_card(block),
            _open_spreads_card(_spread_summary(conn, as_of)), expiry_card]


def _priced_day(df) -> tuple:
    """(sum over priced rows, n unpriced, n total) for one day's value_book frame -- the
    chart's per-day analogue of `_priced_single` (2026-09-18): a day with a few unpriced
    trades is plotted at the sum of the rest, with the hover text saying how many are
    excluded, instead of becoming a gap in the line; only a day with nothing priced at
    all is left out (value None, which plotly draws as a gap). An empty book is 0."""
    if df.empty:
        return 0.0, 0, 0
    priced = df[df["reason"] == ""]
    n_unpriced = int(len(df) - len(priced))
    if priced.empty:
        return None, n_unpriced, int(len(df))
    return float(priced["pnl_usd"].sum()), n_unpriced, int(len(df))


def _filled_count(df) -> int:
    """Trades on this day's frame valued at an earlier close (the fill, 2026-09-21)."""
    from engine.pnl.reference import filled_days
    return sum(n for _day, n in filled_days(df))


@lru_cache(maxsize=1024)
def _cached_ltd(db_path: str, _mtime: float, as_of: str) -> tuple:
    """`_priced_day` of that date's book, memoised on (db path, db mtime, as_of): a
    header-chart render used to re-run one full `value_book` evaluation per charted day
    (~4s for 20 days) on every as-of change; the chart now spans the whole book, so a
    day's value is worked out once per database revision (maxsize 1024 covers years).
    `_mtime` is part of the key purely to invalidate the cache when the file changes
    (a new upload / Bloomberg write) -- callers pass `os.path.getmtime(db_path)`, never
    a value this function computes itself, so a stale cache never outlives the file it
    was read from. Opens and closes its own read-only connection (the cache key is a
    path, not a connection object, which is unhashable and reopened per request)."""
    from ui.tabs.blotter_pricing import priced_value_book
    from ui.app import connect_readonly
    conn = connect_readonly(db_path)
    try:
        df, _, _ = priced_value_book(conn, as_of)  # same pricing path as the figures
        return (*_priced_day(df), _filled_count(df))
    finally:
        conn.close()


def _chart_days(conn: sqlite3.Connection, as_of: str) -> list:
    """Every business day from the book's first trade date (MIN(trade_date) in `trades`)
    to `as_of`, inclusive, oldest first, on engine.pnl.calendar's own calendar (user
    decision 2026-09-22: the whole history, not a 20-day lookback). [] for a book with
    no trades, or an `as_of` before its first trade: there is nothing to chart before the
    book existed, and each day costs a `priced_value_book` evaluation."""
    from engine.pnl.calendar import _is_business_day, load_holidays

    row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
    if not row or not row[0]:
        return []
    holidays = load_holidays()
    end = dt.date.fromisoformat(as_of)
    d = dt.date.fromisoformat(str(row[0])[:10])
    days = []
    while d <= end:
        if _is_business_day(d, holidays):
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def _build_chart(conn: sqlite3.Connection, as_of: str, db_path=None):
    """The LTD line over `_chart_days(conn, as_of)`: one point per business day from the
    first trade to `as_of`, each the sum of that day's priced trades (`_priced_day`), a
    gap where nothing priced, the excluded count and the fill in the hover text. A book
    with no day to chart gets a sentence saying so, never a blank graph.

    `db_path` (optional) enables the `_cached_ltd` memoisation; omitted (e.g. direct
    unit tests against an in-memory/temp connection with no path handy) falls back to
    one `priced_value_book` call per day -- correctness is identical either way, only
    the cost of repeated renders differs."""
    days = _chart_days(conn, as_of)
    if not days:
        return html.P(f"No trades dated on or before {as_of}: nothing to chart.",
                      className="header-figure-caption")
    xs, ys, texts = [], [], []
    mtime = os.path.getmtime(db_path) if db_path is not None else None
    for d in days:
        xs.append(d.isoformat())
        if db_path is not None:
            value, n_unpriced, n_total, n_filled = _cached_ltd(str(db_path), mtime, d.isoformat())
        else:
            from ui.tabs.blotter_pricing import priced_value_book as _pvb
            _df, _, _ = _pvb(conn, d.isoformat())
            (value, n_unpriced, n_total), n_filled = _priced_day(_df), _filled_count(_df)
        ys.append(value)
        filled = f"{n_filled} valued at an earlier close" if n_filled else ""   # the fill, 2026-09-21
        if value is None:
            texts.append(f"nothing priced ({n_total} trades)")
        elif n_unpriced:
            texts.append(f"excludes {n_unpriced} of {n_total} trades unpriced" + (f"; {filled}" if filled else ""))
        else:
            texts.append(filled)
    figure = {
        "data": [{"x": xs, "y": ys, "type": "scatter", "mode": "lines+markers", "name": "LTD",
                  "text": texts, "hovertemplate": "%{x}<br>LTD %{y:$,.0f}<br>%{text}<extra></extra>"}],
        "layout": {"margin": {"l": 50, "r": 20, "t": 10, "b": 30}, "height": 260,
                   # months of daily points: ticks read as dates ("22 Jul"), not one per day
                   "xaxis": {"type": "date", "tickformat": "%d %b"},
                   "yaxis": {"title": "USD"}},
    }
    return dcc.Graph(id="header-ltd-graph", figure=figure)


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Registers two server callbacks keyed on a shared `AS_OF_STORE_ID` dcc.Store, plus
    the clientside mirror of the chart collapsible's open state (below); the module
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
    is instant. Since the chart spans the whole book (2026-09-22), the first open after
    a database change is the one render that evaluates every business day since the
    first trade.

    Why the clientside mirror exists (2026-09-22, user: "the LTD line chart not
    working"): the chart callback is gated on the Details' `open` prop, but Dash's html
    bundle (dash 4.4.1, dash/html/dash_html_components.min.js) wires only `n_clicks` on
    its elements and never reports a native <details> toggle back to `open`. A click on
    the summary opened the element in the browser while `open` stayed False on the
    server, so the chart callback never fired and the container stayed empty: the chart
    had been unreachable by clicking since the 2026-09-15 split. The clientside callback
    reads the element's own DOM state after each click of the summary and writes it to
    `open`; the server callback then runs exactly as designed (only while open, and again
    on an as-of or data-revision change while open). Do not remove it unless Dash itself
    starts syncing `open`."""

    from ui import revision
    from ui.tabs.blotter_pricing import pricing_snapshot

    @app.callback(
        Output(f"{HEADER_ID}-figures", "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(revision.DATA_REVISION_ID, "data"),
    )
    def _update_figures(as_of: Optional[str], _data_rev=None):
        # `_data_rev` (ui/revision.py, 2026-09-18): re-run when an upload or a Bloomberg
        # write changes the database, so the headline never needs a browser reload.
        if not as_of:
            return [_figure_card("LTD", "No as-of date available.")]

        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return [_figure_card("LTD", f"Database not available ({exc}).")]
        try:
            with pricing_snapshot(conn, "Header figures"):  # one view of the marks for all the cards
                return _build_figures(conn, as_of)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, see below
            # Anything raised in here used to escape as an HTTP 500: the Output never
            # fired and the header sat on its "LTD -" placeholder for good, with nothing
            # on the page saying why (user, 2026-09-18: "the headlines ... didn't even
            # show"). Say what failed instead; the next data revision retries it.
            import logging
            logging.getLogger(__name__).exception("header figures failed for as_of=%s", as_of)
            return [_pnl_card("LTD", {"available": False,
                                      "reason": _failure_reason("headline could not be computed", exc, conn)})]
        finally:
            conn.close()

    # The risk chip (screens redesign Phase B): chained on the figures' output, so it runs after
    # them (every as-of or data-revision change reaches it through them) and the header's first
    # paint never waits on `book_risk`. It fills the chip's own children in place; the figures
    # render the memoised result directly once there is one, so a warm re-render never flashes.
    @app.callback(
        Output(VAR_CHIP_ID, "children"),
        Input(f"{HEADER_ID}-figures", "children"),
        State(AS_OF_STORE_ID, "data"),
        prevent_initial_call=True,
    )
    def _update_risk_chip(_figures, as_of: Optional[str]):
        if not as_of:
            from dash import no_update
            return no_update
        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return _risk_card({"error": f"Database not available ({exc})."}).children
        try:
            return _risk_card(risk_summary(conn, as_of)).children
        except Exception as exc:  # noqa: BLE001 -- as in _update_figures: say why, never a 500
            logging.getLogger(__name__).exception("header risk chip failed for as_of=%s", as_of)
            return _risk_card({"error": _failure_reason("the book's risk could not be shown", exc, conn)}).children
        finally:
            conn.close()

    # The mirror (see the docstring): Dash does not sync a native <details> toggle to
    # `open`, so the browser reports the element's real state after every click.
    app.clientside_callback(
        _SUMMARY_OPEN_MIRROR_JS,
        Output(DETAILS_ID, "open"),
        Input(SUMMARY_ID, "n_clicks"),
    )

    @app.callback(
        Output(CHART_CONTAINER_ID, "children"),
        Input(DETAILS_ID, "open"),
        Input(AS_OF_STORE_ID, "data"),
        Input(revision.DATA_REVISION_ID, "data"),
    )
    def _update_chart(is_open: bool, as_of: Optional[str], _data_rev=None):
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
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, as in _update_figures
            # Anything raised here escaped as an HTTP 500: the Output never fired and the
            # opened collapsible stayed empty with nothing on the page saying why
            # (2026-09-22). Say what failed instead; the next as-of or data revision
            # retries it.
            import logging
            logging.getLogger(__name__).exception("header LTD chart failed for as_of=%s", as_of)
            return html.P(_failure_reason("LTD chart could not be built", exc, conn),
                          className="header-figure-caption header-figure-caption--reason")
        finally:
            conn.close()
