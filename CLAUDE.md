# risk-monitor

FX, futures, rates and options risk monitor for fund NMMF, base currency USD. Python, SQLite, Dash. It shows one P&L (per-trade valuation, an LTD line, Daily / 5d / MTD / YTD / trading), a cash ladder (delta exposure, cashflow timing, settled cash, stress) and a blotter (every trade with its P&L and Greeks).

This file is the rulebook: what is true of the app now and what must not change without the user's say-so. It is not a changelog. History lives in git and `docs/bnp-excel-removal.md`; open items live in `docs/open-questions.md`, not here; the valuation spec is `docs/BUILD_PLAN.md` sections 2 to 4, which agree with "P&L conventions" below. Reference input: `data/raw/new_sample_trades.csv`.

## Hard rules

1. **One trade source.** The uploaded blotter export, parsed by `data/ingest/blotter.py`, is the app's only trade file. The only other way a trade enters the book is the Blotter's Manual entry sub-tab (`source = 'MANUAL'`). There is no BNP file, no Excel workbook and no second feed (user decision 2026-09-17: "no bnp fall back - that excel and everything linked to it need to go"); nothing may reintroduce one. `data/raw/HA_PNL_*.csv` and `data/raw/HA-portfolio vJean.xlsx` stay on disk as untracked reference material that nothing in the app reads.
2. **A missing mark is worked out from the near marks, never from another source, and never silently.** No fallback mark *source* for P&L or delta, ever, and nothing is written to `marks` that Bloomberg or the app's own pricers did not produce. But a mark that is not on file for the exact key is estimated on the fly from the official marks nearest to it (user decision 2026-09-22: "if there is no price, we should always interpolate/extrapolate with near marks", then "use all marks available to extrapolate"; `engine/pnl/valuation.py::_mark_near`): a forward first along that day's own curve (spot at the spot date, which is `engine/pnl/calendar.py::spot_date`, the app's one rule since 2026-09-22: T+2 weekdays, T+1 for USDCAD / USDTRY / USDPHP / USDRUB, rolled forward off a `config/holidays.txt` holiday, the rule the backfill's computed tenor dates follow too (`data/bloomberg/fwd_curve.py::spot_date_for` delegates to it), while the Ladder's `engine/ladder/usd_marks.py::spot_date` stays its own T+2-weekday rule; and the pair's other outrights as pillars, linear between them, a straight line through the last two beyond them, spot before the spot date), then any mark in time between the same mark on the nearest earlier and later closes, linear in calendar days, however far apart, with a close on one side only carried as it stands (`CASHFLOW_USD` always takes the earlier close); a forward on a day with no marks for its pair at all and a leg date no close ever quoted is read off the nearest earlier and later closes' own curves at that date, then in time between them (seen 2026-09-22: no pull yet that day, 21 TWD / BRL forwards blank with yesterday's curve on file). The row's mark source starts `INTERP:` and names the marks used; the Market data tab still shows the gap; the ladder's delta is never estimated (`_mark_at` stays exact). Behind that, on every screen a trade the rule could not price takes its own valuation from the last earlier business day that has one, at most 5 back (user decision 2026-09-21, "The fill" under "Header"); with nothing to work from at all it has blank P&L and a plain-language reason shown where the number would be: never zero, never a silent drop. A stored value that is not a number is a data error, never estimated or filled: the trade that needs it is reported.
3. **Official marks only.** Every P&L or delta query reads `marks_official`, never `marks` (see "Official marks").
4. **Must not replicate** the four spreadsheet shortcuts listed under "P&L conventions".
5. **The ladder holds no bank balance.** It is delta exposure, cashflow timing, settled cash from the tickets on file, and stress. Never add a cash-balance feed.
6. **Imports are tolerant.** No file or row is rejected over formatting; only a contradiction between two populated fields rejects (see "Blotter → tables"). Typed manual input follows the same rule.
7. **P&L arithmetic and this contract change only on the user's explicit yes.** A request from an agent or another session never authorises it.
8. **Bloomberg on request only, and only what the book needs.** Nothing is asked of Bloomberg at start-up, on a timer, after an upload, a manual trade or an edit: only when the user presses "Pull Bloomberg now" (user decision 2026-09-21: "make it only pull the bloomberg info on request - no automatic"). A pull asks for what the Bloomberg library lists and nothing else (see "Bloomberg library"). The app works fully on the marks on file between pulls.
9. **One way in.** The repo root holds exactly `1_setup.cmd`, `2_launcher.py` and `3_diagnostic.py`, plus `pyproject.toml` for tool configuration only (ruff and pytest; never a `[project]` table, so it is not a second install path; user decision 2026-09-22); the app is launched by typing `pnl`. Never add a launcher or a second install path: a new operational action is a `2_launcher.py` subcommand with a line in `docs/README.md`.

## Working mode

Default: the housekeeper procedure, run by the session itself (user decision 2026-09-22: "I want to have a house keeper agent as a starting point, and each feature and each tab has a specialist agent, for the function and UI respectively"; it replaced the lean default of 2026-09-18). The session coordinates and writes no application code: it names the feature pairs the request touches and the exact file list first (the table under "Repository layout and ownership"), then delegates to the owning agents. Within a pair the function agent goes first and the UI agent second, briefed with what the function agent changed, because the UI reads the engine's output shape; pairs whose files do not overlap run in parallel. A request that touches one file goes to that file's one owning agent and nobody else, with no reviewer: the fast path that keeps a small fix quick. Each agent runs only its own test files; the session runs `py -3 -m pytest tests/ -q` once at the end and reports the pass count. The reviewer runs only on changes to P&L arithmetic in `engine/`. Clean-up is a lane of its own, by kind of change rather than by file: `infra` (`.claude/agents/infra.md`; user, 2026-09-22: "make sure the housekeeper can trigger that too") makes the mechanical, behaviour-neutral changes anywhere (dead code, unused imports, duplicate helpers, stale docstrings and references, module splits with every caller updated, ruff / pytest / git configuration, the `.claude/` tooling) and owns the root scripts, `config/`, `tools/`, the shared test fixtures, the golden book (`tests/golden/`, `tests/golden_book.py`, `tests/test_golden_book.py`, `tests/test_health.py`), `pyproject.toml`, `.gitattributes`, `.gitignore` and `.claude/settings.json` outright; formulas and SQL in `engine/`, pricing, official mark sources, schema DDL, CLAUDE.md and any test's expected value are out of its lane and come back as findings. It works from the health audit (`py 2_launcher.py health`) and proves every change with the golden book and the full suite. The golden book's pinned file, `tests/golden/book.json` (the sample blotter at synthetic marks, every valuation pinned), is regenerated only on the user's explicit yes, never by an agent (user decision 2026-09-22): a regeneration that made a P&L change disappear would defeat the proof, so hard rule 7 covers it. The session spawns it only when the user asks (after a feature lands, or as a weekly full pass), never on its own, after the feature commit, on a clean tree with no other agent running. `.claude/agents/housekeeper.md` is the same procedure for a detached run, when the user names it.

**Do not overdo it (user rule, 2026-09-18: "you're doing way too many random things and checks instead of just fixing the problems").** Fix what the user asked for, run the full suite once, push, report in a few lines, and stop. Scope is the user's list. Anything else found on the way (a reviewer finding, a hardening idea, a cosmetic issue) goes in a short "found, not done" list for the user to choose from; it is not started, and a finished agent is not resumed with new items, without the user's yes. The one exception is a finding that makes the requested fix itself wrong: say so in one line and fix that. Brief an agent for the fix and the tests that prove it: no browser proofs, fuzzing or per-agent full-suite runs unless the user asks; whoever spawned the agents runs the full suite once at the end. The reviewer runs on P&L arithmetic changes, and its findings are relayed to the user, not dispatched. Give one time estimate and keep to it by cutting scope, not by extending. Status updates are a few lines: no tables, no re-listing of open questions each time.

**Agent model (user decision 2026-09-18, reversing the earlier Sonnet-for-cost rule).** Every agent runs on Fable 5.1 at high effort (user decision 2026-09-22, "its too slow now, turn to high effort", down from extra-high): each `.claude/agents/*.md` carries `model: fable` and `effort: high`, and a spawn passes `model: "fable"`. Never Sonnet or Haiku. Say plainly which model is doing the work when asked.

**How every reply ends (user rule, 2026-09-18).** Every final message ends with these two sections, in this order, as bullet points. This applies to every chat session and to every agent's final report; whoever spawned an agent relays its two sections to the user instead of swallowing them.

- **What was done**: what changed and where, what was verified and how (the pytest pass count goes here), and anything skipped or left unverified, said plainly.
- **What needs your input**: only the decisions, approvals or facts the user alone can give, one bullet each, with a recommendation where there is one. If there is nothing, write "Nothing". A low-stakes, reversible choice is not input: take the recommended option and report it under "What was done" (user, 2026-09-18: "i dont have strong opinions i imagine your recs are the best"). Hard rule 7 still stands: P&L arithmetic and this contract always need the user's yes.

The working copy is `C:\Users\jeanw\risk-monitor`; GitHub `main` is the backup, pushed at the end of each task.

When agents or several sessions work in parallel, the ownership table under "Repository layout" defines the lanes. State your file list to the other sessions first and keep to it; message the owner and wait before editing outside your lane; commit by explicit path only (the git index is shared: run `git diff --cached --stat` immediately before every commit, never `git add -A`); preserve each file's line endings.

## Data contract

### Tables

All dates ISO `YYYY-MM-DD`, all amounts signed (`+` = receive / long). No column is nullable; "not applicable" uses the documented sentinel. `data/ingest/schema.py` is the executable copy of this section: a column added to its DDL is migrated onto existing databases automatically (`_migrate_columns`), so every `NOT NULL` column added later carries a `DEFAULT`.

