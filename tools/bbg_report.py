"""The Bloomberg report, `py 2_launcher.py bbg-report`: one text file to send back to Claude.

Run on the Bloomberg PC, on request only (hard rule 8). It runs, each on its own so one
failing step never stops the others:

  1. Connection and coverage: `tools.bbg_diagnostics` on the active database (its JSON kept);
  2. the ticker check: `python -m data.bloomberg.ticker_check --book --search --db <db>`
     (`--quick` drops `--search`), its files written into the run's folder;
  3. the last pull: the status file the live pull writes beside the database, read only;
  4. the environment: times, PC, code version, Python and blpapi, the database's counts.

and writes `reports/bbg_report_<stamp>.txt` (the file to send) with the raw outputs in
`reports/bbg_report_<stamp>/`. Nothing is written to the database, the marks, the trades or
config/contracts.csv. Exit 2 when Bloomberg could not be reached, 1 when anything needs
attention, 0 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DIAGNOSTICS_TIMEOUT = 15 * 60
TICKER_CHECK_TIMEOUT = 60 * 60
RULE = "=" * 90
# What one step may raise without stopping the others (the report says why in its section).
STEP_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, LookupError,
               sqlite3.Error, subprocess.SubprocessError, ImportError, SyntaxError, NameError)
EXIT_WORDS = {0: "everything checked is OK", 1: "something needs attention",
              2: "Bloomberg could not be reached"}
# The ticker check's non-OK verdicts, used when its module cannot be imported to read them.
FALLBACK_TOKENS = ("NO_ANSWER", "NOT_FOUND", "NO_PRICE", "CURRENCY_MISMATCH", "SCALE_MISMATCH",
                   "EXCHANGE_MISMATCH", "NAME_CHECK", "NO_TICKER", "SCALE_FLAG", "PRICE_GAP",
                   "DATE_MISMATCH", "NO_CURVE", "OFF_CURVE", "NOT_A_PROMPT", "NO_OPTIONS",
                   "FORM_MISMATCH", "STYLE_MISMATCH", "LEAD_MISMATCH", "FAIL", "WARN", "ERROR",
                   "NO ANSWER", "CHECK")
FINE_TOKENS = {"OK", "PASS", "SKIPPED"}


# --------------------------------------------------------------------------- running a child

def _child_env() -> dict:
    """The children print Chinese exchange names: UTF-8 on the pipe, whatever the console."""
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def _echo(line: str) -> None:
    """Print one line whatever the console's code page can show."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)


