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
- **SUPERSEDED 2026-09-17** ("no bnp fall back" -- see [[no_bnp_fallback_2026_09_17]]): the bullet below described the CASH-position side of `cash_ladder`, which has been REMOVED entirely (no `positions` read at all, no `source` parameter). Kept for historical context only -- do not build anything new against a `cash_ladder(..., source=...)` CASH row, it no longer exists. ~~The real file has multiple `CASH-<CCY>` position rows per currency on the same `as_of_date` (one per account, e.g. 6 rows for `CASH-USD` on 2026-08-17). `cash_ladder`'s CASH-side SQL must `GROUP BY instrument_id, settle_date` (SUM(quantity)) or the ladder silently produces one row per account instead of one row per (ccy, settle_date, kind). Also filter `positions.source = :source` (default `'BNP'`) — the P&L engine will later write `CALC` rows to the same table and they must never be summed alongside the raw PB snapshot.~~
- `marks_official.settle_date` must equal `as_of_date` for `mark_type = 'SPOT'` per the contract ("settle_date = as_of_date for SPOT"); the spot-table query filters on this explicitly. A zero or negative SPOT value must never be inverted/used (would raise `ZeroDivisionError` or produce a bogus negative rate) — drop that row so the ccy falls back to NaN downstream, never estimate or raise.
- `data/bloomberg/bnp_marks.py::extract_bnp_marks` only derives a BNP_BVAL SPOT for a pair when the pair is `USDXXX` (base = USD): SPOT = 1/Fx. For an `XXXUSD` pair (e.g. `AUDUSD`, `EURUSD`, `GBPUSD`, `XAUUSD`) `Fx` is quote(USD)->USD which is always exactly 1.0 by construction and carries no information about the base currency's USD rate; the row's `Price` is a forward outright, not a spot. So no SPOT is derivable from an XXXUSD row at all — its FWD_OUTRIGHT is still emitted but SPOT is skipped (logged as a warning, `strict=False`). On the real file (`HA_PNL_20260818.csv`, as_of 2026-08-17) this means `spot_table(conn, as_of, source='BNP_BVAL')` has NO row for AUD, EUR, GBP, XAU even though all four appear in `cash_ladder` — verified empirically, not assumed. `engine/ladder/views.py::ladder_table` surfaces this as `usd = NaN` for exactly those four currencies when driven off BNP_BVAL marks.
- `spot_table(conn, as_of_date, source=...)` (added for ladder_table): `source=None` (default) reads `marks_official` unchanged; an explicit source (e.g. `'BNP_BVAL'`) reads the raw `marks` table filtered to that one source only, mirroring `engine/pnl/pnl.py::_marks_df`'s `source=None` vs explicit-source pattern. Useful for exercising the ladder end-to-end before Bloomberg marks (and hence `marks_official`) are populated.

- 2026-09-15 scope change: `engine/ladder/exposure.py` is now leg-by-leg (one record per
  trade LEG, not per trade) AND pure delta / no P&L. Records need only `trade_id`,
  `settlement_date`, `book`, `currency`, `local_amount` (REQUIRED_FIELDS); the natural
  dedup key is `(trade_id, currency, settlement_date)` since `trade_id` legitimately
  repeats across a trade's own legs. `usd_entry_amount`, `usd_delta_entry` and
  `exposure_pnl` were removed entirely (first added as a P&L-lite formula, then
  scrapped mid-task when the user decided the cash ladder should be pure exposure).
  `SUMMARY_COLUMNS` is now `currency, fx_rate, local_delta, usd_delta, rate_source,
  rate_timestamp, status`; `portfolio_totals` returns only `net_usd, gross_usd,
  currencies, missing`. A USD leg is priced at identity (fx_rate=1.0) and appears as
  its own row/ladder column, but is excluded from Net/Gross (which are non-USD only)
  to avoid self-referential "USD exposure vs USD". `entry_rate` is kept on adapter
  records for drill-down display only, never consumed by exposure.py math.
- Downstream breakage from that change (owned by other agents, not fixed here):
  `ui/tabs/exposure.py` (lines ~146, 222, 245, 247, 306, 340-341, 370) and
  `engine/pnl/ledger.py` (lines ~6, 104, 106) still read `exposure_pnl` /
  `usd_delta_entry` / `usd_entry_amount` from `build_exposure`/`portfolio_totals`
  output or from adapter records, and will KeyError until updated. `ui/tabs/ledger.py`
  also references `usd_entry_amount` (lines 79/82/95) but on its own realised-trade
  DataFrame, not exposure.py records -- check whether that's actually independent
  before assuming it's the same breakage.
