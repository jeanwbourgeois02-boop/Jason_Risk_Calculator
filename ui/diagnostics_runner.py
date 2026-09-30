"""The Data tab's "Run Bloomberg check", in the background (2026-09-30).

A press starts one thread per database (a module lock: one run at a time for a database; a
second press while one runs is refused with a sentence). The thread runs, in order,

1. `tools.bbg_diagnostics.run_bloomberg_diagnostics(db_path=<the active database>)`: the fast
   local checks (session, official sources, coverage, last pull), on the database the screens
   show, and
2. `data.bloomberg.ticker_check.check_book(db_path=..., on_progress=...)`: the book's own
   tickers asked of Bloomberg, its progress published as it goes.

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
        _update(key, book=book)
    finally:
        _update(key, running=False, phase="done", finished_at=_now())


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
