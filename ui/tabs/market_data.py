"""Market data tab: "Can I trust the numbers?" (BUILD_PLAN.md section 5).

User decision 2026-09-15: this tab is organised BY CURRENCY PAIR, not by a flat
inventory table. Layout:

  1. Top bar: as-of date picker, a pair dropdown (every FX instrument with trades or
     marks, default = the pair with the most open trades) -- the only dropdown on the
     tab -- the "Pull now" button and a one-line feed status, with a second compact line
     giving the last pull's seconds per step, slowest first, when the status file
     carries `timings` (`pull_timings_line`; nothing when absent). When the last press
     found no Bloomberg and re-priced the FX options from the marks on file instead
     (2026-09-22, status["recalc"]), `recalc_block` adds the pull's own sentence, its
     error if it stopped, and a collapsed day-by-day list with each skipped trade's reason.
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
and `backfill_headline` are folded into the single top-bar status line.
`diagnostics_panel` is still defined but no screen renders it any more (no caller left).

Commodity conversion, Phase 2 (user approval 2026-09-24): the macro trader's products left
the app, so this tab no longer shows the swap pricing, the fixings or the swap marks in the
manual form (PAR_RATE / PV_USD / DV01_USD). The pull's OIS step is now `status["curves"]`
(the option pricers still discount on those curves), shown in brief by `curves_block`.
Added the same day: Bloomberg's
contract dates of the commodity futures (`contract_dates_panel`, and the pull's
`status["contract_dates"]` block in the feed status), the futures never asked of Bloomberg
for want of a verified ticker (`status["not_requestable"]`), the library's gaps (a need with
no ticker, listed as a gap, not as a ticker) and the needs with no ticker in the
completeness strip's hover.

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
from ui.tabs import ranking as rk
from dash import Input, Output, State, dash_table, dcc, html

from ui.feed_controls import (PullGuard, click_outcome, pull_timings, recalc_words, safety_refresh_ms,
                              seconds_words)
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
RECALC_BLOCK_ID = "market-data-recalc"          # what a press did on a machine with no Bloomberg (2026-09-22)
RECALC_DAYS_ID = "market-data-recalc-days"
LEDGER_BLOCK_ID = "market-data-ledger"          # what the ledger's re-freeze did (2026-09-22)
CONTRACT_DATES_BLOCK_ID = "market-data-contract-dates-status"   # the pull's contract-dates block (2026-09-24)
CONTRACT_DATES_FAILED_ID = "market-data-contract-dates-failed"
NOT_REQUESTABLE_ID = "market-data-not-requestable"              # futures never asked for (2026-09-24)
CURVES_BLOCK_ID = "market-data-curves-status"                   # the pull's OIS curve step (2026-09-24)
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
    for t in ("SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "DELTA", "PREMIUM")
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


# --------------------------------------------------------------------------- feed status pieces
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


def top_bar_status(status: Optional[dict], say_recalc: bool = True) -> str:
    """The single top-bar status line: feed headline plus backfill progress when
    there is any to report. `say_recalc=False` keeps the options-recalc sentence off it,
    for `status_block`, which prints that sentence in full underneath (`recalc_block`)."""
    line = feed_headline(status, say_recalc=say_recalc)
    bf_line = backfill_headline(status)
    if bf_line:
        line = f"{line} | {bf_line}"
    return line


def _count(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def closed_out_count(block: Optional[dict]) -> int:
    """How many closed-out options the options step left unpriced, from a status block's
    "closed_out" key (2026-09-22, user: "we dont need to price all options, as some of them
    might be closed out already"): a list of trade ids in the pull's options block and in
    each recalc / backfill day, a count at the recalc block's top level. Read defensively:
    0 when the key is absent (a status file from before the change), empty or not a list
    or a number, so the caller says nothing and renders as before."""
    if not isinstance(block, dict):
        return 0
    value = block.get("closed_out")
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, bool):
        return 0
    return _count(value) if isinstance(value, (int, float, str)) else 0


def closed_out_words(n: int) -> str:
    """'3 closed-out options not priced' (the pull's own wording), '' for none."""
    return f"{n} closed-out option{'s' if n != 1 else ''} not priced" if n > 0 else ""


def recalc_day_rows(recalc: Optional[dict]) -> List[dict]:
    """One row per day of `status["recalc"]["days"]` (engine.options.store.recalc_on_file's
    per-day dicts, {"day", "priced", "skipped": [{"trade_id", "reason"}], "closed_out":
    [trade ids not priced because the option is closed out, 2026-09-22; absent on an older
    file], "error" when that day stopped}), read defensively: {"day", "priced", "skipped"
    (a count), "closed_out" (a count, 0 when the key is absent), "reasons"
    (["<trade>: <reason>", ...]), "error"}. Nothing is recomputed: the counts are the
    pricer's own, and a malformed entry is left out rather than guessed at."""
    rows: List[dict] = []
    for entry in (recalc or {}).get("days") or []:
        if not isinstance(entry, dict):
            continue
        skipped = entry.get("skipped")
        if isinstance(skipped, list):
            reasons = [f"{s.get('trade_id') or '?'}: {s.get('reason') or 'no reason given'}"
                       for s in skipped if isinstance(s, dict)]
            count = len(skipped)
        else:
            reasons, count = [], _count(skipped)
        rows.append({"day": str(entry.get("day") or "?"), "priced": _count(entry.get("priced")),
                     "skipped": count, "closed_out": closed_out_count(entry), "reasons": reasons,
                     "error": str(entry.get("error") or "")})
    return rows


def recalc_block(status: Optional[dict]) -> Optional[html.Div]:
    """What "Pull Bloomberg now" did on a machine with no Bloomberg (user decision
    2026-09-22: "pull bbg now should recalc options too, using log data if no bbg access"):
    the pull's own sentence (`status["recalc_summary"]`), its error when the re-pricing
    stopped, and a collapsed day-by-day list (day, priced, skipped, closed-out when any) with each skipped
    trade's reason listed under its day and on hover. Read from the status file only,
    never from the pricer. None when the last status carries no `recalc` block: a
    connected pull, or a status file from before the change."""
    recalc = (status or {}).get("recalc")
    if not isinstance(recalc, dict):
        return None
    children: list = []
    sentence = recalc_words(status, drop_head=False)
    if sentence:
        children.append(html.Div(sentence, className="status-line"))
    if recalc.get("error"):
        children.append(html.Div(f"Re-pricing stopped: {recalc['error']}", className="status-line status-line--bad"))
    rows = recalc_day_rows(recalc)
    if rows:
        items = []
        for row in rows:
            text = f"{row['day']}: {row['priced']} priced, {row['skipped']} skipped"
            closed = closed_out_words(row["closed_out"])
            if closed:
                text += f", {closed}"
            if row["error"]:
                text += f" · stopped: {row['error']}"
            nested = ([html.Ul([html.Li(reason) for reason in row["reasons"]], style={"margin": "0 0 0 16px", "padding": 0})]
                      if row["reasons"] else [])
            items.append(html.Li([text, *nested], title="\n".join(row["reasons"]) or None))
        children.append(html.Details(className="status-line", children=[
            html.Summary(f"Day by day: {len(rows)} day(s)"),
            html.Ul(items, id=RECALC_DAYS_ID, style={"margin": "2px 0 0 16px", "padding": 0}),
        ]))
    return html.Div(children, id=RECALC_BLOCK_ID, className="status-line")


def usd_words(value) -> str:
    """'USD 12,500' / 'USD -1,234': whole dollars with thousands separators, the way the
    tab writes money elsewhere (`notional_words`). 'n/a' for anything that is not a
    number: a blank is never shown as zero."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v != v:  # NaN
        return "n/a"
    return f"USD {v:,.0f}"


def _ledger_entry_line(entry) -> Tuple[str, str]:
    """(line, hover) for one `refrozen` entry: "trade_id product: pnl_from -> pnl_to (why)",
    the mark type and its date on hover. An entry that is a bare string shows as its id
    alone; a dict with keys missing shows what it has."""
    if not isinstance(entry, dict):
        return (str(entry), "")
    head = " ".join(str(entry.get(k)) for k in ("trade_id", "product") if entry.get(k)) or "?"
    why = str(entry.get("why") or "").strip()
    line = f"{head}: {usd_words(entry.get('pnl_from'))} -> {usd_words(entry.get('pnl_to'))}"
    if why:
        line += f" ({why})"
    hover = " ".join(str(entry.get(k)) for k in ("mark_type", "spot_as_of_date") if entry.get(k))
    return (line, hover)


def _kept_entry_line(entry) -> str:
    """"trade_id product: reason" for one `kept` entry; a bare string is its id alone."""
    if not isinstance(entry, dict):
        return str(entry)
    head = " ".join(str(entry.get(k)) for k in ("trade_id", "product") if entry.get(k)) or "?"
    reason = str(entry.get("reason") or "").strip()
    return f"{head}: {reason}" if reason else head


def refrozen_rows(block: Optional[dict]) -> dict:
    """What a ledger status block says the re-freeze did (2026-09-22, the keys bbg-data
    writes on `status["ledger"]`, on each backfill day's ledger block and on the closing
    step's): {"summary": the block's own sentence, "count", "refrozen": [(line, hover)],
    "kept": [line]}. Every key read defensively: absent or empty means nothing, so an
    older status file gives an empty result and the caller says nothing."""
    out = {"summary": "", "count": 0, "refrozen": [], "kept": []}
    if not isinstance(block, dict):
        return out
    refrozen = block.get("refrozen")
    out["refrozen"] = [_ledger_entry_line(e) for e in refrozen] if isinstance(refrozen, (list, tuple)) else []
    kept = block.get("kept")
    out["kept"] = [_kept_entry_line(e) for e in kept] if isinstance(kept, (list, tuple)) else []
    count = block.get("refrozen_count")
    out["count"] = _count(count) if not isinstance(count, bool) else 0
    if not out["count"]:
        out["count"] = len(out["refrozen"])
    summary = block.get("refrozen_summary")
    if isinstance(summary, str) and summary.strip():
        out["summary"] = summary.strip()
    elif out["count"]:
        out["summary"] = f"{out['count']} settled trade{'s' if out['count'] != 1 else ''} re-frozen at the close"
    return out


def _is_ledger_block(value) -> bool:
    return isinstance(value, dict) and any(k in value for k in ("refrozen", "kept", "refrozen_count", "refrozen_summary"))


def ledger_blocks(status: Optional[dict]) -> List[Tuple[str, dict]]:
    """Every ledger block the status file carries, labelled: the pull's own step
    (`status["ledger"]`, "Ledger"), the backfill's closing step and each backfill day's
    (under `status["backfill"]`, as a "ledger" block of its own or on a day's entry;
    "Backfill closing step" / "Backfill <day> ledger"). Only blocks that say something
    are returned, so a status file from before the change gives []."""
    found: List[Tuple[str, dict]] = []
    status = status or {}
    if _is_ledger_block(status.get("ledger")):
        found.append(("Ledger", status["ledger"]))
    backfill = status.get("backfill")
    if isinstance(backfill, dict):
        bf_ledger = backfill.get("ledger")
        if _is_ledger_block(bf_ledger):
            found.append(("Backfill closing step", bf_ledger))
        elif isinstance(bf_ledger, dict):
            for day, block in sorted(bf_ledger.items(), reverse=True):
                if _is_ledger_block(block):
                    label = "Backfill closing step" if str(day) in ("closing", "closing_step", "after") else f"Backfill {day} ledger"
                    found.append((label, block))
        days = backfill.get("days")
        if isinstance(days, dict):
            for day, entry in sorted(days.items(), reverse=True):
                if isinstance(entry, dict) and _is_ledger_block(entry.get("ledger")):
                    found.append((f"Backfill {day} ledger", entry["ledger"]))
    out = []
    for label, block in found:
        rows = refrozen_rows(block)
        if rows["refrozen"] or rows["kept"] or rows["count"]:
            out.append((label, block))
    return out


def ledger_block(status: Optional[dict]) -> Optional[html.Div]:
    """What the ledger's re-freeze did on the last press (user yes 2026-09-22): per ledger
    block the summary sentence and, collapsed under it, one line per re-frozen trade
    "trade_id product: pnl_from -> pnl_to (why)" followed by the trades it kept as they
    were, "trade_id product: reason". Read from the status file only. None when no block
    carries the keys (an older status file, or a press that re-froze nothing), so the
    feed status renders exactly as before."""
    blocks = ledger_blocks(status)
    if not blocks:
        return None
    children: list = []
    for label, block in blocks:
        rows = refrozen_rows(block)
        sentence = rows["summary"] or f"{len(rows['kept'])} settled trade(s) kept as frozen"
        children.append(html.Div(f"{label}: {sentence}", className="status-line"))
        items = [html.Li(line, title=hover or None) for line, hover in rows["refrozen"]]
        items += [html.Li(f"kept: {line}") for line in rows["kept"]]
        if items:
            summary = f"Re-frozen: {len(rows['refrozen'])} trade(s)"
            if rows["kept"]:
                summary += f", kept: {len(rows['kept'])}"
            children.append(html.Details(className="status-line", children=[
                html.Summary(summary),
                html.Ul(items, style={"margin": "2px 0 0 16px", "padding": 0}),
            ]))
    return html.Div(children, id=LEDGER_BLOCK_ID, className="status-line")


def pull_timings_line(status: Optional[dict]) -> str:
    """'Last pull took 48 s: forwards 21 s · options 12 s · curves 8.0 s · ...', the steps
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


def _failure_line(entry) -> str:
    """"ticker: reason" for one `failed` entry of the contract-dates block; a bare string is
    its ticker alone, a dict with a key missing shows what it has."""
    if not isinstance(entry, dict):
        return str(entry)
    ticker = str(entry.get("ticker") or "?")
    reason = str(entry.get("reason") or "").strip()
    return f"{ticker}: {reason}" if reason else f"{ticker}: no reason given"


def contract_dates_block(status: Optional[dict]) -> Optional[html.Div]:
    """What the last pull did with Bloomberg's contract dates of the commodity futures
    (2026-09-24, bbg-live's `status["contract_dates"]`: {requested, stored, failed:
    [{ticker, reason}], applied, summary}): the pull's own sentence ("N contract date(s)
    stored, M future(s) moved to Bloomberg's expiry"), its error when the library could not
    be read, and the tickers that gave no date in a collapsed list with Bloomberg's reason
    for each. Read from the status file only. None when the key is absent (an older status
    file) or the block says nothing (no commodity future asked, nothing moved)."""
    block = (status or {}).get("contract_dates")
    if not isinstance(block, dict):
        return None
    summary = block.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    failed = block.get("failed")
    failed = [_failure_line(e) for e in failed] if isinstance(failed, (list, tuple)) else []
    error = str(block.get("error") or "").strip()
    if not (summary or failed or error):
        return None
    children: list = []
    if summary:
        children.append(html.Div(f"Contract dates: {summary}", className="status-line"))
    if error:
        children.append(html.Div(f"Contract dates: {error}", className="status-line status-line--bad"))
    if failed:
        children.append(html.Details(className="status-line", children=[
            html.Summary(f"Tickers that gave no contract date: {len(failed)}"),
            html.Ul([html.Li(line) for line in failed], id=CONTRACT_DATES_FAILED_ID,
                    style={"margin": "2px 0 0 16px", "padding": 0}),
        ]))
    return html.Div(children, id=CONTRACT_DATES_BLOCK_ID, className="status-line")


def curves_block(status: Optional[dict]) -> Optional[html.Div]:
    """The pull's OIS curve step in brief (2026-09-24, bbg-live's `status["curves"]`:
    {as_of_date, currencies: {ccy: {quotes, nodes, error}}, bootstrapped, seconds}, plus
    `skipped` or `error`): one line naming each currency with its curve nodes ("USD 18
    nodes"), or the step's skip reason, or its error in red; the currencies that got no
    curve, each with its reason, in a collapsed list. Counts are the pull's own. None when
    the key is absent (an older status file)."""
    block = (status or {}).get("curves")
    if not isinstance(block, dict):
        return None
    skipped = str(block.get("skipped") or "").strip()
    error = str(block.get("error") or "").strip()
    currencies = block.get("currencies")
    currencies = currencies if isinstance(currencies, dict) else {}
    built, failed = [], []
    for ccy, entry in sorted(currencies.items()):
        entry = entry if isinstance(entry, dict) else {}
        nodes, quotes = _count(entry.get("nodes")), _count(entry.get("quotes"))
        why = str(entry.get("error") or "").strip()
        if nodes and not why:
            built.append(f"{ccy} {nodes} node{'s' if nodes != 1 else ''}")
        else:
            failed.append(f"{ccy}: {why or f'{quotes} quote(s), no curve built'}")
    children: list = []
    if skipped and not currencies:
        children.append(html.Div(f"OIS curves: not pulled, {skipped}", className="status-line"))
    elif currencies:
        line = f"OIS curves: {', '.join(built) if built else 'none built'}"
        if failed:
            line += f"; {len(failed)} currenc{'ies' if len(failed) != 1 else 'y'} without a curve"
        children.append(html.Div(line, className="status-line"))
    if error:
        children.append(html.Div(f"OIS curves: {error}", className="status-line status-line--bad"))
    if failed:
        children.append(html.Details(className="status-line", children=[
            html.Summary(f"Without a curve: {len(failed)}"),
            html.Ul([html.Li(line) for line in failed], style={"margin": "2px 0 0 16px", "padding": 0}),
        ]))
    if not children:
        return None
    return html.Div(children, id=CURVES_BLOCK_ID, className="status-line")


def _not_requestable_line(entry) -> Tuple[str, str]:
    """(line, hover) for one `not_requestable` entry: "CLZ26 Comdty (2026-11-19, 2 trades):
    reason", the trade ids on hover. A bare string is its id alone."""
    if not isinstance(entry, dict):
        return (str(entry), "")
    instrument = str(entry.get("instrument_id") or "?")
    trade_ids = entry.get("trade_ids")
    trade_ids = [str(t) for t in trade_ids] if isinstance(trade_ids, (list, tuple)) else []
    bits = [b for b in (str(entry.get("settle_date") or ""),
                        f"{len(trade_ids)} trade{'s' if len(trade_ids) != 1 else ''}" if trade_ids else "") if b]
    head = f"{instrument} ({', '.join(bits)})" if bits else instrument
    reason = str(entry.get("reason") or "").strip() or "no reason given"
    return (f"{head}: {reason}", ", ".join(trade_ids))


def not_requestable_block(status: Optional[dict]) -> Optional[html.Div]:
    """The futures the last pull never asked Bloomberg for, for want of a verified
    Bloomberg ticker (2026-09-24, bbg-live's `status["not_requestable"]`: [{instrument_id,
    settle_date, trade_ids, reason}]): one sentence and a short list, each with the
    library's own reason and its trade ids on hover. They have no price from Bloomberg
    until the root is verified. None when the key is absent or the list is empty."""
    entries = (status or {}).get("not_requestable")
    if not isinstance(entries, (list, tuple)) or not entries:
        return None
    lines = [_not_requestable_line(e) for e in entries]
    n = len(lines)
    return html.Div(id=NOT_REQUESTABLE_ID, className="status-line", children=[
        html.Div(f"Not asked of Bloomberg: {n} future{'s' if n != 1 else ''} with no verified Bloomberg ticker, "
                 "so no price from Bloomberg until the ticker is checked", className="status-line status-line--bad"),
        html.Ul([html.Li(line, title=hover or None) for line, hover in lines],
                style={"margin": "2px 0 0 16px", "padding": 0}),
    ])


def status_block(status: Optional[dict]):
    """What the tab's feed-status block shows: the one-line status, plus the last pull's
    per-step timings on a second compact line when the status file has them, plus what the
    pull did with the futures' contract dates (`contract_dates_block`) and the futures it
    could not ask for (`not_requestable_block`), plus what a press did on a machine with no
    Bloomberg (`recalc_block`) when the last status says so; that sentence is then kept off
    the first line, so it is read once; and what the ledger's re-freeze did
    (`ledger_block`) when the status carries it."""
    recalc = recalc_block(status)
    ledger = ledger_block(status)
    dates = contract_dates_block(status)
    unasked = not_requestable_block(status)
    curves = curves_block(status)
    line = top_bar_status(status, say_recalc=recalc is None)
    timings = pull_timings_line(status)
    extras = [block for block in (dates, unasked, curves, recalc, ledger) if block is not None]
    if not timings and not extras:
        return line
    parts: list = [line]
    if timings:
        parts.append(html.Div(timings, id=PULL_TIMINGS_ID, className="status-line"))
    return parts + extras


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
    closed = closed_out_words(closed_out_count(options_status))
    if closed:
        # the pull's "closed_out" list (2026-09-22): kept out of "skipped" by the pricer, said
        # once as a count here; nothing when the key is absent or empty
        lines.append(f"Options: {closed}.")
    for s in skipped or []:
        lines.append(f"Options {s.get('trade_id', '')}: not priced -- {s.get('reason') or 'no reason given'}.")
    return lines


def diagnostics_panel(status: Optional[dict], rates: Dict[str, dict], open_by_default: bool = False) -> html.Details:
    items: List[dict] = list((status or {}).get("items", []))
    failed = [i for i in items if i.get("status") == "FAILED"]
    rows = [{"status": i.get("status", ""), "instrument_id": i.get("instrument_id", ""),
             "mark_type": i.get("mark_type", ""), "settle_date": i.get("settle_date", ""),
             "value": rk.value(i.get("value")),
             "source": i.get("source", ""), "detail": i.get("detail", "")} for i in items]
    rate_rows = [{"currency": c, "pair": v.get("pair", ""), "rate": rk.value(v["rate"]),
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
            columns=[rk.numeric(n, i, rk.rate(8, trim=True)) if i == "value" else rk.text(n, i)
                     for n, i in [("Status", "status"), ("Pair", "instrument_id"),
                                  ("Mark", "mark_type"), ("Settle date", "settle_date"),
                                  ("Value", "value"), ("Source", "source"), ("Detail", "detail")]],
            **rk.sortable("market-data-diag-table"),   # ranks on a header click (ui.tabs.ranking)
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
            columns=[rk.numeric(n, i, rk.rate(8, trim=True)) if i == "rate" else rk.text(n, i)
                     for n, i in [("Currency", "currency"), ("Pair", "pair"), ("Rate", "rate"),
                                  ("USD per local", "inverted"), ("Source", "source"),
                                  ("Snapped at", "timestamp"), ("Freshness", "stale")]],
            **rk.sortable("market-data-rates-table"),
            data=rate_rows, style_table={"overflowX": "auto"}, style_cell=_MONO, style_header=_HEAD,
            style_data_conditional=[{"if": {"filter_query": "{stale} = 'STALE'"}, "color": "#8a4b00",
                                     "backgroundColor": "#fff4e5"}]),
    ]
    pricing_lines = _options_step_lines((status or {}).get("options"))
    if pricing_lines:
        children += [html.H4("Options pricing (this pull's cycle)"),
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
    formatted = df.copy().astype(object)
    for col in ("outright", "points", "used_by_book"):
        if col in formatted.columns:
            formatted[col] = pd.Series([rk.value(v) for v in df[col].tolist()], dtype=object, index=formatted.index)
    formats = {"outright": rk.rate(dp), "points": rk.rate(dp), "used_by_book": rk.count()}
    columns = [rk.numeric(n, i, formats[i]) if i in formats else rk.text(n, i) for n, i in [
        ("Settle date", "settle_date"), ("Tenor", "tenor"), ("Outright", "outright"),
        ("Fwd points", "points"), ("Source", "source"), ("Status", "status"),
        ("Used by book", "used_by_book"),
    ]]
    return dash_table.DataTable(
        id=CURVE_TABLE_ID, columns=columns, data=formatted.to_dict("records"), **rk.sortable(CURVE_TABLE_ID),
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


def _unrequestable_words(value) -> str:
    """'; 2 more with no Bloomberg ticker, not counted' from a close_completeness
    `not_requestable` cell (a list of {instrument_id, settle_date, mark_type, reason},
    2026-09-24; absent on an older inventory), '' when there are none."""
    if not isinstance(value, (list, tuple)) or not value:
        return ""
    names = sorted({str(i.get("instrument_id") or "?") for i in value if isinstance(i, dict)})
    shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    return (f"; {len(value)} more with no Bloomberg ticker, never asked for and not counted"
            + (f" ({shown})" if shown else ""))


def completeness_strip(df: pd.DataFrame) -> html.Div:
    """Compact row of small squares, one per business day, coloured by completeness,
    with a tooltip (title attribute) giving the counts for that day: the marks the book
    needed that a pull can ask for, and apart from them the needs with no Bloomberg ticker
    (`not_requestable`, which never make a day incomplete)."""
    if df.empty:
        return message_box("No completeness data.")
    squares = []
    for _, row in df.iterrows():
        complete = bool(row.get("complete"))
        title = (f"{row['as_of_date']}: {row['present']}/{row['needed']} needed marks on file as official closes"
                 + _unrequestable_words(row.get("not_requestable")))
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
      - a futures leg reads the FUTURE_PX at its expiry and, for a contract not in USD, the SPOT
        of its currency's USD pair (`live._usd_pair_name`, the name the library keys it by);
      - an FX option reads its pair's SPOT, the forward at its expiry and the same USD pairs.
    The pair names come from `data.bloomberg.live.option_spot_pair_names`, the function the
    needs list itself is built from, so a row here matches a row of the inventory.

    Notional is a face amount straight from the ticket: the leg's absolute USD amount where
    the pair has a USD leg, otherwise the base amount under its own currency; a future's is
    its NOTIONAL leg in the contract's currency; an option's is its base-currency notional.
    It is never converted with a mark."""
    from data.bloomberg.live import _usd_pair_name, option_spot_pair_names
    out: Dict[MarkKey, dict] = {}

    def add(key: MarkKey, trade_id: str, ccy: str, amount: float) -> None:
        entry = out.setdefault(key, {"trades": set(), "notional": {}})
        entry["trades"].add(trade_id)
        if ccy and amount:
            entry["notional"][ccy] = entry["notional"].get(ccy, 0.0) + amount

    for trade_id, instrument_id, asset_class, base, quote, ccy, amount, settle in conn.execute(
            _OPEN_LEGS_SQL, {"as_of": as_of}):
        if asset_class == "FUTURE":
            # the NOTIONAL leg is in the contract's own currency: shown under it, never converted.
            # A non-USD future also reads the SPOT of its currency's USD pair (2026-09-24: its P&L
            # converts at spot of the valuation date), the same pair name the library lists.
            add((instrument_id, "FUTURE_PX", settle), trade_id, ccy, _abs_amount(amount))
            if quote and quote != "USD":
                add((_usd_pair_name(quote), "SPOT", as_of), trade_id, ccy, _abs_amount(amount))
            continue
        wanted = "USD" if "USD" in (base, quote) else base
        shown_ccy, shown = (ccy, _abs_amount(amount)) if ccy == wanted else ("", 0.0)
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


_PANEL_FORMATS = {"value": rk.rate(8, trim=True), "previous": rk.rate(8, trim=True), "change_pct": rk.percent(2)}


def _panel_table(table_id: str, columns: List[Tuple[str, str]], rows: List[dict], wide: Tuple[str, ...] = (),
                 numeric: Tuple[str, ...] = (), page_size: int = PANEL_PAGE_SIZE) -> dash_table.DataTable:
    """The tab's DataTable look (`_MONO` / `_HEAD`), ranked on a header click (ui.tabs.ranking).
    `wide` columns hold sentences and wrap; `numeric` columns are right-aligned and, when
    their rows carry numbers, typed numeric with a display format (`_PANEL_FORMATS`, else
    a plain count / amount) so they rank as numbers; a row whose `flag` is not empty is
    tinted. Colours are plain hex: inside a DataTable var(--muted) and var(--accent) are
    dash-table's own, and border colours are forced grey by style.css (see
    ui/tabs/options.py::table_styles)."""
    def typed(name: str, col: str) -> dict:
        values = [r.get(col) for r in rows if r.get(col) not in (None, "")]
        if col in numeric and values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            return rk.numeric(name, col, _PANEL_FORMATS.get(col, rk.amount(2, trim=True)))
        return rk.text(name, col)

    return dash_table.DataTable(
        id=table_id, columns=[typed(name, col) for name, col in columns], data=rows,
        **rk.sortable(table_id),
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
    scope = ("Covers the marks requested from Bloomberg (spot, forwards, futures prices); option values are "
             "computed by the app from these.")
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
            "value": rk.value(value), "previous": rk.value(before),
            "previous_date": "" if before is None else prev_day,
            "change_pct": None if change is None else float(change),
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


LIBRARY_GAP_TICKER = "no ticker: not asked"


def library_rows(conn: sqlite3.Connection, as_of: str) -> Tuple[List[dict], List[dict]]:
    """(tickers a pull on `as_of` asks for, gaps: needs with no Bloomberg ticker to ask
    for), from `library.tickers`, every row shown. A gap (2026-09-24: `requestable` False,
    ticker '', the library's reason in `used_for`) is never shown as a ticker: its ticker
    cell says it is not asked, and its row is flagged."""
    from data.bloomberg import library
    asked, gaps = [], []
    for r in library.tickers(conn, as_of):
        if r.get("requestable", True):
            asked.append({**r, "flag": ""})
        else:
            gaps.append({**r, "ticker": LIBRARY_GAP_TICKER, "flag": "gap"})
    return asked, gaps


def library_panel(conn: sqlite3.Connection, as_of: str) -> html.Details:
    """The Bloomberg library (data/bloomberg/library.py, user decision 2026-09-21): every
    Bloomberg security a pull on `as_of` asks for, what it is for, how many trades need it
    and until when, and, flagged at the top, every need with no Bloomberg ticker to ask for
    (a future of an unverified root, 2026-09-24). It changes only when trades come in;
    nothing outside it is pulled. Collapsed by default: the summary line is the monitor,
    the table is the detail."""
    from data.bloomberg import library
    asked, gaps = library_rows(conn, as_of)
    trades = len({r["trade_id"] for r in library.needed_on(conn, as_of)})
    summary = f"{LIBRARY_TITLE} · {len(asked)} ticker(s) for {trades} trade(s) on {as_of}"
    if gaps:
        summary += f" · {len(gaps)} need(s) with no Bloomberg ticker, not asked"
    summary += " · changes only when trades come in · pulled only on request"
    if not asked and not gaps:
        body = [_kicker("No trade on file needs anything from Bloomberg on this date.")]
    else:
        kicker = ("Everything \"Pull Bloomberg now\" asks for, and nothing else. A forward curve is one "
                  "request per pair; a vol smile and an OIS curve are one ticker per point.")
        if gaps:
            kicker += (f" The {len(gaps)} flagged row(s) at the top are gaps: the book needs them but has no "
                       "verified Bloomberg ticker to ask with, so nothing is asked and the reason is under Used for.")
        body = [_kicker(kicker),
                _panel_table(LIBRARY_TABLE_ID,
                             [("Ticker", "ticker"), ("Field", "field"), ("Used for", "used_for"),
                              ("Trades", "trades"), ("Needed until", "needed_until"), ("In the library since", "added_at")],
                             gaps + asked, wide=("used_for",) if gaps else (), numeric=("trades",))]
    return html.Details(className="details", children=[html.Summary(summary), *body])


CONTRACT_DATES_TITLE = "Contract dates"
CONTRACT_DATES_TABLE_ID = "market-data-contract-dates-table"


def contract_date_rows(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """One row per open commodity future whose Bloomberg contract dates the book needs on
    `as_of`, from `data.bloomberg.inventory.contract_dates_inventory` (2026-09-24): ON_FILE
    with the stored last trade and first notice dates and their source, or MISSING with why:
    the inventory's own reason when the pull cannot ask (no verified ticker), else that the
    next "Pull Bloomberg now" asks for them. Missing rows first; nothing is estimated here."""
    from data.bloomberg.inventory import contract_dates_inventory
    df = contract_dates_inventory(conn, as_of)
    rows = []
    for r in df.to_dict("records"):
        on_file = str(r.get("status") or "") == "ON_FILE"
        reason = str(r.get("reason") or "").strip()
        why = "" if on_file else (reason or "not on file yet: the next Pull Bloomberg now asks for them")
        rows.append({"contract_id": r.get("contract_id", ""),
                     "bbg_ticker": str(r.get("bbg_ticker") or "") or "no ticker",
                     "status": "on file" if on_file else "missing",
                     "last_trade_date": r.get("last_trade_date") or "", "first_notice_date": r.get("first_notice_date") or "",
                     "source": r.get("source") or "", "trades": r.get("trades"), "needed_until": r.get("needed_until", ""),
                     "why": why, "flag": "" if on_file else "missing"})
    rows.sort(key=lambda r: (r["flag"] == "", r["contract_id"]))
    return rows


def contract_dates_panel(conn: sqlite3.Connection, as_of: str) -> html.Details:
    """Bloomberg's own last trade and first notice dates of the open commodity futures
    (2026-09-24): until they are on file a future carries the contract master's
    conservative expiry. Collapsed, like the library: the summary line counts on file and
    missing, the table lists each contract, missing first."""
    rows = contract_date_rows(conn, as_of)
    if not rows:
        return html.Details(className="details", children=[
            html.Summary(f"{CONTRACT_DATES_TITLE} · none needed on {as_of}"),
            _kicker("No open commodity future needs Bloomberg's contract dates on this date.")])
    missing = [r for r in rows if r["flag"]]
    unaskable = sum(1 for r in missing if not r["why"].startswith("not on file yet"))
    summary = f"{CONTRACT_DATES_TITLE} · {len(rows) - len(missing)} of {len(rows)} contract(s) on file on {as_of}"
    if missing:
        summary += f" · {len(missing)} missing"
        if unaskable:
            summary += f", {unaskable} with no Bloomberg ticker to ask with"
    return html.Details(className="details", children=[
        html.Summary(summary),
        _kicker("FUT_LAST_TRADE_DT and FUT_NOTICE_FIRST, asked of Bloomberg on request, once per contract. Until a "
                "contract's dates are on file its future carries the contract master's conservative expiry."),
        _panel_table(CONTRACT_DATES_TABLE_ID,
                     [("Contract", "contract_id"), ("Ticker", "bbg_ticker"), ("Status", "status"),
                      ("Last trade", "last_trade_date"), ("First notice", "first_notice_date"), ("Source", "source"),
                      ("Trades", "trades"), ("Needed until", "needed_until"), ("Why missing", "why")],
                     rows, wide=("why",), numeric=("trades",)),
    ])


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
             "left_out_of": "every figure (no price today)", "reason": r.reason, "flag": ""}
            for r in out.sort_values(["product", "instrument_id", "trade_id"]).itertuples()]
    rows += period_only_rows(conn, as_of, df)
    return len(df), rows


_PERIOD_NAMES = (("daily", "Daily"), ("d5", "5d"), ("mtd", "MTD"), ("ytd", "YTD"))


def period_only_rows(conn: sqlite3.Connection, as_of: str, df_today: pd.DataFrame) -> List[dict]:
    """Why Daily / 5d / MTD / YTD leave out MORE trades than LTD does (user, 2026-09-21): a period
    is today's value minus the value on an earlier close, so a trade priced today but with no
    price on THAT close cannot be in it. One row per such trade, naming the figures and dates."""
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.reference import diff_split
    from ui.tabs.blotter_pricing import priced_value_book
    refs = period_reference_dates(as_of)
    by_trade: Dict[str, dict] = {}
    for key, name in _PERIOD_NAMES:
        ref = refs.get(key)
        if not ref:
            continue
        df_ref = priced_value_book(conn, ref)[0]
        blocked = diff_split(df_today, df_ref).blocked_ids
        if not blocked:
            continue
        why = dict(zip(df_ref["trade_id"], df_ref["reason"])) if not df_ref.empty else {}
        for r in df_today[df_today["trade_id"].isin(blocked)].itertuples():
            row = by_trade.setdefault(r.trade_id, {
                "trade_id": r.trade_id, "product": r.product, "instrument_id": r.instrument_id,
                "trade_date": getattr(r, "trade_date", ""), "status": getattr(r, "status", ""),
                "figures": [], "reason": why.get(r.trade_id, ""), "flag": ""})
            row["figures"].append(f"{name} ({ref})")
    out = []
    for row in sorted(by_trade.values(), key=lambda x: (x["product"], x["instrument_id"], x["trade_id"])):
        row["left_out_of"] = "only " + ", ".join(row.pop("figures")) + ": priced today, no price on that close"
        out.append(row)
    return out


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
    n_today = sum(1 for r in rows if r["left_out_of"].startswith("every"))
    return _panel(f"{UNPRICED_TITLE} · {n_today} of {total} unpriced on {as_of}, {len(rows) - n_today} more in some periods", [
        _kicker(f"{n_today} trades have no price today, so EVERY headline figure leaves them out. Daily, 5d, MTD and "
                "YTD are today's value minus the value on an earlier close, so each also leaves out the trades with "
                "no price on ITS close: that is why the counts differ from one figure to the next. "
                "Most common reasons: " + top + "."),
        _panel_table(UNPRICED_TABLE_ID,
                     [("Trade id", "trade_id"), ("Product", "product"), ("Instrument", "instrument_id"),
                      ("Trade date", "trade_date"), ("Status", "status"), ("Left out of", "left_out_of"),
                      ("Why", "reason")],
                     rows, wide=("reason", "left_out_of")),
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
                      safe_panel(LIBRARY_TITLE, lambda: library_panel(conn, as_of)),
                      safe_panel(CONTRACT_DATES_TITLE, lambda: contract_dates_panel(conn, as_of))]),
            safe_panel(SUSPECT_TITLE, lambda: suspect_panel(conn, as_of)),
            safe_panel(PAST_CLOSES_TITLE, lambda: past_closes_panel(conn, header_day)))


def manual_entry_form(default_pair: Optional[str] = None) -> html.Div:
    """Manual mark entry calling `data.bloomberg.manual.write_manual_mark`, pre-filled
    with the pair currently selected at the top of the tab."""
    return html.Div(className="market-data-manual-entry", children=[
        html.H4("Manual mark entry"),
        html.P("A MANUAL mark is never official: it is kept on file and shown on this tab as \"on file "
               "instead\", but no valuation uses it -- only an official mark prices a trade."),
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


def pull_now_outcome(app, guard: PullGuard, get_db_path: Callable[[], object]) -> Tuple[str, str]:
    """One click of this tab's "Pull now": the SAME action as the top bar's "Pull Bloomberg
    now" (user, 2026-09-22: "the pull bloomberg now on the top right corner should trigger
    this, not from the market data page" -- until then this button called `pull_once` on
    its own, so its press pulled today's marks but ran neither the backfill nor the marks
    snapshot). It asks the feed for one cycle (`ui.feed_controls.click_outcome`:
    `LiveFeed.trigger_now`, then `pull_once`, the backfill and `snapshot.save_after_pull`
    in the feed's thread) and returns its status line and a fresh revision; the tab's own
    status poll shows the outcome as it lands. With no feed on this machine, the
    not-connected message, and nothing is asked of Bloomberg."""
    import time
    from data.bloomberg.live import read_status
    text, _pending = click_outcome(app, guard, read_status(get_db_path()))
    return text, str(time.time())


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

    guard = PullGuard()

    @app.callback(
        Output(PULL_NOW_STATUS_ID, "children"),
        Output(PULL_REVISION_ID, "data"),
        Input(PULL_NOW_ID, "n_clicks"),
        prevent_initial_call=True,
    )
    def _pull_now(n_clicks):
        return pull_now_outcome(app, guard, get_db_path)

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