- Test fixture gotcha: when doubling one-record-per-trade fixtures into one-per-leg,
  counts double (e.g. 229 trades -> 458 leg records), and any per-instrument flag
  (like `is_ndf`) that used to be counted once per trade now appears on both legs, so
  multiply expected counts by 2, not just append a USD leg conditionally.
- 2026-09-17 fix (open-questions item 25): `_DELTA_SQL`'s FX_OPTION quote-ccy branch
  joins its SPOT mark via `s.instrument_id = i.base_ccy || i.quote_ccy` (the pair), NOT
  `s.instrument_id = t.instrument_id` (the option's own id, e.g.
  `USDJPY111926P-1`) — CLAUDE.md's literal "Aggregate delta per currency" SQL still has
  the latter, which can never match any real option instrument and would make the LEFT
  JOIN permanently return NULL spot for every option. Flagged to housekeeper: CLAUDE.md's
  SQL should be corrected to the base_ccy||quote_ccy join. Also: SQL `SUM()` silently
  ignores individual NULL rows within a `GROUP BY` group, so checking the final
  `delta_per_ccy` output for NaN is NOT sufficient to catch a missing SPOT mark when that
  ccy's group also has other non-NULL contributors (e.g. a forward leg in the same
  quote ccy) — the NULL row is dropped from the SUM and no NaN surfaces at all. Must
  check at the row level (query FX_OPTION trades with an official DELTA mark LEFT
  JOINed to SPOT, filter `s.value IS NULL`) before aggregation, and raise ValueError
  there, not just `.isna()` on the aggregated frame.
- `schema.OFFICIAL_MARK_SOURCE['DELTA']` and `['PREMIUM']` changed 2026-09-17 from
  `MANUAL` to `QL_OPTIONS_PRICER` (options_calc merge). Any test inserting a DELTA/
  PREMIUM mark and expecting it to appear in `marks_official` must read the source from
  `schema.OFFICIAL_MARK_SOURCE` rather than hard-coding `'MANUAL'`, or it will silently
  test against a non-official source and get an empty/NaN result. `instruments` also
  gained a sibling table `instrument_options` (option-specific attrs) the same day;
  `instruments` itself is unchanged (still the original 8-column INSERT shape).
- The `marks_official` VIEW in the current dev DB (`data/raw/risk.db`, checked
  2026-09-17) still maps `DELTA`/`PREMIUM` -> `MANUAL`, NOT `QL_OPTIONS_PRICER` — i.e.
  the dev DB's schema is stale relative to `data/ingest/schema.py`'s current
  `OFFICIAL_MARK_SOURCE` mapping (also called out in the 2026-09-17 five-agent audit
  memory). Don't assume the live dev DB always matches the current schema module;
  check `SELECT sql FROM sqlite_master WHERE name='marks_official'` if a query against
  the real file behaves unexpectedly.
- `data/raw/risk.db` (772-trade dev DB, 2026-09-17 snapshot) has real EURSEK exposure
  in TWO shapes: ~30 plain `EURSEK` FX_FWD forwards (2 legs each, `EUR`/`SEK`, signed
  correctly, both directions present) AND several `EURSEK<expiry><C|P>-<id>` FX_OPTION
  instruments (own `base_ccy='EUR'`, `quote_ccy='SEK'`, single NOTIONAL EUR leg,
  `settles_cash=0`). Both trade `USDSEK` and `EURUSD` directly too, so in THIS book
  SEK's and EUR's own USD spots are actually available via `data/bloomberg/live.py`'s
  request list (it only requests SPOT for pairs with their own open trades) — the
  "a cross's component pairs are never requested" gap (see
  `dollar_convention_and_gold_2026_09_17.md`) doesn't currently bite here, but would in
  a book that trades a cross with no direct exposure in either component pair.
- `data/raw/risk.db`'s only FUTURE-asset-class instrument is `ESU6 Index`
  (base_ccy='ES', quote_ccy='USD') — no gold/commodity future exists in the dev DB,
  only XAUUSD-style FX forwards (base_ccy='XAU'). `futures_delta.py` and the FX
  currency-delta path are therefore cleanly disjoint on this file; a future session
  adding a real gold future would need to check `engine.ladder.exposure.COMMODITY_CCYS`
  doesn't double-exclude it if it's also somehow keyed as an FX-style XAUUSD leg.
