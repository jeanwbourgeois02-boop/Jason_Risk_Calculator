---
name: equity-index-removed-listed-path-kept-2026-09-24
description: Phase 2 removal (user yes 2026-09-24) took EQ_OPTION / the Equity group out of ui/tabs/options.py; the generic listed-option display sits behind LISTED_OPTION_PRODUCTS (empty) for Phase 5 options on commodity futures; tests now read data/sample/blotter_sample.csv
metadata:
  type: project
---

The equity index (SPX listed options, EQ_OPTION) left the app on 2026-09-24 (Commodity conversion plan, Phase 2; the user approved the macro products leaving). The Options sub-tab now lists `option_products()` = FX_OPTION, CMDTY_OPTION plus `LISTED_OPTION_PRODUCTS`, and its asset-class groups are FX and Commodity only (no Equity row).

**Why:** the app is being converted to Jason's commodity book; FX options stay dormant but kept, and the listed-option display (value = Bloomberg's own price, official FUTURE_PX on the option's instrument, x multiplier; blank Greeks read "Greeks not calculated: <why>") serves Phase 5's options on commodity futures.

**How to apply:**
- SUPERSEDED 2026-09-24: `LISTED_OPTION_PRODUCTS` is now ("CMDTY_OPTION",), see [[options-on-futures-phase5-2026-09-24]].
- Every product filter in the module goes through `_products_in()` (read at call time), never a literal list.
- Sample tests read `data/sample/blotter_sample.csv` (synthetic, 5 FX options: one with no strike, `USDJPY111926P-500043`, trade 910000043; a closed-out pair 910000044 / 910000045, EURUSD 1.15 put). `data/raw/new_sample_trades.csv` is the macro trader's file and must not be read by tests.
- `tests/test_listed_options.py` (listed-options-pricer's) still had an EQ_OPTION `_leg_rows` test at the time; it goes red once the tab stops reading EQ_OPTION, reported in the Handoff for that lane to delete.

Related: [[options-closed-out-by-status-2026-09-22]], [[options-note-precedence-and-pull-status-2026-09-22]].
