---
name: review-findings-ladder
description: Status of findings from the 2026-09-13/14 reviews of engine/ladder (ladder.py, views.py, tests/test_ladder.py); what to re-check on the next drop
metadata:
  type: project
---

First pass 2026-09-13 (ladder.py, __init__.py): no CRITICALs, delta SQL byte-identical to CLAUDE.md, all marks reads via marks_official.

Second pass 2026-09-14 (spot_table gained source=None; new views.py ladder_table): still no CRITICALs. 135/135 suite.

**Closed on 2026-09-14 second pass:**
1. `_CASH_POSITION_SQL` now filters `positions.source` (default 'BNP') and GROUPs BY (instrument_id, settle_date); test `test_cash_rows_grouped_by_ccy_settle_date_and_filtered_by_source`.
2. SPOT lookup pins `settle_date = as_of_date`; test `test_spot_table_ignores_marks_not_dated_as_of_settle`.
3. Zero/negative SPOT -> row skipped -> NaN, no ZeroDivisionError; test exists.

**Still open / new WARNINGs to re-check:**
4. `cash_ladder` LEG rows are NOT filtered by trades.trade_date <= as_of and CASH rows come only from `positions` source 'BNP' (hard-coded inside `ladder_table`), so a CALC-positions display path does not exist yet.
5. `ladder_table` real-file tests run with `source='BNP_BVAL'` (marks_official empty on real file); only the synthetic pivot test exercises the official path. No test that `source=None` ignores a BNP_BVAL SPOT row when an official row also exists for the same key (same gap as pnl item 2).
6. Empty ladder returns columns [ccy, total, usd] with no date columns (verified, no crash) -- fine, but UI shows an empty DataTable rather than a message.

**Why:** the parent agent wants explicit closed/open status per finding on re-review; do not re-derive.
**How to apply:** on the next engine/ladder review, check items 4-6 first, then diff `_DELTA_SQL` against CLAUDE.md again. Related: [[review-findings-pnl]], [[windows-bash-paths]].
