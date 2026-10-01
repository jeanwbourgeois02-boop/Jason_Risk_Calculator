"""The Data tab's "Run Bloomberg check", in the background (2026-09-30).

A press starts one thread per database (a module lock: one run at a time for a database; a
second press while one runs is refused with a sentence). The thread runs, in order,

1. `tools.bbg_diagnostics.run_bloomberg_diagnostics(db_path=<the active database>)`: the fast
   local checks (session, official sources, coverage, last pull), on the database the screens
   show, and
2. `data.bloomberg.ticker_check.check_book(db_path=..., on_progress=...)`: the book's own
   tickers asked of Bloomberg, its progress published as it goes, then
3. `diagnosis_text`: the one plain-text report the Data tab's Diagnosis card shows, copies and
   downloads (2026-09-30), kept in the state as `report` with its time `report_at`. The card's
   "Build diagnosis report" and the Bloomberg check fold's "Run Bloomberg check" start the same run.

The latest result per database path is kept in memory (never on disk) with its start and
finish times and the progress; the Data tab polls `state(db_path)` while a run is going. A run
is refused while a Bloomberg pull is running (the status file's `progress.running`, fresh within
15 minutes). The check writes no mark, trade or file, and nothing here ever raises to a caller.

The check's suggested fixes (2026-09-30, user: "i should be able to do this in the app") are
applied here on two presses: `prepare_fixes` writes the rows ticked on the Data tab to a
worksheet in the temp folder (`data.contracts.WORKSHEET_COLUMNS`) and dry-runs
`data.contracts.apply_fixes` on it; `confirm_fixes` applies that same worksheet to
`config/contracts.csv`, rebuilds the trades on file of the roots changed
(`data.ingest.upload.reresolve_roots`), then, in a background thread and best effort, commits
`config/contracts.csv` by explicit path and pushes it. Both are refused while a pull or a check
runs. Their state is in memory per database (`fix_state`).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNS: Dict[str, dict] = {}
_FIXES: Dict[str, dict] = {}
REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_REL = "config/contracts.csv"
GIT_TIMEOUT_SECONDS = 90
JASON_REPO = "Jason_Risk_Calculator"      # the one origin a contract fix is ever pushed to
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
        fx = _FIXES.get(key)
        if fx and not (fx.get("git") or {}).get("running"):
            _FIXES.pop(key, None)          # a new check: the last one's fixes are gone with it
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
            # every ticker the pull asks and a search for a root Bloomberg does not know
            # (2026-09-30); only the switches this check_book takes are passed
            wanted = {"include_pull": True, "include_search": True}
            try:
                import inspect
                params = inspect.signature(check_book).parameters
                extra = {k: v for k, v in wanted.items() if k in params}
            except (TypeError, ValueError):
                extra = {}
            book = check_book(db_path=db_path, on_progress=progress, **extra)
        except Exception as exc:  # noqa: BLE001 -- check_book never raises; this is the net
            log.warning("Bloomberg check: the book's tickers failed (%s: %s)", type(exc).__name__, exc)
            book = {"ok": False, "reachable": False, "reason": f"The check could not run ({type(exc).__name__}: {exc}).",
                    "summary": "", "counts": {}, "rows": []}
        _update(key, book=book, phase="report", words="Writing the diagnosis report")
        try:
            text = diagnosis_text(db_path, checks, book)
        except Exception as exc:  # noqa: BLE001 -- the report says it could not be built
            log.warning("Diagnosis report: could not be built (%s: %s)", type(exc).__name__, exc)
            text = f"The diagnosis report could not be built ({type(exc).__name__}: {exc}).\n"
        _update(key, report=text, report_at=_now())
    finally:
        _update(key, running=False, phase="done", finished_at=_now())


# ---- the diagnosis report (2026-09-30, user: "a diagnosis tab for bloomberg - tells you everything i
# will then paste into claude code"): one plain text, built after every run of the check, from the
# check's own results and what is on file. Read-only; every section is built on its own, so one that
# fails says so and the rest are still written.
RULE = "=" * 78
SUMMARY_LINES = 5


def _section(title: str, body: List[str]) -> List[str]:
    return ["", RULE, title, RULE, *(body or ["(nothing)"])]


def _safe(label: str, fn, failed: List[str]) -> List[str]:
    try:
        return list(fn() or [])
    except Exception as exc:  # noqa: BLE001 -- one section's failure is its line
        failed.append(f"{label}: {type(exc).__name__}: {exc}")
        return [f"Could not be read: {type(exc).__name__}: {exc}"]


def _ro(db_path):
    import sqlite3
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=10)


def _hk_now() -> str:
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(timezone.utc)
        hk, ny = now.astimezone(ZoneInfo("Asia/Hong_Kong")), now.astimezone(ZoneInfo("America/New_York"))
        return f"{hk:%a %d %b %Y %H:%M} Hong Kong ({ny:%a %d %b %H:%M} New York)"
    except Exception:  # noqa: BLE001
        return _now()


def no_bloomberg(checks: List[dict], book: Optional[dict]) -> bool:
    """True when this PC could not reach Bloomberg: the session check failed, or the book check
    says Bloomberg was not reachable."""
    for c in checks or []:
        name = str(c.get("name") or "").lower()
        if "session" in name and str(c.get("status") or "").lower() == "fail":
            return True
    return bool(book) and book.get("reachable") is False


def _check_lines(checks: List[dict]) -> List[str]:
    width = max((len(str(c.get("name", ""))) for c in checks or []), default=0)
    return [f"[{str(c.get('status', '')).upper():7s}] {str(c.get('name', '')):{width}s}  {c.get('message', '')}"
            for c in checks or []]


def _book_lines(book: Optional[dict]) -> List[str]:
    book = book or {}
    out = [f"ok: {book.get('ok')}, reachable: {book.get('reachable')}, requests sent: {book.get('requests_sent')}, "
           f"seconds: {book.get('seconds')}"]
    for key in ("summary", "reason"):
        if book.get(key):
            out.append(f"{key}: {book[key]}")
    if book.get("counts"):
        out.append("counts: " + json.dumps(book["counts"]))
    if book.get("unverified_roots"):
        out.append("roots not verified on a terminal: " + ", ".join(str(r) for r in book["unverified_roots"]))
    text = str(book.get("report_text") or "").rstrip()
    if text:
        out += ["", text]
    else:
        rows = book.get("rows") or []
        if rows:
            out.append(f"rows ({len(rows)}):")
            for r in rows:
                out.append("  " + " | ".join(str(r.get(c, "")) for c in ("status", "area", "what", "instrument_id",
                                                                          "ticker", "bloomberg", "ours", "message",
                                                                          "fix")))
    return out


def _cache_lines(status: Optional[dict]) -> List[str]:
    backfill = (status or {}).get("backfill") if isinstance((status or {}).get("backfill"), dict) else {}
    cache = backfill.get("cache") if isinstance(backfill.get("cache"), dict) else None
    out: List[str] = []
    if cache is None:
        out.append("No cache block in the status file (no pull with the backfill has run on this code yet).")
    else:
        if cache.get("error"):
            out.append(f"error: {cache['error']}")
        if cache.get("sentence"):
            out.append(str(cache["sentence"]))
        for k in ("updated_at", "closes_stored", "history_rows_stored", "history_securities_stored",
                  "history_days_stored", "asked", "skipped_stored", "empty", "known_empty"):
            if k in cache:
                out.append(f"{k}: {cache[k]}")
        if cache.get("days"):
            out.append("days: " + json.dumps(cache["days"]))
        if cache.get("empty_tickers"):
            out.append("came back empty: " + ", ".join(str(t) for t in cache["empty_tickers"]))
        for step, entry in (cache.get("steps") or {}).items():
            out.append(f"step {step}: " + json.dumps(entry))
    dates = (status or {}).get("contract_dates")
    if isinstance(dates, dict):
        keys = ("asked", "on_file", "empty", "known_empty", "summary")
        out.append("contract dates: " + ", ".join(f"{k} {dates[k]}" for k in keys if k in dates))
        if dates.get("empty_tickers"):
            out.append("contract dates came back empty: " + ", ".join(str(t) for t in dates["empty_tickers"]))
    return out


def _library_lines(db_path, as_of: str) -> List[str]:
    from data.bloomberg import library
    conn = _ro(db_path)
    try:
        s = library.summary(conn, as_of)
        out = [f"Tickers a pull asks on {as_of}: {s.get('tickers')} for {s.get('trades')} trades; "
               f"{s.get('not_requestable')} needs with no ticker to ask; library synced {s.get('synced_at') or '?'}"]
        counts: Dict[str, int] = {}
        gaps: List[str] = []
        for r in library.needed_on(conn, as_of, include_unrequestable=True):
            k = f"{r.get('kind')}/{r.get('role')}/{r.get('product')}"
            counts[k] = counts.get(k, 0) + 1
            if not r.get("requestable", True):
                gaps.append(f"  not requestable: {r.get('trade_id')} {r.get('kind')} {r.get('key')} "
                            f"{r.get('settle_date')}: {r.get('reason') or r.get('used_for') or ''}".rstrip(": "))
        out += [f"  {k}: {n} rows" for k, n in sorted(counts.items())]
        return out + gaps
    finally:
        conn.close()


def _missing_lines(db_path, as_of: str) -> List[str]:
    from ui.tabs.data_checks import check_frame, mark_rows
    conn = _ro(db_path)
    try:
        rows = mark_rows(conn, as_of, check_frame(conn, as_of))
    finally:
        conn.close()
    # CLOSED: a past day the exchange was shut, no close exists, nothing to fix: counted, never listed as bad
    bad = [r for r in rows if r["status"] not in ("OK", "CLOSED")]
    out = [f"Official prices the book uses on {as_of}: {len(rows)}; missing "
           f"{sum(1 for r in rows if r['status'] == 'MISSING')}; to check {sum(1 for r in rows if r['status'] == 'CHECK')}"
           f"; exchange closed (not due) {sum(1 for r in rows if r['status'] == 'CLOSED')}"]
    for r in bad:
        why = "; ".join(x for x in (r.get("arrived_reason"), r.get("fresh_reason") if r.get("fresh") is False else "",
                                    r.get("sane_reason") if r.get("sane") is False else "",
                                    r.get("units_reason") if r.get("units_ok") is False else "") if x)
        out.append(f"  {r['status']} {r['instrument_id']} {r['mark_type']} {r['settle_date']} "
                   f"(requestable {r.get('requestable')}; trades {r.get('trades') or '-'}): {why}")
    return out


def _upload_lines(db_path) -> List[str]:
    from data.ingest.upload import last_upload_outcome, possible_duplicates
    conn = _ro(db_path)
    try:
        outcome = last_upload_outcome(conn)
        dups = possible_duplicates(conn) if outcome is None else outcome.get("possible_duplicates") or []
        n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()
    try:
        from data.bloomberg.inventory import unrecognised
        conn = _ro(db_path)
        try:
            unrec = unrecognised(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        unrec = [{"trade_id": "?", "reason": f"could not be read: {exc}"}]
    out = [f"Trades on file: {n_trades}", f"Trades not recognised (on file, no P&L until mapped): {len(unrec)}"]
    out += [f"  not recognised: {u.get('trade_id')} {u.get('broker_symbol') or ''}: {u.get('reason') or ''}"
            for u in unrec]
    if outcome is None:
        out.append("No upload recorded on this database.")
    else:
        rep = outcome.get("report") or {}
        out.append(f"Last upload: {rep.get('filename')} at {rep.get('uploaded_at')} (UTC)")
        out.append("counts: " + json.dumps(outcome.get("counts") or {}))
        out.append("loaded by kind: " + json.dumps(outcome.get("loaded_by_kind") or {}))
        for key in ("need_fix", "not_loaded", "cancelled", "warnings"):
            for r in outcome.get(key) or []:
                out.append(f"  {key}: row {r.get('row_no')} trade {r.get('trade_id')} {r.get('symbol')}: "
                           f"{r.get('reason')}")
    out.append(f"Possible duplicates: {len(dups)}")
    for g in dups:
        out.append(f"  {g.get('kind')}: {g.get('sentence')}")
    return out


def _summary(checks, book, status, sections: Dict[str, List[str]]) -> List[str]:
    """What is wrong, most blocking first, one line each."""
    out: List[str] = []
    if no_bloomberg(checks, book):
        why = str((book or {}).get("reason") or "").strip().rstrip(".?!")
        out.append("No Bloomberg connection on this PC" + (f": {why}" if why else "") + ".")
    if not status:
        out.append("No Bloomberg pull has run against this database yet (no status file).")
    try:
        from tools.bbg_report import last_pull_problems
        out += list(last_pull_problems(status))
    except Exception:  # noqa: BLE001 -- the report's own summary then leaves this out
        pass
    fails = [c for c in checks or [] if str(c.get("status") or "").lower() == "fail"
             and "session" not in str(c.get("name") or "").lower()]
    out += [f"Check failed: {c.get('name')}: {c.get('message')}" for c in fails]
    warned = [str(c.get("name") or "") for c in checks or [] if str(c.get("status") or "").lower() == "warning"]
    if warned:
        out.append(f"{len(warned)} data checks warn: " + "; ".join(warned[:6]) + ("; ..." if len(warned) > 6 else ""))
    counts = (book or {}).get("counts") or {}
    if counts.get("attention") or counts.get("no_answer"):
        out.append(f"Ticker check: {counts.get('attention', 0)} tickers need attention, "
                   f"{counts.get('no_answer', 0)} got no answer, of {counts.get('checked', 0)} checked.")
    missing = sections.get("missing") or []
    if missing and len(missing) > 1:
        out.append(missing[0])
    upload = sections.get("upload") or []
    for line in upload:
        if line.startswith("counts: "):
            try:
                c = json.loads(line[len("counts: "):])
            except ValueError:
                continue
            bits = [f"{c[k]} {w}" for k, w in (("need_fix", "need a fix"), ("not_loaded", "not loaded"),
                                               ("possible_duplicates", "possible duplicates")) if c.get(k)]
            if bits:
                out.append("Last upload: " + ", ".join(bits) + ".")
        elif line.startswith("Trades not recognised") and not line.endswith(": 0"):
            out.append(line + ".")
    if not out:
        out.append("Nothing wrong found: Bloomberg answered, the last pull and the checks are clean.")
    return out


def diagnosis_text(db_path, checks: List[dict], book: Optional[dict]) -> str:
    """The one plain-text report to paste into Claude Code: a summary of what is wrong first (at
    most five lines), then the environment (code version, database and its counts), the connection
    and data checks, the book's ticker check, the last pull (every step, the backfill's errors),
    what Bloomberg stored and what the pull asked (the cache block), the library, the missing marks,
    the last upload's outcome, and the status file in full. Works with no Bloomberg on this PC."""
    from data.bloomberg.live import book_today
    as_of = book_today().isoformat()
    failed: List[str] = []
    db = Path(str(db_path)) if db_path else Path("")
    try:
        from data.bloomberg.live import read_status
        status = read_status(db_path)
    except Exception as exc:  # noqa: BLE001
        status = None
        failed.append(f"status file: {type(exc).__name__}: {exc}")

    def env():
        from tools.bbg_report import environment_lines
        return environment_lines(db)

    def pull():
        if not status:
            return ["No pull status on file: no Bloomberg pull has run against this database yet."]
        from tools.bbg_report import last_pull_lines
        return last_pull_lines(status)

    sections = {
        "env": _safe("environment", env, failed),
        "checks": _check_lines(checks) or ["The connection and data checks did not run."],
        "book": _book_lines(book),
        "pull": _safe("last pull", pull, failed),
        "cache": _safe("cache", lambda: _cache_lines(status), failed),
        "library": _safe("library", lambda: _library_lines(db_path, as_of), failed),
        "missing": _safe("missing marks", lambda: _missing_lines(db_path, as_of), failed),
        "upload": _safe("last upload", lambda: _upload_lines(db_path), failed),
    }
    problems = _summary(checks, book, status, sections)
    head = problems[:SUMMARY_LINES]
    if len(problems) > SUMMARY_LINES:
        head.append(f"(and {len(problems) - SUMMARY_LINES} more, below)")
    lines = ["Risk monitor diagnosis report", f"Built: {_hk_now()}", f"Book date: {as_of}",
             f"Database: {db}", "", "WHAT IS WRONG (first)"]
    lines += [f"{i}. {p}" for i, p in enumerate(head, 1) if not p.startswith("(and ")]
    lines += [p for p in head if p.startswith("(and ")]
    if len(problems) > SUMMARY_LINES:
        lines += _section("EVERY PROBLEM FOUND", [f"- {p}" for p in problems])
    lines += _section("ENVIRONMENT (code version, PC, database)", sections["env"])
    lines += _section("CONNECTION AND DATA CHECKS (tools/bbg_diagnostics.py)", sections["checks"])
    lines += _section("THE BOOK'S TICKERS (data/bloomberg/ticker_check.py check_book)", sections["book"])
    lines += _section("LAST PULL (status file: every step, marks not written, backfill errors)", sections["pull"])
    lines += _section("WHAT IS STORED AND WHAT THE LAST PULL ASKED (backfill cache block)", sections["cache"])
    lines += _section("BLOOMBERG LIBRARY (what the book needs)", sections["library"])
    lines += _section("MISSING OR FLAGGED MARKS", sections["missing"])
    lines += _section("LAST BLOTTER UPLOAD", sections["upload"])
    if failed:
        lines += _section("SECTIONS THAT COULD NOT BE READ", failed)
    raw = json.dumps(status, indent=1, default=str) if status else "No status file."
    lines += _section("STATUS FILE IN FULL (JSON)", raw.splitlines())
    return "\n".join(str(x) for x in lines) + "\n"


