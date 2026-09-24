"""Backfill past-close Bloomberg marks history (BUILD_PLAN.md section 3/6, Task B).

`engine/pnl/ledger.py::ltd` recomputes LTD from `marks` on demand. Per CLAUDE.md's "P&L
conventions", Daily/5d/MTD/YTD all difference LTD(t) against LTD(t-1bd) / LTD(t-5bd) /
etc, and every FX leg's LTD needs the FWD_OUTRIGHT for its OWN settle_date (never a single
shared date), every future's needs FUTURE_PX -- SPOT alone is not enough to price a single
past day. For every business day in [start, end] this module writes, as official marks
dated that day:
  - SPOT: the 15:00 New York close (2026-09-21, see "The close" below) of every FX pair
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
    curve_quotes) dated that day under BBG_BDH, stamped at the close. ASSUMPTION: the
    daily PX_LAST is Bloomberg's own close of those quotes; the user's 15:00 New York
    rule was given for FX closes, and the smile and the curve are taken at Bloomberg's
    daily close. A day that already holds a pair's smile or a currency's curve (the live
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
    (`settle_stamp`: 17:00 New York; 2026-09-22). A past day's FUTURE_PX row that a live
    press wrote (PX_LAST or PX_MID, stamped at the press or at 15:00 of the book date) is
    not a close: once the day is past the backfill asks that day's daily PX_LAST and
    replaces it, as it replaces an FX row that is not the 15:00 close. A commodity future
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
    extrapolated) and stamped `settle_stamp` (17:00 New York): a past LME close is the daily
    close like a future's, never the FX 15:00 bar (`is_close_row` with the instrument id).
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

The close (user decision 2026-09-21: "the EOD is 3pm New York time"; "for previous or any
closes in FX, we need to use NY 3pm"; 2026-09-22: "for futures, can use market close, for
fx use new 3pm" -- for EVERY previous close, which replaced the 2026-09-21 cut-over that
applied 15:00 from that day on only). A past FX close -- SPOT, and the tenor series the
forwards are built from -- is Bloomberg's value at 15:00 America/New_York that day, read
from intraday bars (pull_marks.fetch_intraday_close_series: two IntradayBarRequests per
ticker per stretch, BID and ASK, the hourly bar ending 15:00 New York, mid of the two
closes), on every past day Bloomberg's intraday history still reaches (about 140 business
days, `intraday_floor`). A 15:00 bar Bloomberg does not have stays missing, with the
reason, never the daily PX_LAST instead. Only a day beyond the intraday history closes at
Bloomberg's daily close (PX_LAST, 17:00 New York; `first_1500_day` is the floor). Futures
take the daily PX_LAST (the market close), stamped `settle_stamp` (17:00 New York). FX rows are
stamped `close_stamp` (15:00 New York on their date; 17:00 beyond the intraday history).
UNVERIFIED on a terminal.

A day counts as complete (skipped unless overwrite=True) only once ALL of the above are
official for it -- `data.bloomberg.inventory.close_completeness`, the same "needed" set
`data.bloomberg.live.build_requests` uses for the live feed, so the three can never drift
apart. A row already official AT THE CLOSE for its (day, instrument, settle_date,
mark_type) is never rewritten, even when overwrite is False and the day is otherwise
incomplete (e.g. a new trade added a settle_date this day never needed before). A past
day's official FX row that is NOT stamped at the close -- that day's last live pull (say
11:40), or a 17:00 PX_LAST row on a day the intraday history still reaches (written under
the 2026-09-21 cut-over) -- is not a close, nor is a future's row not stamped at the
settlement: the day counts as incomplete and the row is replaced once the close is in hand
(`is_close_row`, `_write_closes`). It is never deleted without its replacement, today's
rows are never touched (intraday = live), and realised_pnl is never touched here: frozen
rows stay frozen (the ledger's own next pass re-freezes a trade whose mark changed). A day
whose marks are all closes but that lacks a smile or a curve its FX options need
(close_completeness's `inputs_missing`) is worked too, for those inputs alone: its rows at
the close are left as they are.

Only if `engine.pnl.ledger.realise_settled` is importable, trades settled before a day are
frozen once that day's marks are on file: every day's SPOT and FUTURE_PX (all a freeze
reads) are written together before anything else, see `backfill`. This module no longer writes a
`pnl_snapshots` row; that table and its "one snapshot per day" model are retired by the
pnl-engine task, which recomputes `ltd(conn, date)` straight from `marks` instead.

Limits, stated plainly:
  - Trades are only those currently in the database (the blotter is the app's only trade
    source; a re-upload replaces the whole book -- see CLAUDE.md's schema notes).
  - FX closes are Bloomberg's 15:00 New York intraday mid (the 17:00 daily close only
    beyond the intraday history), futures and listed options the daily PX_LAST, vol and OIS quotes the daily
    PX_LAST; the live pull's rows are stored under the same official sources, and the
    snapped_at timestamp tells them apart (backfill rows are stamped at the close on their
    date, 17:00 for a settlement; a live row carries the time it was pulled).
  - Calendar is the trading calendar (Monday-Friday less config/holidays.txt, see
    `business_days`): a listed holiday is never asked of Bloomberg. A day Bloomberg
    returns nothing for is still reported as NO_CLOSES.
  - UNVERIFIED on a terminal (docs/open-questions.md items 28 and 30): that the tenor
    tickers quote points (fwd_curve.tenor_unit checks the magnitude against spot
    rather than assume), the points-divisor field (FWD_POINTS_SCALE / FWD_SCALE), and
    the IntradayBarRequest the 15:00 close is read from. Computed tenor dates use
    config/holidays.txt only -- there are no per-currency calendars -- so around a local
    holiday a pillar can sit a day away from Bloomberg's.
  - If `engine.pnl.ledger.realise_settled` cannot be imported (e.g. mid-rewrite by the
    pnl-engine task), marks are still written and the day is reported with
    `realised: None` and a note; nothing is invented and nothing raises.

Usage (Bloomberg PC):  py -3 -m data.bloomberg.backfill [--start YYYY-MM-DD] [--end YYYY-MM-DD]
Default start is the last business day of the previous year (the YTD reference date).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from data.bloomberg import fwd_curve as fc
from data.bloomberg.live import SRC_INTERP, SRC_SPOT_FWD, ledger_block
from data.bloomberg.pull_marks import CLOSE_HOUR_NY, CLOSE_REASON, INTRADAY_HISTORY_BUSINESS_DAYS, SRC_FUTURE
from engine.pnl.aggregate import _last_business_day_of_prev_year
from engine.pnl.calendar import load_holidays

NY = ZoneInfo("America/New_York")

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


# The 15:00 New York close applies to EVERY past day Bloomberg's intraday history still
# reaches (user decision 2026-09-22: "I want to see the ltd line chart, which requires all
# the previous closes, and fixes. For futures, can use market close, for fx use new 3pm";
# it replaced the 2026-09-21 cut-over, `CLOSE_1500_FROM`, under which a day before
# 2026-09-21 kept whatever it had). Only a day beyond the intraday history (about 140
# business days, `intraday_floor`) closes at Bloomberg's daily close (PX_LAST, 17:00 New
# York); no calendar date gates the rule any more.
DAILY_CLOSE_HOUR_NY = 17


def _as_date(today) -> date:
    """`today` as a date: a date as is, an ISO string parsed, None = the New York book date."""
    if today is None:
        from data.bloomberg.live import book_today
        return book_today()
    return today if isinstance(today, date) else date.fromisoformat(str(today))


def first_1500_day(today: Optional[date] = None) -> date:
    """The first day whose FX close is the 15:00 New York value: the oldest day Bloomberg's
    intraday history reaches from `today` (default: the New York book date), `intraday_floor`.
    Every day before it closes at the 17:00 daily close (2026-09-22: no fixed cut-over)."""
    return intraday_floor(_as_date(today))


def close_stamp(day: date, today: Optional[date] = None) -> str:
    """The official close on `day`, with that date's UTC offset resolved: 15:00 New York
    (pull_marks.CLOSE_HOUR_NY) from `first_1500_day(today)` on, Bloomberg's 17:00 daily
    close before it."""
    hour = DAILY_CLOSE_HOUR_NY if day < first_1500_day(today) else CLOSE_HOUR_NY
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=NY).isoformat(timespec="seconds")


# The mark types the 15:00 New York close rule covers (the user said FX closes; a future
# takes its daily PX_LAST, stamped at Bloomberg's daily close, see below).
FX_CLOSE_MARK_TYPES = ("SPOT", "FWD_OUTRIGHT")

# A future's past close is its daily PX_LAST (user, 2026-09-22: "for futures, can use
# market close", then "all futures for past date pnl calculation, use px last": PX_SETTLE
# in between, of which Bloomberg served no history for the listed options), a field
# of Bloomberg's daily history, and that row is stamped at
# Bloomberg's daily close, 17:00 New York, on its date. A live press stamps its FUTURE_PX
# row (PX_LAST, or a listed option's PX_MID) at the press or at 15:00 of the book date
# (pull_marks.build_future_rows), never 17:00, so the stamp alone tells a settlement row
# from a live one: `is_close_row` counts only the former as a close and the backfill
# replaces the latter once the day is past (2026-09-22; until then a future's row always
# counted, so a day's live price stood as its close for good).
SETTLE_HOUR_NY = DAILY_CLOSE_HOUR_NY


def settle_stamp(day: date) -> str:
    """The daily close on `day` a future's or listed option's FUTURE_PX history row is
    stamped with: SETTLE_HOUR_NY:00 New York with that date's UTC offset resolved."""
    return datetime(day.year, day.month, day.day, SETTLE_HOUR_NY, 0, tzinfo=NY).isoformat(timespec="seconds")


