---
name: review-findings-c9-c10-c12-2026-09-24
description: 2026-09-24 review of C9 (future frozen at expiry-date spot), C10 (retired leftovers), C12 (CMDTY_OPTION) in valuation/ledger; no criticals; CLAUDE.md drift, kept INTERP rows, pair precedence
metadata:
  type: project
---

Change (uncommitted on a876051): `valuation.usd_per_quote_on_or_before` (exact marks_official SPOT <= expiry, USD<ccy> inverted first, then <ccy>USD), FUTURE_PRODUCTS = FUTURE + EQ_OPTION + CMDTY_OPTION, RETIRED_PRODUCTS (IRS/SWAPTION/CAP_FLOOR) shown blank or at their frozen realised row, ledger `_retired_row` also keeps NDF SPOT-substitute rows by note marker.

Verified: golden book 0 diffs on an isolated `git archive HEAD` copy + the 4 files (so FX, FX options, closed-out, USD futures unmoved); HEAD's own test pins fail only on wording, C9 and C10; provisional == frozen to ~1e-11 in 5 spot layouts; bad spot text reported, never filled; NDF markers never appear in today's FX notes (present-spot was NDF-only, commit 1924220; "(last before fixing)" rows were NDF_FIX anyway).

Open (relayed, not fixed):
- W: CLAUDE.md line ~216 (realised_pnl.usd_entry_amount comment) and "Settled trades are frozen" still say "spot of that price's date"; P&L conventions say expiry date (session owns CLAUDE.md).
- W: a row frozen under the old rule at an INTERP conversion, with no exact spot <= expiry now, is `kept` with that estimated figure (not blank like a fresh trade).
- N: usd_per_quote_on_or_before takes an old USD<ccy> over a newer <ccy>USD on or before expiry (probe: 09-01 USDCNY beat 09-15 CNYUSD).
- N: FX / FX-option freezes still convert via near-marks `usd_per_quote(m_day)` (can be INTERP), pre-existing.
- Closes my earlier non-USD-futures W (S(m_day) vs S(expiry) LTD jump) when a spot exists on expiry.

**How to apply:** the working tree was shared with other lanes mid-edit (blotter.py NameError); review P&L diffs in an isolated archive copy (HEAD + the diff's files) and run HEAD's own tests against the new engine to see which pins moved. Related: [[review-findings-nonusd-futures-2026-09-24]], [[review-findings-phase2-pnl-removal-2026-09-24]].
