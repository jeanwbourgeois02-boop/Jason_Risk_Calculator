"""Live Bloomberg feed for the FX cash ladder.

If `blpapi` is importable and a Bloomberg API service answers (Terminal / B-PIPE on
localhost:8194 by default), each requested pull of `LiveFeed` fetches:
  * SPOT (PX_LAST, live ReferenceDataRequest) for every FX pair with an open leg, every
    open FX option's pair, and the USD-conversion pairs a cross's legs or an option's
    base / quote currency need (each asked for once);
  * FWD_OUTRIGHT for every open leg's own settle date (direct broken-date request first,
    standard-tenor interpolation as fallback, exactly as data/bloomberg/pull_marks.py
    does). There is no shared WORKDAY(as_of,5) maturity request any more: BUILD_PLAN.md
    section 2 marks each leg at the outright for its own settle_date only; the
    reconciliation view's single-maturity marks are entered manually on the Market data
    tab (see data/bloomberg/marks_csv.py::export_request, which still emits that request
    for the workbook comparison and is unchanged);
  * FUTURE_PX for every FUTURE instrument with an open leg, at the contract's own expiry
    (settle_date); first (2026-09-24), Bloomberg's contract dates (FUT_LAST_TRADE_DT,
    FUT_NOTICE_FIRST) of the futures the library lists under CONTRACT_DATES, stored in
    contract_static and applied to the futures, so that expiry is Bloomberg's
    (`contract_dates_step`); a future with no Bloomberg ticker is never asked for.
  * (2026-09-24, Phase 5) an option on a commodity future's price (FUTURE_PX, live mid) and
    its underlying future's, its contract dates (OPT_EXPIRE_DT / LAST_TRADEABLE_DT), and the
    LME curves of the open LME forwards (`_lme_step`: cash as SPOT, pillars and prompts as
    FWD_OUTRIGHT).
Then the OIS curve quotes the options need, bootstrapped into `curves` (`_curves_step`),
the FX vol smiles (`_vol_step`), the options' pricing (`_options_step`, FX options and
options on futures) and the ledger.
The macro trader's NDF prices (NDF_1M, NDF_FIX), overnight fixings, swap pricing, index
levels and dividend yields left the pull on 2026-09-24 (commodity conversion Phase 2).
Rows are written to `marks` with INSERT OR REPLACE (same primary key each cycle, new
`snapped_at`), sources BBG_BFXFORWARD (official) / BBG_INTERP (fallback, never official).

If Bloomberg is NOT available nothing is written and nothing is invented: `rates_from_marks`
returns only what the marks table holds, and the status file says why the feed is down.

Every cycle writes a status JSON next to the database (`<db>.bloomberg_status.json`)
listing each requested (instrument, mark_type, settle_date) as OK or FAILED with the
value or the failure detail. `py -3 -m data.bloomberg.live --status` prints it;
`--once` runs a single pull. Since 2026-09-29 (Phase G, "Smooth and contained") each step
of the cycle runs on its own and its outcome is under status["steps"]; a running pull
publishes status["progress"] into the same file as it goes (see `pull_once`).

On request only (user decision 2026-09-21, replacing the 15-minute cadence of the same
day: "make it only pull the bloomberg info on request - no automatic"): a cycle runs when
"Pull Bloomberg now" is pressed (`LiveFeed.trigger_now()`), never at start, on a timer,
or after an upload, a manual trade or an edit. What a cycle asks for is READ from the
Bloomberg library (data/bloomberg/library.py, table `bbg_library`): what the trades on
file need for their P&L, written when trades come in. A cycle opens ONE blpapi session
and shares it between the FX marks, the OIS quotes and the vol quotes
(`_SharedSession`), and records where its time went in `status["timings"]` (see
`pull_once`).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# No pull is scheduled any more (2026-09-21: Bloomberg is pulled on request only). What is
# left of the old 15-minute cadence is the screens' own safety-net re-read of the marks on
# file (ui/feed_controls.py::safety_refresh_ms), which asks nothing of Bloomberg.
INTERVAL_SECONDS = 900
# A SPOT mark older than this reads STALE on the Ladder and the Market data tab: a flag
# that the rate is not fresh, never a reason to drop it.
STALE_AFTER_SECONDS = 2 * INTERVAL_SECONDS + 300
SRC_SPOT_FWD = "BBG_BFXFORWARD"
SRC_INTERP = "BBG_INTERP"
# Keys of status["timings"], seconds per step of one cycle (see pull_once). "curves" was
# "rates" until 2026-09-24, when the step stopped pricing swaps.
TIMING_KEYS = ("session", "spot", "forwards", "futures", "curves", "vol", "options", "ledger", "total")


# --------------------------------------------------------------------------- ledger result
def ledger_block(led, as_of: Optional[str] = None) -> dict:
    """The status file's record of one `engine.pnl.ledger.realise_settled` call (user yes,
    2026-09-22: the pull status records what the ledger's re-freeze did). One shape for the
    live pull's `status["ledger"]`, each backfill day, the backfill's closing step and the
    snapshot import: {as_of_date (when given), realised, unrealisable, repaired, refrozen,
    kept, refrozen_count, refrozen_summary}. `refrozen` is the ledger's own list of
    {trade_id, product, mark_type, spot_as_of_date, pnl_from, pnl_to, why} as given; `kept`
    its [{trade_id, product, reason}] of rows it could not recompute and left alone.
    Read defensively: an older ledger returning bare trade ids under `refrozen` (or no
    `refrozen` / `kept` at all) still gives a block, each bare id as {"trade_id": id}, so a
    ledger of another shape never breaks a pull. `refrozen_summary` is the one sentence
    the status line shows when the count is above 0, '' otherwise."""
    led = led if isinstance(led, dict) else {}

    def _entries(key: str) -> List[dict]:
        out = []
        for entry in led.get(key) or []:
            out.append(dict(entry) if isinstance(entry, dict) else {"trade_id": str(entry)})
        return out

    refrozen, kept = _entries("refrozen"), _entries("kept")
    n = len(refrozen)
    block = {"realised": led.get("realised"), "unrealisable": list(led.get("unrealisable") or []),
             "repaired": list(led.get("repaired") or []), "refrozen": refrozen, "kept": kept,
             "refrozen_count": n, "refrozen_summary": refrozen_summary(n)}
    return {"as_of_date": as_of, **block} if as_of is not None else block


def refrozen_summary(n: int) -> str:
    """'' for none, else 'N settled trade(s) re-frozen at the close'."""
    return f"{n} settled trade{'' if n == 1 else 's'} re-frozen at the close" if n else ""


# --------------------------------------------------------------------------- availability
def availability(host: str = "127.0.0.1", port: int = 8194, timeout: float = 0.5) -> Tuple[bool, str]:
    """(True, '') if blpapi imports and the API port accepts a TCP connection.

    The literal string "localhost" is resolved to 127.0.0.1 directly rather than left to
    getaddrinfo: on a PC without a Bloomberg Terminal, "localhost" resolves to both ::1
    and 127.0.0.1, and when nothing answers on ::1 (IPv6 loopback present but nothing
    bound there -- the common case) socket.create_connection tries it first and pays the
    full timeout before ever trying the working IPv4 address. Measured 2.07s for a single
    call at timeout=1.0 on a non-Bloomberg PC (2026-09-17); doubled again because
    data.bloomberg.backfill.start_auto_backfill runs its own separate availability() probe
    of the same host:port moments later inside the same create_app(start_feed=True) call
    (ui/app.py:246-259), for ~4.18s total before the app's first response. Callers that
    pass an explicit non-"localhost" host (a remote B-PIPE address, or an explicit
    "127.0.0.1"/"::1") are left untouched. timeout defaults to 0.5s (was an implicit 1.0s)
    since a loopback port either answers almost immediately or is not listening at all."""
    try:
        import blpapi  # noqa: F401
    except ImportError:
        return False, "blpapi is not installed on this computer"
    import socket
    connect_host = "127.0.0.1" if host == "localhost" else host
    try:
        with socket.create_connection((connect_host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, f"no Bloomberg API service on {host}:{port} ({exc})"


# --------------------------------------------------------------------------- status file
def status_path(db_path) -> Path:
    p = Path(db_path)
    return p.with_name(p.name + ".bloomberg_status.json")


_STATUS_LOCK = threading.Lock()


def _replace_status_file(target: Path, status: dict) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, target)


def write_status(db_path, status: dict) -> None:
    """Atomic replace (temp file + os.replace) under a process-wide lock. The feed
    thread, the Market data tab's "Pull now" callback and the backfill thread all rewrite
    this file; a reader must never see a half-written JSON, which `read_status` would
    turn into None ("no pull recorded yet") for a pull that just succeeded (2026-09-18
    audit).

    The "backfill" block on file is carried over when `status` has none of its own
    (2026-09-21): every full rewrite here is a PULL's status, and it used to wipe the
    backfill's progress and per-day reasons (`patch_status`) on every cycle and on every
    "Pull now" click -- the very reasons the header reads to say why a past close is
    missing. The caller's dict is left as it was."""
    with _STATUS_LOCK:
        if "backfill" not in status:
            kept = (read_status(db_path) or {}).get("backfill")
            if kept is not None:
                status = {**status, "backfill": kept}
        _replace_status_file(status_path(db_path), status)


def patch_status(db_path, key: str, patch: dict) -> dict:
    """Read-modify-write of one top-level key (e.g. "backfill") under the same lock
    `write_status` takes, so a concurrent full rewrite by the feed can neither tear the
    file nor be lost between this function's read and its write. Returns the merged
    status. Meant for data/bloomberg/backfill.py's progress publisher."""
    with _STATUS_LOCK:
        current = read_status(db_path) or {}
        current[key] = {**(current.get(key) or {}), **patch}
        _replace_status_file(status_path(db_path), current)
    return current