```sql
instruments (
  instrument_id   TEXT PRIMARY KEY,   -- 'USDJPY', 'EURSEK', 'XAUUSD', 'ESU6 Index', 'IRSOIS-USD-22860996',
                                      -- 'USDJPY111926P-197571137' (option: one instrument per option trade),
                                      -- 'CASH-CAD'
  asset_class     TEXT NOT NULL,      -- FX | FUTURE | IRS | FX_OPTION | IRS_OPTION | EQ_OPTION | CMDTY_OPTION | CASH
                                      -- | INDEX (a listed option's underlying, 'SPX Index': holds its level, never traded)
  base_ccy        TEXT NOT NULL,      -- unit of trades.quantity: 'USD' for USDJPY, 'AUD' for AUDUSD, 'XAU',
                                      -- 'ES' (index units), notional ccy for IRS, base of pair for options
  quote_ccy       TEXT NOT NULL,      -- currency of trades.price and of local P&L
  multiplier      REAL NOT NULL,      -- 1 for FX / IRS / options; 50 for ES
  is_ndf          INTEGER NOT NULL,   -- 1 = non-deliverable: local legs do not settle
  bbg_ticker      TEXT NOT NULL,      -- 'USDJPY Curncy', 'ESU6 Index', ...
  expiry_date     TEXT NOT NULL       -- '9999-12-31' for perpetual (FX pairs, cash)
);
-- Always name the columns when inserting into instruments: an old database can carry
-- extra columns, and a positional INSERT fails every upload on it.

instrument_options (                   -- option terms, kept out of instruments
  instrument_id   TEXT PRIMARY KEY REFERENCES instruments,
  strike          REAL NOT NULL DEFAULT 0,             -- 0 = not known (never fabricated)
  option_type     TEXT NOT NULL DEFAULT '',            -- CALL | PUT | ''
  barrier_level   REAL NOT NULL DEFAULT 0,             -- barrier / touch level; 0 = n/a
  avg_start_date  TEXT NOT NULL DEFAULT '9999-12-31',  -- Asian averaging start; sentinel = n/a
  payoff          TEXT NOT NULL DEFAULT 'VANILLA'      -- VANILLA | DIGITAL | BARRIER_KI | BARRIER_KO
                                                       -- | ASIAN | ONE_TOUCH | NO_TOUCH | AMERICAN
);

trades (
  trade_id        TEXT PRIMARY KEY,   -- blotter 'Trade Id' column, or app-generated 'MANUAL-<n>'
  source          TEXT NOT NULL,      -- 'XLSX' = every blotter-sourced trade (a historical literal, kept so
                                      -- existing databases and the swap rule's "same source" test are
                                      -- unaffected) | 'MANUAL' = booked on the Manual entry sub-tab
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  product         TEXT NOT NULL,      -- FX_SPOT | FX_FWD | FX_SWAP | FUTURE | IRS | FX_OPTION
                                      -- | SWAPTION | CAP_FLOOR (engine/rates_vol; no ingest path yet)
                                      -- | EQ_OPTION (a listed index option, from the blotter) | CMDTY_OPTION (engine/options; no ingest path yet)
  package_id      TEXT NOT NULL,      -- = trade_id unless grouped by the swap rule below
  trade_date      TEXT NOT NULL,
  quantity        REAL NOT NULL,      -- signed, in base_ccy units: base amount (FX), contracts (FUTURE),
                                      -- notional (IRS: + = pay fixed), notional (FX_OPTION: + = long)
  price           REAL NOT NULL,      -- fill: forward outright / futures price / fixed rate / premium per unit
  account         TEXT NOT NULL,      -- 'BNPP-IPBFX-NMMF', ...
  counterparty    TEXT NOT NULL,
  strategy        TEXT NOT NULL,      -- '' for blotter trades (the export has no such column)
  trader          TEXT NOT NULL,
  description     TEXT NOT NULL,      -- raw description or free text
  theme           TEXT NOT NULL DEFAULT ''   -- bundle membership, see instrument_theme
);

instrument_theme (                     -- theme = bundle name; membership of a Blotter bundle
  instrument_id   TEXT PRIMARY KEY REFERENCES instruments,
  theme           TEXT NOT NULL
);

bundles (                              -- a bundle's own metadata only, never its membership
  name            TEXT PRIMARY KEY,
  description     TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

trade_legs (
  trade_id        TEXT NOT NULL REFERENCES trades,
  leg_no          INTEGER NOT NULL,
  leg_type        TEXT NOT NULL,      -- FX_NEAR | FX_FAR | FIXED | FLOAT | NOTIONAL
  ccy             TEXT NOT NULL,
  amount          REAL NOT NULL,      -- + = receive / long, − = pay / short
  start_date      TEXT NOT NULL,      -- trade_date for FX; effective date for IRS
  settle_date     TEXT NOT NULL,      -- value date / maturity / futures expiry
  rate            REAL NOT NULL,      -- fill for FX legs; fixed rate or spread for IRS; 0 for NOTIONAL
  settles_cash    INTEGER NOT NULL,   -- 0 for NDF local legs and for FUTURE / FX_OPTION notional legs
  PRIMARY KEY (trade_id, leg_no)
);

marks (
  as_of_date      TEXT NOT NULL,
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  settle_date     TEXT NOT NULL,      -- outright date; = as_of_date for SPOT; expiry for futures
  mark_type       TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX | PAR_RATE | PV_USD | DV01_USD | CASHFLOW_USD
                                      -- | PREMIUM | DELTA | GAMMA | THETA | VEGA | RHO
                                      -- | DELTA_PA (premium-adjusted delta, written only for G10 pairs quoted
                                      --   that way; the ladder reads DELTA, never DELTA_PA)
                                      -- | NDF_1M (Bloomberg's 1M NDF outright on the USD pair, as quoted,
                                      --   settle_date = as_of_date; the ladder's rate for an NDF currency)
                                      -- | NDF_FIX (the currency's own official fixing on the USD pair, dated
                                      --   the fixing date, settle_date = as_of_date; the NDF's exit price)
  value           REAL NOT NULL,      -- DELTA = base-ccy delta per 1 unit of trades.quantity (may exceed 1 for digitals)
  source          TEXT NOT NULL,      -- BBG_BFXFORWARD | BBG_BDH | BBG_BDP | BBG_INTERP | QL_PRICER
                                      -- | QL_OPTIONS_PRICER | MANUAL
  snapped_at      TEXT NOT NULL,      -- ISO timestamp, offset resolved from America/New_York for that row
  PRIMARY KEY (as_of_date, instrument_id, settle_date, mark_type, source)
);

curves (                               -- curve nodes so the IRS pricer can be rebuilt without a migration
  curve_id        TEXT NOT NULL,      -- 'USD-SOFR-OIS', 'USD-SOFR-PROJ', ...
  as_of_date      TEXT NOT NULL,
  node_date       TEXT NOT NULL,
  discount_factor REAL NOT NULL,
  par_rate        REAL NOT NULL,      -- 0 where the node carries no par rate
  source          TEXT NOT NULL,      -- BBG_BDP | BBG_BDH | MANUAL | QL_PRICER (bootstrap output, engine/rates)
  PRIMARY KEY (curve_id, as_of_date, node_date, source)
);

curve_quotes (                         -- raw OIS quote staging (bbg-data writes, engine/rates reads)
  as_of_date      TEXT NOT NULL,
  ccy             TEXT NOT NULL,
  index           TEXT NOT NULL,      -- 'SOFR', 'ESTR', 'SONIA', 'TONA', 'SARON', 'CORRA', 'AONIA' (Phase 1: OIS only)
  tenor           TEXT NOT NULL,
  ticker          TEXT NOT NULL,
  value           REAL NOT NULL,
  quote_type      TEXT NOT NULL,
  field           TEXT NOT NULL,
  source          TEXT NOT NULL,
  PRIMARY KEY (as_of_date, ccy, index, tenor, source)
);

index_fixings (                        -- overnight fixings loaded into QuantLib by engine/rates before pricing a
                                       -- seasoned swap; written by data/bloomberg/rates_marketdata.py::write_fixings
  index           TEXT NOT NULL,      -- 'SOFR', 'ESTR', ...
  fixing_date     TEXT NOT NULL,
  value           REAL NOT NULL,      -- decimal (0.0533 = 5.33 %)
  source          TEXT NOT NULL,      -- BBG_BDH | MANUAL
  PRIMARY KEY (index, fixing_date, source)
);

realised_pnl (                         -- one frozen row per settled trade, written by engine/pnl/ledger.realise_settled;
                                       -- a SETTLED row in value_book comes from here and is never recomputed
  trade_id            TEXT PRIMARY KEY REFERENCES trades,
  instrument_id       TEXT NOT NULL,
  product             TEXT NOT NULL DEFAULT 'FX_FWD',
  currency            TEXT NOT NULL,
  settle_date         TEXT NOT NULL,
  local_amount        REAL NOT NULL,
  usd_entry_amount    REAL NOT NULL,   -- 0.0 = not applicable (crosses, futures), not "zero P&L"
  mark_type           TEXT NOT NULL DEFAULT 'SPOT',   -- which mark_type froze this row
  spot_usd_per_local  REAL NOT NULL,
  spot_as_of_date     TEXT NOT NULL,   -- date of the mark used to freeze (settle date, or last prior)
  spot_source         TEXT NOT NULL,
  pnl_usd             REAL NOT NULL,
  frozen_at           TEXT NOT NULL,
  note                TEXT NOT NULL    -- '' or 'spot dated <d> (last before settlement)', or for an NDF
                                       -- 'spot dated <d> (present spot: none on file on or before settlement)'
);

swap_review (                          -- ambiguous FX-swap candidates the package rule refuses to auto-group
  candidate_group     TEXT NOT NULL,   -- 'source|account|instrument_id|trade_date'
  trade_id            TEXT NOT NULL REFERENCES trades,
  reason              TEXT NOT NULL,
  PRIMARY KEY (candidate_group, trade_id)
);
```

