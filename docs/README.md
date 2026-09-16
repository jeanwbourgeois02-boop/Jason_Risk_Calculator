# risk-monitor

FX risk monitor for the NMMF book at BNP: BNP report import, settlement-date cash ladder, live Bloomberg rates, P&L.

**What it does and what the numbers mean: [HOW_IT_WORKS.md](HOW_IT_WORKS.md).**

There are exactly two entry points: double-click `1_setup.cmd` (repo root) once; then type `pnl`.
The repo root itself holds only three files: `1_setup.cmd` (this section), `2_launcher.py` (setup,
launch and doctor logic — see section 3 and "For developers" below), and
`3_diagnostic.py` (runs the Bloomberg diagnostics checks — `py 3_diagnostic.py`).
Everything else, including this file, lives in `docs/`.

---

## 1. Set up

Double-click `1_setup.cmd` in the repository folder (or wherever you saved it, even before cloning). It takes a
Windows PC from "nothing installed" to "ready to run": installs Python and Git if missing, clones or updates
this repository into `C:\Users\<you>\risk-monitor`, creates `.venv`, installs every package, creates the
database, runs the test suite, installs the `pnl` command into your PowerShell profile, and runs `doctor`.
Every step prints `OK` or `FAILED` with the reason. It is safe to run again any time (after a `git pull`, on a
new PC, if something looks broken) — every step is idempotent and never changes an existing database.

It ends with: `Ready. Open a PowerShell terminal and type: pnl`.

**Moving history between computers**: `data\raw\risk.db` holds trades, marks and the P&L snapshot history. It
is not in git. Copy it into `data\raw\` on the other PC if you want that history there; otherwise upload a BNP
report in the app.

---

## 2. Run

Open a PowerShell terminal and type:

```
pnl
```

This pulls the latest code from GitHub (unless you have uncommitted local edits, in which case it says so and
skips the pull) and starts the app. The browser opens at `http://127.0.0.1:8050`. Keep the terminal window
open; **Ctrl+C** stops the app.

- Running `pnl` again while the app is running just reopens the browser tab.
- If a copy with **older code** is still running, it is left alone and the new one starts on the next port
  (8051 and so on). Close the old terminal window to get back to 8050.
- On the Bloomberg PC, log in to the Terminal first, then run `pnl`. The console says
  `Bloomberg feed: started (every 2 min)` or the reason it did not start, and Bloomberg history is backfilled
  automatically in the background (see below) — there is nothing to run for that.
- `py -3 2_launcher.py start --force-new` (inside the project folder) always starts a fresh instance.

**Working copy**: the repository lives in `C:\Users\<you>\risk-monitor`, a plain local folder. GitHub is the
backup: commit and `git push` at the end of every session. Do not keep a clone inside OneDrive: OneDrive syncs
git's internal files while git writes them, which can corrupt the repository, and a second clone means two
versions of the app.

**Daily routine**: upload the morning BNP file with the **Upload BNP report** button, set the as-of date to
today, read the ladder. Details in [HOW_IT_WORKS.md](HOW_IT_WORKS.md) section 8.

**Bloomberg history backfill**: automatic. Whenever a Terminal is available — on `pnl` / `start`, and again at
the end of every live feed cycle — the app fills any business day between the earliest trade in the database
and yesterday that lacks a complete official close, in the background, without blocking the UI. Progress
("Backfill: n days remaining") is written to the same status file the live feed uses, and shown on the Market
data tab. Without a Terminal it does nothing and says so in the status.

---

## 3. Troubleshoot

```
py -3 2_launcher.py doctor
```

(from the project folder, or `pnl` first and then open a new terminal there.)

Checks every prerequisite, prints `OK`, `INFO` or `FAILED` per line, and for every failure the exact command that fixes it. Fix the first failure, run `doctor` again, repeat until it says "Everything checks out".

What it checks: Python version and bitness, `.venv` present, packages import, the database exists with every table and how many trades / marks / snapshots it holds, the Bloomberg status file and the last pull result, whether `blpapi` and a Terminal are available, which ports 8050-8060 are held by the current app, a stale app or something else, and whether the folder is in sync with GitHub.

| Option | Use when |
|---|---|
| `--bloomberg` | On the Bloomberg PC. `blpapi` and a Terminal become required, and every spot and forward request the app makes is tested one by one with Bloomberg's own error text. Reports are saved under `reports\`. |
| `--tests` | Also run the test suite. |
| `--no-git` | Skip the GitHub check (offline). |

Quick answers:

| Symptom | Cause and fix |
|---|---|
| `1_setup.cmd` says winget is unavailable | Update Windows, or install Python 3.12+ / Git manually from python.org and git-scm.com, then re-run `1_setup.cmd`. |
| `'pnl' is not recognized` | Open a **new** PowerShell window (profiles load at startup), or re-run `1_setup.cmd`. |
| `No .venv yet` | Double-click `1_setup.cmd`. |
| `Missing dependency` on start | Double-click `1_setup.cmd`. |
| App opens on 8051, 8052... | An older copy is still running. `doctor` names the port. Close that terminal window. |
| Every USD figure is blank / "Unavailable" | No official Bloomberg marks. This is by design on a PC without a Terminal. On the Bloomberg PC run `py -3 2_launcher.py doctor --bloomberg`. |
| Ladder is empty on today's date | The as-of date picker is on an old date, or no BNP file covers today. Upload the latest file and set the date. |
| Upload refused with "differ from DB" | The file amends rows already stored. The app never overwrites; see HOW_IT_WORKS section 2. |
| `git fetch` fails with `bad object refs/...` | A stale ref left by another tool. Run `git update-ref -d <that ref>` and re-run `doctor`. |
| Tests fail after `git pull` | Double-click `1_setup.cmd` (new packages may be needed). If they still fail, the commit is broken: report the failing test. |

---

## For developers

Everything else lives under `2_launcher.py` too, but the raw commands are:

```
.venv\Scripts\python -m pytest tests/ -q          tests
.venv\Scripts\python -m ui.launch                 the launcher 2_launcher.py start calls
.venv\Scripts\python -m data.bloomberg.live --once   one Bloomberg pull now
.venv\Scripts\python -m data.bloomberg.live --status the last pull, itemised
.venv\Scripts\python -m data.bloomberg.backfill --start ... --end ...   run the backfill manually (normally automatic)
.venv\Scripts\python -m data.load <BNP csv>       command-line import
.venv\Scripts\python tools\make_sample_data.py    rebuild the sample from the real file
```

Environment variables: `RISK_DB` (database path), `RISK_LIVE=0` (disable the feed), `BLP_HOST`, `BLP_PORT`.

Dependencies are a single list, `PACKAGES`, in `2_launcher.py` (`py 2_launcher.py setup` installs it directly;
there is no `requirements.txt` checked into the repo). Need one on disk for `pip install -r` or a
CI step that doesn't go through `2_launcher.py`? Run `py 2_launcher.py freeze` — it (re)writes `requirements.txt`
from `PACKAGES` and is git-ignored, never hand-edited.

Calculation contract: `../CLAUDE.md`. Open items: `open-questions.md`.
