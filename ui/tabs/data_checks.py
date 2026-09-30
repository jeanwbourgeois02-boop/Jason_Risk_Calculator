"""The Data tab's checks (Phase G, round 2, 2026-09-29): "Can I trust today's numbers?"

The design doc's "Table specs", Data:

- **Status line**: Last pull (ok / N problems) · Marks 43 of 47 · Reference closes Daily ✓ 5d ✓
  MTD ✓ YTD ✗ · Contract dates 9 from Bloomberg, 2 estimated; each green or amber, each a jump to
  its detail.
- **Problems** (only when any): every official price the book needs that is missing or failed a
  check (`data.bloomberg.inventory.mark_checks`, status MISSING or CHECK), the last pull's failed
  and partial steps and the backfill's errors (the status file), and any trade left out of the
  P&L for another reason; missing first. Status (red Missing / amber Check) · Price (the contract
  with its exchange) · Problem (plain words) · What it blocks (`blocks_what`).
- **Marks check**: every official price the book uses on the date, MISSING, CHECK, OK, then by
  trade: Status · Price · Mark · Source · Time · Prev close (with its date) · Change and % ·
  Arrived ✓/✗ · Fresh ✓/✗ · Sane move ✓/✗ · Units ✓/✗ · Trades; a cross's sentence on hover. A
  flagged price stays the official price: this only reports it.
- **Diagnostics** (folded): the last pull step by step (status["steps"]), the backfill's errors,
  values that were not numbers and requests, the curves' and vols' left-outs.

Every figure is the engine's or the status file's own: nothing is recomputed, and nothing here
asks Bloomberg for anything or writes a mark.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
from dash import html

from ui.tabs import data_kit as kit
from ui.tabs.formatting import (
    cap, cap_parts, tidy,
    MINUS, MISSING, is_fx_pair, missing_cell, parse_contract_id, plain_words, price_text, short_date,
)

STATUS_ORDER = {"MISSING": 0, "CHECK": 1, "OK": 2}
STATUS_WORDS = {"MISSING": "Missing", "CHECK": "Check", "OK": "OK"}
STATUS_LEVEL = {"MISSING": "red", "CHECK": "amber", "OK": "green"}

# What a failed or partial pull step leaves the book with, in plain words (display only).
STEP_EFFECT = {
    "contract_dates": "Contract dates stay estimated until a pull stores Bloomberg's",
    "requests": "Nothing was asked of Bloomberg: every price stays as it was on file",
    "spot": "FX spots and USD conversions use the last closes on file",
    "forwards": "FX forwards are priced off the nearest marks on file",
    "futures": "Futures and option prices use the last closes on file",
    "write_marks": "The prices fetched were not saved: the book runs on the marks already on file",
    "lme": "LME tickets are priced off the nearest marks on file",
    "curves": "FX option Greeks use older discount curves",
    "vol": "FX option prices use older vols",
    "options": "Option Greeks were not updated",
    "ledger": "Settled trades are shown at the figure they will freeze at, not frozen yet",
    "recalc": "Option Greeks were not re-priced",
}


# ---------------------------------------------------------------------------- names
def _roots() -> dict:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- names fall back to the ids
        return {}


def _commodity(root) -> str:
    try:
        from engine.spreads.trades import commodity_words
        words = commodity_words(root)
    except Exception:  # noqa: BLE001
        words = str(getattr(root, "subsector", "") or "")
    return words[:1].upper() + words[1:] if words else ""


def price_name(instrument_id: str, mark_type: str, settle_date: str, root_id: str, roots: dict) -> str:
    """The price in plain words with its exchange: 'COMEX Silver Dec26', 'LME Zinc cash', 'LME
    Zinc 18 Nov26', 'SHFE Silver Dec26 16,000 call', 'USDCNH spot', 'USDCNH 18 Nov forward'."""
    iid = str(instrument_id or "")
    if is_fx_pair(iid):
        return f"{iid} spot" if mark_type == "SPOT" else f"{iid} {short_date(settle_date)} forward"
    root = roots.get(root_id)
    if root is None:
        return iid
    try:
        from engine.spreads.trades import leg_name
    except Exception:  # noqa: BLE001
        return iid
    if str(getattr(root, "exchange", "")) == "LME" or root_id.startswith("LME:"):
        if mark_type == "SPOT":
            return f"{root.exchange} {_commodity(root)} cash".strip()
        return leg_name(root, str(settle_date)[:7], str(settle_date), iid)
    parsed = parse_contract_id(iid)
    if parsed is None:
        return f"{root.exchange} {iid}"
    # an option on a future: the strike and call / put written by leg_name itself, once ('80,000 call')
    return leg_name(root, f"{parsed['year']:04d}-{parsed['month']:02d}", "", iid,
                    strike=parsed["strike"] if parsed["option_type"] else None, option_type=parsed["option_type"] or "")


def _signed(value: Optional[float], unit: str) -> str:
    if value is None:
        return MISSING
    text = price_text(abs(value), unit)
    try:
        zero = float(text.replace(",", "")) == 0
    except ValueError:
        zero = False
    return text if zero else ("+" if value > 0 else MINUS) + text


def _pct(fraction: Optional[float]) -> str:
    if fraction is None:
        return MISSING
    p = fraction * 100.0
    body = f"{abs(p):.1f} %"
    return body if round(abs(p), 1) == 0 else ("+" if p > 0 else MINUS) + body


