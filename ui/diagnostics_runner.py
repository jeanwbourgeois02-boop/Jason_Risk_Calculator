"""The Data tab's "Run Bloomberg check", in the background (2026-09-30).

A press starts one thread per database (a module lock: one run at a time for a database; a
second press while one runs is refused with a sentence). The thread runs, in order,

1. `tools.bbg_diagnostics.run_bloomberg_diagnostics(db_path=<the active database>)`: the fast
   local checks (session, official sources, coverage, last pull), on the database the screens
   show (the sample book included), and
2. `data.bloomberg.ticker_check.check_book(db_path=..., on_progress=...)`: the book's own
   tickers asked of Bloomberg, its progress published as it goes.

The latest result per database path is kept in memory (never on disk) with its start and
finish times and the progress; the Data tab polls `state(db_path)` while a run is going. A run
is refused while a Bloomberg pull is running (the status file's `progress.running`, fresh within
15 minutes). Nothing here writes a mark, a trade or a file, and nothing ever raises to a caller.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNS: Dict[str, dict] = {}
STALE_PULL_SECONDS = 15 * 60     # a "running" pull not updated for this long is a crashed one


def _key(db_path) -> str:
    return str(db_path or "")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pull_running(db_path) -> bool:
    """True while a Bloomberg pull on `db_path` is publishing its progress (fresh within 15 min)."""
    try:
        from data.bloomberg.live import PROGRESS_KEY, read_status
        block = (read_status(db_path) or {}).get(PROGRESS_KEY) or {}
    except Exception:  # noqa: BLE001 -- an unreadable status file is "no pull running"
        return False
    if not block.get("running"):
        return False
    try:
        updated = datetime.fromisoformat(str(block.get("updated_at") or block.get("started_at")))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds() < STALE_PULL_SECONDS
    except (TypeError, ValueError):
        return True


def state(db_path) -> Optional[dict]:
    """A copy of the latest run's state for `db_path`, or None when none ran in this process:
    {running, phase, started_at, finished_at, done, total, words, checks, book, error}."""
    with _LOCK:
        cur = _RUNS.get(_key(db_path))
        return dict(cur) if cur else None


def running(db_path) -> bool:
    s = state(db_path)
    return bool(s and s.get("running"))


def start(db_path) -> Tuple[bool, str]:
    """Start a run on `db_path` in a background thread: (True, '') when started, (False, the
    sentence why not) when a pull or another run is going."""
    if pull_running(db_path):
        return False, "A Bloomberg pull is running: run the check once it has finished."
    key = _key(db_path)
    with _LOCK:
        cur = _RUNS.get(key)
        if cur and cur.get("running"):
            return False, "A Bloomberg check is already running on this book."
        _RUNS[key] = {"running": True, "phase": "connection", "started_at": _now(), "finished_at": "",
                      "done": 0, "total": 0, "words": "Running the connection and data checks",
                      "checks": [], "book": None, "error": ""}
    threading.Thread(target=_run, args=(db_path,), name="bbg-check", daemon=True).start()
    return True, ""


def _update(key: str, **fields) -> None:
    with _LOCK:
        if key in _RUNS:
            _RUNS[key].update(fields)


def _run(db_path) -> None:
    key = _key(db_path)
    try:
        try:
            from tools.bbg_diagnostics import run_bloomberg_diagnostics
            checks = run_bloomberg_diagnostics(db_path=str(db_path) if db_path else None)
            if not isinstance(checks, list):
                raise TypeError("the diagnostics did not return a list")
        except Exception as exc:  # noqa: BLE001 -- one row saying so, the book check still runs
            log.warning("Bloomberg check: the connection checks failed (%s: %s)", type(exc).__name__, exc)
            checks = [{"name": "Bloomberg diagnostics", "status": "fail",
                       "message": f"The connection checks could not run ({type(exc).__name__}: {exc})."}]
        _update(key, checks=checks, phase="tickers", words="Asking Bloomberg for the book's tickers")

        def progress(done: int, total: int, words: str) -> None:
            _update(key, done=int(done or 0), total=int(total or 0), words=str(words or ""))

        try:
            from data.bloomberg.ticker_check import check_book
            book = check_book(db_path=db_path, on_progress=progress)
        except Exception as exc:  # noqa: BLE001 -- check_book never raises; this is the net
            log.warning("Bloomberg check: the book's tickers failed (%s: %s)", type(exc).__name__, exc)
            book = {"ok": False, "reachable": False, "reason": f"The check could not run ({type(exc).__name__}: {exc}).",
                    "summary": "", "counts": {}, "rows": []}
        _update(key, book=book)
    finally:
        _update(key, running=False, phase="done", finished_at=_now())
