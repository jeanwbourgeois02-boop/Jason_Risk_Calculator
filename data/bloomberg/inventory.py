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
unpriced on an otherwise "complete" day) versus how many are official AT THE CLOSE (a past day's row, of any mark type and any
instrument, counts only when stamped 17:00 New York of its own date, the app's one close
stamp since 2026-09-28, `backfill.is_close_row`), so the Data tab can show holes in history at a glance and
the backfill knows which days to ask for. Beside the marks, `inputs_missing` (2026-09-22)
lists the OIS curves and vol smiles the day's FX options price from that the day does
not hold yet (`inputs_missing`); the backfill asks Bloomberg's history for those too.

`mark_checks(conn, as_of)` (2026-09-29, Screens redesign Phase G) is the Data tab's marks
check: every mark of `mark_inventory` checked four ways (arrived, fresh, a sane move, units
against the fills), with the trades it prices and what a problem does to them, MISSING and
CHECK first. It reports only: a flagged mark stays the official mark.
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


LME_CURVE = "LME_CURVE"     # data.bloomberg.library.LME_CURVE: an inventory item, not a mark_type


def _lme_curve_items(conn: sqlite3.Connection, as_of: str, historical: bool = False,
                     include_unrequestable: bool = False) -> List[dict]:
    """One item per LME metal whose curve the book needs on `as_of` (library kind LME_CURVE,
    2026-09-24): {instrument_id: the root id, settle_date: as_of, mark_type: 'LME_CURVE'},
    with a `reason` key when it cannot be asked for (only with `include_unrequestable`)."""
    from data.bloomberg import library
    out, seen = [], set()
    for r in library.needed_on(conn, as_of, historical=historical, include_unrequestable=include_unrequestable):
        if r["kind"] != library.LME_CURVE or r["key"] in seen:
            continue
        seen.add(r["key"])
        item = {"instrument_id": r["key"], "settle_date": as_of, "mark_type": LME_CURVE}
        if not r.get("requestable", True):
            item["reason"] = r["reason"]
        out.append(item)
    return sorted(out, key=lambda i: i["instrument_id"])


def lme_curve_status(conn: sqlite3.Connection, day: str, root_id: str, today: Optional[str] = None) -> dict:
    """Is `root_id`'s LME curve of `day` on file? The rule (2026-09-24): an LME curve is
    complete when its two anchors are official that day -- the cash price (SPOT, settle_date
    = day) and the 3-month outright (FWD_OUTRIGHT at the 3-month date of that day's curve,
    engine.lme.lme_curve_tickers' '3M' pillar). The monthly prompts between and beyond them
    refine it but are not required: every prompt up to three months lies between cash and 3M,
    and the curve's shape there is what the valuation reads. On a day before `today` each
    anchor counts only at the close (`backfill.is_close_row`: stamped 17:00 New York of its
    own date, the one close stamp of every mark since 2026-09-28; a live press's row is not
    a close). {complete, missing: ['cash' | '3M' ...], snapped_at (the later anchor's, ''
    when missing)}."""
    from data.bloomberg import library
    from data.bloomberg.backfill import is_close_row
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    three_m = next((p["settle_date"] for p in library.lme_curve_pillars(root_id, day) if p["kind"] == "3M"), None)
    anchors = [("cash", "SPOT", day)] + ([("3M", "FWD_OUTRIGHT", three_m)] if three_m else [])
    missing, snaps = ([] if three_m else ["3M"]), []
    for name, mark_type, settle in anchors:
        hit = _official_snap(conn, day, {"instrument_id": root_id, "settle_date": settle, "mark_type": mark_type})
        if hit and (day >= today or is_close_row(mark_type, day, hit[0], today, instrument_id=root_id)):
            snaps.append(hit[0])
        else:
            missing.append(name)
    return {"complete": not missing, "missing": missing, "snapped_at": max(snaps) if snaps and not missing else ""}


