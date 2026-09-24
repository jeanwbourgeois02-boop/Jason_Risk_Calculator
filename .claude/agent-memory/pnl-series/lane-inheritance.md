---
name: lane-inheritance
description: pnl-series was split out of pnl-engine on 2026-09-24; which predecessor notes still apply and which tests in the shared tests/test_pnl.py are NOT mine
metadata:
  type: project
---

pnl-series owns reference.py, aggregate.py, fx_blotter.py, stress.py of engine/pnl, and
tests/test_pnl.py (shared) + tests/test_aggregate.py. Predecessor notes live in
`.claude/agent-memory/pnl-engine/` (reference-date-step-back, data_quirks are the relevant
ones for this lane; ledger / NDF notes belong to pnl-ledger / pnl-valuation now).

**Why:** tests/test_pnl.py still holds pnl-valuation's older `test_value_book_*` tests
(the `_vb_conn` / `_vb_mark` fixtures are shared: my reference / fill tests use them too).
When pnl-valuation removes a product, its tests in MY file go red and I must not delete
them: that is a Request to pnl-valuation, sequenced by the housekeeper (seen 2026-09-24,
Phase 2: 8 IRS / NDF value_book tests red after pnl-valuation dropped IRS / NDF).

**How to apply:** my tests in test_pnl.py are the `test_fx_blotter_*`, `test_stress_*`,
`test_reference_*` and `test_fill_*` ones. Everything `test_value_book_*` / `test_day_pillars*`
/ `test_near_marks_*` is pnl-valuation's. reference.py / aggregate.py / fx_blotter.py hold no
product-specific branch (checked 2026-09-24), so product removals upstream need no code change here.

Related: [[stress-api-2026-09-24]]
