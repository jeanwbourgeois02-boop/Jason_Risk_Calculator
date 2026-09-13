---
name: review-findings-ladder
description: Status of findings from the 2026-09-13 first review of engine/ladder (ladder.py, tests/test_ladder.py); what to re-check on the next drop
metadata:
  type: project
---

First review pass on 2026-09-13 of cash-ladder's drop (engine/ladder/ladder.py, __init__.py, tests/test_ladder.py). 41/41 tests pass. No CRITICAL findings: delta SQL is byte-identical to CLAUDE.md (minus trailing `;`), all marks reads go through `marks_official`, no forward-outright conversion, no `positions.fx_to_usd`, ownership clean.

**Open WARNINGs to re-check on the next engine/ladder drop:**
1. `_CASH_POSITION_SQL` does not filter `positions.source` -> BNP + CALC cash rows for the same day would both appear (verified: two 100.0 rows). Also does not GROUP BY ccy, so the real file yields 6 separate `CASH-USD` rows despite the docstring claiming "one row per (ccy, settle_date, kind)".
2. `_SPOT_SQL` does not pin `settle_date = as_of_date` for SPOT; two SPOT rows for one pair on different settle_dates are resolved by an unstable sort + drop_duplicates (arbitrary winner).
3. `spot_table` raises ZeroDivisionError on a 0.0 SPOT value (plain float division on a USDXXX pair).
4. Test gaps: no synthetic matured-leg (`settle_date < as_of`) exclusion test (real-file version is vacuous, see [[bnp-file-facts]]); no test that CASH rows carry `positions.quantity`; "prefers direct quote" in a test name is not asserted; `test_offsetting_legs_net_to_zero_or_absent` has a no-op branch.

**Why:** the parent agent wants explicit closed/open status per finding on re-review; do not re-derive.
**How to apply:** on the next engine/ladder review, check items 1-4 first, then diff `_DELTA_SQL` against CLAUDE.md again (`sed -n '/^WITH d AS/,/^SELECT ccy, SUM/p'` on both files). Related: [[review-findings-ingest]], [[windows-bash-paths]].