def is_lme_instrument(instrument_id: Optional[str]) -> bool:
    """Is `instrument_id` an LME forward's instrument, the metal's contract root id ('LME:CA';
    commodity conversion Phase 5, 2026-09-24)? A SPOT / FWD_OUTRIGHT on a contract root id is
    only ever an LME metal's: an FX pair's id never carries the 'EXCHANGE:' prefix."""
    from data.bloomberg.library import is_contract_root
    return is_contract_root(instrument_id)


def is_close_row(mark_type: str, as_of_date: str, snapped_at: str, today=None, instrument_id: str = "") -> bool:
    """Is this official row of a PAST day a close? An FX row (SPOT / FWD_OUTRIGHT) is a
    close only when it is stamped 15:00 New York of its own as_of_date -- on every past
    day, whichever side of 2026-09-21 (user decision 2026-09-22) -- or, for a day before
    `first_1500_day(today)` (beyond Bloomberg's intraday history, so the 15:00 value can no
    longer be asked for), 17:00 New York, the daily close the backfill writes there. A
    FUTURE_PX row (a future's, or a listed option's on its own ticker) is a close only when
    it is stamped at the settlement (`settle_stamp`, 17:00 New York; 2026-09-22): a live
    press's PX_LAST row of a past day is not. Any other mark type always is. Anything
    else -- a live pull's last price on any day, a
    17:00 FX row on a day still within intraday reach -- is not a close, and the backfill
    asks for the close and replaces it. `today` is a date or ISO string (default: the New
    York book date).

    An LME metal's SPOT (cash) or FWD_OUTRIGHT (3M, a monthly prompt, a ticket's prompt),
    told by `instrument_id` being its root id ('LME:CA', `is_lme_instrument`; 2026-09-24,
    Phase 5), follows the futures' rule, not the FX one: its past close is Bloomberg's daily
    PX_LAST, stamped at the daily close (`settle_stamp`, 17:00 New York) on every past day,
    and a 15:00 row or a live press's is not a close. Without `instrument_id` a SPOT /
    FWD_OUTRIGHT row is read as FX, as before."""
    if mark_type not in FX_CLOSE_MARK_TYPES and mark_type != "FUTURE_PX":
        return True
    try:
        day = date.fromisoformat(as_of_date)
        stamp = datetime.fromisoformat(snapped_at)
    except (TypeError, ValueError):
        return False
    if mark_type == "FUTURE_PX" or (instrument_id and is_lme_instrument(instrument_id)):
        return stamp == datetime(day.year, day.month, day.day, SETTLE_HOUR_NY, 0, tzinfo=NY)
    if stamp == datetime(day.year, day.month, day.day, CLOSE_HOUR_NY, 0, tzinfo=NY):
        return True
    return (stamp == datetime(day.year, day.month, day.day, DAILY_CLOSE_HOUR_NY, 0, tzinfo=NY)
            and day < first_1500_day(today))


def intraday_floor(today: date) -> date:
    """The oldest day whose 15:00 close can still be asked for: INTRADAY_HISTORY_BUSINESS_DAYS
    weekdays before `today` (Bloomberg keeps intraday prices for about that long)."""
    d, left = today, INTRADAY_HISTORY_BUSINESS_DAYS
    while left > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def _close_series_fetch(first_1500: date) -> Callable:
    """The default FX close series, same shape either way: the 15:00 New York close
    (pull_marks.fetch_intraday_close_series) for the days from `first_1500` on, Bloomberg's
    daily close (pull_marks.fetch_historical_series, PX_LAST: one request for every ticker)
    for the days before it. A stretch that straddles `first_1500` is asked for in its two
    parts."""
    from data.bloomberg import pull_marks as pm

    def fetch(session, service, tickers, fields, start, end):
        parts = []
        if start < first_1500:
            parts.append(pm.fetch_historical_series(session, service, tickers, ["PX_LAST"], start,
                                                    min(end, first_1500 - timedelta(days=1))))
        if end >= first_1500:
            parts.append(pm.fetch_intraday_close_series(session, service, tickers, fields, max(start, first_1500), end))
        out: Dict[str, dict] = {}
        for series in parts:
            for ticker, per_day in (series or {}).items():
                out.setdefault(ticker, {}).update(per_day)
        return out
    return fetch