```sql
bbg_library (                          -- what the trades on file need from Bloomberg for their P&L (see "Bloomberg library")
  trade_id        TEXT NOT NULL,      -- no foreign key: an upload rewrites the whole book before the sync runs
  kind            TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX | NDF_FIX | OIS_CURVE | FIXINGS | VOL_SMILE | NDF_1M | DIV_YIELD
  key             TEXT NOT NULL,      -- pair / future instrument_id; currency for OIS_CURVE and FIXINGS
  settle_date     TEXT NOT NULL,      -- FWD_OUTRIGHT, FUTURE_PX: the date marked; '9999-12-31' otherwise
  bbg_ticker      TEXT NOT NULL,      -- the security asked for; '' where the kind stands for a set of them
  role            TEXT NOT NULL,      -- PAIR | CONVERSION (a USD-conversion pair's SPOT) | UNDERLYING (a listed option's index level)
  product         TEXT NOT NULL,
  needed_from     TEXT NOT NULL,      -- the trade's trade_date
  needed_until    TEXT NOT NULL,      -- settle date / expiry / maturity: not asked for after it
  added_at        TEXT NOT NULL,
  PRIMARY KEY (trade_id, kind, key, settle_date)
);

bbg_library_state (                    -- one row; dirty = 1 when the trades changed since the last sync
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  dirty           INTEGER NOT NULL DEFAULT 1,
  synced_at       TEXT NOT NULL DEFAULT ''
);
```

Views: `marks_official` (below) and `trades_official` (a passthrough of `trades`, the name every engine query reads). Both are dropped and recreated on every startup by `create_schema`, never `CREATE VIEW IF NOT EXISTS`, so a view-definition change always reaches an existing database.

`engine/rates_vol/` keeps its option attributes, manual vols and SABR / Hull-White parameters in its own defensively created tables (`instrument_rate_options`, `rate_vols`, `rate_model_params`), keyed by instrument, not by trade.

`data/ingest/schema.py::purge_retired_sources` runs once per startup and removes what the retired BNP parser and workbook left in an old database (`source = 'BNP'` trades, `BNP_BVAL` / `WORKBOOK_REFERENCE` marks, the `positions` table). It never deletes `BBG_INTERP`.

Leg layouts: FX spot / forward = 2 legs (`FX_NEAR`, one per currency); FX swap = 4 legs (`FX_NEAR` × 2, `FX_FAR` × 2) under one `trade_id`; FUTURE = 1 `NOTIONAL` leg in USD, amount `contracts × multiplier × fill`, `settle_date` = expiry, `settles_cash` 0; IRS = `FIXED` leg (amount = `−quantity`, rate = fixed) + `FLOAT` leg (amount = `+quantity`, rate = spread), `settle_date` = maturity, so a payer (`quantity > 0`) has a negative FIXED leg (pays) and a positive FLOAT leg (receives); FX_OPTION = 1 `NOTIONAL` leg in base ccy, `settles_cash` 0.

### Official marks

`marks.source` is part of the primary key, so joining `marks` directly returns one row per source and double-counts as soon as two sources exist for the same date. Exactly one source is official per `mark_type`:

| mark_type | official source |
|---|---|
| SPOT | BBG_BFXFORWARD |
| FWD_OUTRIGHT | BBG_BFXFORWARD where Bloomberg quotes that exact date (a standard tenor of `FWD_CURVE`); otherwise BBG_INTERP (user decision 2026-09-18): linear interpolation between the two bracketing standard-tenor outrights of Bloomberg's own curve, never extrapolated beyond the last tenor. A direct quote always wins over an interpolated row for the same key |
| FUTURE_PX | BBG_BDH (a future's price; also Bloomberg's own price of a listed index option, written on the option's instrument at its expiry date, in index points) |
| NDF_1M | BBG_BFXFORWARD (the 1M NDF tickers in `data/ingest/common.py::NDF_1M_TICKERS`; read by the ladder only, never by a P&L query) |
| NDF_FIX | BBG_BDH (PX_LAST of the currency's fixing ticker, `data/ingest/common.py::NDF_FIX_TICKERS`, for the fixing date itself, whether pulled live or by the backfill) |
| PAR_RATE, PV_USD, DV01_USD, CASHFLOW_USD | QL_PRICER (`engine/rates`) |
| DELTA, DELTA_PA, PREMIUM, GAMMA, THETA, VEGA, RHO | QL_OPTIONS_PRICER (`engine/options`, vendored options_calc) |

Never official, reconciliation only: `BBG_INTERP` for anything but the FWD_OUTRIGHT fallback (a SPOT or FUTURE_PX is never interpolated into existence); `BBG_BDH` (Bloomberg SWPM) for PAR_RATE / PV_USD / DV01_USD; `MANUAL` for option premiums and Greeks; any `BNP_BVAL` row surviving in an old database (nothing writes one any more).

The mapping is the `marks_official` view: `marks` filtered to the official source per `mark_type`, plus the FWD_OUTRIGHT fallback row only where no direct-quote row exists for the same key (`data/ingest/schema.py::OFFICIAL_MARK_SOURCE` and `OFFICIAL_FALLBACK_SOURCE`), so `(as_of_date, instrument_id, settle_date, mark_type)` is unique.

Standard-tenor forward points are official. The live pull (`data/bloomberg/live.py`) requests Bloomberg's bulk `FWD_CURVE` table once per pair, for every pair with an open FX leg and every open FX option's underlying pair, and writes each standard-tenor row as an official `BBG_BFXFORWARD` `FWD_OUTRIGHT` at that tenor's own settle date, because each row is Bloomberg's own outright quote. A leg or option expiry that falls between two tenors is written as `BBG_INTERP` (the API exposes no direct broken-date outright, `docs/open-questions.md` item 27); that is how a broken-date leg gets its P&L at all. P&L reads forwards by exact leg date, so the tenor rows change nothing there; they exist so that `engine/options/rates.py`'s covered-interest-parity fallback has official points to interpolate between when a currency has no OIS curve (SEK, NOK, TWD, ZAR). The pull reports them under `curve_points_written`, separately from the requested-mark count.

Past closes come from the backfill (`data/bloomberg/backfill.py`), which runs straight after every requested pull, the header's reference dates first; the same button press does both and no screen runs it separately. A past FX close is Bloomberg's 15:00 New York value (see "Mark time"), read from its intraday bars (`pull_marks.fetch_intraday_close_series`: the BID and ASK bar ending 15:00, mid of the two closes, one request per ticker and side per stretch) for every day that history reaches, which since 2026-09-22 is every past day of the book (user decision 2026-09-22: "for fx use new 3pm", replacing the 2026-09-21 cut-over that stopped at that date); a day the intraday history no longer reaches is asked of the daily history (`PX_LAST`, one request per stretch). A past NDF fixing date with no official `NDF_FIX` on file is asked for the same way, since the fix of that date is the only exit price a ticket may take ("Mark date"). After a day's closes are written the backfill asks Bloomberg's daily history (PX_LAST, one request per kind per stretch; user decision 2026-09-22, so options and swaps have true 5d / MTD / YTD and an LTD chart) for the vol-smile quotes of every pair with an FX option open that day and the OIS quotes of every currency an open FX option or swap needs that day, the same tickers the live vol and rates steps ask for, and writes them into `vol_quotes` / `curve_quotes` dated that day under BBG_BDH (a day that already holds a pair's or a currency's quotes is not asked again; a currency with no OIS curve in scope never is); it then prices that day's swaps from them (`engine/rates/store.py::recalc_on_file` for that day) and the FX options open and not closed out that day (`engine/options/store.py::price_close`; user, 2026-09-22: "I expect every time I pull bloomberg now, the options are repriced with the latest data, and that the latest data is also logged", after a day on which option marks were on file for one close only, so the near-marks rule carried that premium to every other close and the options' Daily was 0) from that day's own inputs (its SPOT, forward curve, OIS curve and vol smile; never another day's), so a past close has its own PV, PREMIUM and Greeks and a period difference is the true move; an option or swap with an input missing that day is skipped with its reason and the day stays listed as incomplete for that input. Every requested pull re-prices every open option from the pull's own spot, forwards, curves and vols and replaces that day's option marks. Bloomberg serves no historical `FWD_CURVE`, so a past day's curve is built from the standard-tenor tickers' history, converted from points to an outright the way the live tenor path does where they quote points: the divisor is Bloomberg's own, `FWD_POINTS_SCALE` or else 10 ** `FWD_SCALE`, never a hard-coded pip size, and a converted forward more than 20 % from that day's spot is not written. A tenor's value date is Bloomberg's own `SETTLE_DT` when history returns one; otherwise it is computed by market convention from that day's spot date (T+2, T+1 for USDCAD / USDTRY / USDPHP / USDRUB; week tenors following, month tenors modified following with the end-of-month rule; `config/holidays.txt` is the only calendar, so a pillar can sit a day from Bloomberg's around a local holiday). Every forward built from points or placed on a computed date is written as `BBG_INTERP`, never `BBG_BFXFORWARD` (user decision 2026-09-21), and nothing is extrapolated beyond the last tenor. The backfill asks Bloomberg's history only for what the book needed (user decision 2026-09-21, read from the Bloomberg library): the days being worked, as stretches of consecutive business days, never the days in between; in a stretch, the pairs and futures needed inside it; on a day, the closes of the pairs needed that day; and per pair the standard tenors from SP up to the one that clears its furthest open leg by a week (SP to 1M always, so points are still told from outrights), which gives the same forwards as all eight because a forward is read between the two tenors either side of its date. Bloomberg is pulled on request only (hard rule 8; user decision 2026-09-21, which replaced the 15-minute feed of the same day): `live.LiveFeed` sleeps until "Pull Bloomberg now" and then runs one cycle, today's marks and then the backfill. `live.INTERVAL_SECONDS` is only the screens' own re-read of the marks on file.

### Marks snapshot

