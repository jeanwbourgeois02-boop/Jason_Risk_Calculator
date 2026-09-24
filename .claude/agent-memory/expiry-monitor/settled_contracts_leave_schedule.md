---
name: settled-contracts-leave-schedule
description: A contract whose every trade has a realised_pnl row (settle_date < as_of) leaves expiry_schedule rows for the settled_expired list; partly frozen stays EXPIRED (2026-09-24)
metadata:
  type: project
---

Decided 2026-09-24 (housekeeper, recommended option, after infra's golden book showed CLQ26
EXPIRED forever while the ledger had frozen it SETTLED): a held contract whose every trade
(trade_date <= as_of) has a `realised_pnl` row with `settle_date < as_of` is not a row and not
counted; it is listed once in the result's `settled_expired` (contract_id, lots,
last_trade_date, frozen_at, ...), never an alert. Only partly frozen, or not frozen, past its
event: stays EXPIRED, a real gap.

**Why:** the ledger is the authority on what has left the book; `settle_date < as_of` mirrors the
ledger's own rule so viewing a past day before settlement still shows the contract.
**How to apply:** Phase 5 products (CMDTY_OPTION, LME_FWD) join through `schedule._BUILDERS` and
inherit this rule for free; the golden book pins `expiry_schedule`, so any change there is a
re-pin needing the user's yes. See [[estimated-dates-understate-risk]].
