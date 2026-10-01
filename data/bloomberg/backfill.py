"""Backfill past-close Bloomberg marks history (BUILD_PLAN.md section 3/6, Task B).

`engine/pnl/ledger.py::ltd` recomputes LTD from `marks` on demand. Per CLAUDE.md's "P&L
conventions", Daily/5d/MTD/YTD all difference LTD(t) against LTD(t-1bd) / LTD(t-5bd) /
etc, and every FX leg's LTD needs the FWD_OUTRIGHT for its OWN settle_date (never a single
shared date), every future's needs FUTURE_PX -- SPOT alone is not enough to price a single
past day. For every business day in [start, end] this module writes, as official marks
dated that day:
  - SPOT: Bloomberg's daily close (PX_LAST, see "The close" below) of every FX pair
    with a leg open at any point in [start, end] --
    crosses included, plus every USD-conversion pair a cross's legs need for delta/P&L
    (`traded_pairs`, mirrors `live._cross_usd_legs`; USD-pairs-only until 2026-09-18,
    which meant a cross like EURSEK never got a SPOT close backfilled at all and could
    never be frozen by realise_settled once it settled) -- one HistoricalDataRequest per
    stretch of days being worked (see below). Also (2026-09-18) every FX option's pair
    and the option's own USD-conversion pairs, for every day the option was open
    including its expiry date (`spot_only_pair_names`). From Bloomberg's history, SPOT
    only for options: no historical forward at the expiry and no historical vol is asked
    for (hard rule 8 unchanged) -- the closes are what the expiry-day catch-up's payoff
    and the ledger's base->USD conversion read. The day's options ARE priced, though
    (2026-09-22; user: "options daily pnl 0, that cannot be right, everything is moving"
    and "I expect every time I pull bloomberg now, the options are repriced with the
    latest data, and that the latest data is also logged / overwriting previous marks"):
    once a day's closes are written, `engine.options.store.price_close(conn, day)` prices
    every FX option open that day (trade_date <= day <= expiry) strictly from whatever
    that day already has on file -- its SPOT close, forward curve, OIS curve and vol smile,
    written by that day's own pull or by this backfill -- and writes the pricer marks dated
    that day at the close stamp, replacing any earlier ones. A trade whose inputs that day
    lacks is skipped with its reason (`options_skipped`) and the near-marks rule stands for
    it. Until then no past day ever had a premium of its own (the Bloomberg PC's snapshot
    held vol_quotes and curves for 2026-09-17..21 but pricer marks for 09-21 alone), so the
    later day's premium was carried back: LTD identical on both days, options Daily 0.
    Guarded like realise_settled (`_import_price_close`, `_price_options_close`): not
    importable, or raising, the day's marks and realisation stand and `options_priced` is
    None with the failure named in `options_note`.
  - The inputs those options price from (2026-09-22, later the same day: until then a past
    day's options were priced only where a live pull had happened to write that day's
    smile and curve, so they had no true 5d / MTD / YTD and the LTD chart carried
    premiums): for every past day worked, the vol-smile quotes of every pair with an FX
    option open that day and the OIS quotes of every currency an open FX option needs
    that day -- the same tickers the live vol and rates steps ask for today
    (vol_marketdata.vol_ticker over VOL_TENORS x VOL_QUOTE_TYPES,
    rates_marketdata.ois_curve), read from the Bloomberg library like everything else
    (library.history_inputs_needed) -- from Bloomberg's daily history, PX_LAST, one
    HistoricalDataRequest per kind per stretch of days (`_fetch_vol_history`,
    `_fetch_ois_history`), written into the same tables the live steps write (vol_quotes,
    curve_quotes) dated that day under BBG_BDH, stamped at the close. The daily PX_LAST is
    Bloomberg's own close of those quotes, the same close every mark takes since
    2026-09-28. A day that already holds a pair's smile or a currency's curve (the live
    pull's own, or an earlier run's) is never asked for it again
    (inventory.inputs_missing); a currency with no OIS curve in scope (SEK) is never
    asked for. The options pricer builds each day's discount curves from those quotes
    itself (engine.options.rates reads curve_quotes). Nothing new is asked on a live pull
    for today. (Commodity conversion Phase 2, 2026-09-24: the swaps' re-pricing of past
    days and the NDF fixings' history left with the rates and NDF books; user yes
    2026-09-24.)
  - FWD_OUTRIGHT (2026-09-18): for every FX leg open on that day (trade_date <= day <=
    ... <= settle_date), at the leg's own settle_date. A leg settling on or before that
    day is marked at that day's own SPOT close (same rule the live feed uses). Otherwise
    interpolated linearly from that day's standard-tenor curve (fwd_curve.historical_curve)
    -- never extrapolated beyond the last tenor point. Bloomberg does not serve the bulk
    FWD_CURVE field through HistoricalDataRequest the way it does live via
    ReferenceDataRequest, so the historical curve is assembled from the standard-tenor
    tickers instead (pull_marks.STANDARD_TENORS), one HistoricalDataRequest per stretch
    of days being worked, not one per day. Those tickers quote forward points
    by the live tenor path's account (converted as it converts them: spot + points /
    the pair's points divisor, pull_marks.fetch_points_scales: FWD_POINTS_SCALE as is,
    else 10 ** FWD_SCALE -- the Bloomberg PC returned nothing for the first, 2026-09-21;
    a converted forward more than 20 % away from that day's spot is a wrong divisor and
    is not written), and history sends no SETTLE_DT, so the tenor dates are
    computed by market convention; a forward built on either is written BBG_INTERP, and
    BBG_BFXFORWARD is kept for Bloomberg's own outright at Bloomberg's own date
    (2026-09-21: before, no past forward was ever written, so no past day could complete).
  - FUTURE_PX (2026-09-18): the daily PX_LAST of every future open on that day -- and of
    every listed option on its own ticker, which the library lists as a FUTURE_PX
    (user, 2026-09-22: "all futures for past date pnl calculation, use px last"; PX_SETTLE
    until then, of which Bloomberg served no history for the listed options) -- same
    batched one-request-for-the-whole-range approach. Stamped at Bloomberg's daily close
    (`close_stamp`: 17:00 New York). A past day's FUTURE_PX row that a live press wrote
    (PX_LAST or PX_MID, stamped at the press) is not a close: once the day is past the
    backfill asks that day's daily PX_LAST and replaces it, as it replaces any past row
    not stamped at the close. A commodity future
    (2026-09-24) is asked under the name Bloomberg gives it on the day of the request, the
    one-digit year while it trades and the two-digit canonical id once expired
    (`_future_request_tickers`), and written on its own instrument_id; a future with no
    Bloomberg ticker (a placeholder root) is never asked: the library leaves it out of what
    a past close needs and lists the gap with its reason.
  - Options on commodity futures (CMDTY_OPTION, commodity conversion Phase 5, 2026-09-24):
    the option's own FUTURE_PX, asked like a future under its name on the request day
    (`option_request_ticker`: the canonical two-digit id once expired), and its underlying
    future's (library role UNDERLYING, asked even when the future itself is not traded),
    both in pass 1; the OIS curve its Greeks discount on is one of the day's inputs (no vol
    smile: its vol is implied from its own price). price_close then prices the day's
    CMDTY_OPTIONs too (`_options_open_on`: open until the NOTIONAL leg's settle date), and the
    count among them is `futures_options_priced`.
  - LME curves (LME_FWD, Phase 5, 2026-09-24): for every metal with a ticket open that day,
    the daily PX_LAST of its curve pillars (library.lme_curve_pillars: cash, 3M and the
    monthly prompts up to the furthest open prompt), one request per stretch with the
    futures' fetcher, turned into rows by fwd_curve.lme_history_marks (cash -> the metal's
    SPOT, pillars and open prompts -> FWD_OUTRIGHT, BBG_INTERP on a computed date, never
    extrapolated) and stamped `close_stamp` (17:00 New York) like everything else.
    Written in pass 1, since an LME ticket freezes at the last cash price on or before its
    prompt. The FX paths never see an LME row (library.needed_in_range leaves them out
    unless include_lme).
Only what is needed is asked for (user decision 2026-09-21: "only the data necessary for
the pnl calcs of the trades ... also for the backfill"), all of it read from the Bloomberg
library (data/bloomberg/library.py): the days being worked, as stretches of consecutive
business days (`_runs`) -- never the days between an old incomplete day and yesterday;
within a stretch, the pairs and futures the book needed inside it; on a day, the closes
of the pairs needed that day; and per pair, the tenors up to the one that clears its
furthest open leg (`_tenors_needed`), since a forward is read between the two tenors
either side of its date.

The close (user decision 2026-09-28, an explicit yes under hard rule 7: "everything closes
on its day at the time at which its specific exchange closes"). Every instrument's past
close is Bloomberg's daily close for that instrument on that date -- the daily history's
PX_LAST (pull_marks.fetch_historical_series, one request per kind per stretch) for FX
spot, the FX tenor series the past forward curves are built from, futures, listed options
and the LME pillars alike -- and every row is stamped 17:00 New York of its own date,
`close_stamp`, the one close stamp of the app (CLOSE_HOUR_NY, defined here). Nothing is
read off an intraday bar. History: from 2026-09-21 ("the EOD is 3pm New York time") to
2026-09-28 a past FX close was the 15:00 New York mid of Bloomberg's intraday BID and ASK
bars, stamped 15:00, and only futures, listed options and the LME pillars took the 17:00
daily close; that rule, its intraday fetch, its ~140-business-day intraday floor and the
15:00-or-17:00 stamp choice were reversed on 2026-09-28. A 15:00 row of that rule still on
file is not a close any more: the backfill asks that day's daily close and replaces it,
as it replaces a live press's row.

Smooth and contained (2026-09-29, Phase G; user: "if something pulls badly it doesnt crash
everything - but of course most important is to make sure that everything pulls
correctly"): the history is asked in chunks (HISTORY_CHUNK tickers, a key's tickers kept
together), a chunk that raises is asked again ticker by ticker, and nothing more is sent
after live.TIMEOUTS_BEFORE_GIVING_UP timeouts in a row ("not asked"); the write lock is
released before every request; a value that is not a number is never written and is
counted with its reason; the session, each history stage and each day's steps are guarded
on their own, a failure recorded in plain words and the rest run; a day whose failure says
nothing about the data (a request that failed or was not sent, a step that raised) is due
again on the next press; and the top bar's status["progress"] carries the pull's block on
with "Backfilling closes: 3 of 7 days" while the backfill runs (`_Asks`,
`_BackfillProgress`). What is asked, and which source is official, are unchanged.

Only what is not stored (2026-09-30, user: "bloomberg needs to store on cache all the data - and
only pull any new data"): a day being worked asks only the marks it still lacks at the close,
key by key (`_wanted_keys`; an overwrite run asks everything), so the hourly retry of a
recent day that came back incomplete asks only what is still missing; a
forward-points divisor answered today is not asked again today (`_scale_cache`); and the risk
history keeps the stretches Bloomberg answered with nothing (`_trim_known_empty`). What each
press asked, left alone and got nothing for is the status block's "cache" (`_cache_block`).

A day counts as complete (skipped unless overwrite=True) only once ALL of the above are
official for it -- `data.bloomberg.inventory.close_completeness`, the same "needed" set
`data.bloomberg.live.build_requests` uses for the live feed, so the three can never drift
apart. A row already official AT THE CLOSE for its (day, instrument, settle_date,
mark_type) is never rewritten, even when overwrite is False and the day is otherwise
incomplete (e.g. a new trade added a settle_date this day never needed before). A past
day's official row that is NOT stamped at the close -- that day's last live pull (say
11:40), or a 15:00 row of the retired rule -- is not a close, whatever its mark type or
instrument: the day counts as incomplete and the row is replaced once the close is in hand
(`is_close_row`, `_write_closes`). It is never deleted without its replacement, today's
rows are never touched (intraday = live), and realised_pnl is never touched here: frozen
rows stay frozen (the ledger's own next pass re-freezes a trade whose mark changed). A day
whose marks are all closes but that lacks a smile or a curve its FX options need
(close_completeness's `inputs_missing`) is worked too, for those inputs alone: its rows at
the close are left as they are.

Only if `engine.pnl.ledger.realise_settled` is importable, trades settled before a day are
frozen once that day's marks are on file: every day's SPOT and FUTURE_PX (all a freeze
reads) are written together before anything else, see `backfill`. The automatic run after a
pull freezes once, at its end (`LEDGER_AT_END`, 2026-10-01: one ledger call per press, the
live pull leaving its own out); a standalone call freezes as it goes. This module no longer writes a
`pnl_snapshots` row; that table and its "one snapshot per day" model are retired by the
pnl-engine task, which recomputes `ltd(conn, date)` straight from `marks` instead.

Limits, stated plainly:
  - Trades are only those currently in the database (the blotter is the app's only trade
    source; a re-upload replaces the whole book -- see CLAUDE.md's schema notes).
  - Every close is Bloomberg's daily PX_LAST -- FX spot and tenors, futures and listed
    options, the LME pillars, the vol and OIS quotes; the live pull's rows are stored
    under the same official sources, and the snapped_at timestamp tells them apart
    (backfill rows are stamped 17:00 New York on their date; a live row carries the time
    it was pulled).
  - Calendar is the trading calendar (Monday-Friday less config/holidays.txt, see
    `business_days`): a listed holiday is never asked of Bloomberg. A day Bloomberg
    returns nothing for is still reported as NO_CLOSES.
  - UNVERIFIED on a terminal (docs/open-questions.md items 28 and 30): that the tenor
    tickers quote points (fwd_curve.tenor_unit checks the magnitude against spot
    rather than assume) and the points-divisor field (FWD_POINTS_SCALE / FWD_SCALE).
    Computed tenor dates use config/holidays.txt only -- there are no per-currency
    calendars -- so around a local holiday a pillar can sit a day away from Bloomberg's.
  - If `engine.pnl.ledger.realise_settled` cannot be imported (e.g. mid-rewrite by the
    pnl-engine task), marks are still written and the day is reported with
    `realised: None` and a note; nothing is invented and nothing raises.

Usage (Bloomberg PC):  py -3 -m data.bloomberg.backfill [--start YYYY-MM-DD] [--end YYYY-MM-DD]
Default start is the last business day of the previous year (the YTD reference date).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter as _perf
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from data.bloomberg import fwd_curve as fc
from data.bloomberg.live import SRC_INTERP, SRC_SPOT_FWD, TIMEOUTS_BEFORE_GIVING_UP, ledger_block
from data.bloomberg.pull_marks import CLOSE_REASON, SRC_FUTURE
from engine.pnl.aggregate import _last_business_day_of_prev_year
from engine.pnl.calendar import load_holidays

NY = ZoneInfo("America/New_York")

# The one close of the app (user decision 2026-09-28: "everything closes on its day at the
# time at which its specific exchange closes"): every past mark, whatever its type or
# instrument, is Bloomberg's daily close for that date, stamped 17:00 New York of its own
# date. Defined here, not read from pull_marks, so the backfill's stamp never moves with the
# live pull's. (15:00 New York for FX from 2026-09-21 to 2026-09-28, see the module docstring.)
CLOSE_HOUR_NY = 17
# `CLOSE_REASON` (pull_marks) is the key under which a series row carries the source's own
# reason for a missing value instead of the value: an injected fetch may say why a day has
# none; Bloomberg's daily history says nothing, the day is simply absent from the series.

_SPOT_ON_DATE_SQL = """
SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.source, m.snapped_at
FROM marks_official m JOIN instruments i USING (instrument_id)
WHERE m.mark_type = 'SPOT' AND i.asset_class = 'FX' AND m.as_of_date = :day
ORDER BY m.instrument_id, m.snapped_at
"""

# What a span of past closes needs (pairs, leg dates, futures) is read from the Bloomberg
# library (data/bloomberg/library.py::needed_in_range, 2026-09-21); the range queries over
# the trades that used to live here are gone.


def business_days(start: date, end: date, holidays=None) -> List[date]:
    """Trading days from start to end inclusive: Monday-Friday less config/holidays.txt, the
    calendar the header's period dates use (`holidays=None` reads it). Until 2026-09-21 this
    was Monday-Friday only, so a US holiday (Labor Day: FX quotes, no futures settle) was
    asked of Bloomberg on every run, could never complete, and sat in the close-completeness
    list as a day with marks missing although no period is ever measured from it."""
    if holidays is None:
        holidays = load_holidays()
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


def traded_pairs(conn: sqlite3.Connection, start: date, end: date) -> List[tuple]:
    """(instrument_id, bbg_ticker) for every FX pair a historical SPOT close must be
    backfilled for over [start, end].

    2026-09-18 fix: this used to be USD pairs only (`base_ccy = 'USD' OR quote_ccy =
    'USD'`), so a cross like EURSEK -- 33 forwards in the reference book, settling
    2026-09-25..11-27 -- never got a historical SPOT close backfilled at all. Two
    consequences, both found live: (1) `engine.pnl.ledger.realise_settled` needs the
    pair's OWN official SPOT on or before settlement to freeze a settled trade, so a
    EURSEK forward could never be frozen once it settled; (2) this function also gated
    backfill()'s very first line (`if not pairs: return []`) -- a book with EURSEK/ESU6
    but no *direct* USD-pair FX trade at all short-circuited the whole backfill before
    ever reaching the future/forward logic below, so ESU6's FUTURE_PX was silently never
    attempted either, not because of any bug in the FUTURE_PX path itself.

    Now returns every FX instrument with a leg open at any point in [start, end] --
    crosses included -- via the same range query the forward-curve history uses
    (`_OPEN_FX_LEGS_RANGE_SQL`), PLUS every pair `spot_only_pair_names` lists: the
    USD-conversion pairs a cross's legs need (a cross's own USD legs' SPOT, needed for
    delta/P&L USD conversion, not just the cross's own outright) and, since 2026-09-18,
    every FX option's pair with the option's own USD-conversion pairs. A listed pair
    with no instrument row on file is skipped (`write_marks` can never persist an unknown
    instrument_id); `backfill()` creates those rows first, so that only happens on a
    connection that cannot write.

    2026-09-21: read from the Bloomberg library (data/bloomberg/library.py) -- every SPOT
    row in force on some day of the range. Same set as before, kept in one place."""
    from data.bloomberg import library
    known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    return sorted({(r["key"], r["bbg_ticker"]) for r in library.needed_in_range(conn, start.isoformat(), end.isoformat())
                   if r["kind"] == "SPOT" and r["key"] in known})


def spot_only_pair_names(conn: sqlite3.Connection, start: date, end: date) -> List[str]:
    """Plain pair names whose closing SPOT [start, end] needs although no FX leg of that
    pair need be open in it -- SPOT only, never a forward curve or a vol:

      * the USD-conversion pairs of every cross with a leg open in the range (EURSEK ->
        EURUSD, USDSEK), the range form of `live._cross_usd_legs`;
      * (2026-09-18) for every FX option open at any point in the range -- trade date to
        expiry date INCLUSIVE, realised since or not -- its own pair and its base->USD /
        quote->USD pairs (`live.option_spot_pair_names`). `traded_pairs` looked at FX legs
        only, so a pair held only through options never got a historical close. That
        matters on a missed expiry day: engine/options writes the option's payoff on a
        later pull FROM THE EXPIRY DATE'S CLOSING SPOT (the catch-up), and the ledger
        converts it to USD at the base->USD SPOT of that same date; without both closes
        an expired option stays unrealisable. The other open days' conversion closes let
        a PREMIUM written by a live pull on that day convert to USD.

    Orientation is `live._usd_pair_name` throughout (one table, in live.py). Read from
    the Bloomberg library (2026-09-21): the conversion rows and the options' own SPOT rows."""
    from data.bloomberg import library
    return sorted({r["key"] for r in library.needed_in_range(conn, start.isoformat(), end.isoformat())
                   if r["kind"] == "SPOT" and (r["role"] == library.ROLE_CONVERSION or r["product"] == "FX_OPTION")})


def _as_date(today) -> date:
    """`today` as a date: a date as is, an ISO string parsed, None = the New York book date."""
    if today is None:
        from data.bloomberg.live import book_today
        return book_today()
    return today if isinstance(today, date) else date.fromisoformat(str(today))


def close_stamp(day: date) -> str:
    """The one close stamp of the app: CLOSE_HOUR_NY:00 (17:00) America/New_York on `day`,
    Bloomberg's daily close, with that date's UTC offset resolved -- the stamp every past
    row the backfill writes carries, FX, future, listed option, LME pillar or option
    pricer mark alike (user decision 2026-09-28). Until 2026-09-28 an FX row within
    Bloomberg's intraday reach was stamped 15:00 and `today` chose between the two; there
    is one stamp now and no `today`."""
    return datetime(day.year, day.month, day.day, CLOSE_HOUR_NY, 0, tzinfo=NY).isoformat(timespec="seconds")


# The name the futures' and LME paths, and tests/test_ui_market_data.py, stamp a FUTURE_PX
# row with since 2026-09-22: the same 17:00 stamp, one function.
settle_stamp = close_stamp

# The mark types with two official-capable sources for one key (BBG_BFXFORWARD for
# Bloomberg's own outright, BBG_INTERP for a forward built or placed by this app), whose
# stale non-close rows `_write_closes` deletes when the close is written: INSERT OR REPLACE
# only replaces the same source, and a direct-quote row would go on beating the new
# interpolated close in marks_official. A FUTURE_PX has one source and needs no delete.
SPOT_FWD_MARK_TYPES = ("SPOT", "FWD_OUTRIGHT")


def is_lme_instrument(instrument_id: Optional[str]) -> bool:
    """Is `instrument_id` an LME forward's instrument, the metal's contract root id ('LME:CA';
    commodity conversion Phase 5, 2026-09-24)? A SPOT / FWD_OUTRIGHT on a contract root id is
    only ever an LME metal's: an FX pair's id never carries the 'EXCHANGE:' prefix."""
    from data.bloomberg.library import is_contract_root
    return is_contract_root(instrument_id)


def is_close_row(mark_type: str, as_of_date: str, snapped_at: str, today=None, instrument_id: str = "") -> bool:
    """Is this official row of a PAST day a close? Since 2026-09-28 one rule for every mark
    type and every instrument: a past row is a close iff it is stamped CLOSE_HOUR_NY:00
    (17:00) New York of its own as_of_date, the daily close (`close_stamp`), compared as an
    instant (the same moment written with another offset counts). Anything else -- a live
    press's row at the press's real time, or a 15:00 New York row of the FX rule retired on
    2026-09-28 -- is not a close, and the backfill asks for that day's daily close and
    replaces it. An unreadable date or stamp is not a close.

    `today` and `instrument_id` are kept for the callers' signature (inventory passes
    both: `today` chose the 15:00-or-17:00 stamp and `instrument_id` told an LME row from an
    FX row until 2026-09-28); neither changes the answer now. Today's own rows are live
    and never reach this: the callers ask only about past days."""
    try:
        day = date.fromisoformat(as_of_date)
        stamp = datetime.fromisoformat(snapped_at)
    except (TypeError, ValueError):
        return False
    return stamp == datetime(day.year, day.month, day.day, CLOSE_HOUR_NY, 0, tzinfo=NY)


def rates_on_date(conn: sqlite3.Connection, day: str) -> Dict[str, dict]:
    """currency -> rate dict from the official SPOT marks dated exactly `day` (unlike
    live.rates_from_marks, which takes the latest mark of any date). Never stale."""
    out: Dict[str, dict] = {}
    for pair, base, quote, value, source, snapped in conn.execute(_SPOT_ON_DATE_SQL, {"day": day}):
        if "USD" not in (base, quote):
            continue
        ccy = quote if base == "USD" else base
        out[ccy] = {"rate": float(value), "inverted": base == "USD", "source": source,
                    "timestamp": snapped, "stale": False, "as_of_date": day, "pair": pair}
    return out


# --------------------------------------------------------------------------- fault isolation (2026-09-29)
# Phase G, "Smooth and contained" (user, 2026-09-29: "if something pulls badly it doesnt crash
# everything - but of course most important is to make sure that everything pulls
# correctly"), the backfill's half of what live.pull_once does for today's marks:
#   - every history request goes through one `_Asks` per backfill() call: the tickers of a
#     kind are asked in chunks of at most HISTORY_CHUNK (a key's tickers, a pair's smile or
#     tenors, a currency's OIS curve, kept in one request), a chunk that raises fails alone and,
#     unless it timed out, is asked again one ticker at a time; after
#     live.TIMEOUTS_BEFORE_GIVING_UP timeouts in a row nothing more is sent and every ticker
#     left is "not asked", with that reason. What is asked does not change (hard rule 8): the
#     same tickers and fields the library lists, in more requests when a book is large.
#   - the write lock is released (committed) before every request, so no write lock is held
#     while Bloomberg is waited on;
#   - a value that is not a number (Bloomberg's 'N.A.', NaN, infinity) is never written (hard
#     rule 2): it is counted with its reason, and the mark it would have made is missing with
#     that reason;
#   - each stage of the run and each step of a day (forwards, inputs, options, ledger) is
#     guarded on its own: a raise is recorded with a plain reason (`_Asks.errors`, published as
#     the status block's "errors") and the rest runs.
HISTORY_CHUNK = 50          # tickers per history request; a pair's whole smile (45) stays in one
REQUEST_FAILED = "history request failed"
NOT_ASKED = "not asked"
MAX_STATUS_ERRORS = 20      # entries of the status block's "errors" and "not_numbers" lists

# The history kinds, in plain words for the reasons and the progress line.
ASK_LABELS = {"spot": "FX closes", "fwd": "forward-curve tenors", "future": "futures and option closes",
              "lme": "LME curves", "vol": "vol smiles", "ois": "OIS curves", "scale": "forward-points divisors",
              # the run's own steps (a raise in one is recorded and the rest runs)
              "session": "the Bloomberg session", "plan": "the list of days to backfill",
              "inputs_plan": "the smiles and curves each day lacks", "closes": "saving the closes",
              "forwards": "the day's forward curves", "inputs": "the day's vol smiles and OIS curves",
              "options": "the day's option pricing", "ledger": "the ledger", "closing": "the closing ledger step",
              "bookkeeping": "the backfill's status bookkeeping",
              # the risk history step (2026-09-30): its own report, status["backfill"]["risk_history"]
              "risk_history": "risk history (settlements, volume, open interest)"}


# bbg-curves' wording and test for a value that is not a number (fwd_curve, 2026-09-29), so a
# reason reads the same whichever step refused the value.
NOT_A_NUMBER = getattr(fc, "NOT_A_NUMBER", "Bloomberg sent a value that is not a number")

# The steps of the status block's "cache" (2026-09-30), each history kind under one: what a
# press asked of Bloomberg, and what came back empty, step by step (`_cache_block`).
CACHE_STEPS = {"spot": "fx_closes", "fwd": "fx_forwards", "scale": "points_scale", "future": "futures",
               "lme": "lme", "vol": "vol", "ois": "ois"}