def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _s(value) -> str:
    """A text field of the check frame: '' for None or NaN (a pandas gap)."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value)


def _bool(value) -> Optional[bool]:
    if value is None or (isinstance(value, float) and value != value):
        return None
    return bool(value)


# ---------------------------------------------------------------------------- the marks check
def check_frame(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """`inventory.mark_checks(conn, as_of)`, memoised per database revision, as-of and book day
    (the day the 17:00 New York roll says it is: 'fresh' reads it). Shared: never edit."""
    from data.bloomberg.inventory import mark_checks
    from data.bloomberg.live import book_today
    from ui.tabs.blotter_pricing import screen_memo
    today = book_today().isoformat()
    return screen_memo("mark-checks", conn, as_of, lambda: mark_checks(conn, as_of, today=today), extra=(today,))


def mark_records(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """`mark_rows` of `check_frame`, memoised per database revision, as-of and book day: the one
    copy the Data tab's tables, its CSV and the Book's "price to check" flags read. Shared: never
    edit (filter and sort into new lists)."""
    from data.bloomberg.live import book_today
    from ui.tabs.blotter_pricing import screen_memo
    today = book_today().isoformat()
    return screen_memo("mark-rows", conn, as_of, lambda: mark_rows(conn, as_of, check_frame(conn, as_of)),
                       extra=(today,))


def mark_rows(conn: sqlite3.Connection, as_of: str, frame: pd.DataFrame) -> List[dict]:
    """The marks check as JSON-safe rows (the tab keeps them in a store for its filter, sort and
    CSV): every column of `mark_checks` plus the words and texts the table shows; MISSING, CHECK,
    OK, then by trade and name."""
    from ui.tabs.header import mark_time_words
    from ui.tabs.market_data import _price_units, _sector_label, mark_group, source_label
    if frame is None or frame.empty:
        return []
    roots = _roots()
    bases: Dict[str, str] = {}
    try:
        bases = {str(i): str(b or "") for i, b in conn.execute("SELECT instrument_id, base_ccy FROM instruments")}
    except sqlite3.Error:
        pass
    units = _price_units(conn, set(frame["instrument_id"]))
    rows = []
    for r in frame.to_dict("records"):
        iid, mt, settle = str(r["instrument_id"]), str(r["mark_type"]), str(r["settle_date"])
        unit = units.get(iid, "")
        sector, commodity = mark_group(iid, roots, bases)
        root = roots.get(_s(r.get("root_id")))
        if root is not None and sector != "fx":
            commodity = _commodity(root) or commodity
        value, prev = _num(r.get("value")), _num(r.get("prev_close"))
        change, pct = _num(r.get("change")), _num(r.get("change_pct"))
        mark_date = _s(r.get("mark_date"))
        snapped = _s(r.get("snapped_at"))
        status = _s(r.get("status"))
        blocks = [str(t) for t in (r.get("blocks") if isinstance(r.get("blocks"), list) else [])]
        name = price_name(iid, mt, settle, _s(r.get("root_id")), roots)
        rows.append({
            "status": status, "status_rank": STATUS_ORDER.get(status, 3),
            "name": name, "name_tip": " · ".join(x for x in (iid, mt, settle, f"plays {_s(r.get('roles'))}"
                                                              if _s(r.get("roles")) else "") if x),
            "sector": sector, "group": _sector_label(sector), "commodity": commodity,
            "mark": price_text(value, unit) if value is not None else (_s(r.get("value")) or MISSING),
            "mark_tip": ("" if mark_date == as_of or not mark_date
                         else f"no mark for {short_date(as_of)}: the last on file is from {short_date(mark_date)}"),
            "source": source_label(_s(r.get("source"))) or MISSING, "source_code": _s(r.get("source")),
            "time": mark_time_words(mark_date or as_of, snapped) if snapped else MISSING, "snapped_at": snapped,
            "prev": price_text(prev, unit) if prev is not None else MISSING, "prev_raw": prev,
            "prev_date": short_date(_s(r.get("prev_date"))) if _s(r.get("prev_date")) else "",
            "prev_date_iso": _s(r.get("prev_date")),
            "change": _signed(change, unit), "change_raw": change, "pct": _pct(pct), "pct_raw": pct,
            "arrived": _bool(r.get("arrived")), "arrived_reason": _s(r.get("arrived_reason")),
            "fresh": _bool(r.get("fresh")), "fresh_reason": _s(r.get("fresh_reason")),
            "sane": _bool(r.get("sane")), "sane_reason": _s(r.get("sane_reason")),
            "usual_move_pct": _num(r.get("usual_move_pct")), "sane_limit_pct": _num(r.get("sane_limit_pct")),
            "units_ok": _bool(r.get("units_ok")), "units_reason": _s(r.get("units_reason")),
            "avg_fill": _num(r.get("avg_fill")),
            "trades": _s(r.get("trade_names")), "blocks": blocks, "blocks_what": _s(r.get("blocks_what")),
            "roles": _s(r.get("roles")), "requestable": bool(r.get("requestable", True)),
            "instrument_id": iid, "mark_type": mt, "settle_date": settle, "value_raw": value, "mark_date": mark_date,
            "exchange": _s(r.get("exchange")), "root_id": _s(r.get("root_id")),
        })
    rows.sort(key=lambda x: (x["status_rank"], x["trades"] or "~", x["name"]))
    return rows


def problem_words(row: dict, as_of: str, today: str) -> Tuple[str, str]:
    """(the problem in a few plain words, the engine's full sentences for the hover) of a MISSING or
    CHECK mark."""
    if row["status"] == "MISSING":
        head = "No price today" if as_of >= today else f"No price for {short_date(as_of)}"
        if not row.get("requestable", True):
            head += ": not asked of Bloomberg"
        return head, row["arrived_reason"]
    words, tips = [], []
    if row["fresh"] is False:
        words.append("Carried, not today's")
        tips.append(row["fresh_reason"])
    if row["sane"] is False:
        if row["pct_raw"] is not None:
            when = "in a day" if "in a day" in row["sane_reason"] or not row["sane_reason"] else "since its last close"
            moved = f"Moved {abs(row['pct_raw']) * 100:.1f} % {when}"
            usual = row.get("usual_move_pct")
            if usual is not None:
                words.append(f"{moved}, usual move {abs(usual) * 100:.1f} %")
            elif row["sane_reason"]:
                words.append(f"{moved} ({row['sane_reason'].split('; ', 1)[-1]})")
            else:
                words.append(f"{moved}, beyond its usual move")
        else:
            words.append("The stored price is not a number")
        tips.append(row["sane_reason"])
    if row["units_ok"] is False:
        reason = row["units_reason"]
        words.append("About 100× off its fills: cents vs dollars?" if "100x" in reason
                     else "Far from its fills: check the units")
        tips.append(reason)
    return ("; ".join(words) or "A check failed"), "\n".join(t for t in tips if t)


# ---------------------------------------------------------------------------- problems
def _problem(level: str, label: str, price: str, problem: str, blocks: str, price_tip: str = "",
             problem_tip: str = "", blocks_tip: str = "", rank: float = 0) -> dict:
    return {"level": level, "label": label, "price": price, "price_tip": price_tip, "problem": problem,
            "problem_tip": problem_tip, "blocks": blocks, "blocks_tip": blocks_tip, "rank": rank}


def pull_problems(status: Optional[dict]) -> List[dict]:
    """The last pull's failed and partial steps and the backfill's errors, values that were not
    numbers and stopped requests, as problem rows in plain words (the status file's own words)."""
    out: List[dict] = []
    if not status:
        return out
    from ui.feed_controls import pull_time
    when = str(status.get("time") or "")
    stamp = f" (pull of {pull_time(when)})" if when else ""
    steps = status.get("steps") or []
    errors = status.get("step_errors") or {}
    if steps:
        for s in steps:
            outcome = str(s.get("outcome") or "")
            if outcome not in ("failed", "partial"):
                continue
            key = str(s.get("step") or "")
            label = str(s.get("label") or key)
            detail = str(s.get("detail") or "") or str((errors.get(key) or {}).get("reason") or "")
            out.append(_problem("red" if outcome == "failed" else "amber", "Failed" if outcome == "failed" else "Partial",
                                f"Pull: {label}", plain_words(detail) or f"{label} {outcome}",
                                STEP_EFFECT.get(key, "Prices from this step were not updated"),
                                price_tip=f"The last Bloomberg pull's step '{label}'{stamp}.",
                                problem_tip=plain_words(detail), rank=2 if outcome == "failed" else 4))
    elif status.get("connected") is False and (status.get("reason") or status.get("recalc_summary")):
        why = str(status.get("reason") or status.get("recalc_summary") or "")
        if "no pull" in why.lower():
            out.append(_problem("red", "No pull", "Bloomberg pull", "No Bloomberg pull has run yet",
                                "No prices yet: press Pull Bloomberg now", problem_tip=why, rank=2))
        else:
            out.append(_problem("red", "Failed", "Bloomberg pull", plain_words(why),
                                "No price was updated: the book runs on the marks on file", problem_tip=why, rank=2))
    backfill = status.get("backfill") or {}
    if isinstance(backfill, dict):
        errs = [e for e in backfill.get("errors") or [] if isinstance(e, dict)]
        for e in errs:
            day = str(e.get("day") or "")
            label = str(e.get("label") or e.get("step") or "past closes")
            out.append(_problem("red", "Failed", f"Past closes: {label}" + (f", {short_date(day)}" if day else ""),
                                plain_words(e.get("reason")),
                                (f"The {short_date(day)} close: a period measured from it steps back to an earlier "
                                 "close") if day else "Past closes not filled: Daily, 5d, MTD and YTD may step back to "
                                                      "earlier closes",
                                problem_tip=str(e.get("reason") or ""), rank=2))
        more = int(backfill.get("error_count") or 0) - len(errs)
        if more > 0:
            out.append(_problem("red", "Failed", "Past closes", f"{more} more failures, not listed one by one",
                                "Past closes not filled: see Diagnostics", rank=2.5))
        nans = [n for n in backfill.get("not_numbers") or [] if isinstance(n, dict)]
        for n in nans:
            day = str(n.get("day") or "")
            out.append(_problem("amber", "Check", f"Past close: {n.get('what') or ''}".strip(),
                                "Bloomberg sent a value that is not a number",
                                f"That close is left out for it{f' on {short_date(day)}' if day else ''}",
                                problem_tip=str(n.get("reason") or ""), rank=3))
        more = int(backfill.get("not_number_count") or 0) - len(nans)
        if more > 0:
            out.append(_problem("amber", "Check", "Past closes", f"{more} more values that were not numbers",
                                "Those closes are left out for them: see Diagnostics", rank=3.5))
        req = backfill.get("requests") or {}
        if isinstance(req, dict):
            if req.get("no_session"):
                out.append(_problem("red", "Failed", "Past closes", "No Bloomberg session for the backfill",
                                    "Past closes not filled until a pull reaches Bloomberg",
                                    problem_tip=str(req.get("no_session")), rank=2))
            elif req.get("gave_up"):
                out.append(_problem("red", "Failed", "Past closes", "Bloomberg stopped answering; the backfill gave up",
                                    "The closes not asked stay missing until the next pull",
                                    problem_tip=f"{req.get('tickers_not_asked', 0)} tickers not asked", rank=2))
    return out


def problem_rows(mark_records: List[dict], status: Optional[dict], unpriced: Dict[str, Tuple[str, str]],
                 as_of: str, today: str, unrecognised: Optional[List[dict]] = None) -> List[dict]:
    """Every per-mark and per-trade problem: the missing and flagged marks (missing first) and the
    trades left out of the P&L that nothing above explains; red before amber. The trades whose
    contract is not recognised (`unrecognised`) are the Trades card's (`trade_problem_rows`), left
    out here. The pull's and the backfill's failures are not
    here (2026-09-30, user: "all in one place"): they are the Bloomberg card's "Pull problems"
    (`pull_problems`); `status` is kept in the signature for the callers."""
    del status
    out: List[dict] = []
    covered: set = set()
    # The contracts not recognised are the Trades card's (2026-09-30): their trades are left out
    # here too, so they are not listed again as "No P&L".
    covered |= {str(u.get("trade_id")) for u in unrecognised or []}
    for r in mark_records:
        if r["status"] not in ("MISSING", "CHECK"):
            continue
        covered.update(r["blocks"])
        words, tip = problem_words(r, as_of, today)
        out.append(_problem(STATUS_LEVEL[r["status"]], STATUS_WORDS[r["status"]], r["name"], words,
                            plain_words(r["blocks_what"]) or "No trade reads it today",
                            price_tip=r["name_tip"], problem_tip=tip,
                            blocks_tip=("Trades: " + ", ".join(r["blocks"])) if r["blocks"] else "",
                            rank=0 if r["status"] == "MISSING" else 3))
    for tid, (name, reason) in sorted(unpriced.items(), key=lambda kv: kv[1][0]):
        if tid in covered:
            continue
        out.append(_problem("red", "No P&L", name, plain_words(reason) or "no price from any source",
                            "Its P&L is left out of every total (excl. on the Book)", price_tip=tid,
                            problem_tip=reason, rank=1))
    out.sort(key=lambda p: (p["rank"], p["price"]))
    return out


PROBLEM_COLUMNS: Tuple[kit.Column, ...] = (
    ("label", "Status", "l", "Missing: no official price for the date. Check: a price arrived but failed a check. "
                             "No P&L: a trade nothing can price. A contract not recognised is under Trades.", True),
    ("price", "Price", "l", "The price with its exchange, or the trade.", True),
    ("problem", "Problem", "l", "What is wrong, in plain words; the full sentence on hover.", True),
    ("blocks", "What it blocks", "l", "What the problem does to the book's figures, and which trades.", True),
)
PROBLEM_SORT = {"label": lambda p: (p["rank"], p["label"]), "price": lambda p: p["price"].lower(),
                "problem": lambda p: p["problem"].lower(), "blocks": lambda p: p["blocks"].lower()}


def problems_table(rows: List[dict], sort: Optional[dict], sort_type: str) -> html.Table:
    return tidy(_problems_table(rows, sort, sort_type))


def _problems_table(rows: List[dict], sort: Optional[dict], sort_type: str) -> html.Table:
    body = []
    for p in kit.sort_records(rows, sort, PROBLEM_SORT):
        body.append(html.Tr([
            kit.td(kit.chip(p["label"], p["level"]), left=True),
            kit.td(p["price"], left=True, title=p["price_tip"] or None),
            kit.td(cap_parts(p["problem"]), left=True, title=p["problem_tip"] or None),
            kit.td(cap_parts(p["blocks"]), left=True, title=p["blocks_tip"] or None),
        ]))
    return kit.table(kit.head(PROBLEM_COLUMNS, sort, sort_type), body)


# ---------------------------------------------------------------------------- the marks table
MARK_COLUMNS: Tuple[kit.Column, ...] = (
    ("status", "Status", "l", "Missing, Check (arrived but a check failed) or OK.", True),
    ("name", "Price", "l", "The price with its exchange; the instrument, the kind of price and the date on hover.",
     True),
    ("mark", "Mark", "", "The official price on file for the date (the last one on file when none is).", True),
    ("source", "Source", "l", "Where the price came from.", True),
    ("time", "Time", "l", "When it was marked: a past close is 17:00 New York of its date.", True),
    ("prev", "Prev close", "", "The last official price before the date, with its date.", True),
    ("change", "Change", "", "Mark − previous close, in the price's own unit, and in percent.", True),
    ("arrived", "Arrived", "", "An official price dated the as-of is on file.", True),
    ("fresh", "Fresh", "", "It is the date's own price, not one carried or re-dated from another time.", True),
    ("sane", "Sane move", "", "The move since the previous close is within 5× its usual daily move (8 % with a short "
                              "history).", True),
    ("units", "Units", "", "The price is in the same units as the fills it prices (not 100× off: cents vs dollars).",
     True),
    ("trades", "Trades", "l", "The trades this price values (their trade names).", True),
)
MARK_SORT: Dict[str, Callable[[dict], Any]] = {
    "status": lambda r: (r["status_rank"], r["trades"] or "~", r["name"]),
    "name": lambda r: r["name"].lower(),
    "mark": lambda r: r["value_raw"],
    "source": lambda r: r["source"].lower() if r["source_code"] else None,
    "time": lambda r: r["snapped_at"] or None,
    "prev": lambda r: r["prev_raw"],
    "change": lambda r: r["pct_raw"],
    "arrived": lambda r: r["arrived"],
    "fresh": lambda r: r["fresh"],
    "sane": lambda r: r["sane"],
    "units": lambda r: r["units_ok"],
    "trades": lambda r: r["trades"].lower() or None,
}


# The marks check's column filters (spreadsheet-style, the funnel in each heading; user, 2026-09-29):
# Status, Price (its commodity / sector) and Source tick lists; Mark, Prev close and Change (in
# percent) one comparison each. {"status", "group", "source": [...], "mark", "prev", "change": text}.
MARK_LIST_PARTS = ("status", "group", "source")
MARK_NUMBER_PARTS = ("mark", "prev", "change")
MARK_FILTER_DEFAULT: Dict[str, Any] = {"status": [], "group": [], "source": [], "mark": "", "prev": "", "change": ""}
MARK_COL_TYPE = "md-col"            # a heading funnel's control: {"type", "part"}
STATUS_OPTIONS = [{"label": "Missing", "value": "MISSING"}, {"label": "Check", "value": "CHECK"},
                  {"label": "OK", "value": "OK"}]


def normal_marks_filter(state: Optional[dict]) -> Dict[str, Any]:
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in MARK_FILTER_DEFAULT.items()}
    for k, v in (state or {}).items():
        if k in MARK_LIST_PARTS:
            out[k] = [str(x) for x in (v or []) if x is not None]
        elif k in MARK_NUMBER_PARTS:
            out[k] = str(v or "").strip()
    return out


def marks_filtered(state: Optional[dict]) -> bool:
    s = normal_marks_filter(state)
    return any(s[k] for k in MARK_LIST_PARTS + MARK_NUMBER_PARTS)


def _mark_figure(r: dict, part: str) -> Optional[float]:
    if part == "mark":
        return r.get("value_raw")
    if part == "prev":
        return r.get("prev_raw")
    pct = r.get("pct_raw")
    return None if pct is None else pct * 100.0


def filter_marks(rows: Optional[List[dict]], statuses: Optional[list], groups: Optional[list],
                 search: Optional[str], columns: Optional[dict] = None) -> List[dict]:
    """The marks the status picks, the sector / commodity picks, the search and (`columns`, the
    headings' other filters) the sources ticked and the comparisons keep."""
    from ui.tabs.trade_filter import parse_compare, passes
    picks = [str(g) for g in groups or []]
    stat = {str(s) for s in statuses or []}
    needle = str(search or "").strip().lower()
    cols = normal_marks_filter(columns)
    sources = set(cols["source"])
    tests = {k: parse_compare(cols[k]) for k in MARK_NUMBER_PARTS if cols[k]}
    out = []
    for r in rows or []:
        if stat and r["status"] not in stat:
            continue
        if picks and not any(p in (f"sector:{r['sector']}", f"commodity:{r['sector']}:{r['commodity']}") for p in picks):
            continue
        if sources and str(r.get("source") or "") not in sources:
            continue
        if any(not passes(_mark_figure(r, k), t) for k, t in tests.items() if t is not None):
            continue
        if needle:
            hay = " ".join(str(r.get(k) or "") for k in ("name", "instrument_id", "source", "source_code", "group",
                                                          "commodity", "trades", "exchange")).lower()
            if needle not in hay:
                continue
        out.append(r)
    return out


def _tip(value) -> Optional[str]:
    """A hover from a stored value: None for a blank or a missing one (None, NaN), never the
    text "nan"."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ("nan", "none", "nat") else None


def marks_head(columns, sort: Optional[dict], sort_type: str, state: Optional[dict],
               options: Optional[Dict[str, List[dict]]]) -> html.Thead:
    """The heads: each title sorts, Status, Price and Source filter from a tick list in their funnel,
    Mark, Prev close and Change from one comparison."""
    from ui.tabs.trade_filter import (NUMBER_HINT, arrow_of, funnel, head_th, option_words, pop_list, pop_number)
    s = normal_marks_filter(state)
    opts = {"status": STATUS_OPTIONS, **(options or {})}
    lists = {"status": ("status", "Status"), "name": ("group", "Commodity / sector"), "source": ("source", "Source")}
    hints = {"mark": "The price as on file, in its own unit.", "prev": "The previous close, in its own unit.",
             "change": "The change in percent: > 5 or < -5."}
    half = len(columns) // 2
    cells = []
    for i, (key, title, cls, tip, sortable) in enumerate(columns):
        pop = None
        if key in lists:
            part, words = lists[key]
            ticked = s[part]
            pop = funnel(f"data:{part}", pop_list({"type": MARK_COL_TYPE, "part": part}, words, opts.get(part), ticked),
                         bool(ticked), f"{words}: {option_words(opts.get(part), ticked)}" if ticked else "")
        elif key in hints:
            text = s[key]
            pop = funnel(f"data:{key}", pop_number({"type": MARK_COL_TYPE, "part": key}, text,
                                                   hint=f"{hints[key]} {NUMBER_HINT}"), bool(text), text)
        cells.append(head_th(title, cls, tip, sort_id={"type": sort_type, "idx": key} if sortable else None,
                             arrow=arrow_of(sort, key), pop=pop, right=i > half))
    return html.Thead(html.Tr(cells))


def source_options(rows: Optional[List[dict]]) -> List[dict]:
    """The Source funnel's choices: the sources present, each with its count."""
    counts: Dict[str, int] = {}
    for r in rows or []:
        src = str(r.get("source") or "")
        if src:
            counts[src] = counts.get(src, 0) + 1
    return [{"label": f"{k} ({n})", "value": k} for k, n in sorted(counts.items())]


def marks_table(rows: List[dict], sort: Optional[dict], sort_type: str, total: int,
                state: Optional[dict] = None, options: Optional[Dict[str, List[dict]]] = None) -> html.Table:
    """The marks check: the total row first (the count showing and the checks failed), then one
    row per price. `options`: the funnels' choices over every price ({"group", "source"}; from the
    rows showing when not given); `state`: the headings' filters set."""
    if options is None:
        from ui.tabs.market_data import marks_filter_options
        options = {"group": marks_filter_options(rows), "source": source_options(rows)}
    shown = kit.sort_records(rows, sort, MARK_SORT)
    n_missing = sum(1 for r in rows if r["status"] == "MISSING")
    n_check = sum(1 for r in rows if r["status"] == "CHECK")
    label = f"All prices · {total:,}" if len(rows) == total else f"Filtered · {len(rows):,} of {total:,} prices"
    label += "".join(f" · {n:,} {words}" for n, words in ((n_missing, "missing"), (n_check, "to check")) if n)
    # one previous-close date for every row: said once, in the heading, not beside every figure
    prev_dates = {r["prev_date"] for r in rows if r["prev_raw"] is not None}
    one_prev = next(iter(prev_dates)) if len(prev_dates) == 1 else ""
    columns = tuple((k, f"{t} {one_prev}", c, f"{h} All on {one_prev}.", srt) if k == "prev" and one_prev
                    else (k, t, c, h, srt) for k, t, c, h, srt in MARK_COLUMNS)
    body = [kit.total_row([html.Td(label, colSpan=len(MARK_COLUMNS), className="l")])]
    for r in shown:
        mark = (html.Span(r["mark"], className="cell-estimated", title=plain_words(r["mark_tip"]))
                if r["mark_tip"] and r["mark"] != MISSING else
                (r["mark"] if r["mark"] != MISSING else missing_cell(r["arrived_reason"])))
        prev = ((r["prev"] if one_prev else html.Span([r["prev"], html.Span(r["prev_date"], className="cell-unit")]))
                if r["prev_raw"] is not None else missing_cell("no earlier close on file"))
        change = (html.Span([r["change"], html.Span(r["pct"], className="cell-unit")],
                            className="cell-amber" if r["sane"] is False else None)
                  if r["change_raw"] is not None else missing_cell(r["sane_reason"] or "no move to show"))
        units_ok = None if r["units_reason"] in ("no fill to compare", "no positive mark to compare") else r["units_ok"]
        sane_ok = None if r["sane_reason"] in ("no mark to compare", "no earlier close to compare") else r["sane"]
        body.append(html.Tr([
            kit.td(kit.chip(STATUS_WORDS.get(r["status"], r["status"]), STATUS_LEVEL.get(r["status"], "grey"),
                            r["arrived_reason"] if r["status"] == "MISSING" else None), left=True),
            kit.td(r["name"], left=True, title=r["name_tip"]),
            kit.td(mark),
            kit.td(r["source"], left=True, title=_tip(r["source_code"])),
            kit.td(r["time"], left=True, title=_tip(r["snapped_at"])),
            kit.td(prev, title=_tip(r["prev_date_iso"])),
            kit.td(change),
            kit.td(kit.check_cell(r["arrived"], r["arrived_reason"])),
            kit.td(kit.check_cell(r["fresh"] if r["arrived"] else None, r["fresh_reason"])),
            kit.td(kit.check_cell(sane_ok, r["sane_reason"])),
            kit.td(kit.check_cell(units_ok, r["units_reason"],
                                  ok_hover=(f"Average fill {r['avg_fill']:,.6g}" if r["avg_fill"] else ""))),
            kit.td(r["trades"] or missing_cell("no trade reads it today"), left=True,
                   title=("; ".join(filter(None, [r["blocks_what"], ", ".join(r["blocks"])]))) or None),
        ]))
    return tidy(kit.table(marks_head(columns, sort, sort_type, state, options), body))


MARK_CSV_COLUMNS = ["status", "name", "instrument_id", "exchange", "mark_type", "settle_date", "value_raw", "mark_date",
                    "source_code", "snapped_at", "prev_raw", "prev_date_iso", "change_raw", "pct_raw", "arrived",
                    "arrived_reason", "fresh", "fresh_reason", "sane", "sane_reason", "units_ok", "units_reason",
                    "avg_fill", "roles", "trades", "blocks_what", "requestable"]
PROBLEM_CSV_COLUMNS = ["label", "price", "price_tip", "problem", "problem_tip", "blocks", "blocks_tip"]


# ---------------------------------------------------------------------------- diagnostics
def steps_table(status: Optional[dict]) -> Optional[html.Table]:
    """The last pull step by step: Step · Outcome · Detail · Seconds; None when the status file has
    no step-by-step record (a pull before 29 Sep, or none yet: the Diagnostics facts say so)."""
    steps = (status or {}).get("steps") or []
    if not steps:
        return None
    cols: Tuple[kit.Column, ...] = (
        ("step", "Step", "l", "The step of the pull, in order.", False),
        ("outcome", "Outcome", "l", "Ok, partial (some of it failed), failed, or skipped (nothing to do).", False),
        ("detail", "Detail", "l", "What the step did, in plain words.", False),
        ("seconds", "Seconds", "", "How long it took.", False),
    )
    level = {"ok": "green", "partial": "amber", "failed": "red", "skipped": "grey"}
    body = [html.Tr([
        kit.td(cap(str(s.get("label") or s.get("step") or "")), left=True, title=str(s.get("step") or "") or None),
        kit.td(kit.chip(cap(str(s.get("outcome") or "unknown")), level.get(str(s.get("outcome") or ""), "grey")),
               left=True),
        kit.td(cap_parts(plain_words(s.get("detail")) or "") or MISSING, left=True),
        kit.td(f"{float(s.get('seconds') or 0):,.1f}"),
    ]) for s in steps]
    return kit.table(kit.head(cols, None, "data-steps-none"), body, className="tk-small")


def pull_facts(status: Optional[dict], no_pull: bool = False) -> Tuple[str, str]:
    """(the last pull in a few words, its detail for the hover) for the Diagnostics facts:
    "Mon 28 Sep 14:32 HK · 9 steps, all ok"; "None yet" with no status file or `no_pull` (the
    status file says no pull has run: its time is then the status's own, not a pull's)."""
    from ui.feed_controls import pull_time
    s = status or {}
    when = pull_time(s.get("time")) if s.get("time") else ""
    if not s or no_pull:
        return "None yet", "No Bloomberg pull has run with this database: press Pull Bloomberg now."
    steps = s.get("steps") or []
    if not steps:
        return (when or "Recorded, time unknown",
                "The status file of the last pull records no step-by-step outcome (a pull before 29 Sep).")
    bad = [x for x in steps if str(x.get("outcome") or "") in ("failed", "partial")]
    words = f"{len(steps)} step{'' if len(steps) == 1 else 's'}"
    words += f", {len(bad)} failed or partial" if bad else ", all ok"
    detail = "; ".join(f"{x.get('label') or x.get('step')}: {x.get('outcome')}" for x in steps)
    return (f"{when} · {words}" if when else cap(words)), detail


def backfill_facts(status: Optional[dict]) -> Tuple[str, str]:
    """(the backfill in a few words, its requests in full for the hover) for the Diagnostics facts."""
    b = (status or {}).get("backfill") or {}
    if not isinstance(b, dict) or not b:
        return "None recorded", "No backfill of past closes is recorded in the last pull's status file."
    req = b.get("requests") or {}
    hover = ""
    if isinstance(req, dict) and req:
        hover = (f"History requests: {int(req.get('sent') or 0):,} sent, {int(req.get('answered') or 0):,} answered, "
                 f"{int(req.get('failed') or 0):,} failed; tickers failed {int(req.get('tickers_failed') or 0):,}, "
                 f"not asked {int(req.get('tickers_not_asked') or 0):,}"
                 + ("; gave up after repeated timeouts" if req.get("gave_up") else "")
                 + (f"; no session: {req.get('no_session')}" if req.get("no_session") else ""))
    errors = int(b.get("error_count") or len(b.get("errors") or []))
    nans = int(b.get("not_number_count") or len(b.get("not_numbers") or []))
    if b.get("running"):
        words = (f"Running, {b['remaining']} day{'' if b['remaining'] == 1 else 's'} left"
                 if b.get("remaining") is not None else "Running")
    elif b.get("reason"):
        words, hover = "Not running", "; ".join(x for x in (str(b["reason"]), hover) if x)
    elif b.get("remaining") == 0:
        words = "History complete"
    else:
        words = "Ran"
    extra = [f"{errors:,} error{'' if errors == 1 else 's'}" if errors else "",
             f"{nans:,} not a number" if nans else ""]
    words = " · ".join(x for x in [words, *extra] if x)
    return words, hover or words


def backfill_tables(status: Optional[dict]) -> list:
    """The backfill's errors and values that were not numbers, each a small table under its own
    title; [] when there are none (the facts line says the rest)."""
    from ui.tabs.formatting import about
    b = (status or {}).get("backfill") or {}
    if not isinstance(b, dict) or not b:
        return []
    parts: list = []
    errs = [e for e in b.get("errors") or [] if isinstance(e, dict)]
    if errs:
        cols: Tuple[kit.Column, ...] = (("day", "Day", "l", "The close worked on.", False),
                                        ("step", "Step", "l", "What was being asked.", False),
                                        ("reason", "Reason", "l", "Why it failed.", False))
        parts.append(about(f"Backfill errors ({int(b.get('error_count') or len(errs)):,})",
                           "What the backfill after the last pull could not fill, and why.", level="h5"))
        parts.append(kit.table(kit.head(cols, None, "data-bf-none"), [html.Tr([
            kit.td(short_date(e.get("day")) if e.get("day") else MISSING, left=True),
            kit.td(cap(str(e.get("label") or e.get("step") or "")) or MISSING, left=True),
            kit.td(cap_parts(plain_words(e.get("reason"))) or MISSING, left=True)]) for e in errs],
            className="tk-small"))
    nans = [n for n in b.get("not_numbers") or [] if isinstance(n, dict)]
    if nans:
        cols = (("what", "What", "l", "The value asked.", False), ("day", "Day", "l", "Its date.", False),
                ("reason", "Reason", "l", "What Bloomberg sent.", False))
        parts.append(about(f"Values that were not numbers ({int(b.get('not_number_count') or len(nans)):,})",
                           "Past closes Bloomberg answered with something that is not a number: left out.",
                           level="h5"))
        parts.append(kit.table(kit.head(cols, None, "data-nan-none"), [html.Tr([
            kit.td(cap(str(n.get("what") or "")) or MISSING, left=True),
            kit.td(short_date(n.get("day")) if n.get("day") else MISSING, left=True),
            kit.td(cap_parts(plain_words(n.get("reason"))) or MISSING, left=True)]) for n in nans],
            className="tk-small"))
    return parts


def left_out_block(status: Optional[dict]) -> Optional[html.Table]:
    """The OIS curves' left-out quotes and the vol smiles' rejected points, one row each (What ·
    Left out, the tickers and reasons on hover); None when there are none."""
    s = status or {}
    rows: list = []
    for ccy, entry in sorted(((s.get("curves") or {}).get("currencies") or {}).items()):
        if not isinstance(entry, dict) or not entry.get("left_out"):
            continue
        items = "; ".join(f"{x.get('ticker', '')}: {x.get('reason', '')}" for x in entry["left_out"][:12]
                          if isinstance(x, dict))
        rows.append((f"OIS {ccy}", cap_parts(plain_words(entry.get("left_out_summary") or ""))
                     or f"{len(entry['left_out'])} quotes left out", items))
    rejected = (s.get("vol") or {}).get("rejected") or []
    if rejected:
        items = "; ".join(" ".join(str(r.get(k, "")) for k in ("pair", "tenor", "ticker") if r.get(k)) +
                          f": {r.get('reason', '')}" for r in rejected[:12] if isinstance(r, dict))
        rows.append(("Vol points refused", f"{len(rejected)} point{'' if len(rejected) == 1 else 's'}", items))
    if not rows:
        return None
    cols: Tuple[kit.Column, ...] = (("what", "What", "l", "The OIS curve or the vol smiles.", False),
                                    ("left", "Left out", "l", "What was not used; the tickers and reasons on hover.",
                                     False))
    return kit.table(kit.head(cols, None, "data-left-none"), [html.Tr([
        kit.td(what, left=True), kit.td(words, left=True, title=items or None)]) for what, words, items in rows],
        className="tk-small")


# ---------------------------------------------------------------------------- the Bloomberg card (2026-09-30)
# User, 2026-09-30: "this error thing for the bloomberg pull i see it everywhere ... lets all have it
# in the bloomberg diagnostics section in the data tab please - all in one place". The last pull in
# one line, its failures in full ("Pull problems"), the on-demand check's results.
def bloomberg_line(status: Optional[dict], no_pull: bool = False) -> Tuple[str, str]:
    """(the last pull in one line, its detail for the hover): "Last pull Wed 30 Sep 09:12 HK · 45 marks
    written, 2 failed · Past closes: history complete"."""
    from ui.feed_controls import pull_time
    s = status or {}
    if not s or no_pull:
        return ("No Bloomberg pull yet with this book",
                "No Bloomberg pull has run with this database: press Pull Bloomberg now.")
    when = pull_time(s.get("time")) if s.get("time") else ""
    head = f"Last pull {when}" if when else "Last pull"
    if s.get("connected") is False:
        reason = plain_words(s.get("reason")) or "reason not recorded"
        body = f"not connected: {reason}"
    else:
        written = int(s.get("written") or 0)
        body = f"{written:,} mark{'' if written == 1 else 's'} written, {int(s.get('failed') or 0):,} failed"
    fill_words, fill_hover = backfill_facts(s)
    line = f"{head} · {body} · Past closes: {fill_words[:1].lower() + fill_words[1:]}"
    steps_words, steps_hover = pull_facts(s)
    return line, "; ".join(x for x in (steps_hover or steps_words, fill_hover) if x)


PULL_PROBLEM_COLUMNS: Tuple[kit.Column, ...] = (
    ("label", "Status", "l", "Failed or Partial: a step of the last pull or of its backfill of past closes. Check: a "
                             "value Bloomberg sent that is not a number.", False),
    ("price", "What", "l", "The step of the pull, or the past close.", False),
    ("problem", "Problem", "l", "What went wrong, in Bloomberg's or the pull's own words.", False),
    ("blocks", "What it blocks", "l", "What it leaves the book's figures with.", False),
)


def pull_problems_table(rows: List[dict]) -> html.Table:
    """The pull's and the backfill's problems (`pull_problems`), each in its full sentence: the one
    place in the app a pull or backfill error is written out."""
    if not rows:
        return kit.table(kit.head(PULL_PROBLEM_COLUMNS, None, "data-pull-none"), [kit.note_row(
            "No failed or partial step in the last pull or its backfill.", len(PULL_PROBLEM_COLUMNS),
            "cell-missing")], className="tk-small")
    body = []
    for p in sorted(rows, key=lambda r: (r["rank"], r["price"])):
        full = p["problem_tip"] if p["problem_tip"] and len(p["problem_tip"]) > len(p["problem"]) else p["problem"]
        body.append(html.Tr([
            kit.td(kit.chip(p["label"], p["level"]), left=True),
            kit.td(cap(p["price"]), left=True, title=p["price_tip"] or None),
            kit.td(cap_parts(plain_words(full)), left=True),
            kit.td(cap_parts(p["blocks"]), left=True, title=p["blocks_tip"] or None),
        ]))
    return tidy(kit.table(kit.head(PULL_PROBLEM_COLUMNS, None, "data-pull-none"), body,
                          className="tk-small data-pull-problems"))


def connection_table(checks: list) -> html.Table:
    """The fast local checks (`tools.bbg_diagnostics.run_bloomberg_diagnostics`): Result · Check ·
    What it found, failures first."""
    cols: Tuple[kit.Column, ...] = (("result", "Result", "l", "Pass, warning or fail.", False),
                                    ("name", "Check", "l", "What was checked.", False),
                                    ("message", "What it found", "l", "The check's own sentence.", False))
    if not checks:
        return kit.table(kit.head(cols, None, "data-bbg-none"),
                         [kit.note_row("No check was returned.", len(cols), "cell-missing")],
                         className="tk-small data-bbg-results")
    level = {"pass": "green", "warning": "amber", "fail": "red"}
    words = {"pass": "Pass", "warning": "Warning", "fail": "Fail"}
    order = {"fail": 0, "warning": 1, "pass": 2}
    body = []
    for c in sorted(checks, key=lambda c: order.get(str(c.get("status")), 1)):
        status = str(c.get("status", "warning"))
        body.append(html.Tr([kit.td(kit.chip(words.get(status, cap(status)), level.get(status, "grey")), left=True),
                             kit.td(cap(str(c.get("name", ""))), left=True),
                             kit.td(cap_parts(str(c.get("message", ""))), left=True)]))
    return tidy(kit.table(kit.head(cols, None, "data-bbg-none"), body, className="tk-small data-bbg-results"))


# One heading kit for the two diagnostic tables: every title sorts, the listed columns filter from a
# tick list in their funnel (spreadsheet-style, as the marks check). `lists`: {column key: (the row's
# field, the funnel's words)}; the filter state is {field: [values ticked]}.
def list_options(rows: Optional[List[dict]], field: str, order: Optional[Dict[str, int]] = None,
                 words: Optional[Dict[str, str]] = None) -> List[dict]:
    """The values of `field` present in `rows`, each with its count, in `order` then by name."""
    counts: Dict[str, int] = {}
    for r in rows or []:
        v = str(r.get(field) or "")
        if v:
            counts[v] = counts.get(v, 0) + 1
    keys = sorted(counts, key=lambda k: ((order or {}).get(k, 99), k))
    return [{"label": f"{(words or {}).get(k, k)} ({counts[k]:,})", "value": k} for k in keys]


def filter_rows(rows: Optional[List[dict]], state: Optional[dict], fields: Tuple[str, ...]) -> List[dict]:
    """The rows whose `fields` are among the values ticked (nothing ticked = all)."""
    st = state or {}
    picks = {f: {str(v) for v in st.get(f) or []} for f in fields}
    return [r for r in rows or [] if all(not p or str(r.get(f) or "") in p for f, p in picks.items())]


def normal_list_filter(state: Optional[dict], fields: Tuple[str, ...]) -> Dict[str, List[str]]:
    st = state or {}
    return {f: [str(v) for v in st.get(f) or [] if v is not None] for f in fields}


def funnel_head(columns, sort: Optional[dict], sort_type: str, state: Optional[dict], col_type: str,
                lists: Dict[str, Tuple[str, str]], options: Dict[str, List[dict]], prefix: str) -> html.Thead:
    """The heads: each title sorts; the columns in `lists` filter from a tick list in their funnel."""
    from ui.tabs.trade_filter import arrow_of, funnel, head_th, option_words, pop_list
    st = state or {}
    half = len(columns) // 2
    cells = []
    for i, (key, title, cls, tip, sortable) in enumerate(columns):
        pop = None
        if key in lists:
            field, words = lists[key]
            ticked = [str(v) for v in st.get(field) or []]
            opts = options.get(field) or []
            pop = funnel(f"{prefix}:{field}", pop_list({"type": col_type, "part": field}, words, opts, ticked),
                         bool(ticked), f"{words}: {option_words(opts, ticked)}" if ticked else "")
        cells.append(head_th(title, cls, tip, sort_id={"type": sort_type, "idx": key} if sortable else None,
                             arrow=arrow_of(sort, key), pop=pop, right=i > half))
    return html.Thead(html.Tr(cells))


def _text_key(field: str) -> Callable[[dict], Any]:
    return lambda r: str(r.get(field) or "").lower() or None


# ---- "The book's tickers" (`data.bloomberg.ticker_check.check_book`)
TICKER_ORDER = {"ERROR": 0, "CHECK": 1, "NO ANSWER": 2, "OK": 3}
TICKER_WORDS = {"ERROR": "Error", "CHECK": "Check", "NO ANSWER": "No answer", "OK": "OK"}
TICKER_LEVEL = {"ERROR": "red", "CHECK": "amber", "NO ANSWER": "amber", "OK": "green"}
TICKER_COLUMNS: Tuple[kit.Column, ...] = (
    ("status", "Status", "l", "OK: Bloomberg agrees with ours. Check: it answered something else. No answer: it did not "
                              "answer for this ticker. Error: the request failed.", True),
    ("area", "Area", "l", "What part of the book the ticker serves.", True),
    ("what", "What", "l", "The contract, curve or conversion; its instrument on hover.", True),
    ("ticker", "Ticker", "l", "The Bloomberg ticker asked.", True),
    ("bloomberg", "Bloomberg", "l", "What Bloomberg answered.", True),
    ("ours", "Ours", "l", "What the app holds for it.", True),
    ("fix", "What to do", "l", "The finding, and what to change if anything; Bloomberg's field names kept.", True),
)
TICKER_LISTS = {"status": ("status", "Status"), "area": ("area", "Area")}
TICKER_FIELDS = ("status", "area")
TICKER_SORT: Dict[str, Callable[[dict], Any]] = {
    "status": lambda r: (TICKER_ORDER.get(str(r.get("status")), 9), str(r.get("area") or "")),
    **{k: _text_key(k) for k in ("area", "what", "ticker", "bloomberg", "ours", "fix")},
}
TICKER_CSV_COLUMNS = ["status", "area", "what", "instrument_id", "ticker", "bloomberg", "ours", "message", "fix"]


def ticker_table(rows: List[dict], sort: Optional[dict], sort_type: str, state: Optional[dict], col_type: str,
                 total: int, all_rows: Optional[List[dict]] = None) -> html.Table:
    """The book's tickers against Bloomberg, problems first (the check's own order), the total row
    first; Status and Area filter from their funnels."""
    every = all_rows if all_rows is not None else rows
    options = {"status": list_options(every, "status", TICKER_ORDER, TICKER_WORDS), "area": list_options(every, "area")}
    head = funnel_head(TICKER_COLUMNS, sort, sort_type, state, col_type, TICKER_LISTS, options, "data-tick")
    bad = sum(1 for r in rows if str(r.get("status")) != "OK")
    label = f"All tickers · {total:,}" if len(rows) == total else f"Filtered · {len(rows):,} of {total:,} tickers"
    label += f" · {bad:,} to look at" if bad else ""
    body = [kit.total_row([html.Td(label, colSpan=len(TICKER_COLUMNS), className="l")])]
    for r in kit.sort_records(rows, sort, TICKER_SORT):
        st = str(r.get("status") or "")
        message, fix = str(r.get("message") or ""), str(r.get("fix") or "")
        todo = fix or message
        body.append(html.Tr([
            kit.td(kit.chip(TICKER_WORDS.get(st, cap(st)), TICKER_LEVEL.get(st, "grey"), message or None), left=True),
            kit.td(cap(str(r.get("area") or "")) or MISSING, left=True),
            kit.td(cap(str(r.get("what") or "")) or MISSING, left=True, title=str(r.get("instrument_id") or "") or None),
            kit.td(str(r.get("ticker") or "") or missing_cell("no ticker asked"), left=True),
            kit.td(cap(str(r.get("bloomberg") or "")) or missing_cell("no answer"), left=True),
            kit.td(cap(str(r.get("ours") or "")) or MISSING, left=True),
            kit.td(cap_parts(todo) or MISSING, left=True, title=message if fix and message else None),
        ]))
    return tidy(kit.table(head, body, className="tk-small data-ticker-table"))


# ---- the parsing check (`data.ingest.parse_check.check_file`)
PARSE_ORDER = {"NOT RECOGNISED": 0, "WARNING": 1, "CANCELS": 2, "EXCLUDED": 3, "OK": 4}
PARSE_WORDS = {"NOT RECOGNISED": "Not recognised", "WARNING": "Warning", "CANCELS": "Cancels", "EXCLUDED": "Excluded",
               "OK": "OK"}
PARSE_LEVEL = {"NOT RECOGNISED": "red", "WARNING": "amber", "CANCELS": "grey", "EXCLUDED": "grey"}
UPLOAD_WOULD = {"new": "New", "replaces": "Replaces", "removes": "Removes", "": "Nothing"}
PARSE_COLUMNS: Tuple[kit.Column, ...] = (
    ("row", "Row", "", "The row of the file (the header is row 1).", True),
    ("trade_id", "Trade Id", "l", "The file's Trade Id, the upload's merge key.", True),
    ("symbol", "Symbol", "l", "The Symbol cell as written in the file; its Fin Type on hover.", True),
    ("read_as", "Read as", "l", "The contract the app reads it as; its id and Bloomberg ticker on hover.", True),
    ("side", "Side", "l", "Buy or Sell, as the file says.", True),
    ("size", "Size", "", "The quantity and its unit.", True),
    ("price", "Price", "", "The Price cell as written; when the broker's units differ, the price used after it.", True),
    ("expiry", "Expiry", "l", "The contract's expiry, prompt or value date.", True),
    ("trade_type", "Trade type", "l", "The type the PBRoot decimal names.", True),
    ("upload", "Upload would", "l", "What an upload of this file would do with the row against the book on file.", True),
    ("status", "Status", "l", "OK, Warning (loads with a warning), Not recognised (loads with no P&L until the contract "
                              "is known), Excluded (not a trade of this book), Cancels (removes the trade it names).",
     True),
    ("message", "Note", "l", "The parser's own sentence.", True),
)
PARSE_LISTS = {"read_as": ("product", "Product"), "upload": ("upload", "Upload would"), "status": ("status", "Status")}
PARSE_FIELDS = ("product", "upload", "status")
PARSE_CSV_COLUMNS = ["row_no", "trade_id", "symbol", "fin_type", "status", "product", "instrument_id", "bbg_ticker",
                     "expiry", "trade_date", "side", "quantity", "unit", "price_in_file", "price_used", "price_factor",
                     "strategy", "pb_root", "trade_type", "on_file", "message"]
_FX_PRODUCTS = ("FX forward", "FX spot", "FX option", "FX swap")


def _read_as(r: dict) -> str:
    """'WTI Dec26 · Future', 'USDCNH · FX forward'; '' for a row not read as a contract."""
    from ui.tabs.formatting import plain_ids
    product = str(r.get("product") or "")
    iid = str(r.get("instrument_id") or "")
    if not iid or iid.startswith("UNRECOGNISED") or str(r.get("status")) == "NOT RECOGNISED":
        return ""
    name = iid[:6] if product in _FX_PRODUCTS and len(iid) >= 6 else plain_ids(iid)
    return f"{name} · {product}" if product else name


def _abs_qty(r: dict) -> Optional[float]:
    try:
        return abs(float(r["quantity"])) if r.get("quantity") is not None else None
    except (TypeError, ValueError):
        return None


PARSE_SORT: Dict[str, Callable[[dict], Any]] = {
    "row": lambda r: r.get("row_no"),
    "status": lambda r: (PARSE_ORDER.get(str(r.get("status")), 9), r.get("row_no") or 0),
    "read_as": lambda r: _read_as(r).lower() or None,
    "size": _abs_qty,
    "price": lambda r: r.get("price_used"),
    "expiry": lambda r: str(r.get("expiry") or "") or None,
    "upload": lambda r: str(r.get("upload") or ""),
    **{k: _text_key(k) for k in ("trade_id", "symbol", "side", "trade_type", "message")},
}


def parse_records(result: Optional[dict]) -> List[dict]:
    """The check's rows with the display field `upload` added, problems first then file order."""
    out = []
    for r in (result or {}).get("rows") or []:
        rec = dict(r)
        rec["upload"] = UPLOAD_WOULD.get(str(r.get("on_file") or ""), "Nothing")
        out.append(rec)
    out.sort(key=lambda r: (PARSE_ORDER.get(str(r.get("status")), 9), r.get("row_no") or 0))
    return out


def _figure(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value or "")
    return f"{v:,.0f}" if v == int(v) else f"{v:,.6g}"


def parse_table(rows: List[dict], sort: Optional[dict], sort_type: str, state: Optional[dict], col_type: str,
                total: int, all_rows: Optional[List[dict]] = None) -> html.Table:
    """The file's rows as an upload would read them, problems first; Read as (its product), Upload
    would and Status filter from their funnels."""
    every = all_rows if all_rows is not None else rows
    options = {"product": list_options(every, "product"),
               "upload": list_options(every, "upload", {"New": 0, "Replaces": 1, "Removes": 2, "Nothing": 3}),
               "status": list_options(every, "status", PARSE_ORDER, PARSE_WORDS)}
    head = funnel_head(PARSE_COLUMNS, sort, sort_type, state, col_type, PARSE_LISTS, options, "data-parse")
    bad = sum(1 for r in rows if str(r.get("status")) in ("NOT RECOGNISED", "WARNING"))
    label = f"All rows · {total:,}" if len(rows) == total else f"Filtered · {len(rows):,} of {total:,} rows"
    label += f" · {bad:,} to look at" if bad else ""
    body = [kit.total_row([html.Td(label, colSpan=len(PARSE_COLUMNS), className="l")])]
    for r in kit.sort_records(rows, sort, PARSE_SORT):
        st = str(r.get("status") or "")
        read = _read_as(r)
        hover = " · ".join(x for x in (str(r.get("instrument_id") or ""), str(r.get("bbg_ticker") or "")) if x)
        q = _abs_qty(r)
        unit = str(r.get("unit") or "")
        unit = f"({unit})" if " " in unit else unit          # "5 (as in the file)", "10 lots"
        size = f"{_figure(q)} {unit}".strip() if q is not None else missing_cell("this row writes no trade")
        price: Any = str(r.get("price_in_file") or "") or MISSING
        factor = r.get("price_factor")
        if r.get("price_used") is not None and factor not in (None, 1, 1.0):
            price = html.Span([price, html.Span(f" → {_figure(r['price_used'])} (×{_figure(factor)})",
                                                className="cell-unit")])
        expiry = str(r.get("expiry") or "")
        level = PARSE_LEVEL.get(st)
        status = kit.chip(PARSE_WORDS.get(st, cap(st)), level) if level else PARSE_WORDS.get(st, cap(st))
        body.append(html.Tr([
            kit.td(str(r.get("row_no") or "")),
            kit.td(str(r.get("trade_id") or "") or MISSING, left=True),
            kit.td(str(r.get("symbol") or "") or MISSING, left=True, title=str(r.get("fin_type") or "") or None),
            kit.td(read or missing_cell(r.get("message") or "not read as a contract"), left=True, title=hover or None),
            kit.td(str(r.get("side") or "") or MISSING, left=True),
            kit.td(size),
            kit.td(price, title=f"Price used {_figure(r['price_used'])}" if r.get("price_used") is not None else None),
            kit.td(short_date(expiry) if expiry else MISSING, left=True, title=expiry or None),
            kit.td(cap(str(r.get("trade_type") or "")) or MISSING, left=True, title=str(r.get("pb_root") or "") or None),
            kit.td(r.get("upload") or "Nothing", left=True),
            kit.td(status, left=True),
            html.Td(cap_parts(str(r.get("message") or "")), className="l data-parse-note"),
        ]))
    return tidy(kit.table(head, body, className="tk-small data-parse-table"))


# ---- the Trades card (2026-09-30, user: "any issue with the trades it shows up in a trade pull
# section in the data tab"): every problem with the trades on file in one table, problems first.
TRADE_ORDER = {"NOT RECOGNISED": 0, "NOT LOADED": 1, "WARNING": 2, "FILE": 3, "SKIPPED": 4}
TRADE_WORDS = {"NOT RECOGNISED": "Needs a fix", "NOT LOADED": "Not loaded", "WARNING": "Warning",
               "FILE": "About the file", "SKIPPED": "Skipped"}
TRADE_LEVEL = {"NOT RECOGNISED": "red", "NOT LOADED": "amber", "WARNING": "amber", "FILE": "amber", "SKIPPED": "grey"}
TRADE_COLUMNS: Tuple[kit.Column, ...] = (
    ("status", "Status", "l", "Needs a fix: on file as a trade whose contract the app does not recognise, no P&L until "
                              "it is mapped. Not loaded: a row an older upload could not read. Warning: loaded, but "
                              "two cells disagreed or one was doubtful. About the file: a line about the whole file. "
                              "Skipped: a kind of row the app does not load (a retired product, a cash movement).",
     True),
    ("row", "Row", "", "The row's number in the file, the header row not counted.", True),
    ("trade_id", "Trade Id", "l", "The row's Trade Id; the trade's name on hover.", True),
    ("symbol", "Symbol", "l", "The file's Symbol cell as written.", True),
    ("problem", "What is wrong", "l", "What is wrong, in plain words; the upload's own sentence on hover.", True),
    ("affects", "What it affects", "l", "What it does to the book's figures; what to do about it on hover.", True),
)
TRADE_LISTS = {"status": ("status", "Status")}
TRADE_FIELDS = ("status",)
TRADE_SORT: Dict[str, Callable[[dict], Any]] = {
    "status": lambda r: (TRADE_ORDER.get(str(r.get("status")), 9), r.get("row_no") or 0),
    "row": lambda r: r.get("row_no"),
    **{k: _text_key(k) for k in ("trade_id", "symbol", "problem", "affects")},
}
TRADE_CSV_COLUMNS = ["status", "row_no", "trade_id", "trade_name", "symbol", "problem", "reason", "affects", "todo",
                     "filename", "uploaded_at"]
_FIX_TODO = ("Check the symbol; if it is right, add the contract to the contract list. The trade then prices "
             "without a new upload.")


_PB_ROOT_RE = re.compile(r"'?\b[A-Z]+\d+(?:\.(\d))?_([A-Za-z0-9]+)\b'?")
_PB_TYPE_WORDS = {"3": "cross-exchange", "4": "cross-product", "5": "term structure"}


def pb_root_words(text: str) -> str:
    """The broker's raw PBRoot cells in a sentence of the upload's ('JSHY10_ZNA1', 'JSHY10.3_ZNA1')
    as the trade's name, with the type its decimal marks ("ZNA1", "ZNA1 marked cross-exchange"):
    plain words on screen, the raw cells kept in the CSV."""
    def one(m: "re.Match") -> str:
        kind = _PB_TYPE_WORDS.get(m.group(1) or "", f"type .{m.group(1)}" if m.group(1) else "")
        return f"{m.group(2)} marked {kind}" if kind else m.group(2)
    return _PB_ROOT_RE.sub(one, str(text or ""))


def _trade_row(status: str, issue: dict, problem: str, affects: str, todo: str, reason: str = "",
               trade_name: str = "") -> dict:
    row_no = issue.get("row_no")
    try:
        row_no = int(row_no) if row_no not in (None, "") else None
    except (TypeError, ValueError):
        row_no = None
    return {"status": status, "row_no": row_no or None, "trade_id": str(issue.get("trade_id") or ""),
            "trade_name": trade_name, "symbol": str(issue.get("symbol") or ""),
            "problem": cap(pb_root_words(problem)), "reason": str(reason or ""),
            "reason_words": cap(pb_root_words(plain_words(reason))), "affects": cap(affects), "todo": cap(todo),
            "filename": str(issue.get("filename") or ""), "uploaded_at": str(issue.get("uploaded_at") or "")}


def trade_problem_rows(issues: Optional[List[dict]], unrecognised: Optional[List[dict]]) -> List[dict]:
    """Every problem with the trades on file, problems first: the trades whose contract is not
    recognised (`data.bloomberg.inventory.unrecognised`, each with its row of the upload that
    loaded it when `last_upload_issues` still holds it), the rows an older upload did not load,
    the rows skipped, the row warnings and the lines about the file (row 0). Read as recorded,
    nothing recomputed."""
    from ui.tabs.blotter_fills import what_to_do
    issues = [i for i in issues or [] if isinstance(i, dict)]
    need_rows = {str(i.get("trade_id")): i for i in issues
                 if str(i.get("kind") or "") == "UNRECOGNISED" and i.get("trade_id")}
    out: List[dict] = []
    seen: set = set()
    for u in unrecognised or []:
        tid = str(u.get("trade_id") or "")
        seen.add(tid)
        issue = dict(need_rows.get(tid) or {})
        issue["trade_id"] = tid
        issue["symbol"] = issue.get("symbol") or u.get("broker_symbol") or ""
        why = plain_words(u.get("reason") or issue.get("reason") or "")
        if why.lower().startswith("contract not recognised: "):
            why = why[len("contract not recognised: "):]
        name = str(u.get("trade_name") or "")
        named = f" ({name})" if name and name != tid else ""
        out.append(_trade_row("NOT RECOGNISED", issue, why or "The contract is not in the app's contract list",
                              f"No P&L for this trade{named}: left out of every total", _FIX_TODO,
                              reason=str(u.get("reason") or ""), trade_name=name))
    for i in issues:
        kind = str(i.get("kind") or "")
        reason = str(i.get("reason") or "")
        if kind == "UNRECOGNISED":
            tid = str(i.get("trade_id") or "")
            if not tid:       # a table from before trade ids were kept: listed from the upload's own row
                out.append(_trade_row("NOT RECOGNISED", i, plain_words(reason) or "Contract not recognised",
                                      "No P&L for this trade: left out of every total", _FIX_TODO, reason=reason))
            continue          # with a Trade Id: listed above while still not recognised, else mapped since
        if kind == "WARNING":
            file_level = not (i.get("row_no") or i.get("trade_id"))
            if file_level:
                out.append(_trade_row("FILE", i, plain_words(reason) or "A doubtful line in the file",
                                      "The whole file: read it before the next upload",
                                      "Nothing, if the file is as meant.", reason=reason))
            else:
                out.append(_trade_row("WARNING", i, plain_words(reason) or "A doubtful cell",
                                      "Loaded on the primary field: its P&L uses that value",
                                      "Check the two cells in the file; nothing to do if the loaded value is right.",
                                      reason=reason))
            continue
        todo = what_to_do(kind, reason)
        skipped = kind == "NOT LOADED" and todo.startswith("Nothing")
        out.append(_trade_row("SKIPPED" if skipped else "NOT LOADED", i, plain_words(reason) or "The row was not read",
                              "Not in the book: nothing of it is counted" if skipped
                              else "Not in the book: its P&L and exposure are missing from every figure",
                              todo, reason=reason))
    out.sort(key=lambda r: (TRADE_ORDER.get(r["status"], 9), r["row_no"] or 0, r["trade_id"]))
    return out


def trade_problem_words(rows: Optional[List[dict]]) -> str:
    """'2 need a fix · 1 warning' ('' with none): the Data tab's status line and the Blotter's."""
    c: Dict[str, int] = {}
    for r in rows or []:
        c[r["status"]] = c.get(r["status"], 0) + 1
    fix, lost = c.get("NOT RECOGNISED", 0), c.get("NOT LOADED", 0) + c.get("SKIPPED", 0)
    warn = c.get("WARNING", 0) + c.get("FILE", 0)
    parts = []
    if fix:
        parts.append(f"{fix:,} need{'s' if fix == 1 else ''} a fix")
    if lost:
        parts.append(f"{lost:,} not loaded")
    if warn:
        parts.append(f"{warn:,} warning{'' if warn == 1 else 's'}")
    return " · ".join(parts)


def trade_problems_table(rows: List[dict], sort: Optional[dict], sort_type: str, state: Optional[dict],
                         col_type: str, total: int, all_rows: Optional[List[dict]] = None) -> html.Table:
    """The trade problems, problems first, the Status funnel in its heading; a row per trade or
    file row, each sentence in full (the upload's own words on hover)."""
    every = all_rows if all_rows is not None else rows
    options = {"status": list_options(every, "status", TRADE_ORDER, TRADE_WORDS)}
    head = funnel_head(TRADE_COLUMNS, sort, sort_type, state, col_type, TRADE_LISTS, options, "data-trade")
    body = []
    if not total:
        body.append(kit.note_row("No problem with the trades on file: every row of the last upload loaded cleanly.",
                                 len(TRADE_COLUMNS), "cell-pos"))
    elif not rows:
        body.append(kit.note_row("No problem matches the filter.", len(TRADE_COLUMNS), "cell-missing"))
    elif len(rows) != total:
        body.append(kit.total_row([html.Td(f"Filtered · {len(rows):,} of {total:,} problems",
                                           colSpan=len(TRADE_COLUMNS), className="l")]))
    for r in kit.sort_records(rows, sort, TRADE_SORT):
        st = r["status"]
        if r.get("row_no"):
            row_cell = f"{r['row_no']:,}"
        elif st == "FILE":
            row_cell = "File"
        else:
            row_cell = missing_cell("the upload that loaded it kept no row number")
        whole = "a line about the whole file, not one row"
        tid = r["trade_id"] or missing_cell(whole if st == "FILE" else "the upload recorded no Trade Id")
        body.append(html.Tr([
            kit.td(kit.chip(TRADE_WORDS.get(st, cap(st)), TRADE_LEVEL.get(st, "grey")), left=True),
            kit.td(row_cell),
            kit.td(tid, left=True, title=cap(r.get("trade_name") or "") or None),
            kit.td(r["symbol"] or missing_cell(whole if st == "FILE" else "the row has no symbol"), left=True),
            kit.td(cap_parts(r["problem"]), left=True, title=r.get("reason_words") or None),
            kit.td(cap_parts(r["affects"]), left=True, title=r.get("todo") or None),
        ]))
    return tidy(kit.table(head, body, className="tk-small data-trade-table"))
