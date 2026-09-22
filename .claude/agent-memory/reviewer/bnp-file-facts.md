---
name: bnp-file-facts
description: Verified facts about data/raw/HA_PNL_20260818.csv that matter when reviewing ingest or P&L code (counts, netting groups, blanks, buckets)
metadata:
  type: project
---

Verified on 2026-09-13 against `data/raw/HA_PNL_20260818.csv` (as_of 2026-08-17):

- NMMF rows: 229 FORWARD, 9 CURRENCY, 1 FUTURES (ESU6, 27 contracts, TF 50, Cost 10,234,475, Fx = 1.0), 3 INTEREST_RATE_SWAP. No non-NMMF rows.
- Forward accounts: BNPP-IPBFX-NMMF 220, BNPP-IPB-NMMF 3, JPML-IPB-NMMF 3, NTXS-ISD-NMMF 3; futures at GSIL-FUT-NMMF.
- Forwards heavily share (account, pair, value date): e.g. USDSGD 091626 has 40 rows, USDCHF/USDJPY 26 each. Within every such group Price and Fx are identical (0 groups with >1 distinct value), so netting into the `positions` PK loses per-trade cost/MV only, not mark/Fx.
- The `positions` PK `(as_of_date, source, account, instrument_id, settle_date)` cannot hold BNP's per-trade FORWARD grain; the contract sentence "one row per PB position per day (BNP grain)" is internally inconsistent for forwards. Any ingest must net or change the PK.
- FORWARD pairs (18): AUDUSD, EURUSD, GBPUSD, XAUUSD (Currency = DOL.C-USAA, **Fx = 1.0 exactly**, so Fx can never give an XXXUSD spot) plus 14 USDXXX pairs. No cross pairs (EURSEK etc.) in the file, so cross-spot derivation is only testable synthetically. Distinct (pair, value_date) keys = 19; only USDKRW has 2 value dates, all others 1. Value dates in file: 2026-09-08, -09-16, -09-17, -09-21. `Fx` is identical across all rows of a quote ccy (0 quote ccys with >1 distinct Fx).
- Five `DOL.C-USAA` cash rows exist but each is in a different account, so they do not collide.
- CURRENCY rows with zero balance have blank `Fx` (EUR, XAU, HKD, one USD row). Non-zero forward numeric cells have no blanks.
- All DTD buckets other than `DTD Total P&L` / `DTD Trading P&L` are exactly 0 (confirms "DTD Total = DTD Trading").
- Column `Previous Year End Market Value Base` exists; CLAUDE.md lists no YTD check but one could be added.
- After ingest with as_of 2026-08-17, every `trade_legs.settle_date` lies in 2026-09-08..2026-09-21: there are NO legs with `settle_date <= as_of`, so any real-file test of "matured legs excluded" or "same-day leg included" is vacuous and needs a synthetic case.
- `positions` holds 6 `CASH-USD` rows (one per account) plus CASH-EUR/XAU/HKD at 0.0, all `settle_date = as_of`; consumers that do not GROUP BY ccy will show 6 USD cash rows.
- `marks_official` is empty on the real file (ingest loads no marks), so SPOT/DELTA/option branches of any query are only testable synthetically until bbg-data lands.

**Why:** these facts decide whether netting / sentinel choices in ingest code are defects or forced by the data.
**How to apply:** when reviewing ingest or positions-consuming code, treat forward netting as forced by the PK, and check that blank cash `Fx` is handled with a documented sentinel. See [[review-findings-ingest]], [[review-findings-bloomberg]].
