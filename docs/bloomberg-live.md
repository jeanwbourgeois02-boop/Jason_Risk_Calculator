# Bloomberg live feed

The cash ladder prices FX spot, forwards and swap legs from Bloomberg only. No sample or
mock data is used by the application. When Bloomberg is unavailable, rate cells are blank
and the page says so.

## What runs

`data/bloomberg/live.py` starts with the app (`py -3 -m ui.launch`) when both hold:

1. the `blpapi` Python package imports;
2. a Bloomberg API service accepts a TCP connection (default `localhost:8194`, i.e. a
   logged-in Terminal or B-PIPE on this computer).

Every 2 minutes it pulls, for every FX pair with an open leg:

| Mark | Request | Stored as |
|---|---|---|
| SPOT | `PX_LAST` live ReferenceDataRequest on `<PAIR> Curncy` | `marks`, source `BBG_BFXFORWARD` |
| FWD_OUTRIGHT per open settle date and at `WORKDAY(as_of,5)` | direct broken-date request, else standard-tenor interpolation | `BBG_BFXFORWARD`, fallback `BBG_INTERP` (never official) |

Rows are upserted on the same primary key with a fresh `snapped_at`, so the table always
holds the latest value. The ladder re-renders every 2 minutes (`dcc.Interval`) and reads
the latest official SPOT per currency (`rates_from_marks`); a mark older than 10 minutes
is flagged stale.

## Setting up the Bloomberg computer (no command line needed)

1. Clone or open the project (e.g. in PyCharm).
2. Copy `data\raw\risk.db` from the other computer into the project's `data\raw\` folder
   (`data/raw/` is git-ignored, so it does not come with the repository).
3. Double-click `setup_bloomberg.bat` once: installs `requirements.txt` and `blpapi` from
   Bloomberg's official index, verifies the imports, and reports success or the exact failure.
4. Log in to the Bloomberg Terminal, then double-click `launch.bat`. The console prints
   `Bloomberg feed: started (every 2 min)` or the reason it did not start.
5. Double-click `run_bloomberg_diagnostic.bat` to see every spot/forward request as OK or
   FAILED with Bloomberg's error text (reports saved under `reports\`).

All three batch files use the same interpreter, `py -3` (the Python launcher), so the
packages installed by setup are the ones the app and the diagnostic run with.
Environment overrides: `BLP_HOST`, `BLP_PORT`, `RISK_LIVE=0` (disable the feed).

## Diagnostics

- In the app: the Cash ladder tab has a **Bloomberg diagnostics** panel listing every
  requested `(pair, mark type, settle date)` as OK with its value or FAILED with the
  Bloomberg detail, plus the spot rates the ladder is using and their freshness. It opens
  automatically when anything failed.
- Command line:

```powershell
py -3 -m data.bloomberg.live --status   # last cycle, itemised; exit 0 only if connected and nothing failed
py -3 -m data.bloomberg.live --once     # run one pull now and print the result
```

- Status file: `<db>.bloomberg_status.json` next to the database (git-ignored under
  `data/raw/`), rewritten every cycle, includes a traceback if a pull raised.

## P&L ledger (realised / unrealised / periods)

`engine/pnl/ledger.py`, run by every feed cycle and by "Pull from Bloomberg now":

- **Realised**: when a trade's settle date is before today it is frozen once in
  `realised_pnl` at the official SPOT dated its settle date (or the last spot before it,
  noted). No spot on or before the settle date -> listed as not realisable, never guessed.
- **Unrealised**: open trades at the latest spot (the exposure P&L on the ladder).
- **Snapshot**: one `pnl_snapshots` row per day (realised LTD, unrealised, total LTD, net,
  gross, trading), marked complete only when every currency was priced and every settled
  trade realised.
- **Daily / 5d / MTD / YTD**: today's total LTD minus the complete snapshot on the
  reference business day (Mon-Fri calendar). Missing or incomplete reference -> Unavailable
  with the reason. History starts the day the feed first runs.

Realised trades and the snapshot history are visible under the ledger block on the Cash
ladder tab. Tables are additive; the app creates them on an existing database at startup.

## Untouched by the feed

Exposure formulas, the BNP parser, the schema, and the workbook mark-to-market logic.
The workbook MTM section prices off official FWD_OUTRIGHT marks at the shared maturity
when the source dropdown is set to Official, so it also populates once the feed runs.
