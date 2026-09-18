# risk-monitor

## User-authorised direction, 2026-09-15 (supersedes the 2026-09-14 correction)

`docs/BUILD_PLAN.md` is the build specification. The app's headline P&L is the per-trade valuation in its section 2 (each FX leg marked at the outright for its own value date, quote P&L converted at spot, futures as contracts × multiplier × price change, settled trades frozen), the close series and periods in section 3, and the four tabs in section 5. The "P&L conventions" section below is in force again and the "Must not replicate" list applies to the headline.

The literal workbook arithmetic (`engine/pnl/pnl.py`) and the Excel workbook itself (`data/raw/HA-portfolio vJean.xlsx`) it replicated were deleted outright 2026-09-17 (user decision, "no bnp fall back - that excel and everything linked to it need to go" — `docs/bnp-excel-removal.md`), along with the BNP CSV parser, its P&L/reconciliation modules and everything downstream of them that had no live successor. This was never a partial deprecation: the Reconciliation tab that displayed this arithmetic was already removed 2026-09-16, and by 2026-09-17 nothing in the app could reach it at all, so it was deleted rather than left dormant. See "Trade-source history" below for what that removal actually touched. Missing values stay missing everywhere.

The cash ladder is a pure delta table: leg by leg, crosses included, spot for delta, no P&L on it (engine change landed 2026-09-15).

FX and futures risk monitor: cash ladder (delta exposure + cashflow timing + scenario analysis — not a cash-balance ledger, see below), blotter (all trades, P&L, Greeks), delta per currency. Python; Dash front end later. Fund NMMF, base currency USD. Reference input: `data/raw/new_sample_trades.csv` (transaction-level blotter export — see "Blotter → tables"; the app's **only** trade source, parsed by `data/ingest/blotter.py`). `data/raw/HA_PNL_20260818.csv` (BNP position/P&L snapshot) and `data/raw/HA-portfolio vJean.xlsx` (the Excel calculator this app replaced) are historical inputs only — see "Trade-source history" below. Open items live in `docs/open-questions.md`, not here.

**Trade-source history:** four stages. (1) Until 2026-09-16, `HA_PNL_*.csv` was "the trade source" and futures fills came from the xlsx workbook's `All FX trades` sheet. (2) 2026-09-16: both superseded by the blotter (`data/ingest/blotter.py`), which carries a genuine per-trade fill price and trade ID for every product including futures; BNP was kept alongside it for its `positions` cash-balance snapshot and `BNP_BVAL` reconciliation marks, with an EOD blotter-vs-BNP check (`engine/pnl/reconcile.py`) added the same day. (3) 2026-09-17 (morning): the app's upload control was narrowed to the blotter only; BNP upload code was deleted from `data/ingest/upload.py`, but `data/ingest/bnp.py` and `data/bloomberg/bnp_marks.py` were kept as libraries (still used by `data/load.py`'s CLI), since other agents were mid-edit on files that imported shared symbols from them. (4) **2026-09-17 (afternoon, current, user decision) — BNP and the Excel workbook removed entirely, not kept as dead code** ("no bnp fall back - that excel and everything linked to it need to go"). The user confirmed directly that the cash ladder he actually wants is delta exposure and cashflow timing (already fully derivable from the blotter's trade legs alone, no balance needed) plus scenario analysis, not a literal bank-balance figure — so BNP's one remaining job (the `positions` cash balance) was never needed by anything in the app once the blotter existed. Completed: `data/ingest/bnp.py`, `data/ingest/irs.py`, `data/bloomberg/bnp_marks.py`, `data/load.py`, `engine/pnl/pnl.py`, `engine/pnl/reconcile.py`, `engine/ladder/valuation.py`, `ui/workbook_rates.py`, `data/ingest/workbook_rates.py` all deleted outright; the shared dataclasses/regexes/helpers the live blotter parser needs moved to a new `data/ingest/common.py` first, so `data/ingest/blotter.py` (the app's only trade source) has no dependency on any of it. The `positions` table is dropped from the schema, and `data/ingest/schema.py::purge_retired_sources` (run once per app startup) cleans up any of this an existing database still has on disk. `data/raw/HA-portfolio vJean.xlsx` and `data/raw/HA_PNL_*.csv` were never git-tracked (`data/raw/` is gitignored) so there was nothing to remove from git; they remain on disk as untouched historical reference material only. The blotter parser and upload path remain the app's only trade source, tolerant of format variation (BOM-prefixed files, mixed-case/whitespace-varied headers, one malformed row no longer blocking a whole file) per the same "make it as flexible as possible" instruction. This resolves `docs/open-questions.md`'s item 55 trade-identity clash by removing one side of it entirely (see `docs/open-questions.md` item 69 and `docs/bnp-excel-removal.md` for the full list of what was moved versus deleted).