def _close_name(day: date, first_1500: date) -> str:
    return "daily (17:00 New York)" if day < first_1500 else f"{CLOSE_HOUR_NY}:00 New York"


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
                       lacking: Dict[str, set]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{pair: {date_iso: {ticker: PX_LAST}}} -- one `quote_fetch` call (a daily
    HistoricalDataRequest, PX_LAST) per stretch of `runs`, for every vol ticker of every
    pair some day of the stretch lacks a smile for (`lacking`: {day_iso: {pair}}): the
    same tickers the live vol step asks for today (vol_marketdata.vol_ticker over
    VOL_TENORS x VOL_QUOTE_TYPES). A stretch with nothing lacking asks for nothing."""
    from data.bloomberg import vol_marketdata as vm
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for run_start, run_end in runs:
        pairs = _keys_in_run(lacking, run_start, run_end)
        if not pairs:
            continue
        ticker_to_pair = {vm.vol_ticker(pair, tenor, quote_type): pair
                          for pair in pairs for tenor in vm.VOL_TENORS for quote_type in vm.VOL_QUOTE_TYPES}
        series = quote_fetch(session, service, sorted(ticker_to_pair), [vm.VOL_FIELD], run_start, run_end) or {}
        for ticker, per_day in series.items():
            pair = ticker_to_pair.get(ticker)
            if pair is None:
                continue
            for day_iso, row in (per_day or {}).items():
                if isinstance(row, dict) and row.get(vm.VOL_FIELD) is not None:
                    out.setdefault(pair, {}).setdefault(day_iso, {})[ticker] = row[vm.VOL_FIELD]
    return out


def _fetch_ois_history(session, service, runs: List[Tuple[date, date]], quote_fetch: Callable,
                       lacking: Dict[str, set]) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{ccy: {date_iso: {ticker: value}}} -- one `quote_fetch` call per stretch of `runs`
    for every OIS ticker of every currency some day of the stretch lacks a curve for
    (`lacking`: {day_iso: {ccy}}): the same tickers the live rates step asks for today
    (rates_marketdata.ois_curve). Every field the specs name is asked for in the one
    request (PX_LAST throughout the Phase 1 map)."""
    from data.bloomberg import rates_marketdata as rm
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
        series = quote_fetch(session, service, sorted(ticker_to_ccy), sorted(fields), run_start, run_end) or {}
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
                      ois_history: Dict[str, Dict[str, Dict[str, float]]]) -> Tuple[int, int, List[dict]]:
    """Write `d`'s smiles and curves from the fetched history, for the pairs and currencies
    the day lacked: vol_quotes rows through vol_marketdata.write_vol_quotes (the live vol
    step's writer; source BBG_BDH, the history's), curve_quotes rows through
    rates_marketdata.write_curve_quotes (the live rates step's; BBG_BDH; values scaled from
    per cent as it scales them). A smile is written with whatever quotes came back, as the
    live step writes what Bloomberg answers; a curve only with at least
    rates_marketdata._MIN_QUOTES quotes, the live source's own floor, else nothing. Returns
    (vol rows written, curve rows written, missing_inputs: [{kind, key, reason}] for a pair
    or currency the history had nothing usable for)."""
    from data.bloomberg import rates_marketdata as rm
    from data.bloomberg import vol_marketdata as vm
    import decimal
    day = d.isoformat()
    vol_rows = curve_rows = 0
    missing: List[dict] = []
    for pair in sorted(lacking["VOL_SMILE"].get(day, ())):
        values = (vol_history.get(pair) or {}).get(day) or {}
        quotes = []
        for tenor in vm.VOL_TENORS:
            for quote_type in vm.VOL_QUOTE_TYPES:
                ticker = vm.vol_ticker(pair, tenor, quote_type)
                try:
                    value = float(values[ticker])
                except (KeyError, TypeError, ValueError):
                    continue
                quotes.append(vm.VolQuote(tenor=tenor, quote_type=quote_type, ticker=ticker, value=value,
                                          field=vm.VOL_FIELD, source="BBG"))
        if not quotes:
            missing.append({"kind": "VOL_SMILE", "key": pair,
                            "reason": f"Bloomberg returned no vol quotes ({vm.VOL_FIELD}) for {pair} on {day}"})
            continue
        vol_rows += vm.write_vol_quotes(conn, {pair: vm.PairVolSnapshot(pair=pair, as_of=d, quotes=quotes)}, day,
                                        source=SRC_FUTURE)
    for ccy in sorted(lacking["OIS_CURVE"].get(day, ())):
        values = (ois_history.get(ccy) or {}).get(day) or {}
        quotes, failed = [], []
        for spec in rm.ois_curve(ccy):
            try:
                raw = values[spec.ticker]
                value = rm.scale_quote(decimal.Decimal(str(raw)))
            except (KeyError, TypeError, ValueError, decimal.InvalidOperation):
                failed.append(spec.ticker)
                continue
            quotes.append(rm.CurveQuote(tenor=spec.tenor, ticker=spec.ticker, value=value, field=spec.field))
        if len(quotes) < rm._MIN_QUOTES:
            missing.append({"kind": "OIS_CURVE", "key": ccy,
                            "reason": f"fewer than {rm._MIN_QUOTES} OIS quotes for {ccy} on {day}: Bloomberg returned "
                                      f"no value for {', '.join(failed)}"})
            continue
        quotes.sort(key=lambda q: rm.tenor_to_days(q.tenor))
        snap = rm.CurveSnapshot(currency=ccy, index=rm.OIS_INDEX[ccy], as_of=d, quotes=quotes)
        curve_rows += rm.write_curve_quotes(conn, snap, day, source=SRC_FUTURE)
    return vol_rows, curve_rows, missing


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
                                fields: Optional[List[str]] = None) -> Dict[str, Dict[str, Dict[str, dict]]]:
    """{instrument_id: {date_iso: {tenor label: {'PX_LAST': ..., 'SETTLE_DT': ... if sent}}}}
    -- one call of `fwd_fetch` per stretch of `runs`, for the pairs with a leg still to
    settle inside it (the Bloomberg library's FWD_OUTRIGHT rows; a leg settling on or
    before a day is marked at that day's spot and needs no tenor) and the tenors those
    legs need (`_tenors_needed`). The raw rows, not a curve: a day's curve needs that
    day's SPOT close (fwd_curve.historical_curve), which backfill() only has inside its
    day loop. `fwd_fetch(session, service, tickers, fields, start, end) -> {ticker:
    {date_iso: {field: value}}}` defaults to pull_marks.fetch_intraday_close_series, the
    15:00 New York close (2026-09-21), which is asked for `fields` = ['PX_LAST'] alone.

    An injected `fwd_fetch` is still asked for SETTLE_DT too, so Bloomberg's own tenor
    dates are used wherever a source does send them (a daily HistoricalDataRequest does
    not: it is a static reference field); if a request comes back with no PX_LAST at all
    it is sent once more for PX_LAST alone, and the later stretches ask for PX_LAST alone."""
    from data.bloomberg import library
    out: Dict[str, Dict[str, Dict[str, dict]]] = {}
    fields = list(fields) if fields else ["PX_LAST", "SETTLE_DT"]
    for run_start, run_end in runs:
        legs_by_pair: Dict[str, List[dict]] = {}
        for r in library.needed_in_range(conn, run_start.isoformat(), run_end.isoformat()):
            if r["kind"] == "FWD_OUTRIGHT" and r["settle_date"] > run_start.isoformat():
                legs_by_pair.setdefault(r["key"], []).append(r)
        if not legs_by_pair:
            continue
        tenor_map = {pair: _tenor_tickers(pair, _tenors_needed(pair, legs, run_start, holidays))
                     for pair, legs in sorted(legs_by_pair.items())}
        tickers = sorted({t for by_tenor in tenor_map.values() for t in by_tenor.values()})
        if fwd_fetch is None:
            from data.bloomberg.pull_marks import fetch_intraday_close_series
            fwd_fetch, fields = fetch_intraday_close_series, ["PX_LAST"]
        series = fwd_fetch(session, service, tickers, fields, run_start, run_end) or {}
        if len(fields) > 1 and not any("PX_LAST" in row for per_day in series.values() for row in per_day.values()):
            fields = ["PX_LAST"]
            series = fwd_fetch(session, service, tickers, fields, run_start, run_end) or {}
        for pair, by_tenor in tenor_map.items():
            by_day = out.setdefault(pair, {})
            for tenor, ticker in by_tenor.items():
                for day_iso, row in (series.get(ticker) or {}).items():
                    by_day.setdefault(day_iso, {})[tenor] = row
    return out


def _fetch_points_scales(session, service, pairs: List[str], scale_fetch: Optional[Callable] = None) -> Dict[str, dict]:
    """{pair: scale report} -- the divisor that turns a pair's forward points into an
    outright, from the one helper the live tenor path uses too
    (pull_marks.fetch_points_scales: FWD_POINTS_SCALE as is, else 10 ** FWD_SCALE, both
    fields in ONE ReferenceDataRequest for every pair). A report is {'scale': divisor or
    None, 'field': the field that answered, 'raw', 'errors'}. Only asked for when a tenor
    series turns out to be points. `scale_fetch(session, service, tickers, fields) ->
    {ticker: {field: value}}` replaces the real request; with neither it nor a session
    there is no scale, and the forward is reported missing with that reason. A pair
    Bloomberg sends no scale for has none -- never a hard-coded pip size."""
    from data.bloomberg.pull_marks import fetch_points_scales
    if scale_fetch is None and session is None:
        return {}
    try:
        return fetch_points_scales(session, service, pairs, fetch=scale_fetch)
    except Exception:  # noqa: BLE001 -- a failed lookup is "no scale", reported per forward, not a dead run
        return {}


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
    import math
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
                             not_asked: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, float]]:
    """{instrument_id: {date_iso: PX_LAST}} -- one HistoricalDataRequest per stretch of
    `runs`, for the futures and listed options open inside it (the Bloomberg library's
    FUTURE_PX rows), asked for the daily PX_LAST (user, 2026-09-22: "all futures for past
    date pnl calculation, use px last"; it was PX_SETTLE, which Bloomberg served no history
    of for the listed options; user, 2026-09-24: a commodity future's close stays
    PX_LAST). The security asked is `_future_request_tickers`' (a commodity contract under
    its name on `today`, the request day); a row it will not ask for is added to
    `not_asked` {instrument_id: reason} when given. `fut_fetch` mirrors
    `_fetch_fwd_outright_history`'s `fwd_fetch`; defaults to
    pull_marks.fetch_historical_series."""
    from data.bloomberg import library
    today = today or _as_date(None)
    out: Dict[str, Dict[str, float]] = {}
    for run_start, run_end in runs:
        ticker_to_instrument, skipped = _future_request_tickers(
            conn, library.needed_in_range(conn, run_start.isoformat(), run_end.isoformat()), today)
        if not_asked is not None:
            not_asked.update(skipped)
        if not ticker_to_instrument:
            continue
        if fut_fetch is None:
            from data.bloomberg.pull_marks import fetch_historical_series
            fut_fetch = fetch_historical_series
        series = fut_fetch(session, service, sorted(ticker_to_instrument), ["PX_LAST"], run_start, run_end) or {}
        for ticker, per_day in series.items():
            instrument_id = ticker_to_instrument.get(ticker)
            if instrument_id is None:
                continue
            out.setdefault(instrument_id, {}).update(
                {day: row["PX_LAST"] for day, row in per_day.items() if "PX_LAST" in row})
    return out


