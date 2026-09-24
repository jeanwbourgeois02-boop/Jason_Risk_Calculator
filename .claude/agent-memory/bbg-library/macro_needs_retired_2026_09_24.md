---
name: macro-needs-retired-2026-09-24
description: Phase 2 removal in the Bloomberg library - which kinds left compute (NDF_1M, NDF_FIX, FIXINGS, IRS, DIV_YIELD, UNDERLYING), what stayed, and the retired constants deleted once consumers dropped them
metadata:
  type: project
---

Commodity conversion Phase 2 (user approval 2026-09-24: rates / IRS, NDFs and the equity index leave the app). LIBRARY_VERSION "2026-09-24.2".

- `compute` no longer emits NDF_1M, NDF_FIX (and no longer imports `engine/ladder/ndf.py`), FIXINGS, any IRS row, a listed option's UNDERLYING SPOT, its DIV_YIELD, or its OIS_CURVE. A listed option keeps only its own FUTURE_PX plus a non-USD conversion SPOT (the path Phase 5's options on futures reuse). FX options keep SPOT / FWD / conversions / OIS_CURVE / VOL_SMILE (dormant, not approved for removal).
- I dropped the listed option's OIS_CURVE as well, although the brief named only the UNDERLYING and DIV_YIELD: without the index level and the yield it fed nothing. Phase 5 may need a discount curve again, as a new rule and a version bump.
- The names ROLE_UNDERLYING, NDF_FIX, NDF_1M, DIV_YIELD and DIV_YIELD_FIELDS were kept as a "retired" block until bbg-live and bbg-backfill dropped their reads, then deleted the same day. That order (stop producing, consumers drop the reads, then delete the names) is how a removal runs across lanes without breaking a pull in between.
- HISTORY_INPUT_PRODUCTS and _REALISED_FILTER_PRODUCTS are now ("FX_OPTION",). LIVE_ONLY_KINDS is (CONTRACT_DATES,) and SET_KINDS is (OIS_CURVE, VOL_SMILE).
- inventory.py had no NDF or fixings *counts* as such, only docstring mentions, which are now cleaned. `_option_needed_marks` in inventory.py is dead code (no caller); left for infra.
- Tests now use `data/sample/blotter_sample.csv` (synthetic: commodity futures in USD, CNY, EUR, GBP and JPY; FX forwards; FX options). On this PC `blpapi` imports, so test_live's "no blpapi" availability test fails in this environment, whatever the code.

Related: [[commodity-futures-library-2026-09-24]]
