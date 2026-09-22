# risk-monitor

FX, futures, rates and options risk monitor for the NMMF book: trade-blotter import, delta ladder by value date, live Bloomberg marks, one P&L. Choices, coverage and gaps for the PM: [PM_BRIEF.md](PM_BRIEF.md).

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
is not in git. Copy it into `data\raw\` on the other PC if you want that history there; otherwise upload the
trade blotter in the app (**Upload trade file**).

---

## 2. Run

Open a PowerShell terminal and type:

```
pnl
```

This always brings the folder to the latest code on GitHub first, then starts the app. The console prints
one `GitHub:` line: `code d27cdaa is current`, `UPDATED a1b2c3d -> d27cdaa`, or why it could not update
(offline, or unpushed local commits on a developer PC) and that it is running the code on disk. Local edits
or stray files on a PC are set aside with `git stash` rather than blocking the update (`git stash pop`
restores them). If the code changed, packages are refreshed in `.venv` before the app starts. The browser
opens at `http://127.0.0.1:8050`; the page shows `build <commit>` at the top right and the console prints the
same. Keep the terminal window open; **Ctrl+C** stops the app.

- Running `pnl` again while the app is running just reopens the browser tab.
- If a copy with **older code** is still running on 8050, it is stopped (force-stopped if it is too old to
  answer the stop request) and the new one takes 8050, so an old bookmark never shows old code.
- `py -3 2_launcher.py start --no-sync` starts without touching GitHub.
- On the Bloomberg PC, run `pnl` and log in to the Terminal (in either order). The console says
  `Bloomberg feed: on request only (Pull Bloomberg now)`: nothing is pulled from Bloomberg until you press
  **Pull Bloomberg now** in the top bar. One press pulls today's marks and then fills the missing past closes
  (see below), and only for what the trades on file need (the "Bloomberg library" on the Market data tab).
- `py -3 2_launcher.py start --force-new` (inside the project folder) always starts a fresh instance.
- **Marks on a PC without Bloomberg.** The database is not in git, so such a PC has no marks of its own.
  On the Bloomberg PC, after a pull: `py -3 2_launcher.py marks-export` writes the marks on file to
  `data/bbg_snapshot/` and commits that folder (`--push` also pushes; otherwise `git push`). On the other
  PC: `git pull`, upload the same blotter in the app, then `2_launcher.py marks-import`. The import makes
  the marks there what the Bloomberg PC had at the export (MANUAL marks typed there are kept) and freezes
  the settled trades; run it again after every blotter upload on that PC. It refuses on a PC that has
  Bloomberg unless `--force` is given. No trade travels this way. The snapshot carries every table a pull
  writes (marks, OIS curves and quotes, fixings, FX and rates vol quotes, dividend yields) and the last
  pull's log (`pull_status.json`), which the import prints one line of.

**Working copy**: the repository lives in `C:\Users\<you>\risk-monitor`, a plain local folder. GitHub is the
backup: commit and `git push` at the end of every session. Do not keep a clone inside OneDrive: OneDrive syncs
git's internal files while git writes them, which can corrupt the repository, and a second clone means two
versions of the app.

**Daily routine**: upload the latest blotter export with the **Upload trade file** button, leave the Ladder on
today, read the header and the ladder. Details in [HOW_IT_WORKS.md](HOW_IT_WORKS.md) section 8.

**Bloomberg history backfill**: part of every **Pull Bloomberg now**. Straight after today's marks, the app
fills any business day between the earliest trade in the database and yesterday that lacks a complete official
close, in the background, without blocking the UI. Nothing runs at `pnl` / `start` or on a timer. Progress
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
| `--bloomberg` | On the Bloomberg PC. `blpapi` and a Terminal become required, and every spot and forward request the app makes is tested one by one with Bloomberg's own error text. Reports are saved under `reports\`. Then run `py 3_diagnostic.py` for the coverage checks (marks, curves, fixings, option terms, clock, unverified vol tickers). |
| `--tests` | Also run the test suite. |
| `--no-git` | Skip the GitHub check (offline). |

Quick answers:

| Symptom | Cause and fix |
|---|---|
| `1_setup.cmd` says winget is unavailable | Update Windows, or install Python 3.12+ / Git manually from python.org and git-scm.com, then re-run `1_setup.cmd`. |
| `'pnl' is not recognized` | Open a **new** PowerShell window (profiles load at startup), or re-run `1_setup.cmd`. |
| `No .venv yet` | Double-click `1_setup.cmd`. |
| `Missing dependency` on start | Double-click `1_setup.cmd`. |
| App opens on 8051, 8052... | Port 8050 is held by something that is not this app. `doctor` names it. |
| Page shows an old `build` tag | The `GitHub:` console line says why it did not update (offline, unpushed commits). Fix that and run `pnl` again. |
| Every USD figure is blank / "Unavailable" | No official Bloomberg marks. This is by design on a PC without a Terminal. On the Bloomberg PC run `py -3 2_launcher.py doctor --bloomberg`. |
| Ladder is empty on today's date | The as-of date picker is on an old date, or no trade on file is open today. Upload the latest blotter and set the date. |
| Upload says rows were skipped | The result line names each row and the reason (cancelled status, other fund, unreadable field). Everything else loaded. |
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
.venv\Scripts\python -m data.bloomberg.backfill --start ... --end ...   run the backfill manually (normally part of Pull Bloomberg now)
.venv\Scripts\python 3_diagnostic.py              Bloomberg diagnostics (same checks as the Market data button)
```

Environment variables: `RISK_DB` (database path), `RISK_LIVE=0` (disable the feed), `BLP_HOST`, `BLP_PORT`.

Dependencies are a single list, `PACKAGES`, in `2_launcher.py` (`py 2_launcher.py setup` installs it directly;
there is no `requirements.txt` checked into the repo). Need one on disk for `pip install -r` or a
CI step that doesn't go through `2_launcher.py`? Run `py 2_launcher.py freeze` — it (re)writes `requirements.txt`
from `PACKAGES` and is git-ignored, never hand-edited.

Calculation contract: `../CLAUDE.md`. Open items: `open-questions.md`.
