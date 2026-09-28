---
name: lme-forwards
description: 2026-09-24 Phase 5: LME_FWD valued on the FX branch (FX_PRODUCTS), cash pillar at engine.lme.cash_date, provisional freeze via engine.lme.settlement_price; ledger inherits through FX_PRODUCTS
metadata:
  type: project
---

- User approved 2026-09-24 (CLAUDE.md "P&L conventions -> LME forwards"): `PnL_USD = tonnes x (m - f)`,
  S = 1, m = official FWD_OUTRIGHT at the prompt else near marks along the day's LME curve; settled =
  last official cash price (SPOT of the root) on or before the prompt.
- Wiring: `LME_PRODUCTS = ("LME_FWD",)` appended to `FX_PRODUCTS`, so `_fx_sql` / `_open_fx_row` value it
  unchanged (Q = trades.quantity = tonnes; quote USD -> identity). `_reported_product` only relabels
  FX_FWD, so LME_FWD is never shown as FX_SPOT. `_day_pillars` places the SPOT at
  `engine.lme.cash_date(as_of)` for `_is_lme(pair)` (prefix 'LME:' + `is_lme_instrument`), FX pairs keep
  `spot_date`. `_frozen_row` has an LME branch first, reading `engine.lme.settlement_price`
  (note "frozen at cash price..."); its plain ValueError on a bad value goes to `_guarded_row`'s reason.
- **Why it matters:** pnl-ledger imports `FX_PRODUCTS`, so adding LME_FWD there made the ledger freeze
  LME tickets through `_fx_freeze` (same figure; its lookup lacks settle_date = as_of_date, equivalent
  under the data contract). Any change to `FX_PRODUCTS` moves the ledger too: say so in the Handoff.
- 2026-09-28 (user yes, reviewer W-2): an OPEN ticket with `as_of > freeze_date(prompt)` (tom, the
  prompt) leaves the curve and takes the C14 freeze figure (`_lme_cash_row`, `settlement_price`),
  status still OPEN so the ladder gets its cash on the prompt; a later day's cash price is a different
  prompt and is never used. So for an LME business-day prompt the "nearest, no earlier pillar" LME
  path is only reachable on the freeze day itself (prompt == cash pillar) or for a weekend prompt.
  Golden proof: the nickel ticket's pinned days (08-14, 09-04 open; 09-18 settled) never hit P-1 / P.
- Test date that separates the two pillar rules: as_of 2026-08-27, FX spot date 2026-08-31 (London bank
  holiday), LME cash date 2026-09-01.
- **How to apply:** golden proof on the working tree = compare value_book rows per trade id against the
  pin (new sample trades show as "new", not "changed"); scratch script pattern in the scratchpad, not
  kept. The golden builder (infra) writes no LME curve or listed-option prices yet.
