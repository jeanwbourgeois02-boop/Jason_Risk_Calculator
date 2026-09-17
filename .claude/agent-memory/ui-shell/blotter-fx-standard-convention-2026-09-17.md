---
name: blotter-fx-standard-convention-2026-09-17
description: FX sub-tab reversed off the xlsx-replica table (same day) onto engine/pnl/fx_blotter.py::fx_blotter_rows -> value_book; table and strip now price through one shared path and agree exactly
metadata:
  type: project
---

Same day as [[blotter-fx-strip-and-samples-2026-09-17]] (superseded by this entry): the
user withdrew the xlsx-replica authorisation. pnl-engine deleted
`engine/pnl/xlsx_fx_replica.py` and built `engine/pnl/fx_blotter.py::fx_blotter_rows`
instead -- same `OUTPUT_COLUMNS` shape/labels (legacy sheet's column layout: trade / tenor
/ fill / t-1-EOD-t-2 mark and P&L), but every mark/P&L cell now comes straight from
`engine.pnl.valuation.value_book` (market-standard convention: each FX leg at its own
settle_date's FWD_OUTRIGHT, quote P&L at spot, futures contracts x multiplier x
(mark-fill), settled trades frozen) rather than the workbook's must-not-replicate
arithmetic.

**What changed in `ui/tabs/blotter_fx.py`:**
- Import `fx_blotter_rows` from `engine.pnl.fx_blotter`; call it with
  `value_fn=_priced_value_fn` (a one-line wrapper around
  `ui.tabs.blotter_pricing.priced_value_book` that drops its `(n_fallback, n_total)`
  tuple) so the table retries a missing official mark against BNP_BVAL exactly like the
  strip does -- table and strip now literally share one pricing call per date.
- `STRIP_CAPTION` deleted entirely (it existed only to explain why the strip and table
  showed different numbers; they no longer do). `fallback_caption` behaviour is
  unchanged and still shown when any row used the BNP_BVAL retry.
- Renamed `fx_replica_table` -> `fx_blotter_table`, `DATATABLE_ID` ->
  `"blotter-fx-datatable"` (was `"blotter-fx-replica-datatable"`) -- purely a rename for
  clarity, no behaviour change; nothing outside `ui/tabs/blotter_fx.py` and
  `tests/test_ui.py` referenced the old names (`ui/tabs/blotter.py` only calls
  `blotter_fx.build_layout`).
- Sample-value fallback for missing cells (UI-display only, never touches DB/engine) is
  KEPT but its P&L formula no longer calls `engine.pnl.pnl.workbook_fx_pnl` (that
  function still exists and is still correct for the retained Reconciliation-tab
  arithmetic, just no longer wired into this module). New formula lives entirely in
  `blotter_fx.py`: `_sample_pnl_divisor(instrument_id)` picks `'m'` when the instrument
  starts with `"USD"`, `None` for a plausible 6-letter cross pair (neither side USD --
  left blank, no sample), else `'f'` (covers XXXUSD pairs AND futures, since for futures
  `Q/f` recovers `contracts * multiplier` exactly). Note this frame carries no
  `product`/`base_ccy`/`quote_ccy` column, so the split is a heuristic on
  `instrument_id`'s string shape alone (`len==6 and isalpha and isupper` = "looks like an
  FX pair") -- correct for every instrument in the reference data but worth re-checking
  if an unusual futures ticker or 6-letter non-FX identifier ever shows up here.
  `pnl_t2`'s sample is independent (uses `mark_t2` directly), unlike the old formula's
  t-1-mark-as-denominator quirk.

**Testing gotcha (carried over, still true):** to seed a trade the new pipeline prices
for real, put the `FWD_OUTRIGHT` mark at the trade's OWN `settle_date` with `as_of_date`
matching the query date -- `_mark_at` with `source=None` requires `as_of_date = :d`
exactly. `_seed_fx_trade` in `tests/test_ui.py` is the one fixture now used everywhere in
that test block (the old `_seed_fx_replica_trade`/`_seed_fx_strip_trade` duplication was
collapsed into it, since both the table and the strip use the same settle-date lookup
now).
