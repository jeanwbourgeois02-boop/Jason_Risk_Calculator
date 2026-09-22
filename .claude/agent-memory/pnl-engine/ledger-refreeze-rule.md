---
name: ledger-refreeze-rule
description: 2026-09-22 user-approved ledger rule -- every realised row is re-frozen when the official marks for its date and mark type no longer give the same figure (one generic purge, `purge_superseded`, replacing the NDF / present-spot / close-out special purges); how it is gated, what it compares, what it never touches
metadata:
  type: project
---

`ledger.realise_settled(conn, as_of)` (no other parameters) runs ONE re-freeze rule before
freezing anything: for every `realised_pnl` row with leg settle_date < as_of, recompute the
frozen row by the standard per-product rule (`_fx_freeze`, `_future_freeze`, `_irs_freeze`,
`_option_freeze`, dispatched by `_freeze_for`) and drop it when `(mark_type, spot_as_of_date,
local_amount, usd_entry_amount, spot_usd_per_local, pnl_usd)` differs (`_same_freeze`,
rel 1e-9); the same call freezes it again. Result key `refrozen` is a list of dicts sorted by
trade_id, `{trade_id, product, mark_type, spot_as_of_date, pnl_from, pnl_to, why}` (reviewer
M-2, user yes 2026-09-22; bbg-data's status file and ui-market-data render it, so the keys are a
contract with those lanes); `kept` lists `{trade_id, product, reason}` for rows the rule could
not recompute (reviewer m-2). `purge_superseded` returns the `_Purge(refrozen, kept)` pair.
Approved by the user 2026-09-22 (hard rule 7 satisfied for that change).

**Why:** a trade frozen at a live press (the last pull before the 17:00 NY day roll) kept
that press forever once the backfill replaced the day's row with its 15:00 close; a future
frozen at PX_LAST never took PX_SETTLE; NDF rows frozen at the fixing date's spot had to move
to 1/FIX conversion. Three special-purpose purges (present spot, NDF fix, close-out) were
the same idea written three times.

**How to apply:**
- The freeze functions are the ONLY place the frozen figure is computed; `realise_settled`'s
  open loops just insert what they return. Never re-derive a freeze inline, or the purge and
  the insert can disagree and a row re-freezes on every call (a test pins "the next call
  drops nothing").
- Gating stays: only rows this call can freeze again (settle_date < as_of) are compared, so
  the backfill's past-day calls never drop a row they cannot restore (reviewer W1).
- A row the rule cannot recompute today (`_Unrealisable`, a `_BadValue`, a mark since deleted)
  is left alone: a trade that has a figure is never blanked by the purge.
- Not compared: note, spot_source, frozen_at. Same numbers => untouched, frozen_at kept.
- A NEWER mark after the settle date never matters (the freeze reads on or before settlement).
- Knock-on for other lanes: any test that freezes a row from mark X, then changes X on the
  same date and expects the row to stay, now sees a re-freeze. engine/options' expiry-day
  catch-up (`store.price_all_and_store`) deletes stale frozen rows itself; the ledger would
  now do the same once the PREMIUM row changes.

Related: [[ndf-fix-exact-day]]