`data/bbg_snapshot/` (`data/bloomberg/snapshot.py`; user request 2026-09-21: "I have another machine that has bloomberg access ... set up the git commit so I can git push everything and load the bbg marks here") is how the marks reach a PC without a Terminal: the database is git-ignored, so nothing Bloomberg wrote was ever in git. Every "Pull Bloomberg now" ends with the saving (user, 2026-09-22: "I dont want to need to run step 3 export, jsut set ut up so every pull from bbg triggers the saving", then "dont need to trigger commit and push, jsut need to make sure the bbg data is logged and stored locally, I will trigger the commit and push myself"; `snapshot.save_after_pull`, run by `backfill.start_auto_backfill` after the backfill of a real pull only, never after a test's fake fetches, and off under `RISK_SNAPSHOT=0`): the export alone, the files left in the working copy for the user's own commit and push, its outcome one line in the status file's backfill block and never a failure of the pull. `2_launcher.py marks-export` is the export by hand plus a commit of `data/bbg_snapshot/` alone (`--push` also pushes). The export writes every table a pull writes (`snapshot.MARKET_TABLES`: `marks` whole, every source, values, sources and snap times untouched; `curves`, `curve_quotes`, `index_fixings`, `vol_quotes`, `rate_vol_quotes`, `equity_dividend_yields`), the `instruments` rows the marks hang off, each table's DDL and the last pull's status JSON as plain CSV / JSON in primary-key order, and commits that folder by explicit path (`--push` also pushes; identical data commits nothing). `marks-import` on the other PC makes its market data what the Bloomberg PC had at the export: in every table rows of any source but `MANUAL` are dropped first (so a row the backfill has since replaced under another source cannot stay on and win in `marks_official`), a local instrument is never overwritten, then `realise_settled` runs as the backfill's closing step would, since no pull runs there; it refuses on a PC that has Bloomberg unless `--force`. Imported rows are Bloomberg's and the app's pricers' own output and count as such under hard rule 2. No trade travels (hard rule 1), and no trade file is ever committed (user, 2026-09-22: "i dont want to commit any trade files"): the blotter is uploaded on each machine, `.gitignore` keeps a spreadsheet or CSV dropped under `data/` or at the root out of git, and the snapshot never carries one. Nothing in the app calls either command (hard rule 8 is untouched: the import asks Bloomberg nothing). Not carried: option terms and MANUAL trades typed on the Bloomberg PC, manual vols, and `vol_ticker_checks` (that PC's own ticker diagnostics).

### Bloomberg library

`bbg_library` (`data/bloomberg/library.py`) is the one record of what the trades on file need from Bloomberg for their P&L; the live pull, the rates step, the vol step, the backfill and the Market data tab's "needed" lists all read it, so what is not in it is never asked for.

