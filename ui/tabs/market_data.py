"""Market data tab: "Can I trust the numbers?" (BUILD_PLAN.md section 5).

User decision 2026-09-15: this tab is organised BY CURRENCY PAIR, not by a flat
inventory table. Layout:

  1. Top bar: as-of date picker, a pair dropdown (every FX instrument with trades or
     marks, default = the pair with the most open trades) -- the only dropdown on the
     tab -- the "Pull now" button and a one-line feed status, with a second compact line
     giving the last pull's seconds per step, slowest first, when the status file
     carries `timings` (`pull_timings_line`; nothing when absent).
  1b. Whole-book checks (2026-09-21, user-approved), above the per-pair section and
     independent of the pair dropdown: "What is missing" (`missing_panel`: every needed
     mark with no official mark, trades and notional blocked, Bloomberg's own reason from
     the last pull) and "Marks that look wrong" (`suspect_panel`: today's official marks
     against the previous business day's, flagged above BAD_TICK_PCT, when exactly
     unchanged, or when the snap is old on the live date). "Past closes the header needs"
     (`past_closes_panel`) sits under the completeness strip and follows the HEADER's
     as-of date. All three are filled by `_update_body` through `whole_book_panels`.
  2. For the selected pair: spot (value/source/snapped time), the forward curve as a
     table (one row per settle_date with a FWD_OUTRIGHT mark on the as-of date, all
     sources, official first), and a line chart of outright vs settle date.
  3. Bottom, compact: the close-completeness strip (`data.bloomberg.inventory
     .close_completeness`) as a row of small squares for the trailing 20 business
     days, and the manual mark-entry form pre-filled with the selected pair.

Removed from the previous version of this tab: the whole-book inventory table and the
old detailed Bloomberg diagnostics panel (still reachable via `2_launcher.py doctor`). The
"Check Bloomberg connection" button (click-only pass/fail/warning check) moved here
from `ui/tabs/header.py` on 2026-09-16 (user decision: it belongs on this tab only,
not shown above every tab) -- see `BBG_CHECK_BUTTON_ID` / `BBG_RESULTS_ID` below.
`feed_headline`
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

import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.feed_controls import pull_timings, safety_refresh_ms, seconds_words
from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID
from ui.tabs.controls import build_date_picker

BBG_CHECK_BUTTON_ID = "market-data-bbg-check-button"
BBG_RESULTS_ID = "market-data-bbg-check-results"

DATE_PICKER_ID = "market-data-date"
PAIR_DROPDOWN_ID = "market-data-pair"
TOOLBAR_ID = "market-data-toolbar"
STATUS_ID = "market-data-status"
BODY_ID = "market-data-body"
REFRESH_ID = "market-data-refresh"
PULL_NOW_ID = "market-data-pull-now"
PULL_NOW_STATUS_ID = "market-data-pull-now-status"
PULL_REVISION_ID = "market-data-pull-revision"
PULL_TIMINGS_ID = "market-data-pull-timings"
# Safety-net timer only: one feed cycle, read from data.bloomberg.live.INTERVAL_SECONDS
# (ui.feed_controls.safety_refresh_ms), never a number typed in here. A data change
# redraws the tab within seconds through ui/revision.py's DATA_REVISION_ID.
REFRESH_MS = safety_refresh_ms()

CURVE_TABLE_ID = "market-data-curve-table"
CURVE_CHART_ID = "market-data-curve-chart"
COMPLETENESS_STRIP_ID = "market-data-completeness-strip"
COMPLETENESS_DAYS = 20  # trailing business days shown in the calendar strip; not specified in BUILD_PLAN.md

# Whole-book checks (2026-09-21), none of which depends on the pair dropdown.
MISSING_PANEL_ID = "market-data-missing-panel"
MISSING_TABLE_ID = "market-data-missing-table"
SUSPECT_PANEL_ID = "market-data-suspect-panel"
SUSPECT_FLAGGED_TABLE_ID = "market-data-suspect-flagged-table"
SUSPECT_ALL_TABLE_ID = "market-data-suspect-all-table"
PAST_CLOSES_PANEL_ID = "market-data-past-closes-panel"
PAST_CLOSES_TABLE_ID = "market-data-past-closes-table"
MISSING_TITLE = "What is missing"
SUSPECT_TITLE = "Marks that look wrong"
PAST_CLOSES_TITLE = "Past closes the header needs"
# "Marks that look wrong": one rule for every instrument, stated in the panel's caption.
# A day's move above this is flagged. 2.5 % is loose for a G10 pair and tight for TRY, and
# is meant to be: a flag asks for a look, it does not say the mark is wrong.
BAD_TICK_PCT = 2.5
PANEL_PAGE_SIZE = 15   # rows per page in the two exception tables, so a bad day stays a bounded height

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


# Moved to ui/feed_controls.py 2026-09-18 so the top bar's "Pull Bloomberg now" status line
# and this tab print one sentence, with the cadence read from the feed's own interval
# instead of typed in here. Re-exported under the old name for ui/tabs/cash_ladder.py.
from ui.feed_controls import feed_headline  # noqa: E402,F401


def top_bar_status(status: Optional[dict]) -> str:
    """The single top-bar status line: feed headline plus backfill progress when
    there is any to report."""
    line = feed_headline(status)
    bf_line = backfill_headline(status)
    if bf_line:
        line = f"{line} | {bf_line}"
    return line


def pull_timings_line(status: Optional[dict]) -> str:
    """'Last pull took 48 s: forwards 21 s · options 12 s · rates 8.0 s · ...', the steps
    of the last pull slowest first, from `status["timings"]` (seconds per step, written by
    data.bloomberg.live's pull; "total" is the whole pull and heads the line). "" when the
    status file carries no timings (an older file, or no pull yet), so nothing is shown."""
    timings = pull_timings(status)
    total = seconds_words(timings.pop("total", None))
    steps = sorted(timings.items(), key=lambda kv: (-kv[1], kv[0]))
    parts = " · ".join(f"{step} {seconds_words(value)}" for step, value in steps)
    if total and parts:
        return f"Last pull took {total}: {parts}"
    if total:
        return f"Last pull took {total}"
    return f"Last pull by step: {parts}" if parts else ""


def status_block(status: Optional[dict]):
    """What the tab's feed-status block shows: the one-line status, plus the last pull's
    per-step timings on a second compact line when the status file has them."""
    line = top_bar_status(status)
    timings = pull_timings_line(status)
    if not timings:
        return line
    return [line, html.Div(timings, id=PULL_TIMINGS_ID, className="status-line")]


def _rates_step_lines(rates_status: Optional[dict]) -> List[str]:
    """Plain-English line per currency from data.bloomberg.live._rates_step's output
    (part of the pull_once status dict under "rates"), so a currency that priced nothing
    this cycle says *why* -- a broken RatesBloombergSource, a curve-quote request that
    came back empty, a pricing exception -- rather than the diagnostics only being able to
    say curve_quotes/marks are missing without explaining the live feed's own attempt."""
    if not rates_status:
        return []
    if rates_status.get("skipped"):
        return [f"Rates: {rates_status['skipped']}."]
    lines: List[str] = []
    if rates_status.get("error"):
        lines.append(f"Rates: {rates_status['error']}")
    for ccy, entry in sorted(rates_status.get("currencies", {}).items()):
        if entry.get("error"):
            lines.append(f"Rates {ccy}: FAILED to pull curve/fixings -- {entry['error']}")
        else:
            lines.append(f"Rates {ccy}: {entry.get('quotes', 0)} curve quote(s), "
                          f"{entry.get('fixings', 0)} fixing(s) written this cycle.")
    for f in rates_status.get("failed", []):
        lines.append(f"Rates pricing {f.get('trade_id', '')}: FAILED -- {f.get('error', '')}")
    return lines


def _options_step_lines(options_status: Optional[dict]) -> List[str]:
    """Same idea for the options step (data.bloomberg.live._options_step / status["options"]):
    engine/options never raises, it reports a per-trade skip reason instead, so a trade
    priced to nothing must show that reason here rather than just silently having no
    PREMIUM/DELTA mark."""
    if not options_status:
        return []
    if options_status.get("error"):
        return [f"Options: {options_status['error']}"]
    skipped = options_status.get("skipped")
    if isinstance(skipped, str):  # "no FX_OPTION trades to price" -- nothing was skipped, nothing to do
        return [f"Options: {skipped}."]
    lines = [f"Options: {options_status.get('priced', 0)} option(s) priced this cycle."]
    for s in skipped or []:
        lines.append(f"Options {s.get('trade_id', '')}: not priced -- {s.get('reason') or 'no reason given'}.")
    return lines


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
            # sort_action/filter_action="native" removed 2026-09-16: dash_table's native
            # header filter/sort row does not work in the installed Dash version (see
            # ui/tabs/blotter.py's module docstring for the verified repro) -- it was
            # rendering here as dead, non-functional controls. This is a small debug
            # panel (collapsed by default) rather than a primary blotter view, so it
            # gets a plain sortable-by-click column header via style only, no fake
            # interactive affordance.
            data=rows, page_size=40,
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
    pricing_lines = _rates_step_lines((status or {}).get("rates")) + _options_step_lines((status or {}).get("options"))
    if pricing_lines:
        children += [html.H4("Rates and options pricing (this pull's cycle)"),
                     html.Ul([html.Li(ln, className="status-line") for ln in pricing_lines])]
    if status and status.get("warnings"):
        children += [html.H4("Pull warnings"), html.Ul([html.Li(w, className="status-line") for w in status["warnings"]])]
    return html.Details(id="market-data-diag", className="details details--diag",
                        open=open_by_default or bool(failed), children=children)


# ---------------------------------------------------------------------------
# Bloomberg diagnostics button + panel
# ---------------------------------------------------------------------------
#
# Moved here from ui/tabs/header.py on 2026-09-16 (user decision: the "Check Bloomberg
# connection" button belongs on the Market Data tab only, not shown above every tab).
#
# EXPECTED INTERFACE (for the housekeeper to wire up once bbg-diagnostics lands):
#
#     data.bloomberg.bbg_diagnostics.run_bloomberg_diagnostics() -> list[dict]
#
# where each dict is:
#     {"name": str, "status": "pass" | "fail" | "warning", "message": str}
#
# "name" is a short check label (e.g. "Session connectivity", "SPOT marks official
# source"), "message" is a one-sentence plain-English explanation (no raw exceptions
# or tracebacks -- those must be caught and summarised by the diagnostics module
# itself). This module never surfaces a traceback to the page; see
# `run_bloomberg_diagnostics_safe` below for the failure path.
#
# Until that module exists, `_run_bloomberg_diagnostics_placeholder` below is used
# instead: a minimal, best-effort check built only from what data/bloomberg already
# exposes today (`live.availability` for a live session probe, `live.read_status` for
# the last completed pull). It intentionally does NOT attempt to classify
# marks_official correctness -- that enumeration is bbg-diagnostics' job.


def _bbg_diagnostics_entry_point():
    """Returns the best diagnostics function available: the real bbg-diagnostics
    output if that module has landed, otherwise the local placeholder. Import is
    lazy and wrapped so a partially-built module on someone else's branch can never
    break this tab."""
    try:
        from data.bloomberg.bbg_diagnostics import run_bloomberg_diagnostics as real_fn
        return real_fn
    except ImportError:
        return _run_bloomberg_diagnostics_placeholder


def _run_bloomberg_diagnostics_placeholder() -> list:
    """Placeholder standing in for the future `data.bloomberg.bbg_diagnostics.
    run_bloomberg_diagnostics()`. Reports what today's data/bloomberg module can
    already tell us: whether a live session can be opened, and what the last
    completed pull (`data.bloomberg.live` status file) recorded. Never raises --
    any failure to even check becomes a single "fail" row with a plain-English
    message."""
    checks = []
    try:
        from data.bloomberg.live import availability
        host = os.environ.get("BLP_HOST", "localhost")
        port = int(os.environ.get("BLP_PORT", "8194"))
        ok, reason = availability(host=host, port=port)
        checks.append({
            "name": "Bloomberg session connectivity",
            "status": "pass" if ok else "fail",
            "message": ("Connected to the Bloomberg API session." if ok
                        else f"Could not reach Bloomberg: {reason}."),
        })
    except Exception:
        checks.append({
            "name": "Bloomberg session connectivity",
            "status": "fail",
            "message": "Could not reach Bloomberg (connection check itself failed to run).",
        })

    try:
        from data.bloomberg.live import read_status, book_today
        from ui.app import get_db_path
        db_path = get_db_path()
        status = read_status(db_path)
        if status is None:
            checks.append({
                "name": "Last marks pull",
                "status": "warning",
                "message": "No Bloomberg pull has run yet on this database.",
            })
        elif status.get("connected"):
            # A pull that reported connected=True with requested=0 is only trustworthy at
            # the instant it ran: build_requests() found nothing to price then, but trades
            # uploaded since (before the next scheduled pull) can make marks needed right
            # now that this stale status never asked for. Found on the Bloomberg PC
            # 2026-09-17 -- see data.bloomberg.inventory.stale_empty_pull_reason.
            stale_reason = None
            try:
                from data.bloomberg.inventory import stale_empty_pull_reason
                as_of = status.get("as_of_date") or book_today().isoformat()
                conn = _connect_readonly(db_path)
                try:
                    stale_reason = stale_empty_pull_reason(conn, status, as_of)
                finally:
                    conn.close()
            except Exception:
                pass  # cannot verify freshness right now; fall back to the plain PASS below
            if stale_reason:
                checks.append({
                    "name": "Last marks pull",
                    "status": "fail",
                    "message": f"Last pull at {status.get('time', 'an unknown time')} looks stale: {stale_reason}",
                })
            else:
                checks.append({
                    "name": "Last marks pull",
                    "status": "pass",
                    "message": f"Last pull at {status.get('time', 'an unknown time')} wrote "
                               f"{status.get('written', 0)} of {status.get('requested', 0)} requested marks.",
                })
        else:
            checks.append({
                "name": "Last marks pull",
                "status": "fail" if status.get("failed") else "warning",
                "message": f"Last pull did not connect: {status.get('reason', 'unknown reason')}.",
            })
    except Exception:
        checks.append({
            "name": "Last marks pull",
            "status": "warning",
            "message": "Could not read the last pull status.",
        })

    checks.append({
        "name": "marks_official coverage",
        "status": "warning",
        "message": "Detailed checks of official vs reconciliation-only marks sources "
                   "(BNP_BVAL, MANUAL for FX marks) are not available yet; this placeholder does "
                   "not enumerate them. Interpolated broken-date forwards (BBG_INTERP) are "
                   "official for FWD_OUTRIGHT since 2026-09-18.",
    })
    return checks


_STATUS_LABELS = {"pass": "PASS", "fail": "FAIL", "warning": "WARNING"}


def _render_bbg_results(checks: list) -> html.Div:
    if not checks:
        return html.Div("No diagnostic checks were returned.", className="bbg-check-empty")
    rows = []
    for c in checks:
        status = c.get("status", "warning")
        label = _STATUS_LABELS.get(status, status.upper())
        rows.append(html.Div(className="bbg-check-row", children=[
            html.Span(label, className=f"bbg-check-status bbg-check-status--{status}"),
            html.Span(c.get("name", ""), className="bbg-check-name"),
            html.Span(c.get("message", ""), className="bbg-check-message"),
        ]))
    return html.Div(rows, className="bbg-check-list")


def run_bloomberg_diagnostics_safe() -> list:
    """Runs whichever diagnostics function is available (real or placeholder) with a
    hard safety net: any exception is caught here and turned into a single plain
    "could not reach Bloomberg" row rather than a crash or a traceback on the page.
    This never blocks app startup or other tabs -- it only runs on button click."""
    try:
        fn = _bbg_diagnostics_entry_point()
        result = fn()
        if not isinstance(result, list):
            raise TypeError("diagnostics function did not return a list")
        return result
    except Exception:
        return [{
            "name": "Bloomberg diagnostics",
            "status": "fail",
            "message": "Could not reach Bloomberg or run diagnostics.",
        }]


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
                                 marker_symbol="diamond", name="manual/BNP (not official)"))
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


