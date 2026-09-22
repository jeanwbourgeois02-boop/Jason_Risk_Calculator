---
name: review-findings-ndf-fix-2026-09-22
description: 2026-09-22 reviews of engine/pnl NDF work (exact-day NDF_FIX, then the 4-item pass: calendar.spot_date pillar, present-spot retired, NDF converted at 1/FIX, ledger.purge_superseded re-freeze). Pass 1 items 1-3 closed by pass 2; open: CLAUDE.md drift, refrozen list carries no before/after, cross conversion via _mark_near moves frozen rows
metadata:
  type: project
---

**Pass 2 (2026-09-22, uncommitted diff vs HEAD 38d147d; slice test_valuation/test_ledger/test_pnl/
test_calendar/test_ndf_present_spot = 121 passed).** No critical. Verified: NDF PnL_USD = Q x (FIX - f) / FIX
(short USDBRL -10M, f 5.20, FIX 5.22 -> -38,314, correct sign); non-NDF freeze arithmetic identical to HEAD
line by line; nothing writes marks; all reads marks_official; gating is leg_no=1 settle_date < as_of for every
product (FX_SWAP is two trade_ids of 2 legs each, so leg_no=1 is the trade's only value date); purge+refreeze
are the same pure functions in one transaction, fresh figures independent of as_of, no flip-flop (probe: 2nd
call and a past-day call leave the row).

**Closed by pass 2:** pass-1 items 1 (`_frozen_row` NDF branch), 2 (purge without refreeze on a past-day
call: `purge_superseded` now gates on settle_date < as_of), 3 (SPOT-substitute drift: ledger reads
`ndf_fixed_valuation` too), 4 (present-spot path gone).

**Open after pass 2 (check first next time):**
1. CLAUDE.md not updated with the code: "Mark date" still says NDF converted at the fixing date's SPOT;
   "Settled trades are frozen" (~line 361) and "Marks snapshot" (~line 248) still describe the present-spot
   exception and `realise_settled(ndf_present_spot=True)`; the general re-freeze rule is nowhere in it.
2. `realise_settled()['refrozen']` is a bare id list: a re-freeze to a WORSE figure (a mark deleted with an
   earlier close on file -> re-frozen at the earlier close, probe 30,000 -> 50,000; a downstream re-price
   overwriting an expiry-day payoff) is by rule and leaves no before/after anywhere.
3. `_fx_freeze` converts a cross at `usd_per_quote` = `_mark_near` (time interpolation), so a LATER
   USD-conversion close moves a frozen cross row once (probe EURSEK 10,000 -> 9,803.92) and `_s_src` is
   discarded, so the note never names the INTERP conversion. The pair's own SPOT stays `_last_on_or_before`.
4. Stored identity `pnl_usd = local_amount * spot_usd_per_local - usd_entry_amount` is false for NDF rows
   (spot_usd_per_local = 1/FIX, by spec); no reader recomputes it (valuation._settled_fx_row reads pnl_usd).
5. Two spot-date rules remain: engine/ladder/usd_marks.spot_date (T+2 weekdays, no holidays) for the
   Ladder's USD column; data/bloomberg/fwd_curve.spot_date_for is a verbatim copy of calendar.spot_date.
6. Untested: quote-USD NDF, cross NDF and m == 0 branches of `ndf_fixed_valuation`; the settled-NDF
   "no close at all" reason is the generic `_provisional` one, the ledger's is specific.

**Data facts:** snapshot 2026-09-21 USDBRL: SPOT 5.1089, FWD_OUTRIGHT 09-22 5.1117, 09-23 5.109,
09-24 5.109 (SP and SN identical, both BBG_BFXFORWARD).

**Technique:** scratchpad script importing `_insert_instruments/_insert_trade/_insert_legs/_insert_marks`
from tests.test_ledger (sys.path insert of the repo root), `realise_settled` twice plus a past-day call,
prints the realised_pnl row each time: shows any refreeze drift or flip-flop in one run.

Related: [[review-findings-pnl]], [[review-findings-2026-09-18-guards-digitals-expiry-irs]].
