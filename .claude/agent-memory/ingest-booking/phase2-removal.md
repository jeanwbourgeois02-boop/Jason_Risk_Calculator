---
name: phase2-removal
description: Commodity Phase 2 (2026-09-24) removed the FX-swap package rule and the IRS flip from booking; how an FX swap is booked now and why irs_direction.py outlived its removal
metadata:
  type: project
---

User yes 2026-09-24: rates / IRS and the FX-swap package rule leave the app. `swaps.py` and
`tests/test_swaps.py` were deleted; `upload.py` no longer packages forwards or reverses swap marks.

FX_SWAP stays as a product (Jason may roll FX hedges that way), but no path booked one except
the package rule. So `manual.book_fx_swap` was added. It writes the shape the engine values
(`engine/pnl/valuation.py` docstring): TWO MANUAL trades, product FX_SWAP, shared
`package_id = 'SWAP-' || min(trade_id)` (string min, so MANUAL-10 beats MANUAL-9), near legs
FX_NEAR and far legs FX_FAR. **Why:** a single trade with 4 legs, which is what CLAUDE.md's leg-layout line and the
housekeeper's brief describe, would be valued on leg 1 alone by value_book and the ledger. **How to apply:**
if anyone asks for "one trade, 4 legs", point at valuation.py first. `delete_manual_trade`
removes the whole package and returns the ids removed.

`irs_direction.py` was deleted in a second pass the same day. It could not go first because
`blotter.load` imported it at call time, so it had to wait for ingest-parser to drop that import.
Also 2026-09-24: manual.py dropped its NDF branch. Every pair is booked deliverable (is_ndf 0,
settles_cash 1), as the parser writes, and manual.py no longer imports `NDF_CCYS`.
`swap_review` is cleared only if the table exists (`upload.RETIRED_CHILD_TABLES`, `manual._RETIRED_CHILD_TABLES`) so its FK
never blocks a replace on an old database, before or after ingest-schema drops the DDL.

The upload summary counts loaded trades by product (`upload.loaded_breakdown(result.trades)`),
never ParseResult's n_future etc., which count rejected rows too.

The synthetic `data/sample/blotter_sample.csv` loaded 43 trades / 52 legs in Phase 2 (50 / 62 since Phase 5, [[phase5-options-lme]]) with 2 deliberate rejects
(ZCZ6 ambiguous root, QQZ6 unknown), and it holds CLZ6, so a contract-dates test that needs
a MANUAL-only future must use a month the sample does not trade. Related: [[contract-dates]].
