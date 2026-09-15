"""risk.py -- the one entry point for the risk monitor.

    py risk.py setup      install everything into .venv, create the database, run the tests
    py risk.py start      start the app (opens the browser)
    py risk.py doctor     check every prerequisite and say exactly what to fix

Backfilling P&L history from Bloomberg daily closes is automatic: `start` and every
Bloomberg feed cycle in data/bloomberg/live.py trigger it in the background (see
data/bloomberg/backfill.py). There is no separate command for it.

`setup` runs with whatever Python launched it and creates `.venv` beside this file.
Every other command re-runs itself inside `.venv` so the packages installed by
`setup` are the ones the app uses. No batch files, no PATH edits, no global pip.
Standard library only in this file: it must import before anything is installed.
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
SAMPLE = ROOT / "data" / "sample" / "HA_PNL_SAMPLE_20260818.csv"
SAMPLE_AS_OF = "2026-08-17"
BLPAPI_INDEX = "https://blpapi.bloomberg.com/repository/releases/python/simple/"
MIN_PYTHON = (3, 11)
PORTS = range(8050, 8061)
TABLES = ("instruments", "trades", "trade_legs", "marks", "positions", "realised_pnl")


# ----------------------------------------------------------------------------- helpers

def say(msg: str = "") -> None:
    print(msg, flush=True)


def run(cmd: list, check: bool = True, **kw) -> int:
    say("  > " + " ".join(str(c) for c in cmd))
    code = subprocess.call([str(c) for c in cmd], cwd=str(ROOT), **kw)
    if check and code != 0:
        raise SystemExit(f"FAILED (exit {code}): {' '.join(str(c) for c in cmd)}")
    return code


def in_venv() -> bool:
    try:
        return Path(sys.executable).resolve() == VENV_PY.resolve()
    except OSError:
        return False


def reexec_in_venv(argv: list) -> int:
    """Run this script again under .venv. Returns its exit code."""
    if not VENV_PY.exists():
        say("No .venv yet. Double-click SETUP.cmd (or run:  py risk.py setup), then try again.")
        return 2
    env = dict(os.environ, RISK_MONITOR_VENV="1")
    return subprocess.call([str(VENV_PY), str(ROOT / "risk.py"), *argv], cwd=str(ROOT), env=env)


PNL_MARKER = "# --- risk-monitor pnl function (installed by risk.py setup) ---"
PNL_END_MARKER = "# --- end risk-monitor pnl function ---"


def pnl_function_block() -> str:
    """The `pnl` PowerShell function, pinned to this clone's actual path."""
    return (
        f"{PNL_MARKER}\n"
        "function pnl {\n"
        f"    Set-Location '{ROOT}'\n"
        "    if (-not (git status --porcelain)) { git pull --ff-only origin main }\n"
        "    else { Write-Host 'Local edits present: not pulling from GitHub. Commit or stash them to update.' "
        "-ForegroundColor Yellow }\n"
        "    py -3 risk.py start\n"
        "}\n"
        f"{PNL_END_MARKER}\n"
    )


def _profile_paths() -> list:
    """Windows PowerShell 5.1 and PowerShell 7+ profile paths, respecting Documents
    redirection (OneDrive etc.)."""
    try:
        import ctypes.wintypes
        CSIDL_PERSONAL = 5
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, 0, buf)
        docs = Path(buf.value) if buf.value else Path.home() / "Documents"
    except Exception:
        docs = Path.home() / "Documents"
    return [docs / "WindowsPowerShell" / "profile.ps1", docs / "PowerShell" / "profile.ps1"]


def install_pnl_function() -> list:
    """Add the `pnl` function to every PowerShell profile, replacing a previous copy of
    the block if present so repeat runs never duplicate it. Returns the profile paths
    written. Standard library only (ctypes), so this also runs before `setup` installs
    anything into .venv."""
    block = pnl_function_block()
    written = []
    for profile in _profile_paths():
        profile.parent.mkdir(parents=True, exist_ok=True)
        text = profile.read_text(encoding="utf-8") if profile.exists() else ""
        if PNL_MARKER in text:
            start = text.index(PNL_MARKER)
            end = text.index(PNL_END_MARKER) + len(PNL_END_MARKER)
            # consume one trailing newline so re-writes don't grow blank lines
            after = end + 1 if text[end:end + 1] == "\n" else end
            text = text[:start] + block + text[after:]
        else:
            sep = "\n" if text and not text.endswith("\n") else ""
            text = text + sep + block
        profile.write_text(text, encoding="utf-8")
        written.append(str(profile))
    return written


