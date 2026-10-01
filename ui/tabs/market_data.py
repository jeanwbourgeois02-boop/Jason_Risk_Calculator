"""Data tab (the Market data tab until 2026-09-25): "Can I trust today's numbers?"

Rebuilt 2026-09-29 (user: the layout approved piece by piece, each part put through four tests:
it answers a real question, it changes a decision, it is not derivable from another figure on
screen, it is trustworthy). Top to bottom (`build_layout`, filled by `render`):

  1. One status line: the last Bloomberg pull, marks complete ("43 of 47 marks"), reference
     closes complete, contract dates; each part a link to its block (`status_line`).
  2. Missing, and what it blocks (`missing_block`): each official mark the book needs that is not
     on file, the trades it leaves unpriced and the trades reading it (names on hover), their face
     notional, the reason on hover; then the trades left out of the P&L for another reason. One
     quiet line when nothing is missing.
  3. Marks check (`marks_check_rows`): every official mark the book uses on the date, futures,
     options, LME and FX together, its source and snap time, the previous close and the change,
     flagged by the "marks that look wrong" rule (`suspect_rows`); a filter by commodity / sector,
     a search box and Download CSV, in their own callbacks over a store of the rows.
  4. Reference closes (`reference_close_rows`): Daily, 5d, MTD, YTD, each close's marks needed
     and present, the backfill's reason when incomplete, the trades that period leaves out.
  5. Contract dates (`contract_dates_line`): one line, the list on click.
  6. Bloomberg (a card, `BBG_CARD_ID`, 2026-09-30, user: "all in one place"): the last pull in one
     line, "Pull problems (N)" (the one place in the app a pull or backfill error is written out),
     "Run Bloomberg check" (a background run, `ui/diagnostics_runner.py`: the connection and data
     checks, then the book's own tickers asked of Bloomberg) and its results, and "Details" folded
     (the Bloomberg library, the feed status step by step, everything the last pull recorded).
  7. Trades (a card, `TRADES_CARD_ID`, 2026-09-30): the last upload in one line, "Trade problems (N)"
     (every contract not recognised, row not loaded and warning, `data_checks.trade_problem_rows`:
     the one place the trades' problems are written out), then "Check a blotter file", which reads a
     file as an upload would (`data.ingest.parse_check.check_file`) and lists every row; nothing is saved.
Then the one Data issues drawer.

Removed on 2026-09-29: the tab's own Pull now (the top bar's Pull Bloomberg now is the one
action), the rows of the blotter file that did not become trades (the Trades tab's; their reader
`upload_issue_rows` / `upload_issues_panel` stays here for it), the commodity strip, picker, chart
and per-commodity table, the FX pair section, the completeness squares, and manual mark entry (a
MANUAL mark is never official, so nothing read it; `data/bloomberg/manual.py` is untouched).

No calculation happens here beyond display: every value is read from `marks_official` /
`trades` / `trade_legs` / `instruments`, the status file, `data.bloomberg.inventory` /
`library`, or the shared pricing reader. Nothing is asked of Bloomberg from a render (hard rule 8).
Engine and data imports stay lazy so `import ui.tabs.market_data` always succeeds.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
from ui.tabs import ranking as rk
from dash import Input, Output, State, dash_table, dcc, html

from ui.feed_controls import pull_timings, recalc_words, safety_refresh_ms, seconds_words
from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID
from ui.tabs import data_checks
from ui.tabs import data_kit as kit
from ui.tabs.formatting import cap, compact, plain_ids, tidy
from ui.tabs.formatting import (MISSING, about, contract_name, fx_name, is_fx_pair, issues_drawer, lme_name,
                                missing_cell, parse_contract_id, plain_words, quoted_unit, short_date,
                                short_root_name)

BBG_CHECK_BUTTON_ID = "market-data-bbg-check-button"   # "Run Bloomberg check" (2026-09-30: a background run)
BBG_RESULTS_ID = "market-data-bbg-check-results"
BBG_CARD_ID = "market-data-bloomberg"      # the Bloomberg card: another tab links here (tab key "market-data")
BBG_LINE_ID = "market-data-bloomberg-line"
BBG_PULL_PROBLEMS_ID = "market-data-pull-problems"
BBG_PROGRESS_ID = "market-data-bbg-check-progress"
BBG_POLL_ID = "market-data-bbg-check-poll"
BBG_RUN_STORE_ID = "market-data-bbg-check-run"
BBG_FSTORE_ID = "market-data-bbg-check-fstore"
BBG_SORT_ID = "market-data-bbg-check-sort"
BBG_CSV_ID = "market-data-bbg-check-csv"
BBG_DOWNLOAD_ID = "market-data-bbg-check-download"
BBG_SUMMARY_ID = "market-data-bbg-check-summary"      # the last check's summary line, above its fixes
BBG_DONE_ID = "market-data-bbg-check-done"            # "running" / the finished stamp: redraws the fixes once
BBG_REPORT_ID = "market-data-bbg-report"              # "Download report" (the check's report text)
BBG_REPORT_DOWNLOAD_ID = "market-data-bbg-report-download"
# The check's suggested fixes applied from the card (2026-09-30, user: "i should be able to do
# this in the app"): a tick per fix, a pick where Bloomberg offers several, a dry run, a confirm.
BBG_FIXES_ID = "market-data-bbg-fixes"
BBG_FIX_RESULT_ID = "market-data-bbg-fix-result"
BBG_FIX_APPLY_ID = "market-data-bbg-fix-apply"
BBG_FIX_CONFIRM_ID = "market-data-bbg-fix-confirm"
BBG_FIX_CANCEL_ID = "market-data-bbg-fix-cancel"
BBG_FIX_STORE_ID = "market-data-bbg-fix-token"
BBG_FIX_POLL_ID = "market-data-bbg-fix-poll"          # on while the commit and push run
FIX_TICK_TYPE = "md-fix-tick"
FIX_PICK_TYPE = "md-fix-pick"
BBG_SORT_TYPE = "data-tick-sort"
BBG_COL_TYPE = "md-tick-col"
BBG_POLL_MS = 1000
PARSE_CARD_ID = "market-data-parsing"            # retired 2026-09-30: the check sits in the Trades card
TRADES_CARD_ID = "market-data-trades"            # the Trades card: another tab links here (tab key "market-data")
TRADES_HEAD_ID = "market-data-trades-head"       # the last upload's line and "Trade problems (N)"
TRADES_SLOT_ID = "market-data-trades-slot"
TRADES_STORE_ID = "market-data-trades-rows"
TRADES_FSTORE_ID = "market-data-trades-fstore"
TRADES_SORT_ID = "market-data-trades-sort"
TRADES_CSV_ID = "market-data-trades-csv"
TRADES_DOWNLOAD_ID = "market-data-trades-download"
TRADES_SORT_TYPE = "data-trade-sort"
TRADES_COL_TYPE = "md-trade-col"
DUPES_ID = "market-data-duplicates"              # the duplicates check under the Blotter card's rows (2026-09-30)
BBG_ASKED_ID = "market-data-bbg-asked"           # the fold: what the last pull asked, step by step
BBG_CHECK_FOLD_ID = "market-data-bbg-check-fold"   # the Bloomberg check and its fixes, folded
BBG_CHECK_META_ID = "market-data-bbg-check-meta"
DIAG_CARD_ID = "market-data-diagnosis"           # the Diagnosis card: one report to paste into Claude Code
DIAG_BUILD_ID = "market-data-diagnosis-build"
DIAG_PROGRESS_ID = "market-data-diagnosis-progress"
DIAG_TEXT_ID = "market-data-diagnosis-text"
DIAG_COPY_ID = "market-data-diagnosis-copy"
DIAG_SAVE_ID = "market-data-diagnosis-save"
DIAG_DOWNLOAD_ID = "market-data-diagnosis-download"
DIAG_TOOLS_ID = "market-data-diagnosis-tools"
PARSE_UPLOAD_ID = "market-data-parse-upload"   # never "report-file": the real upload's id
PARSE_STORE_ID = "market-data-parse-result"
PARSE_RESULTS_ID = "market-data-parse-results"
PARSE_FSTORE_ID = "market-data-parse-fstore"
PARSE_SORT_ID = "market-data-parse-sort"
PARSE_CSV_ID = "market-data-parse-csv"
PARSE_DOWNLOAD_ID = "market-data-parse-download"
PARSE_CLEAR_ID = "market-data-parse-clear"
PARSE_SORT_TYPE = "data-parse-sort"
PARSE_COL_TYPE = "md-parse-col"

BODY_ID = "market-data-body"
REFRESH_ID = "market-data-refresh"
PULL_TIMINGS_ID = "market-data-pull-timings"
RECALC_BLOCK_ID = "market-data-recalc"          # what a press did on a machine with no Bloomberg (2026-09-22)
RECALC_DAYS_ID = "market-data-recalc-days"
LEDGER_BLOCK_ID = "market-data-ledger"          # what the ledger's re-freeze did (2026-09-22)
CONTRACT_DATES_BLOCK_ID = "market-data-contract-dates-status"   # the pull's contract-dates block (2026-09-24)
CONTRACT_DATES_FAILED_ID = "market-data-contract-dates-failed"
NOT_REQUESTABLE_ID = "market-data-not-requestable"              # futures never asked for (2026-09-24)
CURVES_BLOCK_ID = "market-data-curves-status"                   # the pull's OIS curve step (2026-09-24)
LME_BLOCK_ID = "market-data-lme-status"                         # the pull's LME step (Phase 5)
LME_MISSING_ID = "market-data-lme-missing"
FUTURES_OPTIONS_LINE_ID = "market-data-futures-options-status"  # options on futures priced (Phase 5)
BACKFILL_OPTIONS_ID = "market-data-backfill-options"            # the backfill's per-day option count
# Safety-net timer only: one feed cycle, read from data.bloomberg.live.INTERVAL_SECONDS
# (ui.feed_controls.safety_refresh_ms), never a number typed in here. A data change
# redraws the tab within seconds through ui/revision.py's DATA_REVISION_ID.
REFRESH_MS = safety_refresh_ms()

# The blocks of the tab (2026-09-29; the ids of 2026-09-21 kept where the block lives on).
MISSING_PANEL_ID = "market-data-missing-panel"
MISSING_TABLE_ID = "market-data-missing-table"
PAST_CLOSES_PANEL_ID = "market-data-past-closes-panel"
PAST_CLOSES_TABLE_ID = "market-data-past-closes-table"
MISSING_TITLE = "Missing, and what it blocks"
SUSPECT_TITLE = "Marks check"
PAST_CLOSES_TITLE = "Reference closes"
# "Marks that look wrong": one rule for every instrument, stated in the panel's caption.
# A day's move above this is flagged. 2.5 % is loose for a G10 pair and tight for TRY, and
# is meant to be: a flag asks for a look, it does not say the mark is wrong.
BAD_TICK_PCT = 2.5
PANEL_PAGE_SIZE = 15   # rows per page in the two exception tables, so a bad day stays a bounded height

# The tab's one "Data issues (N)" drawer, gathering every reason given on hover.
ISSUES_ID = "market-data-issues"

_MONO = {"fontFamily": "Consolas, 'Courier New', monospace", "fontSize": "12px", "padding": "3px 8px",
         "textAlign": "left", "whiteSpace": "nowrap"}
_HEAD = {"fontWeight": "700"}  # the navy header of the one kit (ui/assets/style.css, look pass 2026-09-29)

_SOURCE_LABELS = {"BNP_BVAL": "BNP file", "BBG_BFXFORWARD": "Bloomberg", "BBG_BDH": "Bloomberg (BDH)",
                  "BBG_INTERP": "Interpolated", "MANUAL": "Manual"}


def source_label(source: Optional[str]) -> str:
    if not source:
        return ""
    return _SOURCE_LABELS.get(source, source)


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
# instead of typed in here. Re-exported under the old name (the Ladder tab read it here
# until it left on 2026-09-28; tests and older notes still may).
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
    tab writes money elsewhere (`notional_words`). An em dash for anything that is not a
    number: a blank is never shown as zero."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return MISSING
    if v != v:  # NaN
        return MISSING
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


def _lme_missing_line(entry, with_root: bool = True) -> str:
    """"LME:CA LMCADS03 Comdty 2026-12-24: reason" for one `status["lme"]["missing"]` item; an
    open prompt (ticker '') reads "open prompt". `with_root=False` leaves the metal off."""
    if not isinstance(entry, dict):
        return str(entry)
    what = str(entry.get("ticker") or "").strip() or "open prompt"
    root = str(entry.get("root_id") or "?") if with_root else ""
    head = " ".join(b for b in (root, what, str(entry.get("settle_date") or "")) if b)
    return f"{head}: {str(entry.get('reason') or '').strip() or 'no reason given'}"


def lme_block(status: Optional[dict]) -> Optional[html.Div]:
    """The pull's LME step (Phase 5, 2026-09-24, bbg-live's `status["lme"]`: {roots, written,
    interp_written, missing: [{root_id, ticker, settle_date, reason}], reasons: [str],
    summary, error?}): the pull's own sentence, its error in red, and the pillars or open
    prompts left without a mark in a collapsed list with each reason, then the step's other
    reasons. Read from the status file only. None when the key is absent (no terminal) or the
    block says nothing (the book has no LME forward)."""
    block = (status or {}).get("lme")
    if not isinstance(block, dict):
        return None
    summary = block.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    missing = block.get("missing")
    missing = [_lme_missing_line(e) for e in missing] if isinstance(missing, (list, tuple)) else []
    reasons = block.get("reasons")
    reasons = [str(r) for r in reasons if str(r).strip()] if isinstance(reasons, (list, tuple)) else []
    error = str(block.get("error") or "").strip()
    if not (summary or missing or error):
        return None
    children: list = []
    if summary:
        children.append(html.Div(summary if summary.startswith("LME") else f"LME curves: {summary}",
                                 className="status-line"))
    if error and error not in summary:
        children.append(html.Div(f"LME curves: {error}", className="status-line status-line--bad"))
    if missing or reasons:
        children.append(html.Details(className="status-line", children=[
            html.Summary(f"LME pillars or prompts without a mark: {len(missing)}"),
            html.Ul([html.Li(line) for line in missing] + [html.Li(line) for line in reasons],
                    id=LME_MISSING_ID, style={"margin": "2px 0 0 16px", "padding": 0}),
        ]))
    return html.Div(children, id=LME_BLOCK_ID, className="status-line")


def futures_options_block(status: Optional[dict]) -> Optional[html.Div]:
    """The options step's line for the options on commodity futures (Phase 5,
    `status["options"]["futures_options_summary"]`, "N options on futures priced"; the
    count `futures_options_priced` when the sentence is missing). None when neither says
    anything: an older status file, or no option on a future priced."""
    options = (status or {}).get("options")
    if not isinstance(options, dict):
        return None
    sentence = options.get("futures_options_summary")
    sentence = sentence.strip() if isinstance(sentence, str) else ""
    if not sentence:
        n = options.get("futures_options_priced")
        n = 0 if isinstance(n, bool) else _count(n)
        sentence = f"{n} option{'s' if n != 1 else ''} on futures priced" if n else ""
    if not sentence:
        return None
    return html.Div(f"Options on futures: {sentence}", id=FUTURES_OPTIONS_LINE_ID, className="status-line")


def backfill_option_rows(status: Optional[dict]) -> List[dict]:
    """One row per day of `status["backfill"]["options"]` (what the backfill's past-close
    option pricing made of each worked day, newest first): {"day", "priced" (None when the
    step did not run), "futures" (the options on futures among them, None when the file does
    not say), "skipped", "closed_out", "note"}. Read defensively; the counts are the pricer's."""
    backfill = (status or {}).get("backfill")
    days = backfill.get("options") if isinstance(backfill, dict) else None
    if not isinstance(days, dict):
        return []
    rows = []
    for day, entry in sorted(days.items(), reverse=True):
        if not isinstance(entry, dict):
            continue
        priced, futures = entry.get("priced"), entry.get("futures_options_priced")
        skipped = entry.get("skipped")
        rows.append({"day": str(day),
                     "priced": None if priced is None or isinstance(priced, bool) else _count(priced),
                     "futures": None if futures is None or isinstance(futures, bool) else _count(futures),
                     "skipped": len(skipped) if isinstance(skipped, (list, tuple)) else _count(skipped),
                     "closed_out": closed_out_count(entry), "note": str(entry.get("note") or "").strip()})
    return rows