# --------------------------------------------------------------------------- whole-book checks (2026-09-21)
# "What is missing", "Marks that look wrong" and "Past closes the header needs". All three
# read stored rows and the Bloomberg status file only: no Bloomberg session, nothing
# written, no P&L or delta computed. Each is a handful of queries for the whole book.
MarkKey = Tuple[str, str, str]   # (instrument_id, mark_type, settle_date)

_OPEN_LEGS_SQL = """
SELECT t.trade_id, t.instrument_id, i.asset_class, i.base_ccy, i.quote_ccy, l.ccy, l.amount, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class IN ('FX', 'FUTURE') AND t.trade_date <= :as_of AND l.settle_date >= :as_of
"""

_OPEN_OPTIONS_SQL = """
SELECT t.trade_id, i.base_ccy, i.quote_ccy, i.expiry_date, t.quantity
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND i.expiry_date >= :as_of
"""

_UNOFFICIAL_ON_FILE_SQL = """
SELECT instrument_id, mark_type, settle_date, value, source FROM marks
WHERE as_of_date = ? AND source IN ('BBG_INTERP', 'MANUAL') ORDER BY snapped_at
"""

# Today's official marks the book uses and the same marks on the previous business day, in
# one query: every official SPOT, and a FWD_OUTRIGHT / FUTURE_PX only at a settle date some
# open leg of that instrument needs (the bulk FWD_CURVE tenor rows are left out).
_SUSPECT_MARKS_SQL = """
WITH used AS (
  SELECT DISTINCT t.instrument_id, l.settle_date
  FROM trade_legs l JOIN trades_official t USING (trade_id)
  WHERE t.trade_date <= :as_of AND l.settle_date >= :as_of
)
SELECT m.as_of_date, m.instrument_id, m.mark_type, m.settle_date, m.value, m.snapped_at
FROM marks_official m
LEFT JOIN used u ON u.instrument_id = m.instrument_id AND u.settle_date = m.settle_date
WHERE m.as_of_date IN (:as_of, :prev) AND m.mark_type IN ('SPOT', 'FWD_OUTRIGHT', 'FUTURE_PX')
  AND (m.mark_type = 'SPOT' OR u.instrument_id IS NOT NULL)
ORDER BY m.instrument_id, m.mark_type, m.settle_date
"""


