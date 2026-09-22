---
name: blotter-strips-partial-pricing-2026-09-17
description: Blotter P&L strips (row_scoped_headline/row_scoped_period_pnl) no longer poison to NaN when one visible row is unpriced -- mirrors ui/tabs/header.py's same-day _priced_single/_priced_diff fix, row-scoped instead of whole-book.
metadata:
  type: project
---

2026-09-17, live-Bloomberg-PC follow-up (same day as [[blotter-loading-and-filters-fix-2026-09-17]]
and the BNP-fallback removal): once real marks started flowing (spots/forwards/swaps
priced), every Blotter sub-tab's P&L strip still went blank the moment even ONE visible
row was unpriced (reported case: one forward settling today, one future, five options
among an otherwise-fully-priced book). `ui/tabs/header.py` got the identical fix the
same day to its own headline cards (`_priced_single`/`_priced_diff`, see that module's
"Partial pricing" docstring section) -- this is the equivalent for `ui/tabs/blotter_pricing.py`'s
`row_scoped_headline`/`row_scoped_period_pnl`, which every sub-tab strip (Total/FX/
Futures/Rates/Options, via `render_headline_strip`) and the Total book's per-asset-class
table (`asset_class_pnl_table`) all read.

**What changed**: `_priced_sum_for_ids`/`_entry`/`_diff_entry`/`_trading_entry` (the old
"NaN if ANY row in trade_ids is unpriced" chain) replaced by
`_priced_single_scoped`/`_priced_single_from_df`/`_priced_diff_scoped` (+ `_scoped_frame`,
`_reason_tag`, `_product_label`, `_unpriced_breakdown` -- **duplicated from
`header.py`, not imported**, since that module is a different lane's file and its own
docstring explicitly says so). Every period entry dict now carries
`excluded_summary`/`excluded_detail` alongside the existing `value`/`ref_date`/
`available`/`reason`. A figure sums PRICED rows only within the CURRENT ROW-SCOPED set
(`trade_ids` -- the sub-tab's visible rows, not the whole book, unlike header.py's
whole-book cards) and shows "excludes N of M trades unpriced" as a visible caption with
a per-product/reason breakdown tooltip; only goes fully "n/a" when EVERY row in the set
is unpriced. A DIFFERENCE entry (Daily/5d/MTD/YTD/Previous day) additionally excludes a
trade priced on one date but not the other (never credits a fake one-sided jump); a
trade new since the reference date (no row at all in that date's book) still
contributes its full current value normally, same as `header.py`'s rule.

**`ref_date` convention gotcha, preserved not unified**: `row_scoped_headline` and
`row_scoped_period_pnl` have DIFFERENT, pre-existing `ref_date` display conventions
that predate this fix and were deliberately kept exactly as they were:
`row_scoped_headline` shows `as_of` (today) under every "as of today" card
(ltd/daily/d5/mtd/ytd/trading) and only the raw T-1/T-2 level cards show their own
date -- mirrors the Excel header layout. `row_scoped_period_pnl` shows the COMPARISON
date under a difference card instead (t1 for "daily", etc.) -- used by
`asset_class_pnl_table`. Do not "fix" this into one convention without checking; it's
two call sites of the same `_priced_diff_scoped` with a deliberately different
`ref_date` argument, not an inconsistency in the shared helper.

**Rendering**: `ui/tabs/blotter.py::render_headline_strip` (no longer takes a
`caption` param -- that was solely the now-deleted BNP-fallback badge) appends an
extra `card-note` Div (italic, `var(--muted)`) under a card's ref_date when
`excluded_summary` is present, with `excluded_detail` as its HTML `title` tooltip --
same visual pattern as `header.py::_pnl_card`, built without touching CSS (reused
existing `card-note`/`var(--muted)` rather than adding a new stylesheet rule, since
CSS ownership wasn't confirmed for this "be quick" follow-up). `asset_class_pnl_table`
got the matching DataTable-tooltip treatment: an available-but-partial cell keeps its
real formatted value and gets `excluded_summary`/`excluded_detail` as its tooltip
instead of the plain unavailable-reason tooltip.

**`scoped_period_pnl` (the older, whole-book, non-row-scoped function) was NOT
touched** -- confirmed via grep it has no production caller (only one test in
`tests/test_ui.py`, not this lane's file), so it's dead/legacy code, out of scope for
this fix which was specifically about the strips actually wired into the UI.

See [[blotter-loading-and-filters-fix-2026-09-17]] for the render-time/error-isolation
fixes earlier the same day, and the BNP-fallback-removal addendum there for why
`priced_value_book` still returns a 3-tuple with `n_fallback` hardcoded 0.
