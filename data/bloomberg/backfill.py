"""Backfill past-close Bloomberg marks history (BUILD_PLAN.md section 3/6, Task B).

`engine/pnl/ledger.py::ltd` recomputes LTD from `marks` on demand. Per CLAUDE.md's "P&L
conventions", Daily/5d/MTD/YTD all difference LTD(t) against LTD(t-1bd) / LTD(t-5bd) /
etc, and every FX leg's LTD needs the FWD_OUTRIGHT for its OWN settle_date (never a single
shared date), every future's needs FUTURE_PX -- SPOT alone is not enough to price a single
past day. For every business day in [start, end] this module writes, as official marks
dated that day:
  - SPOT: PX_LAST close of every FX pair with a leg open at any point in [start, end] --
    crosses included, plus every USD-conversion pair a cross's legs need for delta/P&L
    (`traded_pairs`, mirrors `live._cross_usd_legs`; USD-pairs-only until 2026-09-18,
    which meant a cross like EURSEK never got a SPOT close backfilled at all and could
    never be frozen by realise_settled once it settled) -- one HistoricalDataRequest for
    the whole range (2026-09-21; it was one per day). Also (2026-09-18) every FX option's pair
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
    tickers instead (pull_marks.STANDARD_TENORS), one HistoricalDataRequest for the whole
    date range's tenor tickers, not one per day. Those tickers' PX_LAST is forward points
    by the live tenor path's account (converted as it converts them: spot + points /
    FWD_POINTS_SCALE), and HistoricalDataRequest sends no SETTLE_DT, so the tenor dates are
    computed by market convention; a forward built on either is written BBG_INTERP, and
    BBG_BFXFORWARD is kept for Bloomberg's own outright at Bloomberg's own date
    (2026-09-21: before, no past forward was ever written, so no past day could complete).
  - FUTURE_PX (2026-09-18): PX_SETTLE of every future open on that day, same batched
    one-request-for-the-whole-range approach.
A day counts as complete (skipped unless overwrite=True) only once ALL of the above are
official for it -- `data.bloomberg.inventory.close_completeness`, the same "needed" set
`data.bloomberg.live.build_requests` uses for the live feed, so the three can never drift
apart. A row already official for its (day, instrument, settle_date, mark_type) is never
rewritten, even when overwrite is False and the day is otherwise incomplete (e.g. a new
trade added a settle_date this day never needed before).

Only if `engine.pnl.ledger.realise_settled` is importable, trades settled before a day are
frozen once that day's marks are on file: every day's SPOT and FUTURE_PX (all a freeze
reads) are written together before anything else, see `backfill`. This module no longer writes a
`pnl_snapshots` row; that table and its "one snapshot per day" model are retired by the
pnl-engine task, which recomputes `ltd(conn, date)` straight from `marks` instead.

Limits, stated plainly:
  - Trades are only those currently in the database (the blotter is the app's only trade
    source; a re-upload replaces the whole book -- see CLAUDE.md's schema notes).
  - Closes are Bloomberg's daily PX_LAST/PX_SETTLE, not the live-feed's intraday mid. Both
    are stored under the same official sources; the snapped_at timestamp tells them apart
    (backfill rows are stamped 17:00 America/New_York on their date).
  - NDFs still realise at spot on the value date, not the fixing.
  - Calendar is Monday-Friday only; holidays simply return no close and are reported.
  - UNVERIFIED on a terminal (docs/open-questions.md items 28 and 30): that the tenor
    tickers' PX_LAST is points (fwd_curve.tenor_unit checks its magnitude against spot
    rather than assume), and the FWD_POINTS_SCALE field name. Computed tenor dates use
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
from data.bloomberg.live import SRC_INTERP, SRC_SPOT_FWD, write_marks
from data.bloomberg.pull_marks import SRC_FUTURE
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


def business_days(start: date, end: date) -> List[date]:
    """Monday-Friday dates from start to end inclusive."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
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


def close_stamp(day: date) -> str:
    """17:00 New York on `day`, with that date's UTC offset resolved (CLAUDE.md mark time)."""
    return datetime(day.year, day.month, day.day, 17, 0, tzinfo=NY).isoformat(timespec="seconds")


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


