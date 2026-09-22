---
name: ndf-fix-exact-day
description: 2026-09-22 approved NDF rule -- exit price = NDF_FIX of the fixing date only (never a neighbouring day's fix, never estimated), the SPOT substitute, P&L converted at the fix itself (1/FIX), the one-function rule for open / provisional / frozen figures, the present-spot exception retired, the ledger's generic re-freeze rule, and what the residual on a no-pull day looks like
metadata:
  type: project
---

An NDF's exit price is the official `NDF_FIX` dated its fixing date exactly; a fix of another
day is never used and never estimated (`_mark_near` returns None for NDF_FIX past the exact
lookup). With no fix for that date on file, the pair's SPOT of the fixing date stands in,
named as the substitute, itself the near-marks estimate when that day's own is not on file
(a LATER close alone is carried too: "nearest later close, none earlier"). User words relayed
2026-09-22: "each ndf has a unique fix"; approved by the user (hard rule 7 satisfied).

**Why:** on the Mac's imported snapshot, 2026-09-22 had no marks; 21 USDIDR / USDBRL tickets
fixing that day took the 09-17 / 09-14 fixes through the time interpolation and moved Daily
by -126,820 with nothing priced. A fix is a print, not a curve point.

**How to apply:**
- ONE function for an NDF from its fixing date on: `valuation.ndf_fixed_valuation(conn, pair,
  quote_ccy, quantity, fill, fixed_on) -> (row, how, fix_used)`. `_open_fx_row` (fixed branch),
  `_frozen_row` (settled, no realised row yet) and `ledger._fx_freeze` all read it, and the
  ledger stores `row["pnl_usd"]` itself, so open / provisional / frozen are the same float.
  Any new NDF path must call this function, never re-derive.
- USD conversion AT THE FIX (user, 2026-09-22, later the same day): for a USDXXX NDF pair,
  USD per quote unit = 1 / FIX (FIX = the exact-day fix or the SPOT substitute), so
  `PnL_USD = Q x (FIX - f) / FIX`; row spot = 1/FIX, spot_source = the fix's source;
  `realised_pnl.spot_usd_per_local` = 1/FIX (NOT mark x S like a deliverable row: the module
  docstring's `local x spot - entry` identity does not hold for NDF rows, documented there).
  Note wording pinned in several tests: ", converted at the fixing" (fix) / ", converted at
  that spot" (substitute); ledger notes "official fixing dated <d> (NDF fixing), converted at
  the fixing" / "spot dated <d> (NDF fixing), converted at that spot[; <how>]".
- The 2026-09-21 "present spot" exception (an NDF with no close on or before its fixing
  valued at the latest close on file, `ndf_present_spot=True` on the backfill's closing call)
  was RETIRED on 2026-09-22 by the user: no such parameter, no `present_spot_for_ndf`, no
  `PRESENT_SPOT_NOTE`. Such a ticket takes what `ndf_fix` gives (a later close carried, named),
  or is blank with its reason like a deliverable trade. tests/test_ndf_present_spot.py now pins
  the retirement (kept its filename; it is in my lane).
- On a no-pull fixing date the ticket carries the previous close's SPOT. Its P&L then differs
  from the previous day's by the forward points it drops at fixing (value-date outright vs
  spot of the previous close), NOT by zero: on 2026-09-22 that residual was +212.68 over 11
  USDBRL tickets. Do not "fix" that residual; it is the approved arithmetic.
- `tests/golden/book.json` pins the pre-2026-09-22 NDF figures; `tests/test_golden_book.py`
  fails until the user says yes to a regeneration; never regenerate it myself.

Related: [[ledger-refreeze-rule]], [[data-quirks]], [[reference-date-step-back]]