- Per trade, with the dates it is needed between. FX spot / forward / swap: the pair's SPOT until the last leg settles, a FWD_OUTRIGHT at each leg's own date, and for a cross the SPOT of each currency's USD pair; with an NDF currency, also that currency's NDF_1M ticker, for today's pull only, and for a USD NDF pair its NDF_FIX (user, 2026-09-22: "the exit price is the fix on that day, as pulled from bbg"; "each ndf has a unique fix"): the currency's own fixing ticker (`NDF_FIX_TICKERS`: BZFXPTAX for BRL and JISDOR for IDR, both returning fixings since 2026-09-22; KOBRUSD for KRW and TRY11 for TWD, the user's terminal check of 2026-09-22 replacing KFTC18 and TAIFX1, which loaded but returned nothing; FBIL for INR, unverified), asked for on the ticket's fixing date alone (`needed_from = needed_until` = that date), written on the pair dated that date; a past fixing date's is the backfill's to fill, like a close. Only INR's ticker is still unverified against a terminal (`docs/bloomberg-pc-checklist.md`). Future: FUTURE_PX at expiry. Listed index option (EQ_OPTION), until expiry: FUTURE_PX on the option's own Bloomberg ticker (`library.listed_option_ticker`, 'SPX US 10/16/26 P7615 Index'), which is all its P&L needs, and for today's Greeks only the index level (SPOT of 'SPX Index', role UNDERLYING, never asked of Bloomberg's history), the USD OIS curve and the index's dividend yield (DIV_YIELD). FX option, until expiry: the pair's SPOT and its USD-conversion SPOTs, the OIS curve of both currencies and the pair's vol smile on every day it is open (live today, Bloomberg's daily history for a past close), and for today's pricing only a FWD_OUTRIGHT at the expiry. IRS, until maturity: the currency's OIS curve on every day it is open, and today only its fixings (the live rates step pulls the fixing history from the swap's start). Nothing is asked for after `needed_until`; an option or a swap the ledger has realised is left out of a live pull.
- It changes only when the trades change, or when the code's list of needs does. Triggers on `trades` / `trade_legs` set `bbg_library_state.dirty`; an upload syncs it at once and says so in its summary; any reader finding it dirty, or stamped by another `library.LIBRARY_VERSION` (bumped whenever `compute` learns a new kind or ticker rule; 2026-09-22, when `NDF_FIX` reached no existing database until its next upload), syncs it before reading (a read-only connection works the same rows out in memory). A pull never writes it.
- The Market data tab lists it ("Bloomberg library": ticker, field, what it is for, how many trades, until when).

### Blotter → tables

`data/ingest/blotter.py` parses the transaction-level blotter export (`data/raw/new_sample_trades.csv`-shaped files): one row per fill, with a genuine per-fill `Price` and `Trade Id` for every product. Shared dataclasses, regexes and helpers live in `data/ingest/common.py`.

Scope: a row is excluded only when its `Status` says cancelled / rejected / pending / void / deleted / failed, or its `Fund` is populated and is not `NMMF` (a missing column or blank cell never excludes). Row kind is decided by `Fin Type`, matched by keyword after normalisation (`Futures`, `FX Forward`, `Interest Rate Swap` all resolve; a label with `swap` is `INTEREST_RATE_SWAP` only with a rates word (interest, rate, IRS, OIS), `FORWARD` with an FX word (FX, currency, forward, foreign exchange), since an FX swap's rows are forward fills with their own value dates, and `Swap` alone is skipped: reviewer finding, 2026-09-22), with `Product` as the fallback when `Fin Type` is blank or unrecognised: `FORWARD | CURRENCY | FUTURE | OPTION | INTEREST_RATE_SWAP`; anything else is counted and skipped, never coerced.

Tolerance rule: a blank, missing or oddly formatted field never rejects a row when the value can be recovered from another column (forwards fall back from the Description to `TradeDate` / `Settle Date` / `Buy Currency` / `Sell Currency` / `Price`; IRS to Description / `Currency` / `Quantity` × 1e6; options to `Currency Pair` / `Adj. Expiry Date` / `FxOption Type`); only a contradiction between two populated fields does. A repeated `Trade Id` within a file keeps the highest `Version`. File reading (`blotter.read_table`) accepts UTF-8 / BOM / cp1252, comma / semicolon / tab / pipe delimiters, a header row after preamble lines, any header casing, and multi-sheet workbooks with real Excel date cells. Parser guesses are listed in `docs/blotter-parser-assumptions.md`.

- FORWARD: `Symbol` is `<PAIR><VD mmddyy>-<id>`; that trailing id is the `Instrument Id`, not the trade id, which comes from the `Trade Id` column. Base / quote leg amounts come from the structured `Buy Currency` / `Sell Currency` / `BuyCurrency Amount` / `SellCurrency Amount` columns, cross-checked against the description's sold / bought currencies. The blotter's Buy / Sell is our side, for USD-base pairs too (`docs/open-questions.md` item 70).
- CURRENCY: spot FX fills. A row naming both a `Buy Currency` and a `Sell Currency` is a spot trade in its own right and is written as product `FX_SPOT` with the same two `FX_NEAR` legs a forward gets, dated on `Settle Date`, so its cash reaches the ladder and its P&L the book. The `CASH-<ccy>` instrument is still written for the row's own currency; a single-currency row (fee, balance, one-sided movement) stays instrument-only, never a trade, never a reject. Spot trades are not candidates for the FX-swap package rule.
- FUTURE: a trade + 1 `NOTIONAL` leg per fill (`Quantity` = contracts signed by `Side`, `multiplier` = 50 for ES).
- OPTION: product `FX_OPTION`, 1 `NOTIONAL` leg in the pair's base currency (`Currency Pair` column), quantity signed by `Side` (Buy = long = +), price = premium fill as a fraction of base notional. Terms the export leaves blank (the reference sample's three digitals carry no strike) are typed once on the Blotter's Manual entry sub-tab and stored in `instrument_options`. A listed index option (`Symbol` 'SPX/E261016P7615-USAA') is product `EQ_OPTION`: quantity = contracts signed by `Side`, price = the premium in index points, `multiplier` = the contract multiplier (100 for SPX), `bbg_ticker` = the underlying index ('SPX Index'), strike / type / expiry read from the Symbol.
- INTEREST_RATE_SWAP: + = pay fixed, − = receive fixed. `Side` carries no direction here (always `'Buy'` in the reference sample). A short is whatever the book marks with brackets or a minus sign (user, 2026-09-18): on `Notional`, or on `Quantity` where an export leaves `Notional` unsigned; the magnitude still comes from `Notional`, which is already full-unit (not millions-scaled like this file's `Quantity`). The reference sample carries no sign on either column, so it cannot show this; the user's live export does. A swap the user flips (`data/ingest/irs_direction.py::set_direction`) keeps its priced history by sign reversal, done by the pricer itself (`engine/rates/store.py::reverse_direction_marks`: its own `QL_PRICER` PV_USD / DV01_USD / CASHFLOW_USD rows × −1 on every date, PAR_RATE untouched, other sources' rows deleted; reviewer finding 2026-09-22, hard rule 2), never by the ingest layer.

`trades.strategy` is `''` for blotter trades.

### Upload and manual entry

- **An upload replaces the whole book** (user decision 2026-09-17: "when a new excel is put in - that's the only input for the trades - all of the old stuff gets deleted"). `data/ingest/upload.py::import_blotter` deletes every non-MANUAL trade and its trade-keyed rows (`trade_legs`, `realised_pnl`, `swap_review`) inside the same transaction that publishes the new file, and only after the new file has parsed, so a parse failure leaves the existing book intact. Instruments, marks, curves and fixings are untouched. `blotter.load` itself stays an idempotent upsert by `trade_id` for library callers, and dissolves any swap package containing a replaced trade so the package rule re-runs.
- **Manual entry** (`data/ingest/manual.py`, Blotter sub-tab): books OTC trades the export does not carry, as `source = 'MANUAL'`, ids `MANUAL-<n>`, with the same instrument / trade / leg shape the parser writes for that product, so every engine query sees them like any other trade. A MANUAL trade survives every upload and leaves only through `delete_manual_trade`.
- An upload or a manual trade asks nothing of Bloomberg. The upload brings the Bloomberg library up to date and its summary says how many tickers the new book needs; the next "Pull Bloomberg now" prices it.
- The launcher's sample import runs only on an empty database.
- After an upload or a marks write the UI refreshes in place, with no browser reload (`ui/revision.py`).

### `package_id` rule (FX swaps)

Two forward rows form one `FX_SWAP` package when all hold: same source, same account, same pair, same trade date, opposite-signed quantities, equal |USD-leg amount| within 0.01 % (for a cross with no USD leg, equal |base amount| within the same tolerance), **different** value dates. `package_id = 'SWAP-' || min(trade_id)`; near leg = earlier value date. Opposite-signed rows with the same value date are intraday round trips and stay separate outrights. Groups with more than one candidate on a side are not auto-grouped; they go to `swap_review`.

### Tabs as views

Four tabs under a header that sits above all of them (`ui/app.py::VISIBLE_TABS`), in this order: Blotter, Ladder, Risk, Market data (user, 2026-09-22: "I want the first tab to be blotter, and the second to be cash ladder"; the Risk tab came the same day, user: "I want to create a new tab to see all these metrics, as a book, and as per underlyers"; the app opens on the Blotter). Every tab is a read-only view: `ui/` never recomputes P&L or delta. The header and the ladder's risk table are always the whole book; filters shape only the view they sit on.

**Header (all tabs).** LTD, Daily, Previous day, 5d, MTD, YTD and Trading P&L, the trade count (open / settled), Net and Gross USD delta, and a collapsible LTD line chart over every business day from the book's first trade date to the as-of date (user, 2026-09-22: "I want to see the ltd line chart, which requires all the previous closes"; it showed the last 20 business days before), one valuation per day, memoised on the database's mtime and built only when the chart is opened. Its as-of is today in New York by default (user, 2026-09-22: "by default, always price pnl as of today, so that the top bar numbers all reflect todays numbers, unless changed specifically otherwise"): the layout is built on every page load so the default is never frozen at start-up, the Blotter's and the Ladder's date pickers feed it when the user changes one (last change wins; picking today itself, the Today buttons, is not a departure), and after 17:00 New York, the FX day roll (user, 2026-09-22: "only roll to new day after new york 5pm", then "all date rollover at hkt 5am"; `data/bloomberg/live.py::book_today`, `ROLLOVER_HOUR_NY`, the one day boundary of the app, see "Mark time"; `cash_ladder.today_ny` returns it), the header and both pickers roll to the new day unless another day was picked (`ui/app.py`, `header.as_of_after_pick` / `as_of_after_tick`). Marks keep the New York calendar date (`live.book_today`); a manual trade is dated the calendar day it was dealt. A day with no marks yet is valued off the near marks (hard rule 2), so today's figures are the latest closes carried forward until the day's pull. Aggregation: a figure sums the priced trades only and says so in a visible caption ("excludes N of M trades unpriced", breakdown by product and reason on hover); a period difference leaves out any trade priced on only one of its two dates; and when the trades unpriced on the reference date outnumber those priced at both ends, that close is not usable. A period whose reference close is not usable steps its reference date back one business day at a time to the first earlier close that has value, at most 5 business days (user, 2026-09-21: "let it backfill up to 5 days"), and names the date it used in its caption (user decision 2026-09-21: "use previous date until has value"); only when none of those has value is the period n/a, with a sentence naming the reference date. **The fill** (user decision 2026-09-21: "there should be a fill when bloomberg doesnt have the data", after "how is that possible given the fill function??" and "ive told you like 5 times about the fill function"): on any date a screen values, the date looked at as much as a reference close, a trade with no price takes its own valuation from the last earlier business day on which it has one, at most 5 back (`engine/pnl/reference.py::fill_book`, applied once in the reader every screen shares, `ui/tabs/blotter_pricing.py::priced_value_book`). The row keeps what it is on that date and takes the earlier close's mark, spot and P&L whole, never a mix of two dates; its note opens "no price on <date>: value of the <earlier> close" followed by why it had none, the LTD card and each period card say how many trades were filled and back to which close, with each trade's note on hover, and the chart's hover says so per day. A filled value is never carried further (each date is filled from unfilled valuations), a trade with no earlier price in reach stays blank with its reason, and a stored value that is not a number is never filled. Nothing is written to `marks`: the Market data tab still shows the mark as missing, and the ladder's delta is not filled. The engine's own `value_book` and `ledger.ltd` stay unfilled (NaN if any row is NaN); the fill and the partial sum are the screens' rule, never a change to a trade's P&L formula. Since 2026-09-22 the near-marks rule of hard rule 2 runs first, inside `value_book`, so the fill only reaches a trade with no mark of its kind on any date. The trade count and Net / Gross USD delta are shown even when no P&L mark exists. No figure is ever blank without its reason.

**Ladder.** "What am I long or short, and when is it cash?"
- Layout (user decisions 2026-09-21): one table, one row per currency: FX rate as quoted, Local delta and USD delta first, then Settled cash, the dates across and the total of the columns shown, with the USD equivalent (and the net USD delta) as its bottom row. The rate / delta figures were first a table of their own above the grid; the user found that clunky and odd to scroll. A missing or refused rate is named in a caption, never in a repeated "Rate source" row.
- Grid: `trade_legs` where `settles_cash = 1 AND settle_date ≥ as_of`, grouped by `ccy, settle_date`, leg by leg, crosses included. No P&L on it.
- **NDF currencies** (`data/ingest/common.py::NDF_CCYS`: KRW, IDR, INR, TWD, BRL; user decision 2026-09-21). An NDF ticket sits on its fixing date, not its value date, in the grid and in the tab's own delta rules: value date less 2 business days on `config/holidays.txt`, computed because the export carries no fixing date. Its rate everywhere on the tab and in the header's Net / Gross USD delta is the official `NDF_1M` mark (KWN+1M, IHN+1M, IRN+1M, NTN+1M, BCN+1M), never spot ("based off the monthly not the spot"); with no 1M price on file the currency has no rate and says why. Before its fixing an NDF's P&L is untouched: it is still marked at its value date's forward and converted at spot.
- **An NDF that has fixed disappears from the ladder** (user, 2026-09-22: "NDFs - once they expire, they should disappear, not become setteld cash. I was wrong. 0 delta and 0 carry, they just disappears as they expired"; it reversed the 2026-09-21 decision that put a fixed NDF's legs in Settled cash). From the day after its fixing date an NDF ticket is nowhere on the tab: no leg on the grid, no delta, nothing in Settled cash (neither its local legs nor its USD settlement) and nothing named under the grid, realised or not. On the fixing date itself the grid still shows it under that date and the delta already leaves it out. Its P&L lives in the Blotter, frozen at the fixing (see "Mark date" and "Settled trades are frozen").
- `≥` here versus `>` in the delta query is intentional: a leg settling on `as_of` is cash that moves today but carries no delta by close.
- **Settled cash row**, on top (user decision 2026-09-18: "expired tickets must settle not disappear"; `engine/ladder/exposure_adapter.py::settled_records_from_db` is its only source). Deliverable legs past their value date sit there at face value in their own currency and still carry that currency's delta (NOK received on a settled forward is NOK exposure until sold). Non-deliverable tickets other than NDFs (futures, FX options) contribute their USD settlement read from `realised_pnl`, never recomputed; one the ledger has not realised yet is named under the grid, not valued. This is settled cash from the tickets on file, not a bank balance.
- The tab's per-currency delta therefore includes settled deliverable cash, and deliverable legs settling on `as_of` count as cash by close; an NDF's legs carry none from its fixing date on (above). The SQL delta query below and the per-pair Position table stay open-forward-only. Delta rows are at spot (an NDF currency at its 1M NDF price). Gold keeps its own sign in the dollar-convention column (a metal is not a dollar position).
- **USD equivalent column** (`engine/ladder/usd_marks.py`): each cell at the USD-per-unit rate for its own value date: spot on or before the spot date (as-of + 2 weekdays), otherwise the official `FWD_OUTRIGHT` for that exact date, else linear interpolation between the bracketing official outrights with spot as the first pillar, flat beyond the last tenor, and spot when a pair has no forward curve on file (named in a caption, never silent). Undiscounted; settled cash at spot. Its total is the book's FX value at outrights, which is not the headline P&L (that converts quote P&L at spot).
- View controls (currency multiselect, From / To value dates, "Settled dates one by one", "Show table in USD equivalent", CSV downloads, the heatmap, "Local vs USD by value date") shape the grid only.
- Stress block per `docs/BUILD_PLAN.md` section 4 (`config/stress.yaml`); futures delta as its own line.

**Blotter.** "Where did the P&L come from?" `value_book(as_of)` rows, one per trade, open or settled. Sub-tabs: Total book, FX, Futures, Rates, Options, Bundles, Manual entry.
- Total book: above the P&L-by-asset-class table, the **Positions** table, the key table of the Blotter (user, 2026-09-22: "I want to see the delta by currency, futures and rates dv01 in the blotter tab. this is the key table of the blotter", after "I want to see my total positions delta, dv01 options in the total tab in blotter ... my futures is not added up. SPX options - the detla should be added to the esz6 futures"; `engine/ladder/positions.py::book_positions`, rendered by `ui/tabs/blotter.py::positions_table`): first one row per currency with delta, the Ladder's own risk table (rate as quoted, an NDF currency at its 1M NDF ticker, local delta, USD delta, FX options' delta included, a metal row marked as not in the FX net, largest |USD delta| first, USD last), then FX net USD delta (the header's number, + = long USD) and gross; one **Equity index** line adding the ES futures and the SPX options, in index units (a future = contracts × 50, a listed option = contracts × DELTA × 100), in ES-contract equivalents (index units / 50) and in USD (futures at their own price, options at the index level), with each contract as a sub-line; Rates DV01 (USD, the day's official `DV01_USD` marks summed, by currency); FX options delta (USD) by pair. Every figure is the module that owns it (the Ladder's exposure path, `futures_delta`, the option delta records) summed, nothing recomputed; a missing mark leaves its line "n/a" with the reason on hover and out of the sum, never zero.
- FX: FX trades × `marks_official` (`FWD_OUTRIGHT` at the leg's own `settle_date`, `SPOT` for USD conversion); per-pair USD notional = Σ sign(base leg) × |USD leg|. Above the trade table sit two P&L-by-currency tables (user decision 2026-09-21): a fixed one for the whole FX book (LTD, Daily, Previous day, 5d, MTD, YTD per currency, its total equal to the strip) and one summed over the rows the trade table currently shows. Currency = the pair's non-USD currency; a cross is listed under its pair name. Sums follow the header's display rule. A trade the engine could not price shows "n/a" in its mark and P&L cells with its reason on hover, never a made-up figure (the illustrative "(sample)" cells of 2026-09-17 were removed on 2026-09-22, reviewer finding, user yes). Both tables show every currency at full length, with no scroll box of their own.
- Rates: IRS trades × `marks_official` (`PV_USD`, `DV01_USD`, `PAR_RATE`). `curves` is built by `engine/rates` (`bootstrap_and_store` / `price_and_store`; flat forwards, log-linear in the discount factor, by default since 2026-09-22, user decision "yes switch to flat forwards and rerun the past days", after the log-cubic bootstrap of USD SOFR failed to converge on the 22 and 23 September evaluation dates; a bootstrap that does not converge raises, never a silent curve or a fallback; `py 2_launcher.py reprice` re-runs the past days from the quotes on file), single-currency OIS only (Phase 1: USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON, CAD CORRA, AUD AONIA); term-rate, basis and XCCY swaps are Phase 2. The OIS curve is bootstrapped with flat overnight forwards (log-linear discount factors, `engine/rates/curves.py::DEFAULT_INTERPOLATION`; user decision 2026-09-22: "yes switch to flat forwards and rerun the past days", after the monotone log-cubic bootstrap of USD SOFR failed to converge on the 22 and 23 September evaluation dates whatever the quotes, which left every swap and every option needing the USD curve unpriced); never a per-day fallback to another interpolation, so the daily series is on one convention, and a curve that does not converge raises with its reason. `2_launcher.py reprice` re-prices the swaps (`engine/rates/store.py::recalc_on_file`) and then the options from the marks on file, day by day, asking Bloomberg nothing: that is how the past days were re-run and how the Bloomberg PC re-runs them after a pricer change.
- Options: not a top-level tab. A grouped, collapsible trade summary in the user's Bloomberg MARS-style layout: Portfolio Totals, then asset class, then structure / `package_id` (a multi-leg package collapses to one summary row with its legs nested underneath); columns Position / Notional / MktVal / MktPx / Delta / Theta / Gamma / Vega / Expiry / Underlying / UndFwdPx / Rho, with Side / Type / Payoff / Strike right after the label, ahead of them (user decision 2026-09-21: Strike, Type and Payoff are typed in the table on an option's own row, so they must be in sight without scrolling; the table's refresh is held while one of those cells is selected). Data: FX_OPTION trades × `marks_official`. The four tables above the trade summary (By pair, Expiry ladder, By structure, Spot against strike) are built from live options only: a trade the book values with status CLOSED (see "A closed-out option is not live") is left out before grouping, told by that status and never by its marks or a zero value (user, 2026-09-22: "I only want to see live options, no need show closed out options"); their Total line and Portfolio Totals still carry the closed-out group's P&L, and the trade summary still lists it, labelled "(closed out)", at its closing fill with no Greeks. Phased build-out in `engine/options/__init__.py`'s scope ledger and `docs/open-questions.md` item 61.
- Bundles: named groups of trades; membership is `instrument_theme` / `trades.theme`, metadata in `bundles`.

**Risk.** "How much can the book lose?" The risk metrics of the user's nm-dashboard project (its `fx_alpha` package: `core/risk.py` and the Portfolio tab of `dashboard.py`), computed here for this book (user, 2026-09-22: "on another project one level up, called nm-dashboard, we have lots of risk metrics, including blended vol, stress, 1y 95var etc. I want to create a new tab to see all these metrics, as a book, and as per underlyers"): `engine/risk/metrics.py::book_risk(conn, as_of)`, rendered by `ui/tabs/risk.py`, which recomputes nothing.
- Positions are the book's own, from `engine/ladder/positions.py::book_positions`: one underlyer per currency (USD delta, FX options' delta included), per metal, one for the equity index (the ES futures and SPX options line, in USD) and one per rates currency (DV01 in USD per bp). The engine takes no delta of its own and reads no mark.
- The return history is the nm-dashboard's Bloomberg cache, read from disk (`engine/risk/history.py`: `RISK_HISTORY_DIR`, else the freshest of `../nm-dashboard/fx_alpha/bbg_data`, `../nm-dashboard/bbg_data` and `../bbg_data/bbg_data` by the last close in its `bbg_raw_fx_marks.parquet`; the tab names the folder, its last close and the copies it saw): daily closes as USD per unit since 2000 (an NDF currency at its 1M NDF outright), deposit yields for carry, par swap rates. It serves the risk metrics only: nothing from it is written to `marks` or used for P&L or delta (hard rule 2 untouched), and it asks Bloomberg nothing (hard rule 8). With no folder found every figure is "n/a" with the reason.
- Definitions, the dashboard's exactly (`config/risk.yaml` holds the parameters, defaults equal to the dashboard's constants). Daily $ P&L per underlyer, the book held constant across the history: `usd_delta × (Δln close + carry)`, carry `(yield_ccy − yield_USD)[t−1] / 100 / 365`; rates `DV01 × 100 × Δ(par 10Y rate in %)` (+DV01 = payer, yields up → P&L up; MTM only, no accrual). `lag2` = the last close at or before as-of, less 2 business days. Blended annual vol = 2/3 × std of the last 500 daily P&Ls × √252 + 1/3 × std over 2008-01-01..2010-12-31 × √252 (trailing alone before 2011-01-01), on the series to `lag2`, n/a under 500 observations. 1y 95 % VaR = −(5th percentile of the last 252 daily P&Ls), positive = a typical bad day, n/a under 252. Worst day raw = the minimum daily P&L over all history. Worst day ex shocks = the minimum from 2008-01-01 to `lag2` with the shock dates zeroed (SNB 2015-01-15, Brexit 2016-06-24): the stress basis, against a cap of `stress_pct` (50 %) of the vol target. The book's figures are those functions of the summed series, so correlation is in them; per-underlyer figures are standalone. Net and Gross USD = the currency, metal and index rows' USD delta summed, and their absolute values summed; DV01 = the rates rows' sum.
- Limits: `vol_target_usd` (4.5m, the fund's vol allocation of `docs/open-questions.md` item 15; the dashboard's model book ran 60m) and the stress cap; the tab shows blended vol as % of target and the worst day ex shocks as % of cap, flagged when over. Scenario stress reuses `engine/pnl/stress.py` on the same delta, the Ladder's scenarios (`config/stress.yaml`), nothing new computed.
- Layout: a caption (as-of, history folder and dates, config), the book's cards, the per-underlyer table with the Book pinned last (rows by gross USD, rates last), the scenario table with its currency matrix, and a definitions block. No date picker of its own: it follows the header's as-of.

**Market data.** "Can I trust the numbers?" Organised by currency pair: spot and the forward curve with each mark's source and snap time, official first; "Pull now" (the same action as the top bar's "Pull Bloomberg now": one feed cycle, today's marks, the backfill and the marks snapshot; user 2026-09-22) and feed status; on a PC without a terminal that same press re-prices the options from the marks on file instead (user, 2026-09-22: "pull bbg now should recalc options too, using log data if no bbg access, or pull new data for new calculation"; `engine/options/store.py::recalc_on_file`, run by `live.pull_once` when no session opens: every day from the first option trade to the book date that has inputs on file, each from its own day's marks, then the book date at the pricing time; the status file's `recalc` block and `recalc_summary` sentence, shown on the button's status line and in the feed status; it asks Bloomberg nothing, hard rule 8); close completeness for the trailing business days; the Bloomberg library; the manual mark-entry form; the Bloomberg connection check.

### Delta per currency

Aggregate delta per currency across forwards, futures and option deltas in one query:

```sql
WITH d AS (
  SELECT l.ccy, l.amount AS delta
  FROM trade_legs l JOIN trades t USING (trade_id)
  WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP','FUTURE') AND l.settle_date > :as_of
  UNION ALL
  SELECT i.base_ccy, t.quantity * m.value
  FROM trades t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
  UNION ALL
  SELECT i.quote_ccy, -t.quantity * m.value * s.value
  FROM trades t JOIN instruments i USING (instrument_id)
  JOIN marks_official m ON m.instrument_id = t.instrument_id AND m.mark_type = 'DELTA' AND m.as_of_date = :as_of
  LEFT JOIN marks_official s ON s.instrument_id = i.base_ccy || i.quote_ccy AND s.mark_type = 'SPOT'  AND s.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
)
SELECT ccy, SUM(delta) AS delta FROM d GROUP BY ccy;
```

The `SPOT` join in the option quote-currency branch is a `LEFT JOIN` so that a missing spot mark surfaces as a `NULL` delta; the engine must raise if any resulting delta is `NULL`, never drop the leg silently. The join key is the **pair** (`i.base_ccy || i.quote_ccy`, e.g. `USDJPY`), not the option's own `instrument_id` (e.g. `USDJPY111926P-197571137`), which can never match a SPOT row. Because SQL `SUM()` drops individual NULL rows inside a `GROUP BY` group, the engine checks for options with a DELTA mark but no pair SPOT *before* aggregating (`engine/ladder/ladder.py::_OPTION_MISSING_SPOT_SQL`) rather than inspecting the summed output.

Per-pair delta (the "Position" table) is the same union grouped by `t.instrument_id` in USD-notional terms.

## P&L conventions

- **Sign**: `quantity > 0` = long base currency / receive; P&L is positive when the mark moves in favour of the position. Long USDXXX profits when the pair rises; long XXXUSD profits when the pair rises.
- **Display notional**: USD notional per pair, sign = direction of the base currency. Storage keeps exact leg amounts.
- **Source of truth**: fills (`trades` + `trade_legs`). Marks come from Bloomberg (`BFXFORWARD` / `BDH` / `BDP`) and from the app's own pricers on Bloomberg inputs.
- **Mark date**: each FX leg is marked with the `FWD_OUTRIGHT` for its own `settle_date` (not a single T+5 date); with none on file for that date, the near-marks estimate of hard rule 2 (along the day's curve, so an NDF or a metal with spot alone on file is marked at spot: user decisions 2026-09-21 and 2026-09-22, "xauusd has no fwd outright, this should be handled similar to the ndfs and settled cash"). One exception, NDF tickets only (user, 2026-09-22: "0 delta and 0 carry, they just disappears as they expired"; `engine/pnl/valuation.py::_ndf_fixed_on`): from its fixing date (value date less 2 business days, the Ladder's rule) an NDF is done. Its exit price is the currency's official fixing of that date (user, 2026-09-22: "we need the fix value from each date for the NDFs, thats the only correct way to compute the pnl. so the entry price is where we traded, and the exit price is the fix on that day, as pulled from bbg"): `PnL = Q × (FIX − f)`, `FIX` the pair's official `NDF_FIX` mark of the fixing date (`engine/pnl/valuation.py::ndf_fix`: that date's own fix and never another day's, whatever the near-marks rule could reach; with no fix for that date on file, the pair's SPOT of the fixing date, its own near-marks estimate, named as the substitute it is, until the fix lands: user decision 2026-09-22, after a day with no pull priced 21 NDF tickets fixing that day at fixings 5 and 8 days old and showed a Daily of −126,820 where LTD had not moved), converted to USD at the fix itself (user decision 2026-09-22: USD per quote unit = 1 / FIX for a USDXXX pair, so `PnL_USD = Q × (FIX − f) / FIX`, the way an NDF settles; never at the fixing date's spot), all of it spot P&L, no carry, and it does not move again; the row's spot is 1 / FIX with the fix's source, and the note opens "NDF fixed <d>:", says which price and that it is converted at the fixing. After the value date the ledger records the same figure (below).
- **USD conversion**: quote-currency P&L converts to USD at **spot** of the same `as_of_date`, never at the forward outright.
- **Per-trade LTD P&L (USD)**, with `Q` = base amount, `f` = fill, `m` = outright mark for the leg's value date, `S` = spot (quote→USD):
  - FX, any pair: `PnL_quote = Q × (m − f)`; `PnL_USD = PnL_quote × S` (`S = 1` when quote is USD). Crosses: `S` = USD per quote unit from that currency's own USD pair; never invent a USD leg.
  - Futures: `PnL_USD = contracts × multiplier × (m − f)`.
  - Listed index option (EQ_OPTION, user decision 2026-09-21: "Bloomberg's option price"): the futures formula, `PnL_USD = contracts × multiplier × (m − f)`, with `m` Bloomberg's own price of the option (official `FUTURE_PX` on the option's instrument: its mid on a live pull, else last, else the latest settlement price; `PX_SETTLE` on a past close) and `f` the fill, both in index points. No model enters the P&L. Frozen after expiry at the last official price on or before it, like a future. Its Greeks are `engine/options`' at the vol that price implies (index level, USD OIS curve and Bloomberg's dividend yield for the index); a missing input blanks the Greeks with its reason, never the P&L.
  - IRS: `PnL_USD = PV_USD(t) + CASHFLOW_USD(t)`, both official marks from `engine/rates` at the swap's maturity date. A swap dealt at its fixed rate with no upfront is worth zero at the fill by construction, so this is the mark-minus-fill analogue of the FX formula; `CASHFLOW_USD` is the net of coupons already settled on or before `t` (0 for a forward-starting swap), which keeps LTD continuous across a coupon payment and at maturity, when PV goes to 0. Both are computed in the swap's currency and converted at that day's SPOT by the pricer. Realised at maturity by `engine/pnl/ledger.realise_settled` at the last official PV + cashflows on or before maturity.
  - FX option: `PnL_USD = quantity × (PREMIUM_mark − premium_fill) × S`, premium in base-ccy fraction, `S` = USD per base unit at spot; realised at expiry at the last official PREMIUM on or before expiry (since 2026-09-18, user-approved, that PREMIUM is the expiry-day payoff: on the expiry date `engine/options` writes the payoff at the pair's official SPOT of that date — call max(S−K,0)/S, put max(K−S,0)/S, a BASE-payout digital 1 in the money else 0, nothing at S = K — following the official SPOT on file for that date until the first freeze; if the app did not run that day it is written on a later pull, dated the expiry date; the ledger freezes the trade the day after expiry; the payoff is taken at the day's official spot, not at the option's cut time. Digitals pay the BASE currency: USD on USDJPY, EUR on EURSEK, confirmed by the user). Before expiry a digital's PREMIUM is priced on the smile (user decision 2026-09-21: "yes, price off the smile"): a tight call / put spread, each leg at its own smile vol, so the slope of the smile is in the price; with no smile on file (an ATM-interpolated or a manual vol) it is the single-vol closed form.
- **Settled trades are frozen.** A trade whose last leg has settled takes its row from `realised_pnl` and is never marked again; settlement does not move LTD. The freeze is at the pair's last official SPOT on or before the settlement date; for an NDF pair, the official `NDF_FIX` dated its fixing date exactly (`realised_pnl.mark_type = 'NDF_FIX'`; never an earlier day's fix, user 2026-09-22), and only with no fix for that date on file the fixing date's SPOT exactly as the Blotter's near-marks estimate had it: the ledger reads both through `valuation.ndf_fix`, the one function every NDF exit price goes through, and a settled NDF the ledger has not frozen yet (every one after an upload, which clears `realised_pnl`) is shown by `valuation._frozen_row` at that same figure, never at its value date's spot (reviewer finding 2026-09-22), so the frozen figure is the one the Blotter showed from the fixing on; its note names the day used ("official fixing dated <d> (NDF fixing)"). The frozen NDF row's `spot_usd_per_local` is 1 / FIX and its `spot_source` the fix's (or the substitute's). **Re-frozen at the close** (user decision 2026-09-22): on every call `realise_settled` compares each frozen row with what the official marks on file now give for the same trade by the standard rule (`engine/pnl/ledger.py::purge_superseded`: mark type, mark date and every stored figure) and, where they differ, drops the row and freezes it again in the same call: a trade frozen at a live press once the backfill lands that day's 15:00 close, a future or listed option frozen at a live PX_LAST once that day's PX_SETTLE replaces it, an NDF frozen at a spot once the fix lands, a matured swap once its maturity PV is re-run, an option frozen at a PREMIUM once its close-out is recognised. A row whose inputs did not change is untouched and keeps its `frozen_at`, so settlement still does not move LTD except by this re-freeze; only rows whose value date is before the call's as-of are looked at, so the same call re-freezes them, and a row the rule cannot recompute today keeps its figure. The backfill's closing step is the ledger's plain `realise_settled(conn, today)` after the past closes land. The NDF "present spot" exception of 2026-09-21 is retired (user decision 2026-09-22): `realise_settled` takes no `ndf_present_spot` parameter; an NDF with no close on or before its fixing takes the near-marks estimate `valuation.ndf_fix` gives (a later close carried as it stands, named), with no close of its pair on file at all it is blank with its reason like a deliverable trade, and a row frozen under the retired rule on an existing database is re-frozen by the rule above.
- **A closed-out option is not live** (user, 2026-09-21: "for options closed out theyre not live"). FX option trades of the same option (pair, call / put, payoff, strike, barrier, averaging start, expiry, all on file; the export books the buy and the sell-back under two instrument ids, so never by id) whose quantities net to zero as of the date valued are status `CLOSED`: each is valued at the closing fill (quantity-weighted fill of the side opposite the first trade) instead of a `PREMIUM` mark, `quantity × (closing fill − fill) × S`, so the group needs no mark. The options pricer does not price them either (user, 2026-09-22: "we dont need to price all options, as some of them might be closed out already ... we just present the buy and sell price as pnl"): `engine/options/store.py`'s bulk passes (`price_all_and_store`, its expiry-day catch-up, `price_close`, `recalc_on_file`) leave every trade of a group closed out as of the date priced unpriced, by the same grouping rule imported from `engine/pnl/valuation.py`, old marks on file untouched, and report them under their own head (`PricingOutcome.closed_out`, the dicts' `closed_out` list / count, the pull status's "N closed-out options not priced"), never among the trades that could not be priced. On the close-out date the total is exactly what the marked formula gives, because the mark cancels over a flat position, and from then on it does not move: `S` is frozen too (user, 2026-09-21: "of course you freeze the usd converstion"), the official spot of the close-out date (the last trade's date) or the last one before it, named in the note, never a later one; after expiry the ledger records that same figure for every trade of the group (`realised_pnl.mark_type = 'CLOSE_OUT'`), and a row frozen any other way is frozen again once the close-out spot is on file. An option with a term missing (strike 0) is never matched, and a part sell-back stays live on its marks (`engine/pnl/valuation.py::closed_out_from_rows`).
- **Daily P&L** = `LTD(t) − LTD(t−1bd)`. **Trading P&L** = LTD of trades with `trade_date = t`. **5d P&L** = `LTD(t) − LTD(t−5bd)`. **MTD** = `LTD(t) − LTD(last bd of previous month)`. **YTD** = `LTD(t) − LTD(last bd of previous year)`. All from our own recomputed daily series, never summed day by day; `t−n bd` uses the trading calendar (`config/holidays.txt`). A reference close with no value steps back to the previous business day that has one (user decision 2026-09-21; see "Header"). Past closes are valued at historical Bloomberg marks written by the backfill; the live pull only writes today's.
- **Mark time**: official close is 15:00 `America/New_York` for every day of the book (user decision 2026-09-21: "the EOD is 3pm New York time", which replaced the 17:00 close of 2026-09-15 and at first applied from that day on; user decision 2026-09-22: "for fx use new 3pm" for all the previous closes too, so the LTD chart is on one convention). Bloomberg's daily `PX_LAST` is its 17:00 close, so a past FX close is read from Bloomberg's intraday bars at 15:00 New York and stamped 15:00, and a past-day FX row that carries any other stamp (that day's last live press, or a 17:00 daily close on a day the intraday history reaches) is not a close: the backfill asks for the 15:00 value and replaces it (`backfill.is_close_row`). Only a day that Bloomberg's intraday history (about 140 business days, `backfill.intraday_floor`) no longer reaches takes the daily close stamped 17:00. A future's past close is its `PX_SETTLE` (user, 2026-09-22: "for futures, can use market close"), stamped 17:00 New York (`backfill.settle_stamp`, Bloomberg's daily close); a past day's FUTURE_PX row a live press wrote (PX_LAST, or a listed option's PX_MID, stamped at the press) is not a close: the backfill asks that day's PX_SETTLE and replaces it, as it replaces an FX row that is not the 15:00 close (user decision 2026-09-22). The app resolves the offset from the zone per date; intraday = live. Every mark row carries `snapped_at` with the resolved offset for that row. **The book's day turns at 17:00 New York, 05:00 Hong Kong, for everything at once** (user, 2026-09-22: "I want to clarify the time today, so that all daily pnl is calculated from the NY 3pm the day before. I am based in HK, so basically all date rollover at hkt 5am"; `data/bloomberg/live.py::book_today`, `ROLLOVER_HOUR_NY = 17`): the date a pull stamps its marks with, the rates / vol / options steps, the ledger, the backfill's notion of a past day (`is_close_row`, `close_completeness`) and every screen's as-of. A pull at 18:00 New York on D writes D+1's marks stamped at the pull's real time, and the same press's backfill treats D as past, asks its 15:00 bars and replaces D's live rows, so Daily is always the live LTD against the previous day's 15:00 close. Until 2026-09-22 the marks kept the New York calendar date while the screen had rolled, so between 17:00 and midnight New York the screen valued a day with no marks off the previous day's last live press and Daily read 0. A manual trade's default date is still the New York calendar day (`cash_ladder.calendar_today_ny`), an open question.
- **Net USD** (FX only) = the USD position: Σ over pairs of sign × USD notional, sign +1 for USDXXX pairs (long base = long USD), −1 otherwise. The header shows this sign with the word "short USD" / "long USD" underneath. The engine's `portfolio_totals` returns the opposite quantity, the net non-USD delta (+ = long foreign); every view that displays Net USD (the header, the Ladder's headline and risk table) negates it exactly once, itself. **Gross USD** = Σ over pairs of |net USD notional per pair|. Gold and equity futures are reported separately (see open questions).
- **Must not replicate** (spreadsheet shortcuts the old Excel calculator used; they guard the live P&L path):
  1. Futures P&L computed as `Q × (m − f) / m`: understates by `f/m`. Always `contracts × multiplier × (m − f)`.
  2. Converting quote-currency P&L at the forward outright instead of spot (≈ 2.3 % error on TRY; also BRL, MXN, IDR).
  3. Marking every pair at one shared date regardless of each leg's own value date, and marking matured trades forever instead of freezing settled trades.
  4. Hard-coded ranges or cell references in place of a real query over `trades` / `trade_legs` / `marks_official`.