def backfill_options_block(status: Optional[dict]) -> Optional[html.Details]:
    """The backfill's per-day option count, collapsed: "2026-09-18: 4 options priced (1 on
    futures), 0 skipped". A day whose step did not run says why (its note). None when the
    status file has no such block."""
    rows = backfill_option_rows(status)
    if not rows:
        return None
    items = []
    for r in rows:
        if r["priced"] is None:
            text = f"{r['day']}: not priced" + (f" ({r['note']})" if r["note"] else " (no reason recorded)")
        else:
            text = f"{r['day']}: {r['priced']} option{'s' if r['priced'] != 1 else ''} priced"
            if r["futures"]:
                text += f" ({r['futures']} on futures)"
            text += f", {r['skipped']} skipped"
            closed = closed_out_words(r["closed_out"])
            if closed:
                text += f", {closed}"
            if r["note"]:
                text += f" · {r['note']}"
        items.append(html.Li(text))
    return html.Details(id=BACKFILL_OPTIONS_ID, className="status-line", children=[
        html.Summary(f"Past-close option pricing: {len(rows)} day(s)"),
        html.Ul(items, style={"margin": "2px 0 0 16px", "padding": 0}),
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
    lme = lme_block(status)
    futures_options = futures_options_block(status)
    backfill_options = backfill_options_block(status)
    line = top_bar_status(status, say_recalc=recalc is None)
    timings = pull_timings_line(status)
    extras = [block for block in (dates, unasked, curves, lme, futures_options, recalc, ledger, backfill_options)
              if block is not None]
    if not timings and not extras:
        return line
    parts: list = [line]
    if timings:
        parts.append(html.Div(timings, id=PULL_TIMINGS_ID, className="status-line"))
    return parts + extras


# --------------------------------------------------------------------------- whole-book checks (2026-09-21)
# "What is missing", "Marks that look wrong" and "Past closes the header needs". All three
# read stored rows and the Bloomberg status file only: no Bloomberg session, nothing
# written, no P&L or delta computed. Each is a handful of queries for the whole book.
MarkKey = Tuple[str, str, str]   # (instrument_id, mark_type, settle_date)

_OPEN_LEGS_SQL = """
SELECT t.trade_id, t.product, t.instrument_id, i.asset_class, i.base_ccy, i.quote_ccy, l.ccy, l.amount, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE (i.asset_class IN ('FX', 'FUTURE') OR t.product = 'LME_FWD') AND t.trade_date <= :as_of AND l.settle_date >= :as_of
"""

_OPEN_OPTIONS_SQL = """
SELECT t.trade_id, i.base_ccy, i.quote_ccy, i.expiry_date, t.quantity
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND i.expiry_date >= :as_of
"""

# A listed option (the generic listed path, product EQ_OPTION; CMDTY_OPTION for Phase 5) reads
# Bloomberg's own price of the option (FUTURE_PX at its expiry) and, when not in USD, the SPOT of
# its currency's USD pair: the two rows the library lists for it (library.compute).
_OPEN_LISTED_OPTIONS_SQL = """
SELECT t.trade_id, t.instrument_id, i.quote_ccy, i.expiry_date
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product IN ('EQ_OPTION', 'CMDTY_OPTION') AND t.trade_date <= :as_of AND i.expiry_date >= :as_of
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
SELECT m.as_of_date, m.instrument_id, m.mark_type, m.settle_date, m.value, m.snapped_at, m.source
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
      - an FX option reads its pair's SPOT, the forward at its expiry and the same USD pairs;
      - a listed option reads its own FUTURE_PX at expiry and, when not in USD, its currency's
        USD-pair SPOT (counted, no notional: its face is contracts), and an option on a future
        its underlying future's FUTURE_PX (the library's role UNDERLYING rows);
      - an LME forward reads its metal's cash SPOT, the FWD_OUTRIGHT at its prompt and the
        day's LME curve (an inventory item, mark_type 'LME_CURVE'), all on the root id.
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

    for trade_id, product, instrument_id, asset_class, base, quote, ccy, amount, settle in conn.execute(
            _OPEN_LEGS_SQL, {"as_of": as_of}):
        if product in _LME_PRODUCTS:
            # an LME forward (Phase 5) reads the metal's cash price, its outright at its own prompt
            # and the day's LME curve, all keyed on the root id; its notional is the USD leg
            shown_ccy, shown = ("USD", _abs_amount(amount)) if ccy == "USD" else ("", 0.0)
            for key in ((instrument_id, "SPOT", as_of), (instrument_id, "FWD_OUTRIGHT", settle),
                        (instrument_id, "LME_CURVE", as_of)):
                add(key, trade_id, shown_ccy, shown)
            continue
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
    for trade_id, instrument_id, quote, expiry in conn.execute(_OPEN_LISTED_OPTIONS_SQL, {"as_of": as_of}):
        # counted, with no notional: a listed option's face is contracts, not an amount of money
        add((instrument_id, "FUTURE_PX", expiry), trade_id, "", 0.0)
        if quote and quote != "USD":
            add((_usd_pair_name(quote), "SPOT", as_of), trade_id, "", 0.0)
    # an option on a future also reads its underlying future's price (its Greeks; Phase 5): the
    # library's role UNDERLYING rows name which future, the one rule for it (library.compute)
    from data.bloomberg import library
    for r in library.needed_on(conn, as_of, include_unrequestable=True):
        if r.get("role") == getattr(library, "ROLE_UNDERLYING", "UNDERLYING") and r["kind"] == "FUTURE_PX":
            add((r["key"], "FUTURE_PX", r["settle_date"]), r["trade_id"], "", 0.0)
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


def lme_missing_reasons(status: Optional[dict], as_of: str) -> Dict[str, List[str]]:
    """{root id: ["LMCADS03 Comdty 2026-12-24: reason", ...]} from the last pull's
    `status["lme"]["missing"]`, only when that pull was for `as_of` (as `last_pull_reasons`)."""
    if _pull_date(status) != as_of:
        return {}
    block = status.get("lme") if isinstance(status, dict) else None
    items = block.get("missing") if isinstance(block, dict) else None
    out: Dict[str, List[str]] = {}
    for item in items if isinstance(items, (list, tuple)) else []:
        if isinstance(item, dict) and item.get("root_id"):
            out.setdefault(str(item["root_id"]), []).append(_lme_missing_line(item, with_root=False))
    return out


def lme_curve_gap_words(conn: sqlite3.Connection, root_id: str, as_of: str, detail: Optional[str] = None) -> str:
    """What an incomplete LME curve lacks: the close's own `detail` ("cash, 3M not on file")
    when it has one, else `inventory.lme_curve_status` read now ("3M not on file")."""
    if detail:
        return str(detail)
    try:
        from data.bloomberg.inventory import lme_curve_status
        state = lme_curve_status(conn, as_of, root_id, today=max(as_of, _book_today_iso()))
    except Exception:  # noqa: BLE001 -- say it cannot be told rather than take the panel down
        return "curve not complete (which pillar could not be read)"
    return (", ".join(state["missing"]) + " not on file") if state.get("missing") else "curve not complete"


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
    lme_reasons = lme_missing_reasons(status, as_of)
    for m in missing:
        key = (m["instrument_id"], m["mark_type"], m["settle_date"])
        entry = blocked.get(key, {"trades": set(), "notional": {}})
        instead = on_file.get(key)
        on_file_words = ("nothing" if instead is None else
                         f"{source_label(instead[1]).lower()} {_fmt_mark(instead[0])} (not official)")
        reason = reasons.get(key, "not requested by the last pull" if asked else "")
        if key[1] == "LME_CURVE":
            # one item per metal (Phase 5): complete when its cash and 3M are official
            on_file_words = lme_curve_gap_words(conn, key[0], as_of, m.get("detail"))
            reason = "; ".join(lme_reasons.get(key[0], [])) or reason
        rows.append({
            "instrument_id": key[0], "mark_type": key[1], "settle_date": key[2],
            "on_file": on_file_words,
            "trades_blocked": len(entry["trades"]),
            "notional_blocked": notional_words(entry["notional"]),
            "reason": reason,
            "trade_ids": sorted(entry["trades"]), "notional": dict(entry["notional"]),
        })
    rows.sort(key=lambda r: (-r["trades_blocked"], r["instrument_id"], r["mark_type"], r["settle_date"]))
    return needed, rows


def _panel(title: str, children: list, panel_id: Optional[str] = None, about_text: Optional[str] = None) -> html.Div:
    """A section card; its definitions (`about_text`) sit on hover of the title, never as a
    paragraph above the table (screens redesign, 2026-09-25)."""
    kwargs = {"id": panel_id} if panel_id else {}
    return html.Div(className="section", children=[about(title, about_text, level="h4", style={"marginTop": "0"}),
                                                   *children], **kwargs)


def _quiet(title: str, sentence, about_text: Optional[str] = None, extra: Optional[list] = None) -> html.Div:
    """A section with nothing to report, as one quiet line: "<title> · <sentence>", the
    definitions on hover of the title; `extra` (a collapsed detail) may follow on the line
    below. Never a card with a paragraph (screens redesign, 2026-09-25)."""
    return html.Div(className="status-line quiet-line", style={"margin": "2px 0 6px"}, children=[
        about(title, about_text, level="span", style={"fontWeight": "600"}), " · ", sentence, *(extra or [])])


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
            {"if": {"filter_query": "{flag} != ''"}, "backgroundColor": "#fdecea"},
            {"if": {"filter_query": "{flag} != ''", "column_id": "flag"}, "color": "#c0392b", "fontWeight": "600"},
        ])


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
    for mark_date, instrument_id, mark_type, settle, value, snapped_at, source in conn.execute(
            _SUSPECT_MARKS_SQL, {"as_of": as_of, "prev": prev_day}):
        if mark_date == as_of:
            todays.append((instrument_id, mark_type, settle, value, snapped_at, source))
        else:
            previous[(instrument_id, mark_type, "" if mark_type == "SPOT" else settle)] = value

    rows = []
    for instrument_id, mark_type, settle, value, snapped_at, source in todays:
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
            "snapped_at": str(snapped_at or "").replace("T", " "), "source": str(source or ""),
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
    ("Change", "change_pct"), ("Marked at", "snapped_at"), ("Flag", "flag"), ("Note", "note"),
]


def _price_units(conn: sqlite3.Connection, instrument_ids) -> Dict[str, str]:
    """instrument id -> the unit its price is read in by `formatting.price_text`: an FX pair
    itself (a JPY cross 3 decimals, gold 2, else 4), otherwise its contract root's quoted
    unit; '' when neither is known."""
    ids = set(instrument_ids)
    if not ids:
        return {}
    try:
        from data.contracts import load_roots
        roots = dict(load_roots())
    except Exception:  # noqa: BLE001 -- a price with no unit still prints (at four decimals)
        roots = {}
    bases: Dict[str, str] = {}
    try:
        for iid, base in conn.execute("SELECT instrument_id, base_ccy FROM instruments"):
            bases[str(iid)] = str(base or "")
    except sqlite3.Error:
        bases = {}
    return {iid: (iid if is_fx_pair(iid) else quoted_unit(roots.get(bases.get(iid, "")))) for iid in ids}


def _suspect_display(conn: sqlite3.Connection, rows: List[dict]) -> List[dict]:
    """The "Marks that look wrong" rows as shown: Value and Previous at tick precision
    (`price_text`, text), never eight decimals; a blank stays blank. The numbers stay in
    `suspect_rows` for anyone who reads them."""
    units = _price_units(conn, {r["instrument_id"] for r in rows})
    out = []
    for r in rows:
        unit = units.get(r["instrument_id"], "")
        rec = dict(r)
        for col in ("value", "previous"):
            v = r.get(col)
            rec[col] = "" if v is None else data_checks.mark_price_text(v, unit)   # the unit's tick, an FX pair its own
        out.append(rec)
    return out


def suspect_about(as_of: str, prev_day: str) -> str:
    """The "Marks that look wrong" definitions, on hover of its title."""
    from data.bloomberg.live import STALE_AFTER_SECONDS
    return (f"Every official SPOT on {as_of}, and every official forward and futures price an open leg reads, "
            f"against the same mark on {prev_day}, the previous business day. Flagged: a move above "
            f"{BAD_TICK_PCT:g} %, a value exactly unchanged from the previous close (stale or copied), or, on "
            f"today's date, a snap older than {STALE_AFTER_SECONDS // 60} minutes. A comparison of two stored "
            "marks: nothing is recomputed.")


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


def missing_close_words(missing: List[dict], day: str, backfill: Optional[dict]) -> str:
    """Why a reference close is incomplete: the prices with no close that day by name, then what
    the backfill says of the day when it says more than its default (checker C, 2026-09-29: a close
    on file with two prices that never existed read "none has reached this date yet")."""
    from ui.tabs.header import past_close_explanation
    words = {"FUTURE_PX": "price", "SPOT": "spot", "FWD_OUTRIGHT": "forward"}
    names = []
    for m in missing:
        iid, mt = str(m.get("instrument_id") or ""), str(m.get("mark_type") or "")
        what = words.get(mt, plain_words(mt).lower())
        if mt == "FWD_OUTRIGHT" and m.get("settle_date"):
            what += f" for {short_date(str(m.get('settle_date')))}"
        names.append(f"{plain_ids(iid)} {what}".strip())
    head = (f"{len(names)} price{'s' if len(names) != 1 else ''} with no close on {short_date(day)}: "
            + ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else ""))
    tail = past_close_explanation(backfill, day)
    generic = "none has reached this date yet" in tail
    return head if generic else f"{head}; {tail}"


def past_close_rows(conn: sqlite3.Connection, header_as_of: str, backfill: Optional[dict] = None) -> List[dict]:
    """One row per date of `data.bloomberg.backfill.reference_dates(header_as_of)`, newest
    first: which figures read it, needed / present / complete, and when it is incomplete the
    header's own sentence (`ui.tabs.header.past_close_explanation`). The counts come from
    `ui.tabs.header.needed_marks`, which for a past date is `close_completeness(day, day)`,
    the past-close needs list the backfill fills; the header's sentence counts the same way."""
    from data.bloomberg.backfill import reference_dates
    from ui.tabs.header import backfill_status, needed_marks
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
            state, why, flag = f"incomplete: {len(missing)} missing", missing_close_words(missing, day, backfill), "incomplete"
        else:
            state, why, flag = "complete", "", ""
        rows.append({"date": day, "read_by": ", ".join(labels.get(day, [])), "needed": needed,
                     "present": needed - len(missing), "state": state, "why": why, "flag": flag})
    return rows


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
            asked.append({**r, "flag": "", "used_for": cap(plain_ids(r.get("used_for")))})
        else:
            gaps.append({**r, "ticker": LIBRARY_GAP_TICKER, "flag": "gap", "used_for": cap(plain_ids(r.get("used_for")))})
    return asked, gaps


LIBRARY_KINDS_TABLE_ID = "market-data-library-kinds-table"
_LME_PRODUCTS = ("LME_FWD",)
_OPTION_PRODUCTS = ("EQ_OPTION", "CMDTY_OPTION")


def library_need_words(row: dict) -> str:
    """What a library row is for, in plain words, from its kind, role and product (the
    library's own fields; Phase 5 adds LME_CURVE and the role UNDERLYING)."""
    kind, role, product = row.get("kind"), row.get("role"), row.get("product")
    if kind == "LME_CURVE":
        return "LME curve (cash, 3M, monthlies)"
    if kind == "SPOT":
        if product in _LME_PRODUCTS:
            return "LME cash price"
        return "USD conversion spot" if role == "CONVERSION" else "FX spot"
    if kind == "FWD_OUTRIGHT":
        return "LME prompt outright (read off the LME curve)" if product in _LME_PRODUCTS else "FX forward outright"
    if kind == "FUTURE_PX":
        if role == "UNDERLYING":
            return "option's underlying future"
        return "option price" if product in _OPTION_PRODUCTS else "futures price"
    if kind == "CONTRACT_DATES":
        return "option expiry date" if product in _OPTION_PRODUCTS else "contract dates (last trade, first notice)"
    if kind == "OIS_CURVE":
        return "OIS discount curve (option Greeks)"
    if kind == "VOL_SMILE":
        return "FX vol smile"
    return str(kind or "?")


def library_kind_rows(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """The library's needs in force on `as_of` counted by what they are for: [{what, items
    (distinct keys), trades, gaps (items with no ticker to ask with)}], largest first. Every
    row the library lists, requestable or not."""
    from data.bloomberg import library
    groups: Dict[str, dict] = {}
    for r in library.needed_on(conn, as_of, include_unrequestable=True):
        g = groups.setdefault(library_need_words(r), {"items": set(), "trades": set(), "gaps": set()})
        item = (r.get("key"), r.get("settle_date"))
        g["items"].add(item)
        g["trades"].add(r.get("trade_id"))
        if not r.get("requestable", True):
            g["gaps"].add(item)
    rows = [{"what": cap(what), "items": len(g["items"]), "trades": len(g["trades"]), "gaps": len(g["gaps"]),
             "flag": "gap" if g["gaps"] else ""} for what, g in groups.items()]
    return sorted(rows, key=lambda r: (-r["items"], r["what"]))


def _library_shown(row: dict) -> dict:
    """A library row as the table shows it: dates short ("31 Dec 2026"), the time it came in as New
    York time without seconds (layout wave 2, 2026-09-29: never an ISO stamp on screen)."""
    from ui.feed_controls import short_time
    until = str(row.get("needed_until") or "")
    added = str(row.get("added_at") or "")
    return {**row, "needed_until": (short_date(until) + f" {until[:4]}") if until[:4].isdigit() else until,
            "added_at": short_time(added) if added else ""}


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
    summary = (f"{len(asked)} ticker{'' if len(asked) == 1 else 's'} for {trades} "
               f"trade{'' if trades == 1 else 's'}")
    if gaps:
        summary += f" · {len(gaps)} with no ticker"
    about_text = (f"Everything \"Pull Bloomberg now\" asks for on {short_date(as_of)}, and nothing else. It changes "
                  "only when trades come in, and is pulled only on request. A forward curve is one "
                  "request per pair; a vol smile and an OIS curve are one ticker per point.")
    if gaps:
        about_text += (f" The {len(gaps)} flagged row(s) at the top are gaps: the book needs them but has no "
                       "verified Bloomberg ticker to ask with, so nothing is asked and the reason is under Used for.")
    if not asked and not gaps:
        # nothing needed: one title line, not a collapsible with nothing in it
        return about(f"{LIBRARY_TITLE} · nothing needed", f"No trade on file needs anything from Bloomberg on "
                                                          f"{short_date(as_of)}. {about_text}", level="h5")
    body = [_panel_table(LIBRARY_KINDS_TABLE_ID,
                         [("What it is for", "what"), ("Items", "items"), ("Trades", "trades"),
                          ("With no ticker", "gaps")],
                         library_kind_rows(conn, as_of), numeric=("items", "trades", "gaps")),
            _panel_table(LIBRARY_TABLE_ID,
                         [("Ticker", "ticker"), ("Field", "field"), ("Used for", "used_for"),
                          ("Trades", "trades"), ("Needed until", "needed_until"), ("In the library since", "added_at")],
                         [_library_shown(r) for r in gaps + asked], wide=("used_for",) if gaps else (),
                         numeric=("trades",))]
    return html.Details(className="book-fold tk-fold-block data-fold", children=[
        html.Summary([html.Span(LIBRARY_TITLE, className="book-section-title", title=about_text),
                      html.Span(f" · {summary}", className="book-section-meta")]), *body])


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
    about_text = ("Bloomberg's last-trade and first-notice dates, asked of Bloomberg on request, once per contract. Until a "
                  "contract's dates are on file its future carries the contract master's conservative expiry.")
    if not rows:
        return _quiet(CONTRACT_DATES_TITLE, f"none needed on {as_of}: no open commodity future needs Bloomberg's "
                                            "contract dates on this date", about_text)
    missing = [r for r in rows if r["flag"]]
    unaskable = sum(1 for r in missing if not r["why"].startswith("not on file yet"))
    summary = f"{CONTRACT_DATES_TITLE} · {len(rows) - len(missing)} of {len(rows)} contract(s) on file on {as_of}"
    if missing:
        summary += f" · {len(missing)} missing"
        if unaskable:
            summary += f", {unaskable} with no Bloomberg ticker to ask with"
    return html.Details(className="details", children=[
        html.Summary(summary, title=about_text, className="about-title"),
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


UPLOAD_ISSUES_TITLE = "Rows of the blotter file that need a fix"
UPLOAD_ISSUES_TABLE_ID = "market-data-upload-issues-table"
UPLOAD_KIND_WORDS = {"UNRECOGNISED": "needs a fix: on file, no P&L", "WARNING": "loaded, with a warning",
                     "REJECTED": "not loaded: could not be read", "NOT LOADED": "not loaded: a kind the app skips"}


def upload_issue_rows(conn: sqlite3.Connection) -> List[dict]:
    """The last upload's rows that need the user (`data.ingest.upload.last_upload_issues`): kind
    UNRECOGNISED (on file as a trade, P&L blank until the contract is mapped: every row loads,
    2026-09-29), WARNING (loaded on the primary field), REJECTED / NOT LOADED (an older upload's
    rows that did not become trades); the needs-a-fix rows first."""
    try:
        from data.ingest.upload import last_upload_issues
        found = last_upload_issues(conn)
    except Exception:  # noqa: BLE001 -- an older database or a reader mid-change: the table as it is
        try:
            found = [{"row_no": n, "symbol": sym, "kind": kind, "reason": why, "filename": name, "uploaded_at": at,
                      "trade_id": ""} for n, sym, kind, why, name, at in conn.execute(
                "SELECT row_no, symbol, kind, reason, filename, uploaded_at FROM upload_issues ORDER BY row_no")]
        except sqlite3.Error:      # no upload since this panel was added: no table yet
            return []
    rank = {"UNRECOGNISED": 0, "REJECTED": 1, "NOT LOADED": 2, "WARNING": 3}
    rows = [{"row_no": r.get("row_no"), "symbol": r.get("symbol"), "kind": UPLOAD_KIND_WORDS.get(str(r.get("kind")),
                                                                                                  str(r.get("kind") or "")),
             "code": str(r.get("kind") or ""), "trade_id": str(r.get("trade_id") or ""), "reason": r.get("reason"),
             "filename": r.get("filename"), "uploaded_at": r.get("uploaded_at"), "flag": "x"} for r in found]
    return sorted(rows, key=lambda r: (rank.get(r["code"], 9), r["row_no"] or 0))


def upload_issues_panel(conn: sqlite3.Connection) -> html.Div:
    """What the last upload could not price or read, row by row, with the parser's own reason: a
    row that needs a fix is on file as a trade (every row loads) with blank P&L until its contract
    is mapped; a warning loaded on the primary field."""
    rows = upload_issue_rows(conn)
    about_text = ("Rows of your blotter file that need you. Needs a fix: on file as a trade, its P&L blank until the "
                  "contract is mapped (red on the Book and the Blotter). Warning: loaded, but two cells disagreed or "
                  "one was doubtful. Not loaded: an older upload's row that did not become a trade.")
    if not rows:
        return _quiet(UPLOAD_ISSUES_TITLE, "none on file: every row of the last upload loaded cleanly (or no blotter "
                                           "has been uploaded since this list was added).", about_text)
    return _panel(f"{UPLOAD_ISSUES_TITLE} · {len(rows)} in {rows[0]['filename']}", [
        _panel_table(UPLOAD_ISSUES_TABLE_ID,
                     [("File row", "row_no"), ("Symbol", "symbol"), ("Trade Id", "trade_id"), ("What happened", "kind"),
                      ("Why", "reason")],
                     rows, wide=("reason",), numeric=("row_no",)),
    ], about_text=about_text)


def safe_panel(title: str, build: Callable[[], html.Div]) -> html.Div:
    """One panel failing must not take the tab, or the other panels, down with it."""
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 -- say what failed where the panel would be
        import logging
        logging.getLogger(__name__).exception("Data tab panel %r failed", title)
        return _panel(title, [message_box(f"This panel could not be built ({type(exc).__name__}: {exc}).")])


def _connect_readonly(path) -> sqlite3.Connection:
    """Open the database read-only without importing `ui.app` (which pulls in the
    other tabs and can be mid-edit while this module is developed/tested)."""
    from pathlib import Path
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})




# ============================================================================ the Data tab (2026-09-29)
# "Can I trust today's numbers?" (user, 2026-09-29: the layout approved piece by piece). Top to
# bottom: one status line (`status_line`); Missing, and what it blocks (`missing_block`); Marks
# check (`marks_check_rows`, filtered in its own callback); Reference closes
# (`reference_close_rows`); Contract dates (`contract_dates_line`); Diagnostics, one closed fold (the
# Bloomberg library, the feed status in detail, the connection check); the one Data issues drawer.
# Every figure is a stored official mark or an engine reader's own output: nothing is recomputed.
TAB_TITLE = "Data"
TAB_ABOUT = ("Can I trust today's numbers? The last Bloomberg pull, the marks the book needs that are not on "
             "file, every official mark the book uses against its previous close, the reference closes the "
             "header's periods read, and the contract dates. This tab never asks Bloomberg for anything: the top "
             "bar's Pull Bloomberg now is the one action; the tab only re-reads what is on file.")
MISSING_ABOUT = (
    "Every official mark the book needs on the date that is not on file, and the trades it touches. A trade that "
    "reads a missing mark is priced off the nearest official marks meanwhile (trades reading it); it is left out of "
    "the P&L only when there are none (trades unpriced). A trade left out of the P&L for another reason is listed "
    "under them. Notional is the face amount of the tickets, never converted with a mark: sorted by the USD face "
    "amount, largest first, the rows with a face in another currency after them. The reason is on hover.")
MARKS_ABOUT = ("Every official mark the book uses on the date, futures, options, LME and FX together, against the "
               f"previous business day's close. Flagged: a move above {BAD_TICK_PCT:g} %, a value exactly unchanged "
               "(stale or copied), or on today's date a snap older than the stale limit. Filter by commodity or "
               "sector from the Price column's funnel, search by name, instrument or source; Download CSV gives "
               "the rows shown at full figures.")
REFERENCE_ABOUT = (
    "The closes the header's Daily, 5d, MTD and YTD figures difference against. A close is complete when every mark "
    "the book needed that day is on file as official; the Bloomberg backfill fills them after each pull. Trades "
    "left out = priced today but with no price on that close, so that period leaves them out (names on hover). "
    "When a close is not usable, the header steps back to the first earlier close with value and says 'ref <date>'.")
CONTRACT_DATES_ABOUT = (
    "Bloomberg's last-trade and first-notice dates of the open futures and options, asked once per contract by "
    "Pull Bloomberg now. Until they are on file the contract runs on the contract master's conservative estimate.")
DIAG_TITLE = "Diagnostics"
DIAG_ABOUT = ("The Bloomberg library (everything a pull asks for), the last pull's feed status step by step, and "
              "the Bloomberg connection check.")

MARKS_SECTION_ID = "market-data-marks"
MARKS_TOOLS_ID = "market-data-marks-tools"      # the strip's search (hidden with no price)
MARKS_FILTER_ID = "market-data-marks-filter"    # no longer a control: the commodity / sector list is the Price funnel
MARKS_FSTORE_ID = "market-data-marks-fstore"    # session: the headings' filters (`data_checks.normal_marks_filter`)
MARKS_SEARCH_ID = "market-data-marks-search"
MARKS_META_ID = "market-data-marks-meta"
MARKS_EMPTY_ID = "market-data-marks-empty"
MARKS_TABLE_WRAP_ID = "market-data-marks-wrap"
MARKS_TABLE_ID = "market-data-marks-table"
MARKS_STORE_ID = "market-data-marks-rows"
MARKS_CSV_ID = "market-data-marks-csv"
MARKS_DOWNLOAD_ID = "market-data-marks-download"
CONTRACT_DATES_PANEL_ID = "market-data-contract-dates"
DIAGNOSTICS_ID = "market-data-diagnostics"
DIAG_BODY_ID = "market-data-diagnostics-body"
DIAG_SUMMARY_ID = "market-data-diagnostics-summary"   # its clicks build the fold's body (never before it opens)

_HIDDEN = {"display": "none"}
_SECTOR_ORDER = ("energy", "metals", "agriculture", "ferrous", "chemicals", "freight")
FX_SECTOR = "fx"
_PERIODS = (("daily", "Daily"), ("d5", "5d"), ("mtd", "MTD"), ("ytd", "YTD"))
MARKS_CSV_COLUMNS = ["instrument_id", "name", "price_kind", "group", "commodity", "mark_type", "settle_date",
                     "value_raw", "source_code", "snapped_at", "previous_raw", "previous_date_iso", "change_raw",
                     "change_pct_raw", "flag", "note"]


def _as_float(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


# ---- plain names (display only)
_TRADE_NAME_SQL = """
SELECT t.trade_id, t.product, t.instrument_id, i.base_ccy, i.quote_ccy, i.expiry_date, MAX(l.settle_date)
FROM trades_official t JOIN instruments i USING (instrument_id) LEFT JOIN trade_legs l USING (trade_id)
GROUP BY t.trade_id
"""


def _root_id(instrument_id: str, roots: dict, bases: Dict[str, str]) -> str:
    base = str(bases.get(instrument_id, "") or "").replace(" ", "").upper()
    if base in roots:
        return base
    return instrument_id if instrument_id in roots else base


def _is_lme(root_id: str, root) -> bool:
    return root_id.startswith("LME:") or str(getattr(root, "exchange", "") or "") == "LME"


def name_context(conn: sqlite3.Connection) -> Tuple[dict, Dict[str, str], Dict[str, str]]:
    """(contract roots, instrument id -> base_ccy, trade id -> 'WTI Dec26 (T-12)'), read once per render."""
    try:
        from data.contracts import load_roots
        roots = dict(load_roots())
    except Exception:  # noqa: BLE001 -- a name without its root is still shown (the id)
        roots = {}
    bases: Dict[str, str] = {}
    try:
        for iid, base in conn.execute("SELECT instrument_id, base_ccy FROM instruments"):
            bases[str(iid)] = str(base or "")
    except sqlite3.Error:
        pass
    names: Dict[str, str] = {}
    try:
        for tid, product, iid, base, quote, expiry, settle in conn.execute(_TRADE_NAME_SQL):
            iid = str(iid)
            root_id = _root_id(iid, roots, bases)
            if product in ("FX_SPOT", "FX_FWD", "FX_SWAP"):
                name = fx_name(iid, product, settle)
            elif product == "FX_OPTION":
                name = fx_name(f"{base}{quote}", "FX_OPTION", expiry)
            elif product == "LME_FWD":
                name = lme_name(roots.get(root_id), root_id, settle)
            else:
                name = contract_name(iid, roots.get(root_id), root_id)
            names[str(tid)] = f"{name} ({tid})"
    except sqlite3.Error:
        pass
    return roots, bases, names


def mark_name(instrument_id: str, mark_type: str, settle_date: str, roots: dict, bases: Dict[str, str]) -> str:
    """'USDCNH spot', 'USDCNH 18 Nov forward', 'LME copper cash', 'LME copper 10 Dec', 'WTI Dec26'."""
    iid = str(instrument_id or "")
    if is_fx_pair(iid):
        return fx_name(iid, "FX_FWD", settle_date) if mark_type == "FWD_OUTRIGHT" else f"{iid} spot"
    root_id = _root_id(iid, roots, bases)
    root = roots.get(root_id)
    if _is_lme(root_id, root):
        short = short_root_name(root, root_id)
        if mark_type == "SPOT":
            return f"{short} cash"
        if mark_type == "LME_CURVE":
            return f"{short} curve"
        return lme_name(root, root_id, settle_date)
    return contract_name(iid, root, root_id)


def price_kind_words(instrument_id: str, mark_type: str, settle_date: str) -> str:
    """What kind of price a mark is, in plain words."""
    iid = str(instrument_id or "")
    if mark_type == "SPOT":
        return "spot" if is_fx_pair(iid) else "LME cash price"
    if mark_type == "FWD_OUTRIGHT":
        return (f"forward for {short_date(settle_date)}" if is_fx_pair(iid)
                else f"LME prompt {short_date(settle_date)}")
    if mark_type == "FUTURE_PX":
        parsed = parse_contract_id(iid)
        return "option price" if parsed and parsed.get("option_type") else "futures close"
    if mark_type == "LME_CURVE":
        return "LME curve (cash and 3M)"
    return str(mark_type or "").lower()


def mark_group(instrument_id: str, roots: dict, bases: Dict[str, str]) -> Tuple[str, str]:
    """(sector, commodity) of a mark: ('fx', 'USDCNH') for a pair, else its root's sector and
    subsector ('metals', 'copper'); ('other', name) when the root is not known."""
    iid = str(instrument_id or "")
    if is_fx_pair(iid):
        return FX_SECTOR, iid
    root_id = _root_id(iid, roots, bases)
    root = roots.get(root_id)
    sector = str(getattr(root, "sector", "") or "other").lower()
    commodity = str(getattr(root, "subsector", "") or "").replace("_", " ").strip()
    commodity = commodity[:1].upper() + commodity[1:] if commodity else short_root_name(root, root_id)
    return sector, commodity


def _sector_label(sector: str) -> str:
    return "FX" if sector == FX_SECTOR else (sector or "other").capitalize()


def _sector_rank(sector: str) -> int:
    if sector in _SECTOR_ORDER:
        return _SECTOR_ORDER.index(sector)
    return len(_SECTOR_ORDER) + (1 if sector == FX_SECTOR else 0)


def _hover_table(table_id: str, columns: List[Tuple[str, str]], rows: List[dict], hover: Dict[str, str],
                 numeric: Tuple[str, ...] = (), wide: Tuple[str, ...] = (),
                 page_size: int = PANEL_PAGE_SIZE) -> dash_table.DataTable:
    """`_panel_table` with a sentence on hover of some cells: `hover` maps a shown column to the
    row key holding its hover text."""
    table = _panel_table(table_id, columns, rows, wide=wide, numeric=numeric, page_size=page_size)
    table.tooltip_data = [{col: {"value": plain_words(r.get(key)), "type": "text"}
                           for col, key in hover.items() if r.get(key)} for r in rows]
    table.tooltip_delay = 200
    table.tooltip_duration = None
    return table


# ---- Phase G (round 2, 2026-09-29): the checks. The tables are drawn through `ui.tabs.data_kit`
# and their rows built by `ui.tabs.data_checks` (the problems, the marks check, the pull steps).
PROBLEMS_TITLE = "Problems"
PROBLEMS_ABOUT = ("Every price the book needs that is missing or failed a check, and any trade left out of the "
                  "P&L, each with what it does to the figures. A flagged price stays the official price: the Book "
                  "marks the figures it touches. The Bloomberg pull's own problems are in the Bloomberg section "
                  "below, the trades' own (a contract not recognised, a row not loaded) in the Trades section.")
MARKS_ABOUT = ("Every official price the book uses on the date, missing and flagged first, each checked four ways: "
               "it arrived (an official price dated the as-of is on file), it is fresh (the date's own price, not one "
               "carried), its move since the previous close is sane, and its units agree with the fills it prices. "
               "Hover a cross for its sentence.")
PROBLEMS_STORE_ID = "market-data-problems-rows"
PROBLEMS_SORT_ID = "market-data-problems-sort"
PROBLEMS_SLOT_ID = "market-data-problems-slot"
PROBLEMS_META_ID = "market-data-problems-meta"
PROBLEMS_CSV_ID = "market-data-problems-csv"
PROBLEMS_DOWNLOAD_ID = "market-data-problems-download"
PROBLEM_SORT_TYPE = "data-problem-sort"
MARKS_STATUS_ID = "market-data-marks-status"   # retired with the filter bar (2026-09-29): Status is a funnel
MARKS_CLEAR_ID = "market-data-marks-clear"
MARKS_SORT_ID = "market-data-marks-sort"
MARK_SORT_TYPE = "data-mark-sort"


def _link(text: str, target: str, hover: str = "", warn: bool = False) -> html.A:
    classes = "data-status-link " + ("data-status-link--warn cell-amber" if warn else "cell-pos")
    return html.A(text, href=f"#{target}", className=classes, title=plain_words(hover) or None)


# ---- 1. the status line
def status_line(status: Optional[dict], pull_problems: int, marks: Tuple[int, ...], closes: List[dict],
                dates: Tuple[int, int], trades: Optional[Tuple[int, List[dict]]] = None) -> list:
    """Last pull (ok / N problems) · Marks 43 of 47 · Reference closes Daily ✓ 5d ✓ MTD ✓ YTD ✗ ·
    Contract dates 9 from Bloomberg, 2 estimated: each green or amber, each a jump to its detail,
    the full sentence on hover."""
    from ui.feed_controls import pull_time, short_state
    line = top_bar_status(status)
    state = short_state(line)
    when = pull_time((status or {}).get("time")) if status else ""
    if not status or state == "no pull yet":
        pull = _link("No Bloomberg pull yet", BBG_CARD_ID, line, warn=True)
    else:
        words = f"Last pull {when}" if when else "Last pull"
        if pull_problems:
            pull = _link(f"{words} · {pull_problems} problem{'s' if pull_problems != 1 else ''}", BBG_CARD_ID,
                         line, warn=True)
        elif state == "not connected":
            pull = _link(f"{words} · not connected", BBG_CARD_ID, line, warn=True)
        else:
            pull = _link(f"{words} · ok", BBG_CARD_ID, line)
    total, arrived = marks[0], marks[1]
    closed = marks[2] if len(marks) > 2 else 0     # prices on a day their exchange was shut: not due, not counted
    closed_words = (f" {closed:,} more not counted: their exchange was closed that day." if closed else "")
    if not total:
        marks_link = _link("Marks: none needed" + (f" · {closed:,} exchange closed" if closed else ""),
                           MARKS_SECTION_ID, "The book needs no official price on this date." + closed_words)
    else:
        marks_link = _link(f"Marks {arrived:,} of {total:,}" + (f" · {closed:,} exchange closed" if closed else ""),
                           MISSING_PANEL_ID if arrived < total else MARKS_SECTION_ID,
                           (f"{total - arrived:,} of the {total:,} official prices the book needs are not on file for "
                            "the date." if arrived < total else "Every official price the book needs is on file.")
                           + closed_words,
                           warn=arrived < total)
    bits = [f"{c['period']} {kit.CROSS if c['flag'] else kit.TICK}" for c in closes]
    ref = _link("Reference closes " + " ".join(bits) if bits else "Reference closes: none", PAST_CLOSES_PANEL_ID,
                "; ".join(f"{c['period']} ({c['date_iso']}): {c['state']}" for c in closes),
                warn=any(c["flag"] for c in closes))
    n, estimated = dates
    if not n:
        dates_link = _link("Contract dates: none needed", CONTRACT_DATES_PANEL_ID,
                           "No open future or option needs Bloomberg's contract dates.")
    else:
        dates_link = _link(f"Contract dates {estimated} estimated" if estimated else "Contract dates from Bloomberg",
                           CONTRACT_DATES_PANEL_ID,
                           f"{n - estimated} of {n} contracts on Bloomberg's own dates"
                           + (f"; {estimated} on the contract master's estimate until a pull stores Bloomberg's."
                              if estimated else "."),
                           warn=bool(estimated))
    parts = [pull]
    if trades is not None:
        parts.append(trades_link(*trades))
    parts += [marks_link, ref, dates_link]
    out: list = []
    for part in parts:
        if out:
            out.append(html.Span(" · ", className="data-status-sep"))
        out.append(part)
    return out


def trades_link(n_trades: int, rows: List[dict], dupes: int = 0) -> html.A:
    """'Trades 89 · 1 warning' (amber with any problem or possible duplicate, 'Trades 89 · ok'
    without), a jump to the Blotter card; the counts in full on hover."""
    words = data_checks.trade_problem_words(rows)
    if dupes:
        words = " · ".join(x for x in (words, f"{dupes:,} possible duplicate{'' if dupes == 1 else 's'}") if x)
    head = f"Trades {n_trades:,}" if n_trades else "No trades on file"
    if words:
        return _link(f"{head} · {words}", TRADES_CARD_ID,
                     f"The trades on file: {words}. Listed in full in the Blotter section.", warn=True)
    return _link(f"{head} · ok" if n_trades else head, TRADES_CARD_ID,
                 "Every trade on file is recognised, the last upload loaded every row cleanly and no fill is on "
                 "file twice." if n_trades else "No blotter uploaded yet: press Upload blotter.", warn=not n_trades)


TRADES_TITLE = "Blotter"
TRADES_ABOUT = ("What the last blotter file loaded, every row that did not load or needs a fix and why, a check that "
                "no fill is on file twice, and a check of a file before you upload it. The one place the trades' "
                "problems are written out.")
TRADE_PROBLEMS_TITLE = "Rows not loaded or to fix"
TRADE_PROBLEMS_ABOUT = ("Every row of the last file that did not load as a clean trade, needs a fix first: trades on "
                        "file whose contract is not recognised (no P&L until mapped), rows not loaded (a status, the "
                        "book filter, an earlier version of a repeated Trade Id), warnings, and cancelled rows with "
                        "the trade they removed. Sort on a title, filter from the What happened funnel.")
DUPES_TITLE = "Duplicates check"
DUPES_ABOUT = ("The trades on file that look like one fill booked under two or more Trade Ids: the same trade date, "
               "contract, side, size and price. An upload merges by Trade Id, so a Trade Id is never on file twice; "
               "this catches a fill the broker re-booked under a new Trade Id. Nothing is dropped or merged: a flag "
               "only.")
DUPES_NONE = "No duplicates: every Trade Id is on file once, and no fill appears under two Trade Ids"


def _hk_upload_time(iso) -> str:
    """'Wed 30 Sep 11:22 HK' from the upload's UTC stamp (a naive stamp is UTC); '' when unreadable."""
    from ui.feed_controls import pull_time
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return pull_time(when.isoformat())


def trades_upload_line(report: Optional[dict], n_trades: int) -> Tuple[str, str]:
    """(the last upload in one line, its summary for the hover): "Last upload template PnL tool.csv ·
    Wed 30 Sep 11:22 HK · 12 added, 77 replaced, 0 removed · 89 trades on file"."""
    if not report:
        if n_trades:
            return (f"No upload recorded on this database · {n_trades:,} trades on file",
                    "The trades on file were loaded without an upload record: the next upload records what it "
                    "loaded, what it did not, and why.")
        return "No blotter uploaded yet", "Press Upload blotter to load Jason's file."
    name = str(report.get("filename") or "file name not recorded")
    when = _hk_upload_time(report.get("uploaded_at"))
    counts = (f"{int(report.get('added') or 0):,} added, {int(report.get('replaced') or 0):,} replaced, "
              f"{int(report.get('removed') or 0):,} removed")
    on_file = f"{n_trades:,} trade{'' if n_trades == 1 else 's'} on file"
    line = " · ".join(x for x in (f"Last upload {name}", when, counts, on_file) if x)
    return line, plain_words(report.get("summary")) or line


def loaded_line(outcome: Optional[dict]) -> Optional[Tuple[str, str]]:
    """("Loaded 89: 70 futures, 4 options on futures, 15 FX forwards", its hover) of the last
    file; None before the first recorded upload."""
    if not outcome:
        return None
    counts = outcome.get("counts") or {}
    loaded = int(counts.get("loaded") or 0)
    kinds = [(str(k), int(v)) for k, v in (outcome.get("loaded_by_kind") or {}).items() if v]
    parts = [f"{n:,} {k if k.isupper() or k.startswith(('FX', 'LME')) else k.lower()}" for k, n in kinds]
    words = f"Loaded {loaded:,}" + (f": {', '.join(parts)}" if parts else "")
    rest = [f"{int(counts.get(k) or 0):,} {w}" for k, w in (("not_loaded", "not loaded"), ("cancelled", "cancelled"),
                                                          ("need_fix", "need a fix"), ("warnings", "with a warning"))
            if counts.get(k)]
    hover = (f"The rows of the last file that became trades on file, by kind. {'; '.join(rest).capitalize()}."
             if rest else "The rows of the last file that became trades on file, by kind. Every row loaded.")
    return words, hover


def trades_head(report: Optional[dict], n_trades: int, rows: List[dict],
                outcome: Optional[dict] = None) -> html.Div:
    """The Blotter card's first blocks: the last upload in one line, what it loaded by kind in
    one line, then "Rows not loaded or to fix (N)"."""
    words, hover = trades_upload_line(report, n_trades)
    lines = [html.Div(html.Span(words, title=hover or None), className="tk-headline data-bbg-line")]
    loaded = loaded_line(outcome)
    if loaded:
        lines.append(html.Div(html.Span(loaded[0], title=plain_words(loaded[1]) or None),
                              className="tk-headline data-bbg-line"))
    return html.Div(lines + [html.Div(kit.strip_title(f"{TRADE_PROBLEMS_TITLE} ({len(rows)})", TRADE_PROBLEMS_ABOUT),
                                      className="data-bbg-subhead")])


def duplicates_block(groups: List[dict], names: Optional[Dict[str, str]] = None) -> html.Div:
    """"Duplicates check": one green line when no fill is on file twice, else the groups."""
    title = f"{DUPES_TITLE} ({len(groups)})" if groups else DUPES_TITLE
    head = html.Div(kit.strip_title(title, DUPES_ABOUT), className="data-bbg-subhead")
    if not groups:
        return html.Div([head, html.Div(html.Span(f"{kit.TICK} {DUPES_NONE}", className="cell-pos"),
                                        className="tk-headline data-bbg-line")])
    return html.Div([head, data_checks.duplicates_table(groups, names)])


def trade_facts(conn: sqlite3.Connection) -> Tuple[Optional[dict], int, List[dict], Optional[dict], List[dict]]:
    """(the last upload's report, the trades on file, the rows not loaded or to fix, the last
    upload's outcome, the possible duplicates) for the Blotter card."""
    from data.bloomberg.inventory import unrecognised as read_unrecognised
    from data.ingest.upload import last_upload_issues, last_upload_report
    try:
        n_trades = int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
    except sqlite3.Error:
        n_trades = 0
    try:
        from data.ingest.upload import last_upload_outcome
        outcome = last_upload_outcome(conn)
    except ImportError:
        outcome = None
    report = (outcome or {}).get("report") or last_upload_report(conn)
    issues = last_upload_issues(conn) if outcome is None else None
    rows = data_checks.blotter_rows(outcome, issues, read_unrecognised(conn))
    if outcome is not None:
        dupes = list(outcome.get("possible_duplicates") or [])
    else:
        try:
            from data.ingest.upload import possible_duplicates
            dupes = possible_duplicates(conn)
        except ImportError:
            dupes = []
    return report, n_trades, rows, outcome, dupes


def trades_view(rows: Optional[List[dict]], fstate: Optional[dict], sort: Optional[dict]) -> html.Table:
    """The trade problems table for the rows in the store (filtered: its first row says how many)."""
    rows = rows or []
    shown = data_checks.filter_rows(rows, fstate, data_checks.TRADE_FIELDS)
    return data_checks.trade_problems_table(shown, sort, TRADES_SORT_TYPE, fstate, TRADES_COL_TYPE, len(rows), rows)


# ---- 4. Reference closes
def reference_close_rows(conn: sqlite3.Connection, as_of: str, df_today: Optional[pd.DataFrame],
                         tnames: Dict[str, str], backfill: Optional[dict] = None) -> List[dict]:
    """Daily, 5d, MTD, YTD: the reference close each period differences against, its marks needed
    and present (`header.needed_marks`, the header's own count), the backfill's reason when
    incomplete, the trades that period leaves out for want of a price on that close
    (`engine.pnl.reference.diff_split`), and the close the header actually measures from
    (`engine.pnl.reference.resolve_reference`, the header's own call)."""
    from engine.pnl.calendar import load_holidays
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.reference import diff_split, resolve_reference
    from ui.tabs.blotter_pricing import priced_value_book
    from ui.tabs.header import backfill_status, needed_marks
    refs = period_reference_dates(as_of)
    if backfill is None:
        backfill = backfill_status(conn)
    holidays = load_holidays()
    rows = []
    for key, label in _PERIODS:
        day = refs.get(key)
        if not day:
            continue
        needed, missing = needed_marks(conn, day)
        blocked: List[str] = []
        used, used_why = day, ""
        if df_today is not None and not df_today.empty:
            blocked = sorted(str(t) for t in diff_split(df_today, priced_value_book(conn, day)[0]).blocked_ids)
            try:
                choice = resolve_reference(df_today, day, lambda iso: priced_value_book(conn, iso)[0], holidays,
                                           frames_filled=True)
                used = choice.ref_date_used if choice.found else None
                used_why = choice.note if choice.found else choice.exhausted_sentence
            except Exception as exc:  # noqa: BLE001 -- the cell says why
                used, used_why = None, f"the close used could not be worked out ({type(exc).__name__}: {exc})"
        if not needed:
            state, why = "nothing needed", "no trade was open that day"
        elif missing:
            state, why = f"{len(missing):,} missing", missing_close_words(missing, day, backfill)
        else:
            state, why = "complete", ""
        rows.append({"period": label, "date": short_date(day), "date_iso": day,
                     "marks": f"{needed - len(missing):,} of {needed:,}" if needed else MISSING,
                     "needed": needed, "missing": len(missing), "state": state, "why": why,
                     "used": used, "used_why": used_why, "left_out": len(blocked),
                     "left_names": "; ".join(tnames.get(t, t) for t in blocked),
                     "flag": "incomplete" if missing else ""})
    return rows


def reference_closes_panel(rows: List[dict], issues: Optional[list] = None) -> html.Div:
    """Daily, 5d, MTD, YTD: the reference close, complete or not, the close actually used, the
    trades that period leaves out."""
    flagged = sum(1 for r in rows or [] if r["flag"])

    def fold(table):
        meta = f"{flagged} incomplete" if flagged else ""
        return html.Details(className="book-fold tk-fold-block data-fold", children=[
            html.Summary([html.Span(PAST_CLOSES_TITLE, className="book-section-title",
                                    title=plain_words(REFERENCE_ABOUT)),
                          html.Span(f" · {meta}" if meta else "", className="book-section-meta cell-amber")]),
            table])
    if not rows:
        return fold(kit.table(html.Thead(), [kit.note_row("No reference close for this date.", 1, "cell-missing")],
                              className="tk-small"))
    cols: Tuple[kit.Column, ...] = (
        ("period", "Period", "l", "The header's period.", False),
        ("date", "Reference close", "l", "The close the period is measured from by its own rule.", False),
        ("complete", "Complete", "l", "Every official price the book needed that day is on file.", False),
        ("used", "Close used", "l", "The close the header's figure is actually measured from: an earlier one when the "
                                   "reference close is not usable (at most 5 business days back).", False),
        ("left_out", "Trades left out", "", "Priced today but with no price on that close, so that period leaves "
                                            "them out (names on hover).", False),
    )
    body = []
    for r in rows:
        if r["flag"] and issues is not None:
            issues.append((f"{r['period']} close {r['date']}", r["why"]))
        if not r["needed"]:
            complete = html.Span("Nothing needed", className="cell-missing", title=r["why"])
        elif r["flag"]:
            complete = html.Span(f"{kit.CROSS} {r['missing']:,} of {r['needed']:,} missing", className="cell-amber",
                                 title=plain_words(r["why"]) or None)
        else:
            complete = html.Span(f"{kit.TICK} {r['marks']}", className="cell-pos")
        if r["used"] is None:
            used = missing_cell(r["used_why"] or "no usable close within 5 business days")
        elif r["used"] != r["date_iso"]:
            used = html.Span(short_date(r["used"]), className="cell-amber", title=plain_words(r["used_why"]) or None)
        else:
            used = html.Span(short_date(r["used"]), title=plain_words(r["used_why"]) or "the reference close itself")
        body.append(html.Tr([
            kit.td(r["period"], left=True), kit.td(r["date"], left=True, title=r["date_iso"]),
            kit.td(complete, left=True), kit.td(used, left=True),
            kit.td(f"{r['left_out']:,}", title=r["left_names"] or None),
        ]))
    return fold(kit.table(kit.head(cols, None, "data-ref-none"), body, className="tk-small data-ref-table"))


# ---- 5. Contract dates
def contract_dates_line(conn: sqlite3.Connection, as_of: str, ctx: tuple,
                        issues: Optional[list] = None) -> Tuple[html.Div, Tuple[int, int]]:
    """'Contract dates · 9 from Bloomberg, 2 estimated', the list on click (last trade, first
    notice, source)."""
    roots, bases, _names = ctx
    rows = contract_date_rows(conn, as_of)
    if not rows:
        return about(f"{CONTRACT_DATES_TITLE} · none needed", f"No open future or option needs Bloomberg's contract "
                     f"dates on {short_date(as_of)}. {CONTRACT_DATES_ABOUT}", level="h5"), (0, 0)
    estimated = [r for r in rows if r["flag"]]
    held: Dict[str, str] = {}
    try:
        held = {str(i): str(e or "") for i, e in conn.execute("SELECT instrument_id, expiry_date FROM instruments")}
    except sqlite3.Error:
        pass
    body = []
    for r in rows:
        cid = str(r["contract_id"])
        root_id = _root_id(cid, roots, bases)
        name = data_checks.price_name(cid, "FUTURE_PX", "", root_id, roots)
        guess = held.get(cid, "")
        if r["last_trade_date"]:
            last = short_date(r["last_trade_date"])
        elif guess and not guess.startswith("9999"):
            last = html.Span(f"≈ {short_date(guess)}", className="cell-estimated",
                             title=plain_words(f"The contract master's conservative estimate ({guess}), used until "
                                               f"Bloomberg's own date is on file: {r['why']}"))
        else:
            last = missing_cell(r["why"])
        body.append(html.Tr([
            kit.td(name, left=True, title=f"{cid} · {r['bbg_ticker']}"),
            kit.td(last, left=True, title=r["last_trade_date"] or None),
            kit.td(short_date(r["first_notice_date"]) if r["first_notice_date"] else missing_cell(
                r["why"] or "no first notice for this contract"), left=True, title=r["first_notice_date"] or None),
            kit.td(html.Span("Estimated", className="cell-amber", title=plain_words(r["why"]))
                   if r["flag"] else (r["source"] or "Bloomberg"), left=True),
            kit.td(str(r["trades"] if r["trades"] is not None else MISSING)),
        ]))
    if estimated and issues is not None:
        issues.append((CONTRACT_DATES_TITLE, f"{len(estimated)} contract{'' if len(estimated) == 1 else 's'} run on "
                                             "the contract master's estimated "
                                             "dates until Bloomberg's own are on file: "
                                             + ", ".join(str(r["contract_id"]) for r in estimated)))
    cols: Tuple[kit.Column, ...] = (
        ("contract", "Contract", "l", "The contract with its exchange; its id and Bloomberg ticker on hover.", False),
        ("last", "Last trade", "l", "Bloomberg's last trade date.", False),
        ("fn", "First notice", "l", "Bloomberg's first notice date.", False),
        ("source", "Source", "l", "Bloomberg's own dates, or estimated by the contract master until they are on file.",
         False),
        ("trades", "Trades", "", "Trades on the contract.", False),
    )
    # the counts ("0 from Bloomberg, 23 estimated") are said once, in the status line that links here
    return html.Details(className="book-fold tk-fold-block data-fold", children=[
        html.Summary([html.Span(f"{CONTRACT_DATES_TITLE} ({len(rows)})", className="book-section-title",
                                title=CONTRACT_DATES_ABOUT)]),
        kit.table(kit.head(cols, None, "data-dates-none"), body, className="tk-small"),
    ]), (len(rows), len(estimated))


# ---- 6. Diagnostics
def diagnostics_body(conn: sqlite3.Connection, as_of: str, status: Optional[dict]) -> html.Div:
    """The last pull step by step, the backfill's requests / errors / values that were not
    numbers, the curves' and vols' left-outs, the Bloomberg library, and everything the pull
    recorded (folded); the connection check button sits after it in the static layout."""
    from ui.feed_controls import short_state
    left = data_checks.left_out_block(status)
    feed = status_block(status)
    try:
        from ui.warmup import status_line
        warm_text, warm_hover = status_line()
    except Exception as exc:  # noqa: BLE001 -- one line, never the fold
        warm_text, warm_hover = f"Warm-up: status not readable ({type(exc).__name__})", ""
    # layout wave 2 (2026-09-29): the four states in one small key / value table, the detail on hover
    line = top_bar_status(status)
    pull_words, pull_hover = data_checks.pull_facts(status, no_pull=short_state(line) == "no pull yet")
    fill_words, fill_hover = data_checks.backfill_facts(status)
    warm_words = warm_text.split(": ", 1)[1] if warm_text.startswith("Warm-up: ") else warm_text
    facts = [("Warm-up", warm_words, warm_hover or "Fills every screen's caches in the background after a start, "
                                                    "an upload or a pull."),
             ("Last pull", pull_words, pull_hover),
             ("Backfill", fill_words, fill_hover),
             ("Bloomberg", short_state(line), line)]
    parts: list = [html.Table(html.Tbody([
        html.Tr([html.Td(k, className="tk-kv-k"), html.Td(cap(v), title=plain_words(h) or None)])
        for k, v, h in facts]), className="tk-table tk-kv data-diag-facts")]
    steps = data_checks.steps_table(status)
    if steps is not None:
        parts += [about("The last pull, step by step", "What the last Pull Bloomberg now did, each step's outcome.",
                        level="h5"), steps]
    parts += data_checks.backfill_tables(status)
    if left is not None:
        parts += [about("Left out by the curves and vols", "Quotes the OIS curves and vol smiles did not use.",
                        level="h5"), left]
    parts.append(safe_panel(LIBRARY_TITLE, lambda: library_panel(conn, as_of)))
    # everything else the pull recorded, one row per block (the first line is the Bloomberg fact above)
    blocks = feed[1:] if isinstance(feed, list) else []
    if blocks:
        parts.append(html.Details(className="book-fold tk-fold-block data-fold", children=[
            html.Summary(html.Span("Everything the last pull recorded", className="book-section-title")),
            html.Table(html.Tbody([html.Tr(html.Td(b, className="l")) for b in blocks]),
                       className="tk-table tk-kv data-diag-record")]))
    return html.Div(parts, className="data-diag")


def _failed_panel(title: str, exc: Exception) -> html.Div:
    import logging
    logging.getLogger(__name__).exception("Data tab block %r failed", title)
    return _panel(title, [message_box(f"This panel could not be built ({type(exc).__name__}: {exc}).")])


def warm(conn: sqlite3.Connection, as_of: str) -> None:
    """Fill the tab's memos for `as_of` (ui/warmup.py): the body, as the callback renders it."""
    path = next((p for _s, name, p in conn.execute("PRAGMA database_list") if name == "main"), None)
    if path:
        render(as_of, path, diag=False)


def marks_token(as_of: Optional[str], n: int) -> dict:
    """What the marks check's store holds (2026-09-29, performance): not the rows (about 50 kB on
    every tab switch) but the as-of and a stamp; the table, its filter and its CSV read the rows
    from the server's memo (`data_checks.mark_records`)."""
    import time
    return {"as_of": as_of, "n": n, "at": time.time()}


def _token_rows(token, db_path) -> List[dict]:
    """The marks check's rows for the store's token (a list stored by an older page is used as is)."""
    if isinstance(token, list):
        return token
    as_of = (token or {}).get("as_of")
    if not as_of:
        return []
    conn = _connect_readonly(db_path)
    try:
        return data_checks.mark_records(conn, as_of)
    finally:
        conn.close()


def render(as_of_date: Optional[str], db_path, diag: bool = True) -> tuple:
    """The body callback's outputs for `as_of_date` (the header's as-of): the status line, the
    problems (rows for the store) and the card's style, the marks-check rows (store), its
    sector / commodity options, its empty line and its bar's style, the reference closes, the
    contract dates, the diagnostics body, the Data issues drawer, the Bloomberg card's line and
    pull problems, the Blotter card's head (13), its rows (14, for the store), its duplicates check (15),
    and the Bloomberg card's fold of what the last pull asked (16). A block that fails says so
    where it would be; the rest still renders."""
    blank = html.Div()
    if not as_of_date:
        return (message_box("No as-of date available."), [], _HIDDEN, [], [], blank, _HIDDEN, blank, blank, blank,
                blank, blank, blank, blank, [], blank, blank)
    try:
        conn = _connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return (message_box(f"Database not available ({exc})."), [], _HIDDEN, [], [], blank, _HIDDEN, blank, blank,
                blank, blank, blank, blank, blank, [], blank, blank)
    import logging
    log = logging.getLogger(__name__)
    issues: list = []
    try:
        try:
            from data.bloomberg.live import book_today, read_status
            feed_status = read_status(db_path)
            today = book_today().isoformat()
        except Exception:  # noqa: BLE001 -- an unreadable status file is "no pull recorded"
            feed_status, today = None, _book_today_iso()
        ctx = name_context(conn)
        try:
            from ui.tabs.blotter_pricing import priced_value_book
            df_today = priced_value_book(conn, as_of_date)[0]
        except Exception as exc:  # noqa: BLE001 -- the blocks below still show the marks
            df_today = None
            issues.append((TAB_TITLE, f"The book could not be priced on {as_of_date} ({type(exc).__name__}: {exc})."))

        mark_rows: List[dict] = []
        try:
            mark_rows = data_checks.mark_records(conn, as_of_date)
        except Exception as exc:  # noqa: BLE001
            log.exception("Data tab: the marks check failed for %s", as_of_date)
            issues.append((SUSPECT_TITLE, f"The marks check could not be built ({type(exc).__name__}: {exc})."))
        unpriced: Dict[str, Tuple[str, str]] = {}
        if df_today is not None and not df_today.empty:
            left = df_today[df_today["reason"] != ""]
            unpriced = {str(t): (ctx[2].get(str(t), str(t)), str(r)) for t, r in zip(left["trade_id"], left["reason"])}
        try:
            from data.bloomberg.inventory import unrecognised as read_unrecognised
            unrec = read_unrecognised(conn)
        except Exception as exc:  # noqa: BLE001 -- the problems still list the rest
            log.exception("Data tab: the unrecognised trades could not be read")
            unrec = []
            issues.append((PROBLEMS_TITLE, f"The trades whose contract is not recognised could not be listed "
                                           f"({type(exc).__name__}: {exc})."))
        try:
            problems = data_checks.problem_rows(mark_rows, feed_status, unpriced, as_of_date, today, unrec)
        except Exception as exc:  # noqa: BLE001
            log.exception("Data tab: the problems failed for %s", as_of_date)
            problems = []
            issues.append((PROBLEMS_TITLE, f"The problems could not be listed ({type(exc).__name__}: {exc})."))
        pulls = data_checks.pull_problems(feed_status)
        if not mark_rows:          # nothing needed: "no pull yet" blocks nothing
            pulls = [p for p in pulls if p["label"] != "No pull"]
        n_pull = len(pulls)
        if not mark_rows:
            marks_empty = kit.table(html.Thead(), [kit.note_row(
                f"The book uses no official price on {short_date(as_of_date)}.", 1, "cell-missing")],
                className="tk-small")
            tools_style = _HIDDEN
        else:
            marks_empty, tools_style = html.Div(), {"padding": "8px 12px 0"}

        closes: List[dict] = []
        try:
            closes = reference_close_rows(conn, as_of_date, df_today, ctx[2])
            closes_panel = reference_closes_panel(closes, issues)
        except Exception as exc:  # noqa: BLE001
            closes_panel = _failed_panel(PAST_CLOSES_TITLE, exc)

        try:
            dates_panel, dates = contract_dates_line(conn, as_of_date, ctx, issues)
        except Exception as exc:  # noqa: BLE001
            dates_panel, dates = _failed_panel(CONTRACT_DATES_TITLE, exc), (0, 0)

        diag = safe_panel(DIAG_TITLE, lambda: diagnostics_body(conn, as_of_date, feed_status)) if diag else blank
        try:
            report, n_trades, trade_rows, outcome, dupes = trade_facts(conn)
            trades_block = trades_head(report, n_trades, trade_rows, outcome)
            dupes_block = duplicates_block(dupes, ctx[2])
            trades_part: Optional[tuple] = (n_trades, trade_rows, len(dupes))
        except Exception as exc:  # noqa: BLE001 -- the rest of the tab still renders
            log.exception("Data tab: the trades' problems could not be read")
            trade_rows, trades_part, dupes_block = [], None, blank
            trades_block = html.Div(missing_cell(f"the last upload could not be read ({type(exc).__name__}: {exc})"),
                                    className="data-bbg-line")
            issues.append((TRADES_TITLE, f"The trades' problems could not be read ({type(exc).__name__}: {exc})."))
        try:
            from ui.tabs.blotter_pricing import price_history_summary
            history = price_history_summary(conn)
        except Exception as exc:  # noqa: BLE001 -- the card says it could not be read
            history = {"rows": 0, "contracts": 0, "first": "", "last": "", "error": f"{type(exc).__name__}: {exc}"}
        # a price on a past day its exchange was shut (status CLOSED) was never due: left out of the count, named
        due = [r for r in mark_rows if r["status"] != "CLOSED"]
        arrived = sum(1 for r in due if r["arrived"])
        line = status_line(feed_status, n_pull, (len(due), arrived, len(mark_rows) - len(due)), closes, dates,
                           trades_part)
    finally:
        conn.close()
    drawer = issues_drawer(issues, id=f"{ISSUES_ID}-drawer") or blank
    bbg_line, pull_panel = bloomberg_head(feed_status, pulls, history)
    asked = asked_fold(feed_status) or blank
    tidy([line, marks_empty, closes_panel, dates_panel, diag, drawer, bbg_line, pull_panel, trades_block, dupes_block,
          asked])
    return (line, problems, {} if problems else _HIDDEN, mark_rows, marks_filter_options(mark_rows), marks_empty,
            tools_style, closes_panel, dates_panel, diag, drawer, bbg_line, pull_panel, trades_block, trade_rows,
            dupes_block, asked)


PULL_PROBLEMS_TITLE = "Pull problems"
PULL_PROBLEMS_ABOUT = ("Every failed or partial step of the last Bloomberg pull and of its backfill of past closes, in "
                       "full, with what it leaves the book's figures with. The one place a pull's errors are written.")


def _long_date(iso: str) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return str(iso or "")
    return f"{d.day} {d:%b %Y}"


def history_line(history: Optional[dict]) -> html.Div:
    """'Price history: 433 contracts, first 12 Mar 2024, last 30 Sep 2026' (the book database's own
    Bloomberg daily history, the Risk tab's input), or 'none yet' before the first pull."""
    h = history or {}
    hover = ("Bloomberg's daily closes, volume and open interest, fetched by Pull Bloomberg now (about two and a "
             "half years the first time, then only the new days): the input of every risk figure, never a mark")
    if h.get("error"):
        words, warn = f"Price history: could not be read ({h['error']})", True
    elif not h.get("rows"):
        words, warn = "Price history: none yet, press Pull Bloomberg now", True
    else:
        n = int(h.get("contracts") or 0)
        words = (f"Price history: {n:,} {'contract' if n == 1 else 'contracts'}, first {_long_date(h.get('first'))}, "
                 f"last {_long_date(h.get('last'))}")
        hover += f" · {int(h['rows']):,} daily rows"
        warn = False
    return html.Div(html.Span(words, className="cell-amber" if warn else None, title=hover),
                    className="tk-headline data-bbg-line data-history-line")


# ---- what Bloomberg stored and what the last pull asked (2026-09-30, user: "bloomberg needs to store
# on cache all the data - and only pull any new data"): the backfill's status block "cache"
STEP_NAMES = {"fx_closes": "FX closes", "fx_forwards": "FX forward closes", "points_scale": "Forward points scale",
              "futures": "Futures and option closes", "lme": "LME closes", "vol": "FX vol smiles",
              "ois": "OIS discount curves", "risk_history": "Price history (risk)"}
ASKED_TITLE = "What the last pull asked, step by step"
ASKED_ABOUT = ("Each step of the last pull's history requests: how many securities it asked Bloomberg for, how many "
               "values it left alone because they are already stored, and how many came back empty (the tickers on "
               "hover). A value stored is never asked again; a security that came back empty before is not asked "
               "again by the price history.")


def cache_block(status: Optional[dict]) -> Optional[dict]:
    """The backfill's "cache" block of the status file, or None before a pull that wrote one."""
    backfill = (status or {}).get("backfill")
    cache = backfill.get("cache") if isinstance(backfill, dict) else None
    return cache if isinstance(cache, dict) else None


def _step_name(step: str) -> str:
    return STEP_NAMES.get(step, cap(str(step).replace("_", " ")))


def cache_line(status: Optional[dict]) -> Optional[html.Div]:
    """'Stored: 1,204 closes, 9,380 history days · This pull asked: 12 new, skipped 1,180 already
    stored · 2 came back empty', the steps on hover; None before a pull that wrote the block."""
    cache = cache_block(status)
    if cache is None:
        return None
    if cache.get("error") and not cache.get("sentence"):
        return html.Div(html.Span(cap(f"What is stored could not be counted: {plain_words(cache['error'])}"),
                                  className="cell-amber"), className="tk-headline data-bbg-line")
    steps = cache.get("steps") or {}
    tips = [f"{_step_name(k)}: {int(v.get('asked') or 0):,} asked"
            + (f", {int(v['skipped_stored']):,} already stored" if v.get("skipped_stored") else "")
            + (f", {int(v.get('empty') or 0):,} empty" if v.get("empty") else "")
            for k, v in steps.items() if isinstance(v, dict)]
    days = cache.get("days") or {}
    if days:
        tips.append(f"Past business days: {int(days.get('complete') or 0):,} complete of {int(days.get('listed') or 0):,}"
                    f", {int(days.get('asked') or 0):,} asked this pull, {int(days.get('waiting') or 0):,} waiting")
    empty = int(cache.get("empty") or 0)
    return html.Div(html.Span(cap(str(cache.get("sentence") or "")), className="cell-amber" if empty else None,
                              title="\n".join(tips) or None), className="tk-headline data-bbg-line")


def contract_dates_status_line(status: Optional[dict]) -> Optional[html.Div]:
    """'Contract dates: 23 on file · asked 3 · 1 came back empty' from the pull's contract-dates
    block, when it carries the counts."""
    block = (status or {}).get("contract_dates")
    if not isinstance(block, dict) or not any(k in block for k in ("asked", "on_file", "empty")):
        return None
    parts = [f"{int(block.get('on_file') or 0):,} on file", f"asked {int(block.get('asked') or 0):,}"]
    empty = int(block.get("empty") or 0)
    if empty:
        parts.append(f"{empty:,} came back empty")
    if block.get("known_empty"):
        parts.append(f"{int(block['known_empty']):,} known empty, not asked again")
    tickers = ", ".join(str(t) for t in block.get("empty_tickers") or [])
    return html.Div(html.Span("Contract dates: " + " · ".join(parts), className="cell-amber" if empty else None,
                              title=f"Came back empty: {tickers}" if tickers else
                              "Bloomberg's last trade and first notice dates, asked once per contract and stored"),
                    className="tk-headline data-bbg-line")


def asked_fold(status: Optional[dict]) -> Optional[html.Details]:
    """The fold "What the last pull asked, step by step": Step | Asked | Already stored | Came back
    empty (the tickers on hover). None before a pull that wrote the cache block."""
    cache = cache_block(status)
    steps = (cache or {}).get("steps") or {}
    if not steps:
        return None
    cols: Tuple[kit.Column, ...] = (
        ("step", "Step", "l", "The kind of history the pull asked.", False),
        ("asked", "Asked", "", "Securities asked of Bloomberg this pull.", False),
        ("stored", "Already stored", "", "Values on file, not asked again.", False),
        ("empty", "Came back empty", "", "Asked, but Bloomberg sent nothing (the tickers on hover).", False),
    )
    body = []
    for step, v in steps.items():
        if not isinstance(v, dict):
            continue
        empty = int(v.get("empty") or 0)
        tickers = ", ".join(str(t) for t in v.get("empty_tickers") or [] if t)
        known = int(v.get("known_empty") or 0)
        stored = v.get("skipped_stored")
        body.append(html.Tr([
            kit.td(_step_name(step), left=True),
            kit.td(f"{int(v.get('asked') or 0):,}"),
            kit.td(f"{int(stored):,}" if stored is not None else MISSING,
                   title=None if stored is not None else "This step does not count what it left alone"),
            kit.td(html.Span(f"{empty:,}", className="cell-amber" if empty else None),
                   title=" ".join(x for x in (f"Came back empty: {tickers}." if tickers else "",
                                              f"{known:,} known empty, not asked again." if known else "") if x)
                   or None),
        ]))
    return html.Details(className="book-fold tk-fold-block data-fold", children=[
        html.Summary(html.Span(ASKED_TITLE, className="book-section-title", title=plain_words(ASKED_ABOUT))),
        kit.table(kit.head(cols, None, "data-asked-none"), body, className="tk-small data-asked-table")])


def bloomberg_head(status: Optional[dict], pulls: List[dict],
                   history: Optional[dict] = None) -> Tuple[html.Div, html.Div]:
    """The Bloomberg card's first blocks: the last pull in one line, what is stored and what the
    pull asked in one line, the contract dates' counts when the pull wrote them, the price history
    on file in one line, then "Pull problems (N)"."""
    from ui.feed_controls import short_state
    no_pull = short_state(top_bar_status(status)) == "no pull yet"
    words, hover = data_checks.bloomberg_line(status, no_pull=no_pull)
    warn = no_pull or bool(pulls) or (status or {}).get("connected") is False
    line = html.Div([html.Div(html.Span(words, className="cell-amber" if warn else None,
                                        title=plain_words(hover) or None), className="tk-headline data-bbg-line"),
                     *[x for x in (cache_line(status), contract_dates_status_line(status)) if x is not None],
                     history_line(history)])
    panel = html.Div([
        html.Div(kit.strip_title(f"{PULL_PROBLEMS_TITLE} ({len(pulls)})", PULL_PROBLEMS_ABOUT),
                 className="data-bbg-subhead"),
        data_checks.pull_problems_table(pulls)])
    return line, panel


def marks_filter_options(rows: List[dict]) -> List[dict]:
    """The sector / commodity filter's options: each sector with a price, its commodities under it."""
    by_sector: Dict[str, set] = {}
    for r in rows or []:
        by_sector.setdefault(r["sector"], set()).add(r["commodity"])
    out = []
    for sector in sorted(by_sector, key=lambda s: (_sector_rank(s), s)):
        out.append({"label": f"{_sector_label(sector)} (all)", "value": f"sector:{sector}"})
        out += [{"label": f"· {c}", "value": f"commodity:{sector}:{c}"} for c in sorted(by_sector[sector])]
    return out


def _marks_search() -> html.Div:
    """The marks check's search, in its strip (kept for the session); every other filter is a
    column's funnel (`data_checks.marks_head`)."""
    return html.Div(className="tf-search-wrap",
                    title="Free text over the price's name, instrument, source, commodity and trades; each column "
                          "filters from the funnel in its heading", children=[
        dcc.Input(id=MARKS_SEARCH_ID, type="text", value="", debounce=True, persistence=True,
                  persistence_type="session", placeholder="Search price, trade or source",
                  className="blotter-filter-search tf-search", autoComplete="off")])


def _fold(children: list, title: str, about_text: str, id: Optional[str] = None, meta_id: Optional[str] = None,
          style: Optional[dict] = None) -> html.Details:
    """A closed fold of the Bloomberg card: its title (the definition on hover), a meta span the
    callbacks fill, then its body."""
    summary = [html.Span(title, className="book-section-title", title=plain_words(about_text) or None)]
    if meta_id:
        summary.append(html.Span(id=meta_id, className="book-section-meta"))
    props: Dict[str, Any] = {"className": "book-fold tk-fold-block data-fold", "children": [html.Summary(summary)]
                             + children}
    if id:
        props["id"] = id
    if style is not None:
        props["style"] = style
    return html.Details(**props)


def build_layout(default_date: Optional[str] = None) -> html.Div:
    """The Data tab (2026-09-30, user: "the data tab - needs to be clean"): the title with its one
    status line, then three cards and nothing between them: Blotter (what the last file loaded,
    what it did not and why, the duplicates check, the check of a file), Bloomberg (the last pull,
    what is stored and what it asked, its problems; the prices, closes, contract dates, the
    Bloomberg check and the details in closed folds) and Diagnosis (one report to paste into Claude
    Code); the Data issues drawer last. Every container is filled by `register_callbacks`."""
    return html.Div(className="market-data", children=[
        # the tab's name as its title, the one status line beside it (look pass 2026-09-30);
        # BODY_ID holds the status line (the first output of the body callback)
        html.Div(className="tab-header", children=[
            about(TAB_TITLE, TAB_ABOUT, level="h2", className="tab-title"),
            html.Div(id=BODY_ID, className="status-line data-status-line", children=message_box("Loading...")),
        ]),
        trades_card(),
        bloomberg_card(),
        diagnosis_card(),
        html.Div(id=ISSUES_ID),
    ])


BBG_TITLE = "Bloomberg"
BBG_ABOUT = ("The last Bloomberg pull, what is stored and what the pull asked of Bloomberg (a value stored is never "
             "asked again), and its problems in full. Folded under it: the prices the book uses and their checks, the "
             "reference closes, the contract dates, the Bloomberg check with its suggested fixes, and the pull step by "
             "step.")
BBG_CHECK_TITLE = "Bloomberg check and fixes"
BBG_CHECK_ABOUT = ("The connection and data checks, then the book's own tickers asked of Bloomberg and compared with "
                   "the app's contract list, and the fixes it suggests, applied here once you tick and confirm them. "
                   "Building the diagnosis report runs the same check.")
BBG_CHECK_NOTE = ("Asks Bloomberg for the book's own tickers and every ticker a pull asks; writes nothing. Its "
                  "suggested fixes are applied only when you tick them and confirm.")
PROBLEMS_FOLD_TITLE = "Prices missing or flagged"
PARSE_TITLE = "Check a file before uploading"
PARSE_ABOUT = ("Check a blotter file before uploading it: every row read exactly as an upload would read it, what it "
               "would be read as, what the upload would do with it against the book on file, and any fill it would "
               "leave on file twice. Nothing is saved.")
PARSE_NOTE = "Reads the file as an upload would; nothing is saved."
DIAG_CARD_TITLE = "Diagnosis"
DIAG_CARD_ABOUT = ("One plain-text report of everything needed to find a Bloomberg problem, to paste into Claude Code: "
                   "what is wrong first, then the code version and the database, the connection and data checks, the "
                   "book's tickers asked of Bloomberg, the last pull's status in full with the backfill's errors and "
                   "what is stored, the library, the missing prices and the last upload. Asks Bloomberg nothing that "
                   "writes, and works on a PC with no Bloomberg.")
DIAG_BUILD_NOTE = "Runs the Bloomberg check in the background, then writes the report; nothing is saved."


def bloomberg_card() -> html.Div:
    """The Bloomberg card (static; its blocks filled by the body callback and the check's own)."""
    return kit.card(id=BBG_CARD_ID, className="data-bbg-card data-block", children=[
        kit.strip([kit.strip_title(BBG_TITLE, BBG_ABOUT)]),
        html.Div(className="data-card-body", children=[
            html.Div(id=BBG_LINE_ID),
            html.Div(id=BBG_PULL_PROBLEMS_ID, className="data-bbg-pull"),
            html.Div(className="data-folds", children=[
                html.Div(id=BBG_ASKED_ID),
                _fold([html.Div(className="data-fold-tools", children=[
                           html.Button("Download CSV", id=PROBLEMS_CSV_ID, n_clicks=0, className="btn btn--ghost",
                                       title="The problems, every column, with their full sentences"),
                           dcc.Download(id=PROBLEMS_DOWNLOAD_ID)]),
                       html.Div(id=PROBLEMS_SLOT_ID, className=kit.SLOT_CLASS),
                       dcc.Store(id=PROBLEMS_STORE_ID, data=[]),
                       dcc.Store(id=PROBLEMS_SORT_ID, storage_type="session")],
                      PROBLEMS_FOLD_TITLE, PROBLEMS_ABOUT, id=MISSING_PANEL_ID, meta_id=PROBLEMS_META_ID, style=_HIDDEN),
                _fold([html.Div(className="data-fold-tools", children=[
                           html.Div(id=MARKS_TOOLS_ID, className="tf-bar-slot", style=_HIDDEN,
                                    children=_marks_search()),
                           html.Button("Clear filters", id=MARKS_CLEAR_ID, n_clicks=0,
                                       className="book-link-button tf-clear", style=_HIDDEN,
                                       title="Every column's filter back to All"),
                           html.Span(id=MARKS_META_ID, className="book-section-meta"),
                           html.Button("Download CSV", id=MARKS_CSV_ID, n_clicks=0, className="btn btn--ghost",
                                       title="The prices showing, every column, at full figures"),
                           dcc.Download(id=MARKS_DOWNLOAD_ID)]),
                       html.Div(id=MARKS_EMPTY_ID),
                       html.Div(id=MARKS_TABLE_WRAP_ID, className=kit.SLOT_CLASS),
                       dcc.Store(id=MARKS_STORE_ID, data=[]),
                       dcc.Store(id=MARKS_FSTORE_ID, storage_type="session"),
                       dcc.Store(id=MARKS_SORT_ID, storage_type="session")],
                      SUSPECT_TITLE, MARKS_ABOUT, id=MARKS_SECTION_ID),
                html.Div(id=PAST_CLOSES_PANEL_ID),
                html.Div(id=CONTRACT_DATES_PANEL_ID),
                _fold([html.Div(className="data-bbg-run", children=[
                           html.Button("Run Bloomberg check", id=BBG_CHECK_BUTTON_ID, n_clicks=0, className="btn",
                                       title=BBG_CHECK_NOTE),
                           html.Span(id=BBG_PROGRESS_ID, className="data-bbg-progress"),
                           html.Button("Download CSV", id=BBG_CSV_ID, n_clicks=0, className="btn btn--ghost",
                                       style=_HIDDEN, title="Every ticker of the last Bloomberg check, every field"),
                           dcc.Download(id=BBG_DOWNLOAD_ID)]),
                       html.Div(id=BBG_SUMMARY_ID),
                       # the suggested fixes: the table, then its buttons (static, hidden by style), then the
                       # dry run's confirm lines or what the apply did
                       html.Div(id=BBG_FIXES_ID, className="data-bbg-fixes"),
                       html.Div(className="data-bbg-run", children=[
                           html.Button("Apply ticked fixes", id=BBG_FIX_APPLY_ID, n_clicks=0, className="btn",
                                       style=_HIDDEN, title="Shows what the ticked fixes change first; nothing is "
                                                            "written until you confirm"),
                           html.Button("Confirm", id=BBG_FIX_CONFIRM_ID, n_clicks=0, className="btn", style=_HIDDEN,
                                       title="Write these fixes to the contract list and rebuild the trades on them"),
                           html.Button("Cancel", id=BBG_FIX_CANCEL_ID, n_clicks=0, className="btn btn--ghost",
                                       style=_HIDDEN, title="Change nothing"),
                       ]),
                       html.Div(id=BBG_FIX_RESULT_ID, className="data-bbg-fix-result"),
                       html.Div(id=BBG_RESULTS_ID, className="data-bbg-results"),
                       dcc.Store(id=BBG_FSTORE_ID, storage_type="session"),
                       dcc.Store(id=BBG_SORT_ID, storage_type="session")],
                      BBG_CHECK_TITLE, BBG_CHECK_ABOUT, id=BBG_CHECK_FOLD_ID, meta_id=BBG_CHECK_META_ID),
                html.Details(id=DIAGNOSTICS_ID, className="book-fold tk-fold-block data-fold", children=[
                    html.Summary(html.Span("Details", className="book-section-title", title=DIAG_ABOUT),
                                 id=DIAG_SUMMARY_ID, n_clicks=0),
                    html.Div(id=DIAG_BODY_ID),
                ]),
            ]),
            dcc.Store(id=BBG_DONE_ID, data=""),
            dcc.Store(id=BBG_FIX_STORE_ID),
            dcc.Interval(id=BBG_FIX_POLL_ID, interval=BBG_POLL_MS, disabled=True),
            dcc.Interval(id=BBG_POLL_ID, interval=BBG_POLL_MS, disabled=True),
            dcc.Store(id=BBG_RUN_STORE_ID),
        ]),
    ])


def diagnosis_card() -> html.Div:
    """The Diagnosis card: "Build diagnosis report" (the Bloomberg check in the background, then
    the report, `ui.diagnostics_runner.diagnosis_text`), the report in a monospace box with Copy to
    clipboard and Download in the strip, hidden until a report is built."""
    return kit.card(id=DIAG_CARD_ID, className="data-diag-card data-block", children=[
        kit.strip([kit.strip_title(DIAG_CARD_TITLE, DIAG_CARD_ABOUT),
                   html.Div(id=DIAG_TOOLS_ID, className="data-diag-tools", style=_HIDDEN, children=[
                       dcc.Clipboard(id=DIAG_COPY_ID, content="", className="btn data-diag-copy",
                                     title="Copy the whole report, to paste into Claude Code"),
                       html.Button("Download", id=DIAG_SAVE_ID, n_clicks=0, className="btn btn--ghost",
                                   title="The report as a text file"),
                       dcc.Download(id=DIAG_DOWNLOAD_ID)])]),
        html.Div(className="data-card-body", children=[
            html.Div(className="data-bbg-run", children=[
                html.Button("Build diagnosis report", id=DIAG_BUILD_ID, n_clicks=0, className="btn",
                            title=DIAG_BUILD_NOTE),
                html.Span(id=DIAG_PROGRESS_ID, className="data-bbg-progress"),
            ]),
            dcc.Textarea(id=DIAG_TEXT_ID, value="", readOnly=True, spellCheck=False, className="data-diag-text",
                         style=_HIDDEN),
        ]),
    ])


def parse_block() -> html.Div:
    """The check of a blotter file, at the bottom of the Blotter card: a file dropped here is read,
    never loaded. Its Clear and Download CSV sit static in its own heading, hidden by style."""
    return html.Div(className="data-parse-block", children=[
        html.Div(className="data-bbg-subhead data-parse-head", children=[
            kit.strip_title(PARSE_TITLE, PARSE_ABOUT),
            html.Button("Clear", id=PARSE_CLEAR_ID, n_clicks=0, className="btn btn--ghost", style=_HIDDEN,
                        title="Clear the file's check"),
            html.Button("Download CSV", id=PARSE_CSV_ID, n_clicks=0, className="btn btn--ghost", style=_HIDDEN,
                        title="Every row of the file's check, every field"),
            dcc.Download(id=PARSE_DOWNLOAD_ID)]),
        dcc.Upload(id=PARSE_UPLOAD_ID, className="data-parse-upload", accept=".csv,.xlsx,.xls", multiple=False,
                   max_size=25 * 1024 * 1024,
                   children=html.Button("Check a blotter file", className="btn", n_clicks=0, title=PARSE_NOTE)),
        dcc.Loading(type="dot", color="#1f5fbf", children=html.Div(id=PARSE_RESULTS_ID)),
        dcc.Store(id=PARSE_STORE_ID),
        dcc.Store(id=PARSE_FSTORE_ID, storage_type="session"),
        dcc.Store(id=PARSE_SORT_ID, storage_type="session"),
    ])


def trades_card() -> html.Div:
    """The Blotter card (id `market-data-trades`, the Blotter tab links here; static, its head and
    tables filled by the body callback and the table's own): the last upload and what it loaded,
    "Rows not loaded or to fix (N)" with its funnel, the duplicates check, then the check of a
    blotter file."""
    return kit.card(id=TRADES_CARD_ID, className="data-trades-card data-block", children=[
        kit.strip([kit.strip_title(TRADES_TITLE, TRADES_ABOUT),
                   html.Button("Download CSV", id=TRADES_CSV_ID, n_clicks=0, className="btn btn--ghost",
                               title="Every row not loaded or to fix, every column, with the upload's own sentences"),
                   dcc.Download(id=TRADES_DOWNLOAD_ID)]),
        html.Div(className="data-card-body", children=[
            html.Div(id=TRADES_HEAD_ID),
            html.Div(id=TRADES_SLOT_ID, className=kit.SLOT_CLASS),
            html.Div(id=DUPES_ID),
            dcc.Store(id=TRADES_STORE_ID, data=[]),
            dcc.Store(id=TRADES_FSTORE_ID, storage_type="session"),
            dcc.Store(id=TRADES_SORT_ID, storage_type="session"),
            parse_block(),
        ]),
    ])


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body (every block, on the header's as-of and a data change), the problems' and the
    marks check's tables (their filter, sort and CSV), and the Bloomberg connection check.
    `get_db_path` is the shell's zero-arg callable returning the database path."""
    import dash
    from dash import ALL
    from ui.tabs.header import AS_OF_STORE_ID as HEADER_AS_OF_STORE_ID

    @app.callback(
        Output(BODY_ID, "children"),
        Output(PROBLEMS_STORE_ID, "data"),
        Output(MISSING_PANEL_ID, "style"),
        Output(MARKS_STORE_ID, "data"),
        Output(MARKS_EMPTY_ID, "children"),
        Output(MARKS_TOOLS_ID, "style"),
        Output(PAST_CLOSES_PANEL_ID, "children"),
        Output(CONTRACT_DATES_PANEL_ID, "children"),
        Output(ISSUES_ID, "children"),
        Output(BBG_LINE_ID, "children"),
        Output(BBG_PULL_PROBLEMS_ID, "children"),
        Output(TRADES_HEAD_ID, "children"),
        Output(TRADES_STORE_ID, "data"),
        Output(DUPES_ID, "children"),
        Output(BBG_ASKED_ID, "children"),
        Input(HEADER_AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(BOOK_REVISION_ID, "data"),
    )
    def _update_body(as_of_date, *_triggers):
        # The Diagnostics fold's body is built by its own callback when the fold is opened, and the
        # marks check's rows stay on the server (`marks_token`): 2026-09-29, performance.
        out = list(render(as_of_date, get_db_path(), diag=False))
        out[3] = marks_token(as_of_date, len(out[3] or []))
        del out[9]          # the Diagnostics body: its own callback
        del out[4]          # the commodity / sector choices: the Price funnel, drawn with the table
        return tuple(x if isinstance(x, (list, dict)) and i in (1, 2, 3, 5, 12) else compact(x)
                     for i, x in enumerate(out))

    @app.callback(Output(TRADES_SLOT_ID, "children"),
                  Input(TRADES_STORE_ID, "data"), Input(TRADES_FSTORE_ID, "data"), Input(TRADES_SORT_ID, "data"))
    def _trades(rows, fstate, sort):
        return compact(trades_view(rows, fstate, sort))

    @app.callback(Output(TRADES_DOWNLOAD_ID, "data"), Input(TRADES_CSV_ID, "n_clicks"),
                  State(TRADES_STORE_ID, "data"), State(TRADES_FSTORE_ID, "data"), State(TRADES_SORT_ID, "data"),
                  prevent_initial_call=True)
    def _trades_csv(n_clicks, rows, fstate, sort):
        if not n_clicks:
            return dash.no_update
        shown = kit.sort_records(data_checks.filter_rows(rows or [], fstate, data_checks.TRADE_FIELDS), sort,
                                 data_checks.TRADE_SORT)
        cols = data_checks.TRADE_CSV_COLUMNS
        frame = pd.DataFrame([{c: r.get(c, "") for c in cols} for r in shown], columns=cols)
        return dcc.send_data_frame(frame.to_csv, "trade_problems.csv", index=False)

    @app.callback(Output(DIAG_BODY_ID, "children"), Input(DIAG_SUMMARY_ID, "n_clicks"),
                  State(HEADER_AS_OF_STORE_ID, "data"), prevent_initial_call=True)
    def _diagnostics(clicks, as_of_date):
        if not clicks or not as_of_date:
            return dash.no_update
        db_path = get_db_path()
        try:
            from data.bloomberg.live import read_status
            status = read_status(db_path)
        except Exception:  # noqa: BLE001 -- an unreadable status file is "no pull recorded"
            status = None
        conn = _connect_readonly(db_path)
        try:
            body = safe_panel(DIAG_TITLE, lambda: diagnostics_body(conn, as_of_date, status))
        finally:
            conn.close()
        return compact(tidy(body))

    @app.callback(Output(PROBLEMS_SLOT_ID, "children"), Output(PROBLEMS_META_ID, "children"),
                  Input(PROBLEMS_STORE_ID, "data"), Input(PROBLEMS_SORT_ID, "data"))
    def _problems(rows, sort):
        rows = rows or []
        if not rows:
            return html.Div(), ""
        red = sum(1 for r in rows if r.get("level") == "red")
        n = len(rows)
        meta = (f" · {n:,} problem{'' if n == 1 else 's'}"
                + ((", all blocking" if red == n else f", {red:,} blocking") if red else ", none blocking"))
        return compact(data_checks.problems_table(rows, sort, PROBLEM_SORT_TYPE)), meta

    @app.callback(Output(MARKS_TABLE_WRAP_ID, "children"), Output(MARKS_META_ID, "children"),
                  Input(MARKS_STORE_ID, "data"), Input(MARKS_FSTORE_ID, "data"),
                  Input(MARKS_SEARCH_ID, "value"), Input(MARKS_SORT_ID, "data"))
    def _marks(token, fstate, search, sort):
        rows = _token_rows(token, get_db_path())
        if not rows:
            return html.Div(), ""
        f = data_checks.normal_marks_filter(fstate)
        shown = data_checks.filter_marks(rows, f["status"], f["group"], search, f)
        options = {"group": marks_filter_options(rows), "source": data_checks.source_options(rows)}
        # the counts are the table's own total row ("All prices · 45 · 2 missing"): said once
        return compact(data_checks.marks_table(shown, sort, MARK_SORT_TYPE, len(rows), f, options)), ""

    @app.callback(Output(MARKS_FSTORE_ID, "data"),
                  Input({"type": data_checks.MARK_COL_TYPE, "part": ALL}, "value"),
                  State(MARKS_FSTORE_ID, "data"), prevent_initial_call=True)
    def _marks_filter(_values, current):
        """One heading's control into the store: only the control touched (a table's re-render
        inserts the whole set, which writes nothing)."""
        from ui.tabs.trade_filter import parse_compare, triggered_values
        cur = data_checks.normal_marks_filter(current)
        new = dict(cur)
        for ident, value in triggered_values():
            part = str((ident or {}).get("part") or "") if isinstance(ident, dict) else ""
            if part in data_checks.MARK_LIST_PARTS:
                new[part] = [str(x) for x in value or []]
            elif part in data_checks.MARK_NUMBER_PARTS:
                new[part] = str(value or "").strip() if parse_compare(value) is not None else ""
        new = data_checks.normal_marks_filter(new)
        return dash.no_update if new == cur else new

    @app.callback(Output(MARKS_FSTORE_ID, "data", allow_duplicate=True), Input(MARKS_CLEAR_ID, "n_clicks"),
                  prevent_initial_call=True)
    def _marks_clear(clicks):
        return dict(data_checks.MARK_FILTER_DEFAULT) if clicks else dash.no_update

    @app.callback(Output(MARKS_CLEAR_ID, "style"), Input(MARKS_FSTORE_ID, "data"))
    def _marks_clear_shown(fstate):
        return {} if data_checks.marks_filtered(fstate) else _HIDDEN

    def _sorter(store_id: str, sort_type: str) -> None:
        @app.callback(Output(store_id, "data"), Input({"type": sort_type, "idx": ALL}, "n_clicks"),
                      State(store_id, "data"), prevent_initial_call=True)
        def _sort(_clicks, current):
            trig = dash.ctx.triggered_id
            if not isinstance(trig, dict) or not kit.clicked(dash.ctx.triggered):
                return dash.no_update
            return kit.next_sort(current, str(trig.get("idx") or ""))

    _sorter(PROBLEMS_SORT_ID, PROBLEM_SORT_TYPE)
    _sorter(MARKS_SORT_ID, MARK_SORT_TYPE)

    @app.callback(Output(MARKS_DOWNLOAD_ID, "data"), Input(MARKS_CSV_ID, "n_clicks"),
                  State(MARKS_STORE_ID, "data"), State(MARKS_FSTORE_ID, "data"),
                  State(MARKS_SEARCH_ID, "value"), State(MARKS_SORT_ID, "data"), State(HEADER_AS_OF_STORE_ID, "data"),
                  prevent_initial_call=True)
    def _marks_csv(n_clicks, token, fstate, search, sort, as_of_date):
        if not n_clicks:
            return None
        rows = _token_rows(token, get_db_path())
        f = data_checks.normal_marks_filter(fstate)
        shown = kit.sort_records(data_checks.filter_marks(rows, f["status"], f["group"], search, f), sort,
                                 data_checks.MARK_SORT)
        cols = data_checks.MARK_CSV_COLUMNS
        frame = pd.DataFrame([{k: r.get(k) for k in cols} for r in shown], columns=cols)
        return dcc.send_data_frame(frame.to_csv, f"marks-check-{as_of_date or 'today'}.csv", index=False)

    @app.callback(Output(PROBLEMS_DOWNLOAD_ID, "data"), Input(PROBLEMS_CSV_ID, "n_clicks"),
                  State(PROBLEMS_STORE_ID, "data"), State(PROBLEMS_SORT_ID, "data"),
                  State(HEADER_AS_OF_STORE_ID, "data"), prevent_initial_call=True)
    def _problems_csv(n_clicks, rows, sort, as_of_date):
        if not n_clicks or not rows:
            return None
        shown = kit.sort_records(rows, sort, data_checks.PROBLEM_SORT)
        cols = data_checks.PROBLEM_CSV_COLUMNS
        frame = pd.DataFrame([{k: r.get(k) for k in cols} for r in shown], columns=cols)
        return dcc.send_data_frame(frame.to_csv, f"data-problems-{as_of_date or 'today'}.csv", index=False)

    _sorter(BBG_SORT_ID, BBG_SORT_TYPE)
    _sorter(PARSE_SORT_ID, PARSE_SORT_TYPE)
    _sorter(TRADES_SORT_ID, TRADES_SORT_TYPE)

    def _list_filter(store_id: str, col_type: str, fields: Tuple[str, ...]) -> None:
        @app.callback(Output(store_id, "data"), Input({"type": col_type, "part": ALL}, "value"),
                      State(store_id, "data"), prevent_initial_call=True)
        def _filter(_values, current):
            """One heading's tick list into the store: only the control touched."""
            from ui.tabs.trade_filter import triggered_values
            cur = data_checks.normal_list_filter(current, fields)
            new = dict(cur)
            for ident, value in triggered_values():
                part = str((ident or {}).get("part") or "") if isinstance(ident, dict) else ""
                if part in fields:
                    new[part] = [str(x) for x in value or []]
            return dash.no_update if new == cur else new

    _list_filter(BBG_FSTORE_ID, BBG_COL_TYPE, data_checks.TICKER_FIELDS)
    _list_filter(PARSE_FSTORE_ID, PARSE_COL_TYPE, data_checks.PARSE_FIELDS)
    _list_filter(TRADES_FSTORE_ID, TRADES_COL_TYPE, data_checks.TRADE_FIELDS)

    # ---- "Run Bloomberg check" and "Build diagnosis report" (2026-09-30): one background run (the
    # check, then the report), polled while it goes
    @app.callback(Output(BBG_RUN_STORE_ID, "data"), Input(BBG_CHECK_BUTTON_ID, "n_clicks"),
                  Input(DIAG_BUILD_ID, "n_clicks"), prevent_initial_call=True)
    def _bbg_start(n_check, n_build):
        if not (n_check or n_build):
            return dash.no_update
        import time
        from ui import diagnostics_runner
        started, why = diagnostics_runner.start(get_db_path())
        return {"at": time.time(), "started": started, "why": why}

    @app.callback(Output(BBG_RESULTS_ID, "children"), Output(BBG_PROGRESS_ID, "children"),
                  Output(BBG_POLL_ID, "disabled"), Output(BBG_CHECK_BUTTON_ID, "disabled"),
                  Output(BBG_CSV_ID, "style"),
                  Output(BBG_SUMMARY_ID, "children"), Output(BBG_DONE_ID, "data"),
                  Output(DIAG_PROGRESS_ID, "children"), Output(DIAG_BUILD_ID, "disabled"),
                  Output(BBG_CHECK_META_ID, "children"),
                  Input(BBG_RUN_STORE_ID, "data"), Input(BBG_POLL_ID, "n_intervals"),
                  Input(BBG_FSTORE_ID, "data"), Input(BBG_SORT_ID, "data"), State(BBG_DONE_ID, "data"))
    def _bbg_results(run, _ticks, fstate, sort, done_before):
        from ui import diagnostics_runner
        state = diagnostics_runner.state(get_db_path())
        refused = (run or {}).get("why") if run and not run.get("started") else ""
        results, progress = bbg_check_view(state, fstate, sort, with_summary=False)
        going = bool(state and state.get("running"))
        if refused and not going:        # a pull running: the sentence; another run going: its progress
            progress = html.Span(refused, className="cell-amber")
        book = (state or {}).get("book") or {}
        has_rows = bool(book.get("rows"))
        done = "running" if going else (str(state.get("finished_at") or "done") if state and book else "")
        return (compact(results), progress, not going, going, ({} if has_rows else _HIDDEN),
                compact(tidy(bbg_summary(state))), (dash.no_update if done == (done_before or "") else done),
                diagnosis_progress(state, refused), going, check_meta(state))

    # ---- the check's suggested fixes: tick, Apply (a dry run), Confirm (2026-09-30)
    @app.callback(Output(BBG_FIXES_ID, "children"), Output(BBG_FIX_RESULT_ID, "children"),
                  Output(BBG_FIX_APPLY_ID, "style"), Output(BBG_FIX_CONFIRM_ID, "style"),
                  Output(BBG_FIX_CANCEL_ID, "style"), Output(BBG_FIX_POLL_ID, "disabled"),
                  Output(BBG_FIX_APPLY_ID, "disabled"),
                  Input(BBG_DONE_ID, "data"), Input(BBG_FIX_STORE_ID, "data"), Input(BBG_FIX_POLL_ID, "n_intervals"))
    def _bbg_fixes(_done, _token, _ticks):
        from ui import diagnostics_runner
        db = get_db_path()
        state = diagnostics_runner.state(db)
        fx = diagnostics_runner.fix_state(db)
        table, result, show_apply, show_confirm, git_going = fixes_view(state, fx)
        return (compact(table), compact(result), {} if show_apply else _HIDDEN,
                {} if show_confirm else _HIDDEN, {} if show_confirm else _HIDDEN, not git_going,
                bool(fx and fx.get("stage") == "confirm"))

    @app.callback(Output(BBG_FIX_STORE_ID, "data"), Input(BBG_FIX_APPLY_ID, "n_clicks"),
                  State({"type": FIX_TICK_TYPE, "idx": ALL}, "value"),
                  State({"type": FIX_PICK_TYPE, "idx": ALL}, "value"), prevent_initial_call=True)
    def _bbg_fix_apply(n_clicks, _ticks, _picks):
        if not n_clicks:
            return dash.no_update
        import time
        from ui import diagnostics_runner
        db = get_db_path()
        state = diagnostics_runner.state(db) or {}
        fixes = list(((state.get("book") or {}).get("fixes")) or [])
        ticks = {str((s.get("id") or {}).get("idx")): s.get("value") for s in dash.ctx.states_list[0]}
        picks = {str((s.get("id") or {}).get("idx")): s.get("value") for s in dash.ctx.states_list[1]}
        diagnostics_runner.prepare_fixes(db, ticked_fixes(fixes, ticks, picks))
        return {"at": time.time(), "step": "dry"}

    @app.callback(Output(BBG_FIX_STORE_ID, "data", allow_duplicate=True), Input(BBG_FIX_CONFIRM_ID, "n_clicks"),
                  prevent_initial_call=True)
    def _bbg_fix_confirm(n_clicks):
        if not n_clicks:
            return dash.no_update
        import time
        from ui import diagnostics_runner
        diagnostics_runner.confirm_fixes(get_db_path())
        return {"at": time.time(), "step": "confirm"}

    @app.callback(Output(BBG_FIX_STORE_ID, "data", allow_duplicate=True), Input(BBG_FIX_CANCEL_ID, "n_clicks"),
                  prevent_initial_call=True)
    def _bbg_fix_cancel(n_clicks):
        if not n_clicks:
            return dash.no_update
        import time
        from ui import diagnostics_runner
        diagnostics_runner.cancel_fixes(get_db_path())
        return {"at": time.time(), "step": "cancel"}

    # ---- the diagnosis report (2026-09-30): shown once a run has written it, copied or downloaded whole
    @app.callback(Output(DIAG_TEXT_ID, "value"), Output(DIAG_TEXT_ID, "style"), Output(DIAG_COPY_ID, "content"),
                  Output(DIAG_TOOLS_ID, "style"), Input(BBG_DONE_ID, "data"))
    def _diag_text(_done):
        from ui import diagnostics_runner
        state = diagnostics_runner.state(get_db_path()) or {}
        text = str(state.get("report") or "")
        if not text or state.get("running"):
            return "", _HIDDEN, "", _HIDDEN
        return text, {}, text, {}

    @app.callback(Output(DIAG_DOWNLOAD_ID, "data"), Input(DIAG_SAVE_ID, "n_clicks"), prevent_initial_call=True)
    def _diag_save(n_clicks):
        from ui import diagnostics_runner
        state = diagnostics_runner.state(get_db_path()) or {}
        if not n_clicks or not state.get("report"):
            return dash.no_update
        return {"content": state["report"], "filename": diagnostics_runner.report_filename(state),
                "type": "text/plain"}

    @app.callback(Output(BBG_DOWNLOAD_ID, "data"), Input(BBG_CSV_ID, "n_clicks"),
                  State(BBG_FSTORE_ID, "data"), State(BBG_SORT_ID, "data"), prevent_initial_call=True)
    def _bbg_csv(n_clicks, fstate, sort):
        from ui import diagnostics_runner
        state = diagnostics_runner.state(get_db_path()) or {}
        rows = ((state.get("book") or {}).get("rows")) or []
        if not n_clicks or not rows:
            return dash.no_update
        shown = kit.sort_records(data_checks.filter_rows(rows, fstate, data_checks.TICKER_FIELDS), sort,
                                 data_checks.TICKER_SORT)
        cols = data_checks.TICKER_CSV_COLUMNS
        frame = pd.DataFrame([{k: r.get(k) for k in cols} for r in shown], columns=cols)
        stamp = str(state.get("finished_at") or "")[:10] or "today"
        return dcc.send_data_frame(frame.to_csv, f"bloomberg-check-{stamp}.csv", index=False)

    # ---- the Parsing card: "Check a blotter file"
    @app.callback(Output(PARSE_STORE_ID, "data"), Output(PARSE_UPLOAD_ID, "contents"),
                  Input(PARSE_UPLOAD_ID, "contents"), State(PARSE_UPLOAD_ID, "filename"),
                  prevent_initial_call=True)
    def _parse_file(contents, filename):
        if not contents:
            return dash.no_update, dash.no_update
        # the Upload's contents go back to None, so the very same file can be checked again
        return parse_upload(contents, filename, get_db_path()), None

    @app.callback(Output(PARSE_STORE_ID, "data", allow_duplicate=True), Input(PARSE_CLEAR_ID, "n_clicks"),
                  prevent_initial_call=True)
    def _parse_clear(n_clicks):
        return None if n_clicks else dash.no_update

    @app.callback(Output(PARSE_RESULTS_ID, "children"), Output(PARSE_CLEAR_ID, "style"),
                  Output(PARSE_CSV_ID, "style"),
                  Input(PARSE_STORE_ID, "data"), Input(PARSE_FSTORE_ID, "data"), Input(PARSE_SORT_ID, "data"))
    def _parse_results(result, fstate, sort):
        if not result:
            return html.Div(), _HIDDEN, _HIDDEN
        shown = {} if result.get("rows") else _HIDDEN
        return compact(parse_view(result, fstate, sort)), {}, shown

    @app.callback(Output(PARSE_DOWNLOAD_ID, "data"), Input(PARSE_CSV_ID, "n_clicks"),
                  State(PARSE_STORE_ID, "data"), State(PARSE_FSTORE_ID, "data"), State(PARSE_SORT_ID, "data"),
                  prevent_initial_call=True)
    def _parse_csv(n_clicks, result, fstate, sort):
        rows = (result or {}).get("rows") or []
        if not n_clicks or not rows:
            return dash.no_update
        recs = data_checks.parse_records(result)
        shown = kit.sort_records(data_checks.filter_rows(recs, fstate, data_checks.PARSE_FIELDS), sort,
                                 data_checks.PARSE_SORT)
        cols = data_checks.PARSE_CSV_COLUMNS
        frame = pd.DataFrame([{k: r.get(k) for k in cols} for r in shown], columns=cols)
        name = str(result.get("filename") or "blotter").rsplit(".", 1)[0]
        return dcc.send_data_frame(frame.to_csv, f"parse-check-{name}.csv", index=False)


# ---- the Bloomberg check's and the parsing check's views (2026-09-30)
def _utc_words(iso) -> str:
    """'Wed 30 Sep 09:12 HK' from a UTC ISO stamp, the date and Hong Kong time as every pull time
    (`feed_controls.pull_time`; '' when unreadable)."""
    from ui.feed_controls import pull_time
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return pull_time(when.isoformat())


def _sub(title: str, hover: str, extra=None) -> html.Div:
    kids = [kit.strip_title(title, hover)]
    if extra is not None:
        kids.append(extra)
    return html.Div(kids, className="data-bbg-subhead")


def diagnosis_progress(state: Optional[dict], refused: str = "") -> Any:
    """The Diagnosis card's words beside its button: the run's progress while it goes, "Built Wed
    30 Sep 18:20 HK" once a report is on hand, the refusal's sentence when a pull is running."""
    if refused and not (state or {}).get("running"):
        return html.Span(refused, className="cell-amber")
    if not state:
        return ""
    if state.get("running"):
        done, total = int(state.get("done") or 0), int(state.get("total") or 0)
        words = cap(str(state.get("words") or "Running"))
        return html.Span(f"{words}: {done} of {total} requests" if total else words, className="data-bbg-running")
    if state.get("report"):
        from ui.diagnostics_runner import no_bloomberg
        when = _utc_words(state.get("report_at") or state.get("finished_at"))
        extra = "No Bloomberg connection on this PC" if no_bloomberg(state.get("checks") or [],
                                                                      state.get("book")) else ""
        return html.Span([f"Built {when}" if when else "Built", html.Span(f" · {extra}", className="cell-amber")
                          if extra else ""])
    return ""


def check_meta(state: Optional[dict]) -> str:
    """The Bloomberg check fold's meta: " · Running", " · 3 fixes suggested", " · No fix to make"."""
    if not state:
        return ""
    if state.get("running"):
        return " · Running"
    fixes = ((state.get("book") or {}).get("fixes")) or []
    if not (state.get("book") or {}).get("ok"):
        return " · Could not reach Bloomberg" if (state.get("book") or {}).get("reachable") is False else ""
    return f" · {len(fixes):,} fix{'' if len(fixes) == 1 else 'es'} suggested" if fixes else " · No fix to make"


def bbg_summary(state: Optional[dict]) -> html.Div:
    """The last check's summary line with its time (and its reason when it could not run);
    empty before the first run and while one runs."""
    book = (state or {}).get("book") or {}
    if not book or (state or {}).get("running"):
        return html.Div()
    parts: list = []
    summary = cap(plain_words(book.get("summary") or book.get("reason") or ""))
    when = _utc_words(book.get("finished_at") or (state or {}).get("finished_at"))
    seconds = book.get("seconds")
    tail = " · ".join(x for x in (when, f"{float(seconds):,.1f} s" if seconds else "") if x)
    parts.append(html.Div([html.Span(summary or "The check finished.",
                                     className=None if book.get("ok") else "cell-amber"),
                           html.Span(f" · {tail}" if tail else "", className="cell-unit")],
                          className="data-bbg-summary"))
    if not book.get("ok") and book.get("reason") and book.get("reason") != book.get("summary"):
        parts.append(html.Div(cap(plain_words(book["reason"])), className="data-bbg-summary cell-amber"))
    return html.Div(parts)


def bbg_check_view(state: Optional[dict], fstate: Optional[dict], sort: Optional[dict],
                   with_summary: bool = True) -> Tuple[html.Div, Any]:
    """(the results block, the progress words) of the Bloomberg check's latest run in this process
    (`ui.diagnostics_runner.state`): nothing before the first press; while it runs the progress
    ("Asking Bloomberg for 39 of the book's tickers: 2 of 4 requests") and the connection checks as
    soon as they are in; after it the summary with its time (`bbg_summary`, left out with
    `with_summary` False: the card draws it above the fixes), the connection checks and the book's
    tickers (problems first, Status and Area funnels; their Download CSV in the card's strip)."""
    if not state:
        return html.Div(), ""
    running = bool(state.get("running"))
    checks = state.get("checks") or []
    book = state.get("book") or {}
    parts: list = []
    if running:
        done, total = int(state.get("done") or 0), int(state.get("total") or 0)
        words = cap(str(state.get("words") or "Running"))
        progress: Any = html.Span(f"{words}: {done} of {total} requests" if total else words,
                                  className="data-bbg-running")
    else:
        when = _utc_words(state.get("finished_at"))
        progress = f"Last run {when}" if when else ""
    if book and with_summary:
        parts.append(bbg_summary(state))
    if checks:
        parts += [_sub("Connection and data checks", "The fast checks on this PC: the Bloomberg session, the official "
                                                     "sources, the coverage of the book's marks, the last pull."),
                  data_checks.connection_table(checks)]
    rows = book.get("rows") or []
    if rows:
        shown = data_checks.filter_rows(rows, fstate, data_checks.TICKER_FIELDS)
        parts += [_sub("The book's tickers", "Each ticker the book needs, asked of Bloomberg on the press and compared "
                                             "with the app's own contract list: problems first."),
                  data_checks.ticker_table(shown, sort, BBG_SORT_TYPE, fstate, BBG_COL_TYPE, len(rows), rows)]
    return html.Div(parts), progress


def parse_upload(contents, filename, db_path) -> dict:
    """The parsing check of an uploaded file (`data.ingest.parse_check.check_file`); a file that
    cannot be decoded comes back as {ok: False, error} like any other failure."""
    try:
        from data.ingest.parse_check import check_file
        from data.ingest.upload import decode
        return check_file(decode(contents), str(filename or "file"), db_path)
    except Exception as exc:  # noqa: BLE001 -- the card says why
        return {"ok": False, "error": str(exc) or type(exc).__name__, "filename": str(filename or ""),
                "summary": "", "counts": {}, "merge": {}, "file_notes": [], "rows": []}


def parse_view(result: Optional[dict], fstate: Optional[dict], sort: Optional[dict]) -> html.Div:
    """The parsing check's summary sentence, the file's notes, the error in one line when the file
    could not be read, and the rows (problems first; Read as, Upload would and Status funnels)."""
    r = result or {}
    parts: list = []
    name = str(r.get("filename") or "")
    if not r.get("ok"):
        error = cap(plain_words(r.get("error") or "the file could not be read"))
        parts.append(html.Div(f"{name}: {error}" if name else error, className="data-parse-summary cell-red"))
    if r.get("summary"):
        parts.append(html.Div([html.Span(name, className="data-parse-file"), html.Span(" · ", className="cell-unit"),
                               html.Span(cap(r["summary"]))] if name else cap(r["summary"]),
                              className="data-parse-summary"))
    notes = [n for n in r.get("file_notes") or [] if n]
    if notes:
        parts.append(html.Ul([html.Li(cap(str(n))) for n in notes], className="data-parse-notes"))
    recs = data_checks.parse_records(r)
    if recs:
        shown = data_checks.filter_rows(recs, fstate, data_checks.PARSE_FIELDS)
        parts.append(data_checks.parse_table(shown, sort, PARSE_SORT_TYPE, fstate, PARSE_COL_TYPE, len(recs), recs))
    return tidy(html.Div(parts, className="data-parse-results"))


# ---- the Bloomberg check's suggested fixes, applied from the card (2026-09-30)
FIXES_TITLE = "Suggested fixes"
FIXES_ABOUT = ("Where Bloomberg disagrees with the app's contract list, or confirms a contract still to be confirmed: one "
               "row per change. Tick the ones to take and press Apply ticked fixes: you see what changes before "
               "anything is written, and nothing is written until you confirm.")
FIXES_NONE = "Every ticker the book uses is confirmed by Bloomberg"
FIX_VERDICT_WORDS = {"OK": ("Confirmed", "green"), "NOT_FOUND": ("Not found", "red"), "NO_PRICE": ("No price", "amber"),
                     "NO_ANSWER": ("No answer", "amber"), "CURRENCY_MISMATCH": ("Currency differs", "amber"),
                     "SCALE_MISMATCH": ("Price scale differs", "amber"),
                     "EXCHANGE_MISMATCH": ("Exchange differs", "amber"), "NAME_CHECK": ("Name to check", "amber")}
FIX_FIELD_WORDS = {"bbg_root": "Bloomberg ticker", "bbg_yellow_key": "Bloomberg market sector", "currency": "Currency",
                   "contract_size": "Contract size", "size_unit": "Size unit", "quote_unit": "Price unit",
                   "price_scale": "Price scale", "delivery": "Delivery"}


def _fix_name(fix: dict) -> str:
    """The contract's plain name ('COMEX copper'), never its root id."""
    name = str(fix.get("root_name") or "").strip()
    if name:
        return cap(plain_ids(name))
    rid = str(fix.get("root_id") or "")
    try:
        from data.contracts import load_roots
        root = load_roots().get(rid)
    except Exception:  # noqa: BLE001 -- the short id then
        root = None
    return cap(short_root_name(root, rid))


def fix_effect(fix: dict) -> str:
    """What the fix changes, in plain words: the check's own `effect`, else from the field."""
    effect = str(fix.get("effect") or "").strip()
    if effect:
        return cap(plain_ids(effect))
    field = str(fix.get("field") or "")
    cur, sug = str(fix.get("current") or ""), str(fix.get("suggested") or "")
    if field == "bbg_verified":
        return "Confirmed by Bloomberg" if sug.lower() in ("true", "1", "yes") else "No longer confirmed by Bloomberg"
    if field == "bbg_root":
        return "Bloomberg ticker changes"
    words = FIX_FIELD_WORDS.get(field, cap(field.replace("_", " ")))
    return f"{words} {cur or MISSING} → {sug or MISSING}"


def _number_words(text) -> str:
    try:
        return format(float(text), ",.10g")
    except (TypeError, ValueError):
        return str(text or MISSING)


def ticked_fixes(fixes: List[dict], ticks: Dict[str, Any], picks: Dict[str, Any]) -> List[dict]:
    """The fixes ticked on screen (`ticks` {idx: the tick's value}), each with its pick
    (`picks` {idx: the candidate chosen}) as its `suggested`."""
    out = []
    for i, fix in enumerate(fixes or []):
        if not ticks.get(str(i)):
            continue
        row = dict(fix)
        pick = picks.get(str(i))
        if pick not in (None, ""):
            row["suggested"] = str(pick)
        out.append(row)
    return out


def _fix_key(row: dict) -> Tuple[str, str]:
    return str(row.get("root_id") or "").upper(), str(row.get("field") or "").lower()


def fixes_table(fixes: List[dict], chosen: Optional[List[dict]] = None) -> html.Table:
    """One row per fix: a tick (pre-ticked where the check says apply, or as ticked for the dry
    run), Contract (the root id on hover), What changes (Bloomberg's reason on hover; a pick of the
    candidates where Bloomberg offers several), Bloomberg says."""
    cols: Tuple[kit.Column, ...] = (
        ("tick", "Apply", "l", "Ticked fixes are applied when you press Apply ticked fixes and confirm.", False),
        ("name", "Contract", "l", "The contract the fix is for.", False),
        ("effect", "What changes", "l", "What the fix changes in the app's contract list; Bloomberg's own words "
                                        "on hover.", False),
        ("verdict", "Bloomberg says", "l", "What Bloomberg's answer showed.", False))
    picked = {_fix_key(r): r for r in chosen or []}
    body = []
    for i, fix in enumerate(fixes):
        mine = picked.get(_fix_key(fix)) if chosen is not None else None
        ticked = (mine is not None) if chosen is not None else str(fix.get("apply") or "").strip().lower() == "yes"
        reason = cap(plain_words(str(fix.get("reason") or ""))) or None
        effect: list = [html.Span(fix_effect(fix))]
        choices = [c for c in fix.get("choices") or [] if str(c.get("suggested") or "")]
        if choices:
            value = str((mine or {}).get("suggested") or fix.get("suggested") or choices[0]["suggested"])
            if value not in {str(c["suggested"]) for c in choices}:
                value = str(choices[0]["suggested"])
            effect.append(dcc.Dropdown(
                id={"type": FIX_PICK_TYPE, "idx": i}, clearable=False, searchable=False, value=value,
                options=[{"label": cap(plain_ids(str(c.get("label") or c["suggested"]))), "value": str(c["suggested"])}
                         for c in choices],
                className="data-fix-pick", style={"minWidth": "260px", "marginTop": "4px"}))
        verdict = str(fix.get("verdict") or "")
        words, level = FIX_VERDICT_WORDS.get(verdict, (cap(verdict.replace("_", " ").lower()) or MISSING, "grey"))
        body.append(html.Tr([
            kit.td(dcc.Checklist(id={"type": FIX_TICK_TYPE, "idx": i}, options=[{"label": "", "value": "yes"}],
                                 value=["yes"] if ticked else [], className="data-fix-tick"), left=True),
            kit.td(_fix_name(fix), left=True, title=str(fix.get("root_id") or "") or None),
            kit.td(effect, left=True, title=reason),
            kit.td(kit.chip(words, level, reason), left=True)]))
    return tidy(kit.table(kit.head(cols, None, "data-bbg-none"), body, className="tk-small data-bbg-fixes-table"))


def _lines(items: List[Any], className: str = "") -> html.Ul:
    return html.Ul([html.Li(x) for x in items], className=" ".join(c for c in ("data-parse-notes", className) if c))


def _applied_lines(entries: List[dict], rows: List[dict], trades: Dict[str, int]) -> Tuple[List[Any], int]:
    """One line per fix taken ('COMEX copper: confirmed by Bloomberg'), an amber line where the
    value of one price point changes, with the trades on file it reaches; (lines, multiplier changes)."""
    by_key = {_fix_key(r): r for r in rows or []}
    out: List[Any] = []
    moved = 0
    said: set = set()                      # the price point's change said once per contract
    for e in entries or []:
        row = by_key.get(_fix_key(e)) or {"root_id": e.get("root_id"), "field": e.get("field"),
                                          "current": e.get("before"), "suggested": e.get("after")}
        name = _fix_name(row)
        taken = dict(row, current=e.get("before", row.get("current")), suggested=e.get("after", row.get("suggested")))
        out.append(html.Span(f"{name}: {fix_effect(taken)}", title=str(e.get("root_id") or "") or None))
        before, after = e.get("multiplier_before"), e.get("multiplier_after")
        try:
            changed = before not in (None, "") and after not in (None, "") and float(before) != float(after)
        except (TypeError, ValueError):
            changed = False
        if changed and str(e.get("root_id") or "") not in said:
            said.add(str(e.get("root_id") or ""))
            moved += 1
            n = int((trades or {}).get(str(e.get("root_id") or ""), 0))
            reach = (f"the P&L of {n:,} trade{'' if n == 1 else 's'} on {name} changes" if n
                     else f"no trade on file is on {name}")
            out.append(html.Span(f"{name}: the value of one price point changes from {_number_words(before)} to "
                                 f"{_number_words(after)}: {reach}.", className="cell-amber"))
    return out, moved


def _refused_lines(entries: List[dict], rows: List[dict]) -> List[Any]:
    by_key = {_fix_key(r): r for r in rows or []}
    out = []
    for e in entries or []:
        row = by_key.get(_fix_key(e)) or {"root_id": e.get("root_id"), "field": e.get("field")}
        why = plain_words(str(e.get("why") or "refused"))
        out.append(html.Span(f"{_fix_name(row)}: {fix_effect(row)}, not applied: {why}", className="cell-red",
                             title=str(e.get("root_id") or "") or None))
    return out


def _fix_result(fx: dict) -> Tuple[list, bool]:
    """(the blocks under the fixes' buttons, the commit still running) from the fixes' state."""
    parts: list = []
    stage = str(fx.get("stage") or "")
    rows = fx.get("rows") or []
    if stage == "confirm":
        dry = fx.get("dry") or {}
        applied = dry.get("applied") or []
        lines, moved = _applied_lines(applied, rows, fx.get("trades") or {})
        k = len(applied)
        head = (f"Apply {k:,} fix{'' if k == 1 else 'es'} to the contract list?"
                + (f" The value of a price point changes on {moved:,} contract{'s' if moved != 1 else ''}, so "
                   "their P&L moves." if moved else ""))
        parts.append(html.Div(head, className="data-bbg-summary" + (" cell-amber" if moved else "")))
        parts.append(_lines(lines + _refused_lines(dry.get("refused") or [], rows)))
    elif stage == "applied":
        result = fx.get("result") or {}
        applied = result.get("applied") or []
        lines, _moved = _applied_lines(applied, rows, fx.get("trades") or {})
        k = len(applied)
        written = bool(k and result.get("written"))
        only_confirmed = written and all(str(e.get("field") or "") == "bbg_verified" for e in applied)
        if only_confirmed:
            names = ", ".join(sorted({_fix_name(e) for e in applied}))
            head = f"Marked as confirmed by Bloomberg: {names}."
        elif written:
            head = f"Applied {k:,} fix{'' if k == 1 else 'es'} to the contract list."
        else:
            head = "No fix was applied: the contract list is unchanged."
        parts.append(html.Div(head, className="data-bbg-summary" + ("" if written else " cell-amber")))
        extra: List[Any] = []
        rebuild = fx.get("rebuild") or {}
        if rebuild.get("sentence"):
            extra.append(cap(plain_words(str(rebuild["sentence"]))))
        if rebuild.get("error"):
            extra.append(html.Span(cap(plain_words(str(rebuild["error"]))), className="cell-red"))
        for f in rebuild.get("failed") or []:
            extra.append(html.Span(f"Trade {f.get('trade_id', '')}: {plain_words(str(f.get('why') or 'not rebuilt'))}",
                                   className="cell-red"))
        parts.append(_lines(([] if only_confirmed else lines) + _refused_lines(result.get("refused") or [], rows)
                            + extra))
        if written and not only_confirmed:
            # a new Bloomberg root gives the trades a new contract id: their prices come with the next pull
            parts.append(html.Div("Press Pull Bloomberg now to price them.", className="data-bbg-summary cell-amber"))
        git = fx.get("git") or {}
        if git.get("running"):
            parts.append(html.Div("Committing the contract list to git…", className="data-bbg-summary cell-unit"))
        elif git.get("line"):
            ok = "committed and pushed" in str(git["line"])
            parts.append(html.Div(cap(str(git["line"])), className="data-bbg-summary" + ("" if ok else " cell-amber")))
        parts.append(html.Div("Run Bloomberg check again to see what is left.", className="data-bbg-summary cell-unit"))
    if fx.get("why"):
        parts.append(html.Div(cap(plain_words(str(fx["why"]))), className="data-bbg-summary cell-amber"))
    return parts, bool((fx.get("git") or {}).get("running"))


def fixes_view(state: Optional[dict], fx: Optional[dict]) -> Tuple[Any, Any, bool, bool, bool]:
    """(the fixes block, the result block, Apply shown, Confirm and Cancel shown, the commit
    running) of the last check (`diagnostics_runner.state`) and its fixes (`fix_state`). Nothing
    before a check, while one runs, or when the check could not give fixes; one line when it
    found none."""
    fx = fx or {}
    result, git_going = _fix_result(fx) if fx else ([], False)
    book = (state or {}).get("book") or {}
    if not book or (state or {}).get("running") or not book.get("ok") or "fixes" not in book:
        return html.Div(), tidy(html.Div(result)), False, False, git_going
    fixes = list(book.get("fixes") or [])
    stage = str(fx.get("stage") or "")
    if stage == "applied":                 # the list is stale once written: the check runs again
        return html.Div(), tidy(html.Div(result)), False, False, git_going
    if not fixes:
        unverified = [str(r) for r in book.get("unverified_roots") or [] if r]
        if unverified:
            names = ", ".join(_fix_name({"root_id": r}) for r in unverified)
            line = html.Div(f"{len(unverified):,} contract{'s' if len(unverified) != 1 else ''} of the book "
                            f"{'is' if len(unverified) == 1 else 'are'} still to be confirmed ({names}): Bloomberg "
                            "suggested no fix for them.", className="data-bbg-summary cell-amber")
        else:
            line = html.Div(FIXES_NONE, className="data-bbg-summary")
        return tidy(line), tidy(html.Div(result)), False, False, git_going
    chosen = fx.get("rows") or None      # as ticked for the last dry run, else the check's own ticks
    block = html.Div([_sub(f"{FIXES_TITLE} ({len(fixes):,})", FIXES_ABOUT), fixes_table(fixes, chosen)])
    return tidy(block), tidy(html.Div(result)), True, stage == "confirm", git_going