def report_filename(state_: Optional[dict]) -> str:
    """'diagnosis_20260930_1412.txt' on this PC's clock, from the report's time."""
    stamp = str((state_ or {}).get("report_at") or "")
    try:
        when = datetime.fromisoformat(stamp)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when = when.astimezone()
    except (TypeError, ValueError):
        when = datetime.now()
    return f"diagnosis_{when:%Y%m%d_%H%M}.txt"


# ---- the check's suggested fixes, applied from the Data tab (2026-09-30)
def fix_state(db_path) -> Optional[dict]:
    """A copy of the fixes' state for `db_path`, or None: {stage ('confirm' | 'applied' | ''),
    rows (the worksheet rows ticked, each with its root_name and effect), dry (apply_fixes' dry
    run), trades {root_id: trade count}, why (a refusal sentence), result (apply_fixes' real run),
    rebuild (reresolve_roots' answer), git {running, line}}."""
    with _LOCK:
        cur = _FIXES.get(_key(db_path))
        if not cur:
            return None
        out = dict(cur)
        out["git"] = dict(cur.get("git") or {})
        return out


def busy_reason(db_path) -> str:
    """The sentence why the fixes cannot be applied now ('' when they can)."""
    if pull_running(db_path):
        return "A Bloomberg pull is running: apply the fixes once it has finished."
    if running(db_path):
        return "A Bloomberg check is running: apply the fixes once it has finished."
    return ""


