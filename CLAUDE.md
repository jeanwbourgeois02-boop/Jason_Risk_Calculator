# risk-monitor

## User-authorised direction, 2026-09-15 (supersedes the 2026-09-14 correction)

`docs/BUILD_PLAN.md` is the build specification. The app's headline P&L is the per-trade valuation in its section 2 (each FX leg marked at the outright for its own value date, quote P&L converted at spot, futures as contracts × multiplier × price change, settled trades frozen), the close series and periods in section 3, and the four tabs in section 5. The "P&L conventions" section below is in force again and the "Must not replicate" list applies to the headline.

The literal workbook arithmetic (`engine/pnl/pnl.py`, `docs/excel-parity-audit.md`) is retained unchanged as the Reconciliation tab only. It never feeds the header, the blotter or the ladder. `HA_PNL_*.csv` remains the trade source; workbook uploads add only futures fills per the plan. Missing values stay missing everywhere.

The cash ladder is a pure delta table: leg by leg, crosses included, spot for delta, no P&L on it (engine change landed 2026-09-15).

FX and futures risk monitor: cash ladder, delta per currency, daily / 5d / MTD / YTD P&L. Python; Dash front end later. Fund NMMF, base currency USD, prime broker BNP. Reference inputs: `data/raw/HA_PNL_20260818.csv` (BNP position and P&L snapshot, 242 rows × 137 cols) and `data/raw/HA-portfolio vJean.xlsx` (the Excel calculator this app replaces). Open items live in `docs/open-questions.md`, not here.

## Working mode

Default: lean. Work directly in this session on the model set by `/model`. Do not spawn the housekeeper, specialists or reviewer unless the user's message explicitly names them. Keep each task to one module and one test file. End every task with `py -3 -m pytest tests/ -q` and report the pass count. Full agent pipeline is reserved for changes to P&L arithmetic in `engine/` and is invoked only by the user.

## Data contract

### Tables

All dates ISO `YYYY-MM-DD`, all amounts signed (`+` = receive / long). No column is nullable; "not applicable" uses the documented sentinel.

```sql
instruments (
  instrument_id   TEXT PRIMARY KEY,   -- 'USDJPY', 'EURSEK', 'XAUUSD', 'ESU6 Index', 'IRSOIS-USD-22860996',
                                      -- 'USDJPY digi p 152 2026-08-26'
  asset_class     TEXT NOT NULL,      -- FX | FUTURE | IRS | FX_OPTION | CASH
  base_ccy        TEXT NOT NULL,      -- unit of trades.quantity: 'USD' for USDJPY, 'AUD' for AUDUSD, 'XAU',
                                      -- 'ES' (index units), notional ccy for IRS, base of pair for options
  quote_ccy       TEXT NOT NULL,      -- currency of trades.price and of local P&L
  multiplier      REAL NOT NULL,      -- 1 for FX / IRS / options; 50 for ES
  is_ndf          INTEGER NOT NULL,   -- 1 = non-deliverable: local legs do not settle
  bbg_ticker      TEXT NOT NULL,      -- 'USDJPY Curncy', 'ESU6 Index', ...
  expiry_date     TEXT NOT NULL       -- '9999-12-31' for perpetual (FX pairs, cash)
);

trades (
  trade_id        TEXT PRIMARY KEY,   -- BNP id ('196789440'), 'XL-<row>' from the xlsx, or app-generated
  source          TEXT NOT NULL,      -- BNP | XLSX | MANUAL
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  product         TEXT NOT NULL,      -- FX_SPOT | FX_FWD | FX_SWAP | FUTURE | IRS | FX_OPTION
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
  mark_type       TEXT NOT NULL,      -- SPOT | FWD_OUTRIGHT | FUTURE_PX | PAR_RATE | PV_USD | DV01_USD | PREMIUM | DELTA
  value           REAL NOT NULL,      -- DELTA = base-ccy delta per 1 unit of trades.quantity (may exceed 1 for digitals)
  source          TEXT NOT NULL,      -- BNP_BVAL | BBG_BFXFORWARD | BBG_BDH | BBG_BDP | BBG_INTERP | MANUAL
                                      -- BBG_INTERP = linear interpolation in forward points between standard
                                      -- tenors (fallback for broken dates); never official
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

positions (                            -- one row per PB position per day (BNP grain) or per computed net (CALC)
  as_of_date      TEXT NOT NULL,
  source          TEXT NOT NULL,      -- BNP | CALC
  account         TEXT NOT NULL,
  instrument_id   TEXT NOT NULL REFERENCES instruments,
  settle_date     TEXT NOT NULL,      -- value date (forwards), expiry (futures), maturity (IRS), as_of_date (cash)
  quantity        REAL NOT NULL,      -- base_ccy units, signed
  cost_local      REAL NOT NULL,      -- quote_ccy; BNP 'Local Cost'
  mark            REAL NOT NULL,      -- BNP 'Price'
  fx_to_usd       REAL NOT NULL,      -- BNP 'Fx' (quote_ccy → USD spot)
  mv_local        REAL NOT NULL,
  mv_usd          REAL NOT NULL,
  pnl_dtd_usd     REAL NOT NULL,
  pnl_mtd_usd     REAL NOT NULL,
  pnl_ytd_usd     REAL NOT NULL,
  PRIMARY KEY (as_of_date, source, account, instrument_id, settle_date)
);
```

