---
name: data-facts
description: Data quirks discovered while building engine/ladder (cash ladder, delta, spot table) against the real BNP CSV and schema.
metadata:
  type: project
---

Observed on the reference file `data/raw/HA_PNL_20260818.csv` loaded with `as_of_date='2026-08-17'` via `data.ingest.bnp.load`:

- `marks_official` is EMPTY on the real file (no marks are loaded by the ingest step yet). Any code exercising `delta_per_ccy` or `spot_table` against the real file only sees the forward/future-leg UNION branch of the CLAUDE.md delta SQL; the FX_OPTION branches and all SPOT-based USD conversion are untestable until bbg-data populates `marks`. `delta_usd` / `amount_usd` will be NaN for every non-USD currency on the real file for now.
- The cash ladder's non-CASH rows come only from `trade_legs` where `settles_cash = 1`. NDF currencies (BRL, TWD, KRW, IDR per `bnp.NDF_CCYS`) never appear in the ladder because their legs carry `settles_cash = 0`. TRY is deliverable and does appear.
- CLAUDE.md's "Aggregate delta per currency" SQL filters forward/future legs with `settle_date > :as_of` (strictly greater), while the Cash ladder view spec uses `settle_date >= as_of`. These are genuinely different thresholds in the contract — do not assume they match. A leg with `settle_date == as_of` counts in the ladder but not in the aggregate delta query.
- `positions.fx_to_usd` must never be used for ccy->USD conversion in engine/ladder — CLAUDE.md requires SPOT marks from `marks_official` only, and the ingest sets `fx_to_usd = 0.0` as a sentinel on zero-balance CURRENCY rows (see `data/ingest/bnp.py::_parse_currency`), which would silently zero out a real balance if used for conversion.
- Instrument ids for FX are plain 6-letter pairs (e.g. `USDJPY`, `AUDUSD`); building a ccy->USD spot table means checking which side of the pair is `USD` and inverting when USD is the base (`USDJPY` -> JPY rate = 1/value) vs using directly when USD is the quote (`AUDUSD` -> AUD rate = value). Crosses (neither side USD, e.g. `EURSEK`) have no single conversion mark and must be dropped, not estimated.
- The real file has multiple `CASH-<CCY>` position rows per currency on the same `as_of_date` (one per account, e.g. 6 rows for `CASH-USD` on 2026-08-17). `cash_ladder`'s CASH-side SQL must `GROUP BY instrument_id, settle_date` (SUM(quantity)) or the ladder silently produces one row per account instead of one row per (ccy, settle_date, kind). Also filter `positions.source = :source` (default `'BNP'`) — the P&L engine will later write `CALC` rows to the same table and they must never be summed alongside the raw PB snapshot.
- `marks_official.settle_date` must equal `as_of_date` for `mark_type = 'SPOT'` per the contract ("settle_date = as_of_date for SPOT"); the spot-table query filters on this explicitly. A zero or negative SPOT value must never be inverted/used (would raise `ZeroDivisionError` or produce a bogus negative rate) — drop that row so the ccy falls back to NaN downstream, never estimate or raise.