def worksheet_rows(fixes: List[dict]) -> List[dict]:
    """The worksheet rows (`WORKSHEET_COLUMNS`) of the fixes ticked, each marked apply = yes."""
    from data.contracts import WORKSHEET_COLUMNS
    rows = []
    for f in fixes or []:
        row = {c: str(f.get(c) if f.get(c) is not None else "") for c in WORKSHEET_COLUMNS}
        row["apply"] = "yes"
        rows.append(row)
    return rows


def _write_sheet(rows: List[dict]) -> str:
    from data.contracts import WORKSHEET_COLUMNS
    fd, path = tempfile.mkstemp(prefix="bbg_fixes_", suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(WORKSHEET_COLUMNS), extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def trade_counts(db_path, root_ids) -> Dict[str, int]:
    """{root id: trades on file on that root} (a future's, an option's and an LME ticket's
    instrument carries its root id as base_ccy); {} when the database cannot be read."""
    ids = sorted({str(r) for r in root_ids or [] if r})
    if not ids or not db_path:
        return {}
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            marks = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT i.base_ccy, COUNT(*) FROM trades t JOIN instruments i USING (instrument_id) "
                                f"WHERE i.base_ccy IN ({marks}) GROUP BY i.base_ccy", ids).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- a count is display only
        return {}
    return {str(r): int(n) for r, n in rows}