def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def bloomberg_host() -> tuple:
    return os.environ.get("BLP_HOST", "localhost"), int(os.environ.get("BLP_PORT", "8194"))


def bloomberg_pc() -> bool:
    """Heuristic: a Terminal is installed or answering on the API port."""
    host, port = bloomberg_host()
    return Path("C:/blp").exists() or tcp_open(host, port)


def db_path() -> Path:
    raw = os.environ.get("RISK_DB")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p
    return ROOT / "data" / "raw" / "risk.db"


# ----------------------------------------------------------------------------- setup

def cmd_setup(args) -> int:
    say("=" * 70)
    say(f" risk-monitor setup   ({ROOT})")
    say("=" * 70)

    # 1. interpreter
    v = sys.version_info
    bits = platform.architecture()[0]
    say(f"[1/7] Python {v.major}.{v.minor}.{v.micro} {bits} at {sys.executable}")
    if v < MIN_PYTHON:
        say(f"FAILED: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required. Install 64-bit Python from python.org,")
        say("        tick 'py launcher', then run:  py risk.py setup")
        return 1
    if bits != "64bit":
        say("FAILED: 64-bit Python is required (blpapi and pandas wheels are 64-bit).")
        return 1

    # 2. venv
    say("[2/7] Virtual environment")
    if args.recreate and VENV.exists():
        import shutil
        shutil.rmtree(VENV)
        say("  removed old .venv")
    if VENV_PY.exists():
        say(f"  OK: {VENV} exists")
    else:
        run([sys.executable, "-m", "venv", str(VENV)])
        say(f"  OK: created {VENV}")

    # 3. packages
    say("[3/7] Application packages")
    run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], check=False)
    run([VENV_PY, "-m", "pip", "install", "-r", str(REQUIREMENTS), "--quiet"])
    say("  OK: requirements.txt installed")

    # 4. blpapi (Bloomberg PC only)
    say("[4/7] Bloomberg API package")
    want_blp = args.bloomberg or (not args.no_bloomberg and bloomberg_pc())
    if want_blp:
        code = run([VENV_PY, "-m", "pip", "install", "--index-url", BLPAPI_INDEX, "blpapi"], check=False)
        if code != 0:
            say("FAILED: blpapi did not install. This PC must reach blpapi.bloomberg.com. Re-run later,")
            say("        or run  py risk.py setup --no-bloomberg  on a PC without a Terminal.")
            return 1
        say("  OK: blpapi installed")
    else:
        say("  skipped: no Bloomberg Terminal detected (use --bloomberg to force)")

    # 5. verify + database
    say("[5/7] Verify imports, create database and status file")
    check = (
        "import dash, pandas, openpyxl, xlrd, werkzeug, zoneinfo;"
        "print('  OK: dash', dash.__version__, '| pandas', pandas.__version__);"
        "from ui.app import ensure_schema, get_db_path; ensure_schema(get_db_path());"
        "print('  OK: database', get_db_path())"
    )
    run([VENV_PY, "-c", check])
    if args.sample:
        run([VENV_PY, str(ROOT / "risk.py"), "_load_sample"])

    # 6. pnl PowerShell function
    say("[6/7] 'pnl' PowerShell command")
    try:
        for profile in install_pnl_function():
            say(f"  OK: {profile}")
    except OSError as exc:
        say(f"FAILED: could not write PowerShell profile: {exc}")
        return 1

    # 7. tests
    say("[7/7] Tests")
    if args.skip_tests:
        say("  skipped (--skip-tests)")
    else:
        run([VENV_PY, "-m", "pytest", "tests/", "-q"])

    say()
    say("=" * 70)
    say(" SETUP COMPLETE.   Start the app:   py risk.py start")
    say("                   Anything wrong:  py risk.py doctor")
    if want_blp:
        say("                   Log in to the Bloomberg Terminal before starting.")
    say("=" * 70)
    return 0


def cmd_load_sample(args) -> int:
    """Import the sample BNP report (runs inside .venv; identical rows are skipped)."""
    from ui.app import get_db_path
    from data.ingest.upload import import_report
    try:
        say("  sample: " + str(import_report(SAMPLE.read_bytes(), SAMPLE.name, SAMPLE_AS_OF, get_db_path())))
    except ValueError as exc:
        if "differ from DB" in str(exc):
            say(f"  sample: NOT loaded. The database already holds a different report dated {SAMPLE_AS_OF}")
            say("          (real data, most likely). That is fine; the sample is only for empty databases.")
        else:
            raise
    return 0


