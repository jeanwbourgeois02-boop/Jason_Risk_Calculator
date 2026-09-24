---
name: review-findings-lme-forwards-2026-09-24
description: 2026-09-24 review of LME_FWD P&L (valuation FX path + LME cash-date pillar, ledger _lme_freeze via engine.lme.settlement_price): no criticals; open doc drift, bad-value test gap, no LME mark writer yet
metadata:
  type: project
---

Change (uncommitted on a876051): LME_FWD joins valuation.FX_PRODUCTS; `_day_pillars` puts the metal SPOT at `engine.lme.cash_date` when `_is_lme(pair)` ("LME:" prefix + root in contracts.csv metals); `_frozen_row` and ledger `_lme_freeze` both freeze at `engine.lme.settlement_price` (last official SPOT, settle_date = as_of_date, as_of_date <= prompt), S = 1, mark_type SPOT.

Verified by probe (in-memory schema.connect + tests.test_valuation helpers): signs, INTERP labels (between / before cash / extrapolated / neighbour curves), provisional == frozen exactly (0.0), refreeze only on an official cash change on/before prompt, USDCNH pillar untouched, no LTD jump at the prompt (open on prompt day already reads the cash price).

Open (relayed, not fixed):
- W: CLAUDE.md hard rule 2 still calls calendar.spot_date "the app's one rule"; LME uses the LME cash date (session owns CLAUDE.md).
- W: no P&L-level test of a non-number LME cash price; engine/lme/curve.py raises plain ValueError (not _BadValue), so the settled row is kept out of the fill only via reference._NEVER_FILLED's "could not be valued" string.
- W (cross-lane): nothing writes LME marks yet; they must be SPOT/FWD_OUTRIGHT under BBG_BFXFORWARD keyed settle = as_of, or they never reach marks_official (BBG_BDH/BDP ignored).
- S: `_is_lme` keyed on contracts.csv, so a root leaving the universe prices open at the FX spot-date pillar but blanks settled.

**How to apply:** marks PK includes source, not snapped_at: a probe cannot insert two snaps of one source/key; UPDATE instead. Related: [[review-findings-c9-c10-c12-2026-09-24]], [[review-findings-ndf-fix-2026-09-22]].
