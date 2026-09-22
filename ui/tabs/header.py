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
  - `_root_reason`/`_missing_marks_reason` turn a bare "unavailable" into a concrete,
    actionable sentence -- which mark_type is missing, how many, and "run the Bloomberg
    pull" -- built from `data.bloomberg.inventory.mark_inventory` (a cheap, DB-only
    read: no Bloomberg connection is opened here). When nothing is missing (the gap is
    something else, e.g. an unrealisable settled trade), a plain fallback sentence is
    used instead (see "Partial pricing" below for the superseding aggregation logic;
    the older `scoped_period_pnl`-based "today's LTD unavailable" / "LTD on <date>
    unavailable" generic reasons this bullet originally described no longer exist).
  - Net/Gross USD delta and the "Trades" count need no marks at all (CLAUDE.md: Net/
    Gross USD notional is computable from trade legs and spot alone), so they are
    always shown, even on a database with zero official marks -- this is the
    "figure that IS computable" half of the same fix.
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
    the explanation was just invisible until the mouse found it).

    An AVAILABLE entry may also carry `excluded_summary` (2026-09-17 partial-pricing
    follow-up, see module docstring): a short, always-visible caption ("excludes N of M
    trades unpriced") shown the same way as the unavailable reason, with the detailed
    per-product/reason breakdown (`excluded_detail`) as its `title` tooltip -- the
    figure itself is a real sum over priced trades, not a placeholder, so it keeps its
    normal sign colouring; only the caption differs from a fully-priced card."""
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
    children = [
        html.Div(title, className="header-figure-title"),
        html.Div(_fmt_usd(value), className=f"header-figure-value header-figure-value--{cls}"),
    ]
    ref_note = entry.get("ref_note", "")
    if ref_note:
        # The period is measured from an earlier close than its own reference date
        # (engine.pnl.reference): always visible, the skipped dates' reasons on hover.
        children.append(html.Div(ref_note, className="header-figure-caption header-figure-caption--partial",
                                  title=entry.get("ref_note_detail", "")))
    summary = entry.get("excluded_summary", "")
    if summary:
        children.append(html.Div(summary, className="header-figure-caption header-figure-caption--partial",
                                  title=entry.get("excluded_detail", "")))
    return html.Div(className="header-figure", children=children)


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


def _missing_marks_reason(conn: sqlite3.Connection, as_of: str,
                          action: str = "run the Bloomberg pull") -> str:
    """Plain-English, actionable reason for `as_of` built from `data.bloomberg.
    inventory.mark_inventory` (a DB-only read -- it never opens a Bloomberg session,
    so it is safe to call from a UI callback per CLAUDE.md/the perf rule against
    synchronous Bloomberg probes on the request path): which mark_type(s) the book
    needs but does not have an official mark for, and how many. Returns "" when the
    book needs no marks at all, or needs marks and already has every one of them --
    in either case the caller should keep the engine's own (more specific) reason
    instead, e.g. "settled trade X: no official mark on or before its settlement".

    2026-09-21: the count comes from `needed_marks`, which asks a PAST date what a past
    close needed, not what a live pull would request."""
    try:
        needed, missing = needed_marks(conn, as_of)
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
    "FUTURE": "future", "IRS": "swap (IRS)", "FX_OPTION": "option",
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
    from ui.tabs.blotter_pricing import BAD_VALUE_TAG, is_bad_value_reason
    if is_bad_value_reason(reason):
        # 2026-09-18: a trade `value_book` left unpriced because a STORED figure is not a
        # number ("trade <id>: trades.price is not a number ('24-Jul')"), not because a
        # mark is missing. `_unpriced_breakdown` repeats those reasons in full.
        return BAD_VALUE_TAG
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


def _root_reason(conn: sqlite3.Connection, as_of: str) -> str:
    """`_missing_marks_reason` when that can explain the gap, else a plain fallback --
    used as the Unavailable-card reason when nothing at all is priced/contributing for
    a given date."""
    return _missing_marks_reason(conn, as_of) or f"every trade on {as_of} is missing a mark from any source"


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
    from ui.tabs.cash_ladder import net_gross_usd

    df_today, _n_filled, n_total = priced_value_book(conn, as_of)
    ltd_entry = _priced_single(df_today, _root_reason(conn, as_of))
    # The fill (user decision 2026-09-21): trades with no price on `as_of` are valued at their
    # last earlier close. Said on the card, each trade's own note on hover -- never silent.
    from engine.pnl.reference import fill_caption, filled_from
    ltd_entry["ref_note"] = fill_caption(df_today, as_of)
    if ltd_entry["ref_note"]:
        notes = [f"{r.trade_id}: {r.note}" for r in df_today.itertuples() if filled_from(r.note)]
        ltd_entry["ref_note_detail"] = "\n".join(notes[:40] + ([f"and {len(notes) - 40} more"] if len(notes) > 40 else []))
    cards = [_pnl_card("LTD", ltd_entry)]

    holidays = load_holidays()
    d = dt.date.fromisoformat(as_of)
    t1 = _prev_business_day(d, holidays)
    t2 = _prev_business_day(t1, holidays)
    t1_iso, t2_iso = t1.isoformat(), t2.isoformat()
    df_t1, _, _ = priced_value_book(conn, t1_iso)
    df_t2, _, _ = priced_value_book(conn, t2_iso)

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
    # measured from moves and the card says so; no mark is copied for any trade.
    from engine.pnl.reference import annotate, resolve_reference

    def _book(iso: str):
        return priced_value_book(conn, iso)[0]  # per-date cached

    def _period(df_a, a_iso: str, ref_iso: str, key: str) -> dict:
        title = _PERIOD_TITLES[key]
        choice = resolve_reference(df_a, ref_iso, _book, holidays, frames_filled=True)  # `_book` frames carry the fill
        entry = _priced_diff(df_a, choice.frame, _root_reason(conn, a_iso), choice.ref_date_used,
                             _reference_reason(conn, ref_iso, title, backfill))
        return annotate(entry, choice,
                        lambda s: _reference_reason(conn, s.date, title, backfill)(s.n_blocked, s.n_open_then))

    for key in ("daily", "d5", "mtd", "ytd"):
        entries[key] = _period(df_today, as_of, ref_dates[key], key)
    entries["previous_day"] = _period(df_t1, t1_iso, t2_iso, "previous_day")

    trading_rows = df_today[df_today["trade_date"] == as_of] if not df_today.empty else df_today
    entries["trading"] = _priced_single(
        trading_rows, "a trade dated today has no mark from any source")

    for key in _PERIODS:
        cards.append(_pnl_card(_PERIOD_TITLES[key], entries[key]))

    # Always computable, marks or no marks (CLAUDE.md: trade counts/positions need only
    # the blotter, not a mark) -- so the header still shows *something* concrete on a
    # database with zero official marks, per the 2026-09-17 investigation into "the
    # headline doesn't work" (root cause was missing marks alone, not a callback bug;
    # see module docstring).
    n_open = int((df_today["status"] == "OPEN").sum()) if not df_today.empty else 0
    n_settled = n_total - n_open
    cards.append(_figure_card("Trades", f"{n_total:,}", f"{n_open:,} open, {n_settled:,} settled"))

    cards.append(_divider())
    try:
        ng = net_gross_usd(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the delta cards must never take the P&L cards with them
        # 2026-09-18: `net_gross_usd` reads `trade_legs.amount` / spot through the ladder,
        # not through `value_book`, so a stored value that is not a number raised here and
        # the whole header -- every P&L card already built above -- was replaced by one
        # "headline could not be computed" card. Now only these two cards say why.
        import logging
        logging.getLogger(__name__).exception("header Net/Gross USD delta failed for as_of=%s", as_of)
        ng = {"available": False, "reason": _failure_reason("USD delta could not be computed", exc, conn)}
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
    header-chart render used to re-run 20 full `value_book` evaluations (~4s) on every
    as-of change.
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
        finally:
            conn.close()