# ----------------------------------------------------------------------------- start

def cmd_start(args) -> int:
    if not in_venv():
        return reexec_in_venv(sys.argv[1:])
    from ui.launch import main
    return main(["--force-new"] if args.force_new else [])


# ----------------------------------------------------------------------------- doctor

class Doctor:
    """Collects checks as (name, ok, detail, fix). `ok` may be None = informational."""

    def __init__(self):
        self.rows: list = []

    def add(self, name, ok, detail="", fix=""):
        self.rows.append((name, ok, detail, fix))
        label = "OK    " if ok else ("INFO  " if ok is None else "FAILED")
        say(f"[{label}] {name:14s} {detail}")
        if fix and ok is False:
            say(f"         fix -> {fix}")

    @property
    def failures(self):
        return [r for r in self.rows if r[1] is False]


def doctor_checks(d: Doctor, bloomberg: bool, git: bool = True) -> None:
    v = sys.version_info
    d.add("python", v >= MIN_PYTHON and platform.architecture()[0] == "64bit",
          f"{v.major}.{v.minor}.{v.micro} {platform.architecture()[0]} at {sys.executable}",
          "install 64-bit Python 3.11+ and run  py risk.py setup")

    d.add("venv", in_venv(), str(VENV) if in_venv() else "not running inside .venv",
          "py risk.py setup")

    missing = []
    for mod in ("dash", "pandas", "openpyxl", "xlrd", "werkzeug", "zoneinfo"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    d.add("packages", not missing, "all application packages import" if not missing else "missing: " + ", ".join(missing),
          "py risk.py setup")

    # database
    p = db_path()
    if not p.exists():
        d.add("database", False, f"{p} does not exist", "py risk.py setup   (creates it empty)")
    else:
        try:
            conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
            try:
                have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                lacking = [t for t in TABLES if t not in have]
                counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES if t in have}
            finally:
                conn.close()
            if lacking:
                d.add("database", False, f"{p} lacks tables: {', '.join(lacking)}", "py risk.py setup   (applies the schema)")
            else:
                asof = counts.get("positions", 0)
                d.add("database", True, f"{p}: {counts.get('trades', 0)} trades, {asof} positions, "
                                        f"{counts.get('marks', 0)} marks")
                if counts.get("trades", 0) == 0:
                    d.add("data", None, "database is empty: upload a BNP report in the app, or  py risk.py setup --sample")
        except sqlite3.Error as exc:
            d.add("database", False, f"{p}: {exc}", "the file is not a valid SQLite database; restore it from backup")

    status = p.with_name(p.name + ".bloomberg_status.json")
    d.add("status file", None if status.exists() else False,
          str(status) if status.exists() else "missing (created on start)", "py risk.py start")

    # bloomberg
    host, port = bloomberg_host()
    try:
        import blpapi  # noqa: F401
        have_blp = True
    except ImportError:
        have_blp = False
    terminal = tcp_open(host, port)
    if bloomberg:
        d.add("blpapi", have_blp, "installed" if have_blp else "not installed in .venv", "py risk.py setup --bloomberg")
        d.add("terminal", terminal, f"{host}:{port} {'answers' if terminal else 'no answer'}",
              "log in to the Bloomberg Terminal on this PC, then retry")
    else:
        d.add("bloomberg", None, ("blpapi installed, " if have_blp else "blpapi not installed, ")
              + (f"Terminal answers on {host}:{port}" if terminal else "no Terminal on this PC")
              + "  (run  py risk.py doctor --bloomberg  on the Bloomberg PC)")
    try:
        import json
        st = json.loads(status.read_text()) if status.exists() else None
        if st:
            d.add("last pull", None, f"{st.get('time')}: connected={st.get('connected')}, "
                                     f"written={st.get('written')}, failed={st.get('failed')}, "
                                     f"{st.get('reason', '')}".strip(", "))
    except (OSError, ValueError):
        pass

    # running instances
    try:
        from ui.launch import probe, source_fingerprint, identity, IDENTITY_PREFIX
        me = identity(source_fingerprint())
        found = []
        for pt in PORTS:
            seen = probe(f"http://127.0.0.1:{pt}")
            if seen is None:
                continue
            if seen == me:
                found.append(f"{pt}: current code")
            elif seen.startswith(IDENTITY_PREFIX):
                found.append(f"{pt}: STALE code {seen}")
            else:
                found.append(f"{pt}: another application")
        d.add("ports", None, "; ".join(found) if found else "nothing listening on 8050-8060 (start will use 8050)")
        if any("STALE" in f for f in found):
            d.add("stale app", False, "an old copy of the app is still running", "close its terminal window, then  py risk.py start")
    except ImportError as exc:
        d.add("ports", False, f"cannot import ui.launch: {exc}", "py risk.py setup")

    # git
    if git and (ROOT / ".git").exists():
        def g(*a):
            r = subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        code, branch, _ = g("branch", "--show-current")
        if code == 0:
            fcode, _, err = g("fetch", "origin", "--quiet")
            if fcode != 0 or "error" in err.lower():
                lines = err.splitlines() or ["no detail"]
                last = next((ln for ln in lines if ln.startswith("fatal")), lines[-1])
                d.add("git", False, f"fetch failed: {last}",
                      "offline -> ignore. 'bad object refs/...' -> delete that stale ref:  git update-ref -d <ref>")
            else:
                _, ahead, _ = g("rev-list", "--count", f"origin/{branch}..HEAD")
                _, behind, _ = g("rev-list", "--count", f"HEAD..origin/{branch}")
                _, dirty, _ = g("status", "--porcelain")
                msg = f"branch {branch}: {ahead} to push, {behind} to pull" + (", uncommitted changes" if dirty else "")
                d.add("git", ahead == "0" and behind == "0", msg,
                      "git pull" if behind != "0" else "git push")
        else:
            d.add("git", None, "git not available")


