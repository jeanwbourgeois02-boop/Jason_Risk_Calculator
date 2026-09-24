"""Market data tab feed: what mark does the book need today, and do we have it.

`mark_inventory(conn, as_of)` lists exactly the marks `data/bloomberg/live.py::build_requests`
would ask Bloomberg for on `as_of` (one SPOT per open FX pair, one FWD_OUTRIGHT per open FX
leg's own settle_date, one FUTURE_PX per open future at its own expiry, and for each open FX
option its pair's SPOT, a FWD_OUTRIGHT at its expiry and the SPOT of the USD-conversion pairs
of its base and quote currency) and reports what is
actually in `marks` for each: OFFICIAL (present in `marks_official` -- since 2026-09-18 this
includes a BBG_INTERP row for FWD_OUTRIGHT when no BBG_BFXFORWARD row exists for the same key,
data/ingest/schema.py's OFFICIAL_FALLBACK_SOURCE, so INTERP status below is not reachable for
FWD_OUTRIGHT any more), INTERP (present but only as BBG_INTERP for a mark_type with no
official-fallback entry), MANUAL (present but only as a MANUAL row -- official for
DELTA/PREMIUM, informational only here since those mark_types are never requested by
build_requests), or MISSING (no row at all for as_of/instrument/settle/type).

`close_completeness(conn, start, end)` is the calendar strip: one row per business day with
the count of marks the book needed that day (SPOT + FWD_OUTRIGHT + FUTURE_PX per
`_needed_marks`, 2026-09-18 -- SPOT alone used to leave every forward/future's LTD(t)
unpriced on an otherwise "complete" day; plus NDF_FIX on an NDF ticket's fixing date,
2026-09-22) versus how many are official AT THE CLOSE (a past day's FX row counts only when
stamped 15:00 New York, a future's only when stamped at the settlement,
`backfill.is_close_row`), so the Market data tab can show holes in history at a glance and
the backfill knows which days to ask for. Beside the marks, `inputs_missing` (2026-09-22)
lists the OIS curves and vol smiles the day's options and swaps price from that the day
does not hold yet (`inputs_missing`); the backfill asks Bloomberg's history for those too.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

import pandas as pd

STATUS_OFFICIAL = "OFFICIAL"
STATUS_INTERP = "INTERP"
STATUS_MANUAL = "MANUAL"
STATUS_MISSING = "MISSING"


def _option_needed_marks(conn: sqlite3.Connection, as_of: str, historical: bool = False) -> List[dict]:
    """FX_OPTION needs, from `data.bloomberg.live.option_needed_marks` when that function
    exists -- looked up via getattr so this module never hard-depends on a live.py
    function that may not have landed in this working tree yet. It returns exactly
    [{instrument_id, settle_date, mark_type}]: one SPOT per open FX_OPTION pair, one
    FWD_OUTRIGHT at each open option's own expiry, and (2026-09-18) one SPOT per
    USD-conversion pair the option itself needs (base->USD, quote->USD: EURUSD and USDSEK
    for a EURSEK option), so a missing conversion spot is named on the diagnostics panel
    instead of only surfacing as "no SPOT for USD conversion of EUR" on the blotter.
    `historical=True` (a past close): SPOT only, see live.option_needed_marks.
    Returns `[]` when the function is absent."""
    from data.bloomberg import live
    fn = getattr(live, "option_needed_marks", None)
    if fn is None:
        return []
    return list(fn(conn, as_of, historical=historical))


def _needed_marks(conn: sqlite3.Connection, as_of: str, historical: bool = False,
                  include_unrequestable: bool = False) -> List[dict]:
    """Same (instrument_id, settle_date, mark_type) set as live.build_requests, without
    requiring blpapi (RequestRow construction there is Bloomberg-request specific).

    Includes one SPOT per USD-conversion pair an open cross's legs need
    (live._cross_usd_legs, 2026-09-17) even when no instrument row backs it: a cross like
    EURSEK with no direct EURUSD/USDSEK trade must still show that gap here (MISSING
    until a writable pull or backfill creates the pair's instrument row and writes the
    mark) rather than silently never asking -- the same reasoning live.build_requests
    uses, shared via that one function so the two definitions of "needed" can never
    drift apart.

    Also includes FX_OPTION needs via `_option_needed_marks` (2026-09-18): the option
    pair's SPOT and FWD_OUTRIGHT at expiry, and the SPOT of the option's own
    USD-conversion pairs -- same "call live, don't reimplement" discipline, so this can
    never drift from build_requests.

    `historical=True` is what a PAST close needed (close_completeness, backfill.backfill):
    identical for forwards and futures; for options it is SPOT only -- pair and
    USD-conversion pairs, for every option open that day up to and including its expiry
    date, whether or not the ledger has realised it since (live.option_needed_marks says
    why). The default is the live request list, unchanged.

    2026-09-21: read from the Bloomberg library (data/bloomberg/library.py), the one
    record of what the trades need; build_requests and the backfill read the same rows.

    2026-09-24: a mark with no ticker to ask for (a future of a placeholder root,
    library.unrequestable_reason) is left out -- nothing can fill it -- unless
    `include_unrequestable`, when it is listed with a `reason` key (the other items carry
    none), so a listing can show the gap. A non-USD future's USD-conversion SPOT is a SPOT
    like any other and is in the list."""
    from data.bloomberg import library
    needed = [r for r in library.needed_on(conn, as_of, historical=historical,
                                           include_unrequestable=include_unrequestable)
              if r["kind"] in library.MARK_KINDS]
    needed.sort(key=lambda r: (r["kind"] == "FUTURE_PX", r["product"] == "FX_OPTION", r["key"],
                               r["kind"] != "SPOT", r["settle_date"]))
    out, seen = [], set()
    for r in needed:
        settle = as_of if r["kind"] == "SPOT" else r["settle_date"]
        if (r["key"], settle, r["kind"]) not in seen:
            seen.add((r["key"], settle, r["kind"]))
            item = {"instrument_id": r["key"], "settle_date": settle, "mark_type": r["kind"]}
            if not r.get("requestable", True):
                item["reason"] = r["reason"]
            out.append(item)
    return out


def mark_inventory(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """One row per mark the book needs on `as_of`:
    instrument_id, settle_date, mark_type, value, source, snapped_at, status.
    status is OFFICIAL (from marks_official) | INTERP (BBG_INTERP present, official absent) |
    MANUAL (MANUAL present, official absent) | MISSING (nothing at all). `value`/`source`/
    `snapped_at` are the row backing that status, or None when MISSING.

    2026-09-24: also a `reason` column, '' for a mark the pull asks for; for one it cannot
    ask for (no verified Bloomberg ticker for a placeholder root) the reason, the mark still
    listed with its status so the gap is in sight."""
    needed = _needed_marks(conn, as_of, include_unrequestable=True)
    rows = []
    for item in needed:
        instrument_id, settle, mark_type = item["instrument_id"], item["settle_date"], item["mark_type"]
        params = {"as_of": as_of, "instrument_id": instrument_id, "settle": settle, "mark_type": mark_type}
        official = conn.execute(
            "SELECT value, source, snapped_at FROM marks_official WHERE as_of_date=:as_of "
            "AND instrument_id=:instrument_id AND settle_date=:settle AND mark_type=:mark_type",
            params).fetchone()
        if official is not None:
            value, source, snapped_at, status = official[0], official[1], official[2], STATUS_OFFICIAL
        else:
            fallback = conn.execute(
                "SELECT value, source, snapped_at FROM marks WHERE as_of_date=:as_of "
                "AND instrument_id=:instrument_id AND settle_date=:settle AND mark_type=:mark_type "
                "AND source IN ('BBG_INTERP', 'MANUAL') ORDER BY snapped_at DESC LIMIT 1",
                params).fetchone()
            if fallback is not None:
                value, source, snapped_at = fallback
                status = STATUS_INTERP if source == "BBG_INTERP" else STATUS_MANUAL
            else:
                value, source, snapped_at, status = None, None, None, STATUS_MISSING
        rows.append({"instrument_id": instrument_id, "settle_date": settle, "mark_type": mark_type,
                     "value": value, "source": source, "snapped_at": snapped_at, "status": status,
                     "reason": item.get("reason", "")})
    return pd.DataFrame(rows, columns=["instrument_id", "settle_date", "mark_type", "value", "source",
                                       "snapped_at", "status", "reason"])


def stale_empty_pull_reason(conn: sqlite3.Connection, status: Optional[dict], as_of: str) -> Optional[str]:
    """None when the last recorded feed status (data.bloomberg.live.read_status) looks
    trustworthy right now; otherwise a plain-English reason a "Last marks pull" diagnostic
    must not report PASS, even though the pull itself reported connected=True.

    Found on the Bloomberg PC 2026-09-17: the live feed's first pull runs the instant the
    app starts, almost always before any trade has been uploaded through the browser, so
    live.build_requests() finds nothing open to price and the cycle writes a perfectly
    honest {connected: True, requested: 0, written: 0} -- correct at the moment it ran.
    That status is never refreshed until the next scheduled pull (data.bloomberg.live
    .INTERVAL_SECONDS later, or data.bloomberg.live.LiveFeed.trigger_now() sooner), so a
    user who uploads a blotter and checks diagnostics inside that window sees a stale
    "0 requested" pull reported as PASS while marks the book now needs are missing
    entirely. This compares what the DB needs *right now* (_needed_marks) against what the
    *last recorded pull* actually asked for, not against the live book state at pull time,
    which the stored status has no way to express.

    A real connection failure (connected=False) is not this function's job -- the caller's
    ordinary status.get("connected") handling already reports that as FAIL. This only
    covers the specific "connected fine, asked for nothing, but something is now needed"
    trap."""
    if not status or not status.get("connected"):
        return None
    if status.get("requested", 0):
        return None
    needed = _needed_marks(conn, as_of)
    if not needed:
        return None
    example = needed[0]
    when = status.get("time", "an unknown time")
    return (f"the last recorded pull ({when}) asked Bloomberg for 0 marks, but {len(needed)} "
            f"mark(s) are needed right now for {as_of} (e.g. {example['instrument_id']} "
            f"{example['mark_type']} {example['settle_date']}) -- trades were likely imported "
            "after that pull ran. Press \"Pull Bloomberg now\" (Bloomberg is pulled on request only).")


def _holds_vol_smile(conn: sqlite3.Connection, day: str, pair: str) -> bool:
    """Does `vol_quotes` hold any Bloomberg quote of `pair` dated `day` (the live vol step's
    BBG_BDP rows or the backfill's BBG_BDH history)? A day that holds the pair's quotes is
    never asked for them again."""
    try:
        return conn.execute("SELECT 1 FROM vol_quotes WHERE as_of_date = ? AND pair = ? AND source LIKE 'BBG%' LIMIT 1",
                            (day, pair)).fetchone() is not None
    except sqlite3.OperationalError:       # no table yet: nothing on file
        return False


def _holds_ois_curve(conn: sqlite3.Connection, day: str, ccy: str) -> bool:
    """Does `curve_quotes` hold any Bloomberg OIS quote of `ccy` dated `day`?"""
    try:
        return conn.execute("SELECT 1 FROM curve_quotes WHERE as_of_date = ? AND ccy = ? AND quote_type = 'OIS' "
                            "AND source LIKE 'BBG%' LIMIT 1", (day, ccy)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def inputs_missing(conn: sqlite3.Connection, day: str) -> List[dict]:
    """The OIS curves and vol smiles a past close on `day` prices its FX options and swaps
    from (data.bloomberg.library.history_inputs_needed) that the day does not hold yet:
    [{kind: OIS_CURVE | VOL_SMILE, key: currency | pair}], sorted. What the backfill asks
    Bloomberg's daily history for on that day (2026-09-22); a day that already holds a
    pair's or a currency's quotes -- from a live pull or an earlier backfill -- is never
    asked for them again."""
    from data.bloomberg import library
    out = []
    for item in library.history_inputs_needed(conn, day):
        held = (_holds_vol_smile(conn, day, item["key"]) if item["kind"] == "VOL_SMILE"
                else _holds_ois_curve(conn, day, item["key"]))
        if not held:
            out.append({"kind": item["kind"], "key": item["key"]})
    return out


def close_completeness(conn: sqlite3.Connection, start: str, end: str, today: Optional[str] = None) -> pd.DataFrame:
    """One row per business day in [start, end]: as_of_date, needed, present, complete,
    missing (list of {instrument_id, settle_date, mark_type} still missing that day),
    not_closed (how many of those DO have an official row, only not the close), and
    inputs_missing (2026-09-22: the [{kind, key}] OIS curves / vol smiles the day's options
    and swaps price from that it does not hold, `inputs_missing`; on a day before `today`
    only -- today's inputs are the live pull's). `needed` / `present` / `complete` /
    `missing` stay about the marks: the header's "(N of M needed marks)" reads them.

    The close (user decision 2026-09-21: "for previous or any closes in FX, we need to use
    NY 3pm"; 2026-09-22: every previous close, not only from 2026-09-21 on): on a day
    before `today` (default: the New York book date) an official FX row -- SPOT or
    FWD_OUTRIGHT -- counts as present only when it is stamped at the 15:00 New York close
    of its own date (`data.bloomberg.backfill.is_close_row`, given `today` so it knows how
    far back Bloomberg's intraday history reaches). A row stamped at any other time is
    that day's last live pull (say 11:40) or a 17:00 PX_LAST row written under the old
    cut-over: not a close, so the day is not complete (`not_closed` counts such rows) and
    the backfill replaces it. Only on a day beyond the intraday history (about 140
    business days back) does a 17:00 row count, since no 15:00 value can be asked for.
    Today's rows are live and count as they are; a past day's future counts only at its
    PX_SETTLE (stamped at the settlement, 2026-09-22: a live press's PX_LAST row is not a
    close and the backfill replaces it), and an NDF ticket's fixing date needs its NDF_FIX
    (in `_needed_marks` via the library's MARK_KINDS), so a past fixing date with no
    official fixing is incomplete and the backfill asks for it.

    2026-09-18 (BUILD_PLAN.md section 3 / CLAUDE.md "P&L conventions": Daily/5d/MTD/YTD
    all difference LTD(t) against LTD(t-1bd) etc., and every FX leg's LTD needs the
    FWD_OUTRIGHT for its own settle_date, every future's needs FUTURE_PX): `needed`/
    `present` now cover every mark_type the book needed on that day -- SPOT,
    FWD_OUTRIGHT (per open leg's own settle_date) and FUTURE_PX (per open future) -- via
    the same `_needed_marks` `live.build_requests` and `backfill.backfill` both use, not
    SPOT alone. A day used to count as "complete" from a bare SPOT close even though
    every forward's LTD(t) was unpriced -- found live on the Bloomberg PC's first launch,
    where the header's period cards silently excluded the whole FX book on every
    reference date. `needed == 0` still reports `complete = False` (nothing to confirm
    against), unchanged from before this fix.

    Options (2026-09-18): a close needs the closing SPOT of each option's pair and of its
    USD-conversion pairs, for every day the option was open including its expiry date --
    `_needed_marks(..., historical=True)`, the set `backfill.backfill` fills. It does not
    need a forward at the option's expiry: nothing prices an option on a past date, and
    the backfill cannot build one for a pair held only through options, so requiring it
    left every such day incomplete (and re-requested from Bloomberg) forever.

    2026-09-24: `needed` / `present` / `complete` / `missing` count only the marks a pull
    can ask for, so a future with no verified Bloomberg ticker cannot keep a day incomplete
    and send the backfill back to it on every press; those marks are listed apart, in
    `not_requestable` ([{instrument_id, settle_date, mark_type, reason}] not on file as
    official that day). A non-USD future's USD-conversion SPOT is an ordinary needed SPOT."""
    from data.bloomberg.backfill import business_days, is_close_row
    from datetime import date as _date
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    days = business_days(_date.fromisoformat(start), _date.fromisoformat(end))
    rows = []
    for d in days:
        day = d.isoformat()
        listed = _needed_marks(conn, day, historical=True, include_unrequestable=True)
        needed_items = [i for i in listed if "reason" not in i]
        present = not_closed = 0
        missing = []
        for item in needed_items:
            hit = _official_snap(conn, day, item)
            if hit and (day >= today or is_close_row(item["mark_type"], day, hit[0], today)):
                present += 1
            else:
                missing.append(item)
                not_closed += 1 if hit else 0
        unrequestable = [i for i in listed if "reason" in i and _official_snap(conn, day, i) is None]
        rows.append({"as_of_date": day, "needed": len(needed_items), "present": present,
                     "complete": len(needed_items) > 0 and present >= len(needed_items), "missing": missing,
                     "not_closed": not_closed, "inputs_missing": inputs_missing(conn, day) if day < today else [],
                     "not_requestable": unrequestable})
    return pd.DataFrame(rows, columns=["as_of_date", "needed", "present", "complete", "missing", "not_closed",
                                       "inputs_missing", "not_requestable"])


def _official_snap(conn: sqlite3.Connection, day: str, item: dict):
    """(snapped_at,) of the official mark `item` names on `day`, or None."""
    return conn.execute(
        "SELECT snapped_at FROM marks_official WHERE as_of_date=:as_of AND instrument_id=:instrument_id "
        "AND settle_date=:settle AND mark_type=:mark_type",
        {"as_of": day, "instrument_id": item["instrument_id"], "settle": item["settle_date"],
         "mark_type": item["mark_type"]}).fetchone()


STATUS_ON_FILE = "ON_FILE"


def contract_dates_inventory(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """The commodity futures open on `as_of` whose Bloomberg contract dates the book needs
    (library kind CONTRACT_DATES, 2026-09-24), one row per contract: contract_id,
    bbg_ticker, needed_until, trades, status (ON_FILE when data.contracts.static_dates holds
    them, else MISSING), last_trade_date, first_notice_date, source ('' when missing),
    reason ('' when the pull can ask for them; why not otherwise). Only a MISSING row with
    no reason is what today's pull asks for (library.contract_dates_needed)."""
    from data.bloomberg import library
    columns = ["contract_id", "bbg_ticker", "needed_until", "trades", "status", "last_trade_date",
               "first_notice_date", "source", "reason"]
    found = {}
    for r in library.rows(conn):
        if r["kind"] != library.CONTRACT_DATES or not (r["needed_from"] <= as_of <= r["needed_until"]):
            continue
        entry = found.setdefault(r["key"], {"contract_id": r["key"], "bbg_ticker": r["bbg_ticker"],
                                            "needed_until": r["needed_until"], "trade_ids": set(),
                                            "reason": r["reason"]})
        entry["trade_ids"].add(r["trade_id"])
        entry["needed_until"] = max(entry["needed_until"], r["needed_until"])
    try:
        from data.contracts import static_dates
    except ImportError:
        static_dates = None
    out = []
    for key in sorted(found):
        entry = found[key]
        stored = static_dates(conn, key) if static_dates is not None else None
        out.append({"contract_id": key, "bbg_ticker": entry["bbg_ticker"], "needed_until": entry["needed_until"],
                    "trades": len(entry["trade_ids"]), "status": STATUS_ON_FILE if stored else STATUS_MISSING,
                    "last_trade_date": (stored or {}).get("last_trade_date", ""),
                    "first_notice_date": (stored or {}).get("first_notice_date", ""),
                    "source": (stored or {}).get("source", ""), "reason": entry["reason"]})
    return pd.DataFrame(out, columns=columns)