# --------------------------------------------------------------------------- LME curves (Phase 5, 2026-09-24)
# A past LME close is Bloomberg's daily PX_LAST of the metal's curve pillars (cash, 3M, the
# monthly prompts), stamped at the daily close (`settle_stamp`, 17:00 New York), like a
# future's (housekeeper, 2026-09-24, on the user's own rule for listed instruments): not the
# FX 15:00 bar. The rows are built by bbg-curves' pure helper, fwd_curve.lme_history_marks:
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
                       lme_fetch: Callable) -> Dict[str, Dict[str, float]]:
    """{ticker: {date_iso: PX_LAST}} -- one `lme_fetch` call (Bloomberg's daily history,
    PX_LAST: the futures' fetcher) per stretch of `runs`, for every pillar ticker some day of
    the stretch needs (`plans`: {day_iso: _lme_plan}). A stretch with no LME ticket open asks
    for nothing."""
    out: Dict[str, Dict[str, float]] = {}
    for run_start, run_end in runs:
        tickers = sorted({p["ticker"] for day_iso, plan in plans.items()
                          if run_start <= date.fromisoformat(day_iso) <= run_end
                          for entry in plan.values() for p in entry["pillars"] if p.get("ticker")})
        if not tickers:
            continue
        series = lme_fetch(session, service, tickers, ["PX_LAST"], run_start, run_end) or {}
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
                  needed: List[dict], today: str) -> Tuple[List[dict], List[dict]]:
    """(mark rows, missing_marks) of `d`'s LME curves: per metal of `plan`, the day's pillar
    closes from `series` handed to fwd_curve.lme_history_marks(root_id, day: date, pillars,
    closes {ticker: float}, open_prompts [date], snapped_at = settle_stamp(d)), which returns
    (rows, reasons). The rows are written as the helper builds them, never re-sourced. Every LME mark `needed` names (the cash SPOT, a ticket's prompt
    FWD_OUTRIGHT) that the helper did not produce, and that is not already official at the
    close, is listed missing with the helper's reasons for that metal, or Bloomberg's silence
    when it gave none: never dropped silently."""
    day = d.isoformat()
    helper = getattr(fc, "lme_history_marks", None)
    rows: List[dict] = []
    reasons_by_root: Dict[str, List[str]] = {}
    for root_id, entry in plan.items():
        closes = {}
        for p in entry["pillars"]:
            try:
                closes[p["ticker"]] = float((series.get(p["ticker"]) or {})[day])
            except (KeyError, TypeError, ValueError):
                continue
        if helper is None:
            reasons_by_root[root_id] = [LME_MARKS_UNAVAILABLE]
            continue
        try:
            # written as they come (the helper's sources and keys: cash SPOT BBG_BFXFORWARD keyed
            # on the day, pillars and prompts FWD_OUTRIGHT; the P&L reads them only so), never re-sourced
            made, reasons = helper(root_id, d, entry["pillars"], closes,
                                   [date.fromisoformat(x) for x in entry["prompts"]], settle_stamp(d))
        except Exception as exc:  # noqa: BLE001 -- another lane's helper: its failure is this metal's reason
            made, reasons = [], [f"LME curve of {root_id} on {day}: fwd_curve.lme_history_marks raised {exc!r}"]
        rows += [dict(r) for r in (made or [])]
        reasons_by_root[root_id] = [_reason_text(x) for x in (reasons or [])]
    made_keys = {(r["instrument_id"], r["settle_date"], r["mark_type"]) for r in rows}
    missing: List[dict] = []
    for item in needed:
        root_id = item["instrument_id"]
        if item["mark_type"] not in FX_CLOSE_MARK_TYPES or not is_lme_instrument(root_id):
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

    2026-09-21: a PAST day's official FX row that is not stamped at the 15:00 New York
    close (`is_close_row`; on a day within Bloomberg's intraday reach a 17:00 PX_LAST row
    is not one either, 2026-09-22) is that day's last live pull or an old daily-close row,
    not a close, so the new row is kept and `_write_closes` replaces the old one. A row
    dated `today` (default: the New York book date) or later is live and is never replaced."""
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


def _write_closes(conn: sqlite3.Connection, rows: List[dict], overwrite: bool = False, today: Optional[str] = None) -> int:
    """Write the backfill's rows: those whose key already holds a close are left out
    (`_drop_already_official`) unless `overwrite`; for an FX row that is written, the
    same key's rows that are NOT the close (BBG_BFXFORWARD or BBG_INTERP, stamped at any
    other time) are deleted in the same transaction. Without that a stale direct-quote
    row would go on beating the new BBG_INTERP close in marks_official (a direct quote
    wins over an interpolated row for the same key). Nothing is deleted unless its
    replacement is being written, and only `marks` is touched, never realised_pnl."""
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    keep = rows if overwrite else _drop_already_official(conn, rows, today)
    known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    written = 0
    with conn:                                   # the delete and its replacement commit together
        for r in keep:
            if r["instrument_id"] not in known:  # as live.write_marks: only known instruments
                continue
            key = (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"])
            if r["mark_type"] in FX_CLOSE_MARK_TYPES and r["as_of_date"] < today:
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


def _spot_fetch_from_series(series_fetch: Callable, conn: sqlite3.Connection, runs: List[Tuple[date, date]]) -> Callable:
    """A per-day SPOT fetch (the `fetch` signature backfill() takes) answered from one
    `series_fetch` call per stretch of `runs` (pull_marks.fetch_intraday_close_series: the
    15:00 New York close), for the pairs whose SPOT is needed inside that stretch (the
    Bloomberg library's SPOT rows), sent the first time one of its days is asked for.
    `fetch.reason(ticker, day)` is the source's own reason a day has no close ('' when it
    gave none), so a missing close is reported in Bloomberg's words."""
    from data.bloomberg import library
    cache: Dict[Tuple[date, date], dict] = {}

    def _row(ticker, day) -> dict:
        run = next(((a, b) for a, b in runs if a <= day <= b), None)
        return ((cache.get(run) or {}).get(ticker) or {}).get(day.isoformat()) or {}

    def fetch(session, service, wanted, field, day):
        run = next(((a, b) for a, b in runs if a <= day <= b), None)
        if run is None:
            return {}
        if run not in cache:
            known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
            tickers = sorted({r["bbg_ticker"] for r in library.needed_in_range(conn, run[0].isoformat(), run[1].isoformat())
                              if r["kind"] == "SPOT" and r["key"] in known})
            cache[run] = (series_fetch(session, service, tickers, [field], run[0], run[1]) or {}) if tickers else {}
        return {t: _row(t, day).get(field) for t in wanted}

    fetch.reason = lambda ticker, day: str(_row(ticker, day).get(CLOSE_REASON) or "")
    return fetch