Leg layouts: FX spot/forward = 2 legs (`FX_NEAR`, one per currency); FX swap = 4 legs (`FX_NEAR` × 2, `FX_FAR` × 2) under one `trade_id`; FUTURE = 1 `NOTIONAL` leg in USD, amount `contracts × multiplier × fill`, `settle_date` = expiry, `settles_cash` 0; IRS = `FIXED` leg (amount = `−quantity`, rate = fixed) + `FLOAT` leg (amount = `+quantity`, rate = spread), `settle_date` = maturity — a payer (`quantity > 0`) has a negative FIXED leg (pays) and a positive FLOAT leg (receives); FX_OPTION = 1 `NOTIONAL` leg in base ccy, `settles_cash` 0.

### Official marks

`marks.source` is part of the primary key, so joining `marks` directly returns one row per source and double-counts as soon as two sources exist for the same date. Exactly one source is official per `mark_type`:

| mark_type | official source |
|---|---|
| SPOT, FWD_OUTRIGHT | BBG_BFXFORWARD |
| FUTURE_PX | BBG_BDH |
| PAR_RATE, PV_USD, DV01_USD | QL_PRICER |
| DELTA, PREMIUM | MANUAL |
| any | BNP_BVAL is reconciliation only, never official |
| any | BBG_INTERP (linear interpolation in forward points between standard tenors, written by the pull script when a broken-date outright cannot be requested directly) is reconciliation / fallback only, never official |
| PAR_RATE, PV_USD, DV01_USD | BBG_BDH (Bloomberg SWPM) is reconciliation only, never official, mirroring BNP_BVAL for FX (decided 2026-09-15 with the `engine/rates` QuantLib OIS pricer landing) |

The mapping is held in a `marks_official` view (`marks` filtered to the official source per `mark_type`, so `(as_of_date, instrument_id, settle_date, mark_type)` is unique). Every P&L or delta query reads from `marks_official`, never from `marks` directly.

### BNP file → tables

File dated `T` is the **T−1 close** snapshot (`HA_PNL_20260818.csv` contains trades through 2026-08-17). Filter `Fund = NMMF`; `Financial Type ∈ {FORWARD, CURRENCY, FUTURES, INTEREST_RATE_SWAP}`. `Base Currency` is always USD. Column `Unnamed: 136` and `Column 2` are empty.

FORWARD rows (one row per trade):

- `Symbol` = `<PAIR><VD mmddyy>-<trade_id>`; `trade_id` = part after `-`; `instrument_id` = first 6 chars.
- `Symbol Description` matches exactly
  `^TD (\d{2}/\d{2}/\d{4}) VD (\d{2}/\d{2}/\d{4}) (SELL|BUY) ([A-Z]{3}) VS \.(BUY|SELL) ([A-Z]{3}) @ (\d+\.\d{8})$`
  → `trade_date`, `value_date`, `sold_ccy`, `bought_ccy`, `rate`. The dot before the second verb is literal and always present. Only `SELL … VS .BUY …` occurs in the reference file; the parser must accept both orders. Rows that fail the regex are rejected, never coerced.