def read_status(db_path) -> Optional[dict]:
    p = status_path(db_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# The book's day turns at 17:00 New York (05:00 Hong Kong), the FX day roll -- for every
# date the app works with: the marks a pull stamps, the curves / vol / options steps, the
# ledger's freeze date, the backfill's "past", the screens' as-of (ui/tabs/controls.py::
# today_ny delegates to book_today and re-exports this constant). The one rule; nothing else
# decides the day. It is also the hour of the app's one close stamp
# (pull_marks.CLOSE_HOUR_NY, 2026-09-28: every instrument's past close is its own daily
# Bloomberg close, stamped 17:00 New York of its date).
ROLLOVER_HOUR_NY = 17


def book_today(now: Optional[datetime] = None) -> date:
    """The book date: the New York date until 17:00 New York, the NEXT date from 17:00 on.

    User decision 2026-09-22: "I want to clarify the time today, so that all daily pnl is
    calculated from the NY 3pm the day before. I am based in HK, so basically all date
    rollover at hkt 5am"; earlier the same day: "no only roll to new day after new york
    5pm" and "I am a hk user, I expect the date to reset by New york 5pm everyday. So the
    full hk session until new york 5pm is a full day, then a fresh day from hk 5am". HKT
    05:00 is 17:00 New York (ROLLOVER_HOUR_NY).

    Why one boundary for everything: Daily is the live LTD against the previous day's
    daily closes (each instrument's own Bloomberg daily close, stamped 17:00 New York of
    its date, the app's one close stamp since 2026-09-28; it was a 15:00 New York FX close
    from 2026-09-21 until then). Until 2026-09-22 the screens rolled at 17:00 New York
    (ui/tabs/controls.py::today_ny) while this function still gave the New York calendar
    date, so between 17:00 and midnight New York -- 05:00 to 12:00 Hong Kong, the user's
    morning -- the screen valued D+1, a pull wrote D's marks, D was not yet "past" for the
    backfill (its row on file stayed the last live press instead of the close), D+1 had no
    marks of its own and was carried from D, and Daily read 0 for the whole book. With the
    pull, the curves / vol / options steps, realise_settled, the backfill's "past" and the
    screens all on this one date, a pull at 18:00 New York on the 22nd writes marks dated
    the 23rd, the 22nd is a past day whose daily closes the same button press fetches, and
    Daily is live against them. A live row dated D+1 that was snapped on the evening of D
    is that day's live mark until D+1 turns past, when the backfill replaces it with D+1's
    daily close as it does any row not stamped at the close (backfill.is_close_row: a past
    row is a close iff stamped 17:00 New York of its own as_of_date; every live row carries
    the real press time, its FUTURE_PX rows included).

    Never the PC's local date: a PC in Asia is a day ahead of New York until early
    afternoon, and marks stamped with its local date would be a day away from the date
    every screen looks up (found on the first live run, 2026-09-17). `now` is for tests:
    any tz-aware datetime, converted to America/New_York; a naive one is taken as New York
    time."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    if now is None:
        now = datetime.now(ny)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ny)
    ny_now = now.astimezone(ny)
    return ny_now.date() + timedelta(days=1 if ny_now.hour >= ROLLOVER_HOUR_NY else 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- requests
# trades_official (2026-09-17): legacy BNP-sourced trades never reach P&L or delta, so
# no mark is requested for them either -- the same view every engine query reads.
_OPEN_FX_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY i.instrument_id, l.settle_date
"""

_OPEN_FUTURE_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FUTURE' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY i.instrument_id, l.settle_date
"""

# A cross (neither leg is USD, e.g. EURSEK) needs its own outright, but delta/P&L still
# convert each leg's currency to USD at SPOT (CLAUDE.md's delta query: "the SPOT join ...
# is a LEFT JOIN so that a missing spot mark surfaces as a NULL delta; the engine must
# raise if any resulting delta is NULL, never drop the leg silently"). Found 2026-09-17
# (ladder agent): a book holding only a cross, with no direct trade in either leg's own
# USD pair, left both currencies' usd_delta NaN forever because nothing ever requested
# those USD pairs' SPOT. _cross_usd_legs below is that missing request, shared by
# build_requests (live pull) and inventory._needed_marks (coverage/diagnostics) so the two
# can never drift apart.
_MAJOR_QUOTE_CCYS = {"AUD", "EUR", "GBP", "NZD", "XAU", "XAG"}  # quote as XXXUSD; everything else is USDXXX

_OPEN_CROSS_LEG_CCYS_SQL = """
SELECT DISTINCT i.base_ccy, i.quote_ccy
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
  AND i.base_ccy != 'USD' AND i.quote_ccy != 'USD'
"""


def _usd_pair_name(ccy: str) -> str:
    """Conventional USD-pair spelling for `ccy`: '<ccy>USD' for the majors/metals that
    quote against the dollar (AUD, EUR, GBP, NZD, XAU, XAG), 'USD<ccy>' for everything
    else -- the same convention CLAUDE.md's xlsx section and the delta query's pair join
    (`i.base_ccy || i.quote_ccy`) assume elsewhere."""
    return f"{ccy}USD" if ccy in _MAJOR_QUOTE_CCYS else f"USD{ccy}"


def _usd_pair_rows(conn: sqlite3.Connection, ccys) -> List[dict]:
    """One row per USD-conversion pair for each non-USD currency in `ccys`:
    {ccy, pair_name, instrument_id, bbg_ticker, has_instrument}. The one place a currency
    becomes its USD pair (orientation from `_usd_pair_name`, nowhere else), shared by the
    forwards' cross-leg path (`_cross_usd_legs`) and the options' path (`_option_usd_legs`).

    `instrument_id`/`bbg_ticker` come from the `instruments` row when one already exists
    for the conventional pair name (canonical orientation, e.g. EURUSD, USDSEK); otherwise
    they are the conventional pair name itself and '<pair> Curncy'. `has_instrument` tells
    the caller whether a pulled SPOT for this pair can be written as things stand:
    `write_marks` drops any row whose instrument_id isn't a known instrument, so the
    writable call sites (`build_requests`, `backfill.backfill`) create the plain pair row
    first (`_ensure_fx_instruments`). USD itself needs no conversion and is skipped."""
    out = []
    for ccy in sorted({c for c in ccys if c and c != "USD"}):
        pair_name = _usd_pair_name(ccy)
        row = conn.execute("SELECT instrument_id, bbg_ticker FROM instruments WHERE instrument_id = ?",
                           (pair_name,)).fetchone()
        if row is not None:
            out.append({"ccy": ccy, "pair_name": pair_name, "instrument_id": row[0], "bbg_ticker": row[1],
                       "has_instrument": True})
        else:
            out.append({"ccy": ccy, "pair_name": pair_name, "instrument_id": pair_name,
                       "bbg_ticker": f"{pair_name} Curncy", "has_instrument": False})
    return out


def _cross_usd_legs(conn: sqlite3.Connection, as_of_date: str) -> List[dict]:
    """One row per USD-conversion pair an open cross's legs need for their delta/P&L USD
    conversion (row shape and instrument lookup: `_usd_pair_rows`)."""
    ccys = set()
    for base, quote in conn.execute(_OPEN_CROSS_LEG_CCYS_SQL, {"as_of": as_of_date}):
        ccys.add(base)
        ccys.add(quote)
    return _usd_pair_rows(conn, ccys)


# `i.expiry_date >= :as_of`: the expiry day itself counts as open. It has to: on expiry day
# engine/options writes the option's PAYOFF at that day's official SPOT of the pair, and
# the ledger converts it to USD at that same day's base->USD SPOT.
_OPEN_OPTION_PAIRS_SQL = """
SELECT DISTINCT i.base_ccy || i.quote_ccy AS pair, i.expiry_date, i.base_ccy, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND i.expiry_date >= :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
ORDER BY pair, i.expiry_date
"""

# The same question asked of a PAST close (`historical=True` below): every option that was
# open that day, whether or not the ledger has frozen it since. The realised filter must
# NOT apply here: engine/options' expiry-day catch-up deliberately re-prices an option
# that was already frozen from an older premium (it deletes that realised_pnl row and the
# ledger freezes it afresh at the payoff), and it can only do so once the expiry date's
# closing SPOT is on file -- so that SPOT has to stay "needed" for an option the ledger
# has already realised, or an option-only pair would never get it.
_OPTION_PAIRS_OPEN_ON_DAY_SQL = """
SELECT DISTINCT i.base_ccy || i.quote_ccy AS pair, i.expiry_date, i.base_ccy, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :as_of AND i.expiry_date >= :as_of
ORDER BY pair, i.expiry_date
"""


def _option_mark_rows(conn: sqlite3.Connection, as_of_date: str, historical: bool = False) -> List[dict]:
    """One row per (pair, expiry) an open FX_OPTION needs marks for:
    {pair, expiry, base, quote, instrument_id, bbg_ticker, has_instrument}.

    engine/options prices an option off the PAIR's own official SPOT
    (inputs.get_spot keys marks_official by the plain 6-char pair, never the option's
    own instrument_id) and, for a currency with no OIS curve, off the pair's official
    FWD_OUTRIGHT points around the expiry (rates._forward_for_expiry, covered interest
    parity). Until 2026-09-18 nothing ever requested either of those for a pair with no
    open FX forward leg, so an option-only pair was skipped "no SPOT mark" forever (both
    USDJPY options on the fake-pull audit). Read-only; shared with
    data.bloomberg.inventory._needed_marks so "needed" and "requested" cannot drift.
    `historical`: see _OPTION_PAIRS_OPEN_ON_DAY_SQL."""
    sql = _OPTION_PAIRS_OPEN_ON_DAY_SQL if historical else _OPEN_OPTION_PAIRS_SQL
    out = []
    for pair, expiry, base, quote in conn.execute(sql, {"as_of": as_of_date}):
        row = conn.execute("SELECT instrument_id, bbg_ticker FROM instruments WHERE instrument_id = ?",
                           (pair,)).fetchone()
        out.append({"pair": pair, "expiry": expiry, "base": base, "quote": quote,
                    "instrument_id": row[0] if row else pair,
                    "bbg_ticker": row[1] if row else f"{pair} Curncy",
                    "has_instrument": row is not None})
    return out


def option_spot_pair_names(base_ccy: str, quote_ccy: str) -> List[str]:
    """The plain pair names whose SPOT one FX_OPTION on `base_ccy`/`quote_ccy` needs, own
    pair first, de-duplicated: the pair itself (pricing, and the expiry-day payoff), then
    base->USD (CLAUDE.md: an option's value and P&L are in the BASE currency and convert
    to USD at spot -- the EURSEK digital pays EUR), then quote->USD (vega / theta / rho
    convert through the quote currency, engine/options/portfolio.py). A USD leg needs no
    conversion, so USDJPY yields just ['USDJPY'] -- never a 'USDUSD'. Orientation comes
    from `_usd_pair_name`, the same helper the forwards' cross-leg path uses. Used by
    data.bloomberg.backfill.traded_pairs for the historical closing-SPOT set."""
    names = [f"{base_ccy}{quote_ccy}"]
    for ccy in (base_ccy, quote_ccy):
        if ccy and ccy != "USD":
            name = _usd_pair_name(ccy)
            if name not in names:
                names.append(name)
    return names


def _option_usd_legs(conn: sqlite3.Connection, as_of_date: str, historical: bool = False) -> List[dict]:
    """One row per USD-conversion pair the open FX_OPTIONs need on their OWN account (row
    shape and instrument lookup: `_usd_pair_rows`): base->USD when the base is not USD,
    quote->USD when the quote is not USD.

    Found 2026-09-18 (options-pricer): only forwards drove USD-conversion SPOT requests
    (`_OPEN_CROSS_LEG_CCYS_SQL` filters asset_class 'FX'), so an option's USD value rode
    on whatever forwards happened to be open in its currencies. The EURSEK digital runs
    to 2026-11-25; the day the last EUR forward settled, its USD value would have gone
    "unpriced: no SPOT for USD conversion of EUR" with nothing asking for EURUSD.
    Read-only; shared with option_needed_marks so inventory and build_requests agree."""
    ccys = set()
    for o in _option_mark_rows(conn, as_of_date, historical):
        ccys.add(o["base"])
        ccys.add(o["quote"])
    return _usd_pair_rows(conn, ccys)


def option_needed_marks(conn: sqlite3.Connection, as_of_date: str, historical: bool = False) -> List[dict]:
    """The marks open FX_OPTIONs need on `as_of_date`, in the shape
    data.bloomberg.inventory._needed_marks lists everything else in:
    [{instrument_id, settle_date, mark_type}] -- one SPOT (settle_date = as_of) per option
    pair, one FWD_OUTRIGHT at each open option's expiry, and one SPOT per USD-conversion
    pair (`_option_usd_legs`). Read-only wrapper over the same two functions
    build_requests uses, so the inventory / diagnostics "needed" set and the live request
    list can never disagree about options.

    `historical=True` is the same question for a PAST close (close_completeness and the
    backfill): SPOT only, for every option open that day including its expiry date,
    realised since or not (_OPTION_PAIRS_OPEN_ON_DAY_SQL). No FWD_OUTRIGHT: nothing
    prices an option on a past date (there is no historical vol), so a past forward at
    the expiry would feed nothing, and for an option-only pair the backfill has no curve
    history to build one from -- listing it would leave such a day incomplete forever."""
    out, seen = [], set()

    def _add(instrument_id: str, settle: str, mark_type: str) -> None:
        key = (instrument_id, settle, mark_type)
        if key not in seen:
            seen.add(key)
            out.append({"instrument_id": instrument_id, "settle_date": settle, "mark_type": mark_type})

    for o in _option_mark_rows(conn, as_of_date, historical):
        _add(o["instrument_id"], as_of_date, "SPOT")
        if not historical:
            _add(o["instrument_id"], o["expiry"], "FWD_OUTRIGHT")
    for leg in _option_usd_legs(conn, as_of_date, historical):
        _add(leg["instrument_id"], as_of_date, "SPOT")
    return out


def _ensure_fx_instruments(conn: sqlite3.Connection, pairs) -> List[str]:
    """Insert the plain FX `instruments` row for each 6-char pair that has none yet --
    the same conventional reference-data row the blotter parser writes for a traded pair
    (multiplier 1, is_ndf 0, '<pair> Curncy', perpetual). `write_marks` can only persist a
    SPOT / FWD_OUTRIGHT for a known instrument, and an option-only pair or a USD-conversion
    pair may never have been traded outright, so without this row the mark Bloomberg
    returned vanished silently (2026-09-18). Returns the pairs created. A read-only
    connection is left alone. is_ndf is always 0 since NDFs left the app (2026-09-24)."""
    created: List[str] = []
    for pair in sorted({p for p in pairs if isinstance(p, str) and len(p) == 6}):
        if conn.execute("SELECT 1 FROM instruments WHERE instrument_id = ?", (pair,)).fetchone():
            continue
        base, quote = pair[:3], pair[3:]
        try:
            with conn:
                # Column-explicit: a live database can carry extra instrument columns from
                # an earlier schema (the dev DB has 12, all with defaults); a positional
                # insert fails there with "table instruments has 12 columns but 8 values".
                conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, "
                             "multiplier, is_ndf, bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                             (pair, "FX", base, quote, 1.0, 0, f"{pair} Curncy", "9999-12-31"))
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc).lower() or "read-only" in str(exc).lower():
                break  # read-only connection (diagnostics): nothing to create here
            raise
        created.append(pair)
    return created


def needed_live(conn: sqlite3.Connection, as_of_date: str) -> List[dict]:
    """The library rows a live pull on `as_of_date` reads, unrequestable ones included (each
    row carries `requestable`), in one read: `library.needed_on(..., include_unrequestable=
    True)`. `pull_once` reads it once per cycle, after the contract dates moved the futures,
    and hands it to build_requests, not_requestable_futures and the LME, curves and vol
    steps, which each read the library on their own before (2026-09-30). The requestable
    rows are exactly `needed_on`'s default list, in the same order."""
    from data.bloomberg import library
    try:
        return list(library.needed_on(conn, as_of_date, include_unrequestable=True))
    except TypeError:                       # an older library: every row it gives is asked for
        return list(library.needed_on(conn, as_of_date))


def _requestable(needed: List[dict]) -> List[dict]:
    """`needed_live`'s rows a pull may ask for: `library.needed_on`'s default list."""
    return [r for r in needed if r.get("requestable", True)]


def _live_keys(needed: List[dict], kind: str) -> List[str]:
    """`library.keys` off the cycle's one read: the sorted distinct keys of one kind."""
    return sorted({r["key"] for r in _requestable(needed) if r["kind"] == kind})


def build_requests(conn: sqlite3.Connection, as_of_date: str, needed: Optional[List[dict]] = None) -> list:
    """RequestRows per BUILD_PLAN.md section 2: one SPOT per open FX pair, one
    FWD_OUTRIGHT per (pair, open leg's own settle_date) -- no shared workbook maturity --
    one FUTURE_PX per open future at its own settle_date (expiry), (2026-09-17) one
    extra SPOT per USD-conversion pair an open cross's legs need (see _cross_usd_legs),
    and (2026-09-18) one SPOT per open FX_OPTION's pair plus one FWD_OUTRIGHT at each
    open option's own expiry (see _option_mark_rows), plus one SPOT per USD-conversion
    pair the option itself needs -- base->USD and quote->USD, see _option_usd_legs --
    appended after the option's own rows and only where nothing above already asked for
    it. The pair instrument row those need is created here when missing
    (_ensure_fx_instruments), since this is the one writable call site.

    2026-09-21 (user: "only pull data that is essential to calculate the pnl of the trades
    ... they will be in this cache"): the list is READ from the Bloomberg library
    (data/bloomberg/library.py, `bbg_library`), which is written when trades come in, not
    worked out from the trades here. Same rules, same order; what is not in the library is
    not asked for. Only the library's mark kinds (SPOT, FWD_OUTRIGHT, FUTURE_PX) become
    requests; the NDF_1M / NDF_FIX requests left on 2026-09-24.

    2026-09-24 (Phase 5): an LME forward's rows (`library.is_lme_row`: the metal's cash SPOT,
    the FWD_OUTRIGHT at its prompt, its LME_CURVE) are left out: the LME step (`_lme_step`)
    asks the metal's curve pillars, cash included, and writes the cash as SPOT and the
    prompts off that curve, so nothing here asks the cash a second time or reads 'LME:CA'
    as a currency pair. An option on a commodity future's rows are the futures' own shape
    (FUTURE_PX on the option, and on its underlying future, role UNDERLYING) and are asked
    for with the futures.

    `needed` (2026-09-30): the cycle's one library read (`needed_live`); read here when not
    given."""
    from data.bloomberg import library
    from data.bloomberg.pull_marks import RequestRow
    is_lme = getattr(library, "is_lme_row", lambda r: False)
    rows_in_force = library.needed_on(conn, as_of_date) if needed is None else _requestable(needed)
    needed = [r for r in rows_in_force if not is_lme(r)]
    _ensure_fx_instruments(conn, [r["key"] for r in needed if r["kind"] in ("SPOT", "FWD_OUTRIGHT")])
    out, seen = [], set()

    def _add(r: dict) -> None:
        if not str(r.get("bbg_ticker") or "").strip():
            # Not requestable (2026-09-24): a contract with no Bloomberg ticker is never asked
            # for; pull_once lists it with its reason (not_requestable_futures).
            return
        key = (r["key"], "SPOT") if r["kind"] == "SPOT" else (r["key"], r["kind"], r["settle_date"])
        if key not in seen:
            seen.add(key)
            out.append(RequestRow(r["key"], r["bbg_ticker"],
                                  as_of_date if r["kind"] == "SPOT" else r["settle_date"], r["kind"]))

    is_option = lambda r: r["product"] == "FX_OPTION"                     # noqa: E731
    is_conversion = lambda r: r["role"] == library.ROLE_CONVERSION        # noqa: E731
    marks = sorted((r for r in needed if r["kind"] in library.MARK_KINDS),
                   key=lambda r: (r["key"], r["kind"] != "SPOT", r["settle_date"]))
    fx = [r for r in marks if r["kind"] != "FUTURE_PX" and not is_option(r)]
    options = [r for r in marks if is_option(r)]
    # Forwards' own pairs (SPOT, then each leg date), crosses' and futures' conversion
    # pairs, options' own pairs, options' conversion pairs, futures: the order the list
    # always had.
    for group in ([r for r in fx if not is_conversion(r)], [r for r in fx if is_conversion(r)],
                  [r for r in options if not is_conversion(r)], [r for r in options if is_conversion(r)],
                  [r for r in marks if r["kind"] == "FUTURE_PX"]):
        for r in group:
            _add(r)
    return out


# --------------------------------------------------------------------------- contract dates
# Bloomberg's own dates of a commodity future (2026-09-24, commodity conversion Phase 1): the
# instrument carries an estimated last trade date (the last weekday of the contract month,
# data/contracts) until these are on file, and its FUTURE_PX marks are keyed on that expiry.
# Asked once per press, before any futures price, for the library's CONTRACT_DATES rows only.
CONTRACT_DATES = "CONTRACT_DATES"          # the library kind (data/bloomberg/library.py)
CONTRACT_DATE_FIELDS = ("FUT_LAST_TRADE_DT", "FUT_NOTICE_FIRST")
# An option on a commodity future's expiry (2026-09-24, Phase 5; bbg-library's
# OPTION_CONTRACT_DATES_FIELDS, unverified on a terminal): OPT_EXPIRE_DT, else
# LAST_TRADEABLE_DT, stored as its last trade date, with no first notice.
OPTION_CONTRACT_DATE_FIELDS = ("OPT_EXPIRE_DT", "LAST_TRADEABLE_DT")
SRC_CONTRACT_DATES = "BBG_BDP"             # contract_static.source of a date Bloomberg gave


def _contract_dates_kind() -> str:
    try:
        from data.bloomberg import library
        return getattr(library, "CONTRACT_DATES", CONTRACT_DATES)
    except Exception:  # noqa: BLE001
        return CONTRACT_DATES


def _bloomberg_said_for(diag, ticker: str, purpose: str) -> str:
    """Bloomberg's own words for `ticker` in the latest run of requests recorded in `diag`
    with this `purpose` (its security error, else its field exceptions); '' when it said
    nothing. The run is the requests at the end of the log that carry the purpose (the
    contract dates are asked in one request per set of fields since 2026-09-24), the most
    recent that answered for `ticker` first."""
    for rec in reversed(getattr(diag, "requests", None) or []):
        if rec.get("purpose") != purpose:
            return ""
        for sec in rec.get("raw_response") or []:
            if sec.get("security") != ticker:
                continue
            if sec.get("securityError"):
                return str(sec["securityError"].get("message") or "security error")
            return "; ".join(f"{fx.get('fieldId')}: {fx.get('message')}" for fx in sec.get("fieldExceptions") or [])
    return ""


def contract_dates_summary(block: dict) -> str:
    """The one sentence the feed status shows for `status["contract_dates"]`: 'N contract
    dates stored, M futures moved to Bloomberg's expiry', with the tickers that gave no date
    counted after it. '' when nothing was asked and nothing moved. Options on futures moved
    are counted apart from the futures (2026-09-24, Phase 5: '..., 2 futures and 1 option on
    futures moved to Bloomberg's expiry'), an option told by its canonical option id."""
    requested = int(block.get("requested") or 0)
    stored = int(block.get("stored") or 0)
    applied = block.get("applied") if isinstance(block.get("applied"), dict) else {}
    updated = applied.get("updated") or []
    try:
        from data.contracts.tickers import parse_option_ticker
    except Exception:  # noqa: BLE001
        parse_option_ticker = lambda _t: None                              # noqa: E731
    options = sum(1 for u in updated if isinstance(u, dict)
                  and parse_option_ticker(str(u.get("instrument_id") or "")) is not None)
    moved = len(updated) - options
    failed = len(block.get("failed") or [])
    if not (requested or stored or moved or options or failed):
        return ""
    what = f"{moved} future{'' if moved == 1 else 's'}"
    if options:
        what = (what + " and " if moved else "") + f"{options} option{'' if options == 1 else 's'} on futures"
    text = (f"{stored} contract date{'' if stored == 1 else 's'} stored, "
            f"{what} moved to Bloomberg's expiry")
    if failed:
        text += f"; {failed} ticker{'' if failed == 1 else 's'} gave no date"
    if applied.get("error"):
        text += f"; moving the futures stopped ({applied['error']})"
    return text


def _is_option_entry(entry: dict) -> bool:
    """Is this contract-dates entry an option on a commodity future (2026-09-24, Phase 5)?
    Told by the library's `product` (CMDTY_OPTION, or any listed option product), else by
    its fields being the options' own."""
    try:
        from engine.pnl.valuation import LISTED_OPTION_PRODUCTS
    except Exception:  # noqa: BLE001
        LISTED_OPTION_PRODUCTS = ("EQ_OPTION", "CMDTY_OPTION")
    return entry.get("product") in LISTED_OPTION_PRODUCTS or tuple(entry.get("fields") or ()) == OPTION_CONTRACT_DATE_FIELDS


def _contract_dates_entries(conn: sqlite3.Connection, as_of: str) -> Tuple[Dict[str, dict], bool]:
    """({contract id: {ticker, fields, option}} the library lists for today's pull, whether
    any open contract needs contract dates at all, met or not). The list is the library's own
    (`library.contract_dates_needed`: open, of a contract root, a verified ticker, no dates
    on file yet), each with the fields it is asked under: a future's CONTRACT_DATE_FIELDS,
    an option on a future's OPTION_CONTRACT_DATE_FIELDS (2026-09-24, Phase 5); the flag
    decides whether apply_contract_dates runs, so a book with no commodity future pays
    nothing and one whose dates are all on file is still applied."""
    from data.bloomberg import library
    kind = _contract_dates_kind()

    def _entry(ticker, fields, product) -> dict:
        option = _is_option_entry({"product": product, "fields": fields})
        fields = tuple(fields or ()) or (OPTION_CONTRACT_DATE_FIELDS if option else CONTRACT_DATE_FIELDS)
        return {"ticker": str(ticker or "").strip(), "fields": fields, "option": option}

    if hasattr(library, "contract_dates_needed"):
        entries = {e["contract_id"]: _entry(e.get("bbg_ticker"), e.get("fields"), e.get("product"))
                   for e in library.contract_dates_needed(conn, as_of)}
    else:                                   # an older library: its CONTRACT_DATES rows, if any
        entries = {}
        for r in library.needed_on(conn, as_of):
            if r.get("kind") == kind:
                entries.setdefault(r["key"], _entry(r.get("bbg_ticker"), None, r.get("product")))
    in_force = entries or any(r.get("kind") == kind and r["needed_from"] <= as_of <= r["needed_until"]
                              for r in library.rows(conn))
    return entries, bool(in_force)


def contract_dates_step(conn: sqlite3.Connection, today: date, get_session: Callable, diag=None) -> dict:
    """Bloomberg's contract dates for every future the library lists for today's pull
    (`library.contract_dates_needed`: CONTRACT_DATES, open, a verified ticker, no dates on
    file yet): one ReferenceDataRequest of FUT_LAST_TRADE_DT and FUT_NOTICE_FIRST for all
    their tickers, each answer stored in contract_static under BBG_BDP
    (data.contracts.store_static_dates), then data.ingest.contract_dates.apply_contract_dates
    (conn), which moves each future's instrument, legs and FUTURE_PX marks onto Bloomberg's
    last trade date. `pull_once` runs it before the request list is built, so the futures'
    prices are asked for, and land, under Bloomberg's expiry. `get_session()` returns
    (session, service) and is only called when there is something to ask. Never raises.

    The apply runs whenever an open future needs contract dates at all, met or not, so a
    future whose dates were stored by an earlier press but not applied is still moved; a
    book with no commodity future neither asks nor applies.

    Returns the status file's `contract_dates` block: {requested (tickers asked), stored,
    failed: [{ticker, reason}], applied (apply_contract_dates' own dict: checked, updated,
    missing_dates; {} when it did not run, {"error"} when it failed), summary (the one
    sentence, `contract_dates_summary`)}, plus "error" when the library could not be read.
    A ticker Bloomberg rejects or answers without a last trade date is listed under `failed`
    with Bloomberg's own reason; the pull carries on.

    Options on commodity futures (2026-09-24, Phase 5): the library lists them too, with the
    options' own fields (OPTION_CONTRACT_DATE_FIELDS), asked in a request of their own; an
    option's OPT_EXPIRE_DT, else its LAST_TRADEABLE_DT, is stored as its last trade date,
    first notice '' (data.contracts.store_static_dates takes option ids), and the apply
    moves the option onto it like a future."""
    block: dict = {"requested": 0, "stored": 0, "failed": [], "applied": {}}

    def _done() -> dict:
        block["summary"] = contract_dates_summary(block)
        return block

    try:
        entries, in_force = _contract_dates_entries(conn, today.isoformat())
    except Exception as exc:  # noqa: BLE001
        block["error"] = f"contract dates not read from the library: {exc!r}"
        return _done()
    if not in_force:
        return _done()
    for key, entry in sorted(entries.items()):
        if not entry["ticker"]:     # the library leaves these out; never sent to Bloomberg regardless
            block["failed"].append({"ticker": key, "reason": f"no Bloomberg ticker for {key}"})
    ask = {k: e for k, e in entries.items() if e["ticker"]}
    if ask:
        from data.bloomberg.pull_marks import fetch_reference, _to_date
        _release_lock(conn)         # the library's sync is committed before Bloomberg is asked
        block["requested"] = len({e["ticker"] for e in ask.values()})
        # One ReferenceDataRequest per set of fields: the futures' (FUT_LAST_TRADE_DT,
        # FUT_NOTICE_FIRST), and the options' on futures (OPT_EXPIRE_DT, LAST_TRADEABLE_DT).
        by_fields: Dict[tuple, List[str]] = {}
        for entry in ask.values():
            by_fields.setdefault(entry["fields"], [])
            if entry["ticker"] not in by_fields[entry["fields"]]:
                by_fields[entry["fields"]].append(entry["ticker"])
        data: Dict[str, dict] = {}
        asked_ok = set()
        def _ask(tickers: List[str], fields: tuple, may_split: bool) -> None:
            try:
                session, service = get_session()
                got = fetch_reference(session, service, tickers, list(fields), diag=diag,
                                      tag={"purpose": CONTRACT_DATES}) or {}
                for t in tickers:
                    data[t] = got.get(t) or {}
                asked_ok.update(tickers)
            except Exception as exc:  # noqa: BLE001 -- these tickers fail with the reason, the pull goes on
                # One ticker that breaks the request must not cost the others their dates
                # (2026-09-29): asked again one by one, unless Bloomberg timed out or there is
                # no session at all.
                if may_split and len(tickers) > 1 and not _is_timeout(exc) and not isinstance(exc, SessionUnavailable):
                    for t in tickers:
                        _ask([t], fields, False)
                    return
                block["failed"].extend({"ticker": t, "reason": f"request failed: {exc}"} for t in tickers)

        for fields, tickers in sorted(by_fields.items()):
            _ask(sorted(tickers), fields, True)
        ask = {k: e for k, e in ask.items() if e["ticker"] in asked_ok}
        store_static_dates = None
        if ask:
            try:
                from data.contracts import store_static_dates
            except Exception as exc:  # noqa: BLE001
                block["error"] = f"data.contracts.store_static_dates not importable: {exc!r}"
        for key, entry in sorted(ask.items()):
            ticker = entry["ticker"]
            got = data.get(ticker) or {}
            if entry["option"]:
                # An option's expiry is its last trade date; it has no first notice.
                last_trade = next((d for d in (_to_date(got.get(f)) for f in entry["fields"]) if d is not None), None)
                notice = None
                wanted = " or ".join(entry["fields"])
            else:
                last_trade = _to_date(got.get("FUT_LAST_TRADE_DT"))
                notice = _to_date(got.get("FUT_NOTICE_FIRST"))
                wanted = "FUT_LAST_TRADE_DT"
            if last_trade is None:
                said = _bloomberg_said_for(diag, ticker, CONTRACT_DATES)
                block["failed"].append({"ticker": ticker, "reason": said or f"Bloomberg returned no {wanted}"})
                continue
            if store_static_dates is None:
                block["failed"].append({"ticker": ticker, "reason": "not stored: " + block.get("error", "")})
                continue
            try:        # one contract per call: a row contract-master refuses fails alone
                store_static_dates(conn, [{"contract_id": key, "last_trade_date": last_trade.isoformat(),
                                           "first_notice_date": notice.isoformat() if notice else "",
                                           "source": SRC_CONTRACT_DATES}])
                block["stored"] += 1
            except Exception as exc:  # noqa: BLE001
                block["failed"].append({"ticker": ticker, "reason": f"not stored: {exc}"})
    try:
        from data.ingest.contract_dates import apply_contract_dates
    except ImportError as exc:
        block["applied"] = {"error": f"data.ingest.contract_dates.apply_contract_dates not importable: {exc!r}"}
        return _done()
    try:
        applied = apply_contract_dates(conn)
        block["applied"] = dict(applied) if isinstance(applied, dict) else {"result": applied}
    except Exception as exc:  # noqa: BLE001
        block["applied"] = {"error": f"{exc!r}"}
    return _done()


def not_requestable_futures(conn: sqlite3.Connection, as_of_date: str,
                            needed: Optional[List[dict]] = None) -> List[dict]:
    """The futures the library lists on `as_of_date` that it marks not requestable (no
    verified Bloomberg ticker): never asked for, and listed here instead, one entry per
    contract, {instrument_id, settle_date, trade_ids, reason}, the reason the library's own.
    Never raises: a library that cannot say gives []. `needed`: the cycle's one library
    read (`needed_live`, 2026-09-30); read here when not given."""
    from data.bloomberg import library
    if needed is None:
        try:
            needed = library.needed_on(conn, as_of_date, include_unrequestable=True)
        except TypeError:                   # an older library: every row it gives is asked for
            return []
        except Exception:  # noqa: BLE001
            return []
    out: Dict[str, dict] = {}
    for r in needed:
        if r.get("kind") != "FUTURE_PX" or r.get("requestable", bool(r.get("bbg_ticker"))):
            continue
        entry = out.setdefault(r["key"], {
            "instrument_id": r["key"], "settle_date": r.get("settle_date", ""), "trade_ids": [],
            "reason": r.get("reason") or f"no Bloomberg ticker for {r['key']}"})
        if r.get("trade_id") and r["trade_id"] not in entry["trade_ids"]:
            entry["trade_ids"].append(r["trade_id"])
    return [out[k] for k in sorted(out)]


# --------------------------------------------------------------------------- fault isolation
# 2026-09-29 (Phase G, "Smooth and contained"; user: "if something pulls badly it doesnt crash
# everything - but of course most important is to make sure that everything pulls
# correctly"). A cycle is a row of steps, each run on its own (`pull_once`): a step that
# raises is recorded with its reason under status["steps"] and the next step runs, the ledger
# included. Inside the three marks steps (spot, forwards, futures) the tickers are asked in
# chunks, so a request Bloomberg cannot answer fails its own chunk only; a chunk that raised
# for any reason but a timeout is asked again one ticker at a time, so one ticker that breaks
# a request leaves the others standing. What is asked is unchanged: the same tickers and
# fields the library lists, in more requests (hard rule 8).
# 2026-09-30 (user at the Bloomberg PC: "the bloomberg pull is quite slow and looks wasteful"):
# spot and futures 10 -> 50 tickers a request, the backfill's HISTORY_CHUNK. A
# ReferenceDataRequest takes hundreds of securities, and a bad ticker comes back as that
# security's own error, never as a raise, so a chunk only splits when the whole request broke.
# Jason's 20 futures were two requests (and up to two PX_SETTLE fallbacks), now one. The
# forwards stay at 5: each pair's FWD_CURVE answer is a bulk table.
REQUEST_CHUNK = {"spot": 50, "forwards": 5, "futures": 50}
# After this many timeouts in a row the rest of the marks chunks are not sent: each timeout
# costs pull_marks.EVENT_TIMEOUT_MS, and a Bloomberg that stopped answering would otherwise
# hold the press for minutes. Every row not asked is failed with that reason.
TIMEOUTS_BEFORE_GIVING_UP = 2

# The steps of a connected cycle, in order, with the plain words the progress line and the
# per-step outcome use (status["progress"]["step_label"], status["steps"][i]["label"]).
STEP_LABELS = {
    "contract_dates": "contract dates",
    "requests": "the list of what to ask",
    "spot": "spot prices",
    "forwards": "forward curves",
    "futures": "futures and option prices",
    "write_marks": "saving the marks",
    "lme": "LME curves",
    "curves": "OIS curves",
    "vol": "vol smiles",
    "options": "option pricing",
    "ledger": "the ledger",
    "recalc": "re-pricing the options from the marks on file",
}
PULL_STEPS = ("contract_dates", "requests", "spot", "forwards", "futures", "write_marks", "lme", "curves",
              "vol", "options", "ledger")
# The outcome of a step in status["steps"]: it ran and nothing failed, some of it failed,
# it failed (raised, or nothing it asked came back), or it had nothing to do / did not run.
OUTCOMES = ("ok", "partial", "failed", "skipped")


class SessionUnavailable(RuntimeError):
    """The cycle's blpapi session could not be opened (no Terminal logged in): the press has
    no Bloomberg, and every step that needs it stops asking."""


def _is_timeout(exc: BaseException) -> bool:
    return getattr(exc, "classification", None) == "TIMEOUT"


def _plain_error(exc: BaseException) -> str:
    """A request failure in a few words: a timeout as such, anything else as its type and
    message (cut at 300 characters)."""
    if _is_timeout(exc):
        try:
            from data.bloomberg.pull_marks import EVENT_TIMEOUT_MS
            seconds = f" within {EVENT_TIMEOUT_MS / 1000:.0f} s"
        except Exception:  # noqa: BLE001
            seconds = ""
        return f"Bloomberg did not answer{seconds} ({getattr(exc, 'request_type', 'request')})"
    if isinstance(exc, SessionUnavailable):
        return f"no Bloomberg session ({exc})"
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= 300 else text[:297] + "..."


def _release_lock(conn: sqlite3.Connection) -> None:
    """Commit whatever the cycle has written so far, so no write lock is held across a
    Bloomberg request (a request can wait pull_marks.EVENT_TIMEOUT_MS; an upload waiting on
    the lock gives up after schema.BUSY_TIMEOUT_SECONDS). Never raises."""
    try:
        if conn.in_transaction:
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


class _NetLog:
    """What the cycle's Bloomberg requests did: how many were answered, those that raised
    ({step, reason, traceback}), and the timeouts in a row (`gave_up`)."""

    def __init__(self) -> None:
        self.answered = 0
        self.raised: List[dict] = []
        self.timeouts_in_row = 0

    def ok(self) -> None:
        self.answered += 1
        self.timeouts_in_row = 0

    def failed(self, step: str, exc: BaseException) -> None:
        """Called inside the `except` that caught `exc`."""
        self.raised.append({"step": step, "reason": _plain_error(exc), "traceback": traceback.format_exc()})
        self.timeouts_in_row = self.timeouts_in_row + 1 if _is_timeout(exc) else 0

    @property
    def gave_up(self) -> bool:
        return self.timeouts_in_row >= TIMEOUTS_BEFORE_GIVING_UP


def _ask_in_chunks(step: str, requests, size: int, ask: Callable, net: _NetLog, progress=None) -> dict:
    """{"rows", "warnings", "failures", "curve_rows"}: `ask(chunk)` for the requests of
    `size` tickers at a time (every request of a ticker in the same chunk), the lists it
    returns joined. A chunk that raises fails its own rows with the reason, never the others;
    one that raised for anything but a timeout is asked again ticker by ticker first. After
    TIMEOUTS_BEFORE_GIVING_UP timeouts in a row the chunks left are failed unasked.
    `SessionUnavailable` is raised on: the press has no Bloomberg. `progress.add_done` is
    told the rows of each chunk once it is settled."""
    out: dict = {"rows": [], "warnings": [], "failures": [], "curve_rows": []}
    by_ticker: Dict[str, list] = {}
    for r in requests:
        by_ticker.setdefault(r.bbg_ticker, []).append(r)
    tickers = sorted(by_ticker)

    def _fail(rows, reason: str) -> None:
        out["failures"].extend({"instrument_id": r.instrument_id, "mark_type": r.mark_type,
                                "settle_date": r.settle_date, "detail": f"{r.mark_type} {reason}"} for r in rows)

    def _one(chunk: List[str], may_split: bool) -> None:
        rows = [r for t in chunk for r in by_ticker[t]]
        if net.gave_up:
            _fail(rows, f"not asked: Bloomberg did not answer the last {TIMEOUTS_BEFORE_GIVING_UP} requests "
                        "of this pull")
            return
        try:
            got = ask(rows) or {}
        except SessionUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 -- this chunk fails alone
            net.failed(step, exc)
            if may_split and len(chunk) > 1 and not _is_timeout(exc):
                for t in chunk:
                    _one([t], False)
                return
            _fail(rows, f"request failed: {_plain_error(exc)}")
            return
        net.ok()
        for key, values in got.items():
            out.setdefault(key, []).extend(values or [])

    for i in range(0, len(tickers), max(1, int(size))):
        chunk = tickers[i:i + max(1, int(size))]
        _one(chunk, True)
        if progress is not None:
            progress.add_done(sum(len(by_ticker[t]) for t in chunk))
    return out


# --------------------------------------------------------------------------- progress
PROGRESS_KEY = "progress"


def set_progress(db_path, block: dict) -> None:
    """Replace status["progress"] in the status file, under the lock `write_status` takes,
    leaving every other key as it is: the last pull's report stays what the screens show
    (its fingerprint, ui/feed_controls.status_fingerprint, does not move), with the running
    pull's progress beside it. Nothing is written when there is no status file yet (the app
    writes a placeholder at start). Never raises."""
    try:
        with _STATUS_LOCK:
            current = read_status(db_path)
            if current is None:
                return
            current[PROGRESS_KEY] = block
            _replace_status_file(status_path(db_path), current)
    except Exception:  # noqa: BLE001 -- progress is a courtesy, never a failure of the pull
        pass


def progress_sentence(block: dict) -> str:
    """The progress line: 'Pulling 31 of 47 marks · futures and option prices' while a pull
    runs (the step alone before the marks are counted), 'Pull finished: 45 of 47 marks
    written' / 'Pull finished: no Bloomberg (reason)' after."""
    label = block.get("step_label") or ""
    done, total = int(block.get("done") or 0), int(block.get("total") or 0)
    if block.get("running"):
        head = f"Pulling {done} of {total} marks" if total else "Pulling"
        return f"{head} · {label}" if label else head
    return str(block.get("final_sentence") or "Pull finished")


class _Progress:
    """The running pull's status["progress"] block, published at every step and chunk:
    {running, started_at, updated_at, step, step_label, step_index, step_count, done, total,
    sentence}; at the end also finished_at and outcome. Never raises."""

    def __init__(self, db_path, started_at: str, steps=PULL_STEPS):
        self.db_path = db_path
        self.steps = list(steps)
        self.block = {"running": True, "started_at": started_at, "updated_at": started_at, "step": "",
                      "step_label": "", "step_index": 0, "step_count": len(self.steps), "done": 0, "total": 0}
        self.publish()

    def publish(self) -> None:
        self.block["updated_at"] = _now_iso()
        self.block["sentence"] = progress_sentence(self.block)
        set_progress(self.db_path, dict(self.block))

    def step(self, name: str, publish: bool = True) -> None:
        """The step now running. `publish=False` (2026-09-30) for a step with nothing to
        ask, which ends at once: the block moves on, the file is not rewritten for it."""
        if name not in self.steps:
            self.steps.append(name)
            self.block["step_count"] = len(self.steps)
        self.block.update(step=name, step_label=STEP_LABELS.get(name, name), step_index=self.steps.index(name) + 1)
        if publish:
            self.publish()

    def set_total(self, n: int) -> None:
        if int(n) == int(self.block["total"]):
            return                      # nothing new to say: no rewrite (2026-09-30)
        self.block["total"] = int(n)
        self.publish()

    def add_done(self, n: int) -> None:
        done = min(int(self.block["done"]) + int(n), int(self.block["total"]) or 10 ** 9)
        if done == int(self.block["done"]):
            return                      # nothing new to say: no rewrite (2026-09-30)
        self.block["done"] = done
        self.publish()

    def final(self, outcome: str, sentence: str) -> dict:
        """The block the final status carries: not running, with the pull's outcome."""
        now = _now_iso()
        self.block.update(running=False, updated_at=now, finished_at=now, outcome=outcome, step="done",
                          step_label="", final_sentence=sentence)
        self.block["sentence"] = progress_sentence(self.block)
        return dict(self.block)


def _step_outcome(name: str, block, failed_items: int = 0, asked: int = 0) -> Tuple[str, str]:
    """(outcome, detail) of one step from what it returned: `block` is the step's status
    block (a dict), or None for the marks steps, which are judged by `asked` / `failed_items`."""
    if block is None:
        if not asked:
            return "skipped", "nothing to ask"
        if failed_items >= asked:
            return "failed", f"none of {asked} came back"
        if failed_items:
            return "partial", f"{asked - failed_items} of {asked} came back, {failed_items} failed"
        return "ok", f"{asked} of {asked} came back"
    if not isinstance(block, dict):
        return "ok", ""
    if block.get("error"):
        return "failed", str(block["error"])
    skipped = block.get("skipped")
    if isinstance(skipped, str) and skipped:
        return "skipped", skipped
    if name == "contract_dates":
        failed = len(block.get("failed") or [])
        if not (block.get("requested") or failed or (block.get("applied") or {}).get("updated")):
            return "skipped", "no contract dates to ask"
        if (block.get("applied") or {}).get("error"):
            return "partial", block.get("summary") or str(block["applied"]["error"])
        if failed and not block.get("stored") and failed >= int(block.get("requested") or 0):
            return "failed", block.get("summary") or ""
        return ("partial" if failed else "ok"), block.get("summary") or ""
    if name == "lme":
        if not block.get("roots"):
            return "skipped", "no LME forward open"
        return ("partial" if block.get("missing") else "ok"), block.get("summary") or ""
    if name == "curves":
        currencies = block.get("currencies") or {}
        bad = sorted(c for c, e in currencies.items() if e.get("error"))
        short = sorted(c for c, e in currencies.items() if e.get("left_out"))
        n = len(currencies)
        detail = f"{n - len(bad)} of {n} currencies"
        if bad:
            detail += f"; no curve for {', '.join(bad)}"
        if short:
            detail += "; " + "; ".join(f"{c}: {currencies[c].get('left_out_summary', '')}" for c in short)
        return (("partial" if len(bad) < n else "failed") if bad else ("partial" if short else "ok")), detail
    if name == "vol":
        diag = block.get("diagnostics") or []
        refused = block.get("rejected") or []
        detail = f"{block.get('written', 0)} quotes written"
        if diag:
            detail += f", {len(diag)} tickers gave nothing"
        if refused:
            detail += f", {len(refused)} left out ({refused[0].get('ticker', '')}: {refused[0].get('reason', '')})"
        return ("partial" if diag or refused else "ok"), detail
    if name == "options":
        skipped = block.get("skipped") or []
        return ("partial" if skipped else "ok"), (f"{block.get('priced', 0)} priced"
                                                  + (f", {len(skipped)} skipped" if skipped else ""))
    if name == "ledger":
        realised = block.get("realised")
        realised = len(realised) if isinstance(realised, (list, tuple)) else realised
        return "ok", block.get("refrozen_summary") or (f"{realised} realised" if realised is not None else "")
    return "ok", ""


# --------------------------------------------------------------------------- one pull
class _SharedSession:
    """The one blpapi session of a pull cycle (2026-09-21). Opened on first use, handed to
    the FX marks requests, to `RatesBloombergSource` and to `VolBloombergSource`, stopped
    once by `pull_once`. Until then a cycle opened three sessions in a row (FX, rates, vol),
    each paying its own connect + //blp/refdata handshake, against the same host and port
    and the same service. Requests stay strictly sequential, and the three modules draw
    their CorrelationIds from disjoint ranges, so a late reply to one step's timed-out
    request can never be read as another step's answer. `seconds` is the time spent
    opening (status["timings"]["session"]).

    An open that fails (2026-09-29, Phase G) is tried once per cycle: the failure is kept
    (`open_error`, its words; `open_traceback`) and every later ask raises
    `SessionUnavailable` at once, so a PC whose Terminal is not logged in pays the open
    once, not once per step, and `pull_once` knows the press had no Bloomberg."""

    def __init__(self, host: str, port: int, session_factory: Optional[Callable] = None, diag=None):
        self.host, self.port, self._factory, self._diag = host, port, session_factory, diag
        self.session = self.service = None
        self.seconds = 0.0
        self.opened = False
        self.open_error = ""
        self.open_traceback = ""

    def get(self):
        """(session, service), opening the session the first time it is asked for. An open
        that fails raises `SessionUnavailable` (its words the open's own), now and on every
        later ask of this cycle."""
        if self.open_error:
            raise SessionUnavailable(self.open_error)
        if self.session is None:
            started = time.perf_counter()
            try:
                if self._factory is None:
                    from data.bloomberg.pull_marks import open_session
                    session, service = open_session(self.host, self.port, self._diag)
                else:
                    session, service = self._factory()
                self.session, self.service = session, service
                self.opened = True
            except Exception as exc:  # noqa: BLE001 -- kept, and said by pull_once
                self.open_error = f"{type(exc).__name__}: {exc}"
                self.open_traceback = traceback.format_exc()
                raise SessionUnavailable(self.open_error) from exc
            finally:
                self.seconds += time.perf_counter() - started
        return self.session, self.service

    def stop(self) -> None:
        stop = getattr(self.session, "stop", None)
        self.session = self.service = None
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001
                pass


def _bloomberg_said(diag, ticker: str) -> str:
    """Bloomberg's own words for `ticker` in the LIVE_SPOT request just recorded in `diag`
    (its security error, else its field exceptions); '' when it said nothing."""
    return _bloomberg_said_for(diag, ticker, "LIVE_SPOT")


def _live_spot_rows(session, service, requests, as_of: date, diag, snapped: str):
    """Live PX_LAST via ReferenceDataRequest (intraday), unlike pull_marks' close-of-day
    historical path, for every SPOT request in ONE request. A failure carries Bloomberg's
    own reason when it gave one. Returns (rows, failures).

    A request that raises (a timeout, a dead session) raises to the caller since 2026-09-29:
    `pull_once` asks in chunks (`_ask_in_chunks`), which fails that chunk's rows with the
    reason and goes on. A value that is not a finite number fails its own row."""
    from data.bloomberg.pull_marks import fetch_reference, _plain_number
    spot_reqs = [r for r in requests if r.mark_type == "SPOT"]
    if not spot_reqs:
        return [], []
    tickers = sorted({r.bbg_ticker for r in spot_reqs})
    data = fetch_reference(session, service, tickers, ["PX_LAST"], diag=diag, tag={"purpose": "LIVE_SPOT"}) or {}
    rows, failures = [], []
    for r in spot_reqs:
        value = (data.get(r.bbg_ticker) or {}).get("PX_LAST")
        if value is None:
            said = _bloomberg_said(diag, r.bbg_ticker)
            failures.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": r.settle_date,
                             "detail": "no PX_LAST returned" + (f" (Bloomberg: {said})" if said else "")})
            continue
        fvalue = _plain_number(value)
        if fvalue is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": r.settle_date,
                             "detail": f"PX_LAST not numeric: {value!r}"})
            continue
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
                     "settle_date": as_of.isoformat(), "mark_type": r.mark_type, "value": fvalue,
                     "source": SRC_SPOT_FWD, "snapped_at": snapped})
    return rows, failures