def run_child(cmd: Sequence[str], timeout: int, echo: bool) -> dict:
    """Run `cmd` from the repo root; {code (None when stopped), output, errors, stopped (why,
    or '')}. With `echo` its output (stderr merged) is echoed line by line; without, stdout and
    stderr are kept apart. A child still running after `timeout` seconds is stopped (its own
    PID only) and the report says so."""
    if not echo:
        try:
            done = subprocess.run([str(c) for c in cmd], cwd=str(ROOT), env=_child_env(), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return {"code": None, "output": out, "errors": "",
                    "stopped": f"stopped after {timeout // 60} minutes with no end"}
        return {"code": done.returncode, "output": done.stdout, "errors": done.stderr, "stopped": ""}
    proc = subprocess.Popen([str(c) for c in cmd], cwd=str(ROOT), env=_child_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    stopped: List[str] = []

    def _stop() -> None:
        stopped.append(f"stopped after {timeout // 60} minutes with no end")
        proc.kill()

    timer = threading.Timer(timeout, _stop)
    timer.start()
    lines: List[str] = []
    try:
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            if echo:
                _echo("    " + line.rstrip("\n"))
        proc.wait()
    finally:
        timer.cancel()
    return {"code": None if stopped else proc.returncode, "output": "\n".join(lines), "errors": "",
            "stopped": stopped[0] if stopped else ""}


# --------------------------------------------------------------------------- 1. connection and coverage

def run_diagnostics(db: Path, host: Optional[str], port: Optional[int], folder: Path) -> dict:
    """tools.bbg_diagnostics as JSON, saved to the folder with its plain-text rendering.
    {checks: [...] or None, text, error}."""
    cmd = [sys.executable, "-m", "tools.bbg_diagnostics", "--db", str(db), "--json"]
    if host:
        cmd += ["--host", host]
    if port:
        cmd += ["--port", str(port)]
    got = run_child(cmd, DIAGNOSTICS_TIMEOUT, echo=False)
    (folder / "diagnostics_output.txt").write_text(got["output"] + ("\n--- stderr ---\n" + got["errors"]
                                                                    if got["errors"] else ""), encoding="utf-8")
    if got["stopped"]:
        return {"checks": None, "text": "", "error": f"The diagnostics were {got['stopped']}."}
    raw = got["output"]
    start = raw.find("\n[")
    body = raw if raw.lstrip().startswith("[") else (raw[start + 1:] if start >= 0 else "")
    try:
        checks = json.loads(body)
    except ValueError:
        tail = "\n".join((raw + "\n" + got["errors"]).strip().splitlines()[-15:])
        return {"checks": None, "text": "",
                "error": f"The diagnostics gave no report (exit {got['code']}). Their last lines:\n{tail}"}
    if not isinstance(checks, list):
        return {"checks": None, "text": "", "error": "The diagnostics' JSON was not a list of checks."}
    (folder / "diagnostics.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    width = max((len(str(c.get("name", ""))) for c in checks), default=0)
    text = "\n".join(f"[{str(c.get('status', '')).upper():7s}] {str(c.get('name', '')):{width}s}  "
                     f"{c.get('message', '')}" for c in checks)
    (folder / "diagnostics.txt").write_text(text + "\n", encoding="utf-8")
    return {"checks": checks, "text": text, "error": ""}


def diagnostics_counts(checks: Optional[list]) -> dict:
    counts = {"pass": 0, "warning": 0, "fail": 0}
    for c in checks or []:
        status = str(c.get("status", "")).lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def session_failed(checks: Optional[list]) -> bool:
    return any(c.get("name") == "Bloomberg session connectivity" and c.get("status") == "fail"
               for c in checks or [])


# --------------------------------------------------------------------------- 2. the ticker check

def run_ticker_check(db: Path, host: Optional[str], port: Optional[int], folder: Path, quick: bool) -> dict:
    """The ticker check on the book, its files in `folder`. {code, stdout, text, report,
    worksheet, error}: `text` is its .txt report verbatim ('' when none was written)."""
    cmd = [sys.executable, "-m", "data.bloomberg.ticker_check", "--book", "--db", str(db), "--out", str(folder)]
    if not quick:
        cmd.append("--search")
    if host:
        cmd += ["--host", host]
    if port:
        cmd += ["--port", str(port)]
    got = run_child(cmd, TICKER_CHECK_TIMEOUT, echo=True)
    (folder / "ticker_check_output.txt").write_text(got["output"], encoding="utf-8")
    reports = sorted(folder.glob("bbg_check_*.txt"))
    sheets = sorted(folder.glob("contract_fixes_*.csv"))
    report = reports[-1] if reports else None
    text = report.read_text(encoding="utf-8", errors="replace") if report else ""
    error = f"The ticker check was {got['stopped']}." if got["stopped"] else ""
    return {"code": got["code"], "stdout": got["output"], "text": text, "report": report,
            "worksheet": sheets[-1] if sheets else None, "error": error, "command": " ".join(cmd[1:])}


def problem_tokens() -> List[str]:
    """Every non-OK verdict or status word the ticker check can print, read from its own
    constants (a tuple named *_VERDICTS or *_STATUSES), longest first."""
    tokens = set(FALLBACK_TOKENS)
    try:
        from data.bloomberg import ticker_check as tc
        for name in dir(tc):
            value = getattr(tc, name)
            if name.endswith(("VERDICTS", "STATUSES")) and isinstance(value, (tuple, list, set, frozenset)):
                tokens.update(v for v in value if isinstance(v, str))
    except STEP_ERRORS:
        pass
    return sorted(tokens - FINE_TOKENS, key=len, reverse=True)


def ticker_problem_lines(text: str) -> List[str]:
    """The lines of the ticker check's report that say something is not OK: a row or finding
    carrying a non-OK verdict (a count such as '2 WARN' or a summary row 'NOT_FOUND  3' is
    not one), the 'could not be reached' line, and the housekeeper section."""
    if not text:
        return []
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(t) for t in problem_tokens()) + r")(?![\w])")
    out: List[str] = []
    in_housekeeper = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("For the housekeeper"):
            in_housekeeper = True
            continue
        if in_housekeeper:
            if not stripped:
                in_housekeeper = False
            else:
                out.append("housekeeper " + stripped)
            continue
        if stripped.startswith(("Bloomberg could not be reached", "Bloomberg did not answer")):
            out.append(stripped)
            continue
        for m in pattern.finditer(line):
            before, after = line[:m.start()], line[m.end():]
            if re.search(r"\d\s+$", before) or re.fullmatch(r"\s+\d+\s*", after):
                continue
            out.append(stripped)
            break
    return out


def pull_ticker_counts(result: dict) -> List[str]:
    """The ticker check's own line(s) on the tickers the pull asks for, if it printed any."""
    found: List[str] = []
    lines = result.get("text", "").splitlines()
    for i, line in enumerate(lines):
        if re.search(r"tickers the pull asks for", line, re.I):
            found.append(line.strip())
            if not re.search(r"\d", line):
                nxt = next((x.strip() for x in lines[i + 1:] if x.strip()), "")
                if nxt:
                    found.append(nxt)
            break
    for line in result.get("stdout", "").splitlines():
        if re.search(r"\bpull\b", line, re.I) and re.search(r"\d", line) and "Pull Bloomberg now" not in line:
            found.append(line.strip())
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------------- 3. the last pull

def status_file(db: Path) -> Path:
    try:
        from data.bloomberg.live import status_path
        return Path(status_path(db))
    except STEP_ERRORS:
        return db.with_name(db.name + ".bloomberg_status.json")


def read_last_pull(db: Path, folder: Path) -> dict:
    """The status file as it is on disk (copied into the folder). {status, path, error}."""
    path = status_file(db)
    if not path.exists():
        return {"status": None, "path": path, "error": f"No pull status on file ({path}): no pull has run "
                                                       "against this database yet."}
    shutil.copyfile(path, folder / "last_pull_status.json")
    status = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        return {"status": None, "path": path, "error": "The pull status file is not a JSON object."}
    return {"status": status, "path": path, "error": ""}


def _scalars(block: dict, keys: Sequence[str]) -> List[str]:
    return [f"{k}: {block[k]}" for k in keys if k in block and block[k] not in (None, "", [], {})]


def _entries(block: dict, key: str, label: str, indent: str = "  ") -> List[str]:
    """Every entry of a list of dicts, one line each, keys in the entry's own order."""
    items = block.get(key)
    if not isinstance(items, list) or not items:
        return []
    out = [f"{indent}{label} ({len(items)}):"]
    for it in items:
        if isinstance(it, dict):
            out.append(f"{indent}  - " + ", ".join(f"{k} {v}" for k, v in it.items() if v not in (None, "")))
        else:
            out.append(f"{indent}  - {it}")
    return out


def last_pull_lines(status: dict) -> List[str]:
    """The pull's outcome, compact: every step, every failed mark, every warning and error."""
    out = _scalars(status, ("time", "as_of_date", "host", "connected", "reason", "requested", "written", "failed"))
    progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
    out += [f"progress {line}" for line in _scalars(progress, ("outcome", "finished_at", "sentence"))]
    steps = [s for s in status.get("steps") or [] if isinstance(s, dict)]
    if steps:
        out.append(f"Steps ({len(steps)}):")
        for s in steps:
            out.append(f"  {str(s.get('outcome', '?')):8s} {s.get('label') or s.get('step')}"
                       + (f" ({s['seconds']} s)" if s.get("seconds") is not None else "")
                       + (f": {s['detail']}" if s.get("detail") else ""))
    failed = [i for i in status.get("items") or [] if isinstance(i, dict) and i.get("status") != "OK"]
    if failed:
        out.append(f"Marks not written ({len(failed)} of {len(status.get('items') or [])} asked):")
        for i in failed:
            out.append(f"  {i.get('status', '?')} {i.get('instrument_id', '?')} {i.get('mark_type', '')} "
                       f"{i.get('settle_date', '')} {i.get('ticker', '')} {i.get('detail', '')}".rstrip())
    for key, sub in (("contract_dates", "summary"), ("lme", "summary"), ("options", "futures_options_summary")):
        block = status.get(key)
        if isinstance(block, dict) and block.get(sub):
            out.append(f"{key}: {block[sub]}")
        if isinstance(block, dict):
            out += _entries(block, "skipped", f"{key} skipped")
            out += _entries(block, "missing", f"{key} missing")
    out += _entries(status, "not_requestable", "Futures with no Bloomberg ticker (never asked)", indent="")
    for w in status.get("warnings") or []:
        out.append(f"warning: {w}")
    backfill = status.get("backfill") if isinstance(status.get("backfill"), dict) else None
    if backfill:
        out += ["", "Backfill (past closes):"]
        out += ["  " + x for x in _scalars(backfill, ("running", "outcome", "sentence", "reason", "last_run",
                                                        "finished_at", "error_count", "waiting_on_tickers", "note"))]
        out += _entries(backfill, "errors", "errors")
        out += _entries(backfill, "not_numbers", "values not a number")
        days = backfill.get("days") if isinstance(backfill.get("days"), dict) else {}
        open_days = {d: v for d, v in sorted(days.items()) if isinstance(v, dict) and v.get("status") != "DONE"}
        if open_days:
            out.append(f"  days not complete ({len(open_days)} of {len(days)}):")
            for d, v in open_days.items():
                out.append(f"    {d} {v.get('status')} missing {v.get('missing_count', 0)}"
                           + (": " + "; ".join(str(m) for m in v.get("missing") or []) if v.get("missing") else ""))
        risk = backfill.get("risk_history") if isinstance(backfill.get("risk_history"), dict) else None
        if risk:
            out.append("  risk history:")
            out += ["    " + x for x in _scalars(risk, ("ran_at", "as_of_date", "needs", "up_to_date", "asked",
                                                          "rows_written", "failure_count", "unrequestable_count",
                                                          "not_number_count", "error"))]
            for key, label in (("failures", "failures"), ("unrequestable", "not requestable"),
                               ("not_numbers", "values not a number")):
                out += _entries(risk, key, label, indent="    ")
    return out


def last_pull_problems(status: Optional[dict]) -> List[str]:
    if not status:
        return []
    out = [f"last pull: step {s.get('label') or s.get('step')} {s.get('outcome')}: {s.get('detail', '')}".rstrip(": ")
           for s in status.get("steps") or [] if isinstance(s, dict) and s.get("outcome") in ("failed", "partial")]
    if status.get("connected") is False:
        out.append(f"last pull: not connected: {status.get('reason', '')}")
    n_failed = sum(1 for i in status.get("items") or [] if isinstance(i, dict) and i.get("status") != "OK")
    if n_failed:
        out.append(f"last pull: {n_failed} mark(s) not written (each listed under Last pull)")
    backfill = status.get("backfill") if isinstance(status.get("backfill"), dict) else {}
    if backfill.get("error_count"):
        out.append(f"backfill: {backfill['error_count']} error(s) (listed under Last pull)")
    risk = backfill.get("risk_history") if isinstance(backfill.get("risk_history"), dict) else {}
    if risk.get("error"):
        out.append(f"risk history: did not run: {risk['error']}")
    if risk.get("failure_count"):
        out.append(f"risk history: {risk['failure_count']} ticker(s) failed (listed under Last pull)")
    return out


# --------------------------------------------------------------------------- 4. environment

def _git_line() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace").stdout.strip()
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    changed = [x for x in git("status", "--porcelain").splitlines() if x.strip()]
    subject = git("log", "-1", "--format=%s")
    state = f"dirty, {len(changed)} file(s) changed" if changed else "clean"
    return f"Code: commit {commit} ({state}): {subject}"


def _blpapi_line() -> str:
    try:
        import blpapi
    except ImportError as exc:
        return f"blpapi: not importable ({exc})"
    version = getattr(blpapi, "__version__", "")
    if not version and hasattr(blpapi, "version"):
        version = str(blpapi.version())
    return f"blpapi: {version or 'imported, version not given'}"


def book_day() -> date:
    try:
        from data.bloomberg.live import book_today
        return book_today()
    except STEP_ERRORS:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()


def _db_lines(db: Path, today: date) -> List[str]:
    if not db.exists():
        return [f"Database: {db} (not found)"]
    out = [f"Database: {db} ({db.stat().st_size / 1e6:.1f} MB)"]
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        queries = (
            ("Trades on file", "SELECT COUNT(*) FROM trades", ()),
            ("Trades open (not frozen, last leg on or after the book date)",
             "SELECT COUNT(*) FROM trades t WHERE t.trade_id NOT IN (SELECT trade_id FROM realised_pnl) AND "
             "COALESCE((SELECT MAX(settle_date) FROM trade_legs l WHERE l.trade_id = t.trade_id), '9999-12-31') >= ?",
             (today.isoformat(),)),
            ("Trades not recognised", "SELECT COUNT(*) FROM trades WHERE product = 'UNRECOGNISED'", ()),
            (f"Marks on file for {today}", "SELECT COUNT(*) FROM marks WHERE as_of_date = ?", (today.isoformat(),)),
            (f"Official marks for {today}", "SELECT COUNT(*) FROM marks_official WHERE as_of_date = ?",
             (today.isoformat(),)),
            ("Latest marks date", "SELECT MAX(as_of_date) FROM marks", ()),
            ("Price history rows", "SELECT COUNT(*) FROM price_history", ()),
        )
        for label, sql, params in queries:
            try:
                out.append(f"{label}: {conn.execute(sql, params).fetchone()[0]}")
            except sqlite3.Error as exc:
                out.append(f"{label}: not read ({exc})")
    finally:
        conn.close()
    return out


def environment_lines(db: Path) -> List[str]:
    out: List[str] = []
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        hk, ny = now.astimezone(ZoneInfo("Asia/Hong_Kong")), now.astimezone(ZoneInfo("America/New_York"))
        out.append(f"Time: {hk:%a %d %b %Y %H:%M} Hong Kong, {ny:%a %d %b %Y %H:%M} New York")
    except STEP_ERRORS as exc:
        out.append(f"Time: {now:%Y-%m-%d %H:%M} UTC (time zones not available: {exc})")
    out.append(f"PC: {platform.node()} ({platform.system()} {platform.release()})")
    for fn in (_git_line, _blpapi_line):
        try:
            out.append(fn())
        except STEP_ERRORS as exc:
            out.append(f"{fn.__name__.strip('_').split('_')[0]}: not read ({exc})")
    out.append(f"Python: {sys.version.split()[0]} ({sys.executable})")
    today = book_day()
    out.append(f"Book date: {today} (the day turns at 17:00 New York)")
    host, port = os.environ.get("BLP_HOST", "localhost"), os.environ.get("BLP_PORT", "8194")
    out.append(f"Bloomberg API: {host}:{port} (environment default)")
    try:
        out += _db_lines(db, today)
    except STEP_ERRORS as exc:
        out.append(f"Database {db}: not read ({exc})")
    return out


# --------------------------------------------------------------------------- the report

def _section(title: str, body: Sequence[str]) -> List[str]:
    return ["", RULE, title, RULE, *body]


def verdict_of(diag: dict, ticker: dict, pull: dict, failed_steps: List[str]) -> int:
    if ticker.get("code") == 2 or session_failed(diag.get("checks")):
        return 2
    counts = diagnostics_counts(diag.get("checks"))
    attention = (counts.get("fail") or counts.get("warning") or ticker.get("code") != 0 or failed_steps
                 or last_pull_problems(pull.get("status")))
    return 1 if attention else 0


def build_report(*, stamp: str, db: Path, folder: Path, quick: bool, diag: dict, ticker: dict, pull: dict,
                 env: List[str], failed_steps: List[str]) -> tuple:
    """(text, exit code) of the one file to send."""
    code = verdict_of(diag, ticker, pull, failed_steps)
    counts = diagnostics_counts(diag.get("checks"))
    head = {2: "BLOOMBERG UNREACHABLE", 1: "NEEDS ATTENTION", 0: "ALL OK"}[code]
    lines = [f"Bloomberg report {stamp}", f"Verdict: {head} (exit {code})", ""]
    if diag.get("checks") is not None:
        lines.append(f"Connection and coverage: {counts['pass']} pass, {counts['warning']} warning, "
                     f"{counts['fail']} fail")
    else:
        lines.append("Connection and coverage: did not run (see its section)")
    tc_code = ticker.get("code")
    lines.append(f"Ticker check: exit {tc_code} ({EXIT_WORDS.get(tc_code, 'did not finish')})"
                 + (", quick run (no search)" if quick else ""))
    counts_lines = pull_ticker_counts(ticker)
    lines.append("Tickers the pull asks for: " + ("; ".join(counts_lines) if counts_lines
                                                   else "no count in the ticker check's output"))
    status = pull.get("status")
    if status:
        lines.append(f"Last pull: {status.get('time', '?')}, connected {status.get('connected')}, requested "
                     f"{status.get('requested', '?')}, written {status.get('written', '?')}, failed "
                     f"{status.get('failed', '?')}")
    else:
        lines.append("Last pull: none on file")
    if failed_steps:
        lines.append("Steps of this report that could not run: " + "; ".join(failed_steps))

    problems = [f"diagnostics {str(c.get('status')).upper()}: {c.get('name')}: {c.get('message')}"
                for c in diag.get("checks") or [] if c.get("status") in ("fail", "warning")]
    problems += ["ticker check: " + x for x in ticker_problem_lines(ticker.get("text", ""))]
    if ticker.get("error"):
        problems.append(f"ticker check: {ticker['error']}")
    elif tc_code != 0 and not ticker.get("text"):
        last = next((x.strip() for x in reversed(ticker.get("stdout", "").splitlines()) if x.strip()), "no output")
        problems.append(f"ticker check: exit {tc_code}, no report written: {last}")
    problems += last_pull_problems(status)
    problems += [f"report step not run: {x}" for x in failed_steps]
    lines += _section(f"SUMMARY OF PROBLEMS ({len(problems)})", [f"- {p}" for p in problems] or ["None."])

    body = [diag["text"]] if diag.get("text") else [diag.get("error") or "Did not run."]
    lines += _section("CONNECTION AND COVERAGE (tools/bbg_diagnostics.py)", body)

    tc_body = [f"Command: python {ticker.get('command', '-m data.bloomberg.ticker_check')}",
               f"Exit code: {tc_code} ({EXIT_WORDS.get(tc_code, 'did not finish')})"]
    if ticker.get("error"):
        tc_body.append(ticker["error"])
    if ticker.get("text"):
        tc_body += ["", ticker["text"].rstrip()]
    else:
        tc_body += ["The ticker check wrote no report. Its output:", "", ticker.get("stdout", "").rstrip() or "(none)"]
    lines += _section("TICKERS THE PULL ASKS FOR AND ROOT CHECK (data/bloomberg/ticker_check.py)", tc_body)

    pull_body = [f"Status file: {pull.get('path')}"]
    pull_body += last_pull_lines(status) if status else [pull.get("error") or "Not read."]
    lines += _section("LAST PULL", pull_body)
    lines += _section("ENVIRONMENT", env)

    sheet = ticker.get("worksheet")
    lines += ["", RULE,
              f"Raw outputs: {folder}",
              "Send this file to Claude, as it is.",
              (f"Contract fixes: mark 'yes' in the apply column of {sheet}, then  "
               f"py 2_launcher.py contracts-apply \"{sheet}\"") if sheet else
              "Contract fixes: the ticker check wrote no worksheet this time.",
              RULE]
    return "\n".join(lines) + "\n", code


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="py 2_launcher.py bbg-report",
                                 description="One Bloomberg report to send: diagnostics, ticker check, last pull.")
    ap.add_argument("--db", help="the app's database (default: $RISK_DB, else data/raw/risk.db)")
    ap.add_argument("--host", help="Bloomberg API host (default localhost)")
    ap.add_argument("--port", type=int, help="Bloomberg API port (default 8194)")
    ap.add_argument("--quick", action="store_true", help="the book's tickers only, no search for unknown roots")
    ap.add_argument("--no-open", action="store_true", help="do not open the reports folder at the end")
    return ap