def _book_today_iso() -> str:
    from data.bloomberg.live import book_today
    return book_today().isoformat()


def _abs_amount(value) -> float:
    """|value| as a float; 0.0 for a stored value that is not a number (never raises)."""
    try:
        return abs(float(value))
    except (TypeError, ValueError):
        return 0.0


def _fmt_mark(value) -> str:
    """Enough digits to see a bad tick or a copied value, no more."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    if abs(v) >= 10:
        return f"{v:.4f}"
    return f"{v:.6f}"


def notional_words(notional: Dict[str, float]) -> str:
    """'USD 12,500,000 + EUR 5,000,000': one amount per currency, USD first. Amounts in
    different currencies are listed side by side, never converted into one another."""
    parts = sorted(((c, a) for c, a in notional.items() if a), key=lambda kv: (kv[0] != "USD", kv[0]))
    return " + ".join(f"{ccy} {amount:,.0f}" for ccy, amount in parts)


def blocked_by_mark(conn: sqlite3.Connection, as_of: str) -> Dict[MarkKey, dict]:
    """Which open trades read which mark on `as_of`:
    (instrument_id, mark_type, settle_date) -> {"trades": set of trade ids, "notional": {ccy: amount}}.

    Two queries for the whole book (open FX / futures legs, open FX options), folded here:
      - an FX leg reads the FWD_OUTRIGHT of its pair at its own settle date, the pair's SPOT,
        and for a cross the SPOT of each currency's USD pair (EURSEK reads EURUSD and USDSEK);
      - a futures leg reads the FUTURE_PX at its expiry;
      - an FX option reads its pair's SPOT, the forward at its expiry and the same USD pairs.
    The pair names come from `data.bloomberg.live.option_spot_pair_names`, the function the
    needs list itself is built from, so a row here matches a row of the inventory.

    Notional is a face amount straight from the ticket: the leg's absolute USD amount where
    the pair has a USD leg, otherwise the base amount under its own currency; an option's
    is its base-currency notional. It is never converted with a mark."""
    from data.bloomberg.live import option_spot_pair_names
    out: Dict[MarkKey, dict] = {}

    def add(key: MarkKey, trade_id: str, ccy: str, amount: float) -> None:
        entry = out.setdefault(key, {"trades": set(), "notional": {}})
        entry["trades"].add(trade_id)
        if ccy and amount:
            entry["notional"][ccy] = entry["notional"].get(ccy, 0.0) + amount

    for trade_id, instrument_id, asset_class, base, quote, ccy, amount, settle in conn.execute(
            _OPEN_LEGS_SQL, {"as_of": as_of}):
        wanted = "USD" if "USD" in (base, quote) else base
        shown_ccy, shown = (ccy, _abs_amount(amount)) if ccy == wanted else ("", 0.0)
        if asset_class == "FUTURE":
            add((instrument_id, "FUTURE_PX", settle), trade_id, shown_ccy, shown)
            continue
        add((instrument_id, "FWD_OUTRIGHT", settle), trade_id, shown_ccy, shown)
        add((instrument_id, "SPOT", as_of), trade_id, shown_ccy, shown)
        for usd_pair in option_spot_pair_names(base, quote)[1:]:
            add((usd_pair, "SPOT", as_of), trade_id, shown_ccy, shown)
    for trade_id, base, quote, expiry, quantity in conn.execute(_OPEN_OPTIONS_SQL, {"as_of": as_of}):
        names = option_spot_pair_names(base, quote)
        add((names[0], "FWD_OUTRIGHT", expiry), trade_id, base, _abs_amount(quantity))
        for name in names:
            add((name, "SPOT", as_of), trade_id, base, _abs_amount(quantity))
    return out


def _pull_date(status: Optional[dict]) -> Optional[str]:
    """The date the last pull's marks were stamped with; None when the file does not say."""
    if not isinstance(status, dict):
        return None
    return status.get("as_of_marks") or status.get("as_of_date") or None