def _fwd_outright_rows(session, service, fwd_reqs, as_of: date, spot_by_pair: Dict[str, float],
                       snapped: str) -> Tuple[List[dict], List[str], List[dict], List[dict]]:
    """FWD_OUTRIGHT per (pair, settle date) from the bulk FWD_CURVE table (one request
    per cycle for all pairs). EXACT tenor -> BBG_BFXFORWARD; interpolated -> BBG_INTERP
    (never official). Returns (rows, warnings, failures, curve_rows).

    `curve_rows` (2026-09-18): one official BBG_BFXFORWARD FWD_OUTRIGHT row per FWD_CURVE
    point with settle_date > as_of, at the tenor's OWN settle date, for every pair whose
    curve was fetched. Those points are Bloomberg's own quoted outrights, not
    interpolations, so official is correct for them; P&L reads a forward by the leg's
    exact settle date so nothing else changes, and engine/options' covered-interest-parity
    rate for a currency with no OIS curve (SEK, NOK, ...) needs at least two official
    points around the option expiry -- which, when every leg-date row was BBG_INTERP,
    it never had ("no curve/rate SEK" on the Bloomberg PC, 2026-09-17).

    A leg settling on or before `as_of` is marked directly at that pair's live SPOT
    (source BBG_BFXFORWARD -- still official, not a fallback) rather than through the
    curve at all: FWD_CURVE's points start at the first standard tenor *after* spot, so
    fwd_curve.outright_for_date has no interpolation range when settle_date == as_of --
    its from-spot branch only fires for spot_date < target, never ==. Found on the
    Bloomberg PC 2026-09-17: USDMXN settling the same calendar day it was pulled came back
    MISSING every cycle for exactly this reason. `past` settle dates (< as_of) never reach
    this function at all (pull_once filters those out as SKIPPED before calling it); this
    handles the boundary day itself."""
    from data.bloomberg.fwd_curve import outright_for_date, request_fwd_curves
    from data.bloomberg.pull_marks import _get_blpapi
    if not fwd_reqs:
        return [], [], [], []
    rows, warnings, failures, curve_rows = [], [], [], []
    today_reqs = [r for r in fwd_reqs if date.fromisoformat(r.settle_date) <= as_of]
    curve_reqs = [r for r in fwd_reqs if date.fromisoformat(r.settle_date) > as_of]
    for r in today_reqs:
        spot = spot_by_pair.get(r.instrument_id)
        if spot is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"settles on or before {as_of.isoformat()} but no live SPOT for "
                                       f"{r.instrument_id} was pulled this cycle to mark it at"})
            continue
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id, "settle_date": r.settle_date,
                     "mark_type": "FWD_OUTRIGHT", "value": float(spot), "source": SRC_SPOT_FWD, "snapped_at": snapped,
                     "detail": "settles today: marked at spot"})
    if not curve_reqs:
        return rows, warnings, failures, curve_rows
    blpapi = _get_blpapi()
    tickers = sorted({r.bbg_ticker for r in curve_reqs})
    try:
        curves = request_fwd_curves(blpapi, session, service, tickers)
    except Exception as exc:  # network layer raised: every forward fails with that reason
        failures += [{"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                     "detail": f"FWD_CURVE request raised: {exc!r}"} for r in curve_reqs]
        return rows, warnings, failures, curve_rows
    from data.bloomberg.pull_marks import _plain_number
    instrument_by_ticker = {r.bbg_ticker: r.instrument_id for r in curve_reqs}
    seen_points = set()
    # A point that is not a dated, finite number (2026-09-29) is dropped from its curve with a
    # warning: it is never written, and never stops the pair's other points or other pairs.
    for ticker, curve in list((curves or {}).items()):
        if not isinstance(curve, dict):
            curves[ticker] = {"points": [], "error": f"unreadable FWD_CURVE answer: {curve!r}"[:200]}
            continue
        good = []
        for point in curve.get("points") or []:
            try:
                point_date, raw = point
                value = _plain_number(raw)
            except (TypeError, ValueError):
                point_date, value = None, None
            if not isinstance(point_date, date) or value is None:
                warnings.append(f"{ticker}: FWD_CURVE point {point!r} is not a date and a number; left out")
                continue
            good.append((point_date, value))
        curve["points"] = sorted(good)
        # The table rows fwd_curve left out, with its reasons (bbg-curves, 2026-09-29).
        warnings.extend(f"{ticker}: FWD_CURVE {why}" for why in curve.get("skipped") or [])
        instrument_id = instrument_by_ticker.get(ticker)
        if instrument_id is None:
            continue
        for point_date, value in curve["points"]:
            if point_date <= as_of or (instrument_id, point_date) in seen_points:
                continue
            seen_points.add((instrument_id, point_date))
            curve_rows.append({"as_of_date": as_of.isoformat(), "instrument_id": instrument_id,
                               "settle_date": point_date.isoformat(), "mark_type": "FWD_OUTRIGHT",
                               "value": float(value), "source": SRC_SPOT_FWD, "snapped_at": snapped,
                               "detail": "FWD_CURVE tenor point"})
    for r in curve_reqs:
        curve = curves.get(r.bbg_ticker, {"points": [], "error": "no curve"})
        if not curve["points"]:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"FWD_CURVE: {curve.get('error') or 'no points'}"})
            continue
        target = date.fromisoformat(r.settle_date)
        try:
            value, how = outright_for_date(curve["points"], target, spot_by_pair.get(r.instrument_id), as_of)
            value = _plain_number(value) if value is not None else None
        except Exception as exc:  # noqa: BLE001 -- this forward fails alone
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"not read off the FWD_CURVE: {type(exc).__name__}: {exc}"})
            continue
        if value is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "FWD_OUTRIGHT", "settle_date": r.settle_date,
                             "detail": f"{r.settle_date} outside curve {curve['points'][0][0]}..{curve['points'][-1][0]}"
                                       + (" and no live spot for near-date interpolation" if how == "" and
                                          spot_by_pair.get(r.instrument_id) is None else "")})
            continue
        source = SRC_SPOT_FWD if how == "EXACT" else SRC_INTERP
        if how != "EXACT":
            warnings.append(f"{r.instrument_id} {r.settle_date}: {how.lower()} from FWD_CURVE tenors (source {SRC_INTERP})")
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id, "settle_date": r.settle_date,
                     "mark_type": "FWD_OUTRIGHT", "value": float(value), "source": source, "snapped_at": snapped})
    return rows, warnings, failures, curve_rows


