---
name: irs-ndf-removed
description: 2026-09-24 Phase 2 + C10: no IRS / NDF freeze; old IRS/SWAPTION/CAP_FLOOR, NDF_FIX, PV_USD and NDF SPOT-substitute rows kept under `kept` (told by note); HEAD pin fixture in test_ledger
metadata:
  type: project
---

User approved on 2026-09-24 that rates / IRS and NDFs leave the app (CLAUDE.md "Commodity
conversion plan", Phase 2), and C10 the same day: "an old database's leftovers of the retired
products show as they were frozen (an NDF keeps its figure)". The ledger freezes no IRS /
SWAPTION / CAP_FLOOR (no SQL selects them) and no NDF by the fixing rule; an unfrozen old NDF
freezes like a deliverable forward.

**How to apply:**
- `_retired_row(product, mark_type, note)` -> the `kept` reason, '' otherwise:
  - product in `valuation.RETIRED_PRODUCTS`, or mark_type NDF_FIX / PV_USD:
    "<product> row frozen at <mark_type>: rates swaps and NDFs left the app on 2026-09-24, so it is
    left as frozen until the next upload replaces the book"
  - mark_type SPOT on an FX product whose note carries a retired NDF rule's marker
    (`_RETIRED_NDF_SPOT_NOTES`): "(NDF fixing)" (d0f8ae5..a876051 wrote "spot dated <fix day> (NDF
    fixing)[, converted at that spot]"; bd43604 too), "(last before fixing)" (bd43604..81d96c8),
    "present spot: none on file on or before settlement" (1924220..cc837ec, PRESENT_SPOT_NOTE).
    Reason: "<product> row frozen at SPOT by the NDF fixing rule: ...same tail".
  Today's FX rule never writes those markers ("spot dated <d> (last before settlement)" or '').
- Never recompute such a row by the FX rule: it would move (fixing-date spot / 1/FIX vs value date).
- `_FROZEN_SQL` joins instruments and leg 1: an old retired row with no leg_no 1 is invisible to the
  purge (not dropped, just not listed under kept).
- Proof of "no number moved": `_PIN_ROWS` in tests/test_ledger.py, computed by the ledger at
  e660974 from a `git archive HEAD engine data config` copy in the scratchpad (never git stash in
  the shared tree). Still bit for bit after C9 (every pinned future's spot is dated its expiry).

Related: [[non-usd-future-freeze]]