def last_pull_reasons(status: Optional[dict], as_of: str) -> Dict[MarkKey, str]:
    """Bloomberg's own word on each mark, from the last pull's `status["items"]` detail, and
    only when that pull was for `as_of`: a forward's key carries no as-of date, so a reason
    from another day's pull would be attached to the wrong date. Every key may be absent."""
    if _pull_date(status) != as_of:
        return {}
    items = status.get("items")
    out: Dict[MarkKey, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("instrument_id") or ""), str(item.get("mark_type") or ""),
               str(item.get("settle_date") or ""))
        state = str(item.get("status") or "").upper()
        detail = str(item.get("detail") or "").strip()
        if state == "OK":
            out[key] = "the last pull reports this mark as written, yet it is not on file as official"
        else:
            out[key] = detail or (f"last pull: {state.lower()}" if state else "last pull gave no reason")
    return out


def pull_note(status: Optional[dict], as_of: str, is_past: bool, backfill: Optional[dict]) -> str:
    """One sentence on where the missing marks' reasons come from when the last pull cannot
    give them row by row. "" when the last pull was for `as_of` (its reasons are in the rows)."""
    if is_past:
        from ui.tabs.header import past_close_explanation
        return (f"{as_of} is a past date, and a past close only arrives through the Bloomberg backfill: "
                f"{past_close_explanation(backfill, as_of)}.")
    if not isinstance(status, dict):
        return "No Bloomberg pull is recorded yet, so there is no reason from Bloomberg to show."
    if not status.get("connected"):
        return f"The last Bloomberg pull did not connect ({status.get('reason') or 'no reason recorded'})."
    pulled_for = _pull_date(status)
    if pulled_for != as_of:
        return (f"The last Bloomberg pull was for {pulled_for or 'an unrecorded date'}, not {as_of}, "
                "so its reasons are not shown here.")
    return ""


def missing_rows(conn: sqlite3.Connection, as_of: str, status: Optional[dict] = None) -> Tuple[int, List[dict]]:
    """(marks the book needs on `as_of`, one row per needed mark with no OFFICIAL mark),
    rows sorted by trades blocked, largest first. The needs list is `ui.tabs.header
    .needed_marks`, the one the header's own sentence counts with."""
    from ui.tabs.header import needed_marks
    needed, missing = needed_marks(conn, as_of)
    if not missing:
        return needed, []
    on_file = {(r[0], r[1], r[2]): (r[3], r[4]) for r in conn.execute(_UNOFFICIAL_ON_FILE_SQL, (as_of,))}
    blocked = blocked_by_mark(conn, as_of)
    reasons = last_pull_reasons(status, as_of)
    asked = _pull_date(status) == as_of and bool((status or {}).get("connected"))
    rows = []
    for m in missing:
        key = (m["instrument_id"], m["mark_type"], m["settle_date"])
        entry = blocked.get(key, {"trades": set(), "notional": {}})
        instead = on_file.get(key)
        rows.append({
            "instrument_id": key[0], "mark_type": key[1], "settle_date": key[2],
            "on_file": "nothing" if instead is None else
                       f"{source_label(instead[1]).lower()} {_fmt_mark(instead[0])} (not official)",
            "trades_blocked": len(entry["trades"]),
            "notional_blocked": notional_words(entry["notional"]),
            "reason": reasons.get(key, "not requested by the last pull" if asked else ""),
        })
    rows.sort(key=lambda r: (-r["trades_blocked"], r["instrument_id"], r["mark_type"], r["settle_date"]))
    return needed, rows


