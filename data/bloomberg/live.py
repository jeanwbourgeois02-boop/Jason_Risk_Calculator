"""Live Bloomberg feed for the FX cash ladder.

If `blpapi` is importable and a Bloomberg API service answers (Terminal / B-PIPE on
localhost:8194 by default), `LiveFeed` pulls every INTERVAL_SECONDS:
  * SPOT (PX_LAST, live ReferenceDataRequest) for every FX pair with an open leg;
  * FWD_OUTRIGHT for every open leg's own settle date and for the workbook maturity
    (direct broken-date request first, standard-tenor interpolation as fallback, exactly
    as data/bloomberg/pull_marks.py does).
Rows are written to `marks` with INSERT OR REPLACE (same primary key each cycle, new
`snapped_at`), sources BBG_BFXFORWARD (official) / BBG_INTERP (fallback, never official).

If Bloomberg is NOT available nothing is written and nothing is invented: `rates_from_marks`
returns only what the marks table holds, and the status file says why the feed is down.

Every cycle writes a status JSON next to the database (`<db>.bloomberg_status.json`)
listing each requested (instrument, mark_type, settle_date) as OK or FAILED with the
value or the failure detail. `py -3 -m data.bloomberg.live --status` prints it;
`--once` runs a single pull.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

INTERVAL_SECONDS = 120
STALE_AFTER_SECONDS = 600
SRC_SPOT_FWD = "BBG_BFXFORWARD"
SRC_INTERP = "BBG_INTERP"


# --------------------------------------------------------------------------- availability
def availability(host: str = "localhost", port: int = 8194) -> Tuple[bool, str]:
    """(True, '') if blpapi imports and the API port accepts a TCP connection."""
    try:
        import blpapi  # noqa: F401
    except ImportError:
        return False, "blpapi is not installed on this computer"
    import socket
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True, ""
    except OSError as exc:
        return False, f"no Bloomberg API service on {host}:{port} ({exc})"


# --------------------------------------------------------------------------- status file
def status_path(db_path) -> Path:
    p = Path(db_path)
    return p.with_name(p.name + ".bloomberg_status.json")


def write_status(db_path, status: dict) -> None:
    status_path(db_path).write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")


def read_status(db_path) -> Optional[dict]:
    p = status_path(db_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- requests
_OPEN_FX_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l JOIN trades t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX' AND t.trade_date <= :as_of AND l.settle_date >= :as_of
ORDER BY i.instrument_id, l.settle_date
"""


def build_requests(conn: sqlite3.Connection, as_of_date: str) -> list:
    """RequestRows: one SPOT per open pair, one FWD_OUTRIGHT per (pair, open settle
    date) and per pair at the workbook maturity WORKDAY(as_of,5)."""
    from data.bloomberg.pull_marks import RequestRow
    from engine.pnl.pnl import workbook_valuation_date
    rows = conn.execute(_OPEN_FX_SQL, {"as_of": as_of_date}).fetchall()
    out, seen = [], set()
    maturity = workbook_valuation_date(as_of_date)
    for instrument_id, ticker, settle in rows:
        if (instrument_id, "SPOT") not in seen:
            seen.add((instrument_id, "SPOT"))
            out.append(RequestRow(instrument_id, ticker, as_of_date, "SPOT"))
        for day in (settle, maturity):
            if (instrument_id, "FWD_OUTRIGHT", day) not in seen:
                seen.add((instrument_id, "FWD_OUTRIGHT", day))
                out.append(RequestRow(instrument_id, ticker, day, "FWD_OUTRIGHT"))
    return out


# --------------------------------------------------------------------------- one pull
def _live_spot_rows(session, service, requests, as_of: date, diag, snapped: str):
    """Live PX_LAST via ReferenceDataRequest (intraday), unlike pull_marks' close-of-day
    historical path. Returns (rows, failures)."""
    from data.bloomberg.pull_marks import fetch_reference, BloombergRequestError
    spot_reqs = [r for r in requests if r.mark_type == "SPOT"]
    if not spot_reqs:
        return [], []
    tickers = sorted({r.bbg_ticker for r in spot_reqs})
    try:
        data = fetch_reference(session, service, tickers, ["PX_LAST"], diag=diag, tag={"purpose": "LIVE_SPOT"})
    except BloombergRequestError as exc:
        return [], [{"instrument_id": r.instrument_id, "mark_type": "SPOT", "settle_date": r.settle_date,
                     "detail": f"SPOT request failed: {exc}"} for r in spot_reqs]
    rows, failures = [], []
    for r in spot_reqs:
        value = data.get(r.bbg_ticker, {}).get("PX_LAST")
        if value is None:
            failures.append({"instrument_id": r.instrument_id, "mark_type": "SPOT", "settle_date": r.settle_date,
                             "detail": "no PX_LAST returned"})
            continue
        try:
            fvalue = float(value)
        except (TypeError, ValueError):
            failures.append({"instrument_id": r.instrument_id, "mark_type": "SPOT", "settle_date": r.settle_date,
                             "detail": f"PX_LAST not numeric: {value!r}"})
            continue
        rows.append({"as_of_date": as_of.isoformat(), "instrument_id": r.instrument_id,
                     "settle_date": as_of.isoformat(), "mark_type": "SPOT", "value": fvalue,
                     "source": SRC_SPOT_FWD, "snapped_at": snapped})
    return rows, failures


