---
name: blotter-fx-strip-and-samples-2026-09-17
description: SUPERSEDED same day by [[blotter-fx-standard-convention-2026-09-17]] -- xlsx-replica table + deliberately-divergent strip was reversed to one shared standard-convention pricing path
metadata:
  type: project
---

**Superseded same day, see [[blotter-fx-standard-convention-2026-09-17]].** This entry
describes the xlsx-replica table's strip/sample-value design; a few hours later the same
day the user reversed the replica decision entirely (`engine/pnl/xlsx_fx_replica.py`
deleted, `engine/pnl/fx_blotter.py` built in its place), so the "strip deliberately
differs from the table" behaviour and the `workbook_fx_pnl`-based sample formula
described below no longer exist. Kept for history only -- do not act on it.

Original (now-stale) entry: 2026-09-17, same day as the FX sub-tab's xlsx-replica rebuild
(`ui/tabs/blotter_fx.py`, `engine/pnl/xlsx_fx_replica.py` -- that rebuild dropped the
strip/filter-bar/row-expand entirely and was never itself written to memory) -- two
follow-up asks, both done in `ui/tabs/blotter_fx.py` only:

**1. P&L strip restored, but intentionally NOT sourced from the replica.** The strip
(`blotter_fx._fx_strip`) reuses `ui.tabs.blotter.render_headline_strip` +
`ui.tabs.blotter_pricing.priced_value_book`/`row_scoped_headline` -- the exact same
official-valuation machinery Total book/Rates/Options use -- scoped to
`FX_AND_FUTURE_PRODUCTS = ("FX_SPOT","FX_FWD","FX_SWAP","FUTURE")` (`_scoped_trade_ids`).
This is a genuinely different LTD number from the xlsx-replica table beneath it (which
still carries the must-not-replicate quirks on purpose) -- `STRIP_CAPTION` says so in the
UI. Built statically inside `build_layout` on every render (like the rest of this
sub-tab), NOT via the generic `_register_strip_callback` loop in `blotter.py` (still
excluded from that loop alongside "rates" -- the table itself still isn't
`priced_value_book`-shaped). Gotcha: `blotter_fx.py` must NOT import `ui.tabs.blotter` at
module level -- `blotter.py` imports `blotter_fx` at module level already, so that would
be circular; `render_headline_strip`/`fallback_caption` are imported lazily inside
`_fx_strip`.

**2. UI-only sample/placeholder values for missing marks.** `_fill_sample_values`
(pure pandas, no DB/engine writes) fills a cell ONLY when the real value is `None`/NaN:
sample mark = `fill * (1 + fixed_offset)` (offsets: mark_t1 -1.25%, mark_eod +1.75%,
mark_t2 -2.25% -- deterministic and column-distinct, not random), sample P&L reuses
`engine.pnl.pnl.workbook_fx_pnl` (the SAME formula/quirks the real replica uses, including
the LTD-2 t-1-mark-as-denominator bug) so the sample number's shape stays representative.
Real values are always preferred as inputs where a formula needs another mark (e.g.
pnl_t2 uses a real t1 mark over a sample one if the real one exists). Every sample cell
gets a literal `" (sample)"` text suffix (`SAMPLE_SUFFIX`) AND a `style_data_conditional`
rule (muted italic, matching the app's existing `.card-value--muted`/`.cell--unavailable`
CSS convention) -- both, not just one, since the instruction was "clearly flagged." A
one-line caption (`SAMPLE_CAPTION`) appears above the table only when at least one sample
was actually used this render.

**Testing gotcha worth remembering**: to unit-test that the strip's official LTD number
genuinely diverges from the replica table's own number (proving they're not
accidentally the same calculation), seed the official `FWD_OUTRIGHT` mark at the trade's
own `settle_date` (what `value_book` looks up) while leaving `fx_replica`'s shared
`workbook_valuation_date(as_of)` node unmarked -- the strip prices for real, the replica
table cell for that same trade is genuinely missing and gets sample-filled. One fixture,
both behaviours exercised at once.
