---
name: review-findings-pnl
description: Status of findings from the 2026-09-14 reviews of engine/pnl/pnl.py (ltd_per_trade) and ui/app.py; C-1 matured-trade test closed on second pass, items 2-6 still open
metadata:
  type: project
---

First review on 2026-09-14 of pnl-engine's drop (engine/pnl/pnl.py, tests/test_pnl.py) and ui-shell's drop (ui/app.py, tests/test_ui.py). 17/17 tests pass. P&L maths, signs, spot conversion and marks_official read are correct; ownership clean.

**Open items to re-check on the next engine/pnl drop:**
1. CLOSED 2026-09-14 second pass: `test_matured_and_same_day_trades_excluded_only_future_settle_kept` inserts as_of-1 / as_of / as_of+1 USDJPY trades with official FWD_OUTRIGHT marks for all three settle dates and asserts `trade_id == ["FUTURE"]`; kills both `>=` and filter-removed mutants. (Real file still has no matured legs, see [[bnp-file-facts]].)
2. No synthetic test that `source=None` ignores a BNP_BVAL row when both sources exist for the same key; only the real-file test covers it (skips without the raw file).
3. Grep guard `/\s*(mark|m|spot)\b` does not match `/ trades["mark"]` (the pandas form this module would actually use) and is evaded by renaming (module itself renamed its division to `pair_rate`).
4. `_TRADE_SQL` takes settle_date from `leg_no = 1` only; FX_SPOT trades and FX_SWAP packages (near/far legs, different settle_dates) are out of scope and silently excluded.
5. Cross pairs whose quote ccy is market-quoted XXXUSD (EURGBP, AUDNZD) get NaN because only `USD<quote>` is looked up.
6. Synthetic tests use one settle_date per pair, so per-settle-date marking (must-not #4) is only covered by the real file's USDKRW (2 value dates).

**UI (ui/app.py):** summary is computed once at `create_app`, not per page load; placeholder text not asserted in tests; `MarkRow` unused import in test_pnl.py.

**Why:** the parent agent wants explicit closed/open status per finding on re-review.
**How to apply:** on the next engine/pnl review, check items 2-6 first, then re-run the real-file reconciliation. Related: [[review-findings-ladder]], [[review-findings-bloomberg]].