def _row_problem(r, known: set) -> str:
    """Why a mark row cannot be written ('' when it can): an unknown instrument, a missing
    key field, or a value that is not a finite number (a stored value that is not a number is
    a data error, CLAUDE.md hard rule 2)."""
    from data.bloomberg.pull_marks import _plain_number
    if not isinstance(r, dict):
        return f"not a mark row: {r!r}"[:200]
    missing = [k for k in ("as_of_date", "instrument_id", "settle_date", "mark_type", "source", "snapped_at")
               if not str(r.get(k) or "").strip()]
    if missing:
        return f"no {', '.join(missing)}"
    if r["instrument_id"] not in known:
        return f"{r['instrument_id']} is not a known instrument"
    if _plain_number(r.get("value")) is None:
        return f"value {r.get('value')!r} is not a number"
    return ""


def write_marks(conn: sqlite3.Connection, rows: List[dict], rejected: Optional[List[dict]] = None) -> int:
    """INSERT OR REPLACE so each cycle refreshes the same key with a new snapped_at, in one
    short transaction (no Bloomberg request inside it). Only known instruments with a finite
    value (2026-09-29: a bad row never aborts the others' write); each row left out is
    appended to `rejected` when given, as {instrument_id, mark_type, settle_date, reason}.
    Returns the rows written."""
    known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    good = []
    for r in rows:
        problem = _row_problem(r, known)
        if problem:
            if rejected is not None:
                row = r if isinstance(r, dict) else {}
                rejected.append({"instrument_id": row.get("instrument_id", ""), "mark_type": row.get("mark_type", ""),
                                 "settle_date": row.get("settle_date", ""), "reason": f"not written: {problem}"})
            continue
        good.append(r)
    with conn:
        conn.executemany("INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?,?,?)",
                         [(r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"],
                           float(r["value"]), r["source"], r["snapped_at"]) for r in good])
    return len(good)


