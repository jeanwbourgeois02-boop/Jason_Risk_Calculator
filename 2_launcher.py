"""2_launcher.py -- the one entry point for the risk monitor (setup and launch). The repo
root holds exactly three files, sorted in the order you use them: `1_setup.cmd` (for a PC
that doesn't have Python or git yet), this file, and `3_diagnostic.py` (Bloomberg
diagnostics).

    py 2_launcher.py setup      install everything into .venv, create the database, run the tests
    py 2_launcher.py start      start the app (opens the browser)
    py 2_launcher.py            nothing after it, or a double-click on this file: the same as `start`
                                (and a failure then waits for Enter, so the window stays readable)
    py 2_launcher.py doctor     check every prerequisite and say exactly what to fix
    py 2_launcher.py freeze     (re)write requirements.txt from the list below, for `pip install -r`
                                 or CI that doesn't go through this file
    py 2_launcher.py marks-export   Bloomberg PC: write the marks on file to data/bbg_snapshot/ and commit it
    py 2_launcher.py marks-import   PC with no Terminal: load data/bbg_snapshot/ (after a git pull)
    py 2_launcher.py reprice        re-price the FX options from the marks on file, day by day (no Bloomberg)
    py 2_launcher.py bbg-check      Bloomberg PC: check every contract root's ticker against config/contracts.csv
    py 2_launcher.py contracts-apply <worksheet>   apply the check's fixes worksheet to config/contracts.csv

Backfilling P&L history from Bloomberg daily closes is part of every "Pull Bloomberg now"
in the app (data/bloomberg/live.py runs data/bloomberg/backfill.py straight after
today's marks). Nothing is pulled at `start`. There is no separate command for it.

`setup` runs with whatever Python launched it and creates `.venv` beside this file.
Every other command re-runs itself inside `.venv` so the packages installed by
`setup` are the ones the app uses. No batch files, no PATH edits, no global pip.
Standard library only in this file: it must import before anything is installed.

Every third-party package the app needs, in one place (PACKAGES below): `setup` installs
straight from this list, and `doctor` / `setup`'s own verify step import-check
IMPORT_CHECKS. Keeping both in this one file is what stops the "installed but not
checked" / "checked but not installed" gap that bit us once already (numpy, pyyaml,
QuantLib were imported by engine/ui code but missing from an earlier version of both
lists). `requirements.txt` is checked in for tools that do not go through this file
(`pip install -r requirements.txt`, CI); it is generated from PACKAGES by
`py 2_launcher.py freeze`, never edited by hand, and must be re-frozen whenever PACKAGES
changes.
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
PACKAGES_STAMP = VENV / "packages.stamp"
REQUIREMENTS = ROOT / "requirements.txt"
REAL_DB = ROOT / "data" / "raw" / "risk.db"
# The real database holds only what Jason uploads (user, 2026-09-28: "when i put jasons actual
# excel i want that to be the only source of information"). The sample book is reached from
# inside the app (ui/sample_book.py, "View the sample book" on the empty Book tab), in the
# same process and on a throw-away database, so nothing here loads or serves it.
EMPTY_DB_LINE = "database is empty: upload Jason's blotter (Upload blotter in the app)"
BLPAPI_INDEX = "https://blpapi.bloomberg.com/repository/releases/python/simple/"
MIN_PYTHON = (3, 11)
PORTS = range(8050, 8061)
TABLES = ("instruments", "trades", "trade_legs", "marks", "realised_pnl")

# Every third-party package `pip install`s (blpapi is separate: special index, PC-conditional).
# Comment marks *why* each one is here so a future removal of the corresponding import can
# remove the right line instead of leaving a dangling dependency.
PACKAGES = [
    "dash>=4.4,<5",       # ui/ -- the app itself
    "pandas>=3,<4",       # data/ingest, data/bloomberg, engine/ -- blotter CSV/xlsx parsing, series math
    "numpy>=2,<3",        # engine/ -- pnl/ladder/rates numeric work
    "openpyxl>=3.1,<4",   # data/ingest/blotter.py, ui/uploads.py -- .xlsx blotter upload read
    "xlrd>=2,<3",         # data/ingest -- legacy .xls read
    "plotly",             # ui/tabs/market_data.py imports plotly.graph_objects directly
    "werkzeug>=3,<4",     # ui/launch.py imports werkzeug.serving.make_server directly
    "tzdata",             # zoneinfo has no system tz database on Windows; needed at runtime, no import statement
    "pyyaml>=6.0",        # engine/pnl/stress.py imports this as `yaml`
    "QuantLib==1.43",     # engine/rates, engine/options -- curve bootstrap and option pricing
    "scipy>=1.11",        # engine/options/vendor/options_calc -- implied-vol solvers (scipy.optimize.brentq)
    "pyarrow>=15",        # engine/risk/history.py -- pandas.read_parquet of the nm-dashboard market history
]
DEV_PACKAGES = [
    "pytest>=8",          # tests/
    "ruff>=0.6",          # tools/health.py and the ruff hook in .claude/settings.json (pyproject.toml rules)
]
# Modules `doctor` / `setup` import-check after install, to catch "pip said OK but the
# module doesn't actually import" (wrong wheel, ABI mismatch, etc). zoneinfo is stdlib
# (3.9+) but still checked since tzdata above depends on it existing. scipy is checked by
# the two submodules the option pricers really import (2026-09-21: the Options pricer on a
# new PC said scipy was missing while the launcher saw nothing wrong): a bare `import
# scipy` is lazy and succeeds even when its compiled parts cannot load.
IMPORT_CHECKS = ("dash", "pandas", "numpy", "openpyxl", "xlrd", "plotly", "werkzeug",
                  "yaml", "QuantLib", "zoneinfo", "scipy.optimize", "scipy.stats", "pyarrow")


def requirements_text() -> str:
    lines = [
        "# GENERATED by `py 2_launcher.py freeze` from the PACKAGES list in 2_launcher.py -- do not edit",
        "# by hand, it will be overwritten. `py 2_launcher.py setup` does not read this file; it",
        "# installs PACKAGES directly. This file exists only for `pip install -r requirements.txt`",
        "# or CI that doesn't go through 2_launcher.py.",
        *PACKAGES,
        "# blpapi is NOT listed here: `py 2_launcher.py setup` installs it from Bloomberg's own index",
        "# (" + BLPAPI_INDEX + ") on the Bloomberg PC.",
        *DEV_PACKAGES,
        "",
    ]
    return "\n".join(lines)


def cmd_freeze(args) -> int:
    # newline="\n": the repository keeps LF (.gitattributes); Python's default would write
    # CRLF on Windows and make every freeze a line-ending diff.
    REQUIREMENTS.write_text(requirements_text(), encoding="utf-8", newline="\n")
    say(f"wrote {REQUIREMENTS}")
    return 0


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
        say("No .venv yet. Double-click 1_setup.cmd (or run:  py 2_launcher.py setup), then try again.")
        return 2
    env = dict(os.environ, RISK_MONITOR_VENV="1")
    return subprocess.call([str(VENV_PY), str(ROOT / "2_launcher.py"), *argv], cwd=str(ROOT), env=env)


CHELSEA_MARKER = "# --- Jason Risk Monitor: chelsea command (installed by setup) ---"
CHELSEA_END_MARKER = "# --- end Jason Risk Monitor chelsea command ---"
# Neither marker names the launcher file, so they stay byte-for-byte stable across a rename
# of it and install_pnl_function() still finds and replaces its own earlier block.
# This project was forked from Henry's risk monitor, whose setup writes a `pnl` function
# into the same PowerShell profiles. This launcher installs `chelsea` (user, 2026-09-24)
# under its own markers and never reads, replaces or removes a `pnl` block, so running
# either project's setup leaves the other project's command exactly as it was.


def pnl_function_block(newline: str = "\n") -> str:
    """The `chelsea` PowerShell function (the name of this helper is historical), pinned to
    this clone's actual path. It only changes directory and calls `start`: the GitHub sync
    (fetch, stash local edits, fast-forward, reinstall packages if the code changed) lives in
    `sync_with_github` below so it runs identically from `chelsea` and from
    `py 2_launcher.py start`."""
    # A single quote inside the clone's path (O'Brien) is doubled: PowerShell's one escape
    # inside a single-quoted string. Spaces need nothing more than the quotes.
    lines = [
        CHELSEA_MARKER,
        "function chelsea {",
        f"    Set-Location '{str(ROOT).replace(chr(39), chr(39) * 2)}'",
        "    py -3 2_launcher.py start",
        "}",
        CHELSEA_END_MARKER,
    ]
    return newline.join(lines) + newline


def _git(*args, timeout: int = 90) -> tuple:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def sync_with_github() -> tuple:
    """Bring this clone to origin/main before the app starts, whatever state it is in.
    Returns (changed, message). Never raises and never blocks a start: offline, a
    non-git folder or a clone with unpushed commits (the dev machine) just runs the code
    on disk and says so. Local edits and untracked files on a user PC are stashed (`git
    stash list` / `git stash pop` to get them back), so a stray file can no longer pin a
    PC to old code silently -- the failure mode seen on 2026-09-17."""
    if not (ROOT / ".git").exists():
        return False, "not a git clone; running the code on disk"
    try:
        # 10s, not the default 90s: on a PC where GitHub is slow or blocked (seen from
        # China, 2026-09-17) a fetch that will fail anyway must not stall every `start`
        # for up to a minute and a half -- fail fast and run the code already on disk.
        code, _, err = _git("fetch", "origin", "main", "--quiet", timeout=10)
    except subprocess.TimeoutExpired:
        return False, "GitHub slow, running the code on disk"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"git unavailable ({exc.__class__.__name__}); running the code on disk"
    if code != 0:
        last = (err.splitlines() or ["no detail"])[-1]
        return False, f"GitHub unreachable ({last}); running the code on disk"
    _, local, _ = _git("rev-parse", "HEAD")
    _, remote, _ = _git("rev-parse", "origin/main")
    if local == remote:
        return False, f"code {local[:7]} is current with GitHub"
    _, ahead, _ = _git("rev-list", "--count", "origin/main..HEAD")
    if ahead not in ("", "0"):
        return False, f"{ahead} local commit(s) not on GitHub; not updating (push them first), running {local[:7]}"
    _, dirty, _ = _git("status", "--porcelain")
    note = ""
    if dirty:
        import datetime as _dt
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        code, _, err = _git("stash", "push", "--include-untracked", "-m", f"launcher auto-stash {stamp}")
        if code == 0:
            note = " -- local edits were set aside with git stash (git stash pop restores them)"
        else:
            # Whatever is in the way, old code must not run: hard reset to GitHub's main.
            # Only reached with no local commits (checked above); ignored files such as the
            # database are untouched by reset/clean.
            _git("reset", "--hard", "origin/main")
            _git("clean", "-fd")
            note = " -- local edits could not be set aside and were discarded (hard reset)"
    code, _, err = _git("merge", "--ff-only", "origin/main")
    if code != 0:
        _git("reset", "--hard", "origin/main")
        _git("clean", "-fd")
        note = " -- fast-forward failed; folder hard-reset to GitHub's main"
    _, now, _ = _git("rev-parse", "HEAD")
    if now != remote:
        return False, f"update failed ({err.splitlines()[-1] if err else 'no detail'}); running {now[:7]}"
    return True, f"UPDATED {local[:7]} -> {remote[:7]}{note}"


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
    """Add the `chelsea` function to every PowerShell profile (the name of this function is
    historical), replacing this launcher's own earlier block if present so repeat runs never
    duplicate it. Every other byte of the profile (BOM, line endings, another project's
    `pnl` block) is written back exactly as it was read. Returns the profile paths written.
    Standard library only (ctypes), so this also runs before `setup` installs anything into
    .venv."""
    written = []
    for profile in _profile_paths():
        profile.parent.mkdir(parents=True, exist_ok=True)
        raw = profile.read_bytes() if profile.exists() else b""
        # surrogateescape round-trips any byte, so a profile that is not UTF-8 is untouched
        text = raw.decode("utf-8", "surrogateescape")
        newline = "\r\n" if "\r\n" in text or (not text and os.name == "nt") else "\n"
        block = pnl_function_block(newline)
        end = text.find(CHELSEA_END_MARKER)
        start = text.rfind(CHELSEA_MARKER, 0, end) if end >= 0 else -1
        if start >= 0:
            end += len(CHELSEA_END_MARKER)
            # consume the block's own line ending so re-writes don't grow blank lines
            end += 2 if text.startswith("\r\n", end) else 1 if text.startswith("\n", end) else 0
            text = text[:start] + block + text[end:]
        else:
            sep = newline if text and not text.endswith(("\n", "\r")) else ""
            text = text + sep + block
        new = text.encode("utf-8", "surrogateescape")
        if new != raw:
            profile.write_bytes(new)
        written.append(str(profile))
    return written


def _powershell_shells() -> list:
    """The PowerShell executables on this PC that read the profiles above: Windows
    PowerShell 5.1 (always present) and PowerShell 7+ (`pwsh`) when installed."""
    import shutil
    return [exe for exe in ("powershell", "pwsh") if shutil.which(exe)]


def _powershell(exe: str, command: str, timeout: int = 60) -> str:
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", command],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def ensure_profile_loads() -> list:
    """Make sure each PowerShell on this PC will run its profile, so `chelsea` exists in a
    new window. Windows PowerShell 5.1's default policy on a home PC is Restricted, under
    which the profile is skipped with a red error and `chelsea` is "not recognized" (the
    fresh-machine walk of 2026-09-28). RemoteSigned for the current user needs no admin and
    lets a local profile run; a policy pinned by group policy cannot be changed here and is
    reported with the way round it. Returns one line per shell."""
    if os.name != "nt":
        return []
    notes = []
    for exe in _powershell_shells():
        try:
            policy = _powershell(exe, "(Get-ExecutionPolicy).ToString()") or "unknown"
            if policy in ("Restricted", "AllSigned"):
                after = _powershell(exe, "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force; "
                                         "(Get-ExecutionPolicy).ToString()")
                if after in ("Restricted", "AllSigned"):
                    notes.append(f"{exe}: execution policy {policy} is pinned by policy, so the profile does not load and "
                                 f"`chelsea` will not exist there. Start with:  py 2_launcher.py start")
                else:
                    notes.append(f"{exe}: execution policy {policy} -> RemoteSigned for the current user, "
                                 f"so the profile (and `chelsea`) loads")
            else:
                notes.append(f"{exe}: execution policy {policy}, the profile loads")
        except (OSError, subprocess.SubprocessError) as exc:
            notes.append(f"{exe}: could not read the execution policy ({exc.__class__.__name__})")
    return notes


def chelsea_available() -> list:
    """(shell, ok, detail) per PowerShell on this PC: whether a new window of it knows
    `chelsea`, checked the way a new window would (its profile loaded, `Get-Command`)."""
    if os.name != "nt":
        return []
    rows = []
    for exe in _powershell_shells():
        try:
            r = subprocess.run([exe, "-NonInteractive", "-Command",
                                "[bool](Get-Command chelsea -ErrorAction SilentlyContinue)"],
                               capture_output=True, text=True, timeout=60)
            ok = r.stdout.strip().endswith("True")
            err = (r.stderr.strip().splitlines() or [""])[0]
            rows.append((exe, ok, "chelsea is defined" if ok else ("not defined" + (f": {err}" if err else ""))))
        except (OSError, subprocess.SubprocessError) as exc:
            rows.append((exe, False, f"could not check ({exc.__class__.__name__})"))
    return rows


def venv_python_runs() -> bool:
    """False when .venv exists but its interpreter no longer starts: the Python it was
    created from was removed or upgraded (Windows venvs hold a `home` path, not a copy),
    the case after a python.org upgrade. Such a venv is rebuilt by `setup`, never trusted."""
    try:
        return subprocess.call([str(VENV_PY), "-c", "pass"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0
    except OSError:
        return False


LONG_PATH_WARN = 110   # characters of ROOT beyond which a package's deepest file can pass MAX_PATH


def long_paths_enabled() -> bool:
    """Windows' 260-character path limit is lifted only with LongPathsEnabled in the registry
    (and a manifest, which python.exe has). Without it, `pip install numpy` into a venv under
    a deep folder fails with WinError 206 (seen 2026-09-28 at a 180-character clone path)."""
    if os.name != "nt":
        return True
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem")
        try:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    # "localhost" resolves to both ::1 and 127.0.0.1; when nothing answers on ::1 (the
    # common case) socket.create_connection tries it first and pays the full timeout
    # before ever trying the working IPv4 address (same issue and fix as
    # data.bloomberg.live.availability(), 2026-09-17). A caller-supplied host other than
    # the literal "localhost" is left untouched.
    connect_host = "127.0.0.1" if host == "localhost" else host
    try:
        with socket.create_connection((connect_host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _listener(port: int) -> str:
    """'PID 1234 (python.exe)' for the process listening on 127.0.0.1:port, or '' when it
    cannot be named (netstat / tasklist are Windows; elsewhere the port alone is reported)."""
    if os.name != "nt":
        return ""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=15).stdout
        pid = next((int(p[4]) for p in (ln.split() for ln in out.splitlines())
                    if len(p) >= 5 and p[0].upper() == "TCP" and p[1].endswith(f":{port}") and p[3] == "LISTENING"), None)
        if pid is None:
            return ""
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
        name = out.strip().split(",")[0].strip('"') if out.strip().startswith('"') else "unknown process"
        return f"PID {pid} ({name})"
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def port_preflight() -> list:
    """What holds the app's ports before a start, in plain words, one line per port that
    needs one. ui/launch.py already stops a risk monitor that runs old code and reuses one on
    the current code, but a listener that does not answer as a risk monitor at all -- a copy
    of the app that hung, or another program -- is silently skipped for the next port, so a
    bookmark on the old port would still show the old page. Standard library only: this runs
    before the re-exec into .venv."""
    from ui.launch import IDENTITY_PREFIX, probe
    lines = []
    for port in PORTS:
        if not tcp_open("127.0.0.1", port, timeout=0.2):
            continue
        seen = probe(f"http://127.0.0.1:{port}")
        if seen is None:
            who = _listener(port) or "a process"
            lines.append(f"Port {port}: held by {who} that does not answer as a risk monitor (a copy of the app "
                         f"that hung, or another program). The app starts on the next free port; an old "
                         f"bookmark on {port} would show that process, not the app. To free the port: "
                         f"taskkill /PID <pid> /F  (or close its window).")
        elif seen.startswith(IDENTITY_PREFIX):
            lines.append(f"Port {port}: a risk monitor is running ({seen}): reused if it is the current code, "
                         f"stopped and replaced if not.")
        else:
            lines.append(f"Port {port}: another application; the app takes the next free port.")
    return lines


def bloomberg_host() -> tuple:
    return os.environ.get("BLP_HOST", "localhost"), int(os.environ.get("BLP_PORT", "8194"))


def terminal_installed() -> bool:
    """A Bloomberg Terminal is installed on this PC (its C:\blp folder exists), whether or
    not anyone is logged in right now. Separate from tcp_open() so tests can pin it."""
    return Path("C:/blp").exists()


def bloomberg_pc() -> bool:
    """Heuristic: a Terminal is installed or answering on the API port."""
    host, port = bloomberg_host()
    return terminal_installed() or tcp_open(host, port)


def db_path() -> Path:
    raw = os.environ.get("RISK_DB")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p
    return REAL_DB


def _trades_count() -> int:
    """0 when the database does not exist yet, has no `trades` table, or is genuinely
    empty. An empty database is said so and left empty: the blotter upload is the one
    trade source (hard rule 1); nothing seeds it."""
    p = db_path()
    if not p.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


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
        say("        tick 'py launcher', then run:  py 2_launcher.py setup")
        return 1
    if bits != "64bit":
        say("FAILED: 64-bit Python is required (blpapi and pandas wheels are 64-bit).")
        return 1

    # 2. venv
    say("[2/7] Virtual environment")
    if len(str(ROOT)) > LONG_PATH_WARN and not long_paths_enabled():
        say(f"  WARNING: this folder's path is {len(str(ROOT))} characters long and Windows long paths are off,")
        say("           so a package install can fail with 'WinError 206: the filename is too long'. If it does,")
        say("           move the folder somewhere short (C:\\Users\\<you>\\Jason Risk Monitor) or enable long paths")
        say("           (as administrator:  Set-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem "
            "LongPathsEnabled 1) and run setup again.")
    if VENV.exists() and (args.recreate or (VENV_PY.exists() and not venv_python_runs())):
        import shutil
        why = "--recreate" if args.recreate else "its Python no longer runs (removed or upgraded since it was created)"
        shutil.rmtree(VENV)
        say(f"  removed old .venv: {why}")
    if VENV_PY.exists():
        say(f"  OK: {VENV} exists")
    else:
        run([sys.executable, "-m", "venv", str(VENV)])
        say(f"  OK: created {VENV}")

    # 3. packages
    say("[3/7] Application packages")
    run([VENV_PY, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], check=False)
    run([VENV_PY, "-m", "pip", "install", *PACKAGES, *DEV_PACKAGES, "--quiet"])
    _write_packages_stamp()  # so the first `start` after setup trusts this install (fast path)
    say(f"  OK: {len(PACKAGES) + len(DEV_PACKAGES)} packages installed (see PACKAGES in 2_launcher.py)")

    # 4. blpapi (Bloomberg PC only)
    say("[4/7] Bloomberg API package")
    want_blp = args.bloomberg or (not args.no_bloomberg and bloomberg_pc())
    if want_blp:
        code = run([VENV_PY, "-m", "pip", "install", "--index-url", BLPAPI_INDEX, "blpapi"], check=False)
        if code != 0:
            # Not fatal: the app runs fully on the marks on file without the live feed, and
            # every `start` on a PC with a Terminal tries this install again (cmd_start).
            # Stopping here used to leave the database and `chelsea` uninstalled (2026-09-28).
            say("WARNING: blpapi did not install (this PC must reach blpapi.bloomberg.com). The app starts")
            say("         without the live feed; the next `chelsea` tries again, or run  py 2_launcher.py setup --bloomberg")
        else:
            say("  OK: blpapi installed")
    else:
        say("  skipped: no Bloomberg Terminal detected (use --bloomberg to force)")

    # 5. verify + database
    say("[5/7] Verify imports, create database and status file")
    check = (
        f"import {', '.join(IMPORT_CHECKS)};"
        "print('  OK: dash', dash.__version__, '| pandas', pandas.__version__);"
        "from ui.app import ensure_schema, get_db_path; ensure_schema(get_db_path());"
        "print('  OK: database', get_db_path())"
    )
    run([VENV_PY, "-c", check])
    if _trades_count() == 0:
        say(f"  {EMPTY_DB_LINE}")

    # 6. chelsea PowerShell function
    say("[6/7] 'chelsea' PowerShell command")
    try:
        written = install_pnl_function()
        for profile in written:
            say(f"  OK: {profile}")
    except OSError as exc:
        say(f"FAILED: could not write PowerShell profile: {exc}")
        return 1
    if written:
        # A profile that PowerShell refuses to load (execution policy Restricted, the
        # Windows default) leaves `chelsea` unknown in every new window: check and fix it.
        for note in ensure_profile_loads():
            say(f"  {note}")

    # 7. tests: informational. The install itself was verified in step 5 (every package
    # imports, the database is created); a test that needs data this PC does not have (the
    # research app's database, the market-history folder) fails here without the app being
    # broken, and a fresh PC must still reach "start". A failure is named, never hidden.
    say("[7/7] Tests")
    tests_failed = False
    if args.skip_tests:
        say("  skipped (--skip-tests)")
    else:
        tests_failed = run([VENV_PY, "-m", "pytest", "tests/", "-q"], check=False) != 0
        if tests_failed:
            say("  WARNING: some tests failed (see above). The app is installed and starts; report the")
            say("           failing tests. Run them again any time:  py 2_launcher.py doctor --tests")

    say()
    say("=" * 70)
    say(" SETUP COMPLETE.   Start the app:   chelsea   (or:  py 2_launcher.py start)")
    say("                   Anything wrong:  py 2_launcher.py doctor")
    if tests_failed:
        say("                   Some tests failed: see the WARNING above.")
    if want_blp:
        say("                   Log in to the Bloomberg Terminal before starting.")
    say("=" * 70)
    return 0


# ----------------------------------------------------------------------------- start

def _packages_fingerprint() -> str:
    """Hash of PACKAGES + DEV_PACKAGES + IMPORT_CHECKS + this interpreter's python version
    (major.minor): changes when a `pip install` would need to do something, and when what
    counts as "the packages import" changes -- so a PC whose stamp was written under a
    weaker check re-verifies once on its next `chelsea` instead of trusting that stamp forever."""
    import hashlib
    v = sys.version_info
    payload = ("\n".join(sorted(PACKAGES + DEV_PACKAGES)) + "|" + ",".join(IMPORT_CHECKS)
               + f"|py{v.major}.{v.minor}")
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _packages_stamp_matches() -> bool:
    try:
        return PACKAGES_STAMP.read_text(encoding="utf-8").strip() == _packages_fingerprint()
    except OSError:
        return False


def _write_packages_stamp() -> None:
    try:
        PACKAGES_STAMP.write_text(_packages_fingerprint(), encoding="utf-8")
    except OSError:
        pass  # best-effort cache; a missing/unwritable stamp just costs the slow path again


def venv_imports_ok() -> bool:
    """True when every module in IMPORT_CHECKS imports inside .venv.

    A matching packages.stamp (written after a successful install, below) short-circuits
    this without spawning a subprocess: the real check -- a whole extra interpreter
    importing dash/pandas/QuantLib/scipy, then `start` importing them AGAIN once it
    re-execs into .venv -- was measured at 1.5s on every `start` (2026-09-17), even when
    nothing had changed. Only a missing or stale stamp (PACKAGES/DEV_PACKAGES edited, or a
    different python) pays for the real work: a `pip install` of PACKAGES, which brings
    .venv to the list (a version bound raised in PACKAGES is applied here; an import check
    alone would pass on the old version and trust it for ever, 2026-09-28) and is a quick
    local no-op when every requirement is already met, then the import check. A success
    re-writes the stamp so the next call is fast again."""
    if _packages_stamp_matches():
        return True
    say("Bringing .venv up to PACKAGES (the list changed, or the first start on this Python): pip install")
    subprocess.call([str(VENV_PY), "-m", "pip", "install", *PACKAGES, *DEV_PACKAGES, "--quiet"], cwd=str(ROOT))
    code = subprocess.call([str(VENV_PY), "-c", "import " + ", ".join(IMPORT_CHECKS)], cwd=str(ROOT),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = code == 0
    if ok:
        _write_packages_stamp()
    return ok


def venv_blpapi_ok() -> bool:
    """True when `blpapi` imports inside .venv. Not part of IMPORT_CHECKS on purpose --
    blpapi only exists on a Bloomberg PC -- so `start` checks it separately whenever a
    Terminal is detected (see cmd_start)."""
    code = subprocess.call([str(VENV_PY), "-c", "import blpapi"], cwd=str(ROOT),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return code == 0


def cmd_start(args) -> int:
    if not in_venv():
        if not VENV_PY.exists():
            # A PC that was never set up (or a fresh clone): do the setup here rather than
            # stopping with "No .venv yet" -- one time, a few minutes.
            say("No .venv yet: running setup first (one time; creates .venv, installs packages, creates the database).")
            setup_args = build_parser().parse_args(["setup", "--skip-tests"])
            code = cmd_setup(setup_args)
            if code != 0:
                return code
        if not args.no_sync:
            changed, message = sync_with_github()
            say(f"GitHub: {message}")
            if changed:
                # The file now on disk may be newer than this running copy: hand over to
                # it rather than continue with old code. `--force-new`: a risk monitor left
                # running from before the update is stopped and replaced even when its
                # source fingerprint (ui/launch.py: .py files only) did not move -- an
                # update to config/, a stylesheet or a contract table must never be served
                # by the old process (2026-09-28).
                argv = [a for a in _argv() if a not in ("--no-sync", "--force-new")] + ["--no-sync", "--force-new"]
                return subprocess.call([sys.executable, str(ROOT / "2_launcher.py"), *argv], cwd=str(ROOT))
        # venv_imports_ok() decides whether .venv needs a `pip install` (packages.stamp: fast
        # when nothing changed; a stale stamp installs PACKAGES then checks the imports). An
        # unconditional install on every start after every update was the single biggest
        # cost on a PC with slow PyPI access (2026-09-17); --refresh-packages is accepted for
        # old `chelsea` blocks and ignored.
        if VENV_PY.exists() and not venv_imports_ok():
            say("FAILED: .venv cannot import every package the app needs, even after pip. Run:  py 2_launcher.py setup")
            return 1
        # blpapi is installed by `setup` only when a Terminal was detected AT SETUP TIME
        # (bloomberg_pc()), and venv_imports_ok() deliberately ignores it, so a PC whose
        # Terminal was installed or logged in after setup ran started the app without the
        # live feed on every launch, with nothing but the feed's own "blpapi is not
        # installed" status to say so (2026-09-18 audit). Re-check on every start.
        if VENV_PY.exists() and bloomberg_pc() and not venv_blpapi_ok():
            say("Bloomberg Terminal detected but blpapi is not installed in .venv: installing it now")
            run([VENV_PY, "-m", "pip", "install", "--index-url", BLPAPI_INDEX, "blpapi", "--quiet"], check=False)
            if not venv_blpapi_ok():
                say("WARNING: blpapi still does not import; the app starts without the live feed. "
                    "Fix: py 2_launcher.py setup --bloomberg")
        for line in port_preflight():
            say(line)
        return reexec_in_venv([a for a in _argv() if a not in ("--no-sync", "--refresh-packages")])
    if _trades_count() == 0:
        say(EMPTY_DB_LINE)
    from ui.launch import main
    # The sample book runs inside the app's own process (ui/sample_book.py), never as an
    # instance of its own, so a running risk monitor on the current code is safe to reuse.
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
          "install 64-bit Python 3.11+ and run  py 2_launcher.py setup")

    d.add("venv", in_venv(), str(VENV) if in_venv() else "not running inside .venv",
          "py 2_launcher.py setup")

    missing = []
    for mod in IMPORT_CHECKS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    d.add("packages", not missing, "all application packages import" if not missing else "missing: " + ", ".join(missing),
          "py 2_launcher.py setup")

    # database
    p = db_path()
    if not p.exists():
        d.add("database", False, f"{p} does not exist", "py 2_launcher.py setup   (creates it empty)")
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
                d.add("database", False, f"{p} lacks tables: {', '.join(lacking)}", "py 2_launcher.py setup   (applies the schema)")
            else:
                d.add("database", True, f"{p}: {counts.get('trades', 0)} trades, "
                                        f"{counts.get('trade_legs', 0)} legs, {counts.get('marks', 0)} marks")
                if counts.get("trades", 0) == 0:
                    d.add("data", None, EMPTY_DB_LINE)
        except sqlite3.Error as exc:
            d.add("database", False, f"{p}: {exc}", "the file is not a valid SQLite database; restore it from backup")

    status = p.with_name(p.name + ".bloomberg_status.json")
    d.add("status file", None if status.exists() else False,
          str(status) if status.exists() else "missing (created on start)", "py 2_launcher.py start")

    # bloomberg
    host, port = bloomberg_host()
    try:
        import blpapi  # noqa: F401
        have_blp = True
    except ImportError:
        have_blp = False
    terminal = tcp_open(host, port)
    if bloomberg:
        d.add("blpapi", have_blp, "installed" if have_blp else "not installed in .venv", "py 2_launcher.py setup --bloomberg")
        d.add("terminal", terminal, f"{host}:{port} {'answers' if terminal else 'no answer'}",
              "log in to the Bloomberg Terminal on this PC, then retry")
    else:
        # A Terminal on this PC (answering, or installed under C:lp) with no blpapi in
        # .venv is a failure even in plain doctor mode: the live feed can never start, and
        # before 2026-09-18 this row was informational only, so `doctor` said "Everything
        # checks out" on exactly the PC where the feed was silently missing.
        if (terminal or terminal_installed()) and not have_blp:
            d.add("blpapi", False, "a Bloomberg Terminal is on this PC but blpapi is not installed in .venv "
                                   "(the live feed cannot start)", "py 2_launcher.py setup --bloomberg")
        d.add("bloomberg", None, ("blpapi installed, " if have_blp else "blpapi not installed, ")
              + (f"Terminal answers on {host}:{port}" if terminal else "no Terminal on this PC")
              + "  (run  py 2_launcher.py doctor --bloomberg  on the Bloomberg PC)")
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
                if tcp_open("127.0.0.1", pt, timeout=0.2):
                    found.append(f"{pt}: {_listener(pt) or 'a process'} that does not answer as a risk monitor "
                                 f"(a hung copy of the app, or another program)")
                continue
            if seen == me:
                found.append(f"{pt}: current code")
            elif seen.startswith(IDENTITY_PREFIX):
                found.append(f"{pt}: STALE code {seen}")
            else:
                found.append(f"{pt}: another application")
        d.add("ports", None, "; ".join(found) if found else "nothing listening on 8050-8060 (start will use 8050)")
        if any("STALE" in f for f in found):
            d.add("stale app", False, "an old copy of the app is still running", "close its terminal window, then  py 2_launcher.py start")
    except ImportError as exc:
        d.add("ports", False, f"cannot import ui.launch: {exc}", "py 2_launcher.py setup")

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


TICKER_CHECK = "data.bloomberg.ticker_check"


def doctor_ticker_check(d: Doctor) -> int:
    """`doctor --bloomberg`'s run of the ticker check (every contract root, no book check:
    202 securities in 5 requests on today's universe). Exit 2, Bloomberg unreachable or
    silent, is a doctor failure; exit 1, roots needing attention, is information: the
    report and the fixes worksheet under reports/ say what to change. Returns the exit code."""
    code = run([sys.executable, "-m", TICKER_CHECK], check=False)
    if code == 0:
        d.add("bbg tickers", True, "every contract root's ticker agrees with config/contracts.csv")
    elif code == 2:
        d.add("bbg tickers", False, "Bloomberg could not be reached or did not answer the ticker check",
              "log in to the Bloomberg Terminal on this PC, then  py 2_launcher.py bbg-check")
    else:
        d.add("bbg tickers", None, "some contract roots need attention: read reports/bbg_check_*.txt, set 'apply' "
                                   "in reports/contract_fixes_*.csv, then  py 2_launcher.py contracts-apply <worksheet>")
    return code


def cmd_doctor(args) -> int:
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    say("=" * 70)
    say(f" risk-monitor doctor   ({ROOT})")
    say("=" * 70)
    d = Doctor()
    doctor_checks(d, bloomberg=args.bloomberg, git=not args.no_git)
    # `chelsea` the way a new window sees it: profile loaded, execution policy applied. A
    # profile PowerShell refuses to load (policy Restricted) is the one fresh-PC failure
    # setup could not see by writing the file (2026-09-28).
    for shell, ok, detail in chelsea_available():
        d.add("chelsea", ok, f"{shell}: {detail}",
              "py 2_launcher.py setup   (writes the profile and sets the execution policy for you)")
    code = 0
    if args.bloomberg:
        say()
        say("Bloomberg request-by-request check (read-only, reports under reports/):")
        code = run([sys.executable, str(ROOT / "tools" / "bloomberg_terminal_probe.py"), "--once"], check=False)
        d.add("bbg requests", code == 0, "every spot/forward request answered" if code == 0 else "see FAILED lines above",
              "read the Bloomberg error text above; reports/bloomberg_diagnostic_*.txt has the detail")
        say()
        say("Bloomberg ticker check of every contract root (read-only, reports under reports/):")
        doctor_ticker_check(d)
    # Code health, informational: the audit the infra agent works from (tools/health.py).
    # Never fails doctor; `py 2_launcher.py health` is the command that exits 1 on a breach.
    try:
        from tools import health as _health
        _cfg = _health.load_config()
        _hard = [b for b in _health.breaches(_health.collect(ROOT, _cfg)["summary"], _cfg) if b["level"] == "hard"]
        d.add("health", None, ("; ".join(f"{b['measure']} {b['value']} (limit {b['limit']})" for b in _hard)
                               + "  ->  py 2_launcher.py health") if _hard else "no hard breach (py 2_launcher.py health for the full report)")
    except (ImportError, OSError, ValueError, subprocess.SubprocessError) as exc:
        d.add("health", None, f"audit not run: {exc!r}")
    if args.tests:
        say()
        tcode = run([sys.executable, "-m", "pytest", "tests/", "-q"], check=False)
        d.add("tests", tcode == 0, "all passed" if tcode == 0 else "failures above", "the code is broken: git pull, or report the failing test")
    say()
    say("=" * 70)
    if d.failures:
        say(f" {len(d.failures)} problem(s). Fix the first one, then run  py 2_launcher.py doctor  again:")
        for name, _, detail, fix in d.failures:
            say(f"   - {name}: {detail}")
            if fix:
                say(f"       fix -> {fix}")
        say("=" * 70)
        return 1
    say(" Everything checks out.   Start the app:  py 2_launcher.py start")
    say("=" * 70)
    return 0


# ----------------------------------------------------------------------------- marks snapshot

SNAPSHOT_REL = "data/bbg_snapshot"


def cmd_marks_export(args) -> int:
    """Bloomberg PC, by hand: what every "Pull Bloomberg now" already does at its end
    (data/bloomberg/snapshot.py::save_after_pull): write the marks on file to
    data/bbg_snapshot/, commit that folder alone and, with --push, push it."""
    from data.bloomberg import snapshot
    try:
        m = snapshot.export_snapshot(db_path())
    except snapshot.SnapshotError as exc:
        say(f"  marks-export: {exc}")
        return 1
    say(f"  marks-export: {m['rows'].get('marks', 0)} marks, {m['marks_from']} to {m['marks_through']}, "
        f"written to {SNAPSHOT_REL}/")
    if args.no_commit:
        return 0
    result = snapshot.commit_snapshot(ROOT, m, push=args.push)
    say(f"  {result['message']}")
    if not result["pushed"]:
        say("  next:  git push      then on the other PC:  git pull  and  2_launcher.py marks-import")
    else:
        say("  On the other PC:  git pull  and  2_launcher.py marks-import")
    return 0 if (result["committed"] or "identical" in result["message"]) and (result["pushed"] or not args.push) else 1


def cmd_marks_import(args) -> int:
    """PC with no Terminal: load data/bbg_snapshot/ into the database (runs inside .venv: the
    ledger's freeze of settled trades needs the app's packages)."""
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    from data.bloomberg import snapshot
    if bloomberg_pc() and not args.force:
        say("  marks-import: this PC has Bloomberg. An import replaces the marks on file with the snapshot's,")
        say("  so anything pulled here since that export would be lost. Add --force if that is what you want.")
        return 1
    try:
        r = snapshot.import_snapshot(db_path())
    except snapshot.SnapshotError as exc:
        say(f"  marks-import: {exc}")
        return 1
    m = r["manifest"] or {}
    say(f"  marks-import: snapshot of {m.get('exported_at', 'unknown time')}, "
        f"marks {m.get('marks_from', '?')} to {m.get('marks_through', '?')}")
    pull = m.get("last_pull") or {}
    if pull:
        say(f"  last pull on the Bloomberg PC: {pull.get('time', '?')}, {pull.get('written', '?')} written, "
            f"{pull.get('failed', '?')} failed ({SNAPSHOT_REL}/pull_status.json has the detail)")
    say("  loaded: " + ", ".join(f"{n} {t}" for t, n in r["rows"].items())
        + f"; {r['instruments_added']} instrument(s) added")
    if r["skipped_marks"]:
        say(f"  {r['skipped_marks']} mark(s) skipped: their instrument is not in the snapshot")
    led = r["ledger"]
    if led is not None:
        say(f"  settled trades frozen: {led['realised']}; not freezable: {len(led['unrealisable'])}")
    if _trades_count() == 0:
        say("  No trades on file yet: upload the blotter in the app, then run marks-import again")
        say("  (an upload clears the frozen settled trades, and only this command freezes them here).")
    return 0


# ----------------------------------------------------------------------------- reprice

def cmd_reprice(args) -> int:
    """Re-price the FX options from the marks on file, day by day, asking Bloomberg nothing
    (engine/options/store.py::recalc_on_file): how the past days are re-run after a pricer
    change. Runs inside .venv (QuantLib). Exit 1 when the recalc reports an error."""
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    if not db_path().exists():
        say(f"  reprice: no database at {db_path()} (py 2_launcher.py setup creates it)")
        return 1
    from data.bloomberg.live import book_today
    from data.ingest.schema import connect
    from engine.options import store as options_store
    as_of = args.as_of or book_today().isoformat()
    say(f"  reprice: as of {as_of}, since {args.since or 'the earliest data on file'}, from the marks on file")
    conn = connect(db_path())
    try:
        options = options_store.recalc_on_file(conn, as_of, since=args.since)
    finally:
        conn.close()
    code = 0
    for day in options.get("days", []):
        say(f"  options  {day['day']}  priced {day['priced']}  skipped {len(day.get('skipped', []))}")
    say(f"  options: {options.get('priced', 0)} priced, {options.get('skipped', 0)} skipped over {len(options.get('days', []))} day(s)")
    if options.get("error"):
        say(f"  options: error {options['error']}")
        code = 1
    return code


# ----------------------------------------------------------------------------- bbg-check

# The ticker check's own flags, passed through as given (data/bloomberg/ticker_check.py::_parser).
BBG_CHECK_FLAGS = (("dry_run", "--dry-run"), ("book", "--book"), ("search", "--search"),
                   ("lme", "--lme"), ("options", "--options"), ("desk", "--desk"))
BBG_CHECK_OPTIONS = (("sector", "--sector"), ("db", "--db"), ("host", "--host"), ("port", "--port"),
                     ("out", "--out"), ("limit", "--limit"))


def bbg_check_argv(args) -> list:
    """The ticker check's command line rebuilt from this parser's namespace, flag for flag."""
    argv = [flag for dest, flag in BBG_CHECK_FLAGS if getattr(args, dest)]
    for root in args.root or []:
        argv += ["--root", root]
    for dest, flag in BBG_CHECK_OPTIONS:
        value = getattr(args, dest)
        if value is not None:
            argv += [flag, str(value)]
    return argv


def cmd_bbg_check(args) -> int:
    """Bloomberg PC, on request (hard rule 8): ask Bloomberg what each contract root's ticker
    really is and compare it with config/contracts.csv (data/bloomberg/ticker_check.py). Runs
    `python -m data.bloomberg.ticker_check` inside .venv (blpapi) and returns its exit code:
    0 all OK, 1 something needs attention, 2 Bloomberg unreachable."""
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    return run([sys.executable, "-m", TICKER_CHECK, *bbg_check_argv(args)], check=False)


# ----------------------------------------------------------------------------- contracts-apply

def cmd_contracts_apply(args) -> int:
    """Apply the ticker check's fixes worksheet (reports/contract_fixes_*.csv, the rows whose
    `apply` is yes) to config/contracts.csv through data.contracts.apply_fixes, which
    validates the whole file before writing it. Exit 0 when no row was refused, else 1."""
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    from data.contracts import apply_fixes
    path = Path(args.worksheet)
    if not path.exists():
        say(f"  contracts-apply: no worksheet at {path}")
        return 1
    try:
        r = apply_fixes(path, dry_run=args.dry_run)
    except (ValueError, OSError) as exc:
        say(f"  contracts-apply: the worksheet could not be read: {exc}")
        return 1
    for e in r["applied"]:
        line = f"  applied  row {e['row']}: {e['root_id']} {e['field']} {e['before']!r} -> {e['after']!r}"
        if e.get("quote_unit_before") is not None:
            line += f", quote unit {e['quote_unit_before']!r} -> {e['quote_unit_after']!r}"
        if e.get("multiplier_before") != e.get("multiplier_after"):
            line += f", multiplier {e.get('multiplier_before')} -> {e.get('multiplier_after')}"
        say(line)
    for e in r["skipped"]:
        say(f"  skipped  row {e['row']}: {e['root_id']} {e['field']} (apply is {e['apply']!r}, not yes)")
    for e in r["refused"]:
        say(f"  refused  row {e['row']}: {e['root_id']} {e['field']}: {e['why']}")
    n_applied, n_skipped, n_refused = len(r["applied"]), len(r["skipped"]), len(r["refused"])
    say(f"  contracts-apply: {n_applied} applied, {n_skipped} skipped, {n_refused} refused")
    if r["written"]:
        say("  config/contracts.csv rewritten. Run the check again to confirm:  py 2_launcher.py bbg-check")
    elif args.dry_run and n_applied:
        say("  dry run: config/contracts.csv not written")
    else:
        say("  config/contracts.csv not written")
    return 0 if not n_refused else 1


# ----------------------------------------------------------------------------- health

def cmd_health(args) -> int:
    """Code-health audit (tools/health.py): the measures the infra agent works from, with
    the thresholds in config/health.yaml. Exit 1 on a hard breach, or with --baseline when
    a ratchet measure is worse than config/health_baseline.json. Runs inside .venv so the
    same ruff the hook uses is the one measured."""
    if not in_venv() and VENV_PY.exists():
        return reexec_in_venv(sys.argv[1:])
    from tools import health
    argv = ["--json"] if args.json else []
    if args.out:
        argv += ["--out", args.out]
    if args.baseline is not None:
        argv += ["--baseline"] + ([args.baseline] if args.baseline else [])
    if args.update_baseline is not None:
        argv += ["--update-baseline"] + ([args.update_baseline] if args.update_baseline else [])
    if args.compare:
        argv += ["--compare", *args.compare]
    return health.main(argv)


# ----------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="py 2_launcher.py", description="risk-monitor: setup, start, doctor.")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="install everything into .venv, create the database, run the tests")
    s.add_argument("--bloomberg", action="store_true", help="install blpapi even if no Terminal is detected")
    s.add_argument("--no-bloomberg", action="store_true", help="never install blpapi")
    s.add_argument("--recreate", action="store_true", help="delete and rebuild .venv")
    s.add_argument("--skip-tests", action="store_true")
    s.set_defaults(func=cmd_setup)

    t = sub.add_parser("start", help="start the app and open the browser")
    t.add_argument("--force-new", action="store_true", help="never reuse a running instance")
    t.add_argument("--no-sync", action="store_true", help="do not update from GitHub first")
    t.add_argument("--refresh-packages", action="store_true", help=argparse.SUPPRESS)
    t.set_defaults(func=cmd_start)

    dcm = sub.add_parser("doctor", help="check every prerequisite and say what to fix")
    dcm.add_argument("--bloomberg", action="store_true", help="require Bloomberg and test every request")
    dcm.add_argument("--tests", action="store_true", help="also run the test suite")
    dcm.add_argument("--no-git", action="store_true", help="skip the GitHub sync check")
    dcm.set_defaults(func=cmd_doctor)

    me = sub.add_parser("marks-export", help="Bloomberg PC: write the marks on file to data/bbg_snapshot/ and commit it "
                                             "(every Pull Bloomberg now already does this)")
    me.add_argument("--push", action="store_true", help="also git push")
    me.add_argument("--no-commit", action="store_true", help="write the files only")
    me.set_defaults(func=cmd_marks_export)

    mi = sub.add_parser("marks-import", help="PC with no Terminal: load data/bbg_snapshot/ into the database")
    mi.add_argument("--force", action="store_true", help="import even on a PC that has Bloomberg")
    mi.set_defaults(func=cmd_marks_import)

    rp = sub.add_parser("reprice", help="re-price the FX options from the marks on file, day by day "
                                        "(asks Bloomberg nothing)")
    rp.add_argument("--as-of", dest="as_of", metavar="YYYY-MM-DD", help="the last day to price (default: the book date)")
    rp.add_argument("--since", metavar="YYYY-MM-DD", help="the first day to price (default: the earliest data on file)")
    rp.set_defaults(func=cmd_reprice)

    bc = sub.add_parser("bbg-check", help="Bloomberg PC: check every contract root's ticker, currency and scale "
                                          "against config/contracts.csv and write a fixes worksheet under reports/ "
                                          "(exit 0 all OK, 1 attention, 2 Bloomberg unreachable)")
    bc.add_argument("--dry-run", action="store_true", help="print what would be asked; ask nothing, write nothing")
    bc.add_argument("--root", action="append", default=[], metavar="ROOT_ID",
                    help="one contract root, e.g. NYMEX:CL (repeatable)")
    bc.add_argument("--sector", help="only the roots of this sector (energy, metals, ...)")
    bc.add_argument("--book", action="store_true",
                    help="only the roots the book holds; reads --db, else the app's database")
    bc.add_argument("--db", help="the app's database, for the book check (opened read-only)")
    bc.add_argument("--search", action="store_true",
                    help="search Bloomberg for candidates for every root it does not know")
    bc.add_argument("--lme", action="store_true",
                    help="only the LME curve tickers (cash, 3M, first monthly); a plain run includes them")
    bc.add_argument("--options", action="store_true",
                    help="only the options on futures (each root's option chain); a plain run includes them")
    bc.add_argument("--desk", action="store_true",
                    help="only the desk checks (settlement, open interest, LME liquidity, SGX history, delivery, "
                         "holidays, history depth, contract dates); a plain run includes them")
    bc.add_argument("--host", help="Bloomberg API host (default localhost)")
    bc.add_argument("--port", type=int, help="Bloomberg API port (default 8194)")
    bc.add_argument("--out", help="folder for the reports (default reports/)")
    bc.add_argument("--limit", type=int, help="only the first N roots")
    bc.set_defaults(func=cmd_bbg_check)

    ca = sub.add_parser("contracts-apply", help="apply the rows marked apply=yes of the bbg-check fixes worksheet "
                                                "to config/contracts.csv (exit 1 when a row was refused)")
    ca.add_argument("worksheet", help="the worksheet, reports/contract_fixes_<stamp>.csv")
    ca.add_argument("--dry-run", action="store_true", help="say what would change; write nothing")
    ca.set_defaults(func=cmd_contracts_apply)

    f = sub.add_parser("freeze", help="(re)write requirements.txt from PACKAGES, for pip/CI use outside 2_launcher.py")
    f.set_defaults(func=cmd_freeze)

    h = sub.add_parser("health", help="code-health audit: ruff, dead code, duplicate helpers, dated comments, layering, line endings (tools/health.py)")
    h.add_argument("--json", action="store_true", help="print the report as JSON")
    h.add_argument("--out", metavar="PATH", help="also write the JSON report to PATH")
    h.add_argument("--baseline", nargs="?", const="", metavar="PATH",
                   help="fail when a ratchet measure is worse than the baseline (default config/health_baseline.json)")
    h.add_argument("--update-baseline", nargs="?", const="", metavar="PATH", help="record the current measures as the baseline")
    h.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"), help="print what changed from report A to report B")
    h.set_defaults(func=cmd_health)
    return parser


def _default_argv(argv: list) -> list:
    """The command line with the one default applied: nothing at all means `start`. Windows
    opens a double-clicked .py file as `py.exe "2_launcher.py"` with no arguments, and the
    parser's required subcommand used to answer that with a usage error in a window that
    closed at once (2026-09-28). A typed command is returned exactly as given, so `-h`,
    `setup`, `doctor` and the rest behave as before."""
    return list(argv) if argv else ["start"]


def _argv() -> list:
    """This process's own command line after the default: what `start` hands to the copy of
    itself it runs (the re-exec into .venv, the handover after a GitHub update), so a child
    always carries an explicit `start` and never pauses as a double-click would."""
    return _default_argv(sys.argv[1:])


def main(argv=None) -> int:
    args = build_parser().parse_args(_default_argv(sys.argv[1:] if argv is None else argv))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return args.func(args)


def double_click_mode() -> bool:
    """True only in the process a double-click (or a bare `py 2_launcher.py`) started: no
    subcommand on its command line, and not a copy `start` runs inside .venv
    (RISK_MONITOR_VENV=1, set by reexec_in_venv; that child gets an explicit `start` too).
    A typed command, and the `chelsea` function, always carry a subcommand, so this is
    never true for them."""
    return len(sys.argv) == 1 and "RISK_MONITOR_VENV" not in os.environ


def run_from_double_click(run_main=main, ask=input) -> int:
    """`main()` for a double-click, where the console window closes with the process: on a
    failure (a non-zero exit code, or a SystemExit carrying a message) the reason is printed
    and the window waits for Enter before closing with that code. A success returns at once.
    EOFError from `ask` (no console attached) is ignored; a KeyboardInterrupt is not caught
    here, so it still exits 130 without a prompt. `run_main` and `ask` are parameters so the
    pause path can be exercised without starting anything."""
    try:
        code = run_main()
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, str):
            say(code)
            code = 1
        elif code is None:
            code = 0
    if code:
        say()
        say(f"The launcher stopped (exit code {code}). The reason is in the lines above.")
        say("Anything wrong:  py 2_launcher.py doctor")
        try:
            ask("Press Enter to close")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    try:
        sys.exit(run_from_double_click() if double_click_mode() else main())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            say(exc.code)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        sys.exit(130)