def cmd_doctor(args) -> int:
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    say("=" * 70)
    say(f" risk-monitor doctor   ({ROOT})")
    say("=" * 70)
    d = Doctor()
    doctor_checks(d, bloomberg=args.bloomberg, git=not args.no_git)
    code = 0
    if args.bloomberg:
        say()
        say("Bloomberg request-by-request check (read-only, reports under reports/):")
        code = run([sys.executable, str(ROOT / "tools" / "bloomberg_diagnostic.py"), "--once"], check=False)
        d.add("bbg requests", code == 0, "every spot/forward request answered" if code == 0 else "see FAILED lines above",
              "read the Bloomberg error text above; reports/bloomberg_diagnostic_*.txt has the detail")
    if args.tests:
        say()
        tcode = run([sys.executable, "-m", "pytest", "tests/", "-q"], check=False)
        d.add("tests", tcode == 0, "all passed" if tcode == 0 else "failures above", "the code is broken: git pull, or report the failing test")
    say()
    say("=" * 70)
    if d.failures:
        say(f" {len(d.failures)} problem(s). Fix the first one, then run  py risk.py doctor  again:")
        for name, _, detail, fix in d.failures:
            say(f"   - {name}: {detail}")
            if fix:
                say(f"       fix -> {fix}")
        say("=" * 70)
        return 1
    say(" Everything checks out.   Start the app:  py risk.py start")
    say("=" * 70)
    return 0


# ----------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="py risk.py", description="risk-monitor: setup, start, doctor.")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="install everything into .venv, create the database, run the tests")
    s.add_argument("--bloomberg", action="store_true", help="install blpapi even if no Terminal is detected")
    s.add_argument("--no-bloomberg", action="store_true", help="never install blpapi")
    s.add_argument("--sample", action="store_true", help="import the sample BNP report so the screens show data")
    s.add_argument("--recreate", action="store_true", help="delete and rebuild .venv")
    s.add_argument("--skip-tests", action="store_true")
    s.set_defaults(func=cmd_setup)

    t = sub.add_parser("start", help="start the app and open the browser")
    t.add_argument("--force-new", action="store_true", help="never reuse a running instance")
    t.set_defaults(func=cmd_start)

    dcm = sub.add_parser("doctor", help="check every prerequisite and say what to fix")
    dcm.add_argument("--bloomberg", action="store_true", help="require Bloomberg and test every request")
    dcm.add_argument("--tests", action="store_true", help="also run the test suite")
    dcm.add_argument("--no-git", action="store_true", help="skip the GitHub sync check")
    dcm.set_defaults(func=cmd_doctor)

    sub.add_parser("_load_sample", help=argparse.SUPPRESS).set_defaults(func=cmd_load_sample)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            say(exc.code)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        sys.exit(130)
