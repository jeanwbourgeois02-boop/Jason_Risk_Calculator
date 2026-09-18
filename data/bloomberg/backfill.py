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
    never be frozen by realise_settled once it settled) -- one HistoricalDataRequest per
    day (unchanged mechanism, wider ticker set). Also (2026-09-18) every FX option's pair
    and the option's own USD-conversion pairs, for every day the option was open
    including its expiry date (`spot_only_pair_names`). SPOT only for options: no
    historical forward at the expiry and no historical vol, since nothing prices an
    option on a past date -- the closes are what the expiry-day catch-up's payoff and the
    ledger's base->USD conversion read.
  - FWD_OUTRIGHT (2026-09-18): for every FX leg open on that day (trade_date <= day <=
    ... <= settle_date), at the leg's own settle_date. A leg settling on or before that
    day is marked at that day's own SPOT close (same rule the live feed uses). Otherwise
    interpolated linearly in forward points from a historical standard-tenor curve (see
    fwd_curve.historical_points_by_day) -- never extrapolated beyond the last tenor point.
    Bloomberg does not serve the bulk FWD_CURVE field through HistoricalDataRequest the
    way it does live via ReferenceDataRequest, so the historical curve is assembled from
    the standard-tenor outright tickers instead (pull_marks.STANDARD_TENORS), one
    HistoricalDataRequest for the whole date range's tenor tickers, not one per day.
  - FUTURE_PX (2026-09-18): PX_SETTLE of every future open on that day, same batched
    one-request-for-the-whole-range approach.
A day counts as complete (skipped unless overwrite=True) only once ALL of the above are
official for it -- `data.bloomberg.inventory.close_completeness`, the same "needed" set
`data.bloomberg.live.build_requests` uses for the live feed, so the three can never drift
apart. A row already official for its (day, instrument, settle_date, mark_type) is never
rewritten, even when overwrite is False and the day is otherwise incomplete (e.g. a new
trade added a settle_date this day never needed before).

Only if `engine.pnl.ledger.realise_settled` is importable, days are also processed in
order so realisation sees each day's own marks (including the new FWD_OUTRIGHT/FUTURE_PX)
before freezing trades settled on or before it. This module no longer writes a
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
  - The historical forward curve's tenor-to-settle-date mapping comes from each tenor
    ticker's own SETTLE_DT field via HistoricalDataRequest -- UNVERIFIED that this is
    available (and correct per historical day) the same way it is live via
    ReferenceDataRequest; see docs/bloomberg-pc-checklist.md.
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

NY = ZoneInfo("America/New_York")

_SPOT_ON_DATE_SQL = """
SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.source, m.snapped_at
FROM marks_official m JOIN instruments i USING (instrument_id)
WHERE m.mark_type = 'SPOT' AND i.asset_class = 'FX' AND m.as_of_date = :day
ORDER BY m.instrument_id, m.snapped_at
"""

# Open FX legs / futures across a *range* (not one as_of date, unlike live.py's
# _OPEN_FX_SQL / _OPEN_FUTURE_SQL): the historical forward-curve / future-settle history
# is fetched once for the whole span a backfill run needs, not once per day.
_OPEN_FX_LEGS_RANGE_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :end AND l.settle_date >= :start
"""

_OPEN_FUTURES_RANGE_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FUTURE' AND t.trade_date <= :end AND l.settle_date >= :start
"""

# Same cross-leg USD-conversion need as live._cross_usd_legs (EURSEK needs EURUSD and
# USDSEK for delta/P&L, per live.py's own module docstring), but over a *range* rather
# than one as_of date -- 2026-09-18, see traded_pairs' docstring for why this matters.
_OPEN_CROSS_LEG_CCYS_RANGE_SQL = """
SELECT DISTINCT i.base_ccy, i.quote_ccy
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :end AND l.settle_date >= :start
  AND i.base_ccy != 'USD' AND i.quote_ccy != 'USD'
"""

