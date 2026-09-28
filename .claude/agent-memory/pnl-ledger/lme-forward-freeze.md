---
name: lme-forward-freeze
description: LME_FWD freeze (2026-09-24, C14 2026-09-28): FX rule on the root id ('LME:CA'), official cash price (SPOT) on or before freeze_date(prompt) = prompt - 2 LME business days via engine.lme.settlement_price, S = 1, mark_type SPOT, note "cash price of <d>, the day the prompt became cash"
metadata:
  type: project
---

LME_FWD is in `valuation.FX_PRODUCTS` (pnl-valuation, 2026-09-24), so `_OPEN_FX_SQL` selects it;
`_fx_or_lme_freeze` dispatches it to `_lme_freeze`, which reads `engine.lme.settlement_price(conn,
root, prompt)` -- the same lookup valuation's provisional `_frozen_row` uses (it also requires
`settle_date = as_of_date` on the SPOT row, which `_last_on_or_before` does not), so frozen =
provisional by construction. The ledger passes the PROMPT; `settlement_price` applies the C14 rule
itself (user decision 2026-09-28): the cash price of `engine.lme.freeze_date(prompt)` = prompt - 2
LME business days (the day the prompt became cash), else the last before it, never the prompt-day
price (which is for delivery two days later). The ledger only imports `freeze_date` for wording.
S = `usd_per_quote('USD')` = 1. Columns as FX: local_amount = tonnes, usd_entry_amount = tonnes x
fill, spot_usd_per_local = cash price, spot_as_of_date = its date, mark_type SPOT, currency USD,
product LME_FWD.
Note: "cash price of <d>, the day the prompt became cash" when d == freeze_date, else
"cash price dated <d> (last before the prompt became cash on <freeze_date>)" (lme-forwards' suggested
wording, 2026-09-28; before C14 it was "cash price dated <d>" + " (last before settlement)").
Unrealisable reason: "no official cash price (SPOT) for <root> on or before <freeze_date>, the day its
prompt <prompt> became cash".

**Why:** user decisions 2026-09-24 (CLAUDE.md "P&L conventions -> LME forwards") and 2026-09-28 (C14,
the stricter rule: the ticket's own outright on its last day on the curve).

**How to apply:** re-freeze is the generic path (why "SPOT <d> mark a -> b ..." or "SPOT dated X
replaced the one dated Y"). `tests/test_ledger.py::_lme_db` pins prompts 09-16 (cash day Mon 09-14)
and 09-13 (a Sunday; cash day Thu 09-10, priced from 09-09). A Sunday prompt is not a valid LME
prompt; the fixture keeps it only to exercise the "last before" branch.

Related: [[non-usd-future-freeze]]
