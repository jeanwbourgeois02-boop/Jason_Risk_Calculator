---
name: ndf-fix-exact-day
description: 2026-09-22 approved rule that an NDF's exit price is the NDF_FIX of its own fixing date only (never a neighbouring day's fix, never estimated), the SPOT substitute, what the residual on a no-pull day looks like, and the ledger / golden-book consequences
metadata:
  type: project
---

An NDF's exit price is the official `NDF_FIX` dated its fixing date exactly; a fix of another
day is never used and never estimated (`_mark_near` returns None for NDF_FIX past the exact
lookup). With no fix for that date on file, the pair's SPOT of the fixing date stands in,
named as the substitute, itself the near-marks estimate when that day's own is not on file.
User words relayed 2026-09-22: "each ndf has a unique fix"; approved by the user (hard rule 7
satisfied for that change only).

**Why:** on the Mac's imported snapshot, 2026-09-22 had no marks; 21 USDIDR / USDBRL tickets
fixing that day took the 09-17 / 09-14 fixes through the time interpolation and moved Daily
by -126,820 with nothing priced. A fix is a print, not a curve point.

**How to apply:**
- On a no-pull fixing date the ticket carries the previous close's SPOT. Its P&L then differs
  from the previous day's by the forward points it drops at fixing (value-date outright vs
  spot of the previous close), NOT by zero: on 2026-09-22 that residual was +212.68 over 11
  USDBRL tickets (5.109 outright vs 5.1089 spot; USDIDR had outright = spot, so 0). Do not
  "fix" that residual; it is the approved arithmetic. Say so when a brief expects exactly 0.
- Ledger: `_fix_on_day` (exact day) then SPOT on or before the fixing date; the
  `purge_superseded_ndf_fix` guard drops a SPOT / present-spot row once the exact-day fix is
  on file and any 'NDF_FIX' row whose `spot_as_of_date` is not the fixing date, then the same
  call re-freezes them; result key `refrozen`. `realised_pnl.spot_source` holds the MARK's
  source (BBG_BDH for a fix), never the conversion spot's -- a test asserting the INTERP
  spot name there is wrong.
- The golden book (`tests/golden/book.json`) pins 43 realised rows at "official fixing dated
  2026-09-11 (last before fixing)", written under the neighbouring-fix rule of earlier that
  day, so `tests/test_golden_book.py` fails until the user says yes to a regeneration; never
  regenerate it myself.
- `tests/test_pnl.py::test_value_book_ndf_exit_price_is_the_official_fixing_of_the_fixing_date`
  and `..._closed_out_option_past_expiry...` (the exact `realise_settled` dict) are the pins
  that move whenever this rule or the result keys change.

Related: [[data-quirks]], [[reference-date-step-back]]