## Working mode

Default: lean. Work directly in this session on the model set by `/model`. Do not spawn the housekeeper, specialists or reviewer unless the user's message explicitly names them. Keep each task to one module and one test file. End every task with `py -3 -m pytest tests/ -q` and report the pass count. Full agent pipeline is reserved for changes to P&L arithmetic in `engine/` and is invoked only by the user.

## Data contract

### Tables

All dates ISO `YYYY-MM-DD`, all amounts signed (`+` = receive / long). No column is nullable; "not applicable" uses the documented sentinel.

```sql
instruments (
  instrument_id   TEXT PRIMARY KEY,   -- 'USDJPY', 'EURSEK', 'XAUUSD', 'ESU6 Index', 'IRSOIS-USD-22860996',
                                      -- 'USDJPY digi p 152 2026-08-26'
  asset_class     TEXT NOT NULL,      -- FX | FUTURE | IRS | FX_OPTION | IRS_OPTION | EQ_OPTION | CMDTY_OPTION | CASH
  base_ccy        TEXT NOT NULL,      -- unit of trades.quantity: 'USD' for USDJPY, 'AUD' for AUDUSD, 'XAU',
                                      -- 'ES' (index units), notional ccy for IRS, base of pair for options
  quote_ccy       TEXT NOT NULL,      -- currency of trades.price and of local P&L
  multiplier      REAL NOT NULL,      -- 1 for FX / IRS / options; 50 for ES
  is_ndf          INTEGER NOT NULL,   -- 1 = non-deliverable: local legs do not settle
  bbg_ticker      TEXT NOT NULL,      -- 'USDJPY Curncy', 'ESU6 Index', ...
  expiry_date     TEXT NOT NULL       -- '9999-12-31' for perpetual (FX pairs, cash)
);

trades (
  trade_id        TEXT PRIMARY KEY,   -- BNP file's Symbol-derived id ('196789440'), blotter 'Trade Id' column
                                      -- (different numbering scheme from the BNP one, see "BNP file -> tables"),
                                      -- 'XL-<row>' from the xlsx workbook, or app-generated
  source          TEXT NOT NULL,      -- BNP | XLSX (both the blotter and the xlsx-workbook futures loader use
                                      -- this literal value; see "Blotter -> tables") | MANUAL
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  product         TEXT NOT NULL,      -- FX_SPOT | FX_FWD | FX_SWAP | FUTURE | IRS | FX_OPTION
                                      -- | SWAPTION | CAP_FLOOR (engine/rates_vol; no ingest path yet, 2026-09-17)
                                      -- | EQ_OPTION | CMDTY_OPTION (engine/options; no ingest path yet, 2026-09-17)
  package_id      TEXT NOT NULL,      -- = trade_id unless grouped by the swap rule below
  trade_date      TEXT NOT NULL,
  quantity        REAL NOT NULL,      -- signed, in base_ccy units: base amount (FX), contracts (FUTURE),
                                      -- notional (IRS: + = pay fixed), notional (FX_OPTION: + = long)
  price           REAL NOT NULL,      -- fill: forward outright / futures price / fixed rate / premium per unit
  account         TEXT NOT NULL,      -- 'BNPP-IPBFX-NMMF', ...
  counterparty    TEXT NOT NULL,
  strategy        TEXT NOT NULL,      -- 'HAHY7' | 'HACA'
  trader          TEXT NOT NULL,
  description     TEXT NOT NULL       -- raw PB description or free text
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
  mark_type       TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX | PAR_RATE | PV_USD | DV01_USD | CASHFLOW_USD | PREMIUM | DELTA
                                      -- | GAMMA | THETA | VEGA | RHO (option Greeks, engine/options, 2026-09-17)
                                      -- | DELTA_PA (premium-adjusted delta, written only for G10 pairs quoted that way;
                                      --   the ladder reads DELTA, never DELTA_PA)
  value           REAL NOT NULL,      -- DELTA = base-ccy delta per 1 unit of trades.quantity (may exceed 1 for digitals)
  source          TEXT NOT NULL,      -- BNP_BVAL | BBG_BFXFORWARD | BBG_BDH | BBG_BDP | BBG_INTERP | MANUAL
                                      -- BBG_INTERP = linear interpolation between the two bracketing standard
                                      -- tenors of Bloomberg's own forward curve (broken dates); official only
                                      -- as the FWD_OUTRIGHT fallback (user decision 2026-09-18), never for SPOT
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

curve_quotes (                         -- raw OIS quote staging (data-ingest DDL, bbg-data writer, engine/rates reader)
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
```