def prepare_fixes(db_path, fixes: List[dict]) -> dict:
    """The dry run of the fixes ticked (each a fix of the check, its pick as `suggested`):
    nothing is written but a worksheet in the temp folder. The stage is 'confirm' when at least
    one fix would apply."""
    key = _key(db_path)
    why = busy_reason(db_path)
    if not why and not fixes:
        why = "No fix is ticked."
    if why:
        with _LOCK:
            cur = _FIXES.setdefault(key, {})
            if cur.get("stage") == "confirm":
                _remove(cur.get("sheet"))
                cur["stage"] = ""
            cur["why"] = why
        return fix_state(db_path) or {}
    rows = worksheet_rows(fixes)
    shown = [dict(r, root_name=str(f.get("root_name") or ""), effect=str(f.get("effect") or ""))
             for r, f in zip(rows, fixes)]
    try:
        from data.contracts import apply_fixes
        sheet = _write_sheet(rows)
        dry = apply_fixes(sheet, dry_run=True)
    except Exception as exc:  # noqa: BLE001 -- the card says why
        log.warning("Contract fixes: the dry run failed (%s: %s)", type(exc).__name__, exc)
        with _LOCK:
            _FIXES[key] = {"stage": "", "rows": shown,
                           "why": f"The fixes could not be checked ({type(exc).__name__}: {exc})."}
        return fix_state(db_path) or {}
    applied = dry.get("applied") or []
    counts = trade_counts(db_path, {e.get("root_id") for e in applied})
    with _LOCK:
        old = _FIXES.get(key) or {}
        _remove(old.get("sheet"))
        _FIXES[key] = {"stage": "confirm" if applied else "", "rows": shown, "sheet": sheet if applied else "",
                       "dry": dry, "trades": counts,
                       "why": "" if applied else "None of the ticked fixes can be applied."}
    if not applied:
        _remove(sheet)
    return fix_state(db_path) or {}


