"""Backfill the P&L ledger history from Bloomberg daily closes.

The ledger (engine/pnl/ledger.py) needs one snapshot per business day to show Daily /
5d / MTD / YTD. The live feed only writes a snapshot on the days it runs. This module
rebuilds the missing days: for every business day in [start, end] it pulls the PX_LAST
close of every USD FX pair the book has ever traded (HistoricalDataRequest, one call
per day), stores the closes as official SPOT marks dated that day, realises trades that
settled before that day, and writes the day's snapshot. Days are processed in order so
realisation always sees the spot on or before each settle date.

Limits, stated plainly:
  - Trades are only those in the database. Trades opened and settled between two BNP
    uploads are absent, so the history is only as complete as the BNP file archive.
  - Closes are Bloomberg's daily PX_LAST, not the live-feed's intraday mid. Both are
    stored under the same official source; the snapped_at timestamp tells them apart
    (backfill rows are stamped 15:00 America/New_York on their date).
  - NDFs still realise at spot on the value date, not the fixing.
  - Days that already hold a complete snapshot are skipped unless overwrite=True.
    Realised rows already frozen by an earlier run are never re-priced.
  - Calendar is Monday-Friday only; holidays simply return no close and are reported.

Usage (Bloomberg PC):  py -3 -m data.bloomberg.backfill [--start YYYY-MM-DD] [--end YYYY-MM-DD]
Default start is the last business day of the previous year (the YTD reference date).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from data.bloomberg.live import SRC_SPOT_FWD, write_marks
from engine.pnl.aggregate import _last_business_day_of_prev_year

NY = ZoneInfo("America/New_York")

_TRADED_USD_PAIRS_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, i.base_ccy, i.quote_ccy
FROM instruments i JOIN trades t USING (instrument_id)
WHERE i.asset_class = 'FX' AND (i.base_ccy = 'USD' OR i.quote_ccy = 'USD')
ORDER BY i.instrument_id
"""

_SPOT_ON_DATE_SQL = """
SELECT m.instrument_id, i.base_ccy, i.quote_ccy, m.value, m.source, m.snapped_at
FROM marks_official m JOIN instruments i USING (instrument_id)
WHERE m.mark_type = 'SPOT' AND i.asset_class = 'FX' AND m.as_of_date = :day
ORDER BY m.instrument_id, m.snapped_at
"""


def business_days(start: date, end: date) -> List[date]:
    """Monday-Friday dates from start to end inclusive."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def traded_pairs(conn: sqlite3.Connection) -> List[tuple]:
    """(instrument_id, bbg_ticker) for every USD pair with at least one trade, ever."""
    return [(r[0], r[1]) for r in conn.execute(_TRADED_USD_PAIRS_SQL)]


def close_stamp(day: date) -> str:
    """15:00 New York on `day`, with that date's UTC offset resolved (CLAUDE.md mark time)."""
    return datetime(day.year, day.month, day.day, 15, 0, tzinfo=NY).isoformat(timespec="seconds")


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


def _has_complete_snapshot(conn: sqlite3.Connection, day: str) -> bool:
    row = conn.execute("SELECT complete FROM pnl_snapshots WHERE as_of_date = ?", (day,)).fetchone()
    return bool(row and row[0])


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print) -> List[dict]:
    """Run the backfill. Returns one dict per business day:
    {day, status: DONE|SKIPPED|NO_CLOSES, closes, missing_pairs, complete, missing}.

    `fetch(session, service, tickers, field, day) -> {ticker: value|None}` defaults to
    pull_marks.fetch_historical; `session_factory` defaults to pull_marks.open_session.
    Both are injectable so the loop is testable without blpapi."""
    from data.ingest.schema import connect
    from engine.pnl.ledger import take_snapshot
    conn = connect(Path(db_path))
    try:
        pairs = traded_pairs(conn)
        if not pairs:
            log("No USD FX pairs with trades in the database; nothing to backfill.")
            return []
        tickers = [t for _, t in pairs]
        by_ticker = {t: p for p, t in pairs}
        days = business_days(start, end)
        todo = [d for d in days if overwrite or not _has_complete_snapshot(conn, d.isoformat())]
        log(f"Backfill {start} .. {end}: {len(days)} business days, {len(todo)} to compute, {len(pairs)} pairs.")
        if not todo:
            return [{"day": d.isoformat(), "status": "SKIPPED", "closes": 0, "missing_pairs": [],
                     "complete": True, "missing": ""} for d in days]
        session = service = None
        if fetch is None:
            from data.bloomberg import pull_marks as pm
            fetch = pm.fetch_historical
            session, service = (session_factory or (lambda: pm.open_session(host, port)))()
        elif session_factory is not None:
            session, service = session_factory()
        results = []
        for d in days:
            day = d.isoformat()
            if d not in todo:
                results.append({"day": day, "status": "SKIPPED", "closes": 0, "missing_pairs": [],
                                "complete": True, "missing": ""})
                continue
            closes = fetch(session, service, tickers, "PX_LAST", d) or {}
            rows, missing_pairs = [], []
            for ticker in tickers:
                value = closes.get(ticker)
                try:
                    fvalue = float(value)
                except (TypeError, ValueError):
                    missing_pairs.append(by_ticker[ticker])
                    continue
                rows.append({"as_of_date": day, "instrument_id": by_ticker[ticker], "settle_date": day,
                             "mark_type": "SPOT", "value": fvalue, "source": SRC_SPOT_FWD,
                             "snapped_at": close_stamp(d)})
            if not rows:
                log(f"  {day}  NO_CLOSES  (holiday or Bloomberg returned nothing; no snapshot written)")
                results.append({"day": day, "status": "NO_CLOSES", "closes": 0, "missing_pairs": missing_pairs,
                                "complete": False, "missing": ""})
                continue
            write_marks(conn, rows)
            led = take_snapshot(conn, day, rates_on_date(conn, day))
            missing = ",".join(led["missing"] + [u["trade_id"] for u in led["unrealisable"]])
            flag = "complete" if led["complete"] else f"INCOMPLETE missing={missing}"
            log(f"  {day}  DONE  closes={len(rows)}  open={led['open_trades']}  realised={led['realised_trades']}  {flag}")
            results.append({"day": day, "status": "DONE", "closes": len(rows), "missing_pairs": missing_pairs,
                            "complete": led["complete"], "missing": missing})
        done = [r for r in results if r["status"] == "DONE"]
        log(f"Finished: {len(done)} days written, {sum(1 for r in done if r['complete'])} complete, "
            f"{sum(1 for r in results if r['status'] == 'NO_CLOSES')} with no closes, "
            f"{sum(1 for r in results if r['status'] == 'SKIPPED')} skipped.")
        return results
    finally:
        conn.close()


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
    today = date.today()
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