## Repository layout and ownership

Each directory has one owning agent, and inside `ui/` each tab has its own (user decision 2026-09-22: the feature pairs below). The session coordinates and writes no application code; every agent stays inside its own files, and so does every parallel session (see "Working mode").

```
data/ingest/      blotter parser, upload, manual entry, SQLite schema,   -> data-ingest
                  swap package rule
data/bloomberg/   blpapi pulls (live, backfill), marks, inventory,       -> bbg-data
                  manual marks, diagnostics
engine/pnl/       value_book, realised ledger, LTD / daily / 5d / MTD /  -> pnl-engine
                  YTD, stress
engine/ladder/    cash ladder, settled cash, USD equivalents, delta      -> cash-ladder
                  per currency and per pair
engine/rates/     OIS curve bootstrap + swap valuation (QuantLib)        -> rates-pricer
engine/options/   FX / equity / commodity option pricing, vendored       -> options-pricer
                  options_calc (QuantLib); phase status in
                  engine/options/__init__.py's scope ledger
engine/rates_vol/ swaptions, caps / floors, SABR, Bermudan (vendored     -> rates-exotics
                  options_calc.rates)
engine/risk/      the Risk tab's metrics (blended vol, VaR, worst days,     -> risk-metrics
                  scenario pass-through) and the nm-dashboard history reader
ui/               Dash app: the shell (app, launch, refresh signal,      -> ui-shell for the shell,
                  upload, shared controls, the shared pricing reader)      one UI agent per tab
                  and one module per tab or sub-tab, owned tab by tab      (feature pairs below)
tests/            pytest, one file per module, owned by the module's agent;
                  tests/conftest.py (shared fixtures), tests/golden/ and the
                  golden-book and health tests are infra's
config/           holidays.txt (trading calendar), stress.yaml               -> infra
                  (risk.yaml is risk-metrics')
tools/            Bloomberg diagnostics and terminal probe, setup script     -> infra
1_setup.cmd, 2_launcher.py, 3_diagnostic.py, .gitignore, .gitattributes,     -> infra (clean-up / infra:
                  pyproject.toml, .claude/settings.json: the root scripts
                  and ignore rules (hard rule 9); mechanical, behaviour-         also the mechanical
                  neutral clean-up anywhere, see "Working mode"                  clean-up anywhere)
docs/             contract, build plan and open questions, owned by housekeeper
```

