---
name: irs-ndf-removed
description: 2026-09-24 Phase 2 removal: the ledger no longer freezes IRS or NDFs; old rows of those kinds are left as frozen and named under kept; the HEAD pin fixture that proves no remaining product moved
metadata:
  type: project
---

User approved on 2026-09-24 that rates / IRS and NDFs leave the app (CLAUDE.md "Commodity
conversion plan", Phase 2). In `engine/pnl/ledger.py` the IRS freeze (PV_USD + CASHFLOW_USD,
`_irs_freeze`, `_OPEN_IRS_SQL`) and the NDF freeze (`_ndf_fixing_day`, the
`valuation.ndf_fixed_valuation` branch of `_fx_freeze`) are gone; `tests/test_ndf_present_spot.py`
was deleted and left the lane.

**Why:** the housekeeper brief said an old database's rows with `mark_type` NDF_FIX or an IRS
product are "left as they are, never recomputed: the upload replaces the book anyway".

**How to apply:**
- `_retired_row(product, mark_type)`: product IRS, or mark_type NDF_FIX / PV_USD -> the row is
  skipped by `purge_superseded` and listed under `kept` with a fixed reason sentence (pinned in
  `test_an_older_databases_swap_and_ndf_fix_rows_are_left_as_they_are_and_named_under_kept`).
  Never recompute such a row by the FX rule: it would move (1/FIX conversion vs spot).
- Gap left on purpose: an old NDF row frozen at the SPOT *substitute* (mark_type SPOT, dated
  the fixing day) is not caught and would be re-frozen at the value date's spot, reported under
  `refrozen`. An unfrozen NDF on an old database now freezes like a deliverable forward.
- A matured IRS trade is simply not frozen any more (not in unrealisable either).
- Proof of "no number moved": `_PIN_ROWS` in tests/test_ledger.py, computed by the ledger at
  e660974 from a `git archive HEAD engine data config` copy in the scratchpad (never git stash in
  the shared tree). Covers USDCNH, EURGBP cross, XAUUSD, USDJPY spot, CNY future, USD future,
  PREMIUM option and a CLOSE_OUT pair; compared with `==`, bit for bit.

Related: [[non-usd-future-freeze]]
