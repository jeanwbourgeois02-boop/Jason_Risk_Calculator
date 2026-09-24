---
name: lme-forward-freeze
description: 2026-09-24 user-approved LME_FWD freeze: FX rule on the root id ('LME:CA'), last official cash price (SPOT) on or before the prompt via engine.lme.settlement_price, S = 1, mark_type SPOT, note "cash price dated <d>"
metadata:
  type: project
---

LME_FWD is in `valuation.FX_PRODUCTS` (pnl-valuation, 2026-09-24), so `_OPEN_FX_SQL` selects it;
`_fx_or_lme_freeze` dispatches it to `_lme_freeze`, which reads `engine.lme.settlement_price(conn,
root, prompt)` -- the same lookup valuation's provisional `_frozen_row` uses (it also requires
`settle_date = as_of_date` on the SPOT row, which `_last_on_or_before` does not), so frozen =
provisional by construction. S = `usd_per_quote('USD')` = 1. Columns as FX: local_amount = tonnes,
usd_entry_amount = tonnes x fill, spot_usd_per_local = cash price, spot_as_of_date = its date,
mark_type SPOT, currency USD, product LME_FWD. Note ALWAYS "cash price dated <d>", plus
" (last before settlement)" when earlier than the prompt (FX's own note is '' on the day).
Unrealisable reason: "no official cash price (SPOT) for <root> on or before its prompt <d>".

**Why:** user decision 2026-09-24, CLAUDE.md "P&L conventions -> LME forwards"; housekeeper asked for
the "cash price" wording.

**How to apply:** re-freeze is the generic path (why "SPOT <d> mark a -> b ..." or "SPOT dated X
replaced the one dated Y"). Golden book (in-flight sample 2026-09-24) has an LME:NI ticket and a
CMDTY_OPTION (GCQ26C 3300) with no marks, so they now show under `realised.unrealisable` there;
that is infra's synthetic-marks gap, not a ledger bug.

Related: [[non-usd-future-freeze]]