`positions` (one row per PB position per day, BNP grain) was dropped from the schema 2026-09-17: it was written only by the retired BNP CSV parser, and the app's only trade source (the blotter) never had an equivalent snapshot to write there. `data/ingest/schema.py::purge_retired_sources` drops the table outright on any existing database that still has it from before this change.

Leg layouts: FX spot/forward = 2 legs (`FX_NEAR`, one per currency); FX swap = 4 legs (`FX_NEAR` × 2, `FX_FAR` × 2) under one `trade_id`; FUTURE = 1 `NOTIONAL` leg in USD, amount `contracts × multiplier × fill`, `settle_date` = expiry, `settles_cash` 0; IRS = `FIXED` leg (amount = `−quantity`, rate = fixed) + `FLOAT` leg (amount = `+quantity`, rate = spread), `settle_date` = maturity — a payer (`quantity > 0`) has a negative FIXED leg (pays) and a positive FLOAT leg (receives); FX_OPTION = 1 `NOTIONAL` leg in base ccy, `settles_cash` 0.

### Official marks

`marks.source` is part of the primary key, so joining `marks` directly returns one row per source and double-counts as soon as two sources exist for the same date. Exactly one source is official per `mark_type`:

| mark_type | official source |
|---|---|
| SPOT | BBG_BFXFORWARD |
| FWD_OUTRIGHT | BBG_BFXFORWARD where Bloomberg quotes that exact date (a standard tenor of `FWD_CURVE`); otherwise BBG_INTERP (user decision 2026-09-18): linear interpolation between the two bracketing standard-tenor outrights of Bloomberg's own curve, never extrapolated beyond the last tenor, what the Excel `BFXForward` call did for broken dates. A direct quote always wins over an interpolated row for the same key |
| FUTURE_PX | BBG_BDH |
| PAR_RATE, PV_USD, DV01_USD, CASHFLOW_USD | QL_PRICER |
| DELTA, DELTA_PA, PREMIUM, GAMMA, THETA, VEGA, RHO | QL_OPTIONS_PRICER (`engine/options`, vendored options_calc; since 2026-09-17 — MANUAL is reconciliation-only for these) |
| any | BNP_BVAL, if it remains in an old database, is never official — BNP is no longer a live input at all (removed 2026-09-17, `docs/bnp-excel-removal.md`); nothing writes a BNP_BVAL row any more |
| SPOT, FUTURE_PX, any other | BBG_INTERP is reconciliation-only everywhere except its one official role above (the FWD_OUTRIGHT fallback); a SPOT or FUTURE_PX is never interpolated into existence |
| PAR_RATE, PV_USD, DV01_USD | BBG_BDH (Bloomberg SWPM) is reconciliation only, never official, mirroring BNP_BVAL for FX (decided 2026-09-15 with the `engine/rates` QuantLib OIS pricer landing) |

The mapping is held in a `marks_official` view (`marks` filtered to the official source per `mark_type`, plus the FWD_OUTRIGHT fallback row only where no direct-quote row exists for the same key — `data/ingest/schema.py::OFFICIAL_MARK_SOURCE` and `OFFICIAL_FALLBACK_SOURCE` — so `(as_of_date, instrument_id, settle_date, mark_type)` is unique). Every P&L or delta query reads from `marks_official`, never from `marks` directly.