def _tenor_tickers(pair: str) -> Dict[str, str]:
    """{tenor label -> bbg_ticker} for `pair`'s standard-tenor outright tickers, the same
    list and naming the live tenor-fallback path uses (pull_marks.STANDARD_TENORS,
    fetch_tenor_points)."""
    from data.bloomberg.pull_marks import STANDARD_TENORS
    return {t: f"{pair}{t} Curncy" for t in STANDARD_TENORS}


def _fetch_fwd_outright_history(conn: sqlite3.Connection, session, service, span_start: date, span_end: date,
                                fwd_fetch: Optional[Callable] = None) -> Dict[str, Dict[str, Dict[str, dict]]]:
    """{instrument_id: {date_iso: {tenor label: {'PX_LAST': ..., 'SETTLE_DT': ... if sent}}}}
    for every FX pair with a leg open at any point in [span_start, span_end] -- ONE
    HistoricalDataRequest across every pair's standard-tenor tickers for the whole span
    (2026-09-18: "batch-friendly", not one request per day per ticker). The raw rows, not
    a curve: a day's curve needs that day's SPOT close (fwd_curve.historical_curve), which
    backfill() only has inside its day loop. `fwd_fetch(session, service, tickers, fields,
    start, end) -> {ticker: {date_iso: {field: value}}}` defaults to
    pull_marks.fetch_historical_series; injectable for tests.

    SETTLE_DT is still asked for (2026-09-21) so Bloomberg's own tenor dates are used
    wherever it does send them, but it is a static reference field that
    HistoricalDataRequest does not serve; if the request comes back with no PX_LAST at all
    it is sent once more for PX_LAST alone, in case the field it cannot serve is what
    emptied the first response."""
    from data.bloomberg import library
    pairs = sorted({r["key"] for r in library.needed_in_range(conn, span_start.isoformat(), span_end.isoformat())
                    if r["kind"] == "FWD_OUTRIGHT"})
    if not pairs:
        return {}
    tenor_map = {pair: _tenor_tickers(pair) for pair in pairs}
    all_tenor_tickers = sorted({t for tickers in tenor_map.values() for t in tickers.values()})
    if fwd_fetch is None:
        from data.bloomberg.pull_marks import fetch_historical_series
        fwd_fetch = fetch_historical_series
    series = fwd_fetch(session, service, all_tenor_tickers, ["PX_LAST", "SETTLE_DT"], span_start, span_end) or {}
    if not any("PX_LAST" in row for per_day in series.values() for row in per_day.values()):
        series = fwd_fetch(session, service, all_tenor_tickers, ["PX_LAST"], span_start, span_end) or {}
    out: Dict[str, Dict[str, Dict[str, dict]]] = {}
    for pair in pairs:
        by_day = out.setdefault(pair, {})
        for tenor, ticker in tenor_map[pair].items():
            for day_iso, row in (series.get(ticker) or {}).items():
                by_day.setdefault(day_iso, {})[tenor] = row
    return out


def _fetch_points_scales(session, service, pairs: List[str], scale_fetch: Optional[Callable] = None) -> Dict[str, float]:
    """{pair: FWD_POINTS_SCALE} -- the divisor that turns a pair's forward points into an
    outright, read exactly as the live tenor path reads it (pull_marks.fetch_tenor_points:
    field FWD_POINTS_SCALE on '<pair> Curncy'), one ReferenceDataRequest for every pair.
    Only asked for when a tenor series turns out to be points. `scale_fetch(session,
    service, tickers, fields) -> {ticker: {field: value}}` defaults to
    pull_marks.fetch_reference when there is a real session; with neither there is no
    scale, and the forward is reported missing with that reason. A pair Bloomberg sends no
    scale for is simply absent -- never a hard-coded pip size."""
    if scale_fetch is None:
        if session is None:
            return {}
        from data.bloomberg.pull_marks import fetch_reference
        scale_fetch = fetch_reference
    try:
        data = scale_fetch(session, service, [f"{pair} Curncy" for pair in pairs], ["FWD_POINTS_SCALE"]) or {}
    except Exception:  # noqa: BLE001 -- a failed lookup is "no scale", reported per forward, not a dead run
        return {}
    out: Dict[str, float] = {}
    for pair in pairs:
        try:
            scale = float((data.get(f"{pair} Curncy") or {}).get("FWD_POINTS_SCALE"))
        except (TypeError, ValueError):
            continue
        if scale > 0:
            out[pair] = scale
    return out