**Feature pairs (user decision 2026-09-22).** Each feature has a function agent and a UI agent. The function agent goes first; the UI agent reads its output and never recomputes it. Inside `ui/` ownership is by file, since every tab lives in `ui/tabs/`.

| Feature | Function agent | UI agent | UI files | UI tests |
|---|---|---|---|---|
| Header strip (above every tab) | pnl-engine | ui-header | `ui/tabs/header.py` | `tests/test_header.py` |
| Ladder | cash-ladder | ui-ladder | `ui/tabs/exposure.py`, `ui/tabs/cash_ladder.py` | `tests/test_ui_ladder.py`, `tests/test_ui_ladder_view.py` |
| Blotter: Total book, FX, Futures, Bundles, Manual entry | pnl-engine, data-ingest | ui-blotter | `ui/tabs/blotter.py`, `ui/tabs/blotter_fx.py`, `ui/tabs/blotter_bundles.py`, `ui/tabs/manual_entry.py` | `tests/test_ui_blotter.py`, `tests/test_ui_manual_entry.py` |
| Blotter: Rates sub-tab | rates-pricer (rates-exotics once swaptions have an ingest path) | ui-rates | `ui/tabs/rates.py` | `tests/test_ui_rates.py` |
| Blotter: Options sub-tab | options-pricer | ui-options | `ui/tabs/options.py` | `tests/test_ui_options.py` |
| Market data | bbg-data (bbg-diagnostics for the connection check) | ui-market-data | `ui/tabs/market_data.py`, `ui/feed_controls.py` | `tests/test_ui_market_data.py` |
| Risk | risk-metrics (`engine/risk/`, `tests/test_risk.py`, `config/risk.yaml`) | ui-risk | `ui/tabs/risk.py` | `tests/test_ui_risk.py` |
| Shell: app assembly, launch, refresh signal, upload, shared controls and formatting, the shared pricing reader | none | ui-shell | `ui/app.py`, `ui/launch.py`, `ui/revision.py`, `ui/uploads.py`, `ui/__init__.py`, `ui/tabs/__init__.py`, `ui/tabs/controls.py`, `ui/tabs/formatting.py`, `ui/tabs/blotter_pricing.py`, `ui/assets/` | `tests/test_ui.py`, `tests/test_app.py`, `tests/test_ui_revision.py`, `tests/test_uploads.py` |