# FX options open at any point in the range, expiry date included (`>= :start`), realised
# since or not -- the range form of live._OPTION_PAIRS_OPEN_ON_DAY_SQL, which says why the
# realised filter must not apply to a past close.
_OPEN_OPTIONS_RANGE_SQL = """
SELECT DISTINCT i.base_ccy, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION' AND t.trade_date <= :end AND i.expiry_date >= :start
"""


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
    connection that cannot write."""
    legs = conn.execute(_OPEN_FX_LEGS_RANGE_SQL, {"start": start.isoformat(), "end": end.isoformat()}).fetchall()
    pairs = {(instrument_id, ticker) for instrument_id, ticker, _settle in legs}
    for pair_name in spot_only_pair_names(conn, start, end):
        row = conn.execute("SELECT instrument_id, bbg_ticker FROM instruments WHERE instrument_id = ?",
                           (pair_name,)).fetchone()
        if row is not None:
            pairs.add((row[0], row[1]))
    return sorted(pairs)


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

    Orientation is `live._usd_pair_name` throughout (one table, in live.py)."""
    from data.bloomberg.live import _usd_pair_name, option_spot_pair_names
    params = {"start": start.isoformat(), "end": end.isoformat()}
    names = set()
    for base, quote in conn.execute(_OPEN_CROSS_LEG_CCYS_RANGE_SQL, params):
        names.update(_usd_pair_name(ccy) for ccy in (base, quote))
    for base, quote in conn.execute(_OPEN_OPTIONS_RANGE_SQL, params):
        names.update(option_spot_pair_names(base, quote))
    return sorted(names)


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
                                fwd_fetch: Optional[Callable] = None) -> Dict[str, Dict[str, List[fc.Point]]]:
    """{instrument_id: {date_iso: [(settle_date, outright), ...]}} for every FX pair with a
    leg open at any point in [span_start, span_end] -- ONE HistoricalDataRequest across
    every pair's standard-tenor tickers for the whole span (2026-09-18: "batch-friendly",
    not one request per day per ticker). `fwd_fetch(session, service, tickers, fields,
    start, end) -> {ticker: {date_iso: {field: value}}}` defaults to
    pull_marks.fetch_historical_series; injectable for tests."""
    legs = conn.execute(_OPEN_FX_LEGS_RANGE_SQL, {"start": span_start.isoformat(), "end": span_end.isoformat()}).fetchall()
    if not legs:
        return {}
    pairs = sorted({instrument_id for instrument_id, _ticker, _settle in legs})
    tenor_map = {pair: _tenor_tickers(pair) for pair in pairs}
    all_tenor_tickers = sorted({t for tickers in tenor_map.values() for t in tickers.values()})
    if fwd_fetch is None:
        from data.bloomberg.pull_marks import fetch_historical_series
        fwd_fetch = fetch_historical_series
    series = fwd_fetch(session, service, all_tenor_tickers, ["PX_LAST", "SETTLE_DT"], span_start, span_end)
    return {pair: fc.historical_points_by_day(series, tenor_map[pair]) for pair in pairs}