def _fetch_future_px_history(conn: sqlite3.Connection, session, service, span_start: date, span_end: date,
                             fut_fetch: Optional[Callable] = None) -> Dict[str, Dict[str, float]]:
    """{instrument_id: {date_iso: PX_SETTLE}} for every future with a leg open at any
    point in [span_start, span_end] -- one HistoricalDataRequest for the whole span.
    `fut_fetch` mirrors `_fetch_fwd_outright_history`'s `fwd_fetch`; defaults to
    pull_marks.fetch_historical_series."""
    from data.bloomberg import library
    futs = [r for r in library.needed_in_range(conn, span_start.isoformat(), span_end.isoformat())
            if r["kind"] == "FUTURE_PX"]
    if not futs:
        return {}
    ticker_to_instrument = {r["bbg_ticker"]: r["key"] for r in futs}
    tickers = sorted(ticker_to_instrument)
    if fut_fetch is None:
        from data.bloomberg.pull_marks import fetch_historical_series
        fut_fetch = fetch_historical_series
    series = fut_fetch(session, service, tickers, ["PX_SETTLE"], span_start, span_end)
    out: Dict[str, Dict[str, float]] = {}
    for ticker, per_day in series.items():
        instrument_id = ticker_to_instrument.get(ticker)
        if instrument_id is None:
            continue
        out[instrument_id] = {day: row["PX_SETTLE"] for day, row in per_day.items() if "PX_SETTLE" in row}
    return out