**Standard-tenor forward points are official (2026-09-18).** The live pull (`data/bloomberg/live.py`) requests Bloomberg's bulk `FWD_CURVE` table once per pair — every pair with an open FX leg and every open FX option's underlying pair — and writes each standard-tenor row as an official `BBG_BFXFORWARD` `FWD_OUTRIGHT` at that tenor's own settle date, because each row is Bloomberg's own outright quote, not an interpolation. A leg or option expiry that falls between two tenors is written as `BBG_INTERP` (the API exposes no direct broken-date outright, `docs/open-questions.md` item 27), which since the user's decision of 2026-09-18 is official for `FWD_OUTRIGHT` wherever no direct quote exists for that key (table above): that is how a broken-date leg gets its P&L at all. P&L reads forwards by exact leg date, so the tenor rows themselves change nothing there. They exist so that `engine/options/rates.py`'s covered-interest-parity fallback has official points to interpolate between when a currency has no OIS curve (SEK, NOK, TWD, ZAR): before this, a pair whose open dates were all broken dates had no official forward at all and its options were skipped as `no curve/rate <CCY>`. The pull reports these rows under `curve_points_written`, separately from the requested-mark count.

### Blotter → tables

`data/ingest/blotter.py` parses the transaction-level blotter export (`data/raw/new_sample_trades.csv`-shaped files) — one row per fill, unlike the BNP snapshot's one netted row per open position per day. This is the current source of `trades` / `trade_legs` for FX forward/spot, futures, options and IRS: it carries a genuine per-fill `Price` and `Trade Id` for every row, including futures, which the BNP file never does (see "BNP file → tables" below).

Scope: a row is excluded only when its `Status` says cancelled/rejected/pending/void/deleted/failed, or its `Fund` is populated and is not `NMMF` (a missing column or blank cell never excludes). Row kind is decided by `Fin Type`, matched by keyword after normalisation (`Futures`, `FX Forward`, `Interest Rate Swap` all resolve), with `Product` as the fallback when `Fin Type` is blank or unrecognised: `FORWARD | CURRENCY | FUTURE | OPTION | INTEREST_RATE_SWAP`; anything else is counted and skipped, never coerced. Tolerance rule (2026-09-17): a blank, missing or oddly formatted field never rejects a row when the value can be recovered from another column (forwards fall back from the Description to `TradeDate` / `Settle Date` / `Buy Currency` / `Sell Currency` / `Price`; IRS to Description / `Currency` / `Quantity`×1e6; options to `Currency Pair` / `Adj. Expiry Date` / `FxOption Type`); only a contradiction between two populated fields does. A repeated `Trade Id` within a file keeps the highest `Version`; re-uploading replaces trades by id (`blotter.load` is idempotent, and dissolves any swap package containing a replaced trade so the package rule re-runs). File reading (`blotter.read_table`) accepts UTF-8/BOM/cp1252, comma/semicolon/tab/pipe delimiters, a header row after preamble lines, any header casing, and multi-sheet workbooks with real Excel date cells.

