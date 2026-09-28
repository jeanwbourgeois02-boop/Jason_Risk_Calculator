---
name: review-findings-c14-c15-c17-2026-09-28
description: 2026-09-28 review of C14 (LME freeze at prompt - 2 LME days), C15 (FUTURE out of _DELTA_SQL, LME USD leg in), C17 (drop INTERP-converted frozen rows); one red test in test_valuation.py, and the open-row / frozen-row LME jump
metadata:
  type: project
---

Review of the working-tree diff (engine/ladder/ladder.py, engine/lme/, engine/pnl/ledger.py + lane tests) for user decisions C14 / C15 / C17 of 2026-09-28. No criticals.

**Findings**
- W-1: `tests/test_valuation.py::test_a_settled_unfrozen_lme_ticket_shows_the_settlement_price_the_ledger_freezes` (line ~890) still pins the pre-C14 rule (last cash on or before the prompt) and FAILS against the new `settlement_price`. pnl-valuation's test was not re-pinned (test_valuation.py untouched in the tree). The lme-forwards Handoff must name pnl-valuation as a consumer of `settlement_price`'s changed semantics.
- W-2 (P&L rule, needs user yes): with C14 the LME ticket's LTD path is cash(P-2) on P-2, cash(P-1) on P-1, cash(P) on P (open row via `_curve_interp`: prompt before the cash pillar -> "nearest, no earlier pillar" = that day's cash), then the frozen cash(P-2) from P+1 (status SETTLED only when settle_date < as_of). A two-day round trip and a spurious Daily on P+1. Recommended: value the ticket at `settlement_price` once as_of > freeze_date (it has left the curve), status still OPEN until the prompt so the ladder's cash date is untouched.
- W-3: C17 rows go in `refrozen` with `pnl_to` None; `live.ledger_block` counts them in `refrozen_count` and `refrozen_summary` says "re-frozen at the close" for a row that was dropped. `ui/tabs/market_data.py::usd_words(None)` renders "n/a" so nothing breaks, but the sentence misdescribes; bbg-live and ui-market-data are consumers to brief.

**Verified clean**: signs; `_lme_freeze` S = 1 via `usd_per_quote('USD')`; `settlement_price` reads marks_official only, `as_of_date <= freeze_date`, never a later price; `_estimated_conversion` only fires on `_Unrealisable` + FUTURE_PRODUCTS + FUTURE_PX + "(INTERP" in the stored note, and today's `_future_freeze` writes exact rows only (`usd_per_quote_on_or_before` reads `_last_official_on_or_before`, no `_mark_near`), so no exact row can be mistaken for an estimate; `_DELTA_SQL` keeps `>`; golden book diff touches `delta_per_ccy` rows only (8 hunks).

**How to apply**: when C14-style freeze-date changes land, always run `tests/test_valuation.py` too: `_frozen_row` delegates to `engine.lme.settlement_price`, so a change there crosses lanes silently.