def _step(label: str, failed: List[str], fn, *args, default=None):
    """Run one step; a failure is recorded in `failed` and the next step still runs."""
    try:
        return fn(*args)
    except STEP_ERRORS as exc:
        failed.append(f"{label}: {exc.__class__.__name__}: {exc}")
        return default


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.db:
        db = Path(args.db)
        db = db if db.is_absolute() else ROOT / db
    else:
        from data.paths import get_db_path
        db = Path(get_db_path())
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = REPORTS / f"bbg_report_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"bbg_report_{stamp}.txt"
    failed: List[str] = []
    _echo(f"Bloomberg report on {db}")
    _echo("1/4 Connection and coverage ...")
    diag = _step("connection and coverage", failed, run_diagnostics, db, args.host, args.port, folder,
                 default={"checks": None, "text": "", "error": ""})
    if failed and not diag.get("error"):
        diag["error"] = f"Did not run: {failed[-1]}"
    _echo("2/4 Ticker check (the tickers the pull asks for, the book's roots) ...")
    n_failed = len(failed)
    ticker = _step("ticker check", failed, run_ticker_check, db, args.host, args.port, folder, args.quick,
                   default={"code": None, "stdout": "", "text": "", "report": None, "worksheet": None, "error": ""})
    if len(failed) > n_failed and not ticker.get("error"):
        ticker["error"] = f"Did not run: {failed[-1]}"
    _echo("3/4 Last pull ...")
    n_failed = len(failed)
    pull = _step("last pull", failed, read_last_pull, db, folder,
                 default={"status": None, "path": status_file(db), "error": ""})
    if len(failed) > n_failed and not pull.get("error"):
        pull["error"] = f"Not read: {failed[-1]}"
    _echo("4/4 Environment ...")
    env = _step("environment", failed, environment_lines, db, default=[]) or ["Not read (see the summary)."]
    text, code = build_report(stamp=stamp, db=db, folder=folder, quick=args.quick, diag=diag, ticker=ticker,
                              pull=pull, env=env, failed_steps=failed)
    report_path.write_text(text, encoding="utf-8")
    _echo("")
    _echo(f"Report: {report_path}")
    _echo(f"Size:   {report_path.stat().st_size / 1024:.1f} KB")
    _echo(f"Verdict: exit {code} ({EXIT_WORDS[code]})")
    if ticker.get("worksheet"):
        _echo(f"Contract fixes worksheet: {ticker['worksheet']}")
    _echo("Send this file.")
    if os.name == "nt" and not args.no_open:
        try:
            os.startfile(str(REPORTS))
        except OSError as exc:
            _echo(f"(Could not open the reports folder: {exc})")
    return code


if __name__ == "__main__":
    sys.exit(main())