def cancel_fixes(db_path) -> None:
    """Forget a dry run not confirmed (its worksheet removed)."""
    with _LOCK:
        cur = _FIXES.get(_key(db_path))
        if cur and cur.get("stage") != "applied" and not (cur.get("git") or {}).get("running"):
            _remove(cur.get("sheet"))
            _FIXES.pop(_key(db_path), None)


def _remove(path) -> None:
    try:
        if path:
            os.remove(path)
    except OSError:
        pass


def rebuild_trades(db_path, root_ids: List[str], before: Optional[dict] = None) -> dict:
    """`data.ingest.upload.reresolve_roots` on the database for the roots changed, `before` the
    contract list as it was before the fixes (`data.contracts.load_roots()`, read before
    apply_fixes): {rebuilt, unchanged, failed [{trade_id, why}], changed_instruments, sentence,
    error}; never raises."""
    empty = {"rebuilt": [], "unchanged": [], "failed": [], "changed_instruments": [], "sentence": "", "error": ""}
    if not root_ids or not db_path:
        return empty
    try:
        from data.ingest.upload import reresolve_roots
    except ImportError:
        return dict(empty, sentence="The trades on file could not be rebuilt here yet: upload the blotter again "
                                    "to rebuild them on the new contract list.")
    try:
        from data.ingest.schema import connect
        conn = connect(str(db_path))
        try:
            out = reresolve_roots(conn, list(root_ids), before=before) or {}
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- the card says why
        log.warning("Contract fixes: rebuilding the trades failed (%s: %s)", type(exc).__name__, exc)
        return dict(empty, sentence=f"The trades on file could not be rebuilt ({type(exc).__name__}: {exc}): "
                                    "upload the blotter again to rebuild them.")
    return dict(empty, **out)