# The keys every day's result carries for the inputs and pricing steps (2026-09-22), whatever
# its status: what price_close made of the day, and the smiles / curves written for it or
# found missing. (The swaps' rates_priced / rates_failed / rates_note left 2026-09-24.)
# 2026-09-24 (Phase 5): `futures_options_priced`, the options on commodity futures among
# `options_priced`; `lme_marks`, the LME curve rows built for the day (cash, pillars, prompts).
_STEP_KEYS = {"options_priced": None, "options_skipped": [], "options_note": "", "options_closed_out": [],
              "futures_options_priced": None, "vol_quotes": 0, "curve_quotes": 0, "missing_inputs": [],
              "lme_marks": 0}


def _step_keys() -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in _STEP_KEYS.items()}


def _skipped(day: str) -> dict:
    return {"day": day, "status": "SKIPPED", "closes": 0, "fwd_outrights": 0, "future_px": 0,
            "missing_pairs": [], "missing_pair_reasons": {}, "missing_marks": [], "realised": 0, "unrealisable": [],
            **_step_keys()}


# db -> {pair: scale report} of the last backfill() that needed a points divisor; published
# under the status file's "backfill" block so a paste says which field answered.
_scale_reports: Dict[str, dict] = {}


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             fwd_fetch: Optional[Callable] = None, fut_fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print,
             scale_fetch: Optional[Callable] = None, order: Optional[List[date]] = None,
             on_day: Optional[Callable[[dict], None]] = None, today: Optional[date] = None,
             quote_fetch: Optional[Callable] = None) -> List[dict]:
    """Run the backfill. Returns one dict per business day of [start, end], in date order:
    {day, status: DONE|SKIPPED|NO_CLOSES|ERROR, closes, fwd_outrights, future_px,
    missing_pairs, missing_pair_reasons, missing_marks, realised, unrealisable,
    options_priced, options_skipped, options_note, options_closed_out, vol_quotes,
    curve_quotes, missing_inputs}.
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

    The close (2026-09-21, every past day from 2026-09-22): FX closes are the 15:00 New
    York value from intraday bars, see the module docstring. A day older than Bloomberg's
    intraday history (`intraday_floor(today)`, `today` defaulting to the New York book
    date) is the one case that takes Bloomberg's daily close (PX_LAST, stamped 17:00) when
    the default fetchers are in use; its futures run as on any day.

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
    once, after the last day. `on_day(result)` is called as each day finishes.

    `fetch(session, service, tickers, field, day) -> {ticker: value|None}` (SPOT, per
    day; the field is still called PX_LAST) defaults to pull_marks.fetch_intraday_close_series
    per stretch (`_spot_fetch_from_series`). `fwd_fetch`/`fut_fetch` (both `(session,
    service, tickers, fields, start, end) -> {ticker: {date_iso: {field: value}}}`, ONE
    call per stretch, 2026-09-18) default to pull_marks.fetch_intraday_close_series (the
    tenor series) and pull_marks.fetch_historical_series (futures' and listed options'
    daily PX_LAST). `quote_fetch` (same shape; the vol and OIS quote history, one call per kind
    per stretch, 2026-09-22) defaults to pull_marks.fetch_historical_series when this call
    has a session, else to `fut_fetch` (a test that injects the three fetchers and no
    session has no daily-history fetcher but that one). `scale_fetch`: see
    `_fetch_points_scales`. `session_factory` defaults to pull_marks.open_session. All are
    injectable so the loop is testable without blpapi."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness, _needed_marks
    from data.bloomberg.live import _ensure_fx_instruments, book_today
    from data.bloomberg import pull_marks as pm
    realise_settled = _import_realise_settled()
    price_close = _import_price_close()
    today = today or book_today()
    today_iso = today.isoformat()
    first_1500 = first_1500_day(today)   # the intraday floor: days before it close at Bloomberg's daily close
    conn = connect(Path(db_path))
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
        completeness = close_completeness(conn, start.isoformat(), end.isoformat(), today=today_iso)
        complete_by_day = dict(zip(completeness["as_of_date"], completeness["complete"]))
        inputs_by_day = dict(zip(completeness["as_of_date"], completeness["inputs_missing"]))
        todo = [d for d in days if overwrite or not complete_by_day.get(d.isoformat(), False)
                or inputs_by_day.get(d.isoformat())]
        if order is not None:
            todo_set = set(todo)
            work = [d for d in dict.fromkeys(order) if d in todo_set]
        else:
            work = todo
        log(f"Backfill {start} .. {end}: {len(days)} business days, {len(work)} to compute, {len(pairs)} pairs "
            f"(SPOT + FWD_OUTRIGHT + FUTURE_PX).")
        if realise_settled is None:
            log("  note: engine.pnl.ledger.realise_settled not importable; marks only, no realisation this run.")
        if price_close is None:
            log("  note: engine.options.store.price_close not importable; no past day's FX options are priced this run.")
        if not work:
            return [_skipped(d.isoformat()) for d in days]
        span_end = max(work)
        runs = _runs(work)
        fx_runs = runs
        n_daily = sum(1 for d in work if d < first_1500)
        if n_daily:
            log(f"  {n_daily} day(s) before {first_1500} are beyond Bloomberg's intraday history and take "
                f"Bloomberg's daily close (17:00 New York); the {CLOSE_HOUR_NY}:00 New York close applies from "
                f"{first_1500} on.")
        session = service = None
        own_session = False  # did THIS call open the session itself (pm.open_session)?
        fwd_fields = None    # an injected fwd_fetch is asked for PX_LAST + SETTLE_DT, as before
        if fetch is None or fwd_fetch is None or fut_fetch is None:
            if fetch is None:
                fetch = _spot_fetch_from_series(_close_series_fetch(first_1500), conn, fx_runs)
            if fwd_fetch is None:
                fwd_fetch, fwd_fields = _close_series_fetch(first_1500), ["PX_LAST"]
            if fut_fetch is None:
                fut_fetch = pm.fetch_historical_series
            if session_factory is None:
                session, service = pm.open_session(host, port)
                own_session = True
            else:
                session, service = session_factory()
        elif session_factory is not None:
            session, service = session_factory()
        if quote_fetch is None:
            quote_fetch = pm.fetch_historical_series if (own_session or session_factory is not None) else fut_fetch

        try:
            # FWD_OUTRIGHT / FUTURE_PX history: one request per kind per stretch of days
            # being worked (`_runs`), never one per day per ticker, and never a day, a
            # pair or a tenor the book did not need (2026-09-21).
            holidays = load_holidays()
            tenor_rows_by_pair = _fetch_fwd_outright_history(conn, session, service, fx_runs, fwd_fetch, holidays,
                                                             fields=fwd_fields)
            future_not_asked: Dict[str, str] = {}     # instrument_id -> why no history was asked for it
            future_px_by_instrument = _fetch_future_px_history(conn, session, service, runs, fut_fetch, today,
                                                               future_not_asked)
            # LME curves (2026-09-24): each day's pillars, their daily PX_LAST one request per
            # stretch with the futures' fetcher; the rows are built in pass 1 below.
            lme_plans = {d.isoformat(): plan for d in work if (plan := _lme_plan(lme_rows, d.isoformat()))}
            lme_series = _fetch_lme_history(session, service, runs, lme_plans, fut_fetch) if lme_plans else {}
            # The smiles and curves the days' FX options price from (2026-09-22):
            # only the pairs and currencies some day of the stretch lacks, one request per
            # kind per stretch, from Bloomberg's daily history.
            lacking = _lacking_inputs(conn, work)
            vol_history = _fetch_vol_history(session, service, runs, quote_fetch, lacking["VOL_SMILE"])
            ois_history = _fetch_ois_history(session, service, runs, quote_fetch, lacking["OIS_CURVE"])
            scales: Dict[str, Dict[str, dict]] = {}

            def scale_report_for(pair: str) -> dict:
                if "by_pair" not in scales:      # asked for once, and only if some series is points
                    scales["by_pair"] = _fetch_points_scales(session, service, sorted(tenor_rows_by_pair), scale_fetch)
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

            # ---- pass 1: SPOT + FUTURE_PX of every day, written together (see docstring)
            prepared: Dict[date, dict] = {}
            first_rows: List[dict] = []
            for d in work:
                day = d.isoformat()
                # Every mark this day actually needs (inventory._needed_marks -- the same
                # set live.build_requests and close_completeness use, so all three can
                # never drift apart). historical=True: what a PAST close needs -- for an
                # FX option that is closing SPOT only (pair + USD-conversion pairs,
                # covered by spot_rows), never a forward at its expiry.
                needed = _needed_marks(conn, day, historical=True)
                # Only the pairs THIS day needs a close for (2026-09-21); it was every pair
                # of the whole range, on every day.
                tickers = [ticker_of[i["instrument_id"]] for i in needed
                           if i["mark_type"] == "SPOT" and i["instrument_id"] in ticker_of]
                closes = (fetch(session, service, tickers, "PX_LAST", d) or {}) if tickers else {}
                spot_rows, spot_by_pair, missing_pairs, pair_reasons = [], {}, [], {}
                for ticker in tickers:
                    value = closes.get(ticker)
                    try:
                        fvalue = float(value)
                    except (TypeError, ValueError):
                        pair = by_ticker[ticker]
                        missing_pairs.append(pair)
                        pair_reasons[pair] = ((spot_reason(ticker, d) if spot_reason else "")
                                              or f"Bloomberg returned no {_close_name(d, first_1500)} close for {pair} on {day}")
                        continue
                    spot_by_pair[by_ticker[ticker]] = fvalue
                    spot_rows.append({"as_of_date": day, "instrument_id": by_ticker[ticker], "settle_date": day,
                                      "mark_type": "SPOT", "value": fvalue, "source": SRC_SPOT_FWD,
                                      "snapped_at": close_stamp(d, today)})
                if tickers and not spot_rows:
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
                    try:
                        settle_value = float(future_px_by_instrument.get(instrument_id, {}).get(day))
                    except (TypeError, ValueError):
                        missing_marks.append({**item, "reason": future_not_asked.get(instrument_id)
                                              or f"Bloomberg returned no PX_LAST for {instrument_id} on {day}"})
                        continue
                    fut_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": item["settle_date"],
                                     "mark_type": "FUTURE_PX", "value": settle_value, "source": SRC_FUTURE,
                                     "snapped_at": settle_stamp(d)})
                # The LME curves (2026-09-24): cash SPOT, pillars and the open prompts, at the
                # daily close. In this pass, with the futures, because an LME ticket freezes
                # at the metal's last cash price on or before its prompt.
                lme_day_rows, lme_missing = _lme_day_rows(conn, d, lme_plans.get(day, {}), lme_series, needed,
                                                          today_iso)
                missing_marks += lme_missing
                prepared[d] = {"no_closes": False, "needed": needed, "spot_rows": spot_rows, "spot_by_pair": spot_by_pair,
                               "missing_pairs": missing_pairs, "pair_reasons": pair_reasons, "fut_rows": fut_rows,
                               "lme_rows": lme_day_rows, "missing_marks": missing_marks}
                first_rows += spot_rows + fut_rows + lme_day_rows
            # A row already official AT THE CLOSE is never rewritten; a past day's FX row
            # that is not the close is replaced (_write_closes).
            _write_closes(conn, first_rows, overwrite, today_iso)

            # ---- pass 2: FWD_OUTRIGHT day by day, in the caller's order
            results: Dict[date, dict] = {}
            for d in work:
                day, p = d.isoformat(), prepared[d]
                if p["no_closes"]:
                    log(f"  {day}  NO_CLOSES  (holiday or Bloomberg returned nothing; nothing written)")
                    results[d] = {"day": day, "status": "NO_CLOSES", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                                  "missing_pairs": p["missing_pairs"], "missing_pair_reasons": p["pair_reasons"],
                                  "missing_marks": [], "realised": None, "unrealisable": [], **_step_keys()}
                    if on_day:
                        on_day(results[d])
                    continue
                try:
                    spot_by_pair, missing_marks = p["spot_by_pair"], p["missing_marks"]
                    fwd_rows, curves = [], {}
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
                                             "snapped_at": close_stamp(d, today)})
                            continue
                        if instrument_id not in curves:
                            rows = tenor_rows_by_pair.get(instrument_id, {}).get(day, {})
                            curve = fc.historical_curve(d, rows, spot, None, instrument_id, holidays)
                            if curve["unit"] == fc.UNIT_POINTS and not curve["points"]:
                                report = scale_report_for(instrument_id)
                                curve = fc.historical_curve(d, rows, spot, report.get("scale"), instrument_id, holidays)
                                if not curve["points"] and not report.get("scale"):
                                    # neither field answered: say so, with what each one sent
                                    curve["reason"] = pm.describe_scale(instrument_id, report)
                            elif not curve["unit"] and spot is not None:
                                # no tenor value at all that day: the source's own reason, if it gave one
                                said = next((row[CLOSE_REASON] for row in rows.values()
                                             if isinstance(row, dict) and row.get(CLOSE_REASON)), "")
                                curve["reason"] = said or curve["reason"]
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
                                         "mark_type": "FWD_OUTRIGHT", "value": float(value),
                                         "source": SRC_SPOT_FWD if direct else SRC_INTERP, "snapped_at": close_stamp(d, today)})
                    _write_closes(conn, fwd_rows, overwrite, today_iso)
                    # The smiles and curves the day lacked, from the history (2026-09-22),
                    # then the day's FX options from the inputs the day now has on file --
                    # after its closes, before the freeze below, which takes the expiry
                    # day's premium.
                    vol_written, curve_written, missing_inputs = _write_day_inputs(conn, d, lacking, vol_history,
                                                                                   ois_history)
                    (options_priced, options_skipped, options_note, options_closed_out,
                     futures_options_priced) = _price_options_close(conn, day, price_close)
                    realised, unrealisable, flag = None, [], "realisation after the last day"
                    ledger = ledger_block(None)          # what the ledger did on this day, re-freeze included
                    if realise_settled is None:
                        flag = "no realisation (realise_settled unavailable)"
                    elif order is None:
                        try:
                            led = realise_settled(conn, day)
                            ledger = ledger_block(led, day)
                            realised, unrealisable = ledger["realised"], ledger["unrealisable"]
                            flag = "complete" if not unrealisable else f"unrealisable={[u['trade_id'] for u in unrealisable]}"
                            if ledger["refrozen_summary"]:
                                flag += f"  {ledger['refrozen_summary']}"
                        except Exception as exc:  # engine/pnl/ledger.py is owned by another task; never let its
                            # in-progress state stop marks from being written -- report and move on.
                            flag = f"realise_settled raised: {exc!r}"
                    log(f"  {day}  DONE  closes={len(p['spot_rows'])}  fwd_outrights={len(fwd_rows)}  "
                        f"future_px={len(p['fut_rows'])}"
                        + (f"  lme={len(p['lme_rows'])}" if p["lme_rows"] else "")
                        + f"  missing={len(p['missing_pairs']) + len(missing_marks)}"
                        f"  vol_quotes={vol_written}  curve_quotes={curve_written}"
                        + (f"  missing_inputs={len(missing_inputs)}" if missing_inputs else "")
                        + f"  options={options_priced}"
                        + (f"  futures_options={futures_options_priced}" if futures_options_priced else "")
                        + (f"  options_skipped={len(options_skipped)}" if options_skipped else "")
                        + (f"  options_closed_out={len(options_closed_out)}" if options_closed_out else "")
                        + (f"  {options_note}" if options_note else "")
                        + f"  realised={realised}  {flag}")
                    results[d] = {"day": day, "status": "DONE", "closes": len(p["spot_rows"]),
                                  "fwd_outrights": len(fwd_rows), "future_px": len(p["fut_rows"]),
                                  "missing_pairs": p["missing_pairs"], "missing_pair_reasons": p["pair_reasons"],
                                  "missing_marks": missing_marks, "realised": realised, "unrealisable": unrealisable,
                                  "refrozen": ledger["refrozen"], "kept": ledger["kept"],
                                  "refrozen_count": ledger["refrozen_count"],
                                  "refrozen_summary": ledger["refrozen_summary"],
                                  "options_priced": options_priced, "options_skipped": options_skipped,
                                  "options_note": options_note, "options_closed_out": options_closed_out,
                                  "futures_options_priced": futures_options_priced,
                                  "vol_quotes": vol_written,
                                  "curve_quotes": curve_written, "missing_inputs": missing_inputs,
                                  "lme_marks": len(p["lme_rows"])}
                except Exception as exc:  # noqa: BLE001 -- one bad day must not end the run for the others
                    log(f"  {day}  ERROR  {exc!r}")
                    results[d] = {"day": day, "status": "ERROR", "closes": len(p["spot_rows"]), "fwd_outrights": 0,
                                  "future_px": len(p["fut_rows"]), "missing_pairs": p["missing_pairs"],
                                  "missing_pair_reasons": p["pair_reasons"],
                                  "missing_marks": p["missing_marks"], "realised": None, "unrealisable": [],
                                  **_step_keys(), "lme_marks": len(p["lme_rows"]), "error": repr(exc)}
                if on_day:
                    on_day(results[d])
            if order is not None and realise_settled is not None:
                try:
                    led = realise_settled(conn, span_end.isoformat())
                    ledger = _record_ledger(db_path, "after_last_day", ledger_block(led, span_end.isoformat()))
                    log(f"  realised after the last day: {ledger['realised']}"
                        + (f"  {ledger['refrozen_summary']}" if ledger["refrozen_summary"] else ""))
                except Exception as exc:  # noqa: BLE001 -- as above: report and move on
                    log(f"  realise_settled raised: {exc!r}")
            if "by_pair" in scales:
                # Which field gave each pair's points divisor (or that neither did): in the
                # log, and kept for the status file's "backfill" block.
                _scale_reports[_db_key(db_path)] = {
                    pair: {"field": r.get("field") or "", "divisor": r.get("scale"), "raw": r.get("raw") or {},
                           "errors": r.get("errors") or {}} for pair, r in sorted(scales["by_pair"].items())}
                for pair, r in sorted(scales["by_pair"].items()):
                    log("  points divisor  " + pm.describe_scale(pair, r))
            log(f"Finished: {sum(1 for r in results.values() if r['status'] == 'DONE')} days written, "
                f"{sum(1 for r in results.values() if r['status'] == 'NO_CLOSES')} with no closes, "
                f"{len(days) - len(results)} skipped.")
            return [results.get(d) or _skipped(d.isoformat()) for d in days]
        finally:
            # 2026-09-18 fix: a session THIS call opened itself (pm.open_session, not an
            # injected session_factory the caller controls) was leaked. Never stop a
            # caller-supplied session.
            if own_session and session is not None:
                try:
                    session.stop()
                except Exception:  # noqa: BLE001 -- never let cleanup mask the real result/exception
                    pass
    finally:
        conn.close()


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
# (a 15:00 bar or a settlement can still appear for a day that just turned past); an older
# day is asked again only when what it lacks changes (a new upload) or the code's ticker
# rules change (`state_version`), never because an hour passed -- on the Bloomberg PC 43
# past days that could never complete (a few tickers Bloomberg rejects or has nothing for)
# were re-asked on every press after a restart, some 20 minutes of requests for nothing.
RECENT_BUSINESS_DAYS = 3
# Bloomberg's own rejection of a ticker or field, as the pull code passes it through
# verbatim (pull_marks.fetch_intraday_close_series: "Bloomberg answered the intraday
# request for <ticker> with: Unknown/Invalid security [nid:11150]"; fwd_curve / live:
# "securityError: ..." / "<field>: Field not valid"). Matched case-insensitively. A day
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
_notes: Dict[str, str] = {}                    # db -> the last run's "note" (past days being re-requested at 15:00)
_waiting: Dict[str, str] = {}                  # db -> the last run's "waiting_on_tickers" sentence (2026-09-22)


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
    pillar tickers joined it the same day, Phase 5.) A day's state
    stamped by any other value counts as never tried, so a corrected ticker is asked for
    on the next press: the stamp resets when any of those constants or functions changes,
    and only then."""
    import hashlib
    from data.bloomberg import rates_marketdata as rm
    from data.bloomberg import vol_marketdata as vm
    from data.bloomberg.library import LIBRARY_VERSION
    from data.bloomberg.pull_marks import STANDARD_TENORS
    rules = {"tenors": {"USDJPY": _tenor_tickers("USDJPY", list(STANDARD_TENORS))},
             "future_px_field": "PX_LAST",
             "vol": [vm.vol_ticker("USDJPY", t, q) for t in vm.VOL_TENORS for q in vm.VOL_QUOTE_TYPES],
             "ois": {ccy: [(spec.ticker, spec.field) for spec in rm.ois_curve(ccy)] for ccy in sorted(rm.OIS_INDEX)}}
    # 2026-09-24 (Phase 5): the LME curve's pillar tickers and the close they are asked at.
    try:
        from engine.lme import lme_curve_tickers
        rules["lme"] = {"close": "PX_LAST 17:00", "LME:CA": [p["ticker"] for p in lme_curve_tickers("LME:CA", "2026-01-05")]}
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