def _drop_already_official(conn: sqlite3.Connection, rows: List[dict]) -> List[dict]:
    """Filter out any row whose (as_of_date, instrument_id, settle_date, mark_type)
    already has an OFFICIAL mark on file -- a backfill run must never overwrite an
    existing official mark, even with an identical re-computed value, and even on a day
    that is otherwise incomplete (e.g. a trade added later needs a settle_date this day
    never needed before, but this day's SPOT was already official)."""
    out = []
    for r in rows:
        hit = conn.execute(
            "SELECT 1 FROM marks_official WHERE as_of_date=? AND instrument_id=? AND settle_date=? AND mark_type=?",
            (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"])).fetchone()
        if hit is None:
            out.append(r)
    return out


def _spot_fetch_from_series(series_fetch: Callable, tickers: List[str], span_start: date, span_end: date) -> Callable:
    """A per-day SPOT fetch (the `fetch` signature backfill() takes) answered from ONE
    HistoricalDataRequest for every pair over the whole span (2026-09-21) -- the default
    used to be one request per day. Same field, same single-date points, so a day's close
    is the value the per-day request returned. The request is sent on first use."""
    cache: Dict[str, dict] = {}

    def fetch(session, service, wanted, field, day):
        if "series" not in cache:
            cache["series"] = series_fetch(session, service, list(tickers), [field], span_start, span_end) or {}
        return {t: ((cache["series"].get(t) or {}).get(day.isoformat()) or {}).get(field) for t in wanted}
    return fetch


def _skipped(day: str) -> dict:
    return {"day": day, "status": "SKIPPED", "closes": 0, "fwd_outrights": 0, "future_px": 0,
            "missing_pairs": [], "missing_marks": [], "realised": 0, "unrealisable": []}


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             fwd_fetch: Optional[Callable] = None, fut_fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print,
             scale_fetch: Optional[Callable] = None, order: Optional[List[date]] = None,
             on_day: Optional[Callable[[dict], None]] = None) -> List[dict]:
    """Run the backfill. Returns one dict per business day of [start, end], in date order:
    {day, status: DONE|SKIPPED|NO_CLOSES|ERROR, closes, fwd_outrights, future_px,
    missing_pairs, missing_marks, realised, unrealisable}. `realised`/`unrealisable` are
    None on a day where realise_settled could not be imported (marks are still written).
    `missing_marks` lists {instrument_id, settle_date, mark_type, reason} for anything
    this day needed (per inventory.close_completeness) but could not resolve -- a settle
    date beyond the last tenor, no forward tenors / future history for that pair / day,
    etc; never silently dropped. ERROR (2026-09-21) is a day whose own processing raised:
    it carries `error`, and the other days still run -- one bad day used to end the run.

    ONE session and one request per kind for the whole call, however many days it covers.
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
    day) defaults to one pull_marks.fetch_historical_series request for the span
    (`_spot_fetch_from_series`). `fwd_fetch`/`fut_fetch` (both `(session, service,
    tickers, fields, start, end) -> {ticker: {date_iso: {field: value}}}`, ONE call for
    the whole date range, 2026-09-18) default to pull_marks.fetch_historical_series.
    `scale_fetch`: see `_fetch_points_scales`. `session_factory` defaults to
    pull_marks.open_session. All are injectable so the loop is testable without blpapi."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness, _needed_marks
    from data.bloomberg.live import _ensure_fx_instruments
    realise_settled = _import_realise_settled()
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
        tickers = [t for _, t in pairs]
        by_ticker = {t: p for p, t in pairs}
        days = business_days(start, end)
        completeness = close_completeness(conn, start.isoformat(), end.isoformat())
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
        span_start, span_end = min(work), max(work)
        session = service = None
        own_session = False  # did THIS call open the session itself (pm.open_session)?
        if fetch is None or fwd_fetch is None or fut_fetch is None:
            from data.bloomberg import pull_marks as pm
            if fetch is None:
                fetch = _spot_fetch_from_series(pm.fetch_historical_series, tickers, span_start, span_end)
            if fwd_fetch is None:
                fwd_fetch = pm.fetch_historical_series
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
            # FWD_OUTRIGHT / FUTURE_PX history, batched over the whole `work` span in one
            # request each (2026-09-18) -- never one request per day per ticker.
            tenor_rows_by_pair = _fetch_fwd_outright_history(conn, session, service, span_start, span_end, fwd_fetch)
            future_px_by_instrument = _fetch_future_px_history(conn, session, service, span_start, span_end, fut_fetch)
            holidays = load_holidays()
            scales: Dict[str, Dict[str, float]] = {}

            def scale_for(pair: str) -> Optional[float]:
                if "by_pair" not in scales:      # asked for once, and only if some series is points
                    scales["by_pair"] = _fetch_points_scales(session, service, sorted(tenor_rows_by_pair), scale_fetch)
                return scales["by_pair"].get(pair)

            # ---- pass 1: SPOT + FUTURE_PX of every day, written together (see docstring)
            prepared: Dict[date, dict] = {}
            first_rows: List[dict] = []
            for d in work:
                day = d.isoformat()
                closes = (fetch(session, service, tickers, "PX_LAST", d) or {}) if tickers else {}
                spot_rows, spot_by_pair, missing_pairs = [], {}, []
                for ticker in tickers:
                    value = closes.get(ticker)
                    try:
                        fvalue = float(value)
                    except (TypeError, ValueError):
                        missing_pairs.append(by_ticker[ticker])
                        continue
                    spot_by_pair[by_ticker[ticker]] = fvalue
                    spot_rows.append({"as_of_date": day, "instrument_id": by_ticker[ticker], "settle_date": day,
                                      "mark_type": "SPOT", "value": fvalue, "source": SRC_SPOT_FWD,
                                      "snapped_at": close_stamp(d)})
                if tickers and not spot_rows:
                    # Only a genuine holiday/no-data day (there WERE FX tickers to ask
                    # for, and none came back) short-circuits here. A futures-only book
                    # (2026-09-18 fix) has `tickers == []` -- trivially "no spot rows"
                    # every day -- and must still reach the FUTURE_PX logic, not be
                    # treated as a holiday.
                    prepared[d] = {"no_closes": True, "missing_pairs": missing_pairs}
                    continue
                # Every mark this day actually needs (inventory._needed_marks -- the same
                # set live.build_requests and close_completeness use, so all three can
                # never drift apart). historical=True: what a PAST close needs -- for an
                # FX option that is closing SPOT only (pair + USD-conversion pairs,
                # covered by spot_rows), never a forward at its expiry.
                needed = _needed_marks(conn, day, historical=True)
                fut_rows, missing_marks = [], []
                for item in needed:
                    if item["mark_type"] != "FUTURE_PX":
                        continue
                    instrument_id = item["instrument_id"]
                    try:
                        settle_value = float(future_px_by_instrument.get(instrument_id, {}).get(day))
                    except (TypeError, ValueError):
                        missing_marks.append({**item, "reason": f"Bloomberg returned no PX_SETTLE for {instrument_id} on {day}"})
                        continue
                    fut_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": item["settle_date"],
                                     "mark_type": "FUTURE_PX", "value": settle_value, "source": SRC_FUTURE,
                                     "snapped_at": close_stamp(d)})
                prepared[d] = {"no_closes": False, "needed": needed, "spot_rows": spot_rows, "spot_by_pair": spot_by_pair,
                               "missing_pairs": missing_pairs, "fut_rows": fut_rows, "missing_marks": missing_marks}
                first_rows += spot_rows + fut_rows
            # A row already official is never rewritten (_drop_already_official).
            write_marks(conn, first_rows if overwrite else _drop_already_official(conn, first_rows))

            # ---- pass 2: FWD_OUTRIGHT day by day, in the caller's order
            results: Dict[date, dict] = {}
            for d in work:
                day, p = d.isoformat(), prepared[d]
                if p["no_closes"]:
                    log(f"  {day}  NO_CLOSES  (holiday or Bloomberg returned nothing; nothing written)")
                    results[d] = {"day": day, "status": "NO_CLOSES", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                                  "missing_pairs": p["missing_pairs"], "missing_marks": [], "realised": None,
                                  "unrealisable": []}
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
                                             "snapped_at": close_stamp(d)})
                            continue
                        if instrument_id not in curves:
                            rows = tenor_rows_by_pair.get(instrument_id, {}).get(day, {})
                            curve = fc.historical_curve(d, rows, spot, None, instrument_id, holidays)
                            if curve["unit"] == fc.UNIT_POINTS and not curve["points"]:
                                curve = fc.historical_curve(d, rows, spot, scale_for(instrument_id), instrument_id, holidays)
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
                                         "source": SRC_SPOT_FWD if direct else SRC_INTERP, "snapped_at": close_stamp(d)})
                    write_marks(conn, fwd_rows if overwrite else _drop_already_official(conn, fwd_rows))
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
                                  "missing_pairs": p["missing_pairs"], "missing_marks": missing_marks,
                                  "realised": realised, "unrealisable": unrealisable}
                except Exception as exc:  # noqa: BLE001 -- one bad day must not end the run for the others
                    log(f"  {day}  ERROR  {exc!r}")
                    results[d] = {"day": day, "status": "ERROR", "closes": len(p["spot_rows"]), "fwd_outrights": 0,
                                  "future_px": len(p["fut_rows"]), "missing_pairs": p["missing_pairs"],
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
    if result["status"] == "NO_CLOSES":
        reasons.append("Bloomberg returned no FX close for this day (a holiday, or no data)")
    else:
        if result.get("error"):
            reasons.append(f"this day's run raised {result['error']}")
        reasons += [f"Bloomberg returned no closing SPOT (PX_LAST) for {pair}" for pair in result["missing_pairs"]]
        reasons += [m["reason"] for m in result["missing_marks"]]
    if not reasons:
        reasons = [f"{m['instrument_id']} {m['mark_type']} {m['settle_date']}: not written" for m in still_missing]
    return list(dict.fromkeys(reasons))[:MAX_STATUS_REASONS]


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
