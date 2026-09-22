---
name: ndf-fix-exact-day
description: 2026-09-22 approved rule that an NDF's exit price is the NDF_FIX of its own fixing date only (never a neighbouring day's fix, never estimated), the SPOT substitute, the one-function rule for open / provisional / frozen NDF figures, the as_of gate on the ledger purges, what the residual on a no-pull day looks like, and the golden-book consequence
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
- ONE function for an NDF from its fixing date on: `valuation.ndf_fixed_valuation(conn, pair,
  quote_ccy, quantity, fill, fixed_on) -> (row, how, fix_used)`. `_open_fx_row` (fixed branch),
  `_frozen_row` (settled, no realised row yet) and `ledger.realise_settled` all read it, and the
  ledger stores `row["pnl_usd"]` itself, so open / provisional / frozen are the same float.
  Reviewer 2026-09-22 caught the split: `_frozen_row` had no NDF branch (read the VALUE date's
  spot: 28,037 before the ledger ran, 3,810 after) and the ledger's substitute was the earlier
  close alone while the Blotter's was the interpolation between closes. Any new NDF path must
  call this function, never re-derive.
- Present-spot rule (user 2026-09-21) is the one sub-case still outside that function, kept on
  the coordinator's instruction: an NDF with neither a fix nor an official SPOT on or before
  its fixing date (`ndf_fixing_marks_on_file` False). There the open fixed branch (`ndf_fix`
  carries the nearest LATER close) and the settled figure (present spot = latest close on or
  before as_of, or blank on the strict call) still differ at settlement. Flagged to the user
  2026-09-22 as an arithmetic decision; do not resolve it without their yes.
- On a no-pull fixing date the ticket carries the previous close's SPOT. Its P&L then differs
  from the previous day's by the forward points it drops at fixing (value-date outright vs
  spot of the previous close), NOT by zero: on 2026-09-22 that residual was +212.68 over 11
  USDBRL tickets (5.109 outright vs 5.1089 spot; USDIDR had outright = spot, so 0). Do not
  "fix" that residual; it is the approved arithmetic. Say so when a brief expects exactly 0.
- Ledger purges (`purge_superseded_present_spot`, `purge_superseded_ndf_fix`) take `as_of` and
  touch only `settle_date < as_of`: the refreeze (`_OPEN_FX_SQL`) covers those alone, so a
  past-day call from the backfill must never drop a row it cannot freeze again (reviewer W1).
  Result key `refrozen`. `realised_pnl.spot_source` holds the MARK's source (BBG_BDH for a
  fix, the INTERP name for an estimated spot), never the conversion spot's.
- Frozen-row notes: "official fixing dated <d> (NDF fixing)"; "spot dated <d> (NDF fixing)"
  for an exact spot; "spot dated <d> (NDF fixing); no official fixing on file: at the spot of
  <d> instead (INTERP: ...)" for an estimate. Several tests pin these verbatim.
- The golden book (`tests/golden/book.json`) pins 43 realised rows at "official fixing dated
  2026-09-11 (last before fixing)" (the neighbouring-fix rule of earlier that day) and more
  rows will move under the interpolated substitute, so `tests/test_golden_book.py` fails until
  the user says yes to a regeneration; never regenerate it myself.
- `tests/test_pnl.py::test_value_book_ndf_exit_price_is_the_official_fixing_of_the_fixing_date`
  and `..._closed_out_option_past_expiry...` (the exact `realise_settled` dict) are the pins
  that move whenever this rule or the result keys change.

Related: [[data-quirks]], [[reference-date-step-back]]
