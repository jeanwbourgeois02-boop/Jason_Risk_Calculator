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

import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
from dash import html

from ui.tabs import data_kit as kit
from ui.tabs.formatting import (
    cap, tidy,
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
    name = leg_name(root, f"{parsed['year']:04d}-{parsed['month']:02d}", "", iid)
    if parsed["option_type"]:
        name += f" {parsed['strike']} {parsed['option_type']}"
    return name


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
            "source": source_label(_s(r.get("source"))) or MISSING, "source_code": str(r.get("source") or ""),
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
    when = str(status.get("time") or "")
    stamp = f" (pull of {when[:16].replace('T', ' ')})" if when else ""
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


def unrecognised_problems(unrecognised: Optional[List[dict]]) -> Tuple[List[dict], set]:
    """(one red problem per symbol the parser could not identify, the trade ids they cover) from
    `data.bloomberg.inventory.unrecognised(conn)`: the trade is on file (every row loads) with no
    P&L until the symbol is added to the contract list; the trades on hover."""
    by_symbol: Dict[str, List[dict]] = {}
    for u in unrecognised or []:
        by_symbol.setdefault(str(u.get("broker_symbol") or u.get("instrument_id") or "?"), []).append(u)
    out, covered = [], set()
    for sym, items in sorted(by_symbol.items()):
        covered.update(str(u.get("trade_id")) for u in items)
        reasons = list(dict.fromkeys(str(u.get("reason") or "") for u in items if u.get("reason")))
        blocks = list(dict.fromkeys(str(u.get("blocks_what") or "") for u in items if u.get("blocks_what")))
        trades = [f"{u.get('trade_id')} ({u.get('trade_name') or 'no trade name'}, {u.get('trade_date')}, "
                  f"{_num(u.get('quantity')) if _num(u.get('quantity')) is not None else '?'} at "
                  f"{u.get('price')})" for u in items]
        names = sorted({str(u.get("trade_name") or "") for u in items} - {""})
        out.append(_problem("red", "Not recognised", sym,
                            cap(plain_words(reasons[0])) if reasons else "Contract not recognised",
                            cap((plain_words(blocks[0]) if blocks else "no P&L: contract not recognised")
                                + (f" · {', '.join(names)}" if names else "")),
                            price_tip=f"The file's symbol as written; {len(items)} trade{'' if len(items) == 1 else 's'}",
                            problem_tip="; ".join(reasons), blocks_tip="Trades: " + "; ".join(trades), rank=0.5))
    return out, covered


def problem_rows(mark_records: List[dict], status: Optional[dict], unpriced: Dict[str, Tuple[str, str]],
                 as_of: str, today: str, unrecognised: Optional[List[dict]] = None) -> List[dict]:
    """Every problem: the missing and flagged marks (missing first), the contracts the parser could
    not identify (`unrecognised`, red), the pull's and the backfill's failures, and the trades left
    out of the P&L that nothing above explains; red before amber."""
    out: List[dict] = []
    covered: set = set()
    unrec, unrec_ids = unrecognised_problems(unrecognised)
    out += unrec
    covered |= unrec_ids
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
    pulls = pull_problems(status)
    if not mark_records:          # nothing needed: "no pull yet" blocks nothing
        pulls = [p for p in pulls if p["label"] != "No pull"]
    out += pulls
    out.sort(key=lambda p: (p["rank"], p["price"]))
    return out


PROBLEM_COLUMNS: Tuple[kit.Column, ...] = (
    ("label", "Status", "l", "Missing: no official price for the date. Check: a price arrived but failed a check. "
                             "Failed / Partial: a step of the last pull. No P&L: a trade nothing can price. Not recognised: a "
                             "row of the blotter whose contract the app does not know (on file, no P&L until mapped).", True),
    ("price", "Price", "l", "The price with its exchange, or the pull step.", True),
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
            kit.td(cap(p["problem"]), left=True, title=p["problem_tip"] or None),
            kit.td(cap(p["blocks"]), left=True, title=p["blocks_tip"] or None),
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


def filter_marks(rows: Optional[List[dict]], statuses: Optional[list], groups: Optional[list],
                 search: Optional[str]) -> List[dict]:
    """The marks the status picks, the sector / commodity picks and the search keep."""
    picks = [str(g) for g in groups or []]
    stat = {str(s) for s in statuses or []}
    needle = str(search or "").strip().lower()
    out = []
    for r in rows or []:
        if stat and r["status"] not in stat:
            continue
        if picks and not any(p in (f"sector:{r['sector']}", f"commodity:{r['sector']}:{r['commodity']}") for p in picks):
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


def marks_table(rows: List[dict], sort: Optional[dict], sort_type: str, total: int) -> html.Table:
    """The marks check: the total row first (the count showing and the checks failed), then one
    row per price."""
    shown = kit.sort_records(rows, sort, MARK_SORT)
    n_missing = sum(1 for r in rows if r["status"] == "MISSING")
    n_check = sum(1 for r in rows if r["status"] == "CHECK")
    label = f"All prices · {total:,}" if len(rows) == total else f"Filtered · {len(rows):,} of {total:,} prices"
    label += f" · {n_missing:,} missing · {n_check:,} to check"
    body = [kit.total_row([html.Td(label, colSpan=len(MARK_COLUMNS), className="l")])]
    for r in shown:
        mark = (html.Span(r["mark"], className="cell-estimated", title=plain_words(r["mark_tip"]))
                if r["mark_tip"] and r["mark"] != MISSING else
                (r["mark"] if r["mark"] != MISSING else missing_cell(r["arrived_reason"])))
        prev = (html.Span([r["prev"], html.Span(r["prev_date"], className="cell-unit")])
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
    return tidy(kit.table(kit.head(MARK_COLUMNS, sort, sort_type), body))


MARK_CSV_COLUMNS = ["status", "name", "instrument_id", "exchange", "mark_type", "settle_date", "value_raw", "mark_date",
                    "source_code", "snapped_at", "prev_raw", "prev_date_iso", "change_raw", "pct_raw", "arrived",
                    "arrived_reason", "fresh", "fresh_reason", "sane", "sane_reason", "units_ok", "units_reason",
                    "avg_fill", "roles", "trades", "blocks_what", "requestable"]
PROBLEM_CSV_COLUMNS = ["label", "price", "price_tip", "problem", "problem_tip", "blocks", "blocks_tip"]


# ---------------------------------------------------------------------------- diagnostics
def steps_table(status: Optional[dict]) -> html.Div:
    """The last pull step by step: Step · Outcome · Detail · Seconds."""
    steps = (status or {}).get("steps") or []
    if not steps:
        return html.P("No step-by-step record of the last pull (a pull before 29 Sep, or none yet).",
                      className="book-section-meta")
    cols: Tuple[kit.Column, ...] = (
        ("step", "Step", "l", "The step of the pull, in order.", False),
        ("outcome", "Outcome", "l", "ok, partial (some of it failed), failed, or skipped (nothing to do).", False),
        ("detail", "Detail", "l", "What the step did, in plain words.", False),
        ("seconds", "Seconds", "", "How long it took.", False),
    )
    level = {"ok": "green", "partial": "amber", "failed": "red", "skipped": "grey"}
    body = [html.Tr([
        kit.td(str(s.get("label") or s.get("step") or ""), left=True, title=str(s.get("step") or "")),
        kit.td(kit.chip(str(s.get("outcome") or "?"), level.get(str(s.get("outcome") or ""), "grey")), left=True),
        kit.td(plain_words(s.get("detail")) or "", left=True),
        kit.td(f"{float(s.get('seconds') or 0):,.1f}"),
    ]) for s in steps]
    when = str((status or {}).get("time") or "")
    return html.Div([html.Div(f"The last pull{(' · ' + when[:19].replace('T', ' ')) if when else ''}",
                              className="book-section-meta"),
                     kit.table(kit.head(cols, None, "data-steps-none"), body, className="tk-small")])


def backfill_block(status: Optional[dict]) -> html.Div:
    """The backfill's requests, errors and values that were not numbers."""
    b = (status or {}).get("backfill") or {}
    if not isinstance(b, dict) or not b:
        return html.P("No backfill recorded.", className="book-section-meta")
    parts: list = []
    req = b.get("requests") or {}
    if isinstance(req, dict) and req:
        parts.append(html.Div(
            f"History requests: {int(req.get('sent') or 0):,} sent, {int(req.get('answered') or 0):,} answered, "
            f"{int(req.get('failed') or 0):,} failed; tickers failed {int(req.get('tickers_failed') or 0):,}, not "
            f"asked {int(req.get('tickers_not_asked') or 0):,}"
            + ("; gave up after repeated timeouts" if req.get("gave_up") else "")
            + (f"; no session: {req.get('no_session')}" if req.get("no_session") else ""),
            className="book-section-meta"))
    errs = [e for e in b.get("errors") or [] if isinstance(e, dict)]
    if errs:
        cols: Tuple[kit.Column, ...] = (("day", "Day", "l", "The close worked on.", False),
                                        ("step", "Step", "l", "What was being asked.", False),
                                        ("reason", "Reason", "l", "Why it failed.", False))
        parts.append(html.Div(f"Errors ({int(b.get('error_count') or len(errs)):,})", className="tk-bold"))
        parts.append(kit.table(kit.head(cols, None, "data-bf-none"), [html.Tr([
            kit.td(short_date(e.get("day")) if e.get("day") else MISSING, left=True),
            kit.td(str(e.get("label") or e.get("step") or ""), left=True),
            kit.td(plain_words(e.get("reason")), left=True)]) for e in errs], className="tk-small"))
    nans = [n for n in b.get("not_numbers") or [] if isinstance(n, dict)]
    if nans:
        cols = (("what", "What", "l", "The value asked.", False), ("day", "Day", "l", "Its date.", False),
                ("reason", "Reason", "l", "What Bloomberg sent.", False))
        parts.append(html.Div(f"Values that were not numbers ({int(b.get('not_number_count') or len(nans)):,})",
                              className="tk-bold"))
        parts.append(kit.table(kit.head(cols, None, "data-nan-none"), [html.Tr([
            kit.td(str(n.get("what") or ""), left=True),
            kit.td(short_date(n.get("day")) if n.get("day") else MISSING, left=True),
            kit.td(plain_words(n.get("reason")), left=True)]) for n in nans], className="tk-small"))
    if not parts:
        parts.append(html.P("The backfill recorded no request, error or bad value.", className="book-section-meta"))
    return html.Div(parts)


def left_out_block(status: Optional[dict]) -> Optional[html.Div]:
    """The OIS curves' left-out quotes and the vol smiles' rejected points, when any."""
    s = status or {}
    lines: list = []
    for ccy, entry in sorted(((s.get("curves") or {}).get("currencies") or {}).items()):
        if not isinstance(entry, dict) or not entry.get("left_out"):
            continue
        items = "; ".join(f"{x.get('ticker', '')}: {x.get('reason', '')}" for x in entry["left_out"][:12]
                          if isinstance(x, dict))
        lines.append(html.Div([html.Span(f"OIS {ccy}: ", className="tk-bold"),
                               plain_words(entry.get("left_out_summary") or ""), " ",
                               html.Span(items, className="book-section-meta")]))
    rejected = (s.get("vol") or {}).get("rejected") or []
    if rejected:
        items = "; ".join(" ".join(str(r.get(k, "")) for k in ("pair", "tenor", "ticker") if r.get(k)) +
                          f": {r.get('reason', '')}" for r in rejected[:12] if isinstance(r, dict))
        lines.append(html.Div([html.Span(f"Vol points refused ({len(rejected)}): ", className="tk-bold"),
                               html.Span(items, className="book-section-meta")]))
    return html.Div(lines) if lines else None