- `Quantity` = signed base-currency amount (+ = bought base). `Local Cost` = `Quantity × rate` rounded to whole quote units (BNP derives `Quantity` from it, hence residues like −3,500,000.02). `Currency` = quote ccy (`DOL.C-USAA` = USD).
- Amounts: base amount = `|Quantity|`, quote amount = `|Local Cost|`. `sold_amount` / `bought_amount` are these two mapped by `sold_ccy` / `bought_ccy`.
- Legs: (`base_ccy`, `Quantity`) and (`quote_ccy`, `−Local Cost`), both `settle_date = value_date`, `rate = rate`, `settles_cash = NOT is_ndf`.
- `Price` = forward outright to the value date (BloombergBVAL); `Fx` = quote→USD **spot**, rounded to 6 dp; `Position = Quantity`.
- `Cost` = `Local Cost × Fx` (USD); `Trade Factor = 1`.
- `Quantity = 0` with zero `Local Cost` / `MV Local` / `MV Base` is a **closed line**: an NDF past its fixing (BNP zeroes the position at fixing and books the realised amount to cash; the line stays until value date carrying the realised DTD / MTD) or a settled forward lingering with a rounding residual (2026-09-15 file: 49 rows). No trade or legs (the row has no notional; the fill comes from an earlier daily file or is lost); a `positions` row with quantity 0 and the reported P&L is written; the open-position identities (DTD, MTD, direction sign, `Trade Factor`) are not checked; blank `Fx` → `0.0` sentinel; its `Price` (a fixing, or stale) is excluded from the netting mark check and produces no BNP_BVAL mark. Any other `Quantity = 0` row is rejected.

CURRENCY rows: cash balances; `Symbol` = `<CCY>.C-xxAA`; `Quantity = MV Local = MV Base` (small residuals only in the reference file). `instrument_id` = `CASH-<CCY>`.

FUTURES rows: one netted row per contract. `Quantity` = contracts, `Trade Factor` = multiplier, `Price` = settlement price, `Cost = Σ contracts × multiplier × fill`, `MV = Quantity × Trade Factor × Price − Cost`. `Symbol` = `ESU6-USAA` → `instrument_id` = `ESU6 Index`.

INTEREST_RATE_SWAP rows: `Symbol` = `IRSOIS-USD-<id>` (one instrument per swap); description `IRS NA <start mm/dd/yyyy> <end mm/dd/yyyy> <fixed rate> <ccy>` — **no pay/receive flag**. `Quantity` = notional in millions, `Position` = notional, `Price` = PV per 1m notional, `MV Base = Quantity × Price`. Priced by Markit.

### xlsx → tables

`All FX trades` (A `Date`, B pair, C `Quantity`, D `tenor`, E `fill`): `Quantity` is **signed USD notional for every pair** (AUDUSD +2,000,000 = buy AUD against 2,000,000 USD; BNP shows the same trade as +2,854,667 AUD). Futures rows: `C = contracts × 50 × fill` (contracts recoverable from the cell formula). `trade_id = 'XL-<row>'`. Legs are derived: USDXXX → (USD, `C`) and (XXX, `−C × fill`); XXXUSD → (XXX, `C / fill`) and (USD, `−C`); crosses (EURSEK) → (EUR, `C / EURUSD_spot`) and (SEK, `−EUR amount × fill`). `settle_date = tenor`.

`All IRS trades`: `Direction (local ccy)` < 0 = receive fixed (`Curve!B4 = +1`); `Yield` = fixed rate; `Start`/`End`; PV, DV01, PV T-1 are consumed as marks `PV_USD`, `DV01_USD` (T-1 as the previous `as_of_date`).

`All Options Trades`: `Name` (free text), `Expiry`, `Size` (notional), `Entry Price` (premium), `Current price` → marks `PREMIUM`. Option deltas are typed by hand in `Portfolio!M29:O44` → marks `DELTA`, `source = MANUAL`, value = base-ccy delta / `Size`.

### `package_id` rule (FX swaps)