def confirm_fixes(db_path) -> dict:
    """Apply the dry run's worksheet for real, rebuild the trades of the roots changed, then
    commit and push `config/contracts.csv` in the background (best effort, never blocking)."""
    key = _key(db_path)
    why = busy_reason(db_path)
    with _LOCK:
        cur = dict(_FIXES.get(key) or {})
    if not why and cur.get("stage") != "confirm":
        why = "Nothing to confirm: tick the fixes and press Apply ticked fixes again."
    if why:
        with _LOCK:
            _FIXES.setdefault(key, {})["why"] = why
        return fix_state(db_path) or {}
    before: Optional[dict] = None
    try:
        from data.contracts import apply_fixes, load_roots
        try:
            before = dict(load_roots())     # the list as it was, for the rebuild (an LME lot size fix)
        except Exception:  # noqa: BLE001 -- the rebuild then reads the new list only
            before = None
        result = apply_fixes(cur["sheet"], dry_run=False)
    except Exception as exc:  # noqa: BLE001 -- the card says why
        log.warning("Contract fixes: applying failed (%s: %s)", type(exc).__name__, exc)
        with _LOCK:
            _FIXES[key] = dict(cur, stage="", sheet="",
                               why=f"The fixes could not be applied ({type(exc).__name__}: {exc}).")
        return fix_state(db_path) or {}
    finally:
        _remove(cur.get("sheet"))
    applied = result.get("applied") or []
    roots = sorted({str(e.get("root_id") or "") for e in applied if e.get("root_id")})
    # a mark-as-confirmed fix alone changes no trade: nothing to rebuild
    moves = sorted({str(e.get("root_id") or "") for e in applied if str(e.get("field") or "") != "bbg_verified"})
    rebuild = rebuild_trades(db_path, moves, before) if moves and result.get("written") else {}
    written = bool(result.get("written"))
    names = sorted({str(r.get("root_name") or "") for r in cur.get("rows") or []
                    if str(r.get("root_id") or "").upper() in roots} - {""})
    with _LOCK:
        _FIXES[key] = dict(cur, stage="applied", sheet="", result=result, rebuild=rebuild, why="",
                           git={"running": written, "line": ""})
    if written:
        log.info("Contract fixes: %d applied to %s (%s)", len(applied), CONTRACTS_REL, ", ".join(names))
        threading.Thread(target=_publish, args=(key, names), name="contracts-commit", daemon=True).start()
    return fix_state(db_path) or {}