def mark_inventory(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """One row per mark the book needs on `as_of`:
    instrument_id, settle_date, mark_type, value, source, snapped_at, status.
    status is OFFICIAL (from marks_official) | INTERP (BBG_INTERP present, official absent) |
    MANUAL (MANUAL present, official absent) | MISSING (nothing at all). `value`/`source`/
    `snapped_at` are the row backing that status, or None when MISSING.

    2026-09-24: also a `reason` column, '' for a mark the pull asks for; for one it cannot
    ask for (no verified Bloomberg ticker for a placeholder root) the reason, the mark still
    listed with its status so the gap is in sight.

    Phase 5 (2026-09-24): an LME metal's curve is one more row, mark_type 'LME_CURVE',
    settle_date = as_of, value None, status OFFICIAL when its cash and 3M are on file
    (`lme_curve_status`), else MISSING with `source` naming the missing anchors. An option on
    a future's underlying FUTURE_PX (its Greeks) is an ordinary FUTURE_PX row here."""
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
    for item in _lme_curve_items(conn, as_of, include_unrequestable=True):
        # An LME curve (2026-09-24): OFFICIAL when its cash and 3M are (lme_curve_status);
        # `source` names the anchors still missing otherwise.
        state = lme_curve_status(conn, as_of, item["instrument_id"], today=as_of)
        rows.append({"instrument_id": item["instrument_id"], "settle_date": as_of, "mark_type": LME_CURVE,
                     "value": None, "source": None if state["complete"] else "missing: " + ", ".join(state["missing"]),
                     "snapped_at": state["snapped_at"] or None,
                     "status": STATUS_OFFICIAL if state["complete"] else STATUS_MISSING,
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
    """The OIS curves and vol smiles a past close on `day` prices its FX options from
    (data.bloomberg.library.history_inputs_needed) that the day does not hold yet:
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
    inputs_missing (2026-09-22: the [{kind, key}] OIS curves / vol smiles the day's FX
    options price from that it does not hold, `inputs_missing`; on a day before `today`
    only -- today's inputs are the live pull's). `needed` / `present` / `complete` /
    `missing` stay about the marks: the header's "(N of M needed marks)" reads them.

    The close (user decision 2026-09-28, one close stamp for every instrument, replacing
    the 15:00 New York FX close of 2026-09-21 / 09-22): on a day before `today` (default:
    the New York book date) an official row of any mark type -- SPOT, FWD_OUTRIGHT or
    FUTURE_PX, FX, a future, an option on a future or an LME pillar alike -- counts as
    present only when it is stamped 17:00 New York of its own date, Bloomberg's daily
    close (`data.bloomberg.backfill.is_close_row`; `today` and each item's instrument_id
    are still passed, and no longer change the answer). A row stamped at any other time is
    that day's last live press (say 11:40, the press's real time) or a 15:00 row written
    under the retired FX rule: not a close, so the day is not complete (`not_closed`
    counts such rows) and the backfill asks that day's daily close and replaces it.
    Today's rows are live and count as they are. (The NDF fixing-date need of 2026-09-22
    was retired with the NDFs on 2026-09-24.)

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
    official that day). A non-USD future's USD-conversion SPOT is an ordinary needed SPOT.

    Phase 5 (2026-09-24): an LME forward's cash SPOT and its FWD_OUTRIGHT at the prompt are
    ordinary needed marks, a close at 17:00 New York like every other mark (since
    2026-09-28; until then the LME rows were the one kind stamped at the daily close while
    FX took 15:00), and each LME metal with a ticket open that day adds one item,
    mark_type 'LME_CURVE' (instrument_id the root id, settle_date the day), present when its
    cash and 3M are official at the close (`lme_curve_status`); a missing one carries
    `detail` ("cash, 3M not on file"). An option on a future needs its own FUTURE_PX, its
    underlying future's FUTURE_PX (its Greeks) and, when non-USD, its conversion SPOT on
    every day it is open; its OIS curve is one of the day's `inputs_missing` until held."""
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
            if hit and (day >= today or is_close_row(item["mark_type"], day, hit[0], today,
                                                     instrument_id=item["instrument_id"])):
                present += 1
            else:
                missing.append(item)
                not_closed += 1 if hit else 0
        unrequestable = [i for i in listed if "reason" in i and _official_snap(conn, day, i) is None]
        for item in _lme_curve_items(conn, day, historical=True, include_unrequestable=True):
            state = lme_curve_status(conn, day, item["instrument_id"], today=today)
            if "reason" in item:
                if not state["complete"]:
                    unrequestable.append(item)
                continue
            needed_items.append(item)
            if state["complete"]:
                present += 1
            else:
                missing.append({**item, "detail": ", ".join(state["missing"]) + " not on file"})
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


# --------------------------------------------------------------------------- marks check
# The Data tab's "can I trust today's numbers?" (Screens redesign Phase G, 2026-09-29): every
# official mark the book needs on a day, checked four ways (arrived, fresh, a sane move, units
# against the fills), with the trades each one prices and what a problem does to them. A pure
# read: nothing here writes `marks` or asks Bloomberg (hard rule 8), and a flagged mark is still
# the official mark -- it is reported, never dropped or replaced (hard rules 2 and 3).

CHECK_OK = "OK"
CHECK_CHECK = "CHECK"
CHECK_MISSING = "MISSING"
_CHECK_ORDER = {CHECK_MISSING: 0, CHECK_CHECK: 1, CHECK_OK: 2}

SANE_MULTIPLE = 5.0         # a move beyond 5 x the median absolute daily change is flagged
SANE_HISTORY = 60           # ... over the key's last 60 closes on file
SANE_MIN_CLOSES = 10        # with fewer closes than this, the fixed limit below
SANE_FALLBACK_PCT = 0.08    # 8 % of the previous close
SANE_FLOOR_PCT = 0.005      # never flag a move under 0.5 % (a flat synthetic history has median 0)
UNITS_LOW, UNITS_HIGH = 0.2, 5.0            # mark / average fill outside this: units look wrong
_HUNDRED_LOW, _HUNDRED_HIGH = 50.0, 200.0   # ... and "about 100x off" inside this band (or its inverse)

_LISTED_OPTIONS = ("CMDTY_OPTION", "EQ_OPTION")
_UNITS_PRODUCTS = ("FUTURE", "FX_SPOT", "FX_FWD", "FX_SWAP", "LME_FWD") + _LISTED_OPTIONS

MARK_CHECK_COLUMNS = [
    "instrument_id", "root_id", "exchange", "mark_type", "settle_date", "value", "mark_date", "source",
    "snapped_at", "arrived", "arrived_reason", "fresh", "fresh_reason", "prev_close", "prev_date", "change",
    "change_pct", "usual_move_pct", "sane_limit_pct", "sane", "sane_reason", "avg_fill", "units_ok", "units_reason", "roles", "blocks",
    "trade_names", "blocks_what", "requestable", "status"]


def _num(value) -> Optional[float]:
    """A stored mark value as a float; None when it is not a finite number."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _day_words(day) -> str:
    """'2026-09-28' -> 'Mon 28 Sep'."""
    from datetime import date as _date
    try:
        d = _date.fromisoformat(str(day))
    except (TypeError, ValueError):
        return str(day or "")
    return f"{d:%a} {d.day} {d:%b}"


def _key_sql(mark_type: str) -> str:
    # A SPOT is keyed on its own date (settle_date = as_of_date), so its series is the
    # instrument's SPOT on every day; any other mark keeps its settle date.
    return "settle_date = as_of_date" if mark_type == "SPOT" else "settle_date = :settle"


def _series(conn: sqlite3.Connection, instrument_id: str, mark_type: str, settle: str, through: str,
            limit: int) -> List[tuple]:
    """The official marks of one key on or before `through`, newest first:
    [(as_of_date, value, source, snapped_at)]."""
    return conn.execute(
        f"SELECT as_of_date, value, source, snapped_at FROM marks_official WHERE instrument_id = :iid "
        f"AND mark_type = :mt AND {_key_sql(mark_type)} AND as_of_date <= :through "
        f"ORDER BY as_of_date DESC LIMIT :n",
        {"iid": instrument_id, "mt": mark_type, "settle": settle, "through": through, "n": limit}).fetchall()


def _first_later(conn: sqlite3.Connection, instrument_id: str, mark_type: str, settle: str,
                 after: str) -> Optional[str]:
    """The first date after `after` holding an official mark of the key, or None."""
    row = conn.execute(
        f"SELECT MIN(as_of_date) FROM marks_official WHERE instrument_id = :iid AND mark_type = :mt "
        f"AND {_key_sql(mark_type)} AND as_of_date > :after",
        {"iid": instrument_id, "mt": mark_type, "settle": settle, "after": after}).fetchone()
    return row[0] if row and row[0] else None


def _curve_on_file(conn: sqlite3.Connection, instrument_id: str, as_of: str) -> str:
    """For a forward: 'day' when the day's own curve holds an official SPOT or forward of the
    instrument, 'other' when only some other day's does, '' when none ever did (the near-marks
    rule, hard rule 2, reads a forward along these)."""
    row = conn.execute(
        "SELECT MAX(as_of_date = :d), COUNT(*) FROM marks_official WHERE instrument_id = :iid "
        "AND mark_type IN ('SPOT', 'FWD_OUTRIGHT')", {"iid": instrument_id, "d": as_of}).fetchone()
    if not row or not row[1]:
        return ""
    return "day" if row[0] else "other"


def _book_day_of(stamp: str) -> Optional[str]:
    """The book date a mark stamped at `stamp` belongs to (the 17:00 New York roll, live.book_today)."""
    from datetime import datetime
    from data.bloomberg.live import book_today
    try:
        return book_today(datetime.fromisoformat(str(stamp))).isoformat()
    except (TypeError, ValueError):
        return None


def _names_text(names: List[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) <= 3:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return ", ".join(names[:2]) + f" and {len(names) - 2} more"


def _whose(names: List[str], what: str) -> str:
    """"COPAR3's Daily uses ..." for one trade name, "COPAR3 and CATTLE: Daily uses ..." for more."""
    text = _names_text(names)
    if not text:
        return what[0].upper() + what[1:]
    return f"{text}'s {what}" if len(names) == 1 else f"{text}: {what}"


def _trade_names(trade_ids, trades: dict) -> List[str]:
    """Each trade's PBRoot trade name (trades.strategy), the trade id where it has none; once each."""
    out = []
    for tid in trade_ids:
        name = (trades.get(tid) or {}).get("strategy") or tid
        if name not in out:
            out.append(name)
    return out


def _missing_effect(role: str, estimate: tuple, yesterday: str) -> str:
    """What a missing mark does to the trades it prices in one role. `estimate` is (how, when):
    ('earlier', d) a mark of the key on file before, ('later', d) only after, ('curve', 'day' |
    'other') a forward read along a curve, ('', '') nothing to work from."""
    how, when = estimate
    if how == "earlier":
        close = "yesterday's close" if when == yesterday else f"the {_day_words(when)} close"
        spot = "yesterday's spot" if when == yesterday else f"the {_day_words(when)} spot"
        return {"CONVERSION": f"USD conversion uses {spot}",
                "UNDERLYING": f"Greeks use {close}"}.get(role, f"Daily uses {close}")
    if how == "later":
        return {"CONVERSION": f"USD conversion estimated from the {_day_words(when)} spot",
                "UNDERLYING": f"Greeks estimated from the {_day_words(when)} close"}.get(
                    role, f"P&L estimated from the {_day_words(when)} close")
    if how == "curve":
        return "P&L estimated along " + ("the day's curve" if when == "day" else "the nearest days' curves")
    return {"CONVERSION": "no USD P&L", "UNDERLYING": "no Greeks (P&L unaffected)"}.get(role, "no P&L")


def _median(values: List[float]) -> float:
    s, n = sorted(values), len(values)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def mark_checks(conn: sqlite3.Connection, as_of: str, today: Optional[str] = None) -> pd.DataFrame:
    """Every official mark the book needs on `as_of`, checked (Screens redesign Phase G,
    2026-09-29: the Data tab's marks check, "can I trust today's numbers?"). One row per mark of
    `mark_inventory` (the library's SPOT / FWD_OUTRIGHT / FUTURE_PX needs in force that day,
    requestable or not; its LME_CURVE item is left out, the curve's cash and prompt marks are rows):

    - instrument_id, root_id (the contract root, '' for an FX pair), exchange (contract-master's,
      '' for an FX pair or an unknown root; the plain name is the ui's `formatting.contract_name`),
      mark_type, settle_date.
    - value, mark_date, source, snapped_at: the official mark dated `as_of`; when there is none,
      the last official mark of the same key before it (mark_date says which day), else None.
    - arrived (bool), arrived_reason: an official mark dated `as_of` is on file.
    - fresh (bool), fresh_reason: that mark is `as_of`'s own -- on a past day the 17:00 New York
      close (`backfill.is_close_row`), on today a mark stamped within today's book day
      (`live.book_today` of its stamp) -- not a row carried or re-dated from another time.
    - prev_close, prev_date, change, change_pct (a fraction: 0.012 = 1.2 %): against the last
      official mark of the key before `as_of`.
    - sane (bool), sane_reason: |change| within SANE_MULTIPLE (5) x the median absolute daily
      change of the key's last SANE_HISTORY (60) closes on file, never under SANE_FLOOR_PCT
      (0.5 %) of the previous close; with fewer than SANE_MIN_CLOSES (10) closes, within
      SANE_FALLBACK_PCT (8 %). A stored value that is not a number is not sane (a data error,
      never estimated: hard rule 2).
    - usual_move_pct: that median absolute daily change as a fraction of the previous close
      (0.018 = 1.8 %); None with fewer than SANE_MIN_CLOSES closes or no previous close.
    - sane_limit_pct: the limit applied, as a fraction of the previous close:
      max(SANE_MULTIPLE x usual_move_pct, SANE_FLOOR_PCT), or SANE_FALLBACK_PCT when the history
      is short; None with no previous close. sane_reason names both ("moved +12.0 % in a day;
      its usual move is 1.8 %, the limit 9.0 %").
    - avg_fill, units_ok (bool), units_reason: the mark against the |quantity|-weighted average
      fill of the open trades it prices directly (futures, listed options, FX spot / forwards,
      LME tickets on the same instrument; never an FX option's premium, a conversion spot or an
      option's underlying). A ratio outside UNITS_LOW-UNITS_HIGH (0.2-5) is flagged, "units look
      wrong (about 100x off: cents vs dollars?)" inside 50-200 or its inverse, else the factor
      seen. A listed option's price can fall or rise far from its fill, so only the 100x band
      flags one.
    - roles: the library roles the mark plays (PAIR, CONVERSION, UNDERLYING), comma-joined.
    - blocks: the trade ids this mark prices (a list); trade_names: their PBRoot trade names
      (trades.strategy, the trade id where there is none), comma-joined.
    - blocks_what: what the problem does to them, in words: a missing mark the near-marks rule
      or the fill will estimate ("COPAR3's Daily uses yesterday's close"), one nothing can price
      ("COPAR3: no P&L"), a flagged mark still in use ("COPAR3's P&L uses this price: check
      it"); '' on an OK row.
    - requestable: False for a mark no pull can ask for (library.unrequestable_reason).
    - status: MISSING (not arrived) | CHECK (arrived, a check failed) | OK; sorted MISSING,
      CHECK, OK, then by instrument.

    A pure read: never writes marks, never asks Bloomberg (it may bring the library up to date
    first, as every library reader does). A flagged mark stays the official mark the valuation
    reads; this only reports it."""
    from datetime import date as _date, timedelta
    from data.bloomberg import library
    from data.bloomberg.backfill import business_days, is_close_row
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    inv = mark_inventory(conn, as_of)
    inv = inv[inv["mark_type"] != LME_CURVE]
    if inv.empty:
        return pd.DataFrame([], columns=MARK_CHECK_COLUMNS)

    # Which trades each mark prices, and in which role (the keys mark_inventory uses).
    uses: dict = {}
    for r in library.needed_on(conn, as_of, include_unrequestable=True):
        if r["kind"] not in library.MARK_KINDS:
            continue
        settle = as_of if r["kind"] == "SPOT" else r["settle_date"]
        entry = uses.setdefault((r["key"], settle, r["kind"]), {"trades": [], "roles": {}})
        if r["trade_id"] not in entry["trades"]:
            entry["trades"].append(r["trade_id"])
        role_ids = entry["roles"].setdefault(r["role"], [])
        if r["trade_id"] not in role_ids:
            role_ids.append(r["trade_id"])
    trade_ids = sorted({t for e in uses.values() for t in e["trades"]})
    trades: dict = {}
    for i in range(0, len(trade_ids), 500):
        chunk = trade_ids[i:i + 500]
        for tid, iid, product, qty, price, strategy in conn.execute(
                "SELECT trade_id, instrument_id, product, quantity, price, strategy FROM trades_official "
                f"WHERE trade_id IN ({','.join('?' * len(chunk))})", chunk):
            trades[tid] = {"instrument_id": iid, "product": product, "quantity": _num(qty),
                           "price": _num(price), "strategy": str(strategy or "").strip()}
    inst_ids = sorted(set(inv["instrument_id"]))
    base_of: dict = {}
    for i in range(0, len(inst_ids), 500):
        chunk = inst_ids[i:i + 500]
        base_of.update(dict(conn.execute(
            f"SELECT instrument_id, base_ccy FROM instruments WHERE instrument_id IN ({','.join('?' * len(chunk))})",
            chunk)))
    exchanges: dict = {}

    def exchange_of(root_id: str) -> str:
        if root_id not in exchanges:
            try:
                from data.contracts import get_root
                exchanges[root_id] = get_root(root_id).exchange
            except Exception:       # an unknown root, or no contract master: the ui names it
                exchanges[root_id] = ""
        return exchanges[root_id]

    day = _date.fromisoformat(as_of)
    back = business_days(day - timedelta(days=10), day - timedelta(days=1))
    yesterday = back[-1].isoformat() if back else ""
    live_day = as_of >= today

    out = []
    for rec in inv.to_dict("records"):
        iid, settle, mark_type = rec["instrument_id"], rec["settle_date"], rec["mark_type"]
        root_id = base_of.get(iid) or ""
        root_id = root_id if library.is_contract_root(root_id) else ""
        use = uses.get((iid, settle, mark_type), {"trades": [], "roles": {}})
        requestable = not rec.get("reason")
        series = _series(conn, iid, mark_type, settle, as_of, SANE_HISTORY + 1)
        today_row = series[0] if series and series[0][0] == as_of else None
        past = [s for s in series if s[0] < as_of]
        arrived = today_row is not None
        shown = today_row if arrived else (past[0] if past else None)
        value = _num(shown[1]) if shown else None
        row = {"instrument_id": iid, "root_id": root_id, "exchange": exchange_of(root_id) if root_id else "",
               "mark_type": mark_type, "settle_date": settle,
               "value": value if value is not None else (shown[1] if shown else None),
               "mark_date": shown[0] if shown else None, "source": shown[2] if shown else None,
               "snapped_at": shown[3] if shown else None, "roles": ", ".join(sorted(use["roles"])),
               "blocks": list(use["trades"]), "trade_names": ", ".join(_trade_names(use["trades"], trades)),
               "requestable": requestable}

        # 1. Arrived: an official mark dated as_of.
        if arrived:
            row["arrived"], row["arrived_reason"] = True, ""
        else:
            why = f"no official mark for {_day_words(as_of)}"
            if rec["status"] in (STATUS_INTERP, STATUS_MANUAL):
                why += f" (only a {rec['source']} row, never official)"
            if past:
                why += f"; last on file {_day_words(past[0][0])}"
            if not requestable:
                why += f"; not asked of Bloomberg: {rec['reason']}"
            elif live_day:
                why += "; press Pull Bloomberg now"
            row["arrived"], row["arrived_reason"] = False, why

        # 2. Fresh: as_of's own mark, not one carried or re-dated.
        if not arrived:
            row["fresh"] = False
            row["fresh_reason"] = f"carried from {_day_words(past[0][0])}" if past else "nothing on file"
        else:
            stamp = str(today_row[3] or "")
            is_close = is_close_row(mark_type, as_of, stamp, today, instrument_id=iid)
            if not live_day:
                ok = is_close
                reason = "" if ok else (f"stamped {stamp or 'without a time'}, not the {_day_words(as_of)} close "
                                        "(the next pull's backfill replaces it)")
            else:
                day_of = _book_day_of(stamp)
                ok = is_close or day_of == as_of
                reason = "" if ok else (f"dated {_day_words(as_of)} but stamped {stamp or 'without a time'}"
                                        + (f" (book day {_day_words(day_of)})" if day_of else "")
                                        + ": carried, not today's")
            row["fresh"], row["fresh_reason"] = ok, reason

        # 3. A sane move against the previous close.
        prev = next(((d, _num(v)) for d, v, _s, _t in past if _num(v) is not None), None)
        row["prev_close"], row["prev_date"] = (prev[1], prev[0]) if prev else (None, None)
        row["change"] = row["change_pct"] = None
        row["usual_move_pct"] = row["sane_limit_pct"] = None
        median = None
        closes = [c for c in (_num(v) for _d, v, _s, _t in past[:SANE_HISTORY]) if c is not None]
        if prev is not None:
            if len(closes) >= SANE_MIN_CLOSES:
                median = _median([abs(a - b) for a, b in zip(closes, closes[1:])])
                if prev[1]:
                    row["usual_move_pct"] = median / abs(prev[1])
                    row["sane_limit_pct"] = max(SANE_MULTIPLE * row["usual_move_pct"], SANE_FLOOR_PCT)
            else:
                row["sane_limit_pct"] = SANE_FALLBACK_PCT
        if arrived and value is None:
            row["sane"], row["sane_reason"] = False, f"stored value {today_row[1]!r} is not a number"
        elif not arrived:
            row["sane"], row["sane_reason"] = True, "no mark to compare"
        elif prev is None:
            row["sane"], row["sane_reason"] = True, "no earlier close to compare"
        else:
            change = value - prev[1]
            row["change"] = change
            row["change_pct"] = change / abs(prev[1]) if prev[1] else None
            if median is not None:
                limit = max(SANE_MULTIPLE * median, SANE_FLOOR_PCT * abs(prev[1]))
            else:
                limit = SANE_FALLBACK_PCT * abs(prev[1])
            ok = abs(change) <= limit
            row["sane"] = ok
            if ok:
                row["sane_reason"] = ""
            else:
                when = "in a day" if prev[0] == yesterday else f"since {_day_words(prev[0])}"
                moved = (f"moved {100 * row['change_pct']:+.1f} % {when}" if row["change_pct"] is not None
                         else f"moved {change:+,.6g} {when}")
                if row["usual_move_pct"] is not None:
                    rule = (f"its usual move is {100 * row['usual_move_pct']:.1f} %, "
                            f"the limit {100 * row['sane_limit_pct']:.1f} %")
                elif median is not None:      # a previous close of 0: no percentage to give
                    rule = f"its usual move is {median:,.6g}, the limit {limit:,.6g}"
                else:
                    rule = (f"only {len(closes)} closes on file, "
                            f"the limit {100 * SANE_FALLBACK_PCT:.1f} %")
                row["sane_reason"] = f"{moved}; {rule}"

        # 4. Units against the fills of the trades it prices directly.
        direct = [t for t in use["roles"].get(library.ROLE_PAIR, [])
                  if (trades.get(t) or {}).get("instrument_id") == iid
                  and (trades.get(t) or {}).get("product") in _UNITS_PRODUCTS]
        fills = [(abs(trades[t]["quantity"] or 0.0), trades[t]["price"]) for t in direct
                 if trades[t]["price"] is not None and trades[t]["price"] > 0]
        weight = sum(w for w, _p in fills)
        avg_fill = (sum(w * p for w, p in fills) / weight if weight
                    else (sum(p for _w, p in fills) / len(fills) if fills else None))
        row["avg_fill"] = avg_fill
        if avg_fill is None:
            row["units_ok"], row["units_reason"] = True, "no fill to compare"
        elif value is None or value <= 0:
            row["units_ok"], row["units_reason"] = True, "no positive mark to compare"
        else:
            ratio = value / avg_fill
            hundred = (_HUNDRED_LOW <= ratio <= _HUNDRED_HIGH) or (1 / _HUNDRED_HIGH <= ratio <= 1 / _HUNDRED_LOW)
            option = any(trades[t]["product"] in _LISTED_OPTIONS for t in direct)
            flagged = hundred or (not option and not UNITS_LOW <= ratio <= UNITS_HIGH)
            row["units_ok"] = not flagged
            if hundred:
                row["units_reason"] = (f"units look wrong (about 100x off: cents vs dollars?): mark {value:,.6g} "
                                       f"against an average fill of {avg_fill:,.6g}")
            elif flagged:
                factor = f"{ratio:,.3g}x" if ratio >= 1 else f"1/{1 / ratio:,.3g} of"
                row["units_reason"] = f"units look wrong: mark {value:,.6g} is {factor} the average fill {avg_fill:,.6g}"
            else:
                row["units_reason"] = ""

        # Status, and what a problem does to the trades it prices.
        if not arrived:
            status = CHECK_MISSING
        elif row["fresh"] and row["sane"] and row["units_ok"]:
            status = CHECK_OK
        else:
            status = CHECK_CHECK
        row["status"] = status
        estimate = None
        effects = []
        for role in (library.ROLE_PAIR, library.ROLE_CONVERSION, library.ROLE_UNDERLYING):
            tids = use["roles"].get(role)
            if not tids:
                continue
            names = _trade_names(tids, trades)
            if status == CHECK_MISSING:
                if estimate is None:
                    # The near-marks rule's order (valuation._mark_near): a forward along the
                    # day's own curve first, then the same mark on the nearest closes in time.
                    curve = _curve_on_file(conn, iid, as_of) if mark_type == "FWD_OUTRIGHT" else ""
                    later = None if past else _first_later(conn, iid, mark_type, settle, as_of)
                    estimate = (("curve", "day") if curve == "day" else ("earlier", past[0][0]) if past
                                else ("later", later) if later else ("curve", curve) if curve else ("", ""))
                effect = _missing_effect(role, estimate, yesterday)
                effects.append(f"{_names_text(names)}: {effect}" if effect.startswith("no ")
                               else _whose(names, effect))
            elif status == CHECK_CHECK:
                if value is None:
                    effects.append(f"{_names_text(names)}: no P&L (the stored price is not a number)")
                else:
                    what = {library.ROLE_CONVERSION: "USD conversion uses this spot",
                            library.ROLE_UNDERLYING: "Greeks use this price"}.get(role, "P&L uses this price")
                    effects.append(_whose(names, what + ": check it"))
        row["blocks_what"] = "; ".join(effects)
        out.append(row)
    out.sort(key=lambda r: (_CHECK_ORDER[r["status"]], r["instrument_id"], r["mark_type"], r["settle_date"]))
    return pd.DataFrame(out, columns=MARK_CHECK_COLUMNS)