Two forward rows form one `FX_SWAP` package when all hold: same account, same pair, same trade date, opposite-signed quantities, equal |USD-leg amount| within 0.01 %, **different** value dates. `package_id = 'SWAP-' || min(trade_id)`; near leg = earlier value date. Opposite-signed rows with the same value date are intraday round trips and stay separate outrights. Groups with more than one candidate on a side are not auto-grouped; they go to a review list. A swap's near leg settles T+2 and drops out of the PB snapshot, so swaps are identified from the blotter or by diffing the daily PB archive, never from one snapshot.

### Reconciliation checks and tolerances

- Parse: every FORWARD row matches the regex; pair and value date in `Symbol` agree with the description.
- `|Quantity × rate − Local Cost| ≤ 1` quote unit (Local Cost is rounded to whole units).
- `|Quantity × (Price − rate) − MV Local| ≤ max(0.05, |Quantity| × 0.5e-5)` quote units — BNP prints `Price` to 5 dp but computes MV Local from the unrounded mark (0.07 MXN on an 18m USD leg and 0.11 SEK on a 26.7m EUR leg in the 2026-09-15 file; the flat 0.05 was exact on 2026-08-18).
- `|MV Local × Fx − MV Base| ≤ max(0.01 USD, |MV Local| × 0.5e-6)` — `Fx` is rounded to 6 dp, so this is a tolerance, not an identity (observed max 14.4 USD on a 71m KRW leg; 0.16 % relative on IDR where `Fx = 0.000056`).
- `DTD Total P&L = MV Base − Start Date Dirty MV`; `MTD Total P&L = MV Base − Previous Month End Market Value Base`; `DTD Total = DTD Trading` (no other buckets populated).
- Netting xlsx `Quantity` by pair for trades dated ≤ T−1 must equal the BNP file dated T converted to USD notional (verified exact for 2026-08-18 on every pair).

### Six tabs as views