`ui/tabs/blotter_pricing.py` is the reader every screen shares (`priced_value_book`), so it belongs to the shell, not the Blotter. A test file not listed here belongs to the module it tests (`tests/test_exposure.py` and `tests/test_ladder.py` are cash-ladder's). Each agent's memory lives in `.claude/agent-memory/<agent>/`; the six UI agents were split out of ui-shell on 2026-09-22 and took its per-tab notes with them.

`engine/rates_vol/` writes `PV_USD` / `DV01_USD` under `QL_PRICER` and `VEGA` / `GAMMA` / `THETA` under `QL_OPTIONS_PRICER` (both already official for those mark types). No trade source carries swaptions or caps yet (`docs/open-questions.md` item 61).

## Guard rails learned the hard way

- SQLite stays in `journal_mode=delete`. Do not switch to WAL as a quick fix: in WAL the main file's mtime stops changing on write, which silently breaks every mtime-keyed cache (`ui/revision.py`, the header's LTD cache, the Blotter's pricing cache) and serves stale P&L.
- Writers wait up to 60 s for the lock (`schema.BUSY_TIMEOUT_SECONDS`): an upload holds it for its whole parse and load, and a pull landing meanwhile must not be recorded as a Bloomberg failure.
- Diagnostics pasted by the user come from the Bloomberg PC. The dev `risk.db` has no marks, so "fast" or "blank" locally says nothing about the live app; trace reasons through the code.
- The "Last marks pull" diagnostic fails when the last pull requested 0 marks but the book needs some (a pull pressed before any blotter is uploaded).
