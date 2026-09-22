---
name: review-findings-ndf-fix-2026-09-22
description: 2026-09-22 review of the exact-day NDF_FIX change (valuation.ndf_fix / _mark_near, ledger.purge_superseded_ndf_fix / _fix_on_day); no critical inside the diff; pre-existing _frozen_row gap for settled NDFs, purge/refreeze as_of gating, ledger-vs-blotter SPOT-substitute drift
metadata:
  type: project
---

Reviewed 2026-09-22 (uncommitted diff of engine/pnl/valuation.py + engine/pnl/ledger.py vs HEAD 0c13389).
Suite slice: tests/test_valuation.py + tests/test_ledger.py = 38 passed. Diff arithmetic verified:
Q x (FIX - f) x S, S = spot of the fixing date via usd_per_quote (never the forward, never the fix),
NDF_FIX exact-day only (`_mark_near` short-circuits, `_neighbour` no longer keys NDF_FIX on own day),
nothing writes marks, all reads via marks_official. Purge/refreeze is stable (refreeze writes
spot_as_of_date = fix_day, which the purge keeps).

**Open findings (check first on the next engine/pnl NDF pass):**
1. `valuation._frozen_row` (FX branch, ~line 743) has NO NDF branch: a settled NDF with no
   realised_pnl row is valued at the last SPOT on or before the VALUE date, never the fix or the
   fixing date. Pre-existing (identical at HEAD), but every blotter upload wipes realised_pnl
   (upload.FULL_REPLACE_CHILD_TABLES), so on the Mac (no Bloomberg, realise_settled only runs at
   marks-import) every settled NDF shows the value-date figure until the next import. Probe:
   fix 5.22 / value-date spot 5.35 / fill 5.20 -> 28,037 shown vs 3,810 frozen.
2. `ledger.purge_superseded_ndf_fix` deletes unconditionally but `_OPEN_FX_SQL` refreezes only
   `settle_date < :as_of`; auto_backfill calls realise_settled(span_end) with a PAST day, so a
   dropped row is not refrozen in that call ('refrozen' lists it, realised 0) and item 1 shows
   meanwhile. Closes at `_freeze_ndfs_at_present_spot(today)` at the end of auto_backfill; stays
   open after a manual `backfill` CLI run or an aborted run. Same latent shape in
   purge_superseded_present_spot.
3. SPOT substitute drift: `ndf_fix` uses `_mark_near` SPOT (both neighbours, midpoint) while the
   ledger's fallback is `_last_on_or_before` SPOT (earlier only). Fixing date with no close but
   both neighbours on file -> Blotter shows midpoint until value date, ledger freezes at the
   earlier close: settlement moves LTD.
4. A non-numeric NDF_FIX on file for a SPOT-frozen NDF is swallowed by the purge's
   `except ValueError: continue` and never reported (the row is healthy, so ndf_fix is not read).
5. tests: test_valuation (c) "P&L stands still" only holds because the previous close has no
   outright for the value date; the real one-pip move (previous close's outright vs carried spot)
   is untested. (CLAUDE.md "Mark date" and "Settled trades are frozen" were already updated to
   exact-day in the working tree at review time; the system-reminder copy can lag the file.)

**Data facts:** snapshot 2026-09-21 USDBRL: SPOT 5.1089, FWD_OUTRIGHT 09-22 5.1117, 09-23 5.109,
09-24 5.109 (SP and SN identical, both BBG_BFXFORWARD). The reported Daily +213 on 09-22 (no pull)
= 11 tickets moving 5.109 -> carried 5.1089 (one pip) => sum Q ~ -10.9M USD (short USD / long BRL).
By the contract that move is right: the fixing-date rule drops the previous close's spot-to-value
carry, named INTERP in each note; it re-marks when the 09-22 close and the PTAX land.

**Technique:** build in-memory NDF books by importing `_ndf_db`, `_brl_fix`, `_insert_marks` from
tests.test_ledger in a scratchpad script (sys.path insert of the repo root); value_book vs
realise_settled side by side shows any freeze/blotter divergence in one run.

Related: [[review-findings-pnl]], [[review-findings-2026-09-18-guards-digitals-expiry-irs]].
