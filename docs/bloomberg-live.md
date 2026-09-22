# Bloomberg live feed

Every official mark comes from Bloomberg or from the app's own pricers fed by Bloomberg
quotes. No sample or mock data feeds a figure. When Bloomberg is unavailable, rate cells
are blank and the page says so.

## What runs

`data/bloomberg/live.py` starts with the app (`py 2_launcher.py start`) when both hold:

1. the `blpapi` Python package imports;
2. a Bloomberg API service accepts a TCP connection (default `localhost:8194`, i.e. a
   logged-in Terminal or B-PIPE on this computer).

Every 2 minutes it runs, for the trades open on today's New York date:

| Step | Request | Stored as |
|---|---|---|
| SPOT per FX pair with an open leg | `PX_LAST` live ReferenceDataRequest on `<PAIR> Curncy` | `marks`, source `BBG_BFXFORWARD` |
| FWD_OUTRIGHT per open settle date | direct broken-date request, else standard-tenor interpolation | `BBG_BFXFORWARD`, fallback `BBG_INTERP` (never official) |
| FUTURE_PX per open contract | `PX_SETTLE` / `PX_LAST` on `<CODE> Index` | `BBG_BDH` |
| Rates step | OIS quotes and overnight fixings per swap currency, then the QuantLib bootstrap and swap valuation | `curve_quotes`, `index_fixings`, `curves`; `PV_USD` / `DV01_USD` / `PAR_RATE` / `CASHFLOW_USD` source `QL_PRICER` |
| Options step | FX vol surface (ATM, risk reversal, butterfly per tenor; tickers UNVERIFIED until the first live run) and OIS rates, then the option pricer | `PREMIUM` / `DELTA` / `GAMMA` / `THETA` / `VEGA` / `RHO` source `QL_OPTIONS_PRICER` |
| Realise | trades settled before today are frozen at their settlement-day mark | `realised_pnl` |
| Backfill | business days since the earliest trade that lack a complete close | same tables, in the background |

Rows are upserted on the same primary key with a fresh `snapped_at`, so the table always
holds the latest value. The ladder re-renders every 2 minutes (`dcc.Interval`) and reads
the latest official SPOT per currency (`rates_from_marks`); a mark older than 10 minutes
is flagged stale.

## Setting up the Bloomberg computer

1. Clone or open the project (e.g. in PyCharm) and open a terminal in its folder.
2. Optionally copy `data\raw\risk.db` from the other computer into `data\raw\` (git-ignored)
   if you want that computer's history. Otherwise the app starts with an empty database.
3. `py 2_launcher.py setup` once. It detects the Terminal, installs the packages listed in
   `PACKAGES` in `2_launcher.py` plus `blpapi` from Bloomberg's official index into `.venv`,
   verifies the imports and runs the tests.
4. Log in to the Bloomberg Terminal, then `py 2_launcher.py start`. The console prints
   `Bloomberg feed: started (every 2 min)` or the reason it did not start.
5. `py 2_launcher.py doctor --bloomberg` shows every spot/forward request as OK or FAILED with
   Bloomberg's error text (reports saved under `reports\`).

Every command runs inside `.venv`, so the packages installed by setup are the ones the app
and the diagnostic use. Environment overrides: `BLP_HOST`, `BLP_PORT`, `RISK_LIVE=0`.

## Diagnostics

- In the app: **Check Bloomberg connection** on the Market data tab runs
  `tools/bbg_diagnostics.py` and lists each check as PASS / WARNING / FAIL with a plain
  sentence: session, official-source mapping, spot / forward / futures coverage per open
  leg, OIS quotes, overnight fixings, option terms and option marks, PC clock versus New
  York, last pull, unverified vol tickers. The same checks from a terminal:
  `py 3_diagnostic.py`.
- `py 2_launcher.py doctor --bloomberg` tests every spot and forward request one by one
  with Bloomberg's own error text (reports under `reports\`).
- Command line:

```powershell
.venvScriptspython -m data.bloomberg.live --status   # last cycle, itemised; exit 0 only if connected and nothing failed
.venvScriptspython -m data.bloomberg.live --once     # run one pull now and print the result
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