- FORWARD: same `Symbol` (`<PAIR><VD mmddyy>-<id>`) and `Description` regexes as the BNP file (byte-identical on the reference sample); the trailing id in `Symbol` here is `Instrument Id`, not `Trade Id` — `trades.trade_id` comes from the `Trade Id` column, a different numbering scheme from BNP's Symbol-derived id (see the trade-identity note above). Base/quote leg amounts come from the structured `Buy Currency` / `Sell Currency` / `BuyCurrency Amount` / `SellCurrency Amount` columns, cross-checked against the description's sold/bought currencies.
- CURRENCY: **spot FX fills (user's cash-ladder spec, 2026-09-18).** A row naming both a `Buy Currency` and a `Sell Currency` is a spot trade in its own right (every one of the reference sample's 85 CURRENCY rows: T+1/T+2, its own `Trade Id`, both amounts and a `Price`, none matching any FORWARD row) and is written as a trade with product `FX_SPOT` plus the same two `FX_NEAR` legs a forward gets, dated on `Settle Date`, so its cash reaches the ladder (a settled CAD balance stays CAD until a spot trade in the file converts it) and its P&L the book. The `CASH-<ccy>` instrument is still written for the row's own currency; a single-currency row (fee, balance, one-sided movement) stays instrument-only, never a trade, never a reject. Spot trades are not candidates for the FX-swap package rule (forwards only, below). Before 2026-09-18 these rows were dropped from the book entirely.
- FUTURE: unlike the BNP FUTURES row (netted position, no fill date/price), this file gives `Trade Id` and a real per-contract fill `Price`, so a trade + 1 `NOTIONAL` leg is written (`Quantity` = contracts signed by `Side`, `multiplier` = 50 for ES).
- OPTION: product `FX_OPTION`, 1 `NOTIONAL` leg in the pair's base currency (`Currency Pair` column), quantity signed by `Side` (Buy = long = +), price = premium fill.
- INTEREST_RATE_SWAP: + = pay fixed, − = receive fixed, not `Side` (always `'Buy'` in the reference sample, carries no direction here). **A short is whatever the book marks with brackets or a minus sign (user, 2026-09-18, "if the book has brackets or a negative sign that's a short on the instrument"): on `Notional`, or on `Quantity` where an export leaves `Notional` unsigned — a negative on either column is a short (receive fixed); the magnitude still comes from `Notional`.** The reference sample carries no sign on either column even for the three swaps the old Excel book held as receivers (−625M, −995M, −158.22M), so it cannot show this; the user's live export does. `Notional` is already full-unit (not millions-scaled like this file's own `Quantity` column) — same scale as `trades.quantity` elsewhere. Leg shape mirrors `data/ingest/irs.py`'s BNP path (FIXED = `−quantity`, FLOAT = `+quantity`).

`trades.source = 'XLSX'` for every blotter-sourced trade (the schema's `source` column is free text, not a checked enum; the blotter reuses the same literal `'XLSX'` value the xlsx-workbook futures loader below uses for `trade_id='XL-<row>'` rows — a naming overlap between two different files, tracked in `docs/open-questions.md` rather than resolved here). `trades.strategy` is `''` (no equivalent column in this file).

### BNP file → tables (removed 2026-09-17)

The BNP daily PB snapshot and its parser (`data/ingest/bnp.py`, `data/ingest/irs.py`'s BNP row handler, `data/bloomberg/bnp_marks.py`, `data/load.py`'s CLI) are deleted entirely per user decision ("no bnp fall back — that excel and everything linked to it need to go", `docs/bnp-excel-removal.md`), not kept as historical documentation or dead code. `data/raw/HA_PNL_*.csv` was never git-tracked and remains on disk as untouched historical reference material only — it is not read by anything in the app any more, at any layer. The shared dataclasses/regexes/helpers the live blotter parser (`data/ingest/blotter.py`) actually needs (`Instrument`, `Trade`, `TradeLeg`, `Reject`, the forward/IRS symbol and description regexes, `NDF_CCYS`, `future_expiry`, `FUTURE_MULTIPLIERS`, `cash_ccy`) were moved into `data/ingest/common.py` first, so the live parser has no dependency on anything BNP-specific. `BNP_BVAL` is no longer written anywhere (see "Official marks" below); the `positions` table this file's rows used to populate is dropped from the schema entirely. What this section used to document (the `Symbol`/`Symbol Description` regex shapes, the FORWARD/CURRENCY/FUTURES/INTEREST_RATE_SWAP row formats, the reconciliation tolerances BNP's own arithmetic was checked against) is preserved only in git history and in `docs/bnp-excel-removal.md`, not here.

### xlsx → tables (removed 2026-09-17)