def _panel(title: str, children: list, panel_id: Optional[str] = None) -> html.Div:
    kwargs = {"id": panel_id} if panel_id else {}
    return html.Div(className="section", children=[html.H4(title, style={"marginTop": "0"}), *children], **kwargs)


def _kicker(text: str) -> html.P:
    return html.P(text, className="section-kicker")


def _panel_table(table_id: str, columns: List[Tuple[str, str]], rows: List[dict], wide: Tuple[str, ...] = (),
                 numeric: Tuple[str, ...] = (), page_size: int = PANEL_PAGE_SIZE) -> dash_table.DataTable:
    """The tab's DataTable look (`_MONO` / `_HEAD`). `wide` columns hold sentences and wrap;
    a row whose `flag` is not empty is tinted. Colours are plain hex: inside a DataTable
    var(--muted) and var(--accent) are dash-table's own, and border colours are forced grey
    by style.css (see ui/tabs/options.py::table_styles)."""
    return dash_table.DataTable(
        id=table_id, columns=[{"name": name, "id": col} for name, col in columns], data=rows,
        page_size=page_size, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
        style_cell_conditional=(
            [{"if": {"column_id": c}, "whiteSpace": "normal", "height": "auto",
              "minWidth": "240px", "maxWidth": "520px"} for c in wide]
            + [{"if": {"column_id": c}, "textAlign": "right"} for c in numeric]),
        style_data_conditional=[
            {"if": {"filter_query": "{flag} != ''"}, "backgroundColor": "#fff4f2"},
            {"if": {"filter_query": "{flag} != ''", "column_id": "flag"}, "color": "#b42318", "fontWeight": "600"},
        ])


def missing_panel(conn: sqlite3.Connection, as_of: str, status: Optional[dict] = None) -> html.Div:
    """Panel 1: every mark the book needs on `as_of` that has no official mark."""
    from ui.tabs.header import backfill_status
    needed, rows = missing_rows(conn, as_of, status)
    scope = ("Covers the marks requested from Bloomberg (spot, forwards, futures prices); swap and option "
             "values are computed by the app from these.")
    if not needed:
        return _panel(MISSING_TITLE, [html.P(f"The book needs no marks on {as_of}: no FX, futures or option "
                                             f"trade is open that day."), _kicker(scope)])
    if not rows:
        return _panel(MISSING_TITLE, [html.P(f"Every one of the {needed} marks the book needs on {as_of} is "
                                             f"official."), _kicker(scope)])
    is_past = as_of < _book_today_iso()
    note = pull_note(status, as_of, is_past, backfill_status(conn) if is_past else None)
    children = [
        html.P(f"{len(rows)} of the {needed} marks the book needs on {as_of} have no official mark. "
               "A trade that reads one of them shows no P&L until it arrives."),
        _kicker("Trades blocked = open trades that read the mark. Notional blocked = the absolute USD leg of "
                "those trades' open legs (the base amount, under its own currency, where a pair has no USD "
                "leg); never converted with a mark. " + scope),
        _panel_table(MISSING_TABLE_ID, [
            ("Pair / instrument", "instrument_id"), ("Mark type", "mark_type"), ("Settle date", "settle_date"),
            ("On file instead", "on_file"), ("Trades blocked", "trades_blocked"),
            ("Notional blocked", "notional_blocked"), ("Bloomberg's reason (last pull)", "reason"),
        ], rows, wide=("reason",), numeric=("trades_blocked", "notional_blocked")),
    ]
    if note:
        children.append(html.P(note, className="status-line"))
    return _panel(MISSING_TITLE, children)


# ---- "Marks that look wrong"
def _snap_age_seconds(snapped_at, now: datetime) -> Optional[float]:
    """Seconds between `snapped_at` (ISO; a stamp with no offset is read as UTC, like
    data.bloomberg.live.rates_from_marks) and `now`. None when it cannot be read."""
    try:
        ts = datetime.fromisoformat(str(snapped_at))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