_REQUEST_FOR_RE = re.compile(r"request for (.+?) with:")
_RETURNED_NO_RE = re.compile(r"returned no (.+?) for (.+?)(?: on \d{4}-\d{2}-\d{2}|$)")


def _reason_label(reason: str) -> str:
    """The ticker a failure reason is about, for the "waiting_on_tickers" sentence:
    'USDJPYSP Curncy' from the intraday wording, 'PX_LAST of CLZ26 Comdty' from a
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
    reasons += [pair_reasons.get(pair) or f"Bloomberg returned no {CLOSE_HOUR_NY}:00 New York closing SPOT for {pair}"
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
                "rejected_only": bool(entry.get("rejected_only"))}
        except (KeyError, TypeError, ValueError):
            continue                                   # one bad entry: that day counts as never tried


def _save_state(db_path, key: str) -> None:
    """Write `_day_state` for `key` to the sidecar (temp file + os.replace). Never raises:
    a state that cannot be saved only costs the next restart a retry."""
    days = {day: {"tried_at": s["tried_at"], "version": s["version"],
                  "signature": sorted(list(item) for item in s["signature"]), "status": s["status"],
                  "missing_count": s["missing_count"], "missing": list(s["missing"]),
                  "rejected": list(s["rejected"]), "rejected_only": bool(s["rejected_only"])}
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
        reasons.append(f"Bloomberg returned no {CLOSE_HOUR_NY}:00 New York FX close for this day (a holiday, or no data)")
        reasons += [pair_reasons[pair] for pair in result["missing_pairs"] if pair_reasons.get(pair)][:1]
    else:
        if result.get("error"):
            reasons.append(f"this day's run raised {result['error']}")
        reasons += [pair_reasons.get(pair) or f"Bloomberg returned no {CLOSE_HOUR_NY}:00 New York closing SPOT for {pair}"
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
    refrozen_summary) summed over the run's calls -- `backfill()`'s call after the last
    worked day (step "after_last_day", which is where a re-freeze at a past close lands)
    and the closing step (`_realise_after_backfill`, "closing") -- with each call's own
    block under "steps". The after-last-day call, the run's first, starts the block
    afresh; the closing step merges into it unless the block already holds a closing (a
    run with no due days makes no after-last-day call), so a stale run never shows.
    Returns the step's block."""
    from data.bloomberg.live import patch_status, refrozen_summary
    key = _db_key(db_path)
    with _publish_lock:
        published = _published.setdefault(key, {})
        current = published.get("ledger") if step == "closing" else None
        if current and any(s.get("step") == "closing" for s in current.get("steps") or []):
            current = None
        steps = list((current or {}).get("steps") or []) + [{"step": step, **block}]
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
    """The backfill's closing step: the ledger's plain `realise_settled(conn, today)` once
    the past closes are on file, so a trade settled since the live pull's own freeze (which
    runs before the backfill of the same button press) is frozen at its settlement date's
    close, and one the ledger froze at a live row is frozen again at the close that replaced
    it (the ledger's rule, 2026-09-22). The call is the ledger's plain one. Returns the call's `live.ledger_block` (None when the ledger is not
    importable or raised), after recording it in the status file (`_record_ledger`)."""
    realise_settled = _import_realise_settled()
    if realise_settled is None:
        return None
    from data.ingest.schema import connect
    conn = connect(Path(db_path))
    try:
        led = realise_settled(conn, today.isoformat())
        block = _record_ledger(db_path, "closing", ledger_block(led, today.isoformat()))
        if block["realised"]:
            log(f"Auto-backfill: {block['realised']} settled trade(s) frozen after the backfill.")
        if block["refrozen_summary"]:
            log(f"Auto-backfill: {block['refrozen_summary']}.")
        return block
    except Exception as exc:  # noqa: BLE001 -- as in backfill(): report and move on
        log(f"  realise_settled raised: {exc!r}")
        return None
    finally:
        conn.close()


def auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                   fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                   fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                   log: Callable[[str], None] = print,
                   on_progress: Optional[Callable[[int], None]] = None,
                   scale_fetch: Optional[Callable] = None,
                   clock: Callable[[], float] = __import__("time").monotonic,
                   quote_fetch: Optional[Callable] = None) -> List[dict]:
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
    `scale_fetch` are forwarded to `backfill()` unchanged (see its docstring)."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness
    from data.bloomberg.live import book_today
    key = _db_key(db_path)
    conn = connect(Path(db_path))
    try:
        earliest = _earliest_trade_date(conn)
        if earliest is None:
            log("Auto-backfill: no trades in the database; nothing to do.")
            return []
        today = book_today()
        yesterday = today - timedelta(days=1)
        if earliest > yesterday:
            return []
        completeness = close_completeness(conn, earliest.isoformat(), yesterday.isoformat())
    finally:
        conn.close()
    lacks_input = completeness["inputs_missing"].map(bool)
    open_rows = completeness[((completeness["needed"] > 0) & ~completeness["complete"]) | lacks_input]
    signatures = {row.as_of_date: _signature(row.missing, row.inputs_missing) for row in open_rows.itertuples()}
    # Days that hold marks which are not that day's close (an FX row not at 15:00 New York:
    # that day's last live pull, or a 17:00 PX_LAST row on a day still within Bloomberg's
    # intraday reach, from the 2026-09-21 cut-over; a future's live price rather than its
    # settlement, 2026-09-22): said once in the log and in the status block, since the
    # first run after the change asks for every past day again (2026-09-22: every past
    # close is the 15:00 value; only days beyond the intraday history keep 17:00).
    restamp = sum(1 for row in open_rows.itertuples() if getattr(row, "not_closed", 0) > 0)
    note = ""
    if restamp:
        floor = first_1500_day(today)
        note = (f"{restamp} past day(s) hold marks that are not that day's close (an FX row not at {CLOSE_HOUR_NY}:00 "
                f"New York: a live pull's last price, or a 17:00 daily close; a future's live price rather than its "
                f"settlement); every past day from {floor} on, as far as Bloomberg's intraday history reaches, is "
                f"asked for at {CLOSE_HOUR_NY}:00 New York and those rows replaced, and a future's settlement is asked "
                f"for on any past day. A day before {floor} is beyond that history and closes at Bloomberg's 17:00 "
                f"daily close.")
    _notes[key] = note
    if note:
        log("Auto-backfill: " + note)
    _load_state(db_path, key, clock)              # a restart: what the last process had tried (2026-09-22)
    for stale in [k for k in _day_state if k[0] == key and k[1] not in signatures]:
        del _day_state[stale]                     # complete since (or no longer needed): nothing to report
    now = clock()
    version = state_version()
    recent = recent_business_days(today)
    due = []
    for day_iso, signature in signatures.items():
        state = _day_state.get((key, day_iso))
        if state is None or state["version"] != version or state["signature"] != signature:
            due.append(date.fromisoformat(day_iso))
        elif day_iso in recent and not state["rejected_only"] and now - state["at"] >= RETRY_SECONDS:
            due.append(date.fromisoformat(day_iso))
    waiting = _waiting_sentence(key, signatures, {d.isoformat() for d in due}, recent)
    _waiting[key] = waiting
    refs = reference_dates(today)
    results: List[dict] = []
    try:
        if not due:
            log("Auto-backfill: history already complete." if not signatures else
                f"Auto-backfill: {len(signatures)} day(s) cannot be completed yet; nothing asked of Bloomberg: {waiting}.")
            if on_progress:
                on_progress(0)
            _realise_after_backfill(db_path, today, log)   # nothing left to ask for: the backfill has tried
            return []
        if waiting:
            log(f"Auto-backfill: {waiting}.")
        order = [d for d in refs if d in due] + sorted((d for d in due if d not in refs), reverse=True)
        log(f"Auto-backfill: {len(order)} incomplete day(s) between {min(order)} and {max(order)}, "
            f"reference dates first, then newest first.")
        if on_progress:
            on_progress(len(order))

        def _on_day(result: dict) -> None:
            results.append(result)
            if on_progress:
                on_progress(len(order) - len(results))

        backfill(db_path, min(order), max(order), fetch=fetch, fwd_fetch=fwd_fetch, fut_fetch=fut_fetch,
                 session_factory=session_factory, host=host, port=port, log=log, scale_fetch=scale_fetch,
                 order=order, on_day=_on_day, quote_fetch=quote_fetch)
        _realise_after_backfill(db_path, today, log)
        return results
    finally:
        _record_outcome(db_path, key, results, signatures, refs, clock, today)


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
            for result in results:
                row = close_completeness(conn, result["day"], result["day"]).iloc[0]
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
                    "rejected": rejected, "rejected_only": bool(failures) and all(is_rejection(r) for r in failures)}
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