def write_marks(conn: sqlite3.Connection, rows: List[dict]) -> int:
    """INSERT OR REPLACE so each 2-minute cycle refreshes the same key with a new
    snapped_at. Only known instruments; anything else is skipped and reported."""
    known = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    n = 0
    with conn:
        for r in rows:
            if r["instrument_id"] not in known:
                continue
            conn.execute("INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?,?,?)",
                         (r["as_of_date"], r["instrument_id"], r["settle_date"], r["mark_type"],
                          float(r["value"]), r["source"], r["snapped_at"]))
            n += 1
    return n


def pull_once(db_path, as_of_date: Optional[str] = None, host: str = "localhost", port: int = 8194,
              session_factory: Optional[Callable] = None) -> dict:
    """One full cycle. Returns and writes the status dict. Never raises: any exception
    becomes connected=False with the traceback in `reason`."""
    from data.ingest.schema import connect
    started = _now_iso()
    status = {"time": started, "connected": False, "reason": "", "host": f"{host}:{port}",
              "as_of_date": as_of_date, "requested": 0, "written": 0, "failed": 0, "items": [], "warnings": []}
    try:
        ok, why = availability(host, port) if session_factory is None else (True, "")
        if not ok:
            status["reason"] = why
            write_status(db_path, status)
            return status
        from data.bloomberg import pull_marks as pm
        conn = connect(Path(db_path))
        try:
            if as_of_date is None:
                row = conn.execute("SELECT MAX(as_of_date) FROM positions").fetchone()
                as_of_date = row[0] if row and row[0] else date.today().isoformat()
            status["as_of_date"] = as_of_date
            requests = build_requests(conn, as_of_date)
            status["requested"] = len(requests)
            if not requests:
                status.update(connected=True, reason="no open FX legs to price")
                write_status(db_path, status)
                return status
            diag = pm.Diagnostics()
            if session_factory is None:
                session, service = pm.open_session(host, port, diag)
            else:
                session, service = session_factory()
            snapped = _now_iso()  # live pull: real wall-clock time, not the 15:00 NY convention
            today = date.today()
            spot_rows, spot_fail = _live_spot_rows(session, service, requests, today, diag, snapped)
            spot_by_pair = {r["instrument_id"]: r["value"] for r in spot_rows}
            fwd_reqs = [r for r in requests if r.mark_type == "FWD_OUTRIGHT"]
            fwd_rows, warnings, fwd_fail = pm.build_fwd_outright_rows(session, service, fwd_reqs, today,
                                                                     spot_by_pair, diag)
            for r in fwd_rows:
                r["snapped_at"] = snapped
                r["as_of_date"] = today.isoformat()
            rows = spot_rows + fwd_rows
            written = write_marks(conn, rows)
            status.update(connected=True, written=written, warnings=list(warnings)[:50],
                          as_of_marks=today.isoformat())
            ok_keys = {(r["instrument_id"], r["mark_type"], r["settle_date"]): r for r in rows}
            items = []
            for r in requests:
                settle = today.isoformat() if r.mark_type == "SPOT" else r.settle_date
                hit = ok_keys.get((r.instrument_id, r.mark_type, settle))
                if hit:
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "OK", "value": hit["value"], "source": hit["source"], "detail": ""})
                else:
                    detail = next((f.get("detail", "") for f in spot_fail + fwd_fail
                                   if f.get("instrument_id") == r.instrument_id and f.get("mark_type") == r.mark_type
                                   and (r.mark_type == "SPOT" or f.get("settle_date") == r.settle_date)), "not returned")
                    items.append({"instrument_id": r.instrument_id, "mark_type": r.mark_type, "settle_date": settle,
                                  "status": "FAILED", "value": None, "source": "", "detail": detail})
            status["items"] = items
            status["failed"] = sum(1 for i in items if i["status"] != "OK")
        finally:
            conn.close()
    except Exception:
        status["connected"] = False
        status["reason"] = "pull failed: " + traceback.format_exc(limit=3).strip().splitlines()[-1]
        status["traceback"] = traceback.format_exc()
    write_status(db_path, status)
    return status


# --------------------------------------------------------------------------- feed thread
@dataclass
class LiveFeed:
    db_path: Path
    interval: int = INTERVAL_SECONDS
    host: str = "localhost"
    port: int = 8194
    last_status: Optional[dict] = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def start(self) -> "LiveFeed":
        self._thread = threading.Thread(target=self._loop, name="bloomberg-live-feed", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.last_status = pull_once(self.db_path, host=self.host, port=self.port)
            self._stop.wait(self.interval)


def start_feed_if_available(db_path, host: str = "localhost", port: int = 8194,
                            interval: int = INTERVAL_SECONDS) -> Tuple[Optional[LiveFeed], str]:
    """Start the 2-minute feed when Bloomberg is reachable; otherwise write a status file
    explaining why and return (None, reason). Never fabricates data either way."""
    ok, why = availability(host, port)
    if not ok:
        write_status(db_path, {"time": _now_iso(), "connected": False, "reason": why,
                               "host": f"{host}:{port}", "requested": 0, "written": 0, "failed": 0,
                               "items": [], "warnings": []})
        return None, why
    return LiveFeed(Path(db_path), interval, host, port).start(), ""


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
    for w in status.get("warnings", []):
        print("  warning:", w)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bloomberg live feed diagnostics / single pull.")
    parser.add_argument("--db", default=None, help="SQLite path (default: ui.app.get_db_path())")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--once", action="store_true", help="run one pull now and print its status")
    parser.add_argument("--status", action="store_true", help="print the last recorded status")
    args = parser.parse_args(argv)
    if args.db is None:
        from ui.app import get_db_path
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
