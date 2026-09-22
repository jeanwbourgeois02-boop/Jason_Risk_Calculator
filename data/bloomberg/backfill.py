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
    including its expiry date (`spot_only_pair_names`). SPOT only for options: no
    historical forward at the expiry and no historical vol, since nothing prices an
    option on a past date -- the closes are what the expiry-day catch-up's payoff and the
    ledger's base->USD conversion read.
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
  - FUTURE_PX (2026-09-18): PX_SETTLE of every future open on that day, same batched
    one-request-for-the-whole-range approach.
Only what is needed is asked for (user decision 2026-09-21: "only the data necessary for
the pnl calcs of the trades ... also for the backfill"), all of it read from the Bloomberg
library (data/bloomberg/library.py): the days being worked, as stretches of consecutive
business days (`_runs`) -- never the days between an old incomplete day and yesterday;
within a stretch, the pairs and futures the book needed inside it; on a day, the closes
of the pairs needed that day; and per pair, the tenors up to the one that clears its
furthest open leg (`_tenors_needed`), since a forward is read between the two tenors
either side of its date.

The close (user decision 2026-09-21: "the EOD is 3pm New York time"; "for previous or any
closes in FX, we need to use NY 3pm"). A past FX close -- SPOT, and the tenor series the
forwards are built from -- is Bloomberg's value at 15:00 America/New_York that day, read
from intraday bars (pull_marks.fetch_intraday_close_series: two IntradayBarRequests per
ticker per stretch, BID and ASK, the hourly bar ending 15:00 New York, mid of the two
closes). The daily PX_LAST is the 17:00 close and is NOT used, not even when the 15:00
bar is missing or the day is older than Bloomberg's intraday history (about 140 business
days): that mark stays missing, with the reason. Futures keep PX_SETTLE. Rows are stamped
`close_stamp` (15:00 New York on their date). UNVERIFIED on a terminal.

A day counts as complete (skipped unless overwrite=True) only once ALL of the above are
official for it -- `data.bloomberg.inventory.close_completeness`, the same "needed" set
`data.bloomberg.live.build_requests` uses for the live feed, so the three can never drift
apart. A row already official AT THE CLOSE for its (day, instrument, settle_date,
mark_type) is never rewritten, even when overwrite is False and the day is otherwise
incomplete (e.g. a new trade added a settle_date this day never needed before). A past
day's official FX row that is NOT stamped at the close -- that day's last live pull (say
11:40), or a 17:00 PX_LAST row written before 2026-09-21 -- is not a close: the day counts
as incomplete and the row is replaced once the 15:00 value is in hand (`is_close_row`,
`_write_closes`). It is never deleted without its replacement, today's rows are never
touched (intraday = live), and realised_pnl is never touched: frozen rows stay frozen.

Only if `engine.pnl.ledger.realise_settled` is importable, trades settled before a day are
frozen once that day's marks are on file: every day's SPOT and FUTURE_PX (all a freeze
reads) are written together before anything else, see `backfill`. This module no longer writes a
`pnl_snapshots` row; that table and its "one snapshot per day" model are retired by the
pnl-engine task, which recomputes `ltd(conn, date)` straight from `marks` instead.

Limits, stated plainly:
  - Trades are only those currently in the database (the blotter is the app's only trade
    source; a re-upload replaces the whole book -- see CLAUDE.md's schema notes).
  - FX closes are Bloomberg's 15:00 New York intraday mid, futures the daily PX_SETTLE;
    the live pull's rows are stored under the same official sources, and the snapped_at
    timestamp tells them apart (backfill rows are stamped 15:00 America/New_York on
    their date, a live row carries the time it was pulled).
  - NDFs still realise at spot on the value date, not the fixing.
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
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from data.bloomberg import fwd_curve as fc
from data.bloomberg.live import SRC_INTERP, SRC_SPOT_FWD
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


# The 15:00 New York close applies FROM this day on (user decision 2026-09-21: "make it so
# that from now on its 3pm new york but surely for like a month ago its not that
# serious"). A day before it -- and any day Bloomberg's intraday history no longer reaches
# -- closes at Bloomberg's daily close (PX_LAST, 17:00 New York), and what is already on
# file for a day before it is kept as that day's close.
CLOSE_1500_FROM = date(2026, 9, 21)
DAILY_CLOSE_HOUR_NY = 17


def first_1500_day(today: Optional[date] = None) -> date:
    """The first day whose FX close is the 15:00 New York value: the later of
    CLOSE_1500_FROM and the oldest day Bloomberg's intraday history reaches from `today`
    (default: the New York book date)."""
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today()
    return max(CLOSE_1500_FROM, intraday_floor(today))


def close_stamp(day: date, today: Optional[date] = None) -> str:
    """The official close on `day`, with that date's UTC offset resolved: 15:00 New York
    (pull_marks.CLOSE_HOUR_NY) from `first_1500_day(today)` on, Bloomberg's 17:00 daily
    close before it."""
    hour = DAILY_CLOSE_HOUR_NY if day < first_1500_day(today) else CLOSE_HOUR_NY
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=NY).isoformat(timespec="seconds")


# The mark types the 15:00 New York close rule covers (the user said FX closes; a future
# keeps its PX_SETTLE whatever its stamp).
FX_CLOSE_MARK_TYPES = ("SPOT", "FWD_OUTRIGHT")


def is_close_row(mark_type: str, as_of_date: str, snapped_at: str) -> bool:
    """Is this official row of a PAST day a close? Other mark types always are, and so is
    any FX row (SPOT / FWD_OUTRIGHT) of a day before CLOSE_1500_FROM: those days keep
    what they have. From that day on an FX row is a close only when it is stamped at
    15:00 New York of its own as_of_date, or at 17:00 (the daily close, which the backfill
    only ever writes for a day Bloomberg's intraday history no longer reached); anything
    else is that day's last live pull."""
    if mark_type not in FX_CLOSE_MARK_TYPES:
        return True
    try:
        day = date.fromisoformat(as_of_date)
        if day < CLOSE_1500_FROM:
            return True
        stamp = datetime.fromisoformat(snapped_at)
        return any(stamp == datetime(day.year, day.month, day.day, hour, 0, tzinfo=NY)
                   for hour in (CLOSE_HOUR_NY, DAILY_CLOSE_HOUR_NY))
    except (TypeError, ValueError):
        return False


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
    """{tenor label -> bbg_ticker} for `pair`, the naming the live tenor-fallback path uses
    (pull_marks.fetch_tenor_points)."""
    return {t: f"{pair}{t} Curncy" for t in tenors}


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


def _fetch_future_px_history(conn: sqlite3.Connection, session, service, runs: List[Tuple[date, date]],
                             fut_fetch: Optional[Callable] = None) -> Dict[str, Dict[str, float]]:
    """{instrument_id: {date_iso: PX_SETTLE}} -- one HistoricalDataRequest per stretch of
    `runs`, for the futures open inside it (the Bloomberg library's FUTURE_PX rows).
    `fut_fetch` mirrors `_fetch_fwd_outright_history`'s `fwd_fetch`; defaults to
    pull_marks.fetch_historical_series."""
    from data.bloomberg import library
    out: Dict[str, Dict[str, float]] = {}
    for run_start, run_end in runs:
        ticker_to_instrument = {r["bbg_ticker"]: r["key"]
                                for r in library.needed_in_range(conn, run_start.isoformat(), run_end.isoformat())
                                if r["kind"] == "FUTURE_PX"}
        if not ticker_to_instrument:
            continue
        if fut_fetch is None:
            from data.bloomberg.pull_marks import fetch_historical_series
            fut_fetch = fetch_historical_series
        series = fut_fetch(session, service, sorted(ticker_to_instrument), ["PX_SETTLE"], run_start, run_end) or {}
        for ticker, per_day in series.items():
            instrument_id = ticker_to_instrument.get(ticker)
            if instrument_id is None:
                continue
            out.setdefault(instrument_id, {}).update(
                {day: row["PX_SETTLE"] for day, row in per_day.items() if "PX_SETTLE" in row})
    return out


def _fetch_ndf_fix_history(conn: sqlite3.Connection, session, service, runs: List[Tuple[date, date]],
                           fix_fetch: Optional[Callable] = None) -> Dict[str, Dict[str, float]]:
    """{pair: {fixing_date_iso: fix}} -- one HistoricalDataRequest (PX_LAST) per stretch of
    `runs` for the fixing tickers of the NDF tickets fixing inside it (the Bloomberg
    library's NDF_FIX rows, 2026-09-22: the NDF's exit price is its currency's own official
    fixing on the fixing date). `fix_fetch` mirrors `fut_fetch`."""
    from data.bloomberg import library
    out: Dict[str, Dict[str, float]] = {}
    for run_start, run_end in runs:
        ticker_to_pair = {r["bbg_ticker"]: r["key"]
                          for r in library.needed_in_range(conn, run_start.isoformat(), run_end.isoformat())
                          if r["kind"] == library.NDF_FIX}
        if not ticker_to_pair:
            continue
        if fix_fetch is None:
            from data.bloomberg.pull_marks import fetch_historical_series
            fix_fetch = fetch_historical_series
        series = fix_fetch(session, service, sorted(ticker_to_pair), ["PX_LAST"], run_start, run_end) or {}
        for ticker, per_day in series.items():
            pair = ticker_to_pair.get(ticker)
            if pair is None:
                continue
            out.setdefault(pair, {}).update({day: row["PX_LAST"] for day, row in per_day.items() if "PX_LAST" in row})
    return out


def _drop_already_official(conn: sqlite3.Connection, rows: List[dict], today: Optional[str] = None) -> List[dict]:
    """Filter out any row whose (as_of_date, instrument_id, settle_date, mark_type)
    already has an OFFICIAL mark on file that is a close -- a backfill run must never
    overwrite an existing close, even with an identical re-computed value, and even on a
    day that is otherwise incomplete (e.g. a trade added later needs a settle_date this
    day never needed before, but this day's SPOT was already official).

    2026-09-21: a PAST day's official FX row that is not stamped at the 15:00 New York
    close (`is_close_row`) is that day's last live pull or an old 17:00 PX_LAST row, not a
    close, so the new row is kept and `_write_closes` replaces the old one. A row dated
    `today` (default: the New York book date) or later is live and is never replaced."""
    if today is None:
        from data.bloomberg.live import book_today
        today = book_today().isoformat()
    out = []
    for r in rows:
        hit = conn.execute(
            "SELECT snapped_at FROM marks_official WHERE as_of_date=? AND instrument_id=? AND settle_date=? AND mark_type=?",
            (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"])).fetchone()
        if hit is None or (r["as_of_date"] < today and not is_close_row(r["mark_type"], r["as_of_date"], hit[0])):
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
                    if not is_close_row(r["mark_type"], r["as_of_date"], snapped):
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


def _skipped(day: str) -> dict:
    return {"day": day, "status": "SKIPPED", "closes": 0, "fwd_outrights": 0, "future_px": 0,
            "missing_pairs": [], "missing_pair_reasons": {}, "missing_marks": [], "realised": 0, "unrealisable": []}


# db -> {pair: scale report} of the last backfill() that needed a points divisor; published
# under the status file's "backfill" block so a paste says which field answered.
_scale_reports: Dict[str, dict] = {}


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             fwd_fetch: Optional[Callable] = None, fut_fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print,
             scale_fetch: Optional[Callable] = None, order: Optional[List[date]] = None,
             on_day: Optional[Callable[[dict], None]] = None, today: Optional[date] = None) -> List[dict]:
    """Run the backfill. Returns one dict per business day of [start, end], in date order:
    {day, status: DONE|SKIPPED|NO_CLOSES|ERROR, closes, fwd_outrights, future_px,
    missing_pairs, missing_pair_reasons, missing_marks, realised, unrealisable}.
    `realised`/`unrealisable` are
    None on a day where realise_settled could not be imported (marks are still written).
    `missing_marks` lists {instrument_id, settle_date, mark_type, reason} for anything
    this day needed (per inventory.close_completeness) but could not resolve -- a settle
    date beyond the last tenor, no forward tenors / future history for that pair / day,
    etc; never silently dropped. `missing_pair_reasons` is {pair: why it has no SPOT
    close}, in the source's own words where it gave any. ERROR (2026-09-21) is a day
    whose own processing raised:
    it carries `error`, and the other days still run -- one bad day used to end the run.

    The close (2026-09-21): FX closes are the 15:00 New York value from intraday bars, see
    the module docstring. A day older than Bloomberg's intraday history
    (`intraday_floor(today)`, `today` defaulting to the New York book date) is not asked
    for at all when the default fetchers are in use: its FX marks are reported missing
    with that reason (never the daily PX_LAST instead), and its futures still run.

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
    tenor series) and pull_marks.fetch_historical_series (futures' PX_SETTLE).
    `scale_fetch`: see `_fetch_points_scales`. `session_factory` defaults to
    pull_marks.open_session. All are injectable so the loop is testable without blpapi."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness, _needed_marks
    from data.bloomberg.live import _ensure_fx_instruments, book_today
    from data.bloomberg import pull_marks as pm
    realise_settled = _import_realise_settled()
    today = today or book_today()
    today_iso = today.isoformat()
    first_1500 = first_1500_day(today)   # days before it close at Bloomberg's daily close
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
        has_futures = any(r["kind"] == "FUTURE_PX"
                          for r in library.needed_in_range(conn, start.isoformat(), end.isoformat()))
        if not pairs and not has_futures:
            # 2026-09-18: this used to bail out on `not pairs` alone, before traded_pairs
            # covered crosses -- a book with only futures and zero FX trades of any kind
            # would otherwise never reach the FUTURE_PX logic below at all.
            log("No FX pairs or futures with a leg open in this range; nothing to backfill.")
            return []
        by_ticker = {t: p for p, t in pairs}
        ticker_of = {p: t for p, t in pairs}
        days = business_days(start, end)
        completeness = close_completeness(conn, start.isoformat(), end.isoformat(), today=today_iso)
        complete_by_day = dict(zip(completeness["as_of_date"], completeness["complete"]))
        todo = [d for d in days if overwrite or not complete_by_day.get(d.isoformat(), False)]
        if order is not None:
            todo_set = set(todo)
            work = [d for d in dict.fromkeys(order) if d in todo_set]
        else:
            work = todo
        log(f"Backfill {start} .. {end}: {len(days)} business days, {len(work)} to compute, {len(pairs)} pairs "
            f"(SPOT + FWD_OUTRIGHT + FUTURE_PX).")
        if realise_settled is None:
            log("  note: engine.pnl.ledger.realise_settled not importable; marks only, no realisation this run.")
        if not work:
            return [_skipped(d.isoformat()) for d in days]
        span_end = max(work)
        runs = _runs(work)
        fx_runs = runs
        n_daily = sum(1 for d in work if d < first_1500)
        if n_daily:
            log(f"  {n_daily} day(s) before {first_1500} take Bloomberg's daily close (17:00 New York); "
                f"the {CLOSE_HOUR_NY}:00 New York close applies from {first_1500} on.")
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

        try:
            # FWD_OUTRIGHT / FUTURE_PX history: one request per kind per stretch of days
            # being worked (`_runs`), never one per day per ticker, and never a day, a
            # pair or a tenor the book did not need (2026-09-21).
            holidays = load_holidays()
            tenor_rows_by_pair = _fetch_fwd_outright_history(conn, session, service, fx_runs, fwd_fetch, holidays,
                                                             fields=fwd_fields)
            future_px_by_instrument = _fetch_future_px_history(conn, session, service, runs, fut_fetch)
            ndf_fix_by_pair = _fetch_ndf_fix_history(conn, session, service, runs, fut_fetch)
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
                    if item["mark_type"] == "NDF_FIX":
                        # The currency's official fixing of this day (2026-09-22), on the pair.
                        try:
                            fix_value = float(ndf_fix_by_pair.get(instrument_id, {}).get(day))
                        except (TypeError, ValueError):
                            missing_marks.append({**item, "reason": f"Bloomberg returned no fixing (PX_LAST) for {instrument_id} on {day}"})
                            continue
                        fut_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": item["settle_date"],
                                         "mark_type": "NDF_FIX", "value": fix_value, "source": SRC_FUTURE,
                                         "snapped_at": close_stamp(d, today)})
                        continue
                    if item["mark_type"] != "FUTURE_PX":
                        continue
                    try:
                        settle_value = float(future_px_by_instrument.get(instrument_id, {}).get(day))
                    except (TypeError, ValueError):
                        missing_marks.append({**item, "reason": f"Bloomberg returned no PX_SETTLE for {instrument_id} on {day}"})
                        continue
                    fut_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": item["settle_date"],
                                     "mark_type": "FUTURE_PX", "value": settle_value, "source": SRC_FUTURE,
                                     "snapped_at": close_stamp(d, today)})
                prepared[d] = {"no_closes": False, "needed": needed, "spot_rows": spot_rows, "spot_by_pair": spot_by_pair,
                               "missing_pairs": missing_pairs, "pair_reasons": pair_reasons, "fut_rows": fut_rows,
                               "missing_marks": missing_marks}
                first_rows += spot_rows + fut_rows
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
                                  "missing_marks": [], "realised": None, "unrealisable": []}
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
                    realised, unrealisable, flag = None, [], "realisation after the last day"
                    if realise_settled is None:
                        flag = "no realisation (realise_settled unavailable)"
                    elif order is None:
                        try:
                            led = realise_settled(conn, day)
                            realised, unrealisable = led["realised"], led["unrealisable"]
                            flag = "complete" if not unrealisable else f"unrealisable={[u['trade_id'] for u in unrealisable]}"
                        except Exception as exc:  # engine/pnl/ledger.py is owned by another task; never let its
                            # in-progress state stop marks from being written -- report and move on.
                            flag = f"realise_settled raised: {exc!r}"
                    log(f"  {day}  DONE  closes={len(p['spot_rows'])}  fwd_outrights={len(fwd_rows)}  "
                        f"future_px={len(p['fut_rows'])}  missing={len(p['missing_pairs']) + len(missing_marks)}"
                        f"  realised={realised}  {flag}")
                    results[d] = {"day": day, "status": "DONE", "closes": len(p["spot_rows"]),
                                  "fwd_outrights": len(fwd_rows), "future_px": len(p["fut_rows"]),
                                  "missing_pairs": p["missing_pairs"], "missing_pair_reasons": p["pair_reasons"],
                                  "missing_marks": missing_marks, "realised": realised, "unrealisable": unrealisable}
                except Exception as exc:  # noqa: BLE001 -- one bad day must not end the run for the others
                    log(f"  {day}  ERROR  {exc!r}")
                    results[d] = {"day": day, "status": "ERROR", "closes": len(p["spot_rows"]), "fwd_outrights": 0,
                                  "future_px": len(p["fut_rows"]), "missing_pairs": p["missing_pairs"],
                                  "missing_pair_reasons": p["pair_reasons"],
                                  "missing_marks": p["missing_marks"], "realised": None, "unrealisable": [],
                                  "error": repr(exc)}
                if on_day:
                    on_day(results[d])
            if order is not None and realise_settled is not None:
                try:
                    led = realise_settled(conn, span_end.isoformat())
                    log(f"  realised after the last day: {led['realised']}")
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

_day_state: Dict[Tuple[str, str], dict] = {}   # (db, day) -> {at, signature, status, missing_count, missing}
_days_block: Dict[str, dict] = {}              # db -> the "days" dict last built by auto_backfill
_published: Dict[str, dict] = {}               # db -> the whole "backfill" block last published
_notes: Dict[str, str] = {}                    # db -> the last run's "note" (past days being re-requested at 15:00)


def _db_key(db_path) -> str:
    return str(Path(db_path).resolve())


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


def _signature(missing: List[dict]) -> frozenset:
    return frozenset((m["instrument_id"], m["settle_date"], m["mark_type"]) for m in missing)


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
    if not reasons:
        reasons = [f"{m['instrument_id']} {m['mark_type']} {m['settle_date']}: not written" for m in still_missing]
    return list(dict.fromkeys(reasons))[:MAX_STATUS_REASONS]


def _freeze_ndfs_at_present_spot(db_path, today: date, log: Callable[[str], None]) -> None:
    """User decision 2026-09-21 ("we can use a present spot for the past fixes"): once the
    backfill has tried for the past closes, an NDF ticket that settled with no official SPOT
    on or before its settlement is frozen at the latest official SPOT on file
    (engine.pnl.ledger.realise_settled, ndf_present_spot=True). Here and nowhere else: the
    live pull's own realise_settled runs before the backfill of the same button press, and a
    freeze is never recomputed."""
    realise_settled = _import_realise_settled()
    if realise_settled is None:
        return
    from data.ingest.schema import connect
    conn = connect(Path(db_path))
    try:
        led = realise_settled(conn, today.isoformat(), ndf_present_spot=True)
        if led["realised"]:
            log(f"Auto-backfill: {led['realised']} settled trade(s) frozen after the backfill (an NDF with no "
                f"SPOT on or before its settlement takes the present spot).")
    except Exception as exc:  # noqa: BLE001 -- as in backfill(): report and move on
        log(f"  realise_settled raised: {exc!r}")
    finally:
        conn.close()


def auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                   fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                   fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                   log: Callable[[str], None] = print,
                   on_progress: Optional[Callable[[int], None]] = None,
                   scale_fetch: Optional[Callable] = None,
                   clock: Callable[[], float] = __import__("time").monotonic) -> List[dict]:
    """Fill every business day from the earliest trade date to yesterday that needs marks
    and lacks a complete official close (per `data.bloomberg.inventory.close_completeness`
    -- SPOT + FWD_OUTRIGHT + FUTURE_PX, 2026-09-18), in ONE `backfill()` call: the
    header's `reference_dates` first, then the remaining days newest first. Calls
    `on_progress(days_remaining)` after each day so a caller can publish it, and returns
    the days' results in the order they were worked.

    A day that was tried and stayed incomplete (a holiday with no closes, a settle date
    beyond the last tenor, a ticker Bloomberg does not serve) is left alone for
    RETRY_SECONDS unless what it lacks has changed since; `clock` is injectable for tests.
    A day on which the book needed nothing is never asked for. The outcome per day is kept
    in `_day_state` and the status-file "days" dict in `_days_block` (see
    `start_auto_backfill`). `fwd_fetch`/`fut_fetch`/`scale_fetch` are forwarded to
    `backfill()` unchanged (see its docstring)."""
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
    open_rows = completeness[(completeness["needed"] > 0) & ~completeness["complete"]]
    signatures = {row.as_of_date: _signature(row.missing) for row in open_rows.itertuples()}
    # Days that hold FX marks which are not the 15:00 New York close (that day's last live
    # pull, or a 17:00 PX_LAST row from before 2026-09-21): said once in the log and in the
    # status block, since the first run after the change asks for every past day again.
    restamp = sum(1 for row in open_rows.itertuples() if getattr(row, "not_closed", 0) > 0)
    note = ""
    if restamp:
        note = (f"{restamp} past day(s) since {CLOSE_1500_FROM} hold FX marks that are that day's last pull, not its "
                f"{CLOSE_HOUR_NY}:00 New York close; the close is asked of Bloomberg and replaces them. Days before "
                f"{CLOSE_1500_FROM} keep the marks they have.")
    _notes[key] = note
    if note:
        log("Auto-backfill: " + note)
    for stale in [k for k in _day_state if k[0] == key and k[1] not in signatures]:
        del _day_state[stale]                     # complete since (or no longer needed): nothing to report
    now = clock()
    due = []
    for day_iso, signature in signatures.items():
        state = _day_state.get((key, day_iso))
        if state is None or state["signature"] != signature or now - state["at"] >= RETRY_SECONDS:
            due.append(date.fromisoformat(day_iso))
    refs = reference_dates(today)
    results: List[dict] = []
    try:
        if not due:
            log("Auto-backfill: history already complete." if not signatures else
                f"Auto-backfill: {len(signatures)} day(s) cannot be completed yet; each is tried again within the hour.")
            if on_progress:
                on_progress(0)
            _freeze_ndfs_at_present_spot(db_path, today, log)   # nothing left to ask for: the backfill has tried
            return []
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
                 order=order, on_day=_on_day)
        _freeze_ndfs_at_present_spot(db_path, today, log)
        return results
    finally:
        _record_outcome(db_path, key, results, signatures, refs, clock)


def _record_outcome(db_path, key: str, results: List[dict], signatures: Dict[str, frozenset],
                    refs: List[date], clock: Callable[[], float]) -> None:
    """After a run (finished or not): what each worked day still lacks goes into
    `_day_state` (that is what the hourly retry and the status file read), and the
    status-file "days" dict is rebuilt -- every reference date, plus the newest days that
    are not DONE, MAX_STATUS_DAYS at most."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness
    if results:
        conn = connect(Path(db_path))
        try:
            for result in results:
                row = close_completeness(conn, result["day"], result["day"]).iloc[0]
                if row["complete"] or not row["missing"]:
                    _day_state.pop((key, result["day"]), None)
                    signatures.pop(result["day"], None)
                    continue
                _day_state[(key, result["day"])] = {
                    "at": clock(), "signature": _signature(row["missing"]),
                    "status": "NO_CLOSES" if result["status"] == "NO_CLOSES" else "INCOMPLETE",
                    "missing_count": len(row["missing"]), "missing": _plain_reasons(result, row["missing"])}
        finally:
            conn.close()
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


def start_auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                        fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                        fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                        scale_fetch: Optional[Callable] = None):
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
      "note": "" or a sentence saying how many past days hold FX marks that are not the
              15:00 New York close and are being asked for again (2026-09-21)
      "points_scale": {"<pair>": {"field": "FWD_POINTS_SCALE" | "FWD_SCALE" | "",
                                  "divisor": <float or null>, "raw": {...}, "errors": {...}}}
              -- which Bloomberg field gave each pair's forward-points divisor
    "days" holds the header's reference dates and the newest days that are not DONE, so
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

    if session_factory is None and fetch is None and fwd_fetch is None and fut_fetch is None:
        ok, why = availability(host, port)
        if not ok:
            _publish({"running": False, "reason": why})
            return None

    if not _auto_lock.acquire(blocking=False):
        _publish({})  # a run is already in flight; only put its block back after the feed's rewrite
        return None

    def _run():
        try:
            _publish({"running": True, "reason": ""})
            auto_backfill(db_path, host=host, port=port, fetch=fetch, fwd_fetch=fwd_fetch, fut_fetch=fut_fetch,
                          session_factory=session_factory, scale_fetch=scale_fetch,
                          on_progress=lambda remaining: _publish({"running": remaining > 0, "remaining": remaining}))
        except Exception as exc:  # never let a background thread take the process down
            _publish({"running": False, "reason": f"auto-backfill failed: {exc!r}"})
        finally:
            _publish({"running": False, "remaining": 0, "days": _days_block.get(key, {}),
                      "note": _notes.get(key, ""), "points_scale": _scale_reports.get(key, {}),
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