def _curves_step(conn: sqlite3.Connection, today: date, host: str, port: int, rates_source=None,
                 shared: Optional[_SharedSession] = None, ccys: Optional[List[str]] = None) -> dict:
    """Pull the OIS curve quotes of every currency the Bloomberg library lists under
    OIS_CURVE today (both currencies of every open FX option: engine/options needs a
    domestic AND a foreign discount curve, engine/options/inputs.py::resolve_market_inputs)
    into `curve_quotes`, then bootstrap each currency's curve into `curves`
    (engine.rates.store.bootstrap_and_store, source QL_PRICER). No swap is priced: the
    swaps, their fixings and the swap pricing left the pull on 2026-09-24 (commodity
    conversion Phase 2); this step was `_rates_step` and its block status["rates"].

    `rates_source` (anything with `get_curve_quotes`, e.g.
    data.bloomberg.rates_marketdata.RatesFileSource) is injectable for tests; default is a
    live `RatesBloombergSource` on `host:port`, borrowing the cycle's one session (`shared`).

    A currency outside the OIS set (`rates_marketdata.OIS_INDEX`) is never sent to
    Bloomberg; it is recorded with a plain-English reason, so an option's "no curve/rate"
    skip can name the missing curve. Never raises.

    Returns the status file's `curves` block: {as_of_date, currencies: {ccy: {quotes (rows
    written to curve_quotes), nodes (curve nodes bootstrapped into `curves`, 0 when not
    bootstrapped), error ('' when none)}}, bootstrapped (how many currencies have a curve
    today), seconds: {bloomberg, bootstrap}}, plus "skipped" (no FX option needs a curve)
    or "error" (the step could not run at all). `ccys` (2026-09-30): the OIS_CURVE keys off
    the cycle's one library read; read here when not given."""
    out: dict = {"currencies": {}, "bootstrapped": 0, "as_of_date": today.isoformat()}
    step_started = time.perf_counter()
    if ccys is None:
        from data.bloomberg import library
        ccys = library.keys(conn, today.isoformat(), "OIS_CURVE")
    ccys = sorted(ccys)
    if not ccys:
        out["skipped"] = "no FX_OPTION needs an OIS curve"
        return out
    try:
        from data.bloomberg import rates_marketdata as rm
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"rates_marketdata not importable: {exc!r}"
        return out
    in_scope = [c for c in ccys if c in rm.OIS_INDEX]
    _release_lock(conn)             # no write lock held across the Bloomberg requests below
    for ccy in (c for c in ccys if c not in rm.OIS_INDEX):
        out["currencies"][ccy] = {
            "quotes": 0, "nodes": 0,
            "error": f"{ccy} has no OIS index in scope ({', '.join(sorted(rm.OIS_INDEX))} only); "
                     "an option in this currency gets its rate implied from the pair's forward curve and "
                     "the other currency's OIS curve (engine/options/rates.py, IMPLIED_FORWARD); a manual "
                     "rate (engine/options/rates.py::set_manual_rate) is only needed if that forward curve "
                     "is not on file.",
        }
    if in_scope:
        if rates_source is None:
            try:
                if shared is not None:
                    session, service = shared.get()
                    rates_source = rm.RatesBloombergSource(host=host, port=port, session=session, service=service)
                else:
                    rates_source = rm.RatesBloombergSource(host=host, port=port)
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"RatesBloombergSource unavailable: {exc!r}"
                return out
        for ccy in in_scope:
            # `left_out` (2026-09-29, Phase G): each quote of the currency that did not land,
            # {ticker, reason}: the tickers Bloomberg gave nothing for (CurveSnapshot.failed)
            # and the quotes the writer refused (a value that is not a number), so a partly
            # failed currency never shows "N quotes" with no word on what is missing.
            entry = {"quotes": 0, "nodes": 0, "error": "", "left_out": []}
            try:
                snap = rates_source.get_curve_quotes(ccy, today)
                entry["left_out"] += [{"ticker": str(t), "reason": str(why)}
                                      for t, why in sorted((getattr(snap, "failed", None) or {}).items())]
                rejected: List[dict] = []
                entry["quotes"] = rm.write_curve_quotes(conn, snap, today.isoformat(), rejected=rejected)
                entry["left_out"] += [{"ticker": str(r.get("ticker") or r.get("tenor") or ""),
                                       "reason": str(r.get("reason") or "not written")} for r in rejected]
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            if entry["left_out"]:
                n = len(entry["left_out"])
                entry["left_out_summary"] = (f"{entry['quotes']} quotes written, {n} left out "
                                             f"({entry['left_out'][0]['ticker']}: {entry['left_out'][0]['reason']}"
                                             + (f"; +{n - 1} more" if n > 1 else "") + ")")
            out["currencies"][ccy] = entry
        close = getattr(rates_source, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    bootstrap_started = time.perf_counter()
    to_build = [c for c in in_scope if out["currencies"][c]["quotes"]]
    if to_build:
        try:
            from engine.rates.store import bootstrap_and_store
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"engine.rates.store.bootstrap_and_store not importable: {exc!r}"
            to_build = []
        for ccy in to_build:
            entry = out["currencies"][ccy]
            try:
                bootstrap_and_store(conn, today.isoformat(), ccy)
                entry["nodes"] = conn.execute(
                    "SELECT COUNT(*) FROM curves WHERE as_of_date = ? AND source = 'QL_PRICER' AND curve_id LIKE ?",
                    (today.isoformat(), f"{ccy}-%")).fetchone()[0]
                out["bootstrapped"] += 1
            except Exception as exc:  # noqa: BLE001 -- a curve that does not converge raises with its reason
                entry["error"] = f"bootstrap failed: {type(exc).__name__}: {exc}"
    out["seconds"] = {"bloomberg": round(bootstrap_started - step_started, 1),
                      "bootstrap": round(time.perf_counter() - bootstrap_started, 1)}
    return out


def _vol_step(conn: sqlite3.Connection, today: date, host: str, port: int, vol_source=None,
              shared: Optional[_SharedSession] = None, pairs: Optional[List[str]] = None) -> dict:
    """Pull the FX vol smile (ATM/RR/BF) for every pair with an open FX_OPTION into
    `vol_quotes`, so engine/options has something to resolve from (2026-09-17 fix): this
    pull never ran at all before -- live.py had no call into
    data/bloomberg/vol_marketdata.py anywhere -- so every option fell through SMILE and
    ATM_INTERP straight to the MANUAL option_vols fallback, which nobody had entered
    either, and was skipped "no vol". `vol_source` (e.g.
    data.bloomberg.vol_marketdata.VolFileSource) is injectable for tests; default is a
    live `VolBloombergSource` on `host:port`. Never raises: `{skipped}` when there is no
    open FX_OPTION; a per-ticker failure is recorded in `diagnostics`
    (pair/tenor/quote_type/ticker/detail, exactly VolFetchResult.diagnostics) rather than
    only a currency/pair name, so `_options_step` can name the specific failing Bloomberg
    ticker in a "no vol" skip reason (item 3c) -- see that function's `vol_diagnostics`
    parameter. `shared` (2026-09-21): the cycle's one blpapi session, borrowed by the live
    source instead of opening its own."""
    out: dict = {"pairs": {}, "written": 0, "diagnostics": [], "as_of_date": today.isoformat()}
    # Which pairs: the Bloomberg library's VOL_SMILE rows in force today (2026-09-21), so
    # only options still open. It used to be every FX_OPTION instrument ever on file
    # (vm.vol_pairs_needed; instruments outlive an upload), 45 tickers a pair, as long as
    # the book held one option of any age -- and three default pairs when it found none.
    # `pairs` (2026-09-30): the VOL_SMILE keys off the cycle's one library read.
    if pairs is None:
        from data.bloomberg import library
        pairs = library.keys(conn, today.isoformat(), "VOL_SMILE")
    if not pairs:
        out["skipped"] = "no FX_OPTION trades to price"
        return out
    try:
        from data.bloomberg import vol_marketdata as vm
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"vol_marketdata not importable: {exc!r}"
        return out
    _release_lock(conn)             # no write lock held across the Bloomberg requests below
    if vol_source is None:
        try:
            if shared is not None:
                session, service = shared.get()
                vol_source = vm.VolBloombergSource(host=host, port=port, session=session, service=service)
            else:
                vol_source = vm.VolBloombergSource(host=host, port=port)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"VolBloombergSource unavailable: {exc!r}"
            return out
    try:
        result = vol_source.get_vol_quotes(pairs)  # as_of=None: live ReferenceDataRequest, source BBG_BDP
        rejected: List[dict] = []
        out["written"] = vm.write_vol_quotes(conn, result, today.isoformat(), source=result.source,
                                             rejected=rejected)
        # The quotes the writer refused (2026-09-29, Phase G: a value that is not a number),
        # {pair, tenor, quote_type, ticker, reason}, beside the tickers that gave nothing.
        out["rejected"] = [dict(r) for r in rejected]
        out["diagnostics"] = list(result.diagnostics)
        out["pairs"] = {p: len(pv.quotes) for p, pv in result.pairs.items()}
        # 2026-09-18: persist what this cycle's response actually confirmed/rejected for
        # each UNVERIFIED ticker/field assumption in vol_marketdata.py's docstring, so
        # tools/bbg_diagnostics.py can report a real PASS/FAIL instead of always pointing
        # the user at --probe. Isolated in its own try/except so a bookkeeping failure
        # here never masks a vol_quotes write that otherwise succeeded (same discipline as
        # step).
        try:
            out["ticker_checks_recorded"] = vm.record_vol_ticker_checks(conn, result)
        except Exception as exc2:  # noqa: BLE001
            out["ticker_checks_error"] = f"{type(exc2).__name__}: {exc2}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    close = getattr(vol_source, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _enrich_no_vol_reason(conn: sqlite3.Connection, trade_id: str, reason: str, vol_diagnostics: List[dict]) -> str:
    """Turn a bare "no vol" skip reason into one naming the specific Bloomberg ticker(s)
    that failed for this trade's pair (item 3c), by cross-referencing
    _vol_step's `diagnostics` (one entry per failed ticker, each carrying its own `pair`)
    against the option's own pair. Returns `reason` unchanged if the trade's pair can't be
    found or nothing in `vol_diagnostics` matches it (e.g. the pair priced from
    ATM_INTERP/MANUAL and failed for an unrelated reason)."""
    row = conn.execute(
        "SELECT i.base_ccy || i.quote_ccy FROM trades t JOIN instruments i USING (instrument_id) "
        "WHERE t.trade_id = ?", (trade_id,)).fetchone()
    pair = row[0] if row else None
    if pair is None:
        return reason
    tickers = sorted({d["ticker"] for d in vol_diagnostics if d.get("pair") == pair and d.get("ticker")})
    if not tickers:
        return reason
    shown = ", ".join(tickers[:3]) + (f" (+{len(tickers) - 3} more)" if len(tickers) > 3 else "")
    return (f"{reason}: no vol_quotes for {pair} -- Bloomberg returned nothing for {shown}; run "
            "py -3 -m data.bloomberg.vol_marketdata --probe on the Bloomberg PC to check these tickers.")


def _listed_option_products() -> tuple:
    """The listed option products (pnl-valuation's LISTED_OPTION_PRODUCTS: EQ_OPTION, the
    generic listed path, and CMDTY_OPTION, an option on a commodity future)."""
    try:
        from engine.pnl.valuation import LISTED_OPTION_PRODUCTS
        return tuple(LISTED_OPTION_PRODUCTS)
    except Exception:  # noqa: BLE001
        return ("EQ_OPTION", "CMDTY_OPTION")


def _listed_option_ids(conn: sqlite3.Connection) -> set:
    """The instruments whose live price is Bloomberg's mid (PX_MID, else PX_LAST): every
    listed option, told by its asset class (EQ_OPTION, CMDTY_OPTION, the products' own
    names) or by the product of a trade on it."""
    products = _listed_option_products()
    marks = ", ".join("?" for _ in products)
    return {r[0] for r in conn.execute(
        f"SELECT instrument_id FROM instruments WHERE asset_class IN ({marks}) "
        f"UNION SELECT instrument_id FROM trades WHERE product IN ({marks})", products + products)}


# The products the options step prices (2026-09-24, Phase 5): the FX options and the options
# on commodity futures (options-store's price_all_and_store prices both).
PRICED_OPTION_PRODUCTS = ("FX_OPTION", "CMDTY_OPTION")


def futures_options_sentence(n: int) -> str:
    """'N option(s) on futures priced', the options step's line for the options on commodity
    futures (2026-09-24); '' for none."""
    return f"{n} option{'s' if n != 1 else ''} on futures priced" if n else ""


def _option_trade_count(conn: sqlite3.Connection, today: date) -> int:
    """How many option trades (PRICED_OPTION_PRODUCTS) dealt on or before `today` are on
    file: none, and the options step has nothing to price."""
    return int(conn.execute(f"SELECT COUNT(*) FROM trades_official WHERE product IN "
                            f"({', '.join('?' for _ in PRICED_OPTION_PRODUCTS)}) AND trade_date <= ?",
                            PRICED_OPTION_PRODUCTS + (today.isoformat(),)).fetchone()[0])


def _options_step(conn: sqlite3.Connection, today: date, vol_diagnostics: Optional[List[dict]] = None) -> dict:
    """Price every FX option and every option on a commodity future through engine/options
    (PREMIUM + Greeks; options-store's price_all_and_store prices both). Never raises. The
    listed index options' Greeks left with the equity index on 2026-09-24 (their P&L is
    Bloomberg's price of them, the FUTURE_PX this pull writes, and needs no pricing).
    `vol_diagnostics` (see _vol_step) is used only to enrich a "no vol" skip reason with
    the specific failing Bloomberg ticker(s) for that trade's pair (item 3c, 2026-09-17).

    2026-09-24 (Phase 5): a book with options on commodity futures and no FX option is
    priced too (it returned "no FX_OPTION trades to price" before). `priced` counts both
    products; `futures_options_priced` the CMDTY_OPTION outcomes priced, with its sentence
    under `futures_options_summary` ("N options on futures priced") when there are any."""
    out: dict = {"priced": 0, "skipped": [], "closed_out": [], "as_of_date": today.isoformat(),
                 "futures_options_priced": 0}
    if not _option_trade_count(conn, today):
        out["skipped"] = "no option trades to price"
        return out
    try:
        from engine.options.store import price_all_and_store
        outcomes = list(price_all_and_store(conn, today.isoformat()))
        out["priced"] = sum(1 for o in outcomes if getattr(o, "priced", False))
        out["futures_options_priced"] = sum(1 for o in outcomes if getattr(o, "priced", False)
                                            and getattr(o, "product", "") == "CMDTY_OPTION")
        if out["futures_options_priced"]:
            out["futures_options_summary"] = futures_options_sentence(out["futures_options_priced"])
        skipped, closed_out = [], []
        for o in outcomes:
            if getattr(o, "priced", False):
                continue
            if getattr(o, "closed_out", False):
                # A closed-out option is not live (CLAUDE.md): not priced, no mark, and
                # listed under its own head, never as a skip (2026-09-22).
                closed_out.append(o.trade_id)
                continue
            reason = getattr(o, "skip_reason", "") or getattr(o, "reason", "")
            if reason == "no vol" and vol_diagnostics:
                reason = _enrich_no_vol_reason(conn, o.trade_id, reason, vol_diagnostics)
            skipped.append({"trade_id": o.trade_id, "reason": reason})
        out["skipped"] = skipped
        out["closed_out"] = closed_out
        if closed_out:
            out["closed_out_summary"] = closed_out_sentence(len(closed_out))
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{exc!r}"
    return out


# --------------------------------------------------------------------------- LME curves
def lme_summary(block: dict) -> str:
    """The one sentence the feed status shows for `status["lme"]`: 'LME curves: N mark(s)
    written for M metal(s), K interpolated', with the pillars or prompts left without a mark
    counted after it and an error named. '' when the book has no LME forward."""
    roots = list(block.get("roots") or [])
    if not roots and not block.get("error"):
        return ""
    written, interp = int(block.get("written") or 0), int(block.get("interp_written") or 0)
    missing = len(block.get("missing") or [])
    text = (f"LME curves: {written} mark{'' if written == 1 else 's'} written for {len(roots)} "
            f"metal{'' if len(roots) == 1 else 's'}, {interp} interpolated")
    if missing:
        text += f"; {missing} pillar{'' if missing == 1 else 's'} or prompt{'' if missing == 1 else 's'} without a mark"
    if block.get("error"):
        text += f"; stopped ({block['error']})"
    return text


def _lme_open_prompts(conn: sqlite3.Connection, today: str,
                      needed: Optional[List[dict]] = None) -> Dict[str, List[str]]:
    """{root id: its open LME forwards' prompt dates, sorted}, from the library's in-force
    FWD_OUTRIGHT rows of the LME product (each ticket's own prompt). `needed`: the cycle's
    one library read (`needed_live`, 2026-09-30); read here when not given."""
    from data.bloomberg import library
    lme_products = tuple(getattr(library, "LME_PRODUCTS", ("LME_FWD",)))
    out: Dict[str, set] = {}
    for r in (library.needed_on(conn, today) if needed is None else _requestable(needed)):
        if r.get("product") in lme_products and r.get("kind") == "FWD_OUTRIGHT":
            out.setdefault(r["key"], set()).add(r["settle_date"])
    return {k: sorted(v) for k, v in out.items()}


def _has_lme_curve(needed: List[dict]) -> bool:
    """Does the cycle's library read (`needed_live`) hold an LME curve a pull may ask for?"""
    try:
        from data.bloomberg.library import LME_CURVE
    except ImportError:
        LME_CURVE = "LME_CURVE"
    return any(r["kind"] == LME_CURVE for r in _requestable(needed))


def _lme_step(conn: sqlite3.Connection, today: date, get_session: Callable, snapped: Optional[str] = None,
              needed: Optional[List[dict]] = None) -> dict:
    """The LME curves of the book's open LME forwards (2026-09-24, commodity conversion
    Phase 5): for each metal the library lists (`library.lme_curves_needed`: in force,
    requestable, its pillars trimmed to the furthest open prompt), Bloomberg's PX_LAST and
    prompt date of each pillar ticker (cash, 3M, the monthly prompts; bbg-curves'
    `fwd_curve.request_lme_pillars`, one request for every metal's pillars), turned into
    mark rows by `fwd_curve.lme_curve_marks` (cash as SPOT, pillars and the open prompts as
    FWD_OUTRIGHT, BBG_BFXFORWARD where Bloomberg quotes that date, BBG_INTERP between
    pillars, never extrapolated) and written to `marks` stamped at the pull's time
    (`snapped`). `get_session()` returns (session, service) and is called only when a metal
    is to be asked. Never raises.

    The rows are written exactly as `lme_curve_marks` builds them, never re-sourced or
    re-keyed (reviewer, 2026-09-24): the P&L reads a metal's cash price only as SPOT under
    BBG_BFXFORWARD keyed settle_date = as_of_date, and its pillars and prompts only as
    FWD_OUTRIGHT under BBG_BFXFORWARD, else BBG_INTERP; a cash price under BBG_BDH (the
    futures' habit) or keyed on the cash date would blank every LME ticket.

    Returns the status file's `lme` block: {roots (the metals asked, sorted), written (mark
    rows written), interp_written (of which BBG_INTERP), missing ([{root_id, ticker,
    settle_date, reason}]: a pillar Bloomberg gave no price for, ticker '' for an open
    prompt left without a forward), reasons (lme_curve_marks' own reasons, each opened by
    its metal), summary (`lme_summary`)}, plus "error" when the step stopped.

    `needed` (2026-09-30): the cycle's one library read (`needed_live`). With no LME curve
    in it the step ends before reading anything else; otherwise the metals' pillars are
    read as before (`library.lme_curves_needed`) and the open prompts off `needed`."""
    block: dict = {"roots": [], "written": 0, "interp_written": 0, "missing": [], "reasons": []}

    def _done() -> dict:
        block["summary"] = lme_summary(block)
        return block

    today_iso = today.isoformat()
    try:
        from data.bloomberg import library
        if needed is not None and not _has_lme_curve(needed):
            return _done()                  # no LME forward open: nothing to read or ask
        needs = list(library.lme_curves_needed(conn, today_iso)) if hasattr(library, "lme_curves_needed") else []
        open_prompts = _lme_open_prompts(conn, today_iso, needed) if needs else {}
    except Exception as exc:  # noqa: BLE001
        block["error"] = f"LME curves not read from the library: {exc!r}"
        return _done()
    needs = [e for e in needs if e.get("pillars")]
    if not needs:
        return _done()
    block["roots"] = sorted(e["root_id"] for e in needs)
    try:
        from data.bloomberg.fwd_curve import lme_curve_marks, request_lme_pillars
    except ImportError as exc:
        block["error"] = f"data.bloomberg.fwd_curve LME helpers not importable: {exc!r}"
        return _done()
    tickers = sorted({p["ticker"] for e in needs for p in e["pillars"] if p.get("ticker")})
    _release_lock(conn)             # no write lock held across the Bloomberg request below
    try:
        from data.bloomberg.pull_marks import _get_blpapi
        session, service = get_session()
        quotes = request_lme_pillars(_get_blpapi(), session, service, tickers) or {}
    except Exception as exc:  # noqa: BLE001 -- the pull goes on without the LME marks
        if len(needs) < 2 or _is_timeout(exc) or isinstance(exc, SessionUnavailable):
            block["error"] = f"LME pillar request failed: {exc}"
            return _done()
        # One metal's ticker that breaks the request must not cost the other metals their
        # curves (2026-09-29): asked again metal by metal; a metal that still fails keeps
        # its reason on each of its pillars.
        quotes = {}
        for entry in needs:
            own = sorted({p["ticker"] for p in entry["pillars"] if p.get("ticker")})
            try:
                quotes.update(request_lme_pillars(_get_blpapi(), session, service, own) or {})
            except Exception as exc_one:  # noqa: BLE001
                quotes.update({t: {"value": None, "prompt_date": None,
                                   "error": f"LME pillar request failed: {exc_one}"} for t in own})
                block["reasons"].append(f"{entry['root_id']}: LME pillar request failed ({exc_one})")
    snapped = snapped or _now_iso()
    for entry in needs:
        root = entry["root_id"]
        for p in entry["pillars"]:
            got = quotes.get(p["ticker"]) or {}
            if got.get("value") is None:
                block["missing"].append({"root_id": root, "ticker": p["ticker"], "settle_date": p.get("settle_date", ""),
                                         "reason": str(got.get("error") or "Bloomberg returned no PX_LAST")})
        prompts = open_prompts.get(root, [])
        try:        # its rows are written as they come: never re-sourced or re-keyed here
            rows, reasons = lme_curve_marks(root, today, entry["pillars"], quotes,
                                            [date.fromisoformat(d) for d in prompts], snapped)
        except Exception as exc:  # noqa: BLE001 -- one metal fails alone
            block["reasons"].append(f"{root}: marks not built ({exc!r})")
            rows, reasons = [], []
        rows = list(rows or [])
        block["reasons"] += [str(r) for r in reasons or []]      # each already names its metal
        rejected: List[dict] = []
        try:        # one metal's write fails alone (2026-09-29)
            block["written"] += write_marks(conn, rows, rejected)   # an unknown instrument or a bad value is left out
        except Exception as exc:  # noqa: BLE001
            block["reasons"].append(f"{root}: marks not written ({type(exc).__name__}: {exc})")
            rejected = [{"settle_date": r.get("settle_date", ""), "mark_type": r.get("mark_type", ""),
                         "reason": f"not written: {exc}"} for r in rows]
        refused = {(r.get("settle_date"), r.get("mark_type")) for r in rejected}
        rows = [r for r in rows if (r.get("settle_date"), r.get("mark_type")) not in refused]
        block["interp_written"] += sum(1 for r in rows if r.get("source") == SRC_INTERP)
        block["reasons"] += [f"{root} {r.get('mark_type', '')} {r.get('settle_date', '')}: {r['reason']}"
                             for r in rejected]
        marked = {r["settle_date"] for r in rows if r.get("mark_type") == "FWD_OUTRIGHT"}
        for prompt in prompts:
            if prompt not in marked:
                block["missing"].append({"root_id": root, "ticker": "", "settle_date": prompt,
                                         "reason": f"no forward for the {prompt} prompt on the {today_iso} curve"})
    return _done()


def closed_out_sentence(n: int) -> str:
    """The one sentence about closed-out options a status line carries when there are any
    (2026-09-22): appended to the recalc summary, and under the options step's
    "closed_out_summary". '' for none."""
    return f"{n} closed-out option{'s' if n != 1 else ''} not priced" if n else ""


def recalc_options_on_file(db_path, today: date) -> dict:
    """What "Pull Bloomberg now" does on a machine with no Bloomberg (user, 2026-09-22, to
    the options-pricer agent: "pull bbg now should recalc options too, using log data if no
    bbg access, or pull new data for new calculation"; confirmed to the session: "yes I asked
    for the recalc, wire it in"): every FX option re-priced from the marks on file -- the
    imported snapshot's spots, forwards, OIS quotes and vols, day by day from the first
    option trade to the book date (engine.options.store.recalc_on_file) -- instead of
    leaving everything as it was. Asks Bloomberg nothing (hard rule 8). Returns that
    function's own dict, {"as_of", "since", "days": [per-day price_close dicts], "priced",
    "skipped", "closed_out" (the count of closed-out options, not priced and not among the
    skipped; 2026-09-22)} plus "error" when something raised, in the same shape when the
    pricer is not importable or the database cannot be opened. Never raises. With Bloomberg
    the connected cycle's own options step and the backfill's price_close cover this, so
    `pull_once` calls it only when no session was opened."""
    as_of = today.isoformat()
    empty = {"as_of": as_of, "since": None, "days": [], "priced": 0, "skipped": 0, "closed_out": 0,
             "futures_options_priced": 0}
    try:
        from engine.options.store import recalc_on_file
    except ImportError as exc:
        return {**empty, "error": f"engine.options.store.recalc_on_file not importable: {exc!r}"}
    from data.ingest.schema import connect
    try:
        conn = connect(Path(db_path))
        try:
            return recalc_on_file(conn, as_of)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- said in the status, never raised into the cycle
        return {**empty, "error": f"{exc!r}"}


def recalc_summary(result: dict) -> str:
    """One plain sentence about `recalc_options_on_file`'s result, for the status line and
    the Market data tab (status["recalc_summary"]; status["reason"] stays the connection
    reason). Since 2026-09-24 (Phase 5) the options on commodity futures among the priced
    are said after it ("; N options on futures priced", recalc_on_file's
    `futures_options_priced`), and an empty book is "no option on file"."""
    priced, skipped = int(result.get("priced") or 0), int(result.get("skipped") or 0)
    closed_out = result.get("closed_out") or 0
    closed_out = len(closed_out) if isinstance(closed_out, (list, tuple)) else int(closed_out)
    futures_options = int(result.get("futures_options_priced") or 0)
    days, as_of = len(result.get("days") or []), result.get("as_of")
    head = "no Bloomberg on this machine: "
    tail = f"; {futures_options_sentence(futures_options)}" if futures_options else ""
    tail += f"; {closed_out_sentence(closed_out)}" if closed_out else ""      # never counted as skipped
    if result.get("error"):
        return head + (f"re-pricing the options from the marks on file stopped ({result['error']}); "
                       f"{priced} priced, {skipped} skipped before that") + tail
    if priced == 0 and skipped == 0:
        return head + f"no option on file to re-price as of {as_of}" + tail
    return head + (f"options re-priced from the marks on file as of {as_of}, "
                   f"{priced} priced, {skipped} skipped over {days} day(s)") + tail


def pull_once(db_path, as_of_date: Optional[str] = None, host: str = "localhost", port: int = 8194,
              session_factory: Optional[Callable] = None, today: Optional[date] = None,
              rates_source=None, vol_source=None, lend_session: Optional[list] = None) -> dict:
    """One full cycle. Returns and writes the status dict. Never raises: any exception
    becomes connected=False with the traceback in `reason`. `today` (the date every mark of
    this cycle is stamped with, the curves / vol / options steps price and realise_settled
    freezes as of) defaults to the book date, `book_today`: the New York date, rolled at
    17:00 New York (user decision 2026-09-22); injectable for tests. `vol_source` mirrors
    `rates_source`'s injection for _vol_step (data.bloomberg.vol_marketdata's live/file
    source).

    No Bloomberg on this machine (the availability check fails, or the session cannot be
    opened): nothing is pulled, connected=False with the reason as before, and the FX
    options are re-priced from the marks on file (`recalc_options_on_file`, user 2026-09-22),
    the result under status["recalc"] and one sentence under status["recalc_summary"]; its
    time is status["timings"]["options"].

    `status["timings"]` (2026-09-21) says where the cycle's time went, in seconds rounded
    to 0.1, always with the same keys (TIMING_KEYS; 0.0 for a step that did not run):
    `session` opening the one shared blpapi session; `spot`, `forwards` (the FWD_CURVE
    request and the interpolation) and `futures` the three FX-side Bloomberg requests;
    `curves` the OIS quotes and their bootstrap (split again in status["curves"]["seconds"];
    "rates" until 2026-09-24, when the swap pricing and fixings left the step);
    `vol` the vol quotes; `options` the option pricing; `ledger` realise_settled; `total`
    the whole cycle. What the steps do not cover is in `status["timings_other"]`: building
    the request list and writing the FX marks, which is where a wait for the SQLite write
    lock (an upload, the backfill) would show.

    Contract dates (2026-09-24): before the request list is built, `contract_dates_step`
    asks Bloomberg for FUT_LAST_TRADE_DT / FUT_NOTICE_FIRST of the library's CONTRACT_DATES
    futures, stores them and moves those futures onto Bloomberg's expiry, so their FUTURE_PX
    is asked for and written at it; its block is status["contract_dates"] (time under
    "futures"). A future the library lists with no Bloomberg ticker is never asked for and
    is listed under status["not_requestable"] with its reason.

    Phase 5 (2026-09-24): the contract dates cover the options on commodity futures too
    (their own fields); an option on a future is priced live at Bloomberg's mid like any
    listed option, its underlying future asked with the futures; the LME curves of the open
    LME forwards are pulled after the futures prices (`_lme_step`, status["lme"], time
    under "forwards"); the options step prices the options on futures as well
    (status["options"]["futures_options_priced"]).

    Fault isolation (2026-09-29, Phase G "Smooth and contained"): the cycle is the steps of
    PULL_STEPS, each run on its own. A step that raises is recorded (status["steps"], a
    warning) and the next step runs; the ledger always runs. The spot, forwards and futures
    requests go in chunks (`_ask_in_chunks`), so a request Bloomberg cannot answer fails its
    own rows only, each with its reason in `items`. No write lock is held across a Bloomberg
    request: every step's writes are committed when it ends (rolled back when it raised).
    A session that cannot be opened is the press having no Bloomberg (as before: connected
    False, the options re-priced from the marks on file) and the ledger still runs. A
    session that opened but whose every request raised, with nothing written, is a failed
    pull (connected False, "pull failed: <reason>", the first traceback); anything written
    makes it a connected pull with its failures listed.

    `status["steps"]`: one entry per step in order, {step, label, outcome (OUTCOMES: ok |
    partial | failed | skipped), detail (plain words), seconds}. `status["progress"]`: while
    the pull runs, {running: true, started_at, updated_at, step, step_label, step_index,
    step_count, done, total, sentence} is published into the status file at every step and
    chunk (`set_progress`, the rest of the file untouched); `done` / `total` are the marks
    of the request list settled so far / asked (`total` is 0 until the list is built). The
    final status carries it with running false, finished_at, outcome (ok | partial |
    failed) and the closing sentence.

    Less waste per press (2026-09-30, user at the Bloomberg PC): the library is read once
    after the contract dates (`needed_live`) and handed to every later step; spot and futures
    go 50 tickers a request (REQUEST_CHUNK); a step known to have nothing to ask still runs
    and is recorded, but the progress line is not rewritten for it, nor for a count that did
    not move. What is asked, written and stamped is unchanged.

    `lend_session` (2026-09-30, the pull's speed): a list the cycle's blpapi session is
    handed into, as its `_SharedSession`, instead of being stopped, so the backfill of the
    same press asks its history on it rather than opening a second one. It is lent only
    when the session opened cleanly, the cycle's body ran to its end and the pull is
    connected (a session whose every request raised is stopped here, as before); the
    caller then owns it and must call its `stop()` exactly once. The cycle has finished
    with the session before it is lent: nothing here touches it again."""
    from data.ingest.schema import connect
    clock_started = time.perf_counter()
    started = _now_iso()
    status = {"time": started, "connected": False, "reason": "", "host": f"{host}:{port}",
              "as_of_date": as_of_date, "requested": 0, "written": 0, "failed": 0, "items": [], "warnings": []}
    timings = {key: 0.0 for key in TIMING_KEYS}
    other = {"build_requests": 0.0, "write_marks": 0.0}
    shared: Optional[_SharedSession] = None
    conn: Optional[sqlite3.Connection] = None
    net = _NetLog()
    steps: List[dict] = []
    progress = _Progress(db_path, started)

    def _timed(book: dict, key: str, fn, *args, **kwargs):
        """fn(*args, **kwargs), its wall time added to book[key] -- less whatever it spent
        opening the shared session, which is reported once, under "session"."""
        opening = shared.seconds if shared is not None else 0.0
        step_started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            opened = (shared.seconds if shared is not None else 0.0) - opening
            book[key] += time.perf_counter() - step_started - opened

    def _record(name: str, outcome: str, detail, seconds: float) -> None:
        steps.append({"step": name, "label": STEP_LABELS.get(name, name), "outcome": outcome,
                      "detail": str(detail or "")[:500], "seconds": round(max(seconds, 0.0), 1)})

    def _run(name: str, book: dict, key: str, fn, *args, judge: Optional[Callable] = None, quiet: bool = False,
             **kwargs):
        """(True, result) or (False, the exception): one step, timed under book[key], its
        writes committed when it returns and rolled back when it raises, its outcome
        recorded (`judge(result)` -> (outcome, detail), else `_step_outcome`). A
        SessionUnavailable is recorded and raised on. `quiet` (2026-09-30): the step is
        known to have nothing to ask, so the progress line is not rewritten for it; it runs
        and is recorded as ever."""
        progress.step(name, publish=not quiet)
        step_started = time.perf_counter()
        try:
            result = _timed(book, key, fn, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- the step fails alone
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
            _record(name, "failed", _plain_error(exc), time.perf_counter() - step_started)
            if isinstance(exc, SessionUnavailable):
                raise
            status["warnings"].append(f"{STEP_LABELS.get(name, name)} stopped: {_plain_error(exc)}")
            status.setdefault("step_errors", {})[name] = {"reason": _plain_error(exc),
                                                          "traceback": traceback.format_exc()}
            return False, exc
        if conn is not None:
            _release_lock(conn)
        outcome, detail = judge(result) if judge is not None else _step_outcome(name, result)
        _record(name, outcome, detail, time.perf_counter() - step_started)
        return True, result

    def _skip(name: str, why: str) -> None:
        progress.step(name)
        _record(name, "skipped", why, 0.0)

    def _finish() -> dict:
        timings["session"] = shared.seconds if shared is not None else 0.0
        timings["total"] = time.perf_counter() - clock_started
        status["timings"] = {key: round(timings[key], 1) for key in TIMING_KEYS}
        status["timings_other"] = {key: round(value, 1) for key, value in other.items()}
        status["steps"] = steps
        if not status.get("connected"):
            outcome = "failed"
            sentence = "Pull stopped: " + (status.get("recalc_summary") or status.get("reason") or "no reason recorded")
        else:
            bad = [s["label"] for s in steps if s["outcome"] in ("failed", "partial")]
            outcome = "partial" if bad or status.get("failed") else "ok"
            sentence = f"Pull finished: {status.get('written', 0)} of {status.get('requested', 0)} marks written"
            if status.get("failed"):
                sentence += f", {status['failed']} failed"
            if bad:
                sentence += f"; problems in {', '.join(bad)}"
        status["progress"] = progress.final(outcome, sentence)
        write_status(db_path, status)
        return status

    def _all_failed(reqs, exc) -> dict:
        """A marks step that raised: every row it had to ask fails with the step's reason."""
        return {"rows": [], "warnings": [], "curve_rows": [],
                "failures": [{"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": r.settle_date,
                              "detail": f"{r.mark_type} not priced: the step stopped ({_plain_error(exc)})"}
                             for r in reqs]}

    def _not_asked(reqs) -> dict:
        """A marks step the press could not run for want of a session: every row fails with that."""
        return {"rows": [], "warnings": [], "curve_rows": [],
                "failures": [{"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": r.settle_date,
                              "detail": f"{r.mark_type} not asked: no Bloomberg session ({shared.open_error})"}
                             for r in reqs]}

    def _marks_judge(asked: int):
        return lambda got: _step_outcome("", None, failed_items=len(got.get("failures") or []), asked=asked)

    try:
        # The book date (book_today), fixed once here so every date this cycle stamps,
        # prices, freezes or re-prices as of is the same one. It used to default to
        # MAX(positions.as_of_date) -- the last BNP snapshot date, a source that no longer
        # feeds the app (2026-09-17 fix).
        today = today or book_today()
        ok, why = availability(host, port) if session_factory is None else (True, "")
        if not ok:
            status["reason"] = why
            progress.steps = ["recalc"]
            progress.block["step_count"] = 1
            step_started = time.perf_counter()
            progress.step("recalc")
            status["recalc"] = _timed(timings, "options", recalc_options_on_file, db_path, today)
            status["recalc_summary"] = recalc_summary(status["recalc"])
            _record("recalc", "failed" if status["recalc"].get("error") else "ok", status["recalc_summary"],
                    time.perf_counter() - step_started)
            return _finish()
        from data.bloomberg import pull_marks as pm
        diag = pm.Diagnostics()
        shared = _SharedSession(host, port, session_factory, diag)
        conn = connect(Path(db_path))
        body_done = False
        try:
            if as_of_date is None:
                as_of_date = today.isoformat()
            status["as_of_date"] = as_of_date
            warnings: List[str] = status["warnings"]
            no_session = False
            # Bloomberg's contract dates FIRST (2026-09-24): a commodity future carries an
            # estimated expiry until they are stored, and apply_contract_dates moves its
            # instrument, legs and marks onto Bloomberg's; the legs' change marks the
            # library out of date, so build_requests below reads the futures as moved and
            # their FUTURE_PX lands under Bloomberg's expiry.
            done, block = _run("contract_dates", timings, "futures", contract_dates_step, conn, today, shared.get, diag)
            if not done:
                block = {"requested": 0, "stored": 0, "failed": [], "applied": {}, "error": _plain_error(block)}
                block["summary"] = contract_dates_summary(block)
            status["contract_dates"] = block
            asked_failed = [f for f in block.get("failed") or [] if str(f.get("reason", "")).startswith("request failed")]
            if int(block.get("requested") or 0) > len({f.get("ticker") for f in asked_failed}):
                net.ok()
            elif asked_failed and not shared.open_error:
                net.raised.append({"step": "contract_dates", "reason": asked_failed[0]["reason"], "traceback": ""})
            no_session = bool(shared.open_error)

            # The library, read once for the cycle (2026-09-30), after the contract dates
            # moved the futures: the request list, the futures with no ticker, and the LME,
            # curves and vol steps all work off this one read. None when it could not be
            # read, and then each reads the library itself, as before.
            needed: Optional[List[dict]] = None

            def _build() -> list:
                nonlocal needed
                needed = needed_live(conn, as_of_date)
                return build_requests(conn, as_of_date, needed)

            done, requests = _run("requests", other, "build_requests", _build,
                                  judge=lambda got: ("ok", f"{len(got)} marks to ask") if got
                                  else ("skipped", "no open FX leg or future to price"))
            # The later steps price and ask as of `today`: the read serves them when it is
            # of the same date (always, but for a --as-of run of the command line).
            today_needed = needed if as_of_date == today.isoformat() else None
            requests_built = done
            if not done:
                status["requests_error"] = _plain_error(requests)
                requests = []
            status["requested"] = len(requests)
            progress.set_total(len(requests))
            # A future the library lists with no Bloomberg ticker is never asked for; it is
            # listed here with its reason (2026-09-24).
            status["not_requestable"] = not_requestable_futures(conn, as_of_date, needed)
            for entry in status["not_requestable"]:
                warnings.append(f"FUTURE_PX {entry['instrument_id']} not requested: {entry['reason']}")
            snapped = _now_iso()  # live pull: the real press time, never the 17:00 close stamp
            empty = {"rows": [], "warnings": [], "failures": [], "curve_rows": []}

            # Spot: live PX_LAST, in chunks of REQUEST_CHUNK["spot"] tickers.
            spot_reqs = [r for r in requests if r.mark_type == "SPOT"]

            def _ask_spot(chunk):
                session, service = shared.get()
                rows, failures = _live_spot_rows(session, service, chunk, today, diag, snapped)
                return {"rows": rows, "failures": failures}

            spot = empty
            if no_session:
                _skip("spot", f"no Bloomberg session ({shared.open_error})")
                spot = _not_asked(spot_reqs)
            else:
                try:
                    done, spot = _run("spot", timings, "spot", _ask_in_chunks, "spot", spot_reqs, REQUEST_CHUNK["spot"],
                                      _ask_spot, net, progress, judge=_marks_judge(len(spot_reqs)),
                                      quiet=not spot_reqs)
                    spot = spot if done else _all_failed(spot_reqs, spot)
                except SessionUnavailable:
                    no_session = True
                    spot = _not_asked(spot_reqs)
            spot_by_pair = {r["instrument_id"]: r["value"] for r in spot["rows"] if r["mark_type"] == "SPOT"}

            # Forwards: a forward whose settle date is already past (trade settled since the
            # snapshot) has nothing to price: reported SKIPPED, never FAILED, never counted
            # as missing.
            past = {(r.instrument_id, r.settle_date) for r in requests
                    if r.mark_type == "FWD_OUTRIGHT" and date.fromisoformat(r.settle_date) < today}
            fwd_reqs = [r for r in requests if r.mark_type == "FWD_OUTRIGHT" and (r.instrument_id, r.settle_date) not in past]
            progress.add_done(sum(1 for r in requests if r.mark_type == "FWD_OUTRIGHT") - len(fwd_reqs))

            def _ask_forwards(chunk):
                later = any(date.fromisoformat(r.settle_date) > today for r in chunk)
                session, service = shared.get() if later else (None, None)
                rows, warns, failures, curve_rows = _fwd_outright_rows(session, service, chunk, today, spot_by_pair,
                                                                       snapped)
                return {"rows": rows, "warnings": warns, "failures": failures, "curve_rows": curve_rows}

            forwards = empty
            if no_session:
                _skip("forwards", f"no Bloomberg session ({shared.open_error})")
                forwards = _not_asked(fwd_reqs)
            else:
                try:
                    done, forwards = _run("forwards", timings, "forwards", _ask_in_chunks, "forwards", fwd_reqs,
                                          REQUEST_CHUNK["forwards"], _ask_forwards, net, progress,
                                          judge=_marks_judge(len(fwd_reqs)), quiet=not fwd_reqs)
                    forwards = forwards if done else _all_failed(fwd_reqs, forwards)
                except SessionUnavailable:
                    no_session = True
                    forwards = _not_asked(fwd_reqs)

            # Futures and listed options: live=True (PX_LAST first, the latest PX_SETTLE as
            # the fallback: PX_SETTLE for `today` has nothing to return before that day's US
            # close, 2026-09-17). A listed option (EQ_OPTION, 2026-09-21; an option on a
            # commodity future, CMDTY_OPTION, since 2026-09-24) is asked like a future, but
            # its live price is Bloomberg's mid: the last trade of one strike can be hours
            # old. snapped (2026-09-28): the press time, so a press's FUTURE_PX row never
            # looks like the day's 17:00 close and the backfill replaces it once the day is past.
            fut_reqs = [r for r in requests if r.mark_type == "FUTURE_PX"]
            try:
                listed = _listed_option_ids(conn) if fut_reqs else set()
            except Exception as exc:  # noqa: BLE001 -- the options then take PX_LAST, said
                listed = set()
                warnings.append(f"listed options not told apart from futures (PX_LAST used): {_plain_error(exc)}")

            def _ask_futures(chunk):
                session, service = shared.get()
                rows, warns, failures = pm.build_future_rows(
                    session, service, chunk, today, diag, live=True,
                    mid_first={r.bbg_ticker for r in chunk if r.instrument_id in listed}, snapped=snapped)
                return {"rows": rows, "warnings": warns, "failures": failures}

            futures = empty
            if no_session:
                _skip("futures", f"no Bloomberg session ({shared.open_error})")
                futures = _not_asked(fut_reqs)
            else:
                try:
                    done, futures = _run("futures", timings, "futures", _ask_in_chunks, "futures", fut_reqs,
                                         REQUEST_CHUNK["futures"], _ask_futures, net, progress,
                                         judge=_marks_judge(len(fut_reqs)), quiet=not fut_reqs)
                    futures = futures if done else _all_failed(fut_reqs, futures)
                except SessionUnavailable:
                    no_session = True
                    futures = _not_asked(fut_reqs)
            progress.add_done(len(requests))             # every row is settled now, asked or not
            warnings.extend(forwards["warnings"] + futures["warnings"])

            # The marks, in one short write each: the requested rows, then Bloomberg's own
            # FWD_CURVE tenor points at their own dates as official forwards -- kept out of
            # `written` so "wrote N of M requested" stays exact. A row that cannot be written
            # (an unknown instrument, a value that is not a number) is left out with its reason.
            rows = spot["rows"] + forwards["rows"] + futures["rows"]
            requested_keys = {(r["instrument_id"], r["mark_type"], r["settle_date"]) for r in rows}
            points = [r for r in forwards["curve_rows"]
                      if (r["instrument_id"], r["mark_type"], r["settle_date"]) not in requested_keys]
            rejected: List[dict] = []

            def _write_all():
                n = write_marks(conn, rows, rejected)
                points_rejected: List[dict] = []
                m = write_marks(conn, points, points_rejected)
                if points_rejected:
                    warnings.append(f"{len(points_rejected)} FWD_CURVE tenor point(s) not written: "
                                    f"{points_rejected[0]['reason']}")
                return n, m

            done, got = _run("write_marks", other, "write_marks", _write_all,
                             judge=lambda nm: (("partial" if rejected else "ok") if rows or points else "skipped",
                                               f"{nm[0]} marks and {nm[1]} curve points written"
                                               + (f", {len(rejected)} left out" if rejected else "")))
            if done:
                written, curve_points_written = got
            else:
                written, curve_points_written = 0, 0
                rejected = [{"instrument_id": r["instrument_id"], "mark_type": r["mark_type"],
                             "settle_date": r["settle_date"], "reason": f"not written: {_plain_error(got)}"} for r in rows]
            refused = {(r["instrument_id"], r["mark_type"], r["settle_date"]) for r in rejected}
            landed_rows = [r for r in rows if (r["instrument_id"], r["mark_type"], r["settle_date"]) not in refused]

            # LME curves (2026-09-24, Phase 5): after the futures prices, before the ledger,
            # so an LME forward whose prompt is today freezes at today's cash price. Its
            # marks are reported in status["lme"], not among the requested items.
            if no_session:
                _skip("lme", f"no Bloomberg session ({shared.open_error})")
            else:
                no_lme = today_needed is not None and not _has_lme_curve(today_needed)
                done, lme = _run("lme", timings, "forwards", _lme_step, conn, today, shared.get, snapped,
                                 today_needed, quiet=no_lme)
                status["lme"] = lme if done else {"roots": [], "written": 0, "interp_written": 0, "missing": [],
                                                  "reasons": [], "error": _plain_error(lme)}
                if not done:
                    status["lme"]["summary"] = lme_summary(status["lme"])
                if status["lme"].get("roots") and not status["lme"].get("error"):
                    net.ok()
                if status["lme"].get("summary"):
                    warnings.extend(f"LME {m['root_id']} {m['ticker'] or 'prompt'} {m['settle_date']}: {m['reason']}"
                                    for m in status["lme"].get("missing") or [])
                    if status["lme"].get("error"):
                        warnings.append(f"LME curves: {status['lme']['error']}")

            # Curves: the OIS curve quotes of every open option's currencies into
            # curve_quotes, bootstrapped into `curves` (no swap is priced since 2026-09-24).
            # Vol (2026-09-17): the FX vol smile (ATM/RR/BF) into vol_quotes for every open
            # option's pair. Then every option priced (PREMIUM and Greeks, source
            # QL_OPTIONS_PRICER), with the vol step's per-ticker diagnostics available to
            # enrich a "no vol" skip reason. All three before realise_settled so an option
            # expiring today freezes at today's mark.
            if no_session:
                _skip("curves", f"no Bloomberg session ({shared.open_error})")
                _skip("vol", f"no Bloomberg session ({shared.open_error})")
            else:
                ccys = _live_keys(today_needed, "OIS_CURVE") if today_needed is not None else None
                done, got = _run("curves", timings, "curves", _curves_step, conn, today, host, port, rates_source,
                                 shared=shared, ccys=ccys, quiet=ccys == [])
                status["curves"] = got if done else {"currencies": {}, "bootstrapped": 0,
                                                     "as_of_date": today.isoformat(), "error": _plain_error(got)}
                if any((e or {}).get("quotes") for e in (status["curves"].get("currencies") or {}).values()):
                    net.ok()
                warnings.extend(f"OIS {ccy}: {e['left_out_summary']}"
                                for ccy, e in sorted((status["curves"].get("currencies") or {}).items())
                                if (e or {}).get("left_out_summary"))
                pairs = _live_keys(today_needed, "VOL_SMILE") if today_needed is not None else None
                done, got = _run("vol", timings, "vol", _vol_step, conn, today, host, port, vol_source, shared=shared,
                                 pairs=pairs, quiet=pairs == [])
                status["vol"] = got if done else {"pairs": {}, "written": 0, "diagnostics": [],
                                                  "as_of_date": today.isoformat(), "error": _plain_error(got)}
                if status["vol"].get("written"):
                    net.ok()
                warnings.extend(f"vol {r.get('pair', '')} {r.get('tenor', '')} {r.get('quote_type', '')}: "
                                f"{r.get('reason', '')}" for r in status["vol"].get("rejected") or [])
            no_session = no_session or bool(shared.open_error)
            if no_session:
                # No Bloomberg for this press after all (the port answers but no Terminal is
                # logged in): the options are re-priced from the marks on file, as when
                # Bloomberg is not available at all.
                progress.step("recalc")
                step_started = time.perf_counter()
                status["recalc"] = _timed(timings, "options", recalc_options_on_file, db_path, today)
                status["recalc_summary"] = recalc_summary(status["recalc"])
                _record("recalc", "failed" if status["recalc"].get("error") else "ok", status["recalc_summary"],
                        time.perf_counter() - step_started)
            else:
                try:
                    no_options = not _option_trade_count(conn, today)
                except Exception:  # noqa: BLE001 -- the step itself then says what went wrong
                    no_options = False
                done, got = _run("options", timings, "options", _options_step, conn, today,
                                 (status.get("vol") or {}).get("diagnostics"), quiet=no_options)
                status["options"] = got if done else {"priced": 0, "skipped": [], "closed_out": [],
                                                      "as_of_date": today.isoformat(), "futures_options_priced": 0,
                                                      "error": _plain_error(got)}

            # Realisation (BUILD_PLAN.md section 6, Task B): freeze the trades whose settle
            # date is before `today` and are not yet in realised_pnl. Always run, whatever
            # the steps above did (2026-09-29).
            def _ledger() -> dict:
                try:
                    from engine.pnl.ledger import realise_settled
                except ImportError as exc:
                    return {"skipped": f"engine.pnl.ledger.realise_settled not importable: {exc!r}"}
                # What the ledger did, its re-freeze included (2026-09-22): see ledger_block.
                return ledger_block(realise_settled(conn, today.isoformat()), today.isoformat())

            done, got = _run("ledger", timings, "ledger", _ledger)
            if done:
                status["ledger"] = got
            else:
                status["ledger"] = {"error": f"{got!r}"}
                warnings.append(f"realise_settled failed: {got!r}")

            # Items: one per requested mark, OK only when its row was written.
            failures = spot["failures"] + forwards["failures"] + futures["failures"]
            failures += [{**r, "detail": r["reason"]} for r in rejected]
            ok_keys = {(r["instrument_id"], r["mark_type"], r["settle_date"]): r for r in landed_rows}
            items = []
            for r in requests:
                settle = today.isoformat() if r.mark_type == "SPOT" else r.settle_date
                if (r.instrument_id, r.settle_date) in past and r.mark_type == "FWD_OUTRIGHT":
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "SKIPPED", "value": None, "source": "",
                                  "detail": f"settle date already past on {today}: settled, nothing to price"})
                    continue
                hit = ok_keys.get((r.instrument_id, r.mark_type, settle))
                if hit:
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "OK", "value": hit["value"], "source": hit["source"],
                                  "detail": hit.get("detail", "")})
                else:
                    detail = next((f.get("detail", "") or f.get("reason", "") for f in failures
                                   if f.get("instrument_id") == r.instrument_id and f.get("mark_type") == r.mark_type
                                   and (r.mark_type == "SPOT" or f.get("settle_date") == r.settle_date)),
                                  "not returned")
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "FAILED", "value": None, "source": "", "detail": detail})
            status["items"] = items
            status["failed"] = sum(1 for i in items if i["status"] == "FAILED")
            status["skipped"] = sum(1 for i in items if i["status"] == "SKIPPED")

            # Connected or not: no session is no Bloomberg for this press; a session whose
            # every request raised with nothing landed is a failed pull; anything landed is
            # a connected pull with its failures listed.
            landed = (written + curve_points_written + int((status.get("lme") or {}).get("written") or 0)
                      + int(status["contract_dates"].get("stored") or 0)
                      + sum(int((e or {}).get("quotes") or 0)
                            for e in ((status.get("curves") or {}).get("currencies") or {}).values())
                      + int((status.get("vol") or {}).get("written") or 0))
            if no_session:
                status.update(connected=False, reason="pull failed: " + shared.open_error,
                              traceback=shared.open_traceback)
            elif net.raised and not landed:
                first = next((e for e in net.raised if e["traceback"]), net.raised[0])
                status.update(connected=False, reason="pull failed: " + first["reason"],
                              traceback=first["traceback"] or "".join(
                                  s.get("traceback", "") for s in (status.get("step_errors") or {}).values()))
            else:
                status.update(connected=True,
                              reason="" if requests or not requests_built else "no open FX legs or futures to price")
            status.update(written=written, curve_points_written=curve_points_written,
                          warnings=list(warnings)[:50], as_of_marks=today.isoformat())
            body_done = True
        finally:
            try:
                conn.close()
            finally:
                # The blpapi session this cycle opened must be stopped here, not left to
                # garbage collection: one leaked session per cycle (and per "Pull now" click)
                # was the 2026-09-18 audit's top resource finding. The curves and vol sources
                # only borrow it (their close() leaves a borrowed session alone). The one
                # exception (2026-09-30): a clean session is lent to the caller, who stops it.
                if (lend_session is not None and body_done and status.get("connected")
                        and shared.opened and not shared.open_error and shared.session is not None):
                    lend_session.append(shared)
                else:
                    shared.stop()
    except Exception:
        status["connected"] = False
        status["reason"] = "pull failed: " + traceback.format_exc(limit=3).strip().splitlines()[-1]
        status["traceback"] = traceback.format_exc()
        if today is not None and not (shared is not None and shared.opened) and "recalc" not in status:
            # The cycle could not run at all before a session was opened: no Bloomberg for
            # this press either, so the options are re-priced from the marks on file.
            status["recalc"] = _timed(timings, "options", recalc_options_on_file, db_path, today)
            status["recalc_summary"] = recalc_summary(status["recalc"])
    return _finish()