def _git(*args: str) -> Tuple[int, str]:
    """(exit code, the last line git said) of one git command in the repo root; never prompts."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, env=env,
                              timeout=GIT_TIMEOUT_SECONDS, creationflags=flags)
    except FileNotFoundError:
        return 127, "git is not installed on this PC"
    except subprocess.TimeoutExpired:
        return 124, f"git did not answer within {GIT_TIMEOUT_SECONDS} s"
    said = [ln.strip() for ln in ((done.stderr or "") + "\n" + (done.stdout or "")).splitlines() if ln.strip()]
    first = next((ln for ln in said if ln.lower().startswith(("fatal:", "error:", "! ["))), "")
    line = first or (said[0] if said else "")
    for lead in ("fatal:", "error:"):
        if line.lower().startswith(lead):
            line = line[len(lead):].strip()
    return done.returncode, line.rstrip(".")


def publish_line(names: List[str]) -> str:
    """Commit `config/contracts.csv` alone, by explicit path, then push: one line saying what
    happened. Never raises."""
    code, _said = _git("rev-parse", "--show-toplevel")
    if code != 0:
        return "Not a git repository: the contract list is changed on this PC only."
    what = ", ".join(n for n in names if n) or "the book's contracts"
    message = f"Contract list: fixes confirmed by Bloomberg ({what})"
    code, said = _git("add", "--", CONTRACTS_REL)
    if code != 0:
        return f"The contract list is changed but could not be committed: {said or 'git add failed'}."
    code, said = _git("commit", "--only", "-m", message, "--", CONTRACTS_REL)
    if code != 0:
        return f"The contract list is changed but could not be committed: {said or 'git commit failed'}."
    # push only to Jason's repo (user rule: never Henry_Risk_Calculator or any other)
    code, url = _git("remote", "get-url", "origin")
    if code != 0 or JASON_REPO not in url:
        return f"Committed on this PC; not pushed: origin is {url if code == 0 and url else 'not set'}, not Jason's repo."
    code, said = _git("push", "origin", "HEAD")
    if code != 0:
        return f"The contract list is committed; the push failed: {said or 'git push failed'}."
    return "The contract list is committed and pushed."


def _publish(key: str, names: List[str]) -> None:
    try:
        line = publish_line(names)
    except Exception as exc:  # noqa: BLE001 -- best effort
        line = f"The contract list is changed but could not be committed ({type(exc).__name__}: {exc})."
    log.info("Contract fixes: %s", line)
    with _LOCK:
        cur = _FIXES.get(key)
        if cur is not None:
            cur["git"] = {"running": False, "line": line}