| Tab | View |
|---|---|
| Cash ladder | `trade_legs` where `settles_cash = 1 AND settle_date ≥ as_of`, grouped by `ccy, settle_date`, plus `CASH` rows from `positions` where `source = 'BNP'` (BNP is the source for ladder cash balances, summed per currency across accounts). The `≥` here versus `>` in the delta query is intentional: a leg settling on `as_of` is cash that moves today but carries no delta by close, matching BNP dropping settled forwards. |
| FX | FX trades × `marks_official` (`FWD_OUTRIGHT` at the leg's own `settle_date`, `SPOT` for USD conversion); per-pair USD notional = Σ sign(base leg) × \|USD leg\| |
| Rates | IRS trades × `marks_official` (`PV_USD`, `DV01_USD`, `PAR_RATE`, official source `QL_PRICER`); `curves` is live via `engine/rates` (`bootstrap_and_store` / `price_and_store`), single-currency OIS only (Phase 1: USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON, CAD CORRA, AUD AONIA) — term-rate, basis and XCCY swaps are Phase 2 |
| Options | FX_OPTION trades × `marks_official` (`PREMIUM`, `DELTA`) |
| Delta | query below |
| Overall book | `positions` (BNP vs CALC), P&L rollups by strategy / account, LTD series |

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
  LEFT JOIN marks_official s ON s.instrument_id = t.instrument_id AND s.mark_type = 'SPOT'  AND s.as_of_date = :as_of
  WHERE t.product = 'FX_OPTION'
)
SELECT ccy, SUM(delta) AS delta FROM d GROUP BY ccy;
```

The `SPOT` join in the option quote-currency branch is a `LEFT JOIN` so that a missing spot mark surfaces as a `NULL` delta; the engine must raise if any resulting delta is `NULL`, never drop the leg silently. `engine/ladder/ladder.py` currently embeds the earlier inner-join form of this query and must be changed to match when options are built.

Per-pair delta (the sheet's "Position") is the same union grouped by `t.instrument_id` in USD-notional terms.

## P&L conventions

- **Sign**: `quantity > 0` = long base currency / receive; P&L is positive when the mark moves in favour of the position. Long USDXXX profits when the pair rises; long XXXUSD profits when the pair rises.
- **Display notional**: USD notional per pair, sign = direction of the base currency (the xlsx convention). Storage keeps exact leg amounts.
- **Source of truth**: fills (`trades` + `trade_legs`). Marks come from Bloomberg (`BFXFORWARD` / `BDH` / `BDP`). The daily PB file archive is an independent reconciliation check only; it never feeds P&L.
- **Mark date**: each FX leg is marked with the `FWD_OUTRIGHT` for its own `settle_date` (not a single T+5 date).
- **USD conversion**: quote-currency P&L converts to USD at **spot** of the same `as_of_date`, never at the forward outright.
- **Per-trade LTD P&L (USD)**, with `Q` = base amount, `f` = fill, `m` = outright mark for the leg's value date, `S` = spot (quote→USD):
  - FX, any pair: `PnL_quote = Q × (m − f)`; `PnL_USD = PnL_quote × S` (`S = 1` when quote is USD).
  - Futures: `PnL_USD = contracts × multiplier × (m − f)`.
  - IRS: `PnL_USD = PV_USD(t) − PV_USD(trade date)`; PV, DV01 consumed as marks until the pricer is rebuilt on `curves`.
  - FX option: `PnL_USD = (premium_mark − premium_fill) × Size`, converted at spot if the premium currency is not USD.
- **Daily P&L** = `LTD(t) − LTD(t−1bd)`. **Trading P&L** = LTD of trades with `trade_date = t`. **5d P&L** = `LTD(t) − LTD(t−5bd)`. **MTD** = `LTD(t) − LTD(last bd of previous month)`. **YTD** = `LTD(t) − LTD(last bd of previous year)`. All from our own recomputed daily series; `t−n bd` uses the trading calendar.
- **Mark time**: official close is 17:00 `America/New_York` (user decision 2026-09-15; Bloomberg's daily FX close, so historical `PX_LAST` spot and the `snapped_at` stamp agree). The xlsx hard-codes `"PricingTime","15:00:00-04:00"`; that 15:00 snapshot is a Reconciliation-tab input only. The app resolves the offset from the zone per date; intraday = live. Every mark row carries `snapped_at` with the resolved offset for that row.
- **Net USD** (FX only) = the USD position: Σ over pairs of sign × USD notional, sign +1 for USDXXX pairs (long base = long USD), −1 otherwise. The header shows this sign with the word "short USD" / "long USD" underneath. The engine's `portfolio_totals` returns the opposite quantity, the net non-USD delta (+ = long foreign), which the Delta tab labels as such and combines with futures delta; the header negates it. **Gross USD** = Σ over pairs of |net USD notional per pair|. Gold and equity futures are reported separately (see open questions).
- **Must not replicate** from the xlsx:
  1. Futures P&L computed as `Q × (m − f) / m` (the `RIGHT(pair,3)="USD"` branch): understates by `f/m`.
  2. LTD-2 P&L dividing by the t−1 mark (`F`) instead of the t−2 mark (`J`).
  3. Converting quote-currency P&L at the forward outright (≈ 2.3 % error on TRY; also BRL, MXN, IDR).
  4. Marking every pair at one `WORKDAY(today, 5)` outright regardless of value date, and marking matured trades forever.
  5. Option-delta adjustments on fixed Portfolio rows (`B15`, `B23`, `K15`, `K23`) that no longer align with the sorted spill.
  6. Hard-coded ranges (`SUM(ABS(C2:C135))`, `L11:O29`, `A6:A24`, IRS `H4:H34 / I4:I16 / K4:K27`).

## Repository layout and ownership

Every directory has exactly one owning agent. No agent edits outside its own directory.

```
data/ingest/     BNP CSV and xlsx parsers, SQLite schema, swap rule   -> data-ingest
data/bloomberg/  blpapi pulls, marks table, marks_official view       -> bbg-data
engine/pnl/      LTD, daily, 5d, MTD, YTD per CLAUDE.md conventions   -> pnl-engine
engine/ladder/   cash ladder and delta-per-currency query             -> cash-ladder
engine/rates/    OIS curve bootstrap + swap valuation (QuantLib)      -> rates-pricer
ui/              Dash app, one module per tab                         -> ui-shell
tests/           pytest, one file per module, owned by the module's agent
docs/            contract and open questions, owned by housekeeper
```