# --------------------------------------------------------------------------- feed thread
@dataclass
class LiveFeed:
    """Pulls Bloomberg ON REQUEST only (user decision 2026-09-21: "make it only pull the
    bloomberg info on request - no automatic"). The thread sleeps until `trigger_now()`
    -- the "Pull Bloomberg now" button -- and then runs one cycle: today's marks
    (`pull_once`), then the past closes still missing (`start_auto_backfill`), the second
    run whatever the first did, and neither able to stop the thread. There is
    no pull when the app starts, none on a timer and none after an upload or an edit;
    between requests the app shows the marks on file, with the time of the last pull."""
    db_path: Path
    host: str = "localhost"
    port: int = 8194
    last_status: Optional[dict] = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _wake: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def start(self) -> "LiveFeed":
        self._thread = threading.Thread(target=self._loop, name="bloomberg-live-feed", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger_now(self) -> None:
        """Ask for one pull, now. A request made while a pull is running gets a pull of
        its own straight after it (that one built its request list before the request)."""
        self._wake.set()

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            lent: List[_SharedSession] = []
            try:
                self.last_status = pull_once(self.db_path, host=self.host, port=self.port, lend_session=lent)
            except Exception:  # pull_once already catches; this guards the thread itself
                try:
                    now = _now_iso()
                    write_status(self.db_path, {
                        "time": now, "connected": False,
                        "reason": "feed thread error: " + traceback.format_exc().strip().splitlines()[-1],
                        "traceback": traceback.format_exc(), "requested": 0, "written": 0, "failed": 0,
                        "items": [], "warnings": [],
                        PROGRESS_KEY: {"running": False, "finished_at": now, "updated_at": now, "outcome": "failed",
                                       "step": "done", "done": 0, "total": 0,
                                       "sentence": "Pull stopped: feed thread error"}})
                except Exception:  # noqa: BLE001 -- the thread must live to take the next press
                    pass
            self._backfill(lent[0] if lent else None)

    def _backfill(self, shared: Optional[_SharedSession]) -> None:
        """The backfill hand-off, on its own (2026-09-29): it runs whatever the pull did,
        and a failure to start it is said in the backfill's own block -- it used to
        overwrite the pull's whole status with a "feed thread error".

        `shared` (2026-09-30, the pull's speed): the pull's own session, lent by `pull_once`
        when it opened cleanly, so the backfill asks its history on it instead of opening a
        second one. The pull has finished with it, so it is used by one thread at a time.
        It is stopped exactly once: at once when no backfill run starts (None: a run already
        in flight) or the start raised; else by a small watcher thread once the backfill
        thread ends, raised or not, so a new press is never held up by the wait. With no
        lent session the backfill opens its own, as before."""
        thread = None
        try:
            from data.bloomberg.backfill import start_auto_backfill
            if shared is None:
                start_auto_backfill(self.db_path, host=self.host, port=self.port)
            else:
                thread = start_auto_backfill(self.db_path, host=self.host, port=self.port,
                                             session=(shared.session, shared.service))
        except Exception:  # noqa: BLE001
            thread = None
            try:
                patch_status(self.db_path, "backfill", {
                    "running": False,
                    "reason": "backfill failed to start: " + traceback.format_exc().strip().splitlines()[-1]})
            except Exception:  # noqa: BLE001
                pass
        if shared is None:
            return
        join = getattr(thread, "join", None)
        if not callable(join):
            shared.stop()
            return

        def _stop_after_backfill() -> None:
            try:
                join()
            except Exception:  # noqa: BLE001 -- the session is stopped whatever the wait did
                pass
            finally:
                shared.stop()

        try:
            threading.Thread(target=_stop_after_backfill, name="bloomberg-session-return", daemon=True).start()
        except Exception:  # noqa: BLE001 -- no watcher: wait here rather than leak the session
            _stop_after_backfill()


def start_feed_if_available(db_path, host: str = "localhost", port: int = 8194) -> Tuple[Optional[LiveFeed], str]:
    """`(feed, "")`: the on-request feed, started asleep -- nothing is asked of Bloomberg
    here. It is started whether or not Bloomberg answers right now, so that a Terminal
    logged into after the app was launched still gets its pull when the button is pressed
    (`pull_once` checks the connection itself on every request and records why it could
    not pull). The status file is left as the last pull wrote it, so every screen keeps
    showing when the marks on file were pulled; only when there is no status at all is
    one written, saying no pull has been requested yet."""
    if read_status(db_path) is None:
        ok, why = availability(host, port)
        write_status(db_path, {"time": _now_iso(), "connected": False,
                               "reason": (why if not ok else
                                          "no pull requested yet: press Pull Bloomberg now"),
                               "host": f"{host}:{port}", "requested": 0, "written": 0, "failed": 0,
                               "items": [], "warnings": []})
    return LiveFeed(Path(db_path), host, port).start(), ""


# --------------------------------------------------------------------------- rates for the ladder
_LATEST_SPOT_SQL = """
SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.source, m.snapped_at, m.as_of_date
FROM marks_official m JOIN instruments i USING (instrument_id)
WHERE m.mark_type = 'SPOT' AND i.asset_class = 'FX'
ORDER BY m.instrument_id, m.as_of_date, m.snapped_at
"""


def rates_from_marks(conn: sqlite3.Connection, now: Optional[datetime] = None,
                     stale_after_seconds: int = STALE_AFTER_SECONDS) -> Dict[str, dict]:
    """currency -> {rate, inverted, source, timestamp, stale} from the LATEST official SPOT
    mark per USD pair (last as_of_date, then last snapped_at). USDXXX pairs are inverted.
    Crosses are ignored. A currency with no mark is simply absent (-> MISSING_RATE).
    stale = snapped_at older than stale_after_seconds relative to `now`."""
    now = now or datetime.now(timezone.utc)
    latest: Dict[str, tuple] = {}
    for pair, base, quote, value, source, snapped, as_of in conn.execute(_LATEST_SPOT_SQL):
        if "USD" not in (base, quote):
            continue
        latest[pair] = (base, quote, value, source, snapped, as_of)  # ordered ascending: last wins
    out: Dict[str, dict] = {}
    for pair, (base, quote, value, source, snapped, as_of) in latest.items():
        ccy = quote if base == "USD" else base
        stale = True
        try:
            ts = datetime.fromisoformat(snapped)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            stale = (now - ts) > timedelta(seconds=stale_after_seconds)
        except ValueError:
            pass
        out[ccy] = {"rate": float(value), "inverted": base == "USD", "source": source,
                    "timestamp": snapped, "stale": stale, "as_of_date": as_of, "pair": pair}
    return out


# --------------------------------------------------------------------------- CLI
def _print_status(status: Optional[dict]) -> None:
    if not status:
        print("No Bloomberg status recorded yet.")
        return
    print(f"time {status.get('time')}  connected {status.get('connected')}  {status.get('reason', '')}")
    print(f"as_of {status.get('as_of_date')}  requested {status.get('requested')}  written {status.get('written')}"
          f"  failed {status.get('failed')}")
    for it in status.get("items", []):
        val = "" if it.get("value") is None else f"{it['value']:.8f}"
        print(f"  {it['status']:6s} {it['instrument_id']:8s} {it['mark_type']:12s} {it['settle_date']}  {val:>16s}  "
              f"{it.get('source', '')}  {it.get('detail', '')}")
    # The steps' own sentences (2026-09-24): contract dates, LME curves, options on futures.
    for line in ((status.get("contract_dates") or {}).get("summary"), (status.get("lme") or {}).get("summary"),
                 (status.get("options") or {}).get("futures_options_summary") if isinstance(status.get("options"), dict)
                 else None):
        if line:
            print("  " + line)
    for step in status.get("steps") or []:       # the per-step outcome (2026-09-29)
        if isinstance(step, dict) and step.get("outcome") in ("failed", "partial"):
            print(f"  step {step.get('label')}: {step.get('outcome')} -- {step.get('detail')}")
    for w in status.get("warnings", []):
        print("  warning:", w)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bloomberg live feed diagnostics / single pull.")
    parser.add_argument("--db", default=None, help="SQLite path (default: data.paths.get_db_path())")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--once", action="store_true", help="run one pull now and print its status")
    parser.add_argument("--status", action="store_true", help="print the last recorded status")
    args = parser.parse_args(argv)
    if args.db is None:
        from data.paths import get_db_path
        args.db = get_db_path()
    if args.once:
        status = pull_once(args.db, args.as_of, args.host, args.port)
    else:
        status = read_status(args.db)
        if status is None:
            ok, why = availability(args.host, args.port)
            status = {"time": _now_iso(), "connected": ok, "reason": why or "no pull has run yet",
                      "requested": 0, "written": 0, "failed": 0, "items": []}
    _print_status(status)
    return 0 if status.get("connected") and not status.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