def _fetch_future_px_history(conn: sqlite3.Connection, session, service, span_start: date, span_end: date,
                             fut_fetch: Optional[Callable] = None) -> Dict[str, Dict[str, float]]:
    """{instrument_id: {date_iso: PX_SETTLE}} for every future with a leg open at any
    point in [span_start, span_end] -- one HistoricalDataRequest for the whole span.
    `fut_fetch` mirrors `_fetch_fwd_outright_history`'s `fwd_fetch`; defaults to
    pull_marks.fetch_historical_series."""
    futs = conn.execute(_OPEN_FUTURES_RANGE_SQL, {"start": span_start.isoformat(), "end": span_end.isoformat()}).fetchall()
    if not futs:
        return {}
    ticker_to_instrument = {ticker: instrument_id for instrument_id, ticker, _settle in futs}
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


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             fwd_fetch: Optional[Callable] = None, fut_fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print) -> List[dict]:
    """Run the backfill. Returns one dict per business day:
    {day, status: DONE|SKIPPED|NO_CLOSES, closes, fwd_outrights, future_px, missing_pairs,
    missing_marks, realised, unrealisable}. `realised`/`unrealisable` are None on a day
    where realise_settled could not be imported (marks are still written). `missing_marks`
    lists {instrument_id, settle_date, mark_type, reason} for anything this day needed
    (per inventory.close_completeness) but could not resolve -- interpolation out of
    range, no forward-curve/future history for that pair/day, etc; never silently dropped.

    `fetch(session, service, tickers, field, day) -> {ticker: value|None}` (SPOT, one call
    per day) defaults to pull_marks.fetch_historical. `fwd_fetch`/`fut_fetch` (both
    `(session, service, tickers, fields, start, end) -> {ticker: {date_iso: {field:
    value}}}`, ONE call for the whole date range, 2026-09-18) default to
    pull_marks.fetch_historical_series. `session_factory` defaults to
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
        has_futures = conn.execute(
            "SELECT 1 FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id) "
            "WHERE i.asset_class = 'FUTURE' AND t.trade_date <= ? AND l.settle_date >= ? LIMIT 1",
            (end.isoformat(), start.isoformat())).fetchone()
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
        log(f"Backfill {start} .. {end}: {len(days)} business days, {len(todo)} to compute, {len(pairs)} pairs "
            f"(SPOT + FWD_OUTRIGHT + FUTURE_PX).")
        if realise_settled is None:
            log("  note: engine.pnl.ledger.realise_settled not importable; marks only, no realisation this run.")
        if not todo:
            return [{"day": d.isoformat(), "status": "SKIPPED", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                     "missing_pairs": [], "missing_marks": [], "realised": 0, "unrealisable": []} for d in days]
        session = service = None
        own_session = False  # did THIS call open the session itself (pm.open_session)?
        if fetch is None or fwd_fetch is None or fut_fetch is None:
            from data.bloomberg import pull_marks as pm
            if fetch is None:
                fetch = pm.fetch_historical
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
            # FWD_OUTRIGHT / FUTURE_PX history, batched over the whole `todo` span in one
            # request each (2026-09-18) -- never one request per day per ticker.
            span_start, span_end = min(todo), max(todo)
            fwd_curves_by_pair = _fetch_fwd_outright_history(conn, session, service, span_start, span_end, fwd_fetch)
            future_px_by_instrument = _fetch_future_px_history(conn, session, service, span_start, span_end, fut_fetch)

            results = []
            for d in days:
                day = d.isoformat()
                if d not in todo:
                    results.append({"day": day, "status": "SKIPPED", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                                    "missing_pairs": [], "missing_marks": [], "realised": 0, "unrealisable": []})
                    continue
                closes = fetch(session, service, tickers, "PX_LAST", d) or {}
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
                    # every day -- and must still fall through to the FUTURE_PX logic
                    # below, not be treated as a holiday.
                    log(f"  {day}  NO_CLOSES  (holiday or Bloomberg returned nothing; nothing written)")
                    results.append({"day": day, "status": "NO_CLOSES", "closes": 0, "fwd_outrights": 0, "future_px": 0,
                                    "missing_pairs": missing_pairs, "missing_marks": [], "realised": None,
                                    "unrealisable": []})
                    continue

                # FWD_OUTRIGHT + FUTURE_PX for every mark this day actually needs (per
                # inventory._needed_marks -- the same set live.build_requests and
                # close_completeness use, so all three can never drift apart). A leg
                # settling on or before this day is marked at this day's own SPOT (same
                # rule the live feed uses); otherwise interpolated from the historical
                # tenor curve, never extrapolated beyond the last tenor point.
                # historical=True: what a PAST close needs -- for an FX option that is
                # closing SPOT only (pair + USD-conversion pairs, covered by spot_rows
                # above), never a forward at its expiry (inventory._needed_marks).
                needed = _needed_marks(conn, day, historical=True)
                fwd_rows, fut_rows, missing_marks = [], [], []
                for item in needed:
                    instrument_id, settle, mark_type = item["instrument_id"], item["settle_date"], item["mark_type"]
                    if mark_type == "FWD_OUTRIGHT":
                        target = date.fromisoformat(settle)
                        if target <= d:
                            spot = spot_by_pair.get(instrument_id)
                            if spot is None:
                                missing_marks.append({**item, "reason": f"settles on or before {day} but "
                                                      f"{instrument_id} has no SPOT close that day"})
                                continue
                            fwd_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": settle,
                                             "mark_type": "FWD_OUTRIGHT", "value": spot, "source": SRC_SPOT_FWD,
                                             "snapped_at": close_stamp(d)})
                            continue
                        points = fwd_curves_by_pair.get(instrument_id, {}).get(day, [])
                        if not points:
                            missing_marks.append({**item, "reason": f"no forward-curve history for "
                                                  f"{instrument_id} on {day}"})
                            continue
                        value, how = fc.outright_for_date(points, target, spot_by_pair.get(instrument_id), d)
                        if value is None:
                            missing_marks.append({**item, "reason": f"{settle} outside curve "
                                                  f"{points[0][0]}..{points[-1][0]}, not extrapolated"})
                            continue
                        # EXACT tenor -> BBG_BFXFORWARD; interpolated (INTERP /
                        # INTERP_FROM_SPOT) -> BBG_INTERP -- exactly the live path's own
                        # rule (data.bloomberg.live._fwd_outright_rows). Always write the
                        # actual source, never BBG_BFXFORWARD for an interpolated value:
                        # marks_official's own view (data/ingest/schema.py,
                        # OFFICIAL_FALLBACK_SOURCE, 2026-09-18 user decision) is what
                        # decides BBG_INTERP counts as official here (as a fallback, only
                        # when no BBG_BFXFORWARD row exists for the same key) -- this
                        # module's job is just to report the true provenance.
                        source = SRC_SPOT_FWD if how == "EXACT" else SRC_INTERP
                        fwd_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": settle,
                                         "mark_type": "FWD_OUTRIGHT", "value": float(value), "source": source,
                                         "snapped_at": close_stamp(d)})
                    elif mark_type == "FUTURE_PX":
                        settle_value = future_px_by_instrument.get(instrument_id, {}).get(day)
                        if settle_value is None:
                            missing_marks.append({**item, "reason": f"no PX_SETTLE history for {instrument_id} on {day}"})
                            continue
                        fut_rows.append({"as_of_date": day, "instrument_id": instrument_id, "settle_date": settle,
                                         "mark_type": "FUTURE_PX", "value": float(settle_value), "source": SRC_FUTURE,
                                         "snapped_at": close_stamp(d)})
                    # SPOT items in `needed` are already covered by spot_rows above:
                    # traded_pairs() (2026-09-18) includes every open FX pair (crosses
                    # included), every cross's USD-conversion legs, and every FX option's
                    # pair with its own USD-conversion pairs -- the same superset
                    # _needed_marks' SPOT entries are drawn from. A close Bloomberg did
                    # not return is named under `missing_pairs`.

                all_rows = spot_rows + fwd_rows + fut_rows
                new_rows = all_rows if overwrite else _drop_already_official(conn, all_rows)
                write_marks(conn, new_rows)
                if realise_settled is not None:
                    try:
                        led = realise_settled(conn, day)
                        realised, unrealisable = led["realised"], led["unrealisable"]
                        flag = "complete" if not unrealisable else f"unrealisable={[u['trade_id'] for u in unrealisable]}"
                    except Exception as exc:  # engine/pnl/ledger.py is owned by another task; never let its
                        # in-progress state stop marks from being written -- report and move on.
                        realised, unrealisable = None, []
                        flag = f"realise_settled raised: {exc!r}"
                else:
                    realised, unrealisable, flag = None, [], "no realisation (realise_settled unavailable)"
                log(f"  {day}  DONE  closes={len(spot_rows)}  fwd_outrights={len(fwd_rows)}  future_px={len(fut_rows)}"
                    f"  missing={len(missing_pairs) + len(missing_marks)}  realised={realised}  {flag}")
                results.append({"day": day, "status": "DONE", "closes": len(spot_rows), "fwd_outrights": len(fwd_rows),
                                "future_px": len(fut_rows), "missing_pairs": missing_pairs,
                                "missing_marks": missing_marks, "realised": realised, "unrealisable": unrealisable})
            done = [r for r in results if r["status"] == "DONE"]
            log(f"Finished: {len(done)} days written, "
                f"{sum(1 for r in results if r['status'] == 'NO_CLOSES')} with no closes, "
                f"{sum(1 for r in results if r['status'] == 'SKIPPED')} skipped.")
            return results
        finally:
            # 2026-09-18 fix: a session THIS call opened itself (pm.open_session, not an
            # injected session_factory the caller controls) was leaked -- auto_backfill
            # calls backfill() once per incomplete day, so every real run leaked one
            # blpapi session per day processed. Never stop a caller-supplied session.
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
_auto_lock = __import__("threading").Lock()


def _earliest_trade_date(conn: sqlite3.Connection) -> Optional[date]:
    row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                   fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                   fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                   log: Callable[[str], None] = print,
                   on_progress: Optional[Callable[[int], None]] = None) -> List[dict]:
    """Fill every business day from the earliest trade date to yesterday that lacks a
    complete official close (per `data.bloomberg.inventory.close_completeness` --
    SPOT + FWD_OUTRIGHT + FUTURE_PX, 2026-09-18), one day at a time, calling
    `on_progress(days_remaining)` after each so a caller can publish it. Days that are
    already complete are skipped by `backfill()` itself; here we also skip requesting them
    at all when the whole range is already complete. `fwd_fetch`/`fut_fetch` are forwarded
    to `backfill()` unchanged (see its docstring); each call to `backfill()` here covers a
    single day, so its own `session`, once opened, is stopped again before the next day's
    call opens a fresh one (see `backfill()`'s own session-leak fix)."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness
    conn = connect(Path(db_path))
    try:
        earliest = _earliest_trade_date(conn)
        if earliest is None:
            log("Auto-backfill: no trades in the database; nothing to do.")
            return []
        from data.bloomberg.live import book_today
        yesterday = book_today() - timedelta(days=1)
        if earliest > yesterday:
            return []
        completeness = close_completeness(conn, earliest.isoformat(), yesterday.isoformat())
    finally:
        conn.close()
    todo = [date.fromisoformat(d) for d in completeness.loc[~completeness["complete"], "as_of_date"]]
    if not todo:
        log("Auto-backfill: history already complete.")
        if on_progress:
            on_progress(0)
        return []
    log(f"Auto-backfill: {len(todo)} incomplete day(s) between {earliest} and {yesterday}.")
    results = []
    remaining = len(todo)
    if on_progress:
        on_progress(remaining)
    for d in todo:
        results.extend(backfill(db_path, d, d, fetch=fetch, fwd_fetch=fwd_fetch, fut_fetch=fut_fetch,
                                session_factory=session_factory, host=host, port=port, log=log))
        remaining -= 1
        if on_progress:
            on_progress(remaining)
    return results


def start_auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                        fetch: Optional[Callable] = None, fwd_fetch: Optional[Callable] = None,
                        fut_fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None):
    """Run `auto_backfill` in a background daemon thread when a Terminal is available,
    writing progress into the existing Bloomberg status file under key "backfill" so the
    Market data tab can show "Backfill: n days remaining". Without a Terminal, writes
    `{"running": False, "reason": ...}` and does nothing else. Never blocks the caller.
    A second call while one is already running is a no-op (the lock is held for the
    whole run), which is how a feed cycle avoids overlapping with `start`'s own trigger."""
    import threading
    from data.bloomberg.live import availability, patch_status

    def _publish(patch: dict) -> None:
        # patch_status does the read-modify-write of just the "backfill" key under the
        # same lock write_status itself takes, so a concurrent full rewrite by the feed
        # thread can neither tear the status file nor be lost between a read here and a
        # write there (2026-09-18) -- this used to read_status/write_status by hand.
        patch_status(db_path, "backfill", patch)

    if session_factory is None and fetch is None and fwd_fetch is None and fut_fetch is None:
        ok, why = availability(host, port)
        if not ok:
            _publish({"running": False, "reason": why})
            return None

    if not _auto_lock.acquire(blocking=False):
        return None  # a run is already in flight; this trigger is redundant

    def _run():
        try:
            _publish({"running": True, "reason": ""})
            auto_backfill(db_path, host=host, port=port, fetch=fetch, fwd_fetch=fwd_fetch, fut_fetch=fut_fetch,
                          session_factory=session_factory,
                          on_progress=lambda remaining: _publish({"running": remaining > 0, "remaining": remaining}))
        except Exception as exc:  # never let a background thread take the process down
            _publish({"running": False, "reason": f"auto-backfill failed: {exc!r}"})
        finally:
            _publish({"running": False, "remaining": 0})
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
