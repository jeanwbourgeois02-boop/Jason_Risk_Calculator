---
name: header-partial-pricing
description: header cards sum over priced trades only (not poison-to-NaN) with a visible "excludes N of M" label; _priced_diff additionally drops trades priced on only one of two dates so a mark appearing/disappearing can't fake a jump
metadata:
  type: project
---

2026-09-17, live-Bloomberg-PC follow-up to [[header-visible-reasons-and-trade-counts]].
Once spots/most forwards/swaps were actually marked, the user still saw every headline
card as "n/a" -- root cause this time was real: `engine.pnl.ledger.period_pnl`/`ltd`
(and this module's own prior `ui.tabs.blotter_pricing.scoped_period_pnl`-based
implementation) poison an ENTIRE period to NaN the instant any single trade in the book
is unpriced (a few options with no strike typed in yet, one future, one same-day
forward), regardless of how much of the book does price.

`ui/tabs/header.py::_build_figures` no longer calls `engine.pnl.ledger` or
`ui.tabs.blotter_pricing.scoped_period_pnl` at all. It builds every figure itself from
`priced_value_book` frames:
- `_priced_single(df, root_reason)` -- ONE date (LTD, Trading): sum over `reason==""`
  rows only. Visible caption "excludes N of M trades unpriced", tooltip breakdown by
  (product, reason-tag) via `_unpriced_breakdown`/`_reason_tag` (regex-extracts the
  missing mark_type straight out of `value_book`'s own reason text, e.g. "no PREMIUM"
  -- never invents a reason). All-unpriced (non-empty book) keeps the old single "n/a"
  card; empty book is `0.0`/available.
- `_priced_diff(df_a, df_b, root_reason, ref_label)` -- TWO dates (Daily/5d/MTD/YTD/
  Previous day): same partial-sum idea, PLUS excludes any trade present in both books
  but priced on only one of the two ("blocked" -- would otherwise fake a one-period
  jump the size of its whole LTD). A trade that simply didn't exist yet on the
  reference date (new since then) is NOT excluded -- its full value flows through,
  same as ordinary trading P&L entering the book.

**Verified live** against a throwaway DB copy with synthetic marks for the FX/futures
scope only (3 IRS trades stuck behind the stale-view bug, see
[[stale-marks-official-view-2026-09-17]]): LTD/YTD correctly showed "excludes 3 of 237"
with a real dollar figure; Daily/5d/MTD showed larger exclusion counts (their reference
dates had no marks at all); "Previous day" correctly fell back to the old single n/a
card (its reference date had ZERO priced trades, nothing to sum).

**Explicitly NOT changed** (per the instruction, "aggregation-only"): `engine/pnl/
valuation.py`'s per-trade arithmetic, and `engine/pnl/ledger.py` (not this agent's
file -- other callers of `ledger.period_pnl`/`ltd` keep the old poison-to-NaN
behaviour unless their own owners opt in).

**Still poisons, reported not edited** (files outside this lane): `ui/tabs/blotter*.py`'s
own P&L strips go through `ui.tabs.blotter_pricing`'s `_sum_pnl`/`_priced_sum_for_ids`/
`row_scoped_headline`, all of which still do `if df["pnl_usd"].isna().any(): return NaN`
-- same poison rule, same fix shape needed (`_priced_single`/`_priced_diff`-equivalent)
if the blotter agent wants parity. The header's own collapsible LTD line chart
(`_build_chart`/`_cached_ltd`) also still poisons per-day -- explicitly out of scope
("the headline cards" only).