def start_auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                        fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                        fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                        scale_fetch: Optional[Callable] = None, quote_fetch: Optional[Callable] = None):
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
      "note": "" or a sentence saying how many past days hold FX marks that are not the
              15:00 New York close and are being asked for again (2026-09-21; every past
              day within Bloomberg's intraday reach from 2026-09-22, 17:00 only beyond it)
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
    "days" holds the header's reference dates and the newest days that are not DONE (a
    day lacking a smile or a curve its FX options need counts as not DONE), so
    the header can say WHY a period is n/a. A "reason" of a run that raised contains the
    word "failed". live.pull_once rewrites the whole status file without this key on
    every cycle, so every call here publishes the whole remembered block again."""
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
    if real_pull:
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
        if not real_pull:
            return
        try:
            from data.bloomberg import snapshot
            _publish({"snapshot": snapshot.save_after_pull(db_path)["message"]})
        except Exception as exc:  # noqa: BLE001 -- said in the status, never raised into the thread
            _publish({"snapshot": f"marks snapshot failed: {exc!r}"})

    def _run():
        try:
            try:
                _publish({"running": True, "reason": ""})
                auto_backfill(db_path, host=host, port=port, fetch=fetch, fwd_fetch=fwd_fetch, fut_fetch=fut_fetch,
                              session_factory=session_factory, scale_fetch=scale_fetch, quote_fetch=quote_fetch,
                              on_progress=lambda remaining: _publish({"running": remaining > 0, "remaining": remaining}))
            except Exception as exc:  # never let a background thread take the process down
                _publish({"running": False, "reason": f"auto-backfill failed: {exc!r}"})
            _save()
        finally:
            _publish({"running": False, "remaining": 0, "days": _days_block.get(key, {}),
                      "note": _notes.get(key, ""), "points_scale": _scale_reports.get(key, {}),
                      "options": _options_block.get(key, {}), "inputs": _inputs_block.get(key, {}),
                      "waiting_on_tickers": _waiting.get(key, ""),
                      "last_run": datetime.now().astimezone().isoformat(timespec="seconds")})
            _auto_lock.release()

    t = threading.Thread(target=_run, name="bloomberg-auto-backfill", daemon=True)
    t.start()
    return t


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill P&L ledger snapshots from Bloomberg daily closes.")
    parser.add_argument("--db", default=None, help="SQLite path (default: ui.app.get_db_path())")
    parser.add_argument("--start", default=None, help="first day (default: last business day of previous year)")
    parser.add_argument("--end", default=None, help="last day (default: yesterday)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--overwrite", action="store_true", help="recompute days that already have a complete snapshot")
    args = parser.parse_args(argv)
    if args.db is None:
        from ui.app import get_db_path
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
