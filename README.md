# risk-monitor

FX risk monitor for the NMMF book at BNP: BNP report import, settlement-date cash ladder, live Bloomberg rates, P&L.

**What it does and what the numbers mean: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).**

There is one script, `risk.py`, and three commands. Nothing else is needed.

| I want to | Run |
|---|---|
| Set up a computer (first time, or after `git pull`) | `py risk.py setup` |
| Start the app | `py risk.py start` |
| Find out why something is not working | `py risk.py doctor` |

Open a terminal in the project folder first: in Explorer, right-click the folder and choose
**Open in Terminal**, or in PyCharm / VS Code use the built-in terminal.

---

## 1. Set up

Requirements: Windows, 64-bit Python 3.11 or newer from python.org (tick **py launcher** during install), and this folder from GitHub.

```
py risk.py setup
```

This does everything, in order, and stops with a message at the first thing that fails:

1. Checks the Python version and that it is 64-bit.
2. Creates a private Python environment in `.venv` inside the project folder. Nothing is installed globally.
3. Installs every package in `requirements.txt` into it.
4. On a Bloomberg PC (a Terminal is installed or answering on port 8194) also installs `blpapi` from Bloomberg's official package index. Elsewhere this step is skipped.
5. Verifies the packages import, creates `data\raw\risk.db` with the schema if it is missing, and creates the Bloomberg status file.
6. Runs the whole test suite. Setup is complete only when every test passes.

Options:

| Option | Use when |
|---|---|
| `--sample` | You want data on screen before the first real BNP upload. Imports `data\sample\HA_PNL_SAMPLE_20260818.csv` (real layout and rates, scaled amounts, fake ids). Skipped with a note if the database already holds a different report for that date. |
| `--bloomberg` | Force the `blpapi` install even though no Terminal was detected. |
| `--no-bloomberg` | Never install `blpapi`. |
| `--recreate` | Delete and rebuild `.venv` (after a Python upgrade, or if packages are corrupted). |
| `--skip-tests` | Skip step 6. |

Setup is safe to repeat. It never changes an existing database.

**Moving history between computers**: `data\raw\risk.db` holds trades, marks and the P&L snapshot history. It is not in git. Copy it into `data\raw\` on the other PC if you want that history there; otherwise upload a BNP report in the app.

---

## 2. Start

```
py risk.py start
```

The browser opens at `http://127.0.0.1:8050`. Keep the terminal window open; **Ctrl+C** stops the app.
The console prints the interpreter, the database path, an **App version** fingerprint of the source code, and whether the Bloomberg feed started.

- Running `start` again while the app is running just reopens the browser tab.
- If a copy with **older code** is still running, it is left alone and the new one starts on the next port (8051 and so on). Close the old terminal window to get back to 8050.
- On the Bloomberg PC, log in to the Terminal first, then `start`. The console says `Bloomberg feed: started (every 2 min)` or the reason it did not start.
- `py risk.py start --force-new` always starts a fresh instance.

**Daily routine**: upload the morning BNP file with the **Upload BNP report** button, set the as-of date to today, read the ladder. Details in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) section 8.

**Once, after the first live Bloomberg run**: `py risk.py backfill` builds the Daily / 5d / MTD / YTD history from daily closes (optional `START END` dates, `--overwrite` to redo complete days).

---

## 3. Troubleshoot

```
py risk.py doctor
```

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
| `'py' is not recognized` | Python is not installed, or was installed without the py launcher. Reinstall from python.org, tick **py launcher** and **Add to PATH**. |
| `No .venv yet` | Run `py risk.py setup`. |
| `Missing dependency` on start | Run `py risk.py setup`. |
| App opens on 8051, 8052... | An older copy is still running. `doctor` names the port. Close that terminal window. |
| Every USD figure is blank / "Unavailable" | No official Bloomberg marks. This is by design on a PC without a Terminal. On the Bloomberg PC run `py risk.py doctor --bloomberg`. |
| Ladder is empty on today's date | The as-of date picker is on an old date, or no BNP file covers today. Upload the latest file and set the date. |
| Upload refused with "differ from DB" | The file amends rows already stored. The app never overwrites; see HOW_IT_WORKS section 2. |
| `git fetch` fails with `bad object refs/...` | A stale ref left by another tool. Run `git update-ref -d <that ref>` and re-run `doctor`. |
| Tests fail after `git pull` | Run `py risk.py setup` (new packages may be needed). If they still fail, the commit is broken: report the failing test. |

---

## For developers

Everything else lives under `risk.py` too, but the raw commands are:

```
.venv\Scripts\python -m pytest tests/ -q          tests
.venv\Scripts\python -m ui.launch                 the launcher risk.py start calls
.venv\Scripts\python -m data.bloomberg.live --once   one Bloomberg pull now
.venv\Scripts\python -m data.bloomberg.live --status the last pull, itemised
.venv\Scripts\python -m data.load <BNP csv>       command-line import
.venv\Scripts\python tools\make_sample_data.py    rebuild the sample from the real file
```

Environment variables: `RISK_DB` (database path), `RISK_LIVE=0` (disable the feed), `BLP_HOST`, `BLP_PORT`.
Calculation contract: `CLAUDE.md`. Open items: `docs/open-questions.md`.