`data/raw/HA-portfolio vJean.xlsx` (the Excel calculator), its workbook-arithmetic module (`engine/pnl/pnl.py`) and the Reconciliation tab that once displayed it are removed entirely per the same user decision. `data/raw/HA-portfolio vJean.xlsx` was never git-tracked and remains on disk as untouched historical reference material only. The xlsx workbook's futures-fill loader (`data/ingest/xlsx_futures.py`) was already deleted 2026-09-16, superseded by the blotter's per-trade futures fills; `engine/pnl/pnl.py` (the remaining literal workbook row arithmetic — `ltd_per_trade`, `workbook_valuation_date`, `workbook_fx_pnl`) and its aggregation layer (`engine/pnl/aggregate.py`'s former `aggregate_by_pair`/`book_totals`/`period_pnl`) followed 2026-09-17. What this section used to document (the `All FX trades`/`All IRS trades`/`All Options Trades` sheet layouts and cell formulas) is preserved only in git history, not here.

### `package_id` rule (FX swaps)

Two forward rows form one `FX_SWAP` package when all hold: same source, same account, same pair, same trade date, opposite-signed quantities, equal |USD-leg amount| within 0.01 % (for a cross with no USD leg, equal |base amount| within the same tolerance), **different** value dates. `package_id = 'SWAP-' || min(trade_id)`; near leg = earlier value date. Opposite-signed rows with the same value date are intraday round trips and stay separate outrights. Groups with more than one candidate on a side are not auto-grouped; they go to a review list. A swap's near leg settles T+2 and drops out of the PB snapshot, so swaps are identified from the blotter or by diffing the daily PB archive, never from one snapshot.

### Reconciliation checks and tolerances (removed 2026-09-17)

These tolerances applied to the retired BNP file's own internal arithmetic (`Local Cost`, `MV Local`, `MV Base`, DTD/MTD identities) and to netting the retired Excel workbook against it. Neither input exists in the app any more; nothing reads these checks. Kept only in `docs/bnp-excel-removal.md`'s history, not here.

### Six tabs as views

| Tab | View |
|---|---|
| Cash ladder | `trade_legs` where `settles_cash = 1 AND settle_date ≥ as_of`, grouped by `ccy, settle_date` — cashflow timing and delta exposure only; no cash-balance rows (the `positions`-based `CASH` column was BNP-fed; both it and the `positions` table were removed 2026-09-17 along with BNP itself, `docs/bnp-excel-removal.md`). The `≥` here versus `>` in the delta query is intentional: a leg settling on `as_of` is cash that moves today but carries no delta by close. **Settled cash row (user decision 2026-09-18, "there should be a settled cash row toward the top", "expired tickets must settle not disappear"):** the Ladder tab's grid (`engine/ladder/exposure_adapter.py::settled_records_from_db`) adds one row above the value dates holding, per currency, the legs of every ticket in the uploaded blotter whose value date has passed: deliverable legs (`settles_cash = 1`, `settle_date < as_of`) at face value in their own currency, which still carry that currency's delta (NOK received on a settled forward is NOK exposure until sold), and for non-deliverable tickets (NDF pairs, futures, FX options) the USD settlement read from `realised_pnl` (the ledger's realised P&L, which for an NDF is exactly the USD cash settlement) — never recomputed, and a settled ticket the ledger has not realised yet is named under the grid, not valued. The tab's per-currency delta therefore includes settled deliverable cash (deliverable legs settling on `as_of` count as cash by close; NDF legs settling on `as_of` still carry no delta); the SQL delta query below and the per-pair Position table remain open-forward-only. This is settled cash from the tickets on file, not a bank balance. **USD equivalent and view controls (user's cash-ladder spec, 2026-09-18, `docs/open-questions.md` item 70):** the grid's `USD equivalent` column marks each cell at the USD-per-unit rate for its own value date (`engine/ladder/usd_marks.py`): spot on or before the spot date (as-of + 2 weekdays), otherwise the official `FWD_OUTRIGHT` for that exact date, else linear interpolation between the bracketing official outrights with spot as the first pillar, flat beyond the last tenor, and spot when a pair has no forward curve on file (named in a caption, never silent); undiscounted; settled cash at spot. The column's total is the book's FX value at outrights, which is not the headline P&L (that converts quote P&L at spot). The per-currency delta rows stay at spot. The tab's controls (currency multiselect, From/To value dates, "Settled dates one by one", "Show table in USD equivalent", ladder/legs CSV downloads, the heatmap coloured by USD equivalent and the collapsed "Local vs USD by value date" table) shape the grid only; the headline card and the risk table are always the whole book. |
| FX | FX trades × `marks_official` (`FWD_OUTRIGHT` at the leg's own `settle_date`, `SPOT` for USD conversion); per-pair USD notional = Σ sign(base leg) × \|USD leg\| |
| Rates | IRS trades × `marks_official` (`PV_USD`, `DV01_USD`, `PAR_RATE`, official source `QL_PRICER`); `curves` is live via `engine/rates` (`bootstrap_and_store` / `price_and_store`), single-currency OIS only (Phase 1: USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON, CAD CORRA, AUD AONIA) — term-rate, basis and XCCY swaps are Phase 2 |
| Options | FX_OPTION trades × `marks_official` (`PREMIUM`, `DELTA`) |
| Delta | query below |
| Overall book | P&L rollups by strategy / account, LTD series from `value_book` (the `positions` BNP-vs-CALC comparison no longer applies: the `positions` table itself was dropped 2026-09-17 along with BNP) |

**Options tab placement, decided 2026-09-17 (resolves the standing conflict with `ui/app.py`'s docstring, which claimed BUILD_PLAN's 3/4-tab structure superseded this table — both now agree):** Options is not a standalone top-level tab. It lives inside the Blotter, as a grouped, collapsible trade summary — Portfolio Totals roll-up, then grouped by asset class, then by structure/`package_id` (a multi-leg `combine()`-built package collapses to one summary row with its legs nested underneath), columns Position / Notional / MktVal / MktPx / Delta / Theta / Gamma / Vega / Expiry / Underlying / Strike / UndFwdPx / Rho — per the user's Bloomberg MARS-style reference layout. The row above ("Options | FX_OPTION trades × marks_official") still describes the correct *data* view; only its placement in the UI is corrected here. See `engine/options/__init__.py`'s scope ledger and `docs/open-questions.md` item 61 for the phased build-out (options-pricer, ui-shell Phase 8).

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

The `SPOT` join in the option quote-currency branch is a `LEFT JOIN` so that a missing spot mark surfaces as a `NULL` delta; the engine must raise if any resulting delta is `NULL`, never drop the leg silently. The join key is the **pair** (`i.base_ccy || i.quote_ccy`, e.g. `USDJPY`), not the option's own `instrument_id` (the blotter Symbol, e.g. `USDJPY111926P-197571137`), which can never match a SPOT row — corrected 2026-09-17 (options merge Phase 3) when `engine/ladder/ladder.py` was brought in line with this query. Because SQL `SUM()` drops individual NULL rows inside a `GROUP BY` group, the engine checks for options with a DELTA mark but no pair SPOT *before* aggregating (`_OPTION_MISSING_SPOT_SQL`) rather than inspecting the summed output.

Per-pair delta (the sheet's "Position") is the same union grouped by `t.instrument_id` in USD-notional terms.

## P&L conventions

- **Sign**: `quantity > 0` = long base currency / receive; P&L is positive when the mark moves in favour of the position. Long USDXXX profits when the pair rises; long XXXUSD profits when the pair rises.
- **Display notional**: USD notional per pair, sign = direction of the base currency (the xlsx convention). Storage keeps exact leg amounts.
- **Source of truth**: fills (`trades` + `trade_legs`). Marks come from Bloomberg (`BFXFORWARD` / `BDH` / `BDP`). The BNP daily file, its parser and its `BNP_BVAL` marks are removed entirely (2026-09-17, `docs/bnp-excel-removal.md`) — a `BNP_BVAL` row surviving in an old database is reconciliation-only wherever it exists and never feeds P&L, but nothing writes one any more.
- **Mark date**: each FX leg is marked with the `FWD_OUTRIGHT` for its own `settle_date` (not a single T+5 date).
- **USD conversion**: quote-currency P&L converts to USD at **spot** of the same `as_of_date`, never at the forward outright.
- **Per-trade LTD P&L (USD)**, with `Q` = base amount, `f` = fill, `m` = outright mark for the leg's value date, `S` = spot (quote→USD):
  - FX, any pair: `PnL_quote = Q × (m − f)`; `PnL_USD = PnL_quote × S` (`S = 1` when quote is USD).
  - Futures: `PnL_USD = contracts × multiplier × (m − f)`.
  - IRS: `PnL_USD = PV_USD(t) + CASHFLOW_USD(t)`, both official marks from `engine/rates` at the swap's maturity date. A swap dealt at its fixed rate with no upfront is worth zero at the fill by construction, so this is the mark-minus-fill analogue of the FX formula; `CASHFLOW_USD` is the net of coupons already settled on or before `t` (0 for a forward-starting swap), which keeps LTD continuous across a coupon payment and at maturity, when PV goes to 0. Both are computed in the swap's currency and converted at that day's SPOT by the pricer. Realised at maturity by `engine/pnl/ledger.realise_settled` at the last official PV + cashflows on or before maturity.
  - FX option: `PnL_USD = quantity × (PREMIUM_mark − premium_fill) × S`, premium in base-ccy fraction, `S` = USD per base unit at spot; realised at expiry at the last official PREMIUM on or before expiry (an expiry-day intrinsic mark would be more exact, not written yet).
- **Daily P&L** = `LTD(t) − LTD(t−1bd)`. **Trading P&L** = LTD of trades with `trade_date = t`. **5d P&L** = `LTD(t) − LTD(t−5bd)`. **MTD** = `LTD(t) − LTD(last bd of previous month)`. **YTD** = `LTD(t) − LTD(last bd of previous year)`. All from our own recomputed daily series; `t−n bd` uses the trading calendar.
- **Mark time**: official close is 17:00 `America/New_York` (user decision 2026-09-15; Bloomberg's daily FX close, so historical `PX_LAST` spot and the `snapped_at` stamp agree). The retired xlsx workbook hard-coded `"PricingTime","15:00:00-04:00"`; that 15:00 convention was a Reconciliation-tab input only and has no live successor now that both are removed (2026-09-17). The app resolves the offset from the zone per date; intraday = live. Every mark row carries `snapped_at` with the resolved offset for that row.
- **Net USD** (FX only) = the USD position: Σ over pairs of sign × USD notional, sign +1 for USDXXX pairs (long base = long USD), −1 otherwise. The header shows this sign with the word "short USD" / "long USD" underneath. The engine's `portfolio_totals` returns the opposite quantity, the net non-USD delta (+ = long foreign), which the Delta tab labels as such and combines with futures delta; the header negates it. **Gross USD** = Σ over pairs of |net USD notional per pair|. Gold and equity futures are reported separately (see open questions).
- **Must not replicate** (from any legacy spreadsheet-style shortcut; the xlsx workbook itself and its literal arithmetic are gone as of 2026-09-17, but the principles below still guard the live P&L path):
  1. Futures P&L computed as `Q × (m − f) / m`: understates by `f/m`. Always `contracts × multiplier × (m − f)`.
  2. Converting quote-currency P&L at the forward outright instead of spot (≈ 2.3 % error on TRY; also BRL, MXN, IDR).
  3. Marking every pair at one shared date regardless of each leg's own value date, and marking matured trades forever instead of freezing settled trades.
  4. Hard-coded ranges or cell references in place of a real query over `trades` / `trade_legs` / `marks_official`.

## Repository layout and ownership

Every directory has exactly one owning agent. No agent edits outside its own directory.

```
data/ingest/     blotter CSV/xlsx parser, SQLite schema, swap rule    -> data-ingest
data/bloomberg/  blpapi pulls, marks table, marks_official view       -> bbg-data
engine/pnl/      LTD, daily, 5d, MTD, YTD per CLAUDE.md conventions   -> pnl-engine
engine/ladder/   cash ladder and delta-per-currency query             -> cash-ladder
engine/rates/    OIS curve bootstrap + swap valuation (QuantLib)      -> rates-pricer
engine/options/  FX/equity/commodity option pricing, vendored          -> options-pricer
                 options_calc (QuantLib); phase status in
                 engine/options/__init__.py's scope ledger
ui/              Dash app, one module per tab                         -> ui-shell
tests/           pytest, one file per module, owned by the module's agent
docs/            contract and open questions, owned by housekeeper
```

```
engine/rates_vol/ swaptions, caps/floors, SABR, Bermudan (vendored        -> rates-exotics
                 options_calc.rates; landed 2026-09-17, options merge Phase 6)
```

`engine/rates_vol/` writes `PV_USD`/`DV01_USD` under `QL_PRICER` and `VEGA`/`GAMMA`/`THETA` under `QL_OPTIONS_PRICER` (both already official for those mark_types); its option attributes, manual vols and SABR/Hull-White parameters live in its own defensively-created tables (`instrument_rate_options`, `rate_vols`, `rate_model_params`). No trade source carries swaptions or caps yet — see `docs/open-questions.md` item 61.