def _age_words(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"


def suspect_rows(conn: sqlite3.Connection, as_of: str, now: Optional[datetime] = None,
                 today: Optional[str] = None) -> dict:
    """The bad tick and stale check: {"prev_day", "prev_has_marks", "rows"}.

    One row per official SPOT on `as_of` and per official FWD_OUTRIGHT / FUTURE_PX at a settle
    date an open leg needs, against the official mark for the same instrument, mark type and
    settle date on the previous business day (engine/pnl/calendar.py + config/holidays.txt).
    A SPOT is matched by instrument alone: its settle date is its own as-of date. A forward
    whose settle date has no mark on the previous day is NOT compared, and says so: only that
    pair's SPOT row carries a comparison. Nothing is interpolated.

    A row is flagged when the move exceeds BAD_TICK_PCT, when the value is EXACTLY the previous
    close (a stale or copied mark), or, when `as_of` is today's book date, when its snap is
    older than data.bloomberg.live.STALE_AFTER_SECONDS. Flagged rows come first, a value flag
    ahead of a stale-only one, largest move first. A comparison of two stored marks."""
    from engine.pnl.calendar import _prev_business_day, load_holidays
    prev_day = _prev_business_day(date.fromisoformat(as_of), load_holidays()).isoformat()
    today = today or _book_today_iso()
    live_date = as_of == today
    if live_date:
        from data.bloomberg.live import STALE_AFTER_SECONDS
        now = now or datetime.now(timezone.utc)

    todays, previous = [], {}
    for mark_date, instrument_id, mark_type, settle, value, snapped_at in conn.execute(
            _SUSPECT_MARKS_SQL, {"as_of": as_of, "prev": prev_day}):
        if mark_date == as_of:
            todays.append((instrument_id, mark_type, settle, value, snapped_at))
        else:
            previous[(instrument_id, mark_type, "" if mark_type == "SPOT" else settle)] = value

    rows = []
    for instrument_id, mark_type, settle, value, snapped_at in todays:
        before = previous.get((instrument_id, mark_type, "" if mark_type == "SPOT" else settle))
        flags, note, change, value_flag = [], "", None, False
        if before is None:
            note = (f"no SPOT on file for {prev_day}: not compared" if mark_type == "SPOT" else
                    f"no {prev_day} mark for this settle date: not compared, only the pair's SPOT is")
        else:
            try:
                now_value, before_value = float(value), float(before)
            except (TypeError, ValueError):
                now_value = before_value = None
                note = "a stored value is not a number: not compared"
            if before_value is not None:
                if before_value != 0:
                    change = (now_value / before_value - 1.0) * 100.0
                if now_value == before_value:
                    flags.append(f"exactly unchanged from {prev_day} (stale or copied mark)")
                    value_flag = True
                elif change is not None and abs(change) > BAD_TICK_PCT:
                    flags.append(f"moved {abs(change):.1f} %, above {BAD_TICK_PCT:g} %")
                    value_flag = True
        if live_date:
            age = _snap_age_seconds(snapped_at, now)
            if age is None:
                flags.append("snap time not readable")
            elif age > STALE_AFTER_SECONDS:
                flags.append(f"snapped {_age_words(age)} ago, older than {STALE_AFTER_SECONDS // 60} min")
        rows.append({
            "instrument_id": instrument_id, "mark_type": mark_type, "settle_date": settle,
            "value": _fmt_mark(value), "previous": "" if before is None else _fmt_mark(before),
            "previous_date": "" if before is None else prev_day,
            "change_pct": "" if change is None else f"{change:+.2f} %",
            "snapped_at": str(snapped_at or "").replace("T", " "),
            "flag": "; ".join(flags), "note": note,
            "_order": (not flags, not value_flag, -abs(change or 0.0)),
        })
    rows.sort(key=lambda r: (r["_order"], r["instrument_id"], r["mark_type"] != "SPOT", r["settle_date"]))
    for r in rows:
        del r["_order"]
    return {"prev_day": prev_day, "prev_has_marks": bool(previous), "rows": rows}


_SUSPECT_COLUMNS = [
    ("Instrument", "instrument_id"), ("Mark type", "mark_type"), ("Settle date", "settle_date"),
    ("Value", "value"), ("Previous", "previous"), ("Previous date", "previous_date"),
    ("Change", "change_pct"), ("Snapped at", "snapped_at"), ("Flag", "flag"), ("Note", "note"),
]


def suspect_panel(conn: sqlite3.Connection, as_of: str, now: Optional[datetime] = None,
                  today: Optional[str] = None) -> html.Div:
    """Panel 3: flagged marks in the open, the full list under a "Show all N marks" element."""
    found = suspect_rows(conn, as_of, now=now, today=today)
    rows, prev_day = found["rows"], found["prev_day"]
    if not rows:
        return _panel(SUSPECT_TITLE, [html.P(f"No official spot, forward or futures mark the book uses is on "
                                             f"file for {as_of}, so there is nothing to check.")])
    from data.bloomberg.live import STALE_AFTER_SECONDS
    children = [_kicker(
        f"Every official SPOT on {as_of}, and every official forward and futures price an open leg reads, against "
        f"the same mark on {prev_day}, the previous business day. Flagged: a move above {BAD_TICK_PCT:g} %, a value "
        f"exactly unchanged from the previous close (stale or copied), or, on today's date, a snap older than "
        f"{STALE_AFTER_SECONDS // 60} minutes. A comparison of two stored marks: nothing is recomputed.")]
    if not found["prev_has_marks"]:
        from ui.tabs.header import backfill_status, past_close_explanation
        children.append(html.P(
            f"No official marks are on file for {prev_day}, the previous business day, so the marks of {as_of} "
            f"cannot be compared with a previous close: {past_close_explanation(backfill_status(conn), prev_day)}."))
    flagged = [r for r in rows if r["flag"]]
    compared = sum(1 for r in rows if r["previous"])
    if flagged:
        children.append(html.P(f"{len(flagged)} of {len(rows)} marks flagged ({compared} compared with {prev_day})."))
        children.append(_panel_table(SUSPECT_FLAGGED_TABLE_ID, _SUSPECT_COLUMNS, flagged, wide=("flag", "note"),
                                     numeric=("value", "previous", "change_pct")))
    elif found["prev_has_marks"]:
        children.append(html.P(f"No mark is flagged: {compared} of {len(rows)} marks compared with {prev_day}."))
    children.append(html.Details(className="details", children=[
        html.Summary(f"Show all {len(rows)} marks"),
        _panel_table(SUSPECT_ALL_TABLE_ID, _SUSPECT_COLUMNS, rows, wide=("flag", "note"),
                     numeric=("value", "previous", "change_pct"), page_size=25),
    ]))
    return _panel(SUSPECT_TITLE, children)


# ---- "Past closes the header needs"
def header_reference_labels(as_of: date) -> Dict[str, List[str]]:
    """ISO date -> the header figures that read that past close, built with the same
    engine.pnl.calendar functions ui/tabs/header.py::_build_figures uses. Previous day is
    LTD(t-1) - LTD(t-2), so it reads both of those dates."""
    from engine.pnl.calendar import (_last_business_day_of_prev_month, _last_business_day_of_prev_year,
                                     _n_business_days_back, _prev_business_day, load_holidays)
    holidays = load_holidays()
    t1 = _prev_business_day(as_of, holidays)
    reads = [("Daily", t1), ("Previous day", t1), ("Previous day", _prev_business_day(t1, holidays)),
             ("5d", _n_business_days_back(as_of, 5, holidays)),
             ("MTD", _last_business_day_of_prev_month(as_of, holidays)),
             ("YTD", _last_business_day_of_prev_year(as_of, holidays))]
    out: Dict[str, List[str]] = {}
    for label, day in reads:
        labels = out.setdefault(day.isoformat(), [])
        if label not in labels:
            labels.append(label)
    return out


def past_close_rows(conn: sqlite3.Connection, header_as_of: str, backfill: Optional[dict] = None) -> List[dict]:
    """One row per date of `data.bloomberg.backfill.reference_dates(header_as_of)`, newest
    first: which figures read it, needed / present / complete, and when it is incomplete the
    header's own sentence (`ui.tabs.header.past_close_explanation`). The counts come from
    `ui.tabs.header.needed_marks`, which for a past date is `close_completeness(day, day)`,
    the past-close needs list the backfill fills; the header's sentence counts the same way."""
    from data.bloomberg.backfill import reference_dates
    from ui.tabs.header import backfill_status, needed_marks, past_close_explanation
    day0 = date.fromisoformat(header_as_of)
    labels = header_reference_labels(day0)
    if backfill is None:
        backfill = backfill_status(conn)
    rows = []
    for ref in reference_dates(day0):
        day = ref.isoformat()
        needed, missing = needed_marks(conn, day)
        if not needed:
            state, why, flag = "nothing needed", "no FX, futures or option trade was open that day", ""
        elif missing:
            state, why, flag = f"incomplete: {len(missing)} missing", past_close_explanation(backfill, day), "incomplete"
        else:
            state, why, flag = "complete", "", ""
        rows.append({"date": day, "read_by": ", ".join(labels.get(day, [])), "needed": needed,
                     "present": needed - len(missing), "state": state, "why": why, "flag": flag})
    return rows


def past_closes_panel(conn: sqlite3.Connection, header_as_of: str, backfill: Optional[dict] = None) -> html.Div:
    """Panel 2. It replaces nothing: the 20-day completeness strip stays above it."""
    rows = past_close_rows(conn, header_as_of, backfill)
    return _panel(PAST_CLOSES_TITLE, [
        _kicker(f"The header's Daily, Previous day, 5d, MTD and YTD figures difference the LTD of {header_as_of} "
                "(the header's as-of date) against these past closes. A close is complete when every mark the "
                "book needed that day is official; the Bloomberg backfill fills them by itself after each pull."),
        _panel_table(PAST_CLOSES_TABLE_ID, [
            ("Date", "date"), ("Read by", "read_by"), ("Needed", "needed"), ("Present", "present"),
            ("Status", "state"), ("Why it is incomplete", "why"),
        ], rows, wide=("why",), numeric=("needed", "present")),
    ])


LIBRARY_TITLE = "Bloomberg library"
LIBRARY_TABLE_ID = "market-data-library-table"


def library_panel(conn: sqlite3.Connection, as_of: str) -> html.Details:
    """The Bloomberg library (data/bloomberg/library.py, user decision 2026-09-21): every
    Bloomberg security a pull on `as_of` asks for, what it is for, how many trades need it
    and until when. It changes only when trades come in; nothing outside it is pulled.
    Collapsed by default: the summary line is the monitor, the table is the detail."""
    from data.bloomberg import library
    rows = library.tickers(conn, as_of)
    trades = len({r["trade_id"] for r in library.needed_on(conn, as_of)})
    summary = (f"{LIBRARY_TITLE} · {len(rows)} ticker(s) for {trades} trade(s) on {as_of} · "
               "changes only when trades come in · pulled only on request")
    if not rows:
        body = [_kicker("No trade on file needs anything from Bloomberg on this date.")]
    else:
        body = [_kicker("Everything \"Pull Bloomberg now\" asks for, and nothing else. A forward curve is one "
                        "request per pair; a vol smile and an OIS curve are one ticker per point."),
                _panel_table(LIBRARY_TABLE_ID,
                             [("Ticker", "ticker"), ("Field", "field"), ("Used for", "used_for"),
                              ("Trades", "trades"), ("Needed until", "needed_until"), ("In the library since", "added_at")],
                             [{**r, "flag": ""} for r in rows], numeric=("trades",))]
    return html.Details(className="details", children=[html.Summary(summary), *body])


UNPRICED_TITLE = "Trades left out of the headline P&L"
UNPRICED_TABLE_ID = "market-data-unpriced-trades-table"


def unpriced_trade_rows(conn: sqlite3.Connection, as_of: str) -> Tuple[int, List[dict]]:
    """(trades in the book, one row per trade the header's figures leave out on `as_of`),
    each with the book's own plain-language reason -- the same `value_book` rows and
    reasons the header counts as "excludes N of M trades unpriced", listed by name."""
    from ui.tabs.blotter_pricing import priced_value_book
    df = priced_value_book(conn, as_of)[0]
    if df.empty:
        return 0, []
    out = df[df["reason"] != ""]
    rows = [{"trade_id": r.trade_id, "product": r.product, "instrument_id": r.instrument_id,
             "trade_date": getattr(r, "trade_date", ""), "status": getattr(r, "status", ""),
             "reason": r.reason, "flag": ""}
            for r in out.sort_values(["product", "instrument_id", "trade_id"]).itertuples()]
    return len(df), rows


def unpriced_trades_panel(conn: sqlite3.Connection, as_of: str) -> html.Div:
    """Every trade that is on file but missing from the headline P&L on the header's date,
    and why (user, 2026-09-21). The count here is the header's own "excludes N of M"."""
    total, rows = unpriced_trade_rows(conn, as_of)
    if not rows:
        return _panel(UNPRICED_TITLE, [_kicker(f"All {total} trades on file are priced on {as_of}: nothing is left "
                                               "out of the headline figures.")])
    by_reason: Dict[str, int] = {}
    for r in rows:
        key = re.sub(r"\s+for\s+\S+.*$", "", r["reason"]) or r["reason"]
        by_reason[key] = by_reason.get(key, 0) + 1
    top = "; ".join(f"{n} x {why}" for why, n in sorted(by_reason.items(), key=lambda kv: -kv[1])[:4])
    return _panel(f"{UNPRICED_TITLE} · {len(rows)} of {total} on {as_of}", [
        _kicker("These trades are in the book but have no P&L on this date, so the header's LTD, Daily, 5d, MTD "
                "and YTD leave them out. Most common reasons: " + top + "."),
        _panel_table(UNPRICED_TABLE_ID,
                     [("Trade id", "trade_id"), ("Product", "product"), ("Instrument", "instrument_id"),
                      ("Trade date", "trade_date"), ("Status", "status"), ("Why it has no P&L", "reason")],
                     rows, wide=("reason",)),
    ])


UPLOAD_ISSUES_TITLE = "Rows of the blotter file that did not become trades"
UPLOAD_ISSUES_TABLE_ID = "market-data-upload-issues-table"


def upload_issue_rows(conn: sqlite3.Connection) -> List[dict]:
    try:
        found = conn.execute("SELECT row_no, symbol, kind, reason, filename, uploaded_at FROM upload_issues "
                             "ORDER BY row_no").fetchall()
    except sqlite3.Error:      # no upload since this panel was added: no table yet
        return []
    return [{"row_no": n, "symbol": sym, "kind": kind, "reason": why, "filename": name, "uploaded_at": at,
             "flag": "x"} for n, sym, kind, why, name, at in found]


def upload_issues_panel(conn: sqlite3.Connection) -> html.Div:
    """What the last upload left out of the book entirely, row by row, with the parser's own
    reason -- these are not in any table or count elsewhere in the app."""
    rows = upload_issue_rows(conn)
    if not rows:
        return _panel(UPLOAD_ISSUES_TITLE, [_kicker("Nothing on file: every row of the last upload became a trade "
                                                    "(or no blotter has been uploaded since this list was added; "
                                                    "upload the file again to fill it).")])
    return _panel(f"{UPLOAD_ISSUES_TITLE} · {len(rows)} in {rows[0]['filename']}", [
        _kicker("These rows are in your file but NOT in the book: no P&L, no position, not in any count. "
                "REJECTED = the row could not be read; NOT LOADED = a type the app does not handle yet."),
        _panel_table(UPLOAD_ISSUES_TABLE_ID,
                     [("File row", "row_no"), ("Symbol", "symbol"), ("What happened", "kind"), ("Why", "reason")],
                     rows, wide=("reason",), numeric=("row_no",)),
    ])


def safe_panel(title: str, build: Callable[[], html.Div]) -> html.Div:
    """One panel failing must not take the tab, or the other panels, down with it."""
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 -- say what failed where the panel would be
        import logging
        logging.getLogger(__name__).exception("Market data panel %r failed", title)
        return _panel(title, [message_box(f"This panel could not be built ({type(exc).__name__}: {exc}).")])


def whole_book_panels(conn: sqlite3.Connection, as_of: str, status: Optional[dict] = None,
                      header_as_of: Optional[str] = None) -> Tuple[html.Div, html.Div, html.Div]:
    """(What is missing, Marks that look wrong, Past closes the header needs) for the tab's
    `_update_body` render path. The first two follow the tab's own as-of date. The third
    follows the HEADER's as-of date (`header_as_of`, the Ladder tab's date picker, which is
    what ui/tabs/header.py differences from), and the New York book date when the header has
    none yet."""
    header_day = header_as_of or _book_today_iso()
    return (html.Div([safe_panel(UPLOAD_ISSUES_TITLE, lambda: upload_issues_panel(conn)),
                      safe_panel(UNPRICED_TITLE, lambda: unpriced_trades_panel(conn, header_day)),
                      safe_panel(MISSING_TITLE, lambda: missing_panel(conn, as_of, status)),
                      safe_panel(LIBRARY_TITLE, lambda: library_panel(conn, as_of))]),
            safe_panel(SUSPECT_TITLE, lambda: suspect_panel(conn, as_of)),
            safe_panel(PAST_CLOSES_TITLE, lambda: past_closes_panel(conn, header_day)))


def manual_entry_form(default_pair: Optional[str] = None) -> html.Div:
    """Manual mark entry calling `data.bloomberg.manual.write_manual_mark`, pre-filled
    with the pair currently selected at the top of the tab."""
    return html.Div(className="market-data-manual-entry", children=[
        html.H4("Manual mark entry"),
        html.P("MANUAL is official only for DELTA and PREMIUM; for SPOT / FWD_OUTRIGHT / FUTURE_PX / "
               "PAR_RATE / PV_USD / DV01_USD a manual row is visible here (see the table below) but is "
               "never used by valuation -- only an official mark prices a trade."),
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
                          dcc.Loading(type="dot", color="#1f5fbf", style={"display": "inline-block"},
                                      children=[html.Span(id=PULL_NOW_STATUS_ID, className="status-line",
                                                          style={"marginLeft": "8px"})])]),
                dcc.Store(id=PULL_REVISION_ID),
            ]),
            html.Div(id=STATUS_ID, className="status-line"),
        ]),
        dcc.Interval(id=REFRESH_ID, interval=REFRESH_MS, n_intervals=0),
        # Whole-book checks first: what is missing, then what looks wrong. Neither depends
        # on the pair dropdown. Static containers: each is an Output of `_update_body`.
        html.Div(id=MISSING_PANEL_ID),
        html.Div(id=SUSPECT_PANEL_ID),
        html.H4("Spot and forward curve for the selected pair"),
        html.Div(id=BODY_ID),
        html.H4("Close completeness"),
        html.Div(id="market-data-completeness-container"),
        html.Div(id=PAST_CLOSES_PANEL_ID, style={"marginTop": "16px"}),
        manual_entry_form(),  # static: its ids are callback inputs and must exist on first render
        html.Div(className="bbg-check-block", children=[
            html.Button("Check Bloomberg connection", id=BBG_CHECK_BUTTON_ID,
                        n_clicks=0, className="bbg-check-button"),
            dcc.Loading(type="dot", color="#1f5fbf",
                        children=[html.Div(id=BBG_RESULTS_ID, className="bbg-check-results")]),
        ]),
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
        Input(BOOK_REVISION_ID, "data"),
    )
    def _update_pairs(as_of_date, _book_rev=None):
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

    # The header's own as-of date (the Ladder tab's date picker, mirrored into this store by
    # ui/app.py): "Past closes the header needs" must list the dates the header itself reads.
    from ui.tabs.header import AS_OF_STORE_ID as HEADER_AS_OF_STORE_ID

    @app.callback(
        Output(BODY_ID, "children"),
        Output("market-data-completeness-container", "children"),
        Output(STATUS_ID, "children"),
        Output(MANUAL_INSTRUMENT_ID, "value"),
        Output(MISSING_PANEL_ID, "children"),
        Output(SUSPECT_PANEL_ID, "children"),
        Output(PAST_CLOSES_PANEL_ID, "children"),
        Input(DATE_PICKER_ID, "date"),
        Input(PAIR_DROPDOWN_ID, "value"),
        Input(REFRESH_ID, "n_intervals"),
        Input(PULL_REVISION_ID, "data"),
        Input(MANUAL_STATUS_ID, "children"),
        Input(DATA_REVISION_ID, "data"),
        Input(HEADER_AS_OF_STORE_ID, "data"),
    )
    def _update_body(as_of_date, pair, _n_intervals=0, _pull_rev=None, _manual_status=None, _data_rev=None,
                     header_as_of=None):
        return _render(as_of_date, pair, header_as_of)

    def _render(as_of_date, pair, header_as_of=None):
        blank = html.Div()
        if not as_of_date:
            return (message_box("No as-of date available."), blank, "Bloomberg: status unknown", pair,
                    blank, blank, blank)

        db_path = get_db_path()
        try:
            conn = _connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return (message_box(f"Database not available ({exc})."), blank, "Bloomberg: status unknown", pair,
                    blank, blank, blank)

        try:
            try:
                from data.bloomberg.live import read_status
                feed_status = read_status(db_path)
            except ImportError:
                feed_status = None

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

            missing, suspect, past_closes = whole_book_panels(conn, as_of_date, feed_status, header_as_of)
        finally:
            conn.close()

        return body, strip, status_block(feed_status), pair, missing, suspect, past_closes

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

    @app.callback(
        Output(BBG_RESULTS_ID, "children"),
        Input(BBG_CHECK_BUTTON_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_bbg_check_click(n_clicks):
        # Runs on click only (prevent_initial_call): a Bloomberg-unreachable machine
        # never pays this cost just to load the page, and no other tab or callback
        # depends on this Output.
        checks = run_bloomberg_diagnostics_safe()
        return _render_bbg_results(checks)