def _number(value) -> Optional[float]:
    """`value` as a finite float, else None: Bloomberg's 'N.A.', an empty string, NaN,
    infinity, a bool or None is not a number and never becomes a mark (hard rule 2).
    `fwd_curve.finite_number` when bbg-curves has it."""
    helper = getattr(fc, "finite_number", None)
    if helper is not None:
        return helper(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _not_a_number(what: str, value) -> str:
    return f"{what}: {NOT_A_NUMBER} ({repr(value)[:40]}); not written"


def _is_timeout(exc: BaseException) -> bool:
    """A request Bloomberg did not answer in time (pull_marks.BloombergRequestError with
    classification TIMEOUT), the one failure that is not retried ticker by ticker."""
    return getattr(exc, "classification", None) == "TIMEOUT"


def _plain_error(exc: BaseException) -> str:
    if _is_timeout(exc):
        return "Bloomberg did not answer in time"
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= 300 else text[:297] + "..."


def _release_lock(conn: Optional[sqlite3.Connection]) -> None:
    """Commit whatever this connection has written, so no write lock is held across a
    Bloomberg request (a request can wait pull_marks.EVENT_TIMEOUT_MS; an upload waiting on
    the lock gives up after schema.BUSY_TIMEOUT_SECONDS). Never raises."""
    try:
        if conn is not None and conn.in_transaction:
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


def _chunks(tickers: Iterable[str], key_of: Dict[str, str]) -> List[List[str]]:
    """`tickers` as request chunks: the tickers of one key (`key_of`, a ticker's pair,
    currency, metal or instrument) always together, keys packed in order up to HISTORY_CHUNK
    tickers a chunk (a key larger than that is a chunk of its own), each chunk sorted."""
    groups: Dict[str, List[str]] = {}
    for t in sorted(set(tickers)):
        groups.setdefault(key_of.get(t, t), []).append(t)
    chunks: List[List[str]] = []
    for group in groups.values():
        if chunks and len(chunks[-1]) + len(group) <= HISTORY_CHUNK:
            chunks[-1].extend(group)
        else:
            chunks.append(list(group))
    return [sorted(c) for c in chunks]


class _Problem(str):
    """A log line that reports a problem: a day ERROR or NO_CLOSES, a request or step that
    failed, a stopped run, the ledger's re-freeze or unrealisable news, days that cannot be
    completed yet. Printed like any other line on the command-line path; the only lines the
    in-app path's `_QuietLog` lets through (2026-09-30)."""


def _problem(message: str) -> str:
    """`message` marked as a problem line (see `_Problem`); still a plain string to any log."""
    return _Problem(message)


class _QuietLog:
    """The in-app backfill's log (2026-09-30, user: the terminal was cluttered after every
    "Pull Bloomberg now"): the problem lines (`_Problem`) go to `emit` as they are, every
    other line (the per-day DONE lines, the range header, the points divisors, "realised
    after the last day: 0") goes nowhere, since the status file and the Data tab hold them.
    `start_auto_backfill` prints one summary line at the end (`_summary_line`). Never raises."""

    def __init__(self, emit: Callable[[str], None] = print):
        self.emit = emit

    def __call__(self, message) -> None:
        if isinstance(message, _Problem):
            try:
                self.emit(str(message))
            except Exception:  # noqa: BLE001 -- the terminal is a courtesy, never a failure of the run
                pass


def _summary_line(results: List[dict], report: dict, days_block: Optional[dict] = None, crashed: str = "") -> str:
    """The one line the in-app backfill prints when it finishes (2026-09-30): the past days
    filled and their range, the history requests sent and failed, the values refused for not
    being numbers, and the marks still missing on the latest day worked (from the status
    block's "days", else that day's own result), the clauses with nothing to say left out."""
    if crashed:
        return f"Bloomberg history: {crashed}; see the Data tab."
    req = report.get("requests") or {}
    sent, failed = int(req.get("sent") or 0), int(req.get("failed") or 0)
    asked = (f"{sent} request{'' if sent == 1 else 's'}, {failed} failed" if sent
             else "nothing sent to Bloomberg")
    worked = [r for r in results if r.get("status") != "SKIPPED"]
    if not worked:
        line = f"Bloomberg history: no past day to fill, {asked}"
    else:
        days = sorted(r["day"] for r in worked)
        done = sum(1 for r in worked if r.get("status") == "DONE")
        span = days[0] if days[0] == days[-1] else f"{days[0]} to {days[-1]}"
        count = (f"{done} past day{'' if done == 1 else 's'}" if done == len(worked)
                 else f"{done} of {len(worked)} past days")
        line = f"Bloomberg history: {count} filled ({span}), {asked}"
        latest = days[-1]
        if days_block is not None:
            missing = int((days_block.get(latest) or {}).get("missing_count") or 0)
        else:
            last = next(r for r in worked if r["day"] == latest)
            missing = len(last.get("missing_pairs") or []) + len(last.get("missing_marks") or [])
        if missing:
            line += f"; {missing} mark{'' if missing == 1 else 's'} still missing on the latest day: see the Data tab"
    refused = int(report.get("not_number_count") or 0)
    if refused:
        line += f"; {refused} value{'' if refused == 1 else 's'} not a number, left out"
    return line + "."


class _Asks:
    """Every Bloomberg history request of one backfill() call, and what went wrong in the run.

    `series(kind, fetch, ...)` asks `fetch(session, service, tickers, fields, start, end)` in
    chunks and joins the answers; `reason(kind, key, day)` is why a key has nothing that day
    when its request failed or was not sent ('' otherwise); `errors` lists the run's failures
    ({step, label, day, reason}), `not_numbers` the values that were not numbers ({what, day,
    reason}); `summary()` is the status block's "requests". Never raises."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None, log: Callable[[str], None] = lambda _m: None):
        self.conn = conn
        self.log = log
        self.requests = self.answered = self.failed_requests = 0
        self.tickers_failed = self.tickers_not_asked = 0
        self.timeouts_in_row = 0
        self.no_session = ""                  # why there is no Bloomberg session, when there is none
        self.failures: Dict[Tuple[str, str], List[Tuple[date, date, str]]] = {}
        self.stage_errors: Dict[str, str] = {}
        self.errors: List[dict] = []
        self.error_count = 0
        self.not_numbers: List[dict] = []
        self.not_number_count = 0
        # What was asked and what came back empty, per kind (2026-09-30, user: "bloomberg needs
        # to store on cache all the data - and only pull any new data"): the tickers sent in an
        # answered request, the ones that came back with something, and the ones with nothing
        # ({ticker: reason}); published as the status block's "cache" (`_cache_block`).
        self.asked: Dict[str, set] = {}
        self.had_data: Dict[str, set] = {}
        self.came_back_empty: Dict[str, Dict[str, str]] = {}

    def note_answer(self, kind: str, ticker: str, has_data: bool, reason: str = "") -> None:
        """Count one ticker of an answered request: asked, and with or without data."""
        self.asked.setdefault(kind, set()).add(ticker)
        if has_data:
            self.had_data.setdefault(kind, set()).add(ticker)
        else:
            self.came_back_empty.setdefault(kind, {}).setdefault(ticker, reason or f"Bloomberg returned nothing "
                                                                               f"for {ticker}")

    def empties(self, kind: str) -> Dict[str, str]:
        """{ticker: reason} of `kind` asked this run that brought nothing back in any request."""
        had = self.had_data.get(kind) or set()
        return {t: why for t, why in (self.came_back_empty.get(kind) or {}).items() if t not in had}

    @property
    def gave_up(self) -> bool:
        return self.timeouts_in_row >= TIMEOUTS_BEFORE_GIVING_UP

    def stop_reason(self) -> str:
        """Why nothing more is sent ('' while requests still go out)."""
        if self.no_session:
            return f"{NOT_ASKED}: no Bloomberg session ({self.no_session})"
        if self.gave_up:
            return (f"{NOT_ASKED}: Bloomberg did not answer the last {TIMEOUTS_BEFORE_GIVING_UP} history requests "
                    f"of this backfill")
        return ""

    def error(self, step: str, reason: str, day: str = "") -> str:
        """Record one failure of the run (a stage, a day's step, a request)."""
        self.error_count += 1
        if len(self.errors) < MAX_STATUS_ERRORS:
            self.errors.append({"step": step, "label": ASK_LABELS.get(step, step), "day": day, "reason": reason})
        try:
            self.log(_problem(f"  {day + '  ' if day else ''}{ASK_LABELS.get(step, step)}: {reason}"))
        except Exception:  # noqa: BLE001
            pass
        return reason

    def not_number(self, what: str, day: str, value) -> str:
        return self.refused(what, day, _not_a_number(what, value))

    def refused(self, what: str, day: str, reason: str) -> str:
        """Count a value refused for not being a number, with its sentence."""
        self.not_number_count += 1
        if len(self.not_numbers) < MAX_STATUS_ERRORS:
            self.not_numbers.append({"what": what, "day": day, "reason": reason})
        return reason

    def raised(self, exc: BaseException) -> None:
        """Count a request that raised (a timeout adds to the timeouts in a row)."""
        self.failed_requests += 1
        self.timeouts_in_row = self.timeouts_in_row + 1 if _is_timeout(exc) else 0

    def _fail(self, kind: str, keys: Iterable[str], start: date, end: date, reason: str) -> None:
        for key in keys:
            self.failures.setdefault((kind, key), []).append((start, end, reason))

    def reason(self, kind: str, key: str, day: date) -> str:
        for first, last, why in self.failures.get((kind, key), ()):
            if first <= day <= last:
                return why
        return self.stage_errors.get(kind, "")

    def series(self, kind: str, fetch: Callable, session, service, tickers: Iterable[str], fields: List[str],
               start: date, end: date, key_of: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
        """{ticker: {date_iso: {field: value}}} of `tickers` over [start, end], asked in chunks
        (`_chunks`); a failure is recorded under (kind, key_of[ticker]) for the stretch."""
        key_of = key_of or {}
        out: Dict[str, dict] = {}
        for chunk in _chunks(tickers, key_of):
            self._one(kind, fetch, session, service, chunk, list(fields), start, end, key_of, out, may_split=True)
        return out

    def _one(self, kind, fetch, session, service, chunk, fields, start, end, key_of, out, may_split) -> None:
        keys = list(dict.fromkeys(key_of.get(t, t) for t in chunk))
        stop = self.stop_reason()
        if stop:
            self.tickers_not_asked += len(chunk)
            self._fail(kind, keys, start, end, stop)
            return
        _release_lock(self.conn)              # never a write lock across a request
        self.requests += 1
        try:
            got = fetch(session, service, list(chunk), list(fields), start, end) or {}
        except Exception as exc:  # noqa: BLE001 -- this chunk fails alone
            self.raised(exc)
            if may_split and len(chunk) > 1 and not _is_timeout(exc):
                for t in chunk:               # one ticker that breaks a request leaves the others standing
                    self._one(kind, fetch, session, service, [t], fields, start, end, key_of, out, may_split=False)
                return
            self.tickers_failed += len(chunk)
            what = ", ".join(chunk[:3]) + (f" and {len(chunk) - 3} more" if len(chunk) > 3 else "")
            reason = f"{REQUEST_FAILED} for {what} ({start}..{end}): {_plain_error(exc)}"
            self._fail(kind, keys, start, end, reason)
            self.error(kind, reason)
            return
        self.answered += 1
        self.timeouts_in_row = 0
        if isinstance(got, dict):
            for ticker, per_day in got.items():
                if isinstance(per_day, dict):
                    out.setdefault(ticker, {}).update(per_day)
        for t in chunk:
            per_day = got.get(t) if isinstance(got, dict) else None
            self.note_answer(kind, t, isinstance(per_day, dict) and bool(per_day),
                             f"Bloomberg returned no history for {t} ({start}..{end})")

    def stage(self, kind: str, exc: BaseException, day: str = "") -> str:
        """A whole stage raised (outside a request): recorded, and its reason stands for every
        key of that kind with no other."""
        reason = f"{ASK_LABELS.get(kind, kind)} stopped: {_plain_error(exc)}"
        self.stage_errors.setdefault(kind, reason)
        return self.error(kind, reason, day)

    def summary(self) -> dict:
        return {"sent": self.requests, "answered": self.answered, "failed": self.failed_requests,
                "tickers_failed": self.tickers_failed, "tickers_not_asked": self.tickers_not_asked,
                "gave_up": self.gave_up, "no_session": self.no_session,
                "tickers_asked": sum(len(v) for v in self.asked.values())}

    def by_step(self) -> Dict[str, dict]:
        """{cache step: {asked, empty, empty_tickers}} of this run's answered requests
        (CACHE_STEPS), for the status block's "cache"."""
        out: Dict[str, dict] = {}
        for kind, step in CACHE_STEPS.items():
            asked = self.asked.get(kind) or set()
            if not asked:
                continue
            empty = self.empties(kind)
            entry = out.setdefault(step, {"asked": 0, "empty": 0, "empty_tickers": []})
            entry["asked"] += len(asked)
            entry["empty"] += len(empty)
            entry["empty_tickers"] = (entry["empty_tickers"] + sorted(empty))[:MAX_STATUS_ERRORS]
        return out


def _import_realise_settled():
    """engine.pnl.ledger.realise_settled if importable right now, else None. Guarded per
    BUILD_PLAN.md Task B: this module must not depend on the rest of engine.pnl."""
    try:
        from engine.pnl.ledger import realise_settled
        return realise_settled
    except ImportError:
        return None


def _import_price_close():
    """engine.options.store.price_close if importable right now, else None (2026-09-22).
    The same guard as `_import_realise_settled`: engine/options is another agent's, and a
    module mid-rewrite (a missing name, or a file that does not even parse) must leave the
    backfill writing its marks -- hence any exception, not ImportError alone."""
    try:
        from engine.options.store import price_close
        return price_close
    except Exception:  # noqa: BLE001 -- see docstring: never let the pricer's state stop the marks
        return None


# An FX option is open from its trade date to its expiry; an option on a commodity future
# (CMDTY_OPTION, Phase 5, 2026-09-24) from its trade date to its NOTIONAL leg's settle date
# (its expiry as booked), the days options-store's price_close prices its Greeks for.
_OPTIONS_OPEN_SQL = """
SELECT 1 FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.trade_date <= :day AND (
      (t.product = 'FX_OPTION' AND i.expiry_date >= :day)
   OR (t.product = 'CMDTY_OPTION' AND EXISTS (
          SELECT 1 FROM trade_legs l
          WHERE l.trade_id = t.trade_id AND l.leg_type = 'NOTIONAL' AND l.settle_date >= :day)))
LIMIT 1
"""


def _options_open_on(conn: sqlite3.Connection, day: str) -> bool:
    """Is any option open on `day`, the days price_close prices: an FX_OPTION with
    trade_date <= day <= expiry, or (2026-09-24) a CMDTY_OPTION with trade_date <= day <= its
    NOTIONAL leg's settle date? A book with no option pays nothing for the options step."""
    return conn.execute(_OPTIONS_OPEN_SQL, {"day": day}).fetchone() is not None


PRICE_CLOSE_UNAVAILABLE = "options not priced: engine.options.store.price_close is not importable"


def _price_options_close(conn: sqlite3.Connection, day: str, price_close
                         ) -> Tuple[Optional[int], List[dict], str, List[str], Optional[int]]:
    """Price the options open on `day` from that day's own inputs on file, after its closes
    are written (the FX closes, and pass 1's FUTURE_PX of an option on a future and of its
    underlying future) and before the ledger's freeze (which reads the expiry day's
    premium). Returns (options_priced, options_skipped, options_note, options_closed_out,
    futures_options_priced):
      - no option open that day: (None, [], '', [], None) and `price_close` is never called;
      - `price_close` is None (not importable): (None, [], PRICE_CLOSE_UNAVAILABLE, [], None);
      - `price_close(conn, day)` raised: (None, [], 'price_close raised: ...', [], None);
      - otherwise its own {'priced': int, 'skipped': [{'trade_id', 'reason'}], 'closed_out':
        [trade_id, ...], 'futures_options_priced': int, 'error'?} as (priced, skipped, error
        or '', closed_out, futures_options_priced). `priced` counts both products;
        `futures_options_priced` (2026-09-24) the CMDTY_OPTION trades among them, None when
        the pricer does not say.
    `closed_out` (2026-09-22) are the trades of an option closed out as of `day` (bought and
    sold back; CLAUDE.md "A closed-out option is not live"): not priced, no mark, and never
    counted among the skipped -- the pricer lists them under their own head and so does
    this. Nothing here asks Bloomberg for anything, and nothing raises out of it."""
    if not _options_open_on(conn, day):
        return None, [], "", [], None
    if price_close is None:
        return None, [], PRICE_CLOSE_UNAVAILABLE, [], None
    try:
        out = price_close(conn, day) or {}
    except Exception as exc:  # noqa: BLE001 -- another agent's pricer: report it, keep the day's marks
        return None, [], f"price_close raised: {exc!r}", [], None
    counts = []
    for key in ("priced", "futures_options_priced"):
        try:
            counts.append(int(out.get(key)))
        except (TypeError, ValueError):
            counts.append(None)
    skipped = [dict(item) for item in (out.get("skipped") or [])]
    closed_out = [str(t) for t in (out.get("closed_out") or [])]
    return counts[0], skipped, str(out.get("error") or ""), closed_out, counts[1]


def _lacking_inputs(conn: sqlite3.Connection, work: List[date]) -> Dict[str, Dict[str, set]]:
    """{'VOL_SMILE': {day_iso: {pair, ...}}, 'OIS_CURVE': {day_iso: {ccy, ...}}}: the smiles
    and curves each day being worked needs and does not hold (inventory.inputs_missing).
    Worked out once before the history is asked for, so a request covers only the pairs
    and currencies some day of its stretch lacks."""
    from data.bloomberg.inventory import inputs_missing
    out: Dict[str, Dict[str, set]] = {"VOL_SMILE": {}, "OIS_CURVE": {}}
    for d in work:
        for item in inputs_missing(conn, d.isoformat()):
            out[item["kind"]].setdefault(d.isoformat(), set()).add(item["key"])
    return out


def _keys_in_run(lacking: Dict[str, set], run_start: date, run_end: date) -> List[str]:
    return sorted({key for day_iso, keys in lacking.items()
                   if run_start <= date.fromisoformat(day_iso) <= run_end for key in keys})


def _fetch_vol_history(session, service, runs: List[Tuple[date, date]], quote_fetch: Callable,
                       lacking: Dict[str, set], asks: Optional[_Asks] = None) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{pair: {date_iso: {ticker: PX_LAST}}} -- `quote_fetch` (a daily HistoricalDataRequest,
    PX_LAST) per stretch of `runs`, in chunks (`asks`, a pair's smile in one request), for
    every vol ticker of every pair some day of the stretch lacks a smile for (`lacking`:
    {day_iso: {pair}}): the same tickers the live vol step asks for today
    (vol_marketdata.vol_ticker over VOL_TENORS x VOL_QUOTE_TYPES). A stretch with nothing
    lacking asks for nothing. The values are kept as sent; `_write_day_inputs` refuses one
    that is not a number."""
    from data.bloomberg import vol_marketdata as vm
    asks = asks or _Asks()
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for run_start, run_end in runs:
        pairs = _keys_in_run(lacking, run_start, run_end)
        if not pairs:
            continue
        ticker_to_pair = {vm.vol_ticker(pair, tenor, quote_type): pair
                          for pair in pairs for tenor in vm.VOL_TENORS for quote_type in vm.VOL_QUOTE_TYPES}
        series = asks.series("vol", quote_fetch, session, service, ticker_to_pair, [vm.VOL_FIELD], run_start, run_end,
                             key_of=ticker_to_pair)
        for ticker, per_day in series.items():
            pair = ticker_to_pair.get(ticker)
            if pair is None:
                continue
            for day_iso, row in (per_day or {}).items():
                if isinstance(row, dict) and row.get(vm.VOL_FIELD) is not None:
                    out.setdefault(pair, {}).setdefault(day_iso, {})[ticker] = row[vm.VOL_FIELD]
    return out


def _fetch_ois_history(session, service, runs: List[Tuple[date, date]], quote_fetch: Callable,
                       lacking: Dict[str, set], asks: Optional[_Asks] = None) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{ccy: {date_iso: {ticker: value}}} -- `quote_fetch` per stretch of `runs`, in chunks
    (`asks`, a currency's curve in one request), for every OIS ticker of every currency some
    day of the stretch lacks a curve for (`lacking`: {day_iso: {ccy}}): the same tickers the
    live rates step asks for today (rates_marketdata.ois_curve). Every field the specs name
    is asked for in the one request (PX_LAST throughout the Phase 1 map)."""
    from data.bloomberg import rates_marketdata as rm
    asks = asks or _Asks()
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for run_start, run_end in runs:
        ccys = _keys_in_run(lacking, run_start, run_end)
        if not ccys:
            continue
        ticker_to_ccy, fields = {}, set()
        for ccy in ccys:
            for spec in rm.ois_curve(ccy):
                ticker_to_ccy[spec.ticker] = (ccy, spec.field)
                fields.add(spec.field)
        series = asks.series("ois", quote_fetch, session, service, ticker_to_ccy, sorted(fields), run_start, run_end,
                             key_of={t: hit[0] for t, hit in ticker_to_ccy.items()})
        for ticker, per_day in series.items():
            hit = ticker_to_ccy.get(ticker)
            if hit is None:
                continue
            ccy, field = hit
            for day_iso, row in (per_day or {}).items():
                if isinstance(row, dict) and row.get(field) is not None:
                    out.setdefault(ccy, {}).setdefault(day_iso, {})[ticker] = row[field]
    return out


def _write_day_inputs(conn: sqlite3.Connection, d: date, lacking: Dict[str, Dict[str, set]],
                      vol_history: Dict[str, Dict[str, Dict[str, float]]],
                      ois_history: Dict[str, Dict[str, Dict[str, float]]],
                      asks: Optional[_Asks] = None) -> Tuple[int, int, List[dict]]:
    """Write `d`'s smiles and curves from the fetched history, for the pairs and currencies
    the day lacked: vol_quotes rows through vol_marketdata.write_vol_quotes (the live vol
    step's writer; source BBG_BDH, the history's), curve_quotes rows through
    rates_marketdata.write_curve_quotes (the live rates step's; BBG_BDH; values scaled from
    per cent as it scales them). A smile is written with whatever quotes came back, as the
    live step writes what Bloomberg answers; a curve only with at least
    rates_marketdata._MIN_QUOTES quotes, the live source's own floor, else nothing. Returns
    (vol rows written, curve rows written, missing_inputs: [{kind, key, reason}] for a pair
    or currency the history had nothing usable for). A quote that is not a number is left out
    and counted (`asks.not_number`); a pair or currency whose request failed or was not sent
    says so (`asks.reason`)."""
    from data.bloomberg import rates_marketdata as rm
    from data.bloomberg import vol_marketdata as vm
    import decimal
    asks = asks or _Asks()
    day = d.isoformat()
    vol_rows = curve_rows = 0
    missing: List[dict] = []
    for pair in sorted(lacking["VOL_SMILE"].get(day, ())):
        values = (vol_history.get(pair) or {}).get(day) or {}
        quotes, refused = [], []
        for tenor in vm.VOL_TENORS:
            for quote_type in vm.VOL_QUOTE_TYPES:
                ticker = vm.vol_ticker(pair, tenor, quote_type)
                if ticker not in values:
                    continue
                value = _number(values[ticker])
                if value is None:
                    refused.append(asks.not_number(f"{vm.VOL_FIELD} of {ticker} on {day}", day, values[ticker]))
                    continue
                quotes.append(vm.VolQuote(tenor=tenor, quote_type=quote_type, ticker=ticker, value=value,
                                          field=vm.VOL_FIELD, source="BBG"))
        if not quotes:
            missing.append({"kind": "VOL_SMILE", "key": pair,
                            "reason": asks.reason("vol", pair, d) or (refused[0] if refused else "")
                            or f"Bloomberg returned no vol quotes ({vm.VOL_FIELD}) for {pair} on {day}"})
            continue
        refused_rows: List[dict] = []
        vol_rows += _write_inputs(vm.write_vol_quotes, conn, {pair: vm.PairVolSnapshot(pair=pair, as_of=d, quotes=quotes)},
                                  day, refused_rows)
        for r in refused_rows:                  # the writer's own last guard (bbg-curves, 2026-09-29)
            what = f"{vm.VOL_FIELD} of {r.get('ticker')} on {day}"
            asks.refused(what, day, f"{what}: {r.get('reason')}")
    for ccy in sorted(lacking["OIS_CURVE"].get(day, ())):
        values = (ois_history.get(ccy) or {}).get(day) or {}
        quotes, failed = [], []
        for spec in rm.ois_curve(ccy):
            raw = values.get(spec.ticker)
            number = _number(raw)
            if number is None:
                if raw is not None:
                    asks.not_number(f"{spec.field} of {spec.ticker} on {day}", day, raw)
                failed.append(spec.ticker)
                continue
            try:
                value = rm.scale_quote(decimal.Decimal(str(number)))
            except (TypeError, ValueError, decimal.InvalidOperation):
                failed.append(spec.ticker)
                continue
            quotes.append(rm.CurveQuote(tenor=spec.tenor, ticker=spec.ticker, value=value, field=spec.field))
        if len(quotes) < rm._MIN_QUOTES:
            missing.append({"kind": "OIS_CURVE", "key": ccy,
                            "reason": asks.reason("ois", ccy, d)
                            or f"fewer than {rm._MIN_QUOTES} OIS quotes for {ccy} on {day}: Bloomberg returned "
                               f"no value for {', '.join(failed)}"})
            continue
        quotes.sort(key=lambda q: rm.tenor_to_days(q.tenor))
        snap = rm.CurveSnapshot(currency=ccy, index=rm.OIS_INDEX[ccy], as_of=d, quotes=quotes)
        refused_rows = []
        curve_rows += _write_inputs(rm.write_curve_quotes, conn, snap, day, refused_rows)
        for r in refused_rows:
            what = f"OIS quote {r.get('ticker')} on {day}"
            asks.refused(what, day, f"{what}: {r.get('reason')}")
    return vol_rows, curve_rows, missing


def _write_inputs(writer: Callable, conn: sqlite3.Connection, what, day: str, rejected: List[dict]) -> int:
    """`writer(conn, what, day, source=BBG_BDH, rejected=rejected)`, bbg-curves' vol / OIS
    writer; without `rejected` on a checkout whose writer does not take it yet."""
    try:
        return writer(conn, what, day, source=SRC_FUTURE, rejected=rejected)
    except TypeError as exc:
        if "rejected" not in str(exc):
            raise
        return writer(conn, what, day, source=SRC_FUTURE)


# Only what the days being worked need is asked of Bloomberg's history (user decision
# 2026-09-21: "only the data necessary for the pnl calcs of the trades ... also for the
# backfill"). Until then every ticker was requested for every day between the oldest and
# the newest day being worked -- one old day that could never complete made each run
# re-request months of history for the whole book -- and every pair got all eight tenors.
RUN_MAX_DAYS = 22          # business days per request, so each stretch asks only for the tickers it needs
TENOR_MARGIN_DAYS = 7      # a kept tenor's computed date clears the furthest leg by this much (see _tenors_needed)
MIN_TENORS = 4             # SP..1M are always asked for (see _tenors_needed)


def _runs(work: List[date]) -> List[Tuple[date, date]]:
    """The days to work as (first, last) stretches of consecutive business days, each at
    most RUN_MAX_DAYS long. One request per kind per stretch: days that are not being
    worked are never asked for, and a stretch asks only for the tickers needed inside it
    (a pair first traded in September is not asked for in June)."""
    runs: List[List[date]] = []
    for d in sorted(set(work)):
        if runs and len(runs[-1]) < RUN_MAX_DAYS and business_days(runs[-1][-1], d) == [runs[-1][-1], d]:
            runs[-1].append(d)
        else:
            runs.append([d])
    return [(run[0], run[-1]) for run in runs]


def _tenors_needed(pair: str, legs: List[dict], run_start: date, holidays) -> List[str]:
    """The standard tenors `pair`'s open legs (`legs`: its FWD_OUTRIGHT library rows) need
    over a stretch starting `run_start`: SP up to the first tenor whose date clears the
    furthest leg, seen from the first day the leg is needed (later days see it nearer).
    A leg's forward is read between the two tenors either side of its date
    (fwd_curve.outright_for_date), so the longer tenors change nothing and are not asked
    for. Two safeguards, both on the side of asking for more: the kept tenor must clear
    the leg by TENOR_MARGIN_DAYS, because its date is computed by convention and can sit
    a day from Bloomberg's; and SP..1M are always kept, because fwd_curve.tenor_unit tells
    points from outrights by the spread of the values it is given. A leg beyond the last
    tenor keeps them all (it is reported "outside the forward tenors", as before)."""
    from data.bloomberg.pull_marks import STANDARD_TENORS
    keep = MIN_TENORS
    for leg in legs:
        first_day = max(run_start, date.fromisoformat(leg["needed_from"]))
        clear = date.fromisoformat(leg["settle_date"]) + timedelta(days=TENOR_MARGIN_DAYS)
        spot_day = fc.spot_date_for(first_day, pair, holidays)
        reach = len(STANDARD_TENORS)
        for i, tenor in enumerate(STANDARD_TENORS):
            settle = fc.tenor_settle_date(spot_day, tenor, holidays)
            if settle is not None and settle >= clear:
                reach = i + 1
                break
        keep = max(keep, reach)
    return list(STANDARD_TENORS[:keep])


def _tenor_tickers(pair: str, tenors: List[str]) -> Dict[str, str]:
    """{tenor label -> bbg_ticker} for `pair` in Bloomberg's history (pull_marks.tenor_ticker,
    '<pair><tenor> Curncy'). A tenor the naming has no ticker for is left out and never
    asked for."""
    from data.bloomberg.pull_marks import tenor_ticker
    return {t: ticker for t in tenors if (ticker := tenor_ticker(pair, t))}


def _fetch_fwd_outright_history(conn: sqlite3.Connection, session, service, runs: List[Tuple[date, date]],
                                fwd_fetch: Optional[Callable] = None, holidays=frozenset(),
                                fields: Optional[List[str]] = None,
                                asks: Optional[_Asks] = None,
                                wanted: Optional[Dict[str, set]] = None) -> Dict[str, Dict[str, Dict[str, dict]]]:
    """{instrument_id: {date_iso: {tenor label: {'PX_LAST': ..., 'SETTLE_DT': ... if sent}}}}
    -- one call of `fwd_fetch` per stretch of `runs`, for the pairs with a leg still to
    settle inside it (the Bloomberg library's FWD_OUTRIGHT rows; a leg settling on or
    before a day is marked at that day's spot and needs no tenor) and the tenors those
    legs need (`_tenors_needed`). The raw rows, not a curve: a day's curve needs that
    day's SPOT close (fwd_curve.historical_curve), which backfill() only has inside its
    day loop. `fwd_fetch(session, service, tickers, fields, start, end) -> {ticker:
    {date_iso: {field: value}}}` defaults to pull_marks.fetch_historical_series, Bloomberg's
    daily history (the daily close since 2026-09-28; the 15:00 intraday bar from 2026-09-21
    until then), which is asked for `fields` = ['PX_LAST'] alone.

    An injected `fwd_fetch` is still asked for SETTLE_DT too, so Bloomberg's own tenor
    dates are used wherever a source does send them (a daily HistoricalDataRequest does
    not: it is a static reference field); if a request comes back with no PX_LAST at all
    it is sent once more for PX_LAST alone, and the later stretches ask for PX_LAST alone.

    In chunks (`asks`, a pair's tenors in one request); a tenor whose PX_LAST is not a number
    is left out of that day (counted, `asks.not_number`), so no curve is built on it.

    `wanted` ({day_iso: {pair}}, 2026-09-30, "only pull any new data"): the pairs with a
    forward still missing at the close on some day of the stretch; a pair whose forwards are
    all on file is not asked. None asks every pair (an overwrite run)."""
    from data.bloomberg import library
    asks = asks or _Asks()
    out: Dict[str, Dict[str, Dict[str, dict]]] = {}
    fields = list(fields) if fields else ["PX_LAST", "SETTLE_DT"]
    for run_start, run_end in runs:
        keep = None if wanted is None else set(_keys_in_run(wanted, run_start, run_end))
        legs_by_pair: Dict[str, List[dict]] = {}
        for r in library.needed_in_range(conn, run_start.isoformat(), run_end.isoformat()):
            if (r["kind"] == "FWD_OUTRIGHT" and r["settle_date"] > run_start.isoformat()
                    and (keep is None or r["key"] in keep)):
                legs_by_pair.setdefault(r["key"], []).append(r)
        if not legs_by_pair:
            continue
        tenor_map = {pair: _tenor_tickers(pair, _tenors_needed(pair, legs, run_start, holidays))
                     for pair, legs in sorted(legs_by_pair.items())}
        key_of = {t: pair for pair, by_tenor in tenor_map.items() for t in by_tenor.values()}
        if fwd_fetch is None:
            from data.bloomberg.pull_marks import fetch_historical_series
            fwd_fetch, fields = fetch_historical_series, ["PX_LAST"]
        series = asks.series("fwd", fwd_fetch, session, service, key_of, fields, run_start, run_end, key_of=key_of)
        if len(fields) > 1 and not any("PX_LAST" in row for per_day in series.values() for row in per_day.values()):
            fields = ["PX_LAST"]
            series = asks.series("fwd", fwd_fetch, session, service, key_of, fields, run_start, run_end, key_of=key_of)
        for pair, by_tenor in tenor_map.items():
            by_day = out.setdefault(pair, {})
            for tenor, ticker in by_tenor.items():
                for day_iso, row in (series.get(ticker) or {}).items():
                    if isinstance(row, dict) and "PX_LAST" in row and _number(row["PX_LAST"]) is None:
                        # left out here, before any curve or divisor inference reads it; a day whose
                        # every tenor was refused says so (pass 2 reads "fwd_refused")
                        why = asks.not_number(f"PX_LAST of {ticker} on {day_iso}", day_iso, row["PX_LAST"])
                        on = date.fromisoformat(day_iso)
                        asks._fail("fwd_refused", [pair], on, on, why)
                        continue
                    by_day.setdefault(day_iso, {})[tenor] = row
    return out


def _fetch_points_scales(session, service, pairs: List[str], scale_fetch: Optional[Callable] = None,
                         asks: Optional[_Asks] = None) -> Dict[str, dict]:
    """{pair: scale report} -- the divisor that turns a pair's forward points into an
    outright, from the one helper the live tenor path uses too
    (pull_marks.fetch_points_scales: FWD_POINTS_SCALE as is, else 10 ** FWD_SCALE, both
    fields in ONE ReferenceDataRequest for every pair). A report is {'scale': divisor or
    None, 'field': the field that answered, 'raw', 'errors'}. Only asked for when a tenor
    series turns out to be points. `scale_fetch(session, service, tickers, fields) ->
    {ticker: {field: value}}` replaces the real request; with neither it nor a session
    there is no scale, and the forward is reported missing with that reason. A pair
    Bloomberg sends no scale for has none -- never a hard-coded pip size. With `asks`
    (2026-09-29): not sent once the run has stopped asking, the lock released before it, and
    a failed lookup recorded, its reason in each pair's report under errors["request"]."""
    from data.bloomberg.pull_marks import fetch_points_scales
    if scale_fetch is None and session is None:
        return {}
    asks = asks or _Asks()
    stop = asks.stop_reason()
    if stop:
        return {pair: {"scale": None, "field": "", "raw": {}, "errors": {"request": stop}} for pair in pairs}
    _release_lock(asks.conn)
    asks.requests += 1
    try:
        got = fetch_points_scales(session, service, pairs, fetch=scale_fetch)
    except Exception as exc:  # noqa: BLE001 -- a failed lookup is "no scale", reported per forward, not a dead run
        asks.raised(exc)
        reason = asks.error("scale", f"{REQUEST_FAILED} for the points divisors of {', '.join(pairs[:3])}: "
                                     f"{_plain_error(exc)}")
        return {pair: {"scale": None, "field": "", "raw": {}, "errors": {"request": reason}} for pair in pairs}
    asks.answered += 1
    asks.timeouts_in_row = 0
    for pair in pairs:
        report = (got or {}).get(pair) or {}
        asks.note_answer("scale", pair, bool(report.get("scale")),
                         f"Bloomberg sent no forward-points divisor for {pair}")
    return got


# {db: {"on": book day, "by_pair": {pair: scale report}}} (2026-09-30): the divisors a real
# Bloomberg session answered on that book day, so a second press of the same day, or a later
# stretch, does not ask the same pair again (an empty answer included: not asked again that day).
_scale_cache: Dict[str, dict] = {}


def _cached_points_scales(db_path, today_iso: str, session, service, pairs: List[str],
                          scale_fetch: Optional[Callable], asks: Optional[_Asks]) -> Tuple[Dict[str, dict], int]:
    """(`_fetch_points_scales` for `pairs`, how many pairs came from the day's cache). Only a
    real session is cached (`scale_fetch` None): a test's injected lookup is always asked. A
    lookup that failed is never cached, so it is asked again on the next press."""
    if scale_fetch is not None or session is None:
        return _fetch_points_scales(session, service, pairs, scale_fetch, asks), 0
    key = _db_key(db_path)
    cache = _scale_cache.get(key)
    if not cache or cache.get("on") != today_iso:
        cache = _scale_cache[key] = {"on": today_iso, "by_pair": {}}
    known = {p: cache["by_pair"][p] for p in pairs if p in cache["by_pair"]}
    ask = [p for p in pairs if p not in known]
    got = _fetch_points_scales(session, service, ask, scale_fetch, asks) if ask else {}
    for pair, report in (got or {}).items():
        if not ((report or {}).get("errors") or {}).get("request"):
            cache["by_pair"][pair] = report
    return {**known, **(got or {})}, len(known)


INFERRED_SCALE_FIELD = "Bloomberg's own forwards on file"


def _infer_points_scale(conn: sqlite3.Connection, pair: str, rows_by_day: Dict[str, Dict[str, dict]],
                        holidays=frozenset()) -> Optional[dict]:
    """The points divisor worked out from Bloomberg's own numbers, for a terminal that
    answers neither scale field (2026-09-21: 5d / MTD stayed n/a on the user's terminal for
    exactly that reason, every past forward being points with no divisor).

    The live pull writes Bloomberg's own OUTRIGHT forwards at the standard tenor dates
    (FWD_CURVE, source BBG_BFXFORWARD) next to the pair's SPOT. On the latest day that has
    them, outright - spot at a tenor's date is that tenor's points divided by the divisor,
    so the tenor ticker's points (from `rows_by_day`, the day nearest to it) over that
    difference is 10 ** n up to a day or two of market drift, and n is its rounded log10.
    A tenor votes only when it carries at least one point and lands within 0.3 of a whole
    power of ten; every vote must agree. None when the marks on file do not allow it.
    Nothing is assumed about pip sizes: both numbers are Bloomberg's."""
    hit = conn.execute(
        "SELECT as_of_date FROM marks WHERE instrument_id = ? AND mark_type = 'FWD_OUTRIGHT' AND source = ? "
        "AND julianday(settle_date) - julianday(as_of_date) >= 20 ORDER BY as_of_date DESC LIMIT 1",
        (pair, SRC_SPOT_FWD)).fetchone()
    if hit is None or not rows_by_day:
        return None
    d0_iso = hit[0]
    spot_row = conn.execute("SELECT value FROM marks_official WHERE instrument_id = ? AND mark_type = 'SPOT' "
                            "AND as_of_date = ?", (pair, d0_iso)).fetchone()
    try:
        spot = float(spot_row[0])
    except (TypeError, ValueError, IndexError):
        return None
    if spot <= 0:
        return None
    pillars = []
    for settle, value in conn.execute(
            "SELECT settle_date, value FROM marks WHERE instrument_id = ? AND as_of_date = ? AND mark_type = "
            "'FWD_OUTRIGHT' AND source = ? ORDER BY settle_date", (pair, d0_iso, SRC_SPOT_FWD)):
        try:
            pillars.append((date.fromisoformat(settle), float(value)))
        except (TypeError, ValueError):
            continue
    d0 = date.fromisoformat(d0_iso)
    spot_day = fc.spot_date_for(d0, pair, holidays)
    pillars = [(d, v) for d, v in pillars if d > spot_day and v > 0]
    day_iso = min(rows_by_day, key=lambda iso: abs((date.fromisoformat(iso) - d0).days))
    votes = []
    for tenor, row in (rows_by_day.get(day_iso) or {}).items():
        try:
            points = float((row or {}).get("PX_LAST"))
        except (TypeError, ValueError):
            continue
        tenor_day = fc.tenor_settle_date(spot_day, tenor, holidays)
        if tenor.upper() == "SP" or tenor_day is None or abs(points) < 1.0:
            continue
        outright, _how = fc.outright_for_date(pillars, tenor_day, spot, spot_day)
        if outright is None or outright == spot or (points > 0) != (outright > spot):
            continue
        log_ratio = math.log10(points / (outright - spot))
        n = round(log_ratio)
        if abs(log_ratio - n) <= 0.3 and 0 <= n <= 8:
            votes.append(n)
    if not votes or len(set(votes)) != 1:
        return None
    return {"scale": float(10 ** votes[0]), "field": INFERRED_SCALE_FIELD,
            "raw": {INFERRED_SCALE_FIELD: f"points of {day_iso} against the outrights of {d0_iso}, "
                                          f"{len(votes)} tenor(s) agreeing"},
            "errors": {}}


def _future_request_tickers(conn: sqlite3.Connection, library_rows: List[dict],
                            today: date) -> Tuple[Dict[str, str], Dict[str, str]]:
    """({security to ask -> instrument_id}, {instrument_id -> why it is not asked}) for the
    FUTURE_PX rows of the Bloomberg library in `library_rows`.

    A commodity future (asset class FUTURE, base_ccy a contract root of config/contracts.csv,
    instrument_id its canonical two-digit-year id 'CLZ26 Comdty') is asked under the form
    Bloomberg knows it by ON THE DAY OF THE REQUEST (`today`), whatever historical day is
    being worked (2026-09-24, commodity conversion plan): the one-digit-year form while the
    contract trades ('CLZ6 Comdty'), the two-digit canonical id once it has expired
    (`data.contracts.request_ticker(contract_for(root, id, conn), today)`), because
    Bloomberg reuses the one-digit name for the contract ten years on and asking the history
    of an expired contract under its stored `bbg_ticker` returns nothing. The mark is still
    written on the instrument's own instrument_id and expiry. Every other future (ES, the
    macro futures) and every listed option is asked under its library ticker, as before.
    A future with no Bloomberg ticker (a placeholder root, 'ZZ...' in config/contracts.csv, is
    booked with bbg_ticker '') never reaches this: the library flags it `requestable` False and
    `needed_in_range` leaves it out by default, the one filter, and lists the gap itself. A
    commodity id the contract universe cannot read back is not asked (never a guessed name)
    and is named with its reason.

    An option on a commodity future (instrument asset class CMDTY_OPTION on a contract root,
    commodity conversion Phase 5, 2026-09-24) is asked the same way: its live one-digit form
    while it trades ('CLZ6C 75 Comdty'), its canonical two-digit id once the request day is
    past its last trade date (`data.contracts.option_request_ticker(option_for(root, id,
    conn=conn), today)`). The underlying future an option's Greeks need (library role
    UNDERLYING) is a FUTURE_PX row of a FUTURE instrument like any other, traded or not."""
    from data.bloomberg import library
    from data.contracts import UnknownContract, contract_for, option_for, option_request_ticker, request_ticker
    info = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT instrument_id, asset_class, base_ccy FROM instruments")}
    asked: Dict[str, str] = {}
    not_asked: Dict[str, str] = {}
    for r in library_rows:
        if r["kind"] != "FUTURE_PX":
            continue
        instrument_id, ticker = r["key"], r["bbg_ticker"]
        asset_class, root_id = info.get(instrument_id, ("", ""))
        if asset_class in ("FUTURE", "CMDTY_OPTION") and library.is_contract_root(root_id):
            try:
                if asset_class == "FUTURE":
                    ticker = request_ticker(contract_for(root_id, instrument_id, conn), today)
                else:
                    ticker = option_request_ticker(option_for(root_id, instrument_id, conn=conn), today)
            except (UnknownContract, ValueError) as exc:
                what = "a contract" if asset_class == "FUTURE" else "an option"
                not_asked[instrument_id] = (f"{instrument_id} is not {what} of {root_id} the contract universe "
                                            f"can read ({exc}); not asked of Bloomberg")
                continue
        asked[ticker] = instrument_id
    return asked, not_asked


def _fetch_future_px_history(conn: sqlite3.Connection, session, service, runs: List[Tuple[date, date]],
                             fut_fetch: Optional[Callable] = None, today: Optional[date] = None,
                             not_asked: Optional[Dict[str, str]] = None,
                             asks: Optional[_Asks] = None,
                             wanted: Optional[Dict[str, set]] = None) -> Dict[str, Dict[str, float]]:
    """{instrument_id: {date_iso: PX_LAST}} -- one HistoricalDataRequest per stretch of
    `runs`, for the futures and listed options open inside it (the Bloomberg library's
    FUTURE_PX rows), asked for the daily PX_LAST (user, 2026-09-22: "all futures for past
    date pnl calculation, use px last"; it was PX_SETTLE, which Bloomberg served no history
    of for the listed options; user, 2026-09-24: a commodity future's close stays
    PX_LAST). The security asked is `_future_request_tickers`' (a commodity contract under
    its name on `today`, the request day); a row it will not ask for is added to
    `not_asked` {instrument_id: reason} when given. `fut_fetch` mirrors
    `_fetch_fwd_outright_history`'s `fwd_fetch`; defaults to
    pull_marks.fetch_historical_series. In chunks (`asks`); a failure is recorded under the
    instrument_id (`asks.reason("future", instrument_id, day)`). Values are kept as sent:
    pass 1 refuses one that is not a number. `wanted` ({day_iso: {instrument_id}},
    2026-09-30): the futures and options with a close still missing on some day of the
    stretch; one whose closes are all on file is not asked. None asks every one."""
    from data.bloomberg import library
    today = today or _as_date(None)
    asks = asks or _Asks()
    out: Dict[str, Dict[str, float]] = {}
    for run_start, run_end in runs:
        keep = None if wanted is None else set(_keys_in_run(wanted, run_start, run_end))
        lib = [r for r in library.needed_in_range(conn, run_start.isoformat(), run_end.isoformat())
               if keep is None or r["key"] in keep]
        ticker_to_instrument, skipped = _future_request_tickers(conn, lib, today)
        if not_asked is not None:
            not_asked.update(skipped)
        if not ticker_to_instrument:
            continue
        if fut_fetch is None:
            from data.bloomberg.pull_marks import fetch_historical_series
            fut_fetch = fetch_historical_series
        series = asks.series("future", fut_fetch, session, service, ticker_to_instrument, ["PX_LAST"], run_start,
                             run_end, key_of=ticker_to_instrument)
        for ticker, per_day in series.items():
            instrument_id = ticker_to_instrument.get(ticker)
            if instrument_id is None:
                continue
            out.setdefault(instrument_id, {}).update(
                {day: row["PX_LAST"] for day, row in per_day.items() if "PX_LAST" in row})
    return out


# --------------------------------------------------------------------------- LME curves (Phase 5, 2026-09-24)
# A past LME close is Bloomberg's daily PX_LAST of the metal's curve pillars (cash, 3M, the
# monthly prompts), stamped at the daily close (`close_stamp`, 17:00 New York) like every
# other close. The rows are built by bbg-curves' pure helper, fwd_curve.lme_history_marks:
# cash becomes the metal's SPOT, the pillars and the open prompts FWD_OUTRIGHT (BBG_INTERP on
# a computed date), never extrapolated beyond the last pillar.
LME_MARKS_UNAVAILABLE = ("LME curve not written: data.bloomberg.fwd_curve.lme_history_marks is not available "
                         "in this checkout")


def _lme_rows_in_range(conn: sqlite3.Connection, start: date, end: date) -> List[dict]:
    """The LME forwards' library rows a past close in [start, end] needs (their cash SPOT, the
    FWD_OUTRIGHT at each ticket's prompt, the LME_CURVE): `needed_in_range(...,
    include_lme=True)` restricted to `is_lme_row`, the one place the backfill reads them."""
    from data.bloomberg import library
    return [r for r in library.needed_in_range(conn, start.isoformat(), end.isoformat(), include_lme=True)
            if library.is_lme_row(r)]


def _lme_plan(lme_rows: List[dict], day_iso: str) -> Dict[str, dict]:
    """{root id: {'through': the metal's furthest prompt open that day, 'prompts': [the open
    tickets' prompts, sorted], 'pillars': library.lme_curve_pillars(root, day, through)}}: the
    LME curves `day_iso` needs and the pillars to ask for them, trimmed as today's pull trims
    them (`library.lme_curves_needed`: cash and 3M always, the monthlies up to the first on or
    after the furthest open prompt)."""
    from data.bloomberg import library
    found: Dict[str, dict] = {}
    for r in lme_rows:
        if not (r["needed_from"] <= day_iso <= r["needed_until"]):
            continue
        entry = found.setdefault(r["key"], {"through": "", "prompts": set()})
        if r["kind"] == library.LME_CURVE:
            entry["through"] = max(entry["through"], r["needed_until"])
        elif r["kind"] == "FWD_OUTRIGHT":
            entry["prompts"].add(r["settle_date"])
    return {root: {"through": e["through"], "prompts": sorted(e["prompts"]),
                   "pillars": library.lme_curve_pillars(root, day_iso, through=e["through"])}
            for root, e in sorted(found.items()) if e["through"]}


def _fetch_lme_history(session, service, runs: List[Tuple[date, date]], plans: Dict[str, Dict[str, dict]],
                       lme_fetch: Callable, asks: Optional[_Asks] = None) -> Dict[str, Dict[str, float]]:
    """{ticker: {date_iso: PX_LAST}} -- `lme_fetch` (Bloomberg's daily history, PX_LAST: the
    futures' fetcher) per stretch of `runs`, in chunks (`asks`, a metal's pillars in one
    request; a failure recorded under the root id), for every pillar ticker some day of the
    stretch needs (`plans`: {day_iso: _lme_plan}). A stretch with no LME ticket open asks
    for nothing. Values are kept as sent: `_lme_day_rows` refuses one that is not a number."""
    asks = asks or _Asks()
    out: Dict[str, Dict[str, float]] = {}
    for run_start, run_end in runs:
        key_of = {p["ticker"]: root for day_iso, plan in plans.items()
                  if run_start <= date.fromisoformat(day_iso) <= run_end
                  for root, entry in plan.items() for p in entry["pillars"] if p.get("ticker")}
        if not key_of:
            continue
        series = asks.series("lme", lme_fetch, session, service, key_of, ["PX_LAST"], run_start, run_end,
                             key_of=key_of)
        for ticker, per_day in series.items():
            for day_iso, row in (per_day or {}).items():
                if isinstance(row, dict) and row.get("PX_LAST") is not None:
                    out.setdefault(ticker, {})[day_iso] = row["PX_LAST"]
    return out


def _reason_text(reason) -> str:
    """A reason of lme_history_marks as a sentence: a string as is, a dict's 'reason'."""
    if isinstance(reason, dict):
        return str(reason.get("reason") or reason)
    return str(reason)


def _lme_day_rows(conn: sqlite3.Connection, d: date, plan: Dict[str, dict], series: Dict[str, Dict[str, float]],
                  needed: List[dict], today: str, asks: Optional[_Asks] = None) -> Tuple[List[dict], List[dict]]:
    """(mark rows, missing_marks) of `d`'s LME curves: per metal of `plan`, the day's pillar
    closes from `series` handed to fwd_curve.lme_history_marks(root_id, day: date, pillars,
    closes {ticker: float}, open_prompts [date], snapped_at = close_stamp(d)), which returns
    (rows, reasons). The rows are written as the helper builds them, never re-sourced. Every LME mark `needed` names (the cash SPOT, a ticket's prompt
    FWD_OUTRIGHT) that the helper did not produce, and that is not already official at the
    close, is listed missing with the helper's reasons for that metal, or Bloomberg's silence
    when it gave none: never dropped silently. A pillar close that is not a number is left
    out and counted, and a metal whose request failed or was not sent says so (`asks`)."""
    asks = asks or _Asks()
    day = d.isoformat()
    helper = getattr(fc, "lme_history_marks", None)
    rows: List[dict] = []
    reasons_by_root: Dict[str, List[str]] = {}
    for root_id, entry in plan.items():
        closes, refused = {}, []
        for p in entry["pillars"]:
            per_day = series.get(p.get("ticker")) or {}
            if day not in per_day:
                continue
            value = _number(per_day[day])
            if value is None:
                refused.append(asks.not_number(f"PX_LAST of {p['ticker']} on {day}", day, per_day[day]))
                continue
            closes[p["ticker"]] = value
        failed = asks.reason("lme", root_id, d)
        if helper is None:
            reasons_by_root[root_id] = [LME_MARKS_UNAVAILABLE]
            continue
        if not closes and (failed or refused):
            reasons_by_root[root_id] = [failed] if failed else refused
            continue
        try:
            # written as they come (the helper's sources and keys: cash SPOT BBG_BFXFORWARD keyed
            # on the day, pillars and prompts FWD_OUTRIGHT; the P&L reads them only so), never re-sourced
            made, reasons = helper(root_id, d, entry["pillars"], closes,
                                   [date.fromisoformat(x) for x in entry["prompts"]], close_stamp(d))
        except Exception as exc:  # noqa: BLE001 -- another lane's helper: its failure is this metal's reason
            made, reasons = [], [f"LME curve of {root_id} on {day}: fwd_curve.lme_history_marks raised {exc!r}"]
        rows += [dict(r) for r in (made or [])]
        reasons_by_root[root_id] = ([_reason_text(x) for x in (reasons or [])] + refused
                                    + ([failed] if failed else []))
    made_keys = {(r["instrument_id"], r["settle_date"], r["mark_type"]) for r in rows}
    missing: List[dict] = []
    for item in needed:
        root_id = item["instrument_id"]
        if item["mark_type"] not in SPOT_FWD_MARK_TYPES or not is_lme_instrument(root_id):
            continue
        if (root_id, item["settle_date"], item["mark_type"]) in made_keys:
            continue
        hit = conn.execute("SELECT snapped_at FROM marks_official WHERE as_of_date=? AND instrument_id=? AND "
                           "settle_date=? AND mark_type=?", (day, root_id, item["settle_date"], item["mark_type"])).fetchone()
        if hit is not None and is_close_row(item["mark_type"], day, hit[0], today, instrument_id=root_id):
            continue
        said = reasons_by_root.get(root_id) or []
        if root_id not in plan:
            reason = f"no LME curve of {root_id} is needed on {day} by the Bloomberg library"
        elif said:
            reason = "; ".join(dict.fromkeys(said))
        else:
            asked = ", ".join(p["ticker"] for p in plan[root_id]["pillars"]) or "no pillar tickers"
            reason = f"Bloomberg returned no PX_LAST for the LME curve of {root_id} on {day} (asked: {asked})"
        missing.append({**item, "reason": reason})
    return rows, missing


def _drop_already_official(conn: sqlite3.Connection, rows: List[dict], today: Optional[str] = None) -> List[dict]:
    """Filter out any row whose (as_of_date, instrument_id, settle_date, mark_type)
    already has an OFFICIAL mark on file that is a close -- a backfill run must never
    overwrite an existing close, even with an identical re-computed value, and even on a
    day that is otherwise incomplete (e.g. a trade added later needs a settle_date this
    day never needed before, but this day's SPOT was already official).

    A PAST day's official row that is not stamped at the close (`is_close_row`: 17:00 New
    York of its date, since 2026-09-28 for every mark type) is that day's last live pull or
    a 15:00 row of the retired FX rule, not a close, so the new row is kept and
    `_write_closes` replaces the old one. A row dated `today` (default: the New York book
    date) or later is live and is never replaced."""
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    out = []
    for r in rows:
        hit = conn.execute(
            "SELECT snapped_at FROM marks_official WHERE as_of_date=? AND instrument_id=? AND settle_date=? AND mark_type=?",
            (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"])).fetchone()
        if hit is None or (r["as_of_date"] < today and not is_close_row(r["mark_type"], r["as_of_date"], hit[0], today,
                                                                       instrument_id=r["instrument_id"])):
            out.append(r)
    return out


def _write_closes(conn: sqlite3.Connection, rows: List[dict], overwrite: bool = False, today: Optional[str] = None,
                  rejected: Optional[List[dict]] = None) -> int:
    """Write the backfill's rows: those whose key already holds a close are left out
    (`_drop_already_official`) unless `overwrite`; for a SPOT / FWD_OUTRIGHT row that is
    written (`SPOT_FWD_MARK_TYPES`: an FX pair's or an LME metal's), the same key's rows that
    are NOT the close (BBG_BFXFORWARD or BBG_INTERP, stamped at any other time) are deleted
    in the same transaction. Without that a stale direct-quote
    row would go on beating the new BBG_INTERP close in marks_official (a direct quote
    wins over an interpolated row for the same key). Nothing is deleted unless its
    replacement is being written, and only `marks` is touched, never realised_pnl.

    A row whose value is not a finite number is never written (hard rule 2; 2026-09-29, the
    last guard after the per-kind checks, e.g. a forward a curve helper built from a bad
    input): it is left out, and appended to `rejected` when given as {instrument_id,
    as_of_date, settle_date, mark_type, reason}; the stale row of its key stays."""
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    good = []
    for r in rows:
        if _number(r.get("value")) is None:
            if rejected is not None:
                rejected.append({k: r.get(k, "") for k in ("instrument_id", "as_of_date", "settle_date", "mark_type")}
                                | {"reason": _not_a_number(f"{r.get('mark_type')} of {r.get('instrument_id')} "
                                                           f"on {r.get('as_of_date')}", r.get("value"))})
            continue
        good.append(r)
    rows = good
    keep = rows if overwrite else _drop_already_official(conn, rows, today)
    known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    written = 0
    with conn:                                   # the delete and its replacement commit together
        for r in keep:
            if r["instrument_id"] not in known:  # as live.write_marks: only known instruments
                continue
            key = (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"])
            if r["mark_type"] in SPOT_FWD_MARK_TYPES and r["as_of_date"] < today:
                stale = conn.execute(
                    "SELECT source, snapped_at FROM marks WHERE as_of_date=? AND instrument_id=? AND settle_date=? "
                    "AND mark_type=? AND source IN (?, ?)", key + (SRC_SPOT_FWD, SRC_INTERP)).fetchall()
                for source, snapped in stale:
                    if not is_close_row(r["mark_type"], r["as_of_date"], snapped, today,
                                        instrument_id=r["instrument_id"]):
                        conn.execute("DELETE FROM marks WHERE as_of_date=? AND instrument_id=? AND settle_date=? "
                                     "AND mark_type=? AND source=?", key + (source,))
            conn.execute("INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?,?,?)",
                         key + (float(r["value"]), r["source"], r["snapped_at"]))
            written += 1
    return written


# --------------------------------------------------------------------------- only what is not stored (2026-09-30)
# User, 2026-09-30: "bloomberg needs to store on cache all the data - and only pull any new
# data". A day being worked is asked only for the marks it still lacks at the close
# (close_completeness's `missing`), key by key: a day missing one future's close asks that
# future, not every pair, tenor and future of the day again (before, the whole day was asked
# and `_drop_already_official` threw the stored half of the answer away). The marks the day
# holds at the close are read from file where a missing one is built from them (the day's
# SPOT close under a forward). An overwrite run asks everything, as before.
def _wanted_keys(missing_of: Dict[str, list], work: List[date]) -> Dict[str, Dict[str, set]]:
    """{kind: {day_iso: keys}} of the marks `work`'s days still lack (`missing_of`: {day_iso:
    close_completeness's `missing` list}): 'spot' the FX pairs whose SPOT close is missing,
    'fwd' the (pair, settle date) of a missing FX forward, 'fwd_pairs' the pairs with a missing
    forward that settles after the day (the ones whose tenors are needed), 'future' the
    futures and listed options missing their FUTURE_PX, 'lme' the LME metals with a cash,
    prompt or curve mark missing."""
    out: Dict[str, Dict[str, set]] = {"spot": {}, "fwd": {}, "fwd_pairs": {}, "future": {}, "lme": {}}
    for d in work:
        day = d.isoformat()
        for m in missing_of.get(day) or []:
            iid, mark_type, settle = m.get("instrument_id"), m.get("mark_type"), str(m.get("settle_date") or "")
            if not iid:
                continue
            if is_lme_instrument(iid):
                out["lme"].setdefault(day, set()).add(iid)
            elif mark_type == "SPOT":
                out["spot"].setdefault(day, set()).add(iid)
            elif mark_type == "FWD_OUTRIGHT":
                out["fwd"].setdefault(day, set()).add((iid, settle))
                if settle > day:
                    out["fwd_pairs"].setdefault(day, set()).add(iid)
            elif mark_type == "FUTURE_PX":
                out["future"].setdefault(day, set()).add(iid)
    return out


def _close_spots_on_file(conn: sqlite3.Connection, day: str, pairs: Iterable[str]) -> Dict[str, float]:
    """{pair: value} of the official SPOT closes `day` already holds (stamped at the close,
    `is_close_row`) for `pairs`: what a missing forward of that day is built on when the SPOT
    itself is not asked again."""
    wanted = set(pairs)
    out: Dict[str, float] = {}
    if not wanted:
        return out
    for iid, value, snapped in conn.execute(
            "SELECT instrument_id, value, snapped_at FROM marks_official WHERE as_of_date = ? AND mark_type = 'SPOT'",
            (day,)):
        if iid in wanted and is_close_row("SPOT", day, snapped):
            number = _number(value)
            if number is not None:
                out[iid] = number
    return out


def _spot_fetch_from_series(series_fetch: Callable, conn: sqlite3.Connection, runs: List[Tuple[date, date]],
                            asks: Optional[_Asks] = None, wanted: Optional[Dict[str, set]] = None) -> Callable:
    """A per-day SPOT fetch (the `fetch` signature backfill() takes) answered from
    `series_fetch` per stretch of `runs` (pull_marks.fetch_historical_series: the daily
    close, PX_LAST), in chunks (`asks`), for the pairs whose SPOT is needed inside that
    stretch (the Bloomberg library's SPOT rows), sent the first time one of its days is
    asked for. `fetch.reason(ticker, day)` is the source's own reason a day has no close,
    else why its request failed or was not sent ('' when neither), so a missing close is
    reported in Bloomberg's words. `wanted` ({day_iso: {pair}}, 2026-09-30): the pairs whose
    close is still missing on some day of the stretch; a pair whose closes are all on file is
    not asked. None asks every pair the stretch needs."""
    from data.bloomberg import library
    asks = asks or _Asks()
    cache: Dict[Tuple[date, date], dict] = {}

    def _row(ticker, day) -> dict:
        run = next(((a, b) for a, b in runs if a <= day <= b), None)
        return ((cache.get(run) or {}).get(ticker) or {}).get(day.isoformat()) or {}

    def fetch(session, service, tickers_wanted, field, day):
        run = next(((a, b) for a, b in runs if a <= day <= b), None)
        if run is None:
            return {}
        if run not in cache:
            known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
            keep = None if wanted is None else set(_keys_in_run(wanted, run[0], run[1]))
            tickers = sorted({r["bbg_ticker"] for r in library.needed_in_range(conn, run[0].isoformat(), run[1].isoformat())
                              if r["kind"] == "SPOT" and r["key"] in known and (keep is None or r["key"] in keep)})
            cache[run] = (asks.series("spot", series_fetch, session, service, tickers, [field], run[0], run[1])
                          if tickers else {})
        return {t: _row(t, day).get(field) for t in tickers_wanted}

    fetch.reason = lambda ticker, day: (str(_row(ticker, day).get(CLOSE_REASON) or "")
                                        or asks.reason("spot", ticker, day))
    return fetch


# The keys every day's result carries for the inputs and pricing steps (2026-09-22), whatever
# its status: what price_close made of the day, and the smiles / curves written for it or
# found missing. (The swaps' rates_priced / rates_failed / rates_note left 2026-09-24.)
# 2026-09-24 (Phase 5): `futures_options_priced`, the options on commodity futures among
# `options_priced`; `lme_marks`, the LME curve rows built for the day (cash, pillars, prompts).
# 2026-09-29 (Phase G): `step_errors`, {step: plain reason} of the day's steps that raised
# ("closes", "forwards", "inputs", "options", "ledger"); a day with any is ERROR, its other
# steps having run.
_STEP_KEYS = {"options_priced": None, "options_skipped": [], "options_note": "", "options_closed_out": [],
              "futures_options_priced": None, "vol_quotes": 0, "curve_quotes": 0, "missing_inputs": [],
              "lme_marks": 0, "step_errors": {}}


def _step_keys() -> dict:
    return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in _STEP_KEYS.items()}


def _skipped(day: str) -> dict:
    return {"day": day, "status": "SKIPPED", "closes": 0, "fwd_outrights": 0, "future_px": 0,
            "missing_pairs": [], "missing_pair_reasons": {}, "missing_marks": [], "realised": 0, "unrealisable": [],
            **_step_keys()}


# db -> {pair: scale report} of the last backfill() that needed a points divisor; published
# under the status file's "backfill" block so a paste says which field answered.
_scale_reports: Dict[str, dict] = {}
# db -> what the last run's requests and steps did (2026-09-29): {"errors", "error_count",
# "requests", "not_numbers", "not_number_count"}, published under the "backfill" block.
_run_reports: Dict[str, dict] = {}
# One ledger call per press (user yes, 2026-10-01: "the ledger runs up to three times per press"):
# the auto-backfill a pull starts (`start_auto_backfill` -> `auto_backfill`) ends with exactly ONE
# `realise_settled(conn, today)`, whatever the run did (nothing written, no past day due, no
# trade, a backfill that raised or was cut short), unless the database cannot be opened. The
# live pull reads this to leave out its own call on a press whose auto-backfill runs (a thread
# returned by `start_auto_backfill`; None means no run started and no ledger call was made).
# The standalone `backfill()` / CLI keeps its own ledger calls.
LEDGER_AT_END = True
# db -> True once this run's closing ledger step was made (or attempted), so `start_auto_backfill`
# makes it itself when `auto_backfill` stopped before reaching it (2026-10-01)
_ledger_ran: Dict[str, bool] = {}


def _empty_run_report() -> dict:
    return {"errors": [], "error_count": 0, "requests": {}, "not_numbers": [], "not_number_count": 0, "by_step": {}}


def _run_report(db_path) -> dict:
    return _run_reports.setdefault(_db_key(db_path), _empty_run_report())


def _merge_asks(db_path, asks: _Asks) -> None:
    """Add one backfill() call's failures, refused values and request counts to the run's report."""
    report = _run_report(db_path)
    report["errors"] = (report["errors"] + asks.errors)[:MAX_STATUS_ERRORS]
    report["error_count"] += asks.error_count
    report["not_numbers"] = (report["not_numbers"] + asks.not_numbers)[:MAX_STATUS_ERRORS]
    report["not_number_count"] += asks.not_number_count
    report["requests"] = asks.summary()
    by_step = report.setdefault("by_step", {})
    for step, entry in asks.by_step().items():     # what was asked and came back empty (2026-09-30)
        into = by_step.setdefault(step, {"asked": 0, "empty": 0, "empty_tickers": []})
        into["asked"] += entry["asked"]
        into["empty"] += entry["empty"]
        into["empty_tickers"] = (into["empty_tickers"] + entry["empty_tickers"])[:MAX_STATUS_ERRORS]


def _report_error(db_path, step: str, reason: str, day: str = "") -> str:
    """Record a failure outside backfill() (the planning, the closing ledger, the bookkeeping)."""
    report = _run_report(db_path)
    report["error_count"] += 1
    if len(report["errors"]) < MAX_STATUS_ERRORS:
        report["errors"].append({"step": step, "label": ASK_LABELS.get(step, step), "day": day, "reason": reason})
    return reason


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        if conn.in_transaction:
            conn.rollback()
    except Exception:  # noqa: BLE001
        pass


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             fwd_fetch: Optional[Callable] = None, fut_fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print,
             scale_fetch: Optional[Callable] = None, order: Optional[List[date]] = None,
             on_day: Optional[Callable[[dict], None]] = None, today: Optional[date] = None,
             quote_fetch: Optional[Callable] = None,
             on_stage: Optional[Callable[[str], None]] = None,
             session: Optional[Tuple] = None, reuse_listing: bool = False,
             ledger: bool = True) -> List[dict]:
    """Run the backfill. Returns one dict per business day of [start, end], in date order:
    {day, status: DONE|SKIPPED|NO_CLOSES|ERROR, closes, fwd_outrights, future_px,
    missing_pairs, missing_pair_reasons, missing_marks, realised, unrealisable,
    options_priced, options_skipped, options_note, options_closed_out, vol_quotes,
    curve_quotes, missing_inputs, step_errors}.

    Fault isolation (2026-09-29, Phase G; see "fault isolation" above `_Asks`): the session,
    each history stage and each day's steps (its closes, forwards, inputs, options, ledger)
    are guarded on their own. A stage that raises leaves its reason on every mark it would
    have made; a day's step that raises makes the day ERROR (`error`: the first reason,
    `step_errors`: every one) and the day's other steps still run; the history is asked in
    chunks, a failing chunk retried ticker by ticker, and nothing is sent after
    live.TIMEOUTS_BEFORE_GIVING_UP timeouts in a row; a value that is not a number is never
    written. What went wrong is published by `start_auto_backfill` ("errors", "requests",
    "not_numbers"). `on_stage(words)` is told what the run is doing before each history
    stage (the progress line), and neither it nor `on_day` can stop the run.
    `realised`/`unrealisable` are
    None on a day where realise_settled could not be imported (marks are still written).
    `options_priced` / `options_skipped` / `options_note` (2026-09-22) are what
    engine.options.store.price_close made of the day's FX options once its closes were
    written (`_price_options_close`): the count priced, the [{trade_id, reason}] it could
    not price from that day's inputs, and '' or why the step did not run (no option open
    that day leaves None / [] / ''; the pricer not importable or raising leaves None / []
    and the failure named). Only a DONE day runs it. `vol_quotes` / `curve_quotes` (2026-09-22,
    module docstring) are the smile and OIS rows written for the day from Bloomberg's daily
    history, for the pairs and currencies it lacked (`_write_day_inputs`), `missing_inputs`
    the [{kind, key, reason}] the history had nothing usable for. A day whose marks are all
    at the close but that lacks an input is worked for the inputs.
    `missing_marks` lists {instrument_id, settle_date, mark_type, reason} for anything
    this day needed (per inventory.close_completeness) but could not resolve -- a settle
    date beyond the last tenor, no forward tenors / future history for that pair / day,
    etc; never silently dropped. `missing_pair_reasons` is {pair: why it has no SPOT
    close}, in the source's own words where it gave any. ERROR (2026-09-21) is a day
    whose own processing raised:
    it carries `error`, and the other days still run -- one bad day used to end the run.

    The close (2026-09-28): every close is Bloomberg's daily PX_LAST, stamped 17:00 New York
    of its date (`close_stamp`), see the module docstring. `today` (default: the New York
    book date) only says which days are past: a row dated today is live and never replaced.

    ONE session for the whole call, and one request per kind per stretch of days being
    worked (`_runs`), however many days it covers.
    The work is done in two passes (2026-09-21):
      1. every day's SPOT closes and FUTURE_PX are collected and written in ONE
         transaction. The live pull calls engine.pnl.ledger.realise_settled every cycle,
         and a freeze takes the last official SPOT / FUTURE_PX on or before settlement
         and is never recomputed: writing those marks day by day in any order other than
         newest first would let it freeze a trade at an older close while the close of
         its own settle date was still on its way.
      2. FWD_OUTRIGHT, day by day (no freeze reads it), in `order` when given.
    `order` lists the days to work on, in the order to take them (auto_backfill: the
    header's reference dates first, then newest first); a day it leaves out, or one
    already complete, is SKIPPED. Without it every incomplete day runs in date order and
    realise_settled is called after each day, as before; with it realise_settled runs
    once, after the last day; with `ledger=False` (auto_backfill) never here, see below.
    `on_day(result)` is called as each day finishes.

    `fetch(session, service, tickers, field, day) -> {ticker: value|None}` (SPOT, per
    day; the field is PX_LAST) defaults to pull_marks.fetch_historical_series
    per stretch (`_spot_fetch_from_series`). `fwd_fetch`/`fut_fetch` (both `(session,
    service, tickers, fields, start, end) -> {ticker: {date_iso: {field: value}}}`, ONE
    call per stretch, 2026-09-18) both default to pull_marks.fetch_historical_series (the
    tenor series; the futures' and listed options' daily PX_LAST): one daily history for
    everything since 2026-09-28. `quote_fetch` (same shape; the vol and OIS quote history, one call per kind
    per stretch, 2026-09-22) defaults to pull_marks.fetch_historical_series when this call
    has a session, else to `fut_fetch` (a test that injects the three fetchers and no
    session has no daily-history fetcher but that one). `scale_fetch`: see
    `_fetch_points_scales`. `session_factory` defaults to pull_marks.open_session. All are
    injectable so the loop is testable without blpapi.

    `session` (2026-09-30, the pull's speed): a `(session, service)` pair borrowed from the
    caller -- the live pull's own, still open once its cycle is done -- used for every
    history request of this call in place of opening one (1-3 s per press). It is never
    stopped here: the lender stops it once this call is over. It takes precedence over
    `session_factory`; without it the session is opened (and stopped) here, as before.

    `reuse_listing` (2026-10-01, the pull's speed): the caller (`auto_backfill`) has just listed
    these days; what each day lacks is then read through the same listing memo
    (`_listed_days`), every day kept whose fingerprint has not moved since and the others
    worked out afresh, instead of `close_completeness` over the whole span a second time. The
    same rows: it applies only when `today` is the book's today (the listing's own).

    `ledger` (2026-10-01, one ledger call per press): False leaves out every
    `realise_settled` call of this function (after each day and after the last day), every
    day's `realised` then None and its flag "realisation at the end of the run". Only
    `auto_backfill` passes it, since its closing step makes the press's one ledger call
    (`LEDGER_AT_END`); a standalone call or the CLI keeps the default and freezes here."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness, _needed_marks
    from data.bloomberg.live import _ensure_fx_instruments, book_today
    from data.bloomberg import pull_marks as pm
    borrowed = session           # the caller's (session, service), never stopped here (2026-09-30)
    realise_settled = _import_realise_settled() if ledger else None
    price_close = _import_price_close()
    today = today or book_today()
    today_iso = today.isoformat()

    def _tell(words: str) -> None:
        if on_stage:
            try:
                on_stage(words)
            except Exception:  # noqa: BLE001 -- the progress line never stops the run
                pass

    def _told(result: dict) -> None:
        if on_day:
            try:
                on_day(result)
            except Exception:  # noqa: BLE001 -- as above
                pass

    conn = connect(Path(db_path))
    asks = _Asks(conn, log)
    try:
        # A conversion pair or an option's pair may never have been traded outright, and
        # the live pull only creates the plain pair row for what is open TODAY: a cross
        # that settled, or an option that expired, before this PC first connected would
        # otherwise have its closes fetched by nobody and written nowhere (write_marks
        # skips an unknown instrument). Same row the live pull creates, same function.
        _ensure_fx_instruments(conn, spot_only_pair_names(conn, start, end))
        pairs = traded_pairs(conn, start, end)
        from data.bloomberg import library
        in_range = library.needed_in_range(conn, start.isoformat(), end.isoformat())
        has_futures = any(r["kind"] == "FUTURE_PX" for r in in_range)
        has_inputs = any(r["kind"] in library.HISTORY_INPUT_KINDS for r in in_range)
        lme_rows = _lme_rows_in_range(conn, start, end)     # LME forwards: their own step (2026-09-24)
        _release_lock(conn)                                  # the library's sync, the instruments: committed
        if not pairs and not has_futures and not has_inputs and not lme_rows:
            # 2026-09-18: this used to bail out on `not pairs` alone, before traded_pairs
            # covered crosses -- a book with only futures and zero FX trades of any kind
            # would otherwise never reach the FUTURE_PX logic below at all. 2026-09-22: a
            # book whose only needs are its options' curves and smiles still has them to fetch.
            log("No FX pairs, futures, options or LME forwards open in this range; nothing to backfill.")
            return []
        by_ticker = {t: p for p, t in pairs}
        ticker_of = {p: t for p, t in pairs}
        days = business_days(start, end)
        if reuse_listing and today == book_today():
            # the caller's listing of these days, re-checked day by day (2026-10-01)
            listed = _listed_days(conn, _db_key(db_path), start, end, state_version())
        else:
            listed = close_completeness(conn, start.isoformat(), end.isoformat(), today=today_iso).to_dict("records")
        complete_by_day = {r["as_of_date"]: r["complete"] for r in listed}
        inputs_by_day = {r["as_of_date"]: r["inputs_missing"] for r in listed}
        todo = [d for d in days if overwrite or not complete_by_day.get(d.isoformat(), False)
                or inputs_by_day.get(d.isoformat())]
        if order is not None:
            todo_set = set(todo)
            work = [d for d in dict.fromkeys(order) if d in todo_set]
        else:
            work = todo
        log(f"Backfill {start} .. {end}: {len(days)} business days, {len(work)} to compute, {len(pairs)} pairs "
            f"(SPOT + FWD_OUTRIGHT + FUTURE_PX).")
        if ledger and realise_settled is None:
            log(_problem("  note: engine.pnl.ledger.realise_settled not importable; marks only, no realisation "
                         "this run."))
        if price_close is None:
            log(_problem("  note: engine.options.store.price_close not importable; no past day's FX options are "
                         "priced this run."))
        if not work:
            return [_skipped(d.isoformat()) for d in days]
        span_end = max(work)
        runs = _runs(work)
        fx_runs = runs
        # Only the marks each day still lacks are asked (2026-09-30, "only pull any new data");
        # an overwrite run asks everything. None = every key.
        want = (None if overwrite else
                _wanted_keys({r["as_of_date"]: r["missing"] for r in listed}, work))
        spot_want = None if want is None else want["spot"]
        session = service = None
        own_session = False  # did THIS call open the session itself (pm.open_session)?
        fwd_fields = None    # an injected fwd_fetch is asked for PX_LAST + SETTLE_DT, as before
        spot_per_day = fetch is not None   # an injected per-day SPOT fetch (the default asks per stretch, in chunks)
        # The session, guarded (2026-09-29): with none, nothing is asked ("not asked: no
        # Bloomberg session"), every day says so, and the closing ledger step still runs.
        try:
            if fetch is None or fwd_fetch is None or fut_fetch is None:
                if fetch is None:
                    fetch = _spot_fetch_from_series(pm.fetch_historical_series, conn, fx_runs, asks, spot_want)
                if fwd_fetch is None:
                    fwd_fetch, fwd_fields = pm.fetch_historical_series, ["PX_LAST"]
                if fut_fetch is None:
                    fut_fetch = pm.fetch_historical_series
                if borrowed is not None:
                    session, service = borrowed
                elif session_factory is None:
                    _tell("opening the Bloomberg session")
                    session, service = pm.open_session(host, port)
                    own_session = True
                else:
                    session, service = session_factory()
            elif borrowed is not None:
                session, service = borrowed
            elif session_factory is not None:
                session, service = session_factory()
        except Exception as exc:  # noqa: BLE001 -- no session: recorded, nothing asked, the rest runs
            asks.no_session = _plain_error(exc)
            asks.error("session", f"no Bloomberg session: {asks.no_session}")
        if quote_fetch is None:
            quote_fetch = (pm.fetch_historical_series
                           if (own_session or borrowed is not None or session_factory is not None) else fut_fetch)

        try:
            # FWD_OUTRIGHT / FUTURE_PX history: requests per kind per stretch of days being
            # worked (`_runs`), in chunks, never one per day per ticker, and never a day, a
            # pair or a tenor the book did not need (2026-09-21). Each stage guarded
            # (2026-09-29): one that raises leaves its reason on the marks it would have made.
            holidays = load_holidays()

            def _stage(kind: str, fn: Callable, default):
                _tell(f"asking Bloomberg's history: {ASK_LABELS.get(kind, kind)}")
                try:
                    return fn()
                except Exception as exc:  # noqa: BLE001 -- this stage fails alone
                    _rollback(conn)
                    asks.stage(kind, exc)
                    return default

            tenor_rows_by_pair = _stage("fwd", lambda: _fetch_fwd_outright_history(
                conn, session, service, fx_runs, fwd_fetch, holidays, fields=fwd_fields, asks=asks,
                wanted=None if want is None else want["fwd_pairs"]), {})
            future_not_asked: Dict[str, str] = {}     # instrument_id -> why no history was asked for it
            future_px_by_instrument = _stage("future", lambda: _fetch_future_px_history(
                conn, session, service, runs, fut_fetch, today, future_not_asked, asks=asks,
                wanted=None if want is None else want["future"]), {})
            # LME curves (2026-09-24): each day's pillars, their daily PX_LAST per stretch with
            # the futures' fetcher; the rows are built in pass 1 below.
            # Only the metals with a mark still missing that day (2026-09-30).
            lme_plans = _stage("lme", lambda: {iso: plan for iso, plan in
                                               ((d.isoformat(), {root: entry for root, entry in
                                                                 _lme_plan(lme_rows, d.isoformat()).items()
                                                                 if want is None
                                                                 or root in want["lme"].get(d.isoformat(), ())})
                                                for d in work)
                                               if plan}, {})
            lme_series = _stage("lme", lambda: _fetch_lme_history(session, service, runs, lme_plans, fut_fetch, asks)
                                if lme_plans else {}, {})
            # The smiles and curves the days' FX options price from (2026-09-22):
            # only the pairs and currencies some day of the stretch lacks, per kind per
            # stretch, from Bloomberg's daily history.
            lacking = _stage("inputs_plan", lambda: _lacking_inputs(conn, work), {"VOL_SMILE": {}, "OIS_CURVE": {}})
            vol_history = _stage("vol", lambda: _fetch_vol_history(
                session, service, runs, quote_fetch, lacking["VOL_SMILE"], asks), {})
            ois_history = _stage("ois", lambda: _fetch_ois_history(
                session, service, runs, quote_fetch, lacking["OIS_CURVE"], asks), {})
            scales: Dict[str, Dict[str, dict]] = {}

            def scale_report_for(pair: str) -> dict:
                if "by_pair" not in scales:      # asked for once, and only if some series is points
                    # a pair's divisor answered earlier the same book day is not asked again (2026-09-30)
                    scales["by_pair"], _cached = _cached_points_scales(db_path, today_iso, session, service,
                                                                       sorted(tenor_rows_by_pair), scale_fetch, asks)
                report = scales["by_pair"].get(pair) or {}
                if not report.get("scale"):
                    # neither field answered: work the divisor out from Bloomberg's own
                    # outrights on file against these points (_infer_points_scale)
                    inferred = _infer_points_scale(conn, pair, tenor_rows_by_pair.get(pair) or {}, holidays)
                    if inferred:
                        inferred["errors"] = report.get("errors") or {}
                        scales["by_pair"][pair] = report = inferred
                return report

            spot_reason = getattr(fetch, "reason", None)     # the source's own words, when it has any

            # ---- pass 1: SPOT + FUTURE_PX of every day, written together (see docstring).
            # Each day prepared on its own (2026-09-29): one that raises is ERROR, the others go on.
            _tell("asking Bloomberg's history: FX closes")
            prepared: Dict[date, dict] = {}
            first_rows: List[dict] = []
            for d in work:
                day = d.isoformat()
                try:
                    # Every mark this day actually needs (inventory._needed_marks -- the same
                    # set live.build_requests and close_completeness use, so all three can
                    # never drift apart). historical=True: what a PAST close needs -- for an
                    # FX option that is closing SPOT only (pair + USD-conversion pairs,
                    # covered by spot_rows), never a forward at its expiry.
                    needed = _needed_marks(conn, day, historical=True)
                    # Only the pairs THIS day needs a close for (2026-09-21); it was every pair
                    # of the whole range, on every day.
                    # (2026-09-30) and, but for an overwrite, only those whose close is not on file yet:
                    # the others' stored closes stand in for them below.
                    day_spot_want = None if spot_want is None else spot_want.get(day, set())
                    day_pairs = [i["instrument_id"] for i in needed
                                 if i["mark_type"] == "SPOT" and i["instrument_id"] in ticker_of]
                    tickers = [ticker_of[p_] for p_ in day_pairs if day_spot_want is None or p_ in day_spot_want]
                    stored_spots = ({} if day_spot_want is None else
                                    _close_spots_on_file(conn, day, [p_ for p_ in day_pairs if p_ not in day_spot_want]))
                    closes, spot_failed = {}, ""
                    if tickers and spot_per_day:
                        # an injected per-day fetch: guarded like a chunk (2026-09-29)
                        spot_failed = asks.stop_reason()
                        if not spot_failed:
                            _release_lock(conn)
                            asks.requests += 1
                            try:
                                closes = fetch(session, service, tickers, "PX_LAST", d) or {}
                                asks.answered += 1
                                asks.timeouts_in_row = 0
                                for t in tickers:
                                    asks.note_answer("spot", t, closes.get(t) is not None,
                                                     f"Bloomberg returned no PX_LAST for {t} on {day}")
                            except Exception as exc:  # noqa: BLE001 -- this day's FX closes fail alone
                                asks.raised(exc)
                                spot_failed = asks.error("spot", f"{REQUEST_FAILED} for the FX closes of {day}: "
                                                                 f"{_plain_error(exc)}", day)
                    elif tickers:
                        closes = fetch(session, service, tickers, "PX_LAST", d) or {}
                    spot_rows, spot_by_pair, missing_pairs, pair_reasons = [], dict(stored_spots), [], {}
                    for ticker in tickers:
                        value = closes.get(ticker)
                        fvalue = _number(value)
                        if fvalue is None:
                            pair = by_ticker[ticker]
                            missing_pairs.append(pair)
                            if value is not None:        # sent, but not a number (hard rule 2)
                                pair_reasons[pair] = asks.not_number(f"PX_LAST of {pair} on {day}", day, value)
                            else:
                                pair_reasons[pair] = (spot_failed or (spot_reason(ticker, d) if spot_reason else "")
                                                      or f"Bloomberg returned no PX_LAST for {pair} on {day}")
                            continue
                        spot_by_pair[by_ticker[ticker]] = fvalue
                        spot_rows.append({"as_of_date": day, "instrument_id": by_ticker[ticker], "settle_date": day,
                                          "mark_type": "SPOT", "value": fvalue, "source": SRC_SPOT_FWD,
                                          "snapped_at": close_stamp(d)})
                    if tickers and not spot_rows and not stored_spots:
                        # Only a genuine holiday/no-data day (there WERE FX tickers to ask
                        # for, and none came back) short-circuits here. A futures-only book
                        # (2026-09-18 fix) has `tickers == []` -- trivially "no spot rows"
                        # every day -- and must still reach the FUTURE_PX logic, not be
                        # treated as a holiday.
                        prepared[d] = {"no_closes": True, "missing_pairs": missing_pairs, "pair_reasons": pair_reasons}
                        continue
                    fut_rows, missing_marks = [], []
                    for item in needed:
                        instrument_id = item["instrument_id"]
                        if item["mark_type"] != "FUTURE_PX":
                            continue
                        if want is not None and instrument_id not in want["future"].get(day, ()):
                            continue             # its close is on file (2026-09-30): not asked, left as it is
                        raw = (future_px_by_instrument.get(instrument_id) or {}).get(day)
                        settle_value = _number(raw)
                        if settle_value is None:
                            if raw is not None:          # sent, but not a number (hard rule 2)
                                reason = asks.not_number(f"PX_LAST of {instrument_id} on {day}", day, raw)
                            else:
                                reason = (future_not_asked.get(instrument_id) or asks.reason("future", instrument_id, d)
                                          or f"Bloomberg returned no PX_LAST for {instrument_id} on {day}")
                            missing_marks.append({**item, "reason": reason})
                            continue
                        fut_rows.append({"as_of_date": day, "instrument_id": instrument_id,
                                         "settle_date": item["settle_date"], "mark_type": "FUTURE_PX",
                                         "value": settle_value, "source": SRC_FUTURE, "snapped_at": close_stamp(d)})
                    # The LME curves (2026-09-24): cash SPOT, pillars and the open prompts, at the
                    # daily close. In this pass, with the futures, because an LME ticket freezes
                    # at the metal's last cash price on or before its freeze day, the prompt less
                    # 2 LME business days (`engine.lme.freeze_date`).
                    lme_day_rows, lme_missing = _lme_day_rows(conn, d, lme_plans.get(day, {}), lme_series, needed,
                                                              today_iso, asks)
                    missing_marks += lme_missing
                    prepared[d] = {"no_closes": False, "needed": needed, "spot_rows": spot_rows,
                                   "spot_by_pair": spot_by_pair, "missing_pairs": missing_pairs,
                                   "pair_reasons": pair_reasons, "fut_rows": fut_rows, "lme_rows": lme_day_rows,
                                   "missing_marks": missing_marks}
                    first_rows += spot_rows + fut_rows + lme_day_rows
                except Exception as exc:  # noqa: BLE001 -- one bad day must not end the run for the others
                    _rollback(conn)
                    prepared[d] = {"error": asks.error("closes", f"this day's closes stopped: {_plain_error(exc)}", day)}
            # A row already official AT THE CLOSE is never rewritten; a past day's FX row
            # that is not the close is replaced (_write_closes). All the days together; if that
            # fails, day by day, newest first (a freeze takes the last close on or before its
            # date), a day whose own write fails being ERROR.
            _tell("saving the closes")
            not_saved: Dict[date, str] = {}
            refused_rows: List[dict] = []
            try:
                _write_closes(conn, first_rows, overwrite, today_iso, refused_rows)
            except Exception as exc:  # noqa: BLE001
                _rollback(conn)
                refused_rows = []
                asks.error("closes", f"saving every day's closes together stopped ({_plain_error(exc)}); "
                                     f"saved day by day instead")
                for d in sorted(prepared, reverse=True):
                    p = prepared[d]
                    if p.get("error") or p.get("no_closes"):
                        continue
                    day_refused: List[dict] = []
                    try:
                        _write_closes(conn, p["spot_rows"] + p["fut_rows"] + p["lme_rows"], overwrite, today_iso,
                                      day_refused)
                        refused_rows += day_refused
                    except Exception as exc2:  # noqa: BLE001
                        _rollback(conn)
                        not_saved[d] = asks.error("closes", f"this day's closes were not saved: {_plain_error(exc2)}",
                                                  d.isoformat())
            for r in refused_rows:            # a helper-built row that is not a number: its mark is missing
                asks.refused(f"{r['mark_type']} of {r['instrument_id']} on {r['as_of_date']}", r["as_of_date"],
                             r["reason"])
                p = prepared.get(date.fromisoformat(r["as_of_date"])) or {}
                if "missing_marks" in p:
                    p["missing_marks"].append({k: r[k] for k in ("instrument_id", "settle_date", "mark_type")}
                                              | {"reason": r["reason"]})

            # ---- pass 2: day by day, in the caller's order; each step guarded on its own
            results: Dict[date, dict] = {}
            for n, d in enumerate(work, 1):
                day, p = d.isoformat(), prepared[d]
                if p.get("error") or d in not_saved:
                    reason = p.get("error") or not_saved[d]
                    log(_problem(f"  {day}  ERROR  {reason}"))
                    results[d] = {"day": day, "status": "ERROR", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                                  "missing_pairs": p.get("missing_pairs", []),
                                  "missing_pair_reasons": p.get("pair_reasons", {}),
                                  "missing_marks": p.get("missing_marks", []), "realised": None, "unrealisable": [],
                                  **_step_keys(), "error": reason, "step_errors": {"closes": reason}}
                    _told(results[d])
                    continue
                if p["no_closes"]:
                    log(_problem(f"  {day}  NO_CLOSES  (holiday or Bloomberg returned nothing; nothing written)"))
                    results[d] = {"day": day, "status": "NO_CLOSES", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                                  "missing_pairs": p["missing_pairs"], "missing_pair_reasons": p["pair_reasons"],
                                  "missing_marks": [], "realised": None, "unrealisable": [], **_step_keys()}
                    _told(results[d])
                    continue
                spot_by_pair, missing_marks = p["spot_by_pair"], p["missing_marks"]
                step_errors: Dict[str, str] = {}

                def _failed(step: str, exc: BaseException, what: str) -> None:
                    _rollback(conn)
                    step_errors[step] = asks.error(step, f"{what} stopped: {_plain_error(exc)}", day)

                # -- step 1: the day's FX forwards (no freeze reads them)
                fwd_rows: List[dict] = []
                fwd_written = 0
                try:
                    curves = {}
                    for item in p["needed"]:
                        if item["mark_type"] != "FWD_OUTRIGHT":
                            # SPOT items are covered by pass 1: traded_pairs() (2026-09-18)
                            # includes every open FX pair (crosses included), every cross's
                            # USD-conversion legs, and every FX option's pair with its own
                            # USD-conversion pairs -- the superset _needed_marks' SPOT
                            # entries are drawn from. A close Bloomberg did not return is
                            # named under `missing_pairs`.
                            continue
                        instrument_id, settle = item["instrument_id"], item["settle_date"]
                        if is_lme_instrument(instrument_id):
                            continue    # an LME prompt: built with its metal's curve in pass 1, never an FX curve
                        if want is not None and (instrument_id, settle) not in want["fwd"].get(day, ()):
                            continue    # its close is on file (2026-09-30): not rebuilt, left as it is
                        target = date.fromisoformat(settle)
                        spot = spot_by_pair.get(instrument_id)
                        if target <= d:
                            # A leg settling on or before this day is marked at this
                            # day's own SPOT (same rule the live feed uses).
                            if spot is None:
                                missing_marks.append({**item, "reason": f"settles on or before {day} but "
                                                      f"{instrument_id} has no SPOT close that day"})
                                continue
                            fwd_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": settle,
                                             "mark_type": "FWD_OUTRIGHT", "value": spot, "source": SRC_SPOT_FWD,
                                             "snapped_at": close_stamp(d)})
                            continue
                        if instrument_id not in curves:
                            rows = tenor_rows_by_pair.get(instrument_id, {}).get(day, {})
                            curve = fc.historical_curve(d, rows, spot, None, instrument_id, holidays)
                            if curve["unit"] == fc.UNIT_POINTS and not curve["points"]:
                                report = scale_report_for(instrument_id)
                                curve = fc.historical_curve(d, rows, spot, report.get("scale"), instrument_id, holidays)
                                if not curve["points"] and not report.get("scale"):
                                    # neither field answered: say so, with what each one sent (or
                                    # why the lookup was not answered, 2026-09-29)
                                    curve["reason"] = ((report.get("errors") or {}).get("request")
                                                       or pm.describe_scale(instrument_id, report))
                            elif not curve["unit"] and spot is not None:
                                # no tenor value at all that day: the source's own reason, if it
                                # gave one, else why its request failed or was not sent
                                said = next((row[CLOSE_REASON] for row in rows.values()
                                             if isinstance(row, dict) and row.get(CLOSE_REASON)), "")
                                failed = "" if rows else (asks.reason("fwd", instrument_id, d)
                                                          or asks.reason("fwd_refused", instrument_id, d))
                                curve["reason"] = said or failed or curve["reason"]
                            for sentence in curve.get("skipped") or []:   # a tenor that was not a number
                                asks.refused(f"a forward tenor of {instrument_id} on {day}", day, sentence)
                            curves[instrument_id] = curve
                        curve = curves[instrument_id]
                        points = curve["points"]
                        if not points:
                            missing_marks.append({**item, "reason": curve["reason"]})
                            continue
                        value, how = fc.outright_for_date(points, target, spot, d)
                        if value is None:
                            missing_marks.append({**item, "reason": f"{instrument_id} {settle}: outside the forward "
                                                  f"tenors {points[0][0]}..{points[-1][0]}, not extrapolated"})
                            continue
                        if curve["unit"] == fc.UNIT_POINTS and not pm.points_outright_is_plausible(value, spot):
                            # a wrong points divisor must never reach `marks`
                            missing_marks.append({**item, "reason": pm.implausible_outright_reason(
                                instrument_id, settle, value, spot, scale_report_for(instrument_id))})
                            continue
                        # BBG_BFXFORWARD only for Bloomberg's own outright at Bloomberg's
                        # own tenor date. Interpolated, converted from points, or sitting
                        # on a pillar date this app computed -> BBG_INTERP, the live
                        # path's own rule (live._fwd_outright_rows, pull_marks'
                        # tenor fallback). marks_official (data/ingest/schema.py,
                        # OFFICIAL_FALLBACK_SOURCE, 2026-09-18 user decision) decides that
                        # BBG_INTERP counts as official where no direct row exists; this
                        # module's job is just to report the true provenance.
                        direct = how == "EXACT" and curve["unit"] == fc.UNIT_OUTRIGHT and target in curve["own_dates"]
                        fwd_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": settle,
                                         "mark_type": "FWD_OUTRIGHT", "value": value,
                                         "source": SRC_SPOT_FWD if direct else SRC_INTERP, "snapped_at": close_stamp(d)})
                    refused: List[dict] = []
                    _write_closes(conn, fwd_rows, overwrite, today_iso, refused)
                    fwd_written = len(fwd_rows) - len(refused)
                    for r in refused:                    # never written: the forward is missing, with why
                        asks.refused(f"FWD_OUTRIGHT of {r['instrument_id']} on {day}", day, r["reason"])
                        missing_marks.append({k: r[k] for k in ("instrument_id", "settle_date", "mark_type")}
                                             | {"reason": r["reason"]})
                except Exception as exc:  # noqa: BLE001 -- the day's other steps still run
                    _failed("forwards", exc, "the forward curves")
                # -- step 2: the smiles and curves the day lacked, from the history (2026-09-22),
                # before the day's options below, which price from them
                vol_written = curve_written = 0
                missing_inputs: List[dict] = []
                try:
                    vol_written, curve_written, missing_inputs = _write_day_inputs(conn, d, lacking, vol_history,
                                                                                   ois_history, asks)
                except Exception as exc:  # noqa: BLE001
                    _failed("inputs", exc, "the vol smiles and OIS curves")
                # -- step 3: the day's options from the inputs the day now has on file, before
                # the freeze below, which takes the expiry day's premium
                options_priced, options_skipped, options_note, options_closed_out, futures_options_priced = (
                    None, [], "", [], None)
                try:
                    (options_priced, options_skipped, options_note, options_closed_out,
                     futures_options_priced) = _price_options_close(conn, day, price_close)
                except Exception as exc:  # noqa: BLE001
                    _failed("options", exc, "the option pricing")
                    options_note = step_errors["options"]
                # -- step 4: the ledger, after each day when no order is given
                realised, unrealisable, flag = None, [], "realisation after the last day"
                day_ledger = ledger_block(None)      # what the ledger did on this day, re-freeze included
                if not ledger:
                    flag = "realisation at the end of the run"   # auto_backfill's one call (2026-10-01)
                elif realise_settled is None:
                    flag = "no realisation (realise_settled unavailable)"
                elif order is None:
                    try:
                        _release_lock(conn)
                        led = realise_settled(conn, day)
                        day_ledger = ledger_block(led, day)
                        realised, unrealisable = day_ledger["realised"], day_ledger["unrealisable"]
                        flag = "complete" if not unrealisable else f"unrealisable={[u['trade_id'] for u in unrealisable]}"
                        if day_ledger["refrozen_summary"]:
                            flag += f"  {day_ledger['refrozen_summary']}"
                    except Exception as exc:  # engine/pnl/ledger.py is owned by another task; never let its
                        # in-progress state stop marks from being written -- report and move on.
                        flag = f"realise_settled raised: {exc!r}"
                        _failed("ledger", exc, "the ledger")
                _release_lock(conn)
                status = "ERROR" if step_errors else "DONE"
                line = (f"  {day}  {status}  closes={len(p['spot_rows'])}  fwd_outrights={fwd_written}  "
                        f"future_px={len(p['fut_rows'])}"
                        + (f"  lme={len(p['lme_rows'])}" if p["lme_rows"] else "")
                        + f"  missing={len(p['missing_pairs']) + len(missing_marks)}"
                        f"  vol_quotes={vol_written}  curve_quotes={curve_written}"
                        + (f"  missing_inputs={len(missing_inputs)}" if missing_inputs else "")
                        + (f"  options={options_priced}" if options_priced is not None else "")
                        + (f"  futures_options={futures_options_priced}" if futures_options_priced else "")
                        + (f"  options_skipped={len(options_skipped)}" if options_skipped else "")
                        + (f"  options_closed_out={len(options_closed_out)}" if options_closed_out else "")
                        + (f"  {options_note}" if options_note else "")
                        + (f"  realised={realised}" if realised is not None else "") + f"  {flag}"
                        + (f"  stopped: {', '.join(step_errors)}" if step_errors else ""))
                # a problem line (the in-app log keeps it) when a step stopped or the ledger has news
                log(_problem(line) if step_errors or unrealisable or day_ledger["refrozen_summary"] else line)
                results[d] = {"day": day, "status": status, "closes": len(p["spot_rows"]),
                              "fwd_outrights": fwd_written, "future_px": len(p["fut_rows"]),
                              "missing_pairs": p["missing_pairs"], "missing_pair_reasons": p["pair_reasons"],
                              "missing_marks": missing_marks, "realised": realised, "unrealisable": unrealisable,
                              "refrozen": day_ledger["refrozen"], "kept": day_ledger["kept"],
                              "refrozen_count": day_ledger["refrozen_count"],
                              "refrozen_summary": day_ledger["refrozen_summary"],
                              "options_priced": options_priced, "options_skipped": options_skipped,
                              "options_note": options_note, "options_closed_out": options_closed_out,
                              "futures_options_priced": futures_options_priced,
                              "vol_quotes": vol_written,
                              "curve_quotes": curve_written, "missing_inputs": missing_inputs,
                              "lme_marks": len(p["lme_rows"]), "step_errors": step_errors}
                if step_errors:
                    results[d]["error"] = next(iter(step_errors.values()))
                _told(results[d])
            if order is not None and realise_settled is not None:
                try:
                    _release_lock(conn)
                    led = realise_settled(conn, span_end.isoformat())
                    last = _record_ledger(db_path, "after_last_day", ledger_block(led, span_end.isoformat()))
                    unrealisable = last.get("unrealisable") or []
                    line = (f"  realised after the last day: {last['realised']}"
                            + (f"  {last['refrozen_summary']}" if last["refrozen_summary"] else "")
                            + (f"  unrealisable={[u.get('trade_id') if isinstance(u, dict) else u for u in unrealisable]}" if unrealisable else ""))
                    log(_problem(line) if last["refrozen_summary"] or unrealisable else line)
                except Exception as exc:  # noqa: BLE001 -- as above: report and move on
                    _rollback(conn)
                    asks.error("ledger", f"realise_settled after the last day raised: {_plain_error(exc)}",
                               span_end.isoformat())
            if "by_pair" in scales:
                # Which field gave each pair's points divisor (or that neither did): in the
                # log, and kept for the status file's "backfill" block.
                _scale_reports[_db_key(db_path)] = {
                    pair: {"field": r.get("field") or "", "divisor": r.get("scale"), "raw": r.get("raw") or {},
                           "errors": r.get("errors") or {}} for pair, r in sorted(scales["by_pair"].items())}
                for pair, r in sorted(scales["by_pair"].items()):
                    log("  points divisor  " + pm.describe_scale(pair, r))
            s = asks.summary()
            stopped = sum(1 for r in results.values() if r["status"] == "ERROR")
            log(f"Finished: {sum(1 for r in results.values() if r['status'] == 'DONE')} days written, "
                f"{sum(1 for r in results.values() if r['status'] == 'NO_CLOSES')} with no closes, "
                f"{len(days) - len(results)} skipped"
                + (f"; {stopped} with a step stopped" if stopped else "")
                + f"; history requests {s['sent']} sent, {s['answered']} answered, {s['failed']} failed"
                + (f", {s['tickers_not_asked']} ticker(s) not asked" if s["tickers_not_asked"] else "")
                + (f"; {asks.not_number_count} value(s) not a number, not written" if asks.not_number_count else "")
                + ".")
            return [results.get(d) or _skipped(d.isoformat()) for d in days]
        finally:
            # 2026-09-18 fix: a session THIS call opened itself (pm.open_session, not an
            # injected session_factory the caller controls) was leaked. Never stop a
            # caller-supplied session, a borrowed one included (2026-09-30).
            if own_session and session is not None:
                try:
                    session.stop()
                except Exception:  # noqa: BLE001 -- never let cleanup mask the real result/exception
                    pass
    finally:
        _merge_asks(db_path, asks)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- automatic backfill (2026-09-15)
# 2_launcher.py no longer has a `backfill` subcommand: this runs by itself, on `start` and at
# the end of every live feed cycle, so nobody has to remember to run it. A module-level
# lock keeps two triggers (start + a feed cycle finishing moments later) from overlapping.
#
# 2026-09-21: a run is ONE backfill() call (one session, one request per kind) over every
# day that is due, the header's reference dates first and then newest first -- it used to
# be one call, one session and three requests per day, oldest day first, so the closes the
# header needs came last. What each day still lacks, and why, is kept per process
# (`_day_state`) and published under the status file's "backfill" key; a day that stayed
# incomplete is asked of Bloomberg again at most once per RETRY_SECONDS, not after every
# feed cycle, unless what it lacks has changed (a new upload).
_auto_lock = __import__("threading").Lock()
_publish_lock = __import__("threading").Lock()

RETRY_SECONDS = 3600
MAX_STATUS_DAYS = 30      # "days" in the status file: the reference dates, plus the newest days not DONE
MAX_STATUS_REASONS = 5

# 2026-09-22 (user: "yes do part 2"): the hourly retry is for a day that can still change.
# Only the last RECENT_BUSINESS_DAYS business days before today are asked again by time
# (a daily close can still appear for a day that just turned past); an older
# day is asked again only when what it lacks changes (a new upload) or the code's ticker
# rules change (`state_version`), never because an hour passed -- on the Bloomberg PC 43
# past days that could never complete (a few tickers Bloomberg rejects or has nothing for)
# were re-asked on every press after a restart, some 20 minutes of requests for nothing.
RECENT_BUSINESS_DAYS = 3
# 2026-09-30 (session decision, after "only pull any new data"): the hourly retry of a recent
# day stays -- Bloomberg can lag a close past the 05:00 Hong Kong roll -- and costs little now,
# since a retry asks only the keys the day still lacks (`_wanted_keys`). Each state also keeps
# the book day it was tried on ("tried_on"), bookkeeping only.
# Bloomberg's own rejection of a ticker or field, as the pull code passes it through
# verbatim (a fetch's `.reason`: "Bloomberg answered the history request for <ticker>
# with: Unknown/Invalid security [nid:11150]"; fwd_curve / live: "securityError: ..." /
# "<field>: Field not valid"). Matched case-insensitively. A day
# whose only failures carry one of these is never retried by time, whatever its age.
REJECTION_TEXTS = ("unknown/invalid security", "unknown security", "invalid security",
                   "invalid field", "field not valid")
MAX_WAITING_TICKERS = 5   # tickers named in the "waiting_on_tickers" sentence

_day_state: Dict[Tuple[str, str], dict] = {}   # (db, day) -> {at, tried_at, version, signature, status,
#                                                               missing_count, missing, rejected, rejected_only}
_days_block: Dict[str, dict] = {}              # db -> the "days" dict last built by auto_backfill
_options_block: Dict[str, dict] = {}           # db -> the "options" dict of the last run that worked a day (2026-09-22)
_inputs_block: Dict[str, dict] = {}            # db -> the "inputs" dict, likewise (2026-09-22; "rates" until 2026-09-24)
_published: Dict[str, dict] = {}               # db -> the whole "backfill" block last published
_notes: Dict[str, str] = {}                    # db -> the last run's "note" (past days being re-requested at the close)
_waiting: Dict[str, str] = {}                  # db -> the last run's "waiting_on_tickers" sentence (2026-09-22)
_cache_stats: Dict[str, dict] = {}             # db -> the last listing's day counts (2026-09-30, `_listing_stats`)
_step_seconds: Dict[str, dict] = {}            # db -> seconds per step of the last auto run (2026-10-01)


def _listing_stats(listed: List[dict]) -> dict:
    """What the listing of the past days says is on file (2026-09-30): the days listed, the
    days needing anything, the complete ones, and per day the needed marks present at the close
    (`present`; `_record_outcome` updates the days a run worked). `closes_skipped` is the
    marks on file this press did not ask for (all of them: a stored close is never asked)."""
    needing = [r for r in listed if int(r.get("needed") or 0) > 0 or r.get("inputs_missing")]
    present = {r["as_of_date"]: int(r.get("present") or 0) for r in listed}
    return {"days_listed": len(listed), "days_needing": len(needing),
            "complete": {r["as_of_date"] for r in needing if not _is_open(r)},
            "present": present, "closes_skipped": sum(present.values()), "days_asked": 0, "days_waiting": 0}


def _db_key(db_path) -> str:
    return str(Path(db_path).resolve())


def state_version() -> str:
    """The version stamp every per-day state carries: `library.LIBRARY_VERSION` (bumped
    whenever `library.compute` learns a new kind or ticker rule, the listed-option ticker
    included) plus a digest of the ticker tables and naming rules the backfill asks
    Bloomberg's history with -- the standard forward tenor tickers (`_tenor_tickers` over
    `pull_marks.STANDARD_TENORS` for a deliverable pair), the field the FUTURE_PX history
    is asked for (PX_LAST since 2026-09-22), the vol smile tickers
    (`vol_marketdata.vol_ticker`) and the OIS curve tickers (`rates_marketdata.ois_curve`).
    (The NDF ticker tables left the digest with the NDF book, 2026-09-24; the LME curve's
    pillar tickers joined it the same day, Phase 5.) The close rule itself is in the
    digest too ("close": the daily PX_LAST stamped 17:00 New York for every mark type,
    2026-09-28), so the reversal of the 15:00 FX rule reset every day's state once and a
    day that had waited on a rejected or empty intraday request is asked of the daily
    history. A day's state
    stamped by any other value counts as never tried, so a corrected ticker is asked for
    on the next press: the stamp resets when any of those constants or functions changes,
    and only then."""
    import hashlib
    from data.bloomberg import rates_marketdata as rm
    from data.bloomberg import vol_marketdata as vm
    from data.bloomberg.library import LIBRARY_VERSION
    from data.bloomberg.pull_marks import STANDARD_TENORS
    rules = {"close": f"daily PX_LAST, {CLOSE_HOUR_NY}:00 New York, every mark type",
             "tenors": {"USDJPY": _tenor_tickers("USDJPY", list(STANDARD_TENORS))},
             "future_px_field": "PX_LAST",
             "vol": [vm.vol_ticker("USDJPY", t, q) for t in vm.VOL_TENORS for q in vm.VOL_QUOTE_TYPES],
             "ois": {ccy: [(spec.ticker, spec.field) for spec in rm.ois_curve(ccy)] for ccy in sorted(rm.OIS_INDEX)}}
    # 2026-09-24 (Phase 5): the LME curve's pillar tickers (asked at the one close above).
    try:
        from engine.lme import lme_curve_tickers
        rules["lme"] = {"LME:CA": [p["ticker"] for p in lme_curve_tickers("LME:CA", "2026-01-05")]}
    except Exception:  # noqa: BLE001 -- no LME layer in this checkout: nothing of it is asked
        rules["lme"] = {}
    digest = hashlib.sha1(json.dumps(rules, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return f"{LIBRARY_VERSION}+{digest}"


def recent_business_days(today: date, holidays=None) -> List[str]:
    """The last RECENT_BUSINESS_DAYS business days before `today` (config/holidays.txt), as
    ISO strings: the past days the hourly retry still applies to."""
    days = business_days(today - timedelta(days=14), today - timedelta(days=1), holidays)
    return [d.isoformat() for d in days[-RECENT_BUSINESS_DAYS:]]


def is_rejection(reason: str) -> bool:
    """True when a failure reason carries Bloomberg's own rejection of the ticker or field
    (REJECTION_TEXTS), which no retry can change until the code names another ticker."""
    text = (reason or "").lower()
    return any(marker in text for marker in REJECTION_TEXTS)


# 2026-09-29 (Phase G): a failure that says nothing about the data -- a history request that
# raised or timed out, one not sent after the timeouts or with no session, a step of the run
# that raised. A day that carries one is asked again on the next press, whatever its age:
# without this an older day hit by one timeout waited until what it lacks changed.
TRANSIENT_TEXTS = (REQUEST_FAILED, f"{NOT_ASKED}: ", " stopped", "run raised", "no bloomberg session")


def is_transient(reason: str) -> bool:
    text = (reason or "").lower()
    return any(marker.lower() in text for marker in TRANSIENT_TEXTS)


_REQUEST_FOR_RE = re.compile(r"request for (.+?) with:")
_RETURNED_NO_RE = re.compile(r"returned no (.+?) for (.+?)(?: on \d{4}-\d{2}-\d{2}|$)")


def _reason_label(reason: str) -> str:
    """The ticker a failure reason is about, for the "waiting_on_tickers" sentence:
    'USDJPYSP Curncy' from a "request for <ticker> with:" wording, 'PX_LAST of CLZ26 Comdty' from a
    "returned no <what> for <ticker> on <day>" wording; the reason's start otherwise."""
    m = _REQUEST_FOR_RE.search(reason)
    if m:
        return m.group(1).strip()
    m = _RETURNED_NO_RE.search(reason)
    if m:
        return f"{m.group(1).strip()} of {m.group(2).strip()}"[:60]
    return reason.strip()[:60]


def _failure_reasons(result: dict, still_missing: List[dict]) -> List[str]:
    """Every failure reason of a worked day, untruncated (unlike `_plain_reasons`, which
    is the status file's five): the basis for telling a day Bloomberg rejects from one
    that may still fill. A NO_CLOSES day's reasons are its pairs' own, so a day whose
    every SPOT ticker is rejected counts as rejected, not as a holiday."""
    pair_reasons = result.get("missing_pair_reasons") or {}
    reasons: List[str] = []
    if result.get("error"):
        reasons.append(f"this day's run raised {result['error']}")
    reasons += [f"this day's run raised {r}" for r in (result.get("step_errors") or {}).values()]
    reasons += [pair_reasons.get(pair) or f"Bloomberg returned no PX_LAST for {pair}"
                for pair in result.get("missing_pairs") or []]
    if result["status"] != "NO_CLOSES":
        reasons += [m["reason"] for m in result.get("missing_marks") or []]
        reasons += [m["reason"] for m in result.get("missing_inputs") or []]
    if not reasons:
        reasons = [f"{m['instrument_id']} {m['mark_type']} {m['settle_date']}: not written" for m in still_missing]
    return list(dict.fromkeys(reasons))


def _state_path(db_path) -> Path:
    """The per-day state's home across restarts: a JSON sidecar next to the status file
    (`live.status_path` is `<db>.bloomberg_status.json`; this is `<db>.backfill_state.json`).
    Not a table: `data/ingest/schema.py` is data-ingest's, and the state is the backfill's
    own bookkeeping, never read for a P&L or a delta."""
    p = Path(db_path)
    return p.with_name(p.name + ".backfill_state.json")


def _load_state(db_path, key: str, clock: Callable[[], float]) -> None:
    """Fill `_day_state` for `key` from the sidecar when the process knows nothing about
    this database yet (a restart). `at` is on the caller's `clock` (monotonic, or a test's
    fake): each entry's wall-clock `tried_at` is turned into "that long before now". A
    missing or unreadable sidecar means an empty state, as before 2026-09-22."""
    if any(k[0] == key for k in _day_state):
        return
    try:
        raw = json.loads(_state_path(db_path).read_text(encoding="utf-8"))
        days = raw["days"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    now_wall, now = datetime.now().astimezone(), clock()
    for day_iso, entry in days.items():
        try:
            elapsed = max(0.0, (now_wall - datetime.fromisoformat(entry["tried_at"])).total_seconds())
            _day_state[(key, day_iso)] = {
                "at": now - elapsed, "tried_at": entry["tried_at"], "version": str(entry.get("version") or ""),
                "signature": frozenset(tuple(item) for item in entry["signature"]),
                "status": entry["status"], "missing_count": int(entry["missing_count"]),
                "missing": list(entry["missing"]), "rejected": list(entry.get("rejected") or []),
                "rejected_only": bool(entry.get("rejected_only")), "transient": bool(entry.get("transient")),
                "tried_on": str(entry.get("tried_on") or "")}
        except (KeyError, TypeError, ValueError):
            continue                                   # one bad entry: that day counts as never tried


def _save_state(db_path, key: str) -> None:
    """Write `_day_state` for `key` to the sidecar (temp file + os.replace). Never raises:
    a state that cannot be saved only costs the next restart a retry."""
    days = {day: {"tried_at": s["tried_at"], "version": s["version"],
                  "signature": sorted(list(item) for item in s["signature"]), "status": s["status"],
                  "missing_count": s["missing_count"], "missing": list(s["missing"]),
                  "rejected": list(s["rejected"]), "rejected_only": bool(s["rejected_only"]),
                  "transient": bool(s.get("transient")), "tried_on": str(s.get("tried_on") or "")}
            for (k, day), s in sorted(_day_state.items()) if k == key}
    target = _state_path(db_path)
    try:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"version": state_version(), "days": days}, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _waiting_sentence(key: str, signatures: Dict[str, frozenset], due: set, recent: List[str]) -> str:
    """One plain sentence on the days not asked for this run and why, tickers Bloomberg
    rejects named first (at most MAX_WAITING_TICKERS): the log line and the status block's
    "waiting_on_tickers". '' when nothing waits."""
    waiting = [(day, _day_state[(key, day)]) for day in sorted(signatures, reverse=True)
               if day not in due and (key, day) in _day_state]
    rejected_days = [(day, s) for day, s in waiting if s["rejected"]]
    parts: List[str] = []
    if rejected_days:
        tickers = list(dict.fromkeys(t for _, s in rejected_days for t in s["rejected"]))
        shown = ", ".join(tickers[:MAX_WAITING_TICKERS]) + (" ..." if len(tickers) > MAX_WAITING_TICKERS else "")
        parts.append(f"{len(rejected_days)} day(s) wait on tickers Bloomberg rejects ({shown}); "
                     f"asked again when the ticker list changes")
    older = [day for day, s in waiting if not s["rejected"] and day not in recent]
    if older:
        parts.append(f"{len(older)} older day(s) got nothing more from Bloomberg's history; asked again when "
                     f"what they lack, or the ticker list, changes")
    hourly = [day for day, s in waiting if not s["rejected"] and day in recent]
    if hourly:
        parts.append(f"{len(hourly)} day(s) within the last {RECENT_BUSINESS_DAYS} business days are tried "
                     f"again within the hour")
    return "; ".join(parts)


def _earliest_trade_date(conn: sqlite3.Connection) -> Optional[date]:
    row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


# --------------------------------------------------------------------------- the day listing, remembered (2026-09-30)
# User, 2026-09-30, at the Bloomberg PC: "the bloomberg pull is quite slow and looks wasteful".
# Every press listed every business day since the first trade (`close_completeness`, a few ms
# a day, growing with the book's age) although a past day's answer changes only when what it
# reads changes. `_listing_memo` keeps each day's row with a fingerprint of what the row was
# worked out from: per day, that day's rows of `marks`, `vol_quotes` and `curve_quotes`
# (count, highest rowid -- an INSERT OR REPLACE moves it --, the sum of the values and, for
# marks, of the settle dates, so an in-place UPDATE such as `apply_contract_dates` shows);
# for the whole book, the library's state (dirty, synced_at, code version), the library and
# `instruments` tables' shape, `state_version()` and the config files the library and the
# calendar read. A day whose fingerprint is unchanged keeps its row; every other day is
# worked out afresh, in stretches of consecutive business days (`_runs`), the same call on
# the same data. A table that cannot be read gives a fingerprint that never matches, so
# nothing is kept on it. In process only: a restart lists every day once.
_listing_memo: Dict[str, dict] = {}   # db -> {"global": tuple, "days": {day_iso: (day fingerprint, row)}}

_DAY_FP_SQL = (
    "SELECT as_of_date, COUNT(*), MAX(rowid), TOTAL(value), TOTAL(julianday(settle_date)) FROM marks "
    "WHERE as_of_date BETWEEN ? AND ? GROUP BY as_of_date",
    "SELECT as_of_date, COUNT(*), MAX(rowid), TOTAL(value) FROM vol_quotes "
    "WHERE as_of_date BETWEEN ? AND ? GROUP BY as_of_date",
    "SELECT as_of_date, COUNT(*), MAX(rowid), TOTAL(value) FROM curve_quotes "
    "WHERE as_of_date BETWEEN ? AND ? GROUP BY as_of_date",
)
_GLOBAL_FP_SQL = (
    "SELECT dirty, synced_at, code_version FROM bbg_library_state WHERE id = 1",
    "SELECT COUNT(*), MAX(rowid) FROM bbg_library",
    "SELECT COUNT(*), MAX(rowid), TOTAL(julianday(expiry_date)), TOTAL(length(bbg_ticker)) FROM instruments",
)


def _config_stats() -> tuple:
    """(name, mtime_ns, size) of the config files a day's needs are worked out from: the
    contract list (tickers, LME curves), the FX calendar and the exchange calendars."""
    root = Path(__file__).resolve().parents[2] / "config"
    files = [root / "contracts.csv", root / "holidays.txt"]
    try:
        files += sorted((root / "calendars").iterdir())
    except OSError:
        pass
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((f.name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((f.name, None, None))
    return tuple(out)


def _listing_fingerprint(conn: sqlite3.Connection, start: str, end: str, version: str) -> Tuple[tuple, Dict[str, tuple]]:
    """(the book's fingerprint, {day: that day's fingerprint}) over [start, end], read
    before the rows they vouch for, so a write landing in between is seen next time."""
    unreadable = object()                      # never equal to anything: nothing is kept
    parts: list = [version, _config_stats(), frozenset(load_holidays())]
    for sql in _GLOBAL_FP_SQL:
        try:
            parts.append(tuple(conn.execute(sql).fetchall()))
        except sqlite3.Error:
            parts.append(unreadable)
    per_day: Dict[str, list] = {}
    for i, sql in enumerate(_DAY_FP_SQL):
        try:
            for row in conn.execute(sql, (start, end)):
                per_day.setdefault(row[0], [None] * len(_DAY_FP_SQL))[i] = tuple(row[1:])
        except sqlite3.Error:
            parts.append(unreadable)
    return tuple(parts), {day: tuple(v) for day, v in per_day.items()}


def _listed_days(conn: sqlite3.Connection, key: str, start: date, end: date, version: str) -> List[dict]:
    """`close_completeness(conn, start, end)` as rows (dicts), every day with its own
    columns, each day's row kept from the last listing when nothing it reads has changed
    since (`_listing_memo`), the other days worked out afresh per stretch."""
    from data.bloomberg.inventory import close_completeness
    s_iso, e_iso = start.isoformat(), end.isoformat()
    book_fp, day_fps = _listing_fingerprint(conn, s_iso, e_iso, version)
    memo = _listing_memo.get(key)
    days = [d.isoformat() for d in business_days(start, end)]
    rows: Dict[str, dict] = {}
    if memo is not None and memo["global"] == book_fp:
        for day in days:
            kept = memo["days"].get(day)
            if kept is not None and kept[0] == day_fps.get(day):
                rows[day] = kept[1]
    for a, b in _runs([date.fromisoformat(d) for d in days if d not in rows]):
        for row in close_completeness(conn, a.isoformat(), b.isoformat()).to_dict("records"):
            rows[row["as_of_date"]] = row
    # The days outside [start, end] are kept while the book's fingerprint holds (2026-10-01:
    # backfill() re-reads a sub-span of the listing); each is still checked against its own
    # day's fingerprint before it is used again.
    kept_days = ({d: v for d, v in memo["days"].items() if d not in rows}
                 if memo is not None and memo["global"] == book_fp else {})
    _listing_memo[key] = {"global": book_fp,
                          "days": {**kept_days,
                                   **{day: (day_fps.get(day), rows[day]) for day in days if day in rows}}}
    return [rows[day] for day in days if day in rows]


def _listing_connection(db_path) -> sqlite3.Connection:
    """The listing's connection (2026-09-30). `schema.connect` rewrites the views and commits
    on every call, which moves the database's mtime -- the key every screen's cache is read
    on -- although nothing changed; on a database whose schema is already in place (the
    pull's own connect has just run it) the listing opens a plain connection, foreign keys
    on and the same busy timeout, so a press with nothing to backfill writes nothing. A
    database without the views yet gets `schema.connect`, as before."""
    from data.ingest.schema import BUSY_TIMEOUT_SECONDS, connect
    path = Path(db_path)
    if path.exists():
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_SECONDS)
        try:
            views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
            if {"marks_official", "trades_official"} <= views:
                conn.execute("PRAGMA foreign_keys = ON")
                return conn
        except sqlite3.Error:
            pass
        conn.close()
    return connect(path)


def _is_open(row: dict) -> bool:
    """A listed day the backfill still has to finish: marks needed and not all at the
    close, or a smile / curve its FX options price from missing."""
    return bool((row["needed"] > 0 and not row["complete"]) or row["inputs_missing"])


def reference_dates(today: date) -> List[date]:
    """The past closes the header's period figures difference against, newest first:
    t-1 and t-2 business days (Daily, Previous day), t-5 (5d), the last business day of
    the previous month (MTD) and of the previous year (YTD) -- the same
    engine.pnl.calendar functions and config/holidays.txt calendar ui/tabs/header.py uses."""
    from engine.pnl.calendar import (_last_business_day_of_prev_month, _n_business_days_back,
                                     _prev_business_day)
    holidays = load_holidays()
    t1 = _prev_business_day(today, holidays)
    refs = {t1, _prev_business_day(t1, holidays), _n_business_days_back(today, 5, holidays),
            _last_business_day_of_prev_month(today, holidays), _last_business_day_of_prev_year(today, holidays)}
    return sorted(refs, reverse=True)


def _signature(missing: List[dict], inputs_missing: List[dict] = ()) -> frozenset:
    """What a day still lacks, as a set: its missing marks and (2026-09-22) the smiles and
    curves its FX options price from (`inputs_missing`, keyed by kind)."""
    return (frozenset((m["instrument_id"], m["settle_date"], m["mark_type"]) for m in missing)
            | frozenset((m["key"], m["kind"]) for m in inputs_missing))


def _plain_reasons(result: dict, still_missing: List[dict]) -> List[str]:
    """At most MAX_STATUS_REASONS distinct plain sentences saying why a day is not
    complete, from backfill()'s own result for it -- never a second guess at the cause."""
    reasons: List[str] = []
    pair_reasons = result.get("missing_pair_reasons") or {}
    if result["status"] == "NO_CLOSES":
        reasons.append("Bloomberg returned no daily close (PX_LAST) for any FX pair this day (a holiday, or no data)")
        reasons += [pair_reasons[pair] for pair in result["missing_pairs"] if pair_reasons.get(pair)][:1]
    else:
        if result.get("error"):
            reasons.append(f"this day's run raised {result['error']}")
        reasons += [f"this day's run raised {r}" for r in (result.get("step_errors") or {}).values()]
        reasons += [pair_reasons.get(pair) or f"Bloomberg returned no PX_LAST for {pair}"
                    for pair in result["missing_pairs"]]
        reasons += [m["reason"] for m in result["missing_marks"]]
        reasons += [m["reason"] for m in result.get("missing_inputs") or []]
    if not reasons:
        reasons = [f"{m['instrument_id']} {m['mark_type']} {m['settle_date']}: not written" for m in still_missing]
    return list(dict.fromkeys(reasons))[:MAX_STATUS_REASONS]


def _record_ledger(db_path, step: str, block: dict) -> dict:
    """Publish what one of the backfill's ledger calls did (user yes, 2026-09-22: the pull
    status records what the ledger's re-freeze did) under the status file's "backfill" key
    as "ledger": one block of `live.ledger_block`'s shape (the live pull's `status["ledger"]`
    exactly: as_of_date, realised, unrealisable, repaired, refrozen, kept, refrozen_count,
    refrozen_summary), with the call's own block under "steps" as [{"step": step, ...}].
    Since 2026-10-01 a run makes ONE ledger call (`LEDGER_AT_END`): an auto run's closing step
    (`_realise_after_backfill`, "closing"), or a standalone `backfill()`'s call after the last
    worked day ("after_last_day"). Each call starts the block afresh, so a stale run never
    shows (until 2026-10-01 an auto run made both and the block summed them).
    Returns the step's block."""
    from data.bloomberg.live import patch_status, refrozen_summary
    key = _db_key(db_path)
    with _publish_lock:
        published = _published.setdefault(key, {})
        steps = [{"step": step, **block}]
        refrozen = [r for s in steps for r in s.get("refrozen") or []]
        merged = {"as_of_date": block.get("as_of_date"),
                  "realised": sum(s.get("realised") or 0 for s in steps),
                  "unrealisable": list(block.get("unrealisable") or []),
                  "repaired": [r for s in steps for r in s.get("repaired") or []],
                  "refrozen": refrozen, "kept": list(block.get("kept") or []),
                  "refrozen_count": len(refrozen), "refrozen_summary": refrozen_summary(len(refrozen)),
                  "steps": steps}
        published["ledger"] = merged
        try:
            patch_status(db_path, "backfill", {"ledger": merged})
        except Exception:  # noqa: BLE001 -- the status file is a report, never a reason to stop the run
            pass
    return block


def _realise_after_backfill(db_path, today: date, log: Callable[[str], None]) -> Optional[dict]:
    """The backfill's closing step, the press's ONE ledger call (`LEDGER_AT_END`, user yes
    2026-10-01): the ledger's plain `realise_settled(conn, today)` once the past closes are on
    file, so a trade settled before today is frozen at its settlement date's close, and one
    the ledger froze at a live row (an earlier press) is frozen again at the close that
    replaced it (the ledger's rule, 2026-09-22). Until 2026-10-01 the live pull froze first,
    backfill() again after its last worked day and this step a third time; the one call at
    today covers both, since the ledger looks at every row whose value date is before its
    as-of and freezes from the marks on or before each trade's own settlement, never from the
    as-of. Returns the call's `live.ledger_block` (None when the ledger is not importable or
    raised), after recording it in the status file (`_record_ledger`). A raise, the
    connection's included, and a ledger that cannot be imported are recorded in the run's
    "errors" (step "closing"), never raised (2026-09-29)."""
    _ledger_ran[_db_key(db_path)] = True
    realise_settled = _import_realise_settled()
    if realise_settled is None:
        log(_problem("Auto-backfill: engine.pnl.ledger.realise_settled not importable; no trade frozen this press."))
        _report_error(db_path, "closing", "the ledger could not be loaded, so no settled trade was frozen this press",
                      today.isoformat())
        return None
    from data.ingest.schema import connect
    conn = None
    try:
        conn = connect(Path(db_path))
        led = realise_settled(conn, today.isoformat())
        block = _record_ledger(db_path, "closing", ledger_block(led, today.isoformat()))
        if block["realised"]:
            log(f"Auto-backfill: {block['realised']} settled trade(s) frozen after the backfill.")
        if block["refrozen_summary"]:
            log(_problem(f"Auto-backfill: {block['refrozen_summary']}."))
        if block["unrealisable"]:
            ids = [u.get("trade_id") if isinstance(u, dict) else u for u in block["unrealisable"]]
            log(_problem(f"Auto-backfill: {len(ids)} settled trade(s) could not be frozen: {ids}."))
        return block
    except Exception as exc:  # noqa: BLE001 -- as in backfill(): report and move on
        if conn is not None:
            _rollback(conn)
        log(_problem(f"  realise_settled raised: {exc!r}"))
        _report_error(db_path, "closing", f"the closing realise_settled raised: {_plain_error(exc)}",
                      today.isoformat())
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- risk history (2026-09-30)
# User decision 2026-09-30: the Risk tab stops reading the research app's database; its daily
# history (settlement, volume, open interest per contract; PX_LAST per FX pair and LME pillar)
# comes from Bloomberg into this app's own table `price_history`. A risk input only: never a
# mark, never in `marks`, never in P&L or delta (hard rule 2). Filled at the end of the
# backfill of a real pull ("Pull Bloomberg now", hard rule 8), after the closes and the
# closing ledger step, before the snapshot export; what to ask is the Bloomberg library's
# `risk_history_needs(conn, as_of)`.
PRICE_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS price_history (
  instrument_id   TEXT NOT NULL,
  as_of_date      TEXT NOT NULL,
  settle          REAL NOT NULL,
  volume          REAL NOT NULL DEFAULT -1,
  open_interest   REAL NOT NULL DEFAULT -1,
  bbg_ticker      TEXT NOT NULL DEFAULT '',
  source          TEXT NOT NULL DEFAULT 'BBG_BDH',
  snapped_at      TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (instrument_id, as_of_date)
)
"""
RISK_HISTORY_FIELDS = ["PX_LAST", "PX_VOLUME", "OPEN_INT"]   # a contract or an LME pillar
RISK_HISTORY_FX_FIELDS = ["PX_LAST"]                          # an FX pair: no volume, no open interest
RISK_HISTORY_SOURCE = "BBG_BDH"
RISK_HISTORY_NO_ROWS = "Bloomberg returned no history"
# db -> the "risk_history" block of the last run, published under status["backfill"]
_risk_blocks: Dict[str, dict] = {}


def _ensure_price_history(conn: sqlite3.Connection) -> None:
    """Create `price_history` when ingest-schema's DDL has not reached this database yet (the
    same columns); nothing is written when it exists (a plain read of sqlite_master)."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'price_history'").fetchone():
        return
    conn.execute(PRICE_HISTORY_DDL)
    conn.commit()


def _has_weekday(lo: date, hi: date) -> bool:
    """Whether [lo, hi] holds a Monday-to-Friday day. Weekdays, not config/holidays.txt: a
    Chinese exchange trades on a New York holiday, and Bloomberg sends only the days that traded."""
    if lo > hi:
        return False
    first = lo + timedelta(days=max(0, 7 - lo.weekday()) if lo.weekday() >= 5 else 0)
    return first <= hi


def _risk_asks(needs: List[dict], stored: Dict[str, tuple], last_day: date) -> List[dict]:
    """The stretches to ask per requestable need: {ticker, instrument_id, fields, lo, hi,
    fresh}. With nothing on file, its whole window [start, end]; else the days before the
    first stored day and after the last one (the new days). A stored day is never asked again,
    with one exception: the last stored day of a contract whose volume or open interest came
    as none (-1) is asked with the new days, since Bloomberg publishes those a day late. A day
    inside the stored span with no row (a holiday of that exchange) is not asked on every
    press. `end` is capped at `last_day` (the day before the book's today: today's PX_LAST is
    a live price, not a close)."""
    out: List[dict] = []
    for need in needs:
        try:
            lo, hi = date.fromisoformat(str(need["start"])), date.fromisoformat(str(need["end"]))
        except (KeyError, TypeError, ValueError):
            continue                                   # said by the caller as not asked
        hi = min(hi, last_day)
        fx = str(need.get("kind") or "").upper() == "FX"
        base = {"ticker": need["bbg_ticker"], "instrument_id": need["instrument_id"],
                "fields": tuple(RISK_HISTORY_FX_FIELDS if fx else RISK_HISTORY_FIELDS)}
        have = stored.get(need["instrument_id"])
        if have is None:
            if _has_weekday(lo, hi):
                out.append({**base, "lo": lo, "hi": hi, "fresh": True})
            continue
        first, last, last_incomplete = have
        head_hi = first - timedelta(days=1)
        if _has_weekday(lo, min(head_hi, hi)):
            out.append({**base, "lo": lo, "hi": min(head_hi, hi), "fresh": False})
        tail_lo = max(lo, last + timedelta(days=1))
        if _has_weekday(tail_lo, hi):
            late = {}
            if last_incomplete and not fx and lo <= last:
                tail_lo = last                         # its volume / open interest, a day late
                late = {"late_from": last}             # never asked for that day alone (`_trim_known_empty`)
            out.append({**base, "lo": tail_lo, "hi": hi, "fresh": False, **late})
    return out


# --------------------------------------------------------------------------- risk history: empty answers kept (2026-09-30)
# User, 2026-09-30: "bloomberg needs to store on cache all the data - and only pull any new
# data". A stretch of days Bloomberg answered with nothing is written down (a JSON sidecar
# beside the database, `<db>.risk_history_state.json`, the backfill's own bookkeeping like
# `_state_path`'s, never read for a figure) and not asked again: the same book day, whatever
# it is; for good once the stretch ended RISK_EMPTY_FINAL_DAYS or more before the day it was
# asked (a contract's days before it was listed, an expired contract's days after its last
# close: Bloomberg's history does not fill those in later), or when Bloomberg rejected the
# ticker itself (until the ticker changes: a stretch is kept per ticker). Before this, a
# contract listed inside the 900-day window had its days before its first close asked on
# every press, and an expired contract whose last close fell before its estimated last trade
# date its last days, for nothing each time.
RISK_EMPTY_FINAL_DAYS = 7
# 2026-10-01 (user: the press pulls too much): a security Bloomberg answered with no row at all
# over its whole window (nothing of it on file: an unknown contract month, one not listed
# yet) is asked again at most once every RISK_EMPTY_WHOLE_DAYS days, not on every book day;
# the stretch is kept with "whole": true and is dropped, as every stretch, when its ticker
# changes. It is still reported each press, as known empty.
RISK_EMPTY_WHOLE_DAYS = 7


def _risk_state_path(db_path) -> Path:
    p = Path(db_path)
    return p.with_name(p.name + ".risk_history_state.json")


def _load_risk_empty(db_path) -> Dict[str, List[dict]]:
    """{instrument_id: [{ticker, lo, hi, on, reason, rejected}]} from the sidecar; {} when it
    is missing or unreadable (every stretch is then asked once more)."""
    try:
        raw = json.loads(_risk_state_path(db_path).read_text(encoding="utf-8"))
        return {str(iid): [dict(e) for e in entries if isinstance(e, dict)]
                for iid, entries in (raw.get("empty") or {}).items() if isinstance(entries, list)}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _usable_empty(entry: dict, ticker: str, today: date) -> Optional[Tuple[date, date, str]]:
    """(lo, hi, reason) of a recorded empty stretch that still stands for `ticker` on `today`:
    recorded today, final (ended RISK_EMPTY_FINAL_DAYS before the day it was asked), or a
    ticker Bloomberg rejected. None otherwise."""
    if str(entry.get("ticker") or "") != ticker:
        return None
    try:
        lo, hi, on = (date.fromisoformat(str(entry[k])) for k in ("lo", "hi", "on"))
    except (KeyError, TypeError, ValueError):
        return None
    reason = str(entry.get("reason") or RISK_HISTORY_NO_ROWS)
    if entry.get("rejected") or on == today or (on - hi).days >= RISK_EMPTY_FINAL_DAYS:
        return lo, hi, reason
    if entry.get("whole") and 0 <= (today - on).days < RISK_EMPTY_WHOLE_DAYS:
        # nothing at all over its whole window (2026-10-01): stands for a week, and covers the
        # days the window has gained since, so the security is not asked one new day at a time
        return lo, max(hi, today), reason
    return None


def _save_risk_empty(db_path, empty: Dict[str, List[dict]], today: date) -> None:
    """Write the stretches that still stand tomorrow or later (final, rejected) and today's;
    one that would lapse is dropped, and so is one that ended before the longest window. Never
    raises: a sidecar that cannot be written only costs a re-ask."""
    oldest = (today - timedelta(days=RISK_EMPTY_KEEP_DAYS)).isoformat()
    keep = {}
    for iid, entries in empty.items():
        kept = [e for e in entries if str(e.get("hi") or "") >= oldest
                and _usable_empty(e, str(e.get("ticker") or ""), today) is not None]
        if kept:
            keep[iid] = kept
    target = _risk_state_path(db_path)
    try:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "empty": keep}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


RISK_EMPTY_KEEP_DAYS = 1000     # a stretch older than any window (900 calendar days) is forgotten


def _trim_known_empty(ask: dict, entries: List[dict], today: date) -> Tuple[Optional[dict], str]:
    """(`ask` less the stretches recorded empty that still stand, or None when nothing is left
    to ask; the reason of the stretch that covered it). An ask left with only the late volume
    / open interest day (`late_from`) is dropped too: that day is never asked alone."""
    spans = [u for e in entries if (u := _usable_empty(e, ask["ticker"], today))]
    if not spans:
        return ask, ""
    lo, hi, said = ask["lo"], ask["hi"], ""
    changed = True
    while changed and lo <= hi:
        changed = False
        for elo, ehi, why in spans:
            if elo <= lo <= ehi:
                lo, said, changed = ehi + timedelta(days=1), why, True
            if lo <= hi and elo <= hi <= ehi:
                hi, said, changed = elo - timedelta(days=1), why, True
    late = ask.get("late_from")
    if not _has_weekday(lo, hi) or (late is not None and hi <= late):
        return None, said or RISK_HISTORY_NO_ROWS
    return {**ask, "lo": lo, "hi": hi}, ""


# A request's window is the union of its securities' stretches (`risk_history_step`). Since
# 2026-10-01 (user: "Pull Bloomberg now" slow and pulling too much) securities share a request
# only when that union is at most RISK_WINDOW_SLACK_DAYS calendar days, or RISK_WINDOW_SLACK_SHARE
# of its own stretch when that is more, longer than each one's own stretch: a 1-3 day tail ask
# never rides on a 900-day ask (a never-filled security, a new contract's early gap), which
# made every security of the request fetch 900 days only to keep its last two.
RISK_WINDOW_SLACK_DAYS = 7
RISK_WINDOW_SLACK_SHARE = 0.10


def _window_days(lo: date, hi: date) -> int:
    return (hi - lo).days + 1


def _window_slack(days: int) -> int:
    """The extra calendar days a stretch of `days` may be asked over by sharing a request."""
    return max(RISK_WINDOW_SLACK_DAYS, int(days * RISK_WINDOW_SLACK_SHARE))


def _risk_chunks(asks: List[dict]) -> List[List[dict]]:
    """`asks` packed into requests: one field list per request, HISTORY_CHUNK securities at
    most, never one ticker twice, and (2026-10-01) only securities whose stretches are alike:
    the request's window (the union of its securities' stretches) is never longer than any
    member's own stretch by more than `_window_slack` of it. Greedy over the stretches in
    order of (start, end), so a run of whole windows, a run of head gaps ending on about the
    same day and a run of tail asks of the last few days each make their own requests."""
    chunks: List[List[dict]] = []
    by_fields: Dict[tuple, List[dict]] = {}
    for a in asks:
        by_fields.setdefault(a["fields"], []).append(a)
    for group in by_fields.values():
        group.sort(key=lambda a: (a["lo"], a["hi"], a["ticker"]))
        current: List[dict] = []
        lo = hi = None
        shortest = 0
        for a in group:
            own = _window_days(a["lo"], a["hi"])
            if current:
                union = _window_days(min(lo, a["lo"]), max(hi, a["hi"]))
                tightest = min(shortest, own)
                if (len(current) >= HISTORY_CHUNK or any(c["ticker"] == a["ticker"] for c in current)
                        or union - tightest > _window_slack(tightest)):
                    chunks.append(current)
                    current = []
            if not current:
                lo, hi, shortest = a["lo"], a["hi"], own
            else:
                lo, hi, shortest = min(lo, a["lo"]), max(hi, a["hi"]), min(shortest, own)
            current.append(a)
        if current:
            chunks.append(current)
    return chunks


def _risk_fetch_with_errors(security_errors: Dict[str, str]) -> Callable:
    """pull_marks.fetch_historical_series with Bloomberg's own per-security error kept
    (`security_errors`, ticker -> sentence): the fetcher does not raise on an unknown security,
    it returns no rows, and its diagnostics record says why."""
    from data.bloomberg import pull_marks as pm

    def fetch(session, service, tickers, fields, start, end):
        diag = pm.Diagnostics()
        try:
            return pm.fetch_historical_series(session, service, tickers, fields, start, end, diag=diag)
        finally:
            for rec in diag.requests:
                for sec in rec.get("raw_response") or []:
                    err = sec.get("securityError") if isinstance(sec, dict) else None
                    if err:
                        security_errors[str(sec.get("security"))] = (
                            f"Bloomberg does not know this security: {err.get('message') or 'security error'}")
    return fetch


def risk_history_step(db_path, session: Optional[Tuple] = None, fetch: Optional[Callable] = None,
                      today: Optional[date] = None, log: Callable[[str], None] = print,
                      host: str = "localhost", port: int = 8194) -> dict:
    """Fill `price_history` (the Risk tab's daily history, user decision 2026-09-30) for every
    need of `data.bloomberg.library.risk_history_needs(conn, today)`, and return the status
    block "risk_history" (also kept for `start_auto_backfill`'s publication):

      {"ran_at", "as_of_date", "needs": <int>, "up_to_date": <int> (nothing new to ask),
       "asked": <int> securities asked, "requests": `_Asks.summary()`, "rows_written": <int>,
       "failures": [{"ticker", "instrument_id", "reason"}, ...], "failure_count",
       "not_numbers": [{"what", "day", "reason"}, ...], "not_number_count",
       "unrequestable": [{"instrument_id", "ticker", "reason"}, ...], "unrequestable_count",
       "error": "" or why the step did not run,
       (2026-09-30, "only pull any new data")
       "skipped_stored": <int> rows on file inside the needs' windows, never asked again,
       "empty": [{"ticker", "instrument_id", "reason"}, ...], "empty_count": securities asked
           this press that brought no row for their stretch (recorded, not asked again: see
           `_trim_known_empty`),
       "known_empty": [{"ticker", "instrument_id", "reason"}, ...], "known_empty_count":
           securities not asked this press because their stretch came back empty before,
       "rows_stored", "securities_stored", "days_stored": the whole table after the step}

    A requestable need is asked Bloomberg's daily history (PX_LAST, PX_VOLUME, OPEN_INT; an FX
    pair PX_LAST alone) only for its days not on file (`_risk_asks`), many securities per
    request (`_risk_chunks`) through the backfill's own `_Asks` (a failing request re-asked
    ticker by ticker, nothing sent after TIMEOUTS_BEFORE_GIVING_UP timeouts in a row). Rows are
    written INSERT OR REPLACE, source BBG_BDH, the ticker as asked, snapped_at now; a volume or
    open interest Bloomberg did not give is -1; a value that is not a number is never written
    (a settle: the day is left out; a volume or open interest: -1) and is reported. An
    unrequestable need is listed with its reason and never asked. Never raises, and never
    writes `marks`: a failure here is reported in its own block and fails neither the pull nor
    the backfill.

    `session`: a `(session, service)` to ask on (the live pull's lent one); without it one is
    opened here (pull_marks.open_session) and stopped. `fetch` (tests, scratch checks): the
    history fetcher `(session, service, tickers, fields, start, end) -> {ticker: {date_iso:
    {field: value}}}`, default pull_marks.fetch_historical_series."""
    from data.bloomberg.live import book_today
    today = today or book_today()
    key = _db_key(db_path)
    block: dict = {"ran_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "as_of_date": today.isoformat(), "needs": 0, "up_to_date": 0, "asked": 0, "requests": {},
                   "rows_written": 0, "failures": [], "failure_count": 0, "not_numbers": [], "not_number_count": 0,
                   "unrequestable": [], "unrequestable_count": 0, "error": "",
                   "skipped_stored": 0, "empty": [], "empty_count": 0, "known_empty": [], "known_empty_count": 0,
                   "rows_stored": 0, "securities_stored": 0, "days_stored": 0}
    _risk_blocks[key] = block
    empty_now: Dict[str, dict] = {}         # ticker -> {ticker, instrument_id, reason}: asked, nothing came back
    known_empty: Dict[str, dict] = {}       # ticker -> ...: not asked, came back empty before
    recorded = _load_risk_empty(db_path)    # the empty stretches on record (2026-09-30)
    record_changed = False

    def _record_empty(instrument_id: str, ticker: str, lo: date, hi: date, reason: str, rejected: bool,
                      whole: bool = False) -> None:
        nonlocal record_changed
        if lo > hi:
            return
        entry = {"ticker": ticker, "lo": lo.isoformat(), "hi": hi.isoformat(), "on": today.isoformat(),
                 "reason": reason, "rejected": bool(rejected)}
        if whole:
            entry["whole"] = True       # its whole window came back empty: stands a week (2026-10-01)
        entries = [e for e in recorded.get(instrument_id, [])
                   if (e.get("ticker"), e.get("lo"), e.get("hi")) != (ticker, entry["lo"], entry["hi"])]
        recorded[instrument_id] = entries + [entry]
        record_changed = True
    conn = None
    own_session = None
    failures: Dict[str, dict] = {}

    def _fail(ticker: str, instrument_id: str, reason: str) -> None:
        failures.setdefault(ticker, {"ticker": ticker, "instrument_id": instrument_id, "reason": reason})

    try:
        try:
            from data.bloomberg.library import risk_history_needs
        except Exception as exc:  # noqa: BLE001 -- bbg-library's function not in this checkout yet
            block["error"] = f"the list of risk-history needs is not available ({_plain_error(exc)}); nothing asked"
            return block
        conn = _listing_connection(db_path)
        _ensure_price_history(conn)
        needs = [dict(n) for n in (risk_history_needs(conn, today.isoformat()) or [])]
        block["needs"] = len(needs)
        requestable = []
        unrequestable = []
        for n in needs:
            ticker = str(n.get("bbg_ticker") or "")
            if n.get("requestable") and ticker and n.get("instrument_id"):
                requestable.append({**n, "bbg_ticker": ticker})
            else:
                unrequestable.append({"instrument_id": n.get("instrument_id") or "", "ticker": ticker,
                                      "reason": str(n.get("reason") or "no Bloomberg ticker for it")})
        block["unrequestable_count"] = len(unrequestable)
        block["unrequestable"] = unrequestable[:MAX_STATUS_ERRORS]
        stored: Dict[str, tuple] = {}
        for iid, lo, hi, vol, oi in conn.execute(
                "SELECT p.instrument_id, x.lo, x.hi, p.volume, p.open_interest FROM price_history p "
                "JOIN (SELECT instrument_id, MIN(as_of_date) AS lo, MAX(as_of_date) AS hi FROM price_history "
                "GROUP BY instrument_id) x ON p.instrument_id = x.instrument_id AND p.as_of_date = x.hi"):
            try:
                stored[iid] = (date.fromisoformat(lo), date.fromisoformat(hi),
                               (vol is None or vol < 0) or (oi is None or oi < 0))
            except (TypeError, ValueError):
                continue
        wanted = []
        asked_any = set()
        for a in _risk_asks(requestable, stored, today - timedelta(days=1)):
            asked_any.add(a["instrument_id"])
            kept, why = _trim_known_empty(a, recorded.get(a["instrument_id"], []), today)
            if kept is None:
                known_empty.setdefault(a["ticker"], {"ticker": a["ticker"], "instrument_id": a["instrument_id"],
                                                     "reason": why})
            else:
                wanted.append(kept)
        # The days on file inside the needs' windows: stored, so never asked again.
        ids = {n["instrument_id"] for n in requestable}
        starts = [str(n.get("start") or "") for n in requestable if n.get("start")]
        if ids and starts:
            for iid, count in conn.execute("SELECT instrument_id, COUNT(*) FROM price_history WHERE as_of_date >= ? "
                                           "GROUP BY instrument_id", (min(starts),)):
                if iid in ids:
                    block["skipped_stored"] += int(count)
        for n in requestable:
            try:
                date.fromisoformat(str(n["start"]))
                date.fromisoformat(str(n["end"]))
            except (KeyError, TypeError, ValueError):
                _fail(n["bbg_ticker"], n["instrument_id"],
                      f"no valid window to ask (start {n.get('start')!r}, end {n.get('end')!r}); not asked")
        block["up_to_date"] = len({n["instrument_id"] for n in requestable}
                                  - asked_any
                                  - {f["instrument_id"] for f in failures.values()})
        # a security with an ask left is asked; one whose every ask came back empty before is "known_empty"
        still_asked = {a["instrument_id"] for a in wanted}
        for t in [t for t, e in known_empty.items() if e["instrument_id"] in still_asked]:
            known_empty.pop(t, None)
        block["asked"] = len({a["ticker"] for a in wanted})
        if not wanted:
            return block
        _release_lock(conn)
        security_errors: Dict[str, str] = {}
        if fetch is None:
            fetch = _risk_fetch_with_errors(security_errors)
        if session is None:
            from data.bloomberg import pull_marks as pm
            try:
                own_session = pm.open_session(host, port)
            except Exception as exc:  # noqa: BLE001 -- no session: every security said as not asked
                reason = f"{NOT_ASKED}: no Bloomberg session ({_plain_error(exc)})"
                for a in wanted:
                    _fail(a["ticker"], a["instrument_id"], reason)
                block["error"] = reason
                return block
            session = own_session
        sess, service = session
        asks = _Asks(conn, log)
        snapped = datetime.now(NY).isoformat(timespec="seconds")
        for chunk in _risk_chunks(wanted):      # securities with alike stretches only (2026-10-01)
            fields = list(chunk[0]["fields"])
            lo, hi = min(a["lo"] for a in chunk), max(a["hi"] for a in chunk)
            key_of = {a["ticker"]: a["instrument_id"] for a in chunk}
            got = asks.series("risk_history", fetch, sess, service, list(key_of), fields, lo, hi, key_of=key_of)
            rows = []
            for a in chunk:
                per_day = got.get(a["ticker"]) or {}
                in_window = {d: v for d, v in per_day.items()
                             if isinstance(v, dict) and a["lo"].isoformat() <= str(d) <= a["hi"].isoformat()}
                why = asks.reason("risk_history", a["instrument_id"], a["lo"])
                if why:
                    _fail(a["ticker"], a["instrument_id"], why)
                    continue                           # a request that failed: asked again next press
                if not in_window:
                    rejected = a["ticker"] in security_errors
                    reason = (security_errors[a["ticker"]] if rejected
                              else f"{RISK_HISTORY_NO_ROWS} for {a['ticker']} ({a['lo']}..{a['hi']})")
                    if rejected or a["fresh"]:
                        _fail(a["ticker"], a["instrument_id"], reason)
                    # recorded: not asked again today, nor ever once final (2026-09-30); a whole
                    # window with nothing on file, not for RISK_EMPTY_WHOLE_DAYS (2026-10-01)
                    _record_empty(a["instrument_id"], a["ticker"], a["lo"], a["hi"], reason, rejected,
                                  whole=bool(a["fresh"]))
                    empty_now.setdefault(a["ticker"], {"ticker": a["ticker"], "instrument_id": a["instrument_id"],
                                                       "reason": reason})
                    continue
                written_days = []
                for day_iso, values in sorted(in_window.items()):
                    settle = _number(values.get("PX_LAST"))
                    if settle is None:
                        if "PX_LAST" in values:
                            asks.not_number(f"{a['ticker']} PX_LAST", day_iso, values.get("PX_LAST"))
                        continue               # no settle that day: no row (never a guessed one)
                    extra = []
                    for field in ("PX_VOLUME", "OPEN_INT"):
                        raw = values.get(field)
                        value = _number(raw) if field in fields else None
                        if value is None and field in fields and raw is not None:
                            asks.not_number(f"{a['ticker']} {field}", day_iso, raw)
                        extra.append(-1.0 if value is None else value)
                    rows.append((a["instrument_id"], day_iso, settle, extra[0], extra[1], a["ticker"],
                                 RISK_HISTORY_SOURCE, snapped))
                    written_days.append(day_iso)
                if written_days:
                    # the days either side of what came back hold nothing (2026-09-30)
                    first, last = date.fromisoformat(min(written_days)), date.fromisoformat(max(written_days))
                    if _has_weekday(a["lo"], first - timedelta(days=1)):
                        _record_empty(a["instrument_id"], a["ticker"], a["lo"], first - timedelta(days=1),
                                      f"Bloomberg has no history of {a['ticker']} before {first}", False)
                    if _has_weekday(last + timedelta(days=1), a["hi"]):
                        _record_empty(a["instrument_id"], a["ticker"], last + timedelta(days=1), a["hi"],
                                      f"Bloomberg has no history of {a['ticker']} after {last}", False)
            if rows:
                try:
                    conn.executemany(
                        "INSERT OR REPLACE INTO price_history (instrument_id, as_of_date, settle, volume, "
                        "open_interest, bbg_ticker, source, snapped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                    conn.commit()
                    block["rows_written"] += len(rows)
                except Exception as exc:  # noqa: BLE001 -- this request's rows fail alone
                    _rollback(conn)
                    reason = f"the rows could not be saved: {_plain_error(exc)}"
                    for a in chunk:
                        _fail(a["ticker"], a["instrument_id"], reason)
        block["requests"] = asks.summary()
        block["not_number_count"] = asks.not_number_count
        block["not_numbers"] = list(asks.not_numbers)
        return block
    except Exception as exc:  # noqa: BLE001 -- never a failure of the pull or the backfill
        if conn is not None:
            _rollback(conn)
        block["error"] = f"the risk history step stopped: {_plain_error(exc)}"
        return block
    finally:
        if own_session is not None:
            try:
                own_session[0].stop()
            except Exception:  # noqa: BLE001
                pass
        if conn is not None:
            try:                                  # what is on file now (2026-09-30)
                hit = conn.execute("SELECT COUNT(*), COUNT(DISTINCT instrument_id), COUNT(DISTINCT as_of_date) "
                                   "FROM price_history").fetchone()
                block["rows_stored"], block["securities_stored"], block["days_stored"] = (int(x or 0) for x in hit)
            except Exception:  # noqa: BLE001 -- no table yet: nothing stored
                pass
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if record_changed:
            _save_risk_empty(db_path, recorded, today)
        block["empty_count"] = len(empty_now)
        block["empty"] = list(empty_now.values())[:MAX_STATUS_ERRORS]
        block["known_empty_count"] = len(known_empty)
        block["known_empty"] = list(known_empty.values())[:MAX_STATUS_ERRORS]
        block["failure_count"] = len(failures)
        block["failures"] = list(failures.values())[:MAX_STATUS_ERRORS]
        problems = block["failure_count"] + block["not_number_count"]
        try:
            if block["error"]:
                log(_problem(f"Risk history: {block['error']}."))
            elif problems:
                log(_problem(f"Risk history: {block['rows_written']} row(s) written; {block['failure_count']} "
                             f"security(ies) failed, {block['not_number_count']} value(s) not a number left out; "
                             f"see the Data tab."))
            else:
                log(f"Risk history: {block['rows_written']} row(s) written for {block['asked']} security(ies).")
        except Exception:  # noqa: BLE001 -- the terminal is a courtesy
            pass


def auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                   fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                   fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                   log: Callable[[str], None] = print,
                   on_progress: Optional[Callable[[int], None]] = None,
                   scale_fetch: Optional[Callable] = None,
                   clock: Callable[[], float] = __import__("time").monotonic,
                   quote_fetch: Optional[Callable] = None,
                   on_stage: Optional[Callable[[str], None]] = None,
                   session: Optional[Tuple] = None) -> List[dict]:
    """Fill every business day from the earliest trade date to yesterday that needs marks
    and lacks a complete official close (per `data.bloomberg.inventory.close_completeness`
    -- SPOT + FWD_OUTRIGHT + FUTURE_PX, 2026-09-18) or a smile / curve its FX options
    price from (`inputs_missing`, 2026-09-22), in ONE `backfill()` call: the
    header's `reference_dates` first, then the remaining days newest first. Calls
    `on_progress(days_remaining)` after each day so a caller can publish it, and returns
    the days' results in the order they were worked.

    A day that was tried and stayed incomplete (a holiday with no closes, a settle date
    beyond the last tenor, a ticker Bloomberg does not serve) is asked again only when
    (2026-09-22, user: "yes do part 2"): what it lacks has changed since (a new upload);
    or the code's ticker rules changed (`state_version`, so a corrected ticker is asked
    for on the next press); or, for a day within the last RECENT_BUSINESS_DAYS business
    days alone, RETRY_SECONDS have passed and not every one of its failures is Bloomberg's
    own rejection of a ticker (`is_rejection`) -- an older day, or a day whose every
    failure is a rejected ticker, is never retried by time. `clock` is injectable for
    tests. A day on which the book needed nothing is never asked for. The outcome per day
    is kept in `_day_state`, remembered across restarts in the `_state_path` sidecar
    (loaded here when the process knows nothing about this database yet, written by
    `_record_outcome`), and the status-file "days" dict in `_days_block` (see
    `start_auto_backfill`); the days left waiting are said in one sentence, tickers named
    (`_waiting`, the block's "waiting_on_tickers"). `fwd_fetch`/`fut_fetch`/`quote_fetch`/
    `scale_fetch` are forwarded to `backfill()` unchanged (see its docstring), and so is
    `on_stage(words)`, the progress line's "what it is doing now".

    Fault isolation (2026-09-29, Phase G): this never raises. The listing of the days, the
    backfill and the bookkeeping are guarded on their own and a failure is recorded in the
    run's report (`_run_reports`, published as "errors"). A day whose failures include a
    request that failed or was not sent, or a step that raised (`is_transient`), is due again
    on the next press whatever its age.

    One ledger call per press (user yes, 2026-10-01; `LEDGER_AT_END`): `backfill()` is called
    with `ledger=False`, and the run ends, in a `finally`, with the closing step
    (`_realise_after_backfill`, `realise_settled(conn, today)`) UNCONDITIONALLY: with or
    without trades, past days due or rows written, whether the listing or the backfill raised.
    The live pull leaves out its own call on such a press. Until 2026-10-01 the step was left
    out with no trade or no past day, or when it could only repeat the pull's own call, and
    `backfill()` made a second call after its last worked day.

    The pull's speed (2026-09-30): `session`, a `(session, service)` borrowed from the live
    pull, is handed to `backfill()` and never stopped here. The listing of the past days
    keeps each day's answer while nothing it reads has changed (`_listed_days`)."""
    from data.bloomberg.live import book_today
    key = _db_key(db_path)
    _run_reports[key] = _empty_run_report()      # this run's errors, requests and refused values (2026-09-29)
    _cache_stats.pop(key, None)                  # this run's listing, once made (2026-09-30)
    today = book_today()
    results: List[dict] = []
    signatures: Dict[str, frozenset] = {}
    refs: List[date] = []
    planned = False       # the days were listed: their outcome is recorded whatever happens next
    _ledger_ran.pop(key, None)    # set by the closing step below (2026-10-01)
    seconds = _step_seconds[key] = {}   # where this run's time went (2026-10-01)
    started = t0 = _perf()

    def _tell(words: str) -> None:
        if on_stage:
            try:
                on_stage(words)
            except Exception:  # noqa: BLE001 -- the progress line never stops the run
                pass

    def _remaining(n: int) -> None:
        if on_progress:
            try:
                on_progress(n)
            except Exception:  # noqa: BLE001 -- as above
                pass

    try:
        _tell("listing the past days that lack a close")
        conn = _listing_connection(db_path)
        try:
            earliest = _earliest_trade_date(conn)
            if earliest is None:
                log("Auto-backfill: no trades in the database; nothing to do.")
                return []                             # the closing step still runs (2026-10-01)
            yesterday = today - timedelta(days=1)
            if earliest > yesterday:
                return []
            version = state_version()
            listed = _listed_days(conn, key, earliest, yesterday, version)
        finally:
            conn.close()
        open_rows = [row for row in listed if _is_open(row)]
        signatures = {row["as_of_date"]: _signature(row["missing"], row["inputs_missing"]) for row in open_rows}
        stats = _cache_stats[key] = _listing_stats(listed)
        # Days that hold marks which are not that day's close (a row stamped at a live pull's
        # own time, or at the 15:00 New York close of the FX rule retired on 2026-09-28): said
        # once in the log and in the status block, since the first run after a rule change asks
        # for every such day again (2026-09-28: every past close is the daily close, 17:00 New
        # York, whatever the instrument).
        restamp = sum(1 for row in open_rows if (row.get("not_closed") or 0) > 0)
        note = ""
        if restamp:
            note = (f"{restamp} past day(s) hold marks that are not that day's close (a row stamped at a live pull's "
                    f"own time, or at the 15:00 New York close of the rule retired on 2026-09-28); every such day is "
                    f"asked for Bloomberg's daily close (PX_LAST, stamped {CLOSE_HOUR_NY}:00 New York) and those rows "
                    f"replaced.")
        _notes[key] = note
        if note:
            log("Auto-backfill: " + note)
        _load_state(db_path, key, clock)              # a restart: what the last process had tried (2026-09-22)
        for stale in [k for k in _day_state if k[0] == key and k[1] not in signatures]:
            del _day_state[stale]                     # complete since (or no longer needed): nothing to report
        now = clock()
        recent = recent_business_days(today)
        due = []
        for day_iso, signature in signatures.items():
            state = _day_state.get((key, day_iso))
            if (state is None or state["version"] != version or state["signature"] != signature
                    or state.get("transient")):       # a request or a step failed last time (2026-09-29)
                due.append(date.fromisoformat(day_iso))
            elif day_iso in recent and not state["rejected_only"] and now - state["at"] >= RETRY_SECONDS:
                due.append(date.fromisoformat(day_iso))
        waiting = _waiting_sentence(key, signatures, {d.isoformat() for d in due}, recent)
        _waiting[key] = waiting
        stats["days_asked"], stats["days_waiting"] = len(due), len(signatures) - len(due)
        refs = reference_dates(today)
        planned = True
        if not due:
            log("Auto-backfill: history already complete." if not signatures else
                _problem(f"Auto-backfill: {len(signatures)} day(s) cannot be completed yet; nothing asked of "
                         f"Bloomberg: {waiting}."))
            _remaining(0)
            return []                                 # the closing step runs below: the backfill has tried
        if waiting:
            log(_problem(f"Auto-backfill: {waiting}."))
        order = [d for d in refs if d in due] + sorted((d for d in due if d not in refs), reverse=True)
        log(f"Auto-backfill: {len(order)} incomplete day(s) between {min(order)} and {max(order)}, "
            f"reference dates first, then newest first.")
        _remaining(len(order))

        def _on_day(result: dict) -> None:
            results.append(result)
            _remaining(len(order) - len(results))

        seconds["listing"] = round(_perf() - started, 2)
        started = _perf()
        try:
            backfill(db_path, min(order), max(order), fetch=fetch, fwd_fetch=fwd_fetch, fut_fetch=fut_fetch,
                     session_factory=session_factory, host=host, port=port, log=log, scale_fetch=scale_fetch,
                     order=order, on_day=_on_day, quote_fetch=quote_fetch, on_stage=on_stage, session=session,
                     reuse_listing=True, ledger=False)   # the one ledger call is the closing step below
        except Exception as exc:  # noqa: BLE001 -- recorded; the closing step and the bookkeeping still run
            log(_problem(f"Auto-backfill: the backfill stopped: {exc!r}"))
            _report_error(db_path, "plan", f"the backfill stopped: {_plain_error(exc)}")
        seconds["past_closes"] = round(_perf() - started, 2)
        return results
    except Exception as exc:  # noqa: BLE001 -- the listing of the days raised: said, and the closing step runs
        log(_problem(f"Auto-backfill: the list of days could not be built: {exc!r}"))
        _report_error(db_path, "plan", f"the list of past days to backfill could not be built: {_plain_error(exc)}")
        return results
    finally:
        seconds.setdefault("listing", round(_perf() - t0, 2))   # a run that worked no day
        started = _perf()
        # the press's one ledger call, always (2026-10-01, `LEDGER_AT_END`)
        _tell("the closing ledger step")
        _realise_after_backfill(db_path, today, log)   # guarded inside: a raise is recorded
        seconds["closing"] = round(_perf() - started, 2)
        started = _perf()
        if planned:
            try:
                _record_outcome(db_path, key, results, signatures, refs, clock, today)
            except Exception as exc:  # noqa: BLE001 -- the status file is a report, never a reason to stop
                _report_error(db_path, "bookkeeping", f"the backfill's day status could not be saved: "
                                                      f"{_plain_error(exc)}")
        seconds["bookkeeping"] = round(_perf() - started, 2)


def _record_outcome(db_path, key: str, results: List[dict], signatures: Dict[str, frozenset],
                    refs: List[date], clock: Callable[[], float], today: Optional[date] = None) -> None:
    """After a run (finished or not): what each worked day still lacks goes into
    `_day_state` (that is what the retry rule and the status file read) with the version
    stamp, the tickers Bloomberg rejected and whether those were its only failures; the
    state is saved to the sidecar; the status-file "days" dict is rebuilt -- every
    reference date, plus the newest days that are not DONE, MAX_STATUS_DAYS at most --
    and the "waiting_on_tickers" sentence is rebuilt over every day still incomplete."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness
    if results:
        version = state_version()
        conn = connect(Path(db_path))
        try:
            # one call per stretch of worked days (2026-09-30), not one per day: the same rows
            after: Dict[str, dict] = {}
            for a, b in _runs([date.fromisoformat(r["day"]) for r in results]):
                for row in close_completeness(conn, a.isoformat(), b.isoformat()).to_dict("records"):
                    after[row["as_of_date"]] = row
            stats = _cache_stats.get(key)
            for result in results:
                row = after[result["day"]]
                if stats is not None:          # what is on file now, for the status block's "cache"
                    stats["present"][result["day"]] = int(row.get("present") or 0)
                    if not _is_open(row) and int(row.get("needed") or 0) > 0:
                        stats["complete"].add(result["day"])
                if (row["complete"] or not row["missing"]) and not row["inputs_missing"]:
                    _day_state.pop((key, result["day"]), None)
                    signatures.pop(result["day"], None)
                    continue
                failures = _failure_reasons(result, row["missing"])
                rejected = list(dict.fromkeys(_reason_label(r) for r in failures if is_rejection(r)))
                _day_state[(key, result["day"])] = {
                    "at": clock(), "tried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "version": version, "signature": _signature(row["missing"], row["inputs_missing"]),
                    "status": "NO_CLOSES" if result["status"] == "NO_CLOSES" else "INCOMPLETE",
                    "missing_count": len(row["missing"]) + len(row["inputs_missing"]),
                    "missing": _plain_reasons(result, row["missing"]),
                    "rejected": rejected, "rejected_only": bool(failures) and all(is_rejection(r) for r in failures),
                    "transient": any(is_transient(r) for r in failures),
                    "tried_on": today.isoformat() if today is not None else ""}
        finally:
            conn.close()
    _save_state(db_path, key)
    if today is not None:
        _waiting[key] = _waiting_sentence(key, signatures, set(), recent_business_days(today))
    days: Dict[str, dict] = {}
    for ref in refs:
        iso = ref.isoformat()
        if (key, iso) in _day_state or iso not in signatures:
            continue                               # reported from _day_state below, or DONE
        days[iso] = {"status": "INCOMPLETE", "missing_count": len(signatures[iso]),
                     "missing": ["the backfill has not reached this day yet"]}
    ref_isos = {ref.isoformat() for ref in refs}
    known = sorted((day for k, day in _day_state if k == key), reverse=True)
    for iso in [d for d in known if d in ref_isos] + [d for d in known if d not in ref_isos]:
        if iso in ref_isos or len(days) < MAX_STATUS_DAYS:
            state = _day_state[(key, iso)]
            days[iso] = {"status": state["status"], "missing_count": state["missing_count"],
                         "missing": list(state["missing"])}
    for iso in ref_isos:
        days.setdefault(iso, {"status": "DONE", "missing_count": 0, "missing": []})
    _days_block[key] = dict(sorted(days.items(), reverse=True))
    if results:
        # What price_close made of each worked day's FX options (2026-09-22), newest first,
        # MAX_STATUS_DAYS at most; a run that worked no day leaves the last one's on file.
        # A block of its own, not keys on "days": that block's entries are built from
        # close_completeness, not from these results, and their shape is pinned.
        options = {r["day"]: {"priced": r.get("options_priced"), "skipped": list(r.get("options_skipped") or []),
                              "note": r.get("options_note") or "",
                              "closed_out": list(r.get("options_closed_out") or []),
                              "futures_options_priced": r.get("futures_options_priced")} for r in results}
        _options_block[key] = dict(sorted(options.items(), reverse=True)[:MAX_STATUS_DAYS])
        # and the smile / curve rows each worked day got from the history (2026-09-22), or
        # could not; the block was "rates" and carried the swaps' pricing until 2026-09-24
        inputs = {r["day"]: {"vol_quotes": r.get("vol_quotes") or 0, "curve_quotes": r.get("curve_quotes") or 0,
                             "missing_inputs": list(r.get("missing_inputs") or [])} for r in results}
        _inputs_block[key] = dict(sorted(inputs.items(), reverse=True)[:MAX_STATUS_DAYS])


def _history_counts(db_path) -> Tuple[int, int, int]:
    """(rows, securities, days) of `price_history` on file, read plainly; zeros without it."""
    try:
        from data.ingest.schema import BUSY_TIMEOUT_SECONDS
        conn = sqlite3.connect(str(Path(db_path)), timeout=BUSY_TIMEOUT_SECONDS)
        try:
            hit = conn.execute("SELECT COUNT(*), COUNT(DISTINCT instrument_id), COUNT(DISTINCT as_of_date) "
                               "FROM price_history").fetchone()
            return tuple(int(x or 0) for x in hit)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- no table, no file: nothing stored
        return 0, 0, 0


def _cache_block(db_path, report: dict, risk: dict) -> dict:
    """The status block's "cache" (2026-09-30, user: "bloomberg needs to store on cache all the
    data - and only pull any new data"): what is on file, and what this press asked of
    Bloomberg's history against what it left alone because it is stored.

      {"updated_at",
       "closes_stored": past-day marks the book needs, on file at the close (after the run),
       "history_rows_stored" / "history_securities_stored" / "history_days_stored":
           `price_history` (one row = one security's day),
       "days": {"listed", "needing", "complete", "asked", "waiting"} -- past business days
           since the first trade; needing a mark or an input; complete; asked this press;
           incomplete but not asked (came back incomplete before: see "waiting_on_tickers"),
       "asked": tickers sent to Bloomberg's history this press (every step, risk history included),
       "skipped_stored": stored values not asked again (the closes on file before the run and
           the risk-history rows on file inside its windows),
       "empty" / "empty_tickers": tickers asked that brought nothing back,
       "known_empty": risk-history securities not asked because they came back empty before,
       "steps": {step: {"asked", "empty", "empty_tickers"}} for fx_closes, fx_forwards,
           points_scale, futures, lme, vol, ois (only the steps that asked anything), and
           risk_history {"asked", "empty", "empty_tickers", "skipped_stored", "known_empty"},
       "sentence": "Stored: N closes, N history days · This pull asked: N new, skipped N already
           stored" (then " · N came back empty" when any did)}

    Nothing here asks Bloomberg or writes the database. Never raises."""
    try:
        return _cache_block_of(db_path, report, risk)
    except Exception as exc:  # noqa: BLE001 -- the status file is a report
        return {"error": f"the cache counts could not be made: {_plain_error(exc)}", "sentence": ""}


def _cache_block_of(db_path, report: dict, risk: dict) -> dict:
    key = _db_key(db_path)
    stats = _cache_stats.get(key) or {}
    steps = {step: dict(entry) for step, entry in (report.get("by_step") or {}).items()}
    risk = risk or {}
    if "rows_stored" in risk and risk.get("ran_at"):
        rows, securities, days = (int(risk.get(k) or 0) for k in ("rows_stored", "securities_stored", "days_stored"))
    else:
        rows, securities, days = _history_counts(db_path)
    if risk.get("ran_at"):
        steps["risk_history"] = {"asked": int(risk.get("asked") or 0), "empty": int(risk.get("empty_count") or 0),
                                 "empty_tickers": [e.get("ticker") for e in risk.get("empty") or []],
                                 "skipped_stored": int(risk.get("skipped_stored") or 0),
                                 "known_empty": int(risk.get("known_empty_count") or 0)}
    closes = sum((stats.get("present") or {}).values())
    asked = sum(int(e.get("asked") or 0) for e in steps.values())
    skipped = int(stats.get("closes_skipped") or 0) + int((steps.get("risk_history") or {}).get("skipped_stored") or 0)
    empty = sum(int(e.get("empty") or 0) for e in steps.values())
    empty_tickers = [t for e in steps.values() for t in e.get("empty_tickers") or []][:MAX_STATUS_ERRORS]
    sentence = (f"Stored: {closes:,} closes, {rows:,} history days · This pull asked: {asked:,} new, "
                f"skipped {skipped:,} already stored" + (f" · {empty:,} came back empty" if empty else ""))
    return {"updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "closes_stored": closes, "history_rows_stored": rows, "history_securities_stored": securities,
            "history_days_stored": days,
            "days": {"listed": int(stats.get("days_listed") or 0), "needing": int(stats.get("days_needing") or 0),
                     "complete": len(stats.get("complete") or ()), "asked": int(stats.get("days_asked") or 0),
                     "waiting": int(stats.get("days_waiting") or 0)},
            "asked": asked, "skipped_stored": skipped, "empty": empty, "empty_tickers": empty_tickers,
            "known_empty": int((steps.get("risk_history") or {}).get("known_empty") or 0),
            "steps": steps, "sentence": sentence}


# --------------------------------------------------------------------------- snapshot only after a write (2026-10-01)
# User, 2026-10-01: "Pull Bloomberg now" is slow. The export (`snapshot.save_after_pull`)
# fingerprints every market table month by month on every press; when nothing the snapshot
# carries changed since this process's last export (the live pull, the backfill and the risk
# history wrote no row, and nothing else did either), it is not run and the status says
# SNAPSHOT_UNCHANGED. What "changed" means: `_market_fingerprint`, one read per table --
# `marks` and `price_history` (large, written only by INSERT OR REPLACE and DELETE: a new
# rowid or a count moves) by their count and highest rowid, plus `marks`' settle dates (the
# one in-place UPDATE, Bloomberg's contract dates moving a future's mark keys); the small
# tables (`instruments`, OIS curves and quotes, vol quotes, contract dates) by the total of
# every column. In process only: the first press after a start always exports.
SNAPSHOT_UNCHANGED = "marks snapshot: nothing new, snapshot not rewritten"
_LARGE_MARKET_TABLES = {"marks": "COUNT(*), MAX(rowid), TOTAL(julianday(settle_date))",
                        "price_history": "COUNT(*), MAX(rowid)"}
_exported_fp: Dict[str, tuple] = {}     # db -> `_market_fingerprint` at this process's last export


def _market_fingerprint(db_path) -> Optional[tuple]:
    """What the snapshot carries, as one comparable tuple (see above); None when it cannot be
    read (the export then runs). A read only: no schema pass, nothing written."""
    try:
        from data.bloomberg.snapshot import MARKET_TABLES
        from data.ingest.schema import BUSY_TIMEOUT_SECONDS
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            conn.execute("BEGIN")              # every table read at the same moment
            parts = []
            for table in ("instruments",) + tuple(MARKET_TABLES):
                info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                if not info:
                    parts.append((table, None))
                    continue
                aggregate = _LARGE_MARKET_TABLES.get(table)
                if aggregate is None:
                    cols = ["COUNT(*)"]
                    for col in info:
                        name, decl = f'"{col[1]}"', str(col[2] or "").upper()
                        if any(t in decl for t in ("INT", "REAL", "NUM", "FLOA", "DOUB")):
                            cols.append(f"TOTAL({name})")
                        else:
                            cols.append(f"TOTAL(length({name}))")
                            if col[1].lower().endswith(("date", "_at")):
                                cols.append(f"TOTAL(julianday({name}))")
                    aggregate = ", ".join(cols)
                parts.append((table, tuple(conn.execute(f'SELECT {aggregate} FROM "{table}"').fetchone())))
            return tuple(parts)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- unknown: the export runs
        return None


def failure_sentence(report: dict) -> str:
    """The backfill block's "reason" after a run that did not raise (2026-09-29): '' when
    nothing failed, else one sentence with the word "failed", the count and the first
    failure in plain words (the rest are under "errors")."""
    count = int(report.get("error_count") or 0)
    if not count:
        return ""
    first = (report.get("errors") or [{}])[0]
    head = f"{first.get('label')}: " if first.get("label") else ""
    return (f"backfill partly failed: {count} step(s) or request(s) did not go through, the rest ran; "
            f"first: {head}{first.get('reason', '')}")


class _BackfillProgress:
    """status["progress"] while the backfill of a press runs (2026-09-29, Phase G): the live
    pull's own block (`live.set_progress`) kept as it finished, running again under phase
    "backfill" with the backfill's sentence ("Backfilling closes: 3 of 7 days · FX closes"),
    then finished with the pull's final sentence and the backfill's outcome after it. The
    backfill's own counts are under progress["backfill"] ({running, started_at, updated_at,
    days_done, days_total, stage, sentence, outcome?, finished_at?}); the pull's done / total
    (its marks) are left as they were. A block written by a newer pull (another started_at)
    is never overwritten. Nothing is written without a status file. Never raises."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.base: dict = {}
        try:
            from data.bloomberg.live import read_status
            self.base = dict((read_status(db_path) or {}).get("progress") or {})
        except Exception:  # noqa: BLE001
            self.base = {}
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.owner = self.base.get("started_at") or self.started_at
        self.total = self.done = 0
        self.what = ""

    def _ours(self, status: Optional[dict]) -> bool:
        current = (status or {}).get("progress") or {}
        return not current or current.get("started_at") in (None, self.owner)

    def _sentence(self) -> str:
        head = (f"Backfilling closes: {self.done} of {self.total} days" if self.total
                else "Backfilling closes")
        return f"{head} · {self.what}" if self.what else head

    def _publish(self, block: dict) -> None:
        # One read of the status file per publish (2026-09-30; it was read twice, once to check
        # the block is still ours and once by live.set_progress), the check and the write under
        # the status file's own lock, as set_progress does it.
        try:
            from data.bloomberg import live
            with live._STATUS_LOCK:
                status = live.read_status(self.db_path)
                if status is None or not self._ours(status):
                    return
                status[live.PROGRESS_KEY] = block
                live._replace_status_file(live.status_path(self.db_path), status)
        except Exception:  # noqa: BLE001 -- progress is a courtesy, never a failure of the backfill
            pass

    def _running_block(self) -> dict:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        sentence = self._sentence()
        return {**self.base, "started_at": self.owner, "running": True, "phase": "backfill", "step": "backfill",
                "step_label": "past closes", "updated_at": now, "sentence": sentence,
                "backfill": {"running": True, "started_at": self.started_at, "updated_at": now,
                             "days_done": self.done, "days_total": self.total, "stage": self.what,
                             "sentence": sentence}}

    def stage(self, words: str) -> None:
        self.what = str(words or "")
        self._publish(self._running_block())

    def days_left(self, remaining: int) -> None:
        remaining = max(0, int(remaining))
        self.total = max(self.total, remaining)
        self.done = self.total - remaining
        self._publish(self._running_block())

    def finish(self, report: dict, crashed: str = "") -> None:
        count = int(report.get("error_count") or 0)
        refused = int(report.get("not_number_count") or 0)
        if crashed:
            outcome, words = "failed", f"past closes stopped ({crashed})"
        else:
            outcome = "partial" if count else "ok"
            words = (f"past closes: {self.done} of {self.total} days worked" if self.total
                     else "past closes: nothing to ask")
            if count:
                words += f", {count} problem(s) (see the Data tab)"
            if refused:
                words += f", {refused} value(s) not a number left out"
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        pull = str(self.base.get("final_sentence") or self.base.get("sentence") or "")
        block = {**self.base, "started_at": self.owner, "running": False, "phase": "done", "step": "done",
                 "step_label": "", "updated_at": now, "sentence": f"{pull} · {words}" if pull else words,
                 "backfill": {"running": False, "started_at": self.started_at, "updated_at": now, "finished_at": now,
                              "days_done": self.done, "days_total": self.total, "stage": "", "outcome": outcome,
                              "sentence": words}}
        self._publish(block)


def start_auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                        fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                        fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                        scale_fetch: Optional[Callable] = None, quote_fetch: Optional[Callable] = None,
                        session: Optional[Tuple] = None):
    """Run `auto_backfill` in a background daemon thread when a Terminal is available,
    writing progress into the existing Bloomberg status file under key "backfill" so the
    Market data tab can show "Backfill: n days remaining". Without a Terminal, writes
    `{"running": False, "reason": ...}` and does nothing else. Never blocks the caller.
    A second call while one is already running is a no-op (the lock is held for the
    whole run), which is how a feed cycle avoids overlapping with `start`'s own trigger.

    The "backfill" block (2026-09-21): "running" / "remaining" / "reason" as before, plus
      "days": {"<YYYY-MM-DD>": {"status": "DONE" | "NO_CLOSES" | "INCOMPLETE",
                                "missing_count": <int>, "missing": [<plain reason>, ... at most 5]}}
      "last_run": "<ISO timestamp>" of the end of the last run
      "waiting_on_tickers": "" or one sentence on the incomplete days not asked for again
              and why (2026-09-22): how many wait on tickers Bloomberg rejects, those
              tickers named (at most MAX_WAITING_TICKERS), then how many older days got
              nothing more from the history, then how many recent days are retried hourly
      "note": "" or a sentence saying how many past days hold marks that are not the
              daily close (17:00 New York of their date, the one close since 2026-09-28: a
              live pull's row, or a 15:00 row of the retired FX rule) and are being asked
              for again
      "points_scale": {"<pair>": {"field": "FWD_POINTS_SCALE" | "FWD_SCALE" | "",
                                  "divisor": <float or null>, "raw": {...}, "errors": {...}}}
              -- which Bloomberg field gave each pair's forward-points divisor
      "options": {"<YYYY-MM-DD>": {"priced": <int or null>, "skipped": [{"trade_id", "reason"}, ...],
                                   "note": "" | why the step did not run,
                                   "closed_out": [<trade_id>, ...] -- closed-out options, not priced
                                   and not among the skipped (2026-09-22),
                                   "futures_options_priced": <int or null> -- the options on
                                   commodity futures among "priced" (2026-09-24)}}
              -- (2026-09-22) what engine.options.store.price_close made of each worked
              day's FX options from that day's own inputs on file, the days of the last run
              that worked any, newest first, MAX_STATUS_DAYS at most (see backfill())
      "inputs": {"<YYYY-MM-DD>": {"vol_quotes": <int>, "curve_quotes": <int>,
                                  "missing_inputs": [{"kind", "key", "reason"}, ...]}}
              -- (2026-09-22) the smile / curve rows each worked day got from Bloomberg's
              history or could not (see backfill()), likewise. Until 2026-09-24 this was
              "rates", with the swaps' "priced" / "failed" / "note" as well; the swaps'
              re-pricing left with the rates book.
      (2026-09-29, Phase G "Smooth and contained")
      "errors": [{"step", "label", "day", "reason"}, ... at most MAX_STATUS_ERRORS] -- every
              stage, request or day's step of the last run that did not go through, in plain
              words (a request that raised or timed out, one not sent, a step that raised);
              the rest of the run went on. "error_count": how many in all.
      "requests": {"sent", "answered", "failed", "tickers_failed", "tickers_not_asked",
              "gave_up" (TIMEOUTS_BEFORE_GIVING_UP timeouts in a row), "no_session"} -- the
              history requests of the last backfill() call.
      "not_numbers": [{"what", "day", "reason"}, ... at most MAX_STATUS_ERRORS] -- values
              Bloomberg sent that are not numbers ('N.A.', NaN), never written (hard rule 2);
              "not_number_count": how many in all.
      "cache": what is stored and what the last press asked (2026-09-30, user: "bloomberg needs
              to store on cache all the data - and only pull any new data"): see `_cache_block`
              for its keys; "sentence" is the Data tab's one line.
      "risk_history": the Risk tab's daily history step of the last real pull (2026-09-30,
              `risk_history_step`'s block: securities asked, rows written, failures by ticker,
              values not a number, the unrequestable needs with their reasons, "error"); its
              failures are its own, never in "errors" or "reason".
      "seconds": where the press's backfill time went (2026-10-01, read off the Data tab's
              diagnosis report): {"listing" (the past days and what each lacks), "past_closes"
              (the backfill() call: closes, forwards, futures, LME, option inputs and past-close
              pricing; absent when no day was due), "closing" (the closing ledger step, the
              press's one ledger call since 2026-10-01), "bookkeeping" (each worked day's outcome), "risk_history" and
              "snapshot" (a real pull only), "total"}, seconds rounded to 0.01.
      "snapshot": the export's one line; since 2026-10-01 SNAPSHOT_UNCHANGED ("marks snapshot:
              nothing new, snapshot not rewritten") when nothing the snapshot carries changed
              since this process's last export (`_market_fingerprint`), and no export ran.
    "days" holds the header's reference dates and the newest days that are not DONE (a
    day lacking a smile or a curve its FX options need counts as not DONE), so
    the header can say WHY a period is n/a. A "reason" of a run that raised contains the
    word "failed"; so does that of a run in which anything under "errors" failed
    (`failure_sentence`), '' otherwise. live.pull_once rewrites the whole status file
    without this key on every cycle, so every call here publishes the whole remembered
    block again. The top bar's status["progress"] is carried on by `_BackfillProgress`
    while the run lasts ("Backfilling closes: 3 of 7 days · ...").

    `session` (2026-09-30, the pull's speed): the live pull's own `(session, service)`, lent
    for the whole run, which then asks every history request on it instead of opening a
    second session. It is never stopped here. Returns the thread when a run starts (the
    lender stops the session once `thread.join()` returns) and None when none does (a run
    already in flight, or no Terminal): the session is then not used at all. A lent session
    counts as a real pull (the snapshot is saved) and stands in for the availability probe;
    `session_factory` keeps meaning a test's own session (no probe, no snapshot)."""
    import threading
    from data.bloomberg.live import availability, patch_status
    key = _db_key(db_path)

    def _publish(patch: dict) -> None:
        # patch_status does the read-modify-write of just the "backfill" key under the
        # same lock write_status itself takes, so a concurrent full rewrite by the feed
        # thread can neither tear the status file nor be lost between a read here and a
        # write there (2026-09-18) -- this used to read_status/write_status by hand.
        with _publish_lock:
            block = _published.setdefault(key, {})
            block.update(patch)
            patch_status(db_path, "backfill", dict(block))

    real_pull = (session_factory is None and fetch is None and fwd_fetch is None and fut_fetch is None
                 and quote_fetch is None)
    if real_pull and session is None:
        ok, why = availability(host, port)
        if not ok:
            _publish({"running": False, "reason": why})
            return None

    if not _auto_lock.acquire(blocking=False):
        _publish({})  # a run is already in flight; only put its block back after the feed's rewrite
        return None

    def _save() -> None:
        # The saving every pull ends with (user, 2026-09-22: "every pull from bbg triggers the
        # saving" ... "I will trigger the commit and push myself"): the marks on file written
        # to data/bbg_snapshot/, nothing committed (data/bloomberg/snapshot.py::save_after_pull).
        # Only after a REAL pull (a test's fake fetches must never write the repository's own
        # snapshot), and whether or not the backfill itself got through: today's marks are on
        # file either way, and the pull's own marks must never go unsaved over a history error.
        # 2026-10-01: not at all when nothing the snapshot carries changed since this process's
        # last export (`_market_fingerprint`): said as SNAPSHOT_UNCHANGED.
        if not real_pull:
            return
        try:
            from data.bloomberg import snapshot
            fp = None if os.environ.get("RISK_SNAPSHOT", "1") == "0" else _market_fingerprint(db_path)
            if fp is not None and _exported_fp.get(key) == fp:
                _publish({"snapshot": SNAPSHOT_UNCHANGED})
                return
            out = snapshot.save_after_pull(db_path)
            if fp is not None and out.get("exported"):
                _exported_fp[key] = fp
            else:
                _exported_fp.pop(key, None)
            _publish({"snapshot": out["message"]})
        except Exception as exc:  # noqa: BLE001 -- said in the status, never raised into the thread
            _exported_fp.pop(key, None)
            _publish({"snapshot": f"marks snapshot failed: {exc!r}"})

    progress = _BackfillProgress(db_path)

    def _on_progress(remaining: int) -> None:
        _publish({"running": remaining > 0, "remaining": remaining})
        progress.days_left(remaining)

    def _run():
        crashed = ""
        results: List[dict] = []
        risk_ran = False
        run_started = _perf()
        seconds: Dict[str, float] = {}       # where this press's backfill time went (2026-10-01)
        _step_seconds.pop(key, None)
        _ledger_ran.pop(key, None)           # auto_backfill's closing step sets it (2026-10-01)
        try:
            try:
                _publish({"running": True, "reason": ""})
                progress.stage("listing the past days that lack a close")
                # the app's terminal gets the problem lines only, and one summary line below
                # (2026-09-30); the command-line backfill keeps its full table
                results = auto_backfill(db_path, host=host, port=port, fetch=fetch, fwd_fetch=fwd_fetch,
                                        fut_fetch=fut_fetch, session_factory=session_factory,
                                        scale_fetch=scale_fetch, quote_fetch=quote_fetch, log=_QuietLog(),
                                        on_progress=_on_progress, on_stage=progress.stage, session=session) or []
            except Exception as exc:  # never let a background thread take the process down
                crashed = f"auto-backfill failed: {exc!r}"
                _publish({"running": False, "reason": crashed})
            if not _ledger_ran.get(key):
                # auto_backfill stopped before its closing step (it never should): the press's
                # one ledger call is made here, since the live pull left its own out (2026-10-01)
                try:
                    from data.bloomberg.live import book_today
                    _realise_after_backfill(db_path, book_today(), _QuietLog())
                except Exception as exc:  # noqa: BLE001 -- recorded, never raised into the thread
                    _report_error(db_path, "closing", f"the closing ledger step could not run: {_plain_error(exc)}")
            seconds.update(_step_seconds.get(key) or {})
            if real_pull:
                # The Risk tab's daily history (2026-09-30), after the closes and the closing
                # ledger step, before the snapshot: a real pull only, on the lent session when
                # there is one; reported in its own block, never a failure of the backfill.
                progress.stage("the risk history")
                risk_ran = True
                started = _perf()
                try:
                    risk_history_step(db_path, session=session, log=_QuietLog(), host=host, port=port)
                except Exception as exc:  # noqa: BLE001 -- the step never raises; belt and braces
                    _risk_blocks[key] = {"error": f"the risk history step stopped: {_plain_error(exc)}"}
                seconds["risk_history"] = round(_perf() - started, 2)
                progress.stage("saving the marks snapshot")
                started = _perf()
                _save()
                seconds["snapshot"] = round(_perf() - started, 2)
        finally:
            try:
                seconds["total"] = round(_perf() - run_started, 2)
                report = dict(_run_reports.get(key) or _empty_run_report())
                patch = {"running": False, "remaining": 0, "days": _days_block.get(key, {}),
                         "note": _notes.get(key, ""), "points_scale": _scale_reports.get(key, {}),
                         "options": _options_block.get(key, {}), "inputs": _inputs_block.get(key, {}),
                         "waiting_on_tickers": _waiting.get(key, ""),
                         # 2026-09-29 (Phase G): what went wrong, and what the requests did
                         "errors": report["errors"], "error_count": report["error_count"],
                         "requests": report["requests"], "not_numbers": report["not_numbers"],
                         "not_number_count": report["not_number_count"],
                         # 2026-09-30: the Risk tab's history step, its own report
                         "risk_history": _risk_blocks.get(key, {}),
                         # 2026-09-30: what is stored, and what this press asked against what it left alone
                         "cache": _cache_block(db_path, report, _risk_blocks.get(key, {}) if risk_ran else {}),
                         # 2026-10-01: seconds per step of this press (`_run`'s docstring)
                         "seconds": dict(seconds),
                         "last_run": datetime.now().astimezone().isoformat(timespec="seconds")}
                if not crashed:
                    patch["reason"] = failure_sentence(report)
                try:
                    _publish(patch)
                except Exception:  # noqa: BLE001 -- the status file is a report; the lock must still go
                    pass
                progress.finish(report, crashed)
                try:
                    print(_summary_line(results, report, _days_block.get(key), crashed))
                except Exception:  # noqa: BLE001 -- the terminal line is a courtesy
                    pass
            finally:
                _auto_lock.release()

    t = threading.Thread(target=_run, name="bloomberg-auto-backfill", daemon=True)
    t.start()
    return t


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill P&L ledger snapshots from Bloomberg daily closes.")
    parser.add_argument("--db", default=None, help="SQLite path (default: data.paths.get_db_path())")
    parser.add_argument("--start", default=None, help="first day (default: last business day of previous year)")
    parser.add_argument("--end", default=None, help="last day (default: yesterday)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--overwrite", action="store_true", help="recompute days that already have a complete snapshot")
    args = parser.parse_args(argv)
    if args.db is None:
        from data.paths import get_db_path
        args.db = get_db_path()
    from data.bloomberg.live import book_today
    today = book_today()
    start = date.fromisoformat(args.start) if args.start else _last_business_day_of_prev_year(today)
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    from data.bloomberg.live import availability
    ok, why = availability(args.host, args.port)
    if not ok:
        print(f"Bloomberg unavailable: {why}. Nothing written.")
        return 1
    results = backfill(args.db, start, end, host=args.host, port=args.port, overwrite=args.overwrite)
    return 0 if any(r["status"] == "DONE" for r in results) or all(r["status"] == "SKIPPED" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
