"""Backfill past-close Bloomberg marks history (BUILD_PLAN.md section 3/6, Task B).

`engine/pnl/ledger.py::ltd` recomputes LTD from `marks` on demand; it needs one official
SPOT mark per business day per traded USD FX pair to do that. This module rebuilds the
missing days: for every business day in [start, end] it pulls the PX_LAST close of every
USD FX pair the book has ever traded (HistoricalDataRequest, one call per day), stores
the closes as official SPOT marks dated that day (source BBG_BFXFORWARD; interpolated
tenor history, if ever added, would be BBG_INTERP and is never official), and -- only if
`engine.pnl.ledger.realise_settled` is importable -- freezes trades that settled on or
before that day. Days are processed in order so realisation always sees the spot on or
before each settle date. This module no longer writes a `pnl_snapshots` row; that table
and its "one snapshot per day" model are retired by the pnl-engine task, which recomputes
`ltd(conn, date)` straight from `marks` instead.

Limits, stated plainly:
  - Trades are only those in the database. Trades opened and settled between two BNP
    uploads are absent, so the history is only as complete as the BNP file archive.
  - Closes are Bloomberg's daily PX_LAST, not the live-feed's intraday mid. Both are
    stored under the same official source; the snapped_at timestamp tells them apart
    (backfill rows are stamped 15:00 America/New_York on their date).
  - NDFs still realise at spot on the value date, not the fixing.
  - Days that already hold official SPOT marks for every traded pair are skipped unless
    overwrite=True. Realised rows already frozen by an earlier run are never re-priced.
  - Calendar is Monday-Friday only; holidays simply return no close and are reported.
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


def _import_realise_settled():
    """engine.pnl.ledger.realise_settled if importable right now, else None. Guarded per
    BUILD_PLAN.md Task B: this module must not depend on the rest of engine.pnl."""
    try:
        from engine.pnl.ledger import realise_settled
        return realise_settled
    except ImportError:
        return None


def _has_all_closes(conn: sqlite3.Connection, day: str, pairs: List[tuple]) -> bool:
    have = {r[0] for r in conn.execute(
        "SELECT instrument_id FROM marks_official WHERE mark_type='SPOT' AND as_of_date=?", (day,))}
    return all(p in have for p, _ in pairs)


def backfill(db_path, start: date, end: date, fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None, host: str = "localhost", port: int = 8194,
             overwrite: bool = False, log: Callable[[str], None] = print) -> List[dict]:
    """Run the backfill. Returns one dict per business day:
    {day, status: DONE|SKIPPED|NO_CLOSES, closes, missing_pairs, realised, unrealisable}.
    `realised`/`unrealisable` are None on a day where realise_settled could not be
    imported (marks are still written).

    `fetch(session, service, tickers, field, day) -> {ticker: value|None}` defaults to
    pull_marks.fetch_historical; `session_factory` defaults to pull_marks.open_session.
    Both are injectable so the loop is testable without blpapi."""
    from data.ingest.schema import connect
    realise_settled = _import_realise_settled()
    conn = connect(Path(db_path))
    try:
        pairs = traded_pairs(conn)
        if not pairs:
            log("No USD FX pairs with trades in the database; nothing to backfill.")
            return []
        tickers = [t for _, t in pairs]
        by_ticker = {t: p for p, t in pairs}
        days = business_days(start, end)
        todo = [d for d in days if overwrite or not _has_all_closes(conn, d.isoformat(), pairs)]
        log(f"Backfill {start} .. {end}: {len(days)} business days, {len(todo)} to compute, {len(pairs)} pairs.")
        if realise_settled is None:
            log("  note: engine.pnl.ledger.realise_settled not importable; marks only, no realisation this run.")
        if not todo:
            return [{"day": d.isoformat(), "status": "SKIPPED", "closes": 0, "missing_pairs": [],
                     "realised": 0, "unrealisable": []} for d in days]
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
                                "realised": 0, "unrealisable": []})
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
                log(f"  {day}  NO_CLOSES  (holiday or Bloomberg returned nothing; nothing written)")
                results.append({"day": day, "status": "NO_CLOSES", "closes": 0, "missing_pairs": missing_pairs,
                                "realised": None, "unrealisable": []})
                continue
            write_marks(conn, rows)
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
            log(f"  {day}  DONE  closes={len(rows)}  realised={realised}  {flag}")
            results.append({"day": day, "status": "DONE", "closes": len(rows), "missing_pairs": missing_pairs,
                            "realised": realised, "unrealisable": unrealisable})
        done = [r for r in results if r["status"] == "DONE"]
        log(f"Finished: {len(done)} days written, "
            f"{sum(1 for r in results if r['status'] == 'NO_CLOSES')} with no closes, "
            f"{sum(1 for r in results if r['status'] == 'SKIPPED')} skipped.")
        return results
    finally:
        conn.close()


# --------------------------------------------------------------------------- automatic backfill (2026-09-15)
# risk.py no longer has a `backfill` subcommand: this runs by itself, on `start` and at
# the end of every live feed cycle, so nobody has to remember to run it. A module-level
# lock keeps two triggers (start + a feed cycle finishing moments later) from overlapping.
_auto_lock = __import__("threading").Lock()


def _earliest_trade_date(conn: sqlite3.Connection) -> Optional[date]:
    row = conn.execute("SELECT MIN(trade_date) FROM trades").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                   fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None,
                   log: Callable[[str], None] = print,
                   on_progress: Optional[Callable[[int], None]] = None) -> List[dict]:
    """Fill every business day from the earliest trade date to yesterday that lacks a
    complete official close (per `data.bloomberg.inventory.close_completeness`), one day
    at a time, calling `on_progress(days_remaining)` after each so a caller can publish
    it. Days that are already complete are skipped by `backfill()` itself; here we also
    skip requesting them at all when the whole range is already complete."""
    from data.ingest.schema import connect
    from data.bloomberg.inventory import close_completeness
    conn = connect(Path(db_path))
    try:
        earliest = _earliest_trade_date(conn)
        if earliest is None:
            log("Auto-backfill: no trades in the database; nothing to do.")
            return []
        yesterday = date.today() - timedelta(days=1)
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
        results.extend(backfill(db_path, d, d, fetch=fetch, session_factory=session_factory,
                                host=host, port=port, log=log))
        remaining -= 1
        if on_progress:
            on_progress(remaining)
    return results


def start_auto_backfill(db_path, host: str = "localhost", port: int = 8194,
                        fetch: Optional[Callable] = None, session_factory: Optional[Callable] = None):
    """Run `auto_backfill` in a background daemon thread when a Terminal is available,
    writing progress into the existing Bloomberg status file under key "backfill" so the
    Market data tab can show "Backfill: n days remaining". Without a Terminal, writes
    `{"running": False, "reason": ...}` and does nothing else. Never blocks the caller.
    A second call while one is already running is a no-op (the lock is held for the
    whole run), which is how a feed cycle avoids overlapping with `start`'s own trigger."""
    import threading
    from data.bloomberg.live import availability, read_status, write_status

    def _publish(patch: dict) -> None:
        current = read_status(db_path) or {}
        current["backfill"] = {**current.get("backfill", {}), **patch}
        write_status(db_path, current)

    if session_factory is None and fetch is None:
        ok, why = availability(host, port)
        if not ok:
            _publish({"running": False, "reason": why})
            return None

    if not _auto_lock.acquire(blocking=False):
        return None  # a run is already in flight; this trigger is redundant

    def _run():
        try:
            _publish({"running": True, "reason": ""})
            auto_backfill(db_path, host=host, port=port, fetch=fetch, session_factory=session_factory,
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
