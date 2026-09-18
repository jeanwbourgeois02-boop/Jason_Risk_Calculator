---
name: review-findings-2026-09-18-guards-digitals-expiry-irs
description: Review of commit 184d56d (realised_pnl named INSERT + value_book guards, BASE-payout digitals, expiry-day intrinsic mark, IRS direction overrides with in-place mark reversal, strict _num). No criticals; open follow-ups listed.
metadata:
  type: project
---

Reviewed 2026-09-18, commit 184d56d vs parent 9fe6332. Suite 1195 passed. No criticals.
Arithmetic in engine/pnl/valuation.py and ledger.py verified line by line as unchanged
(only float() -> _number and indentation). Digital static replication, touch
inverted-pair chain rule, Greek USD conversions and expiry-day unit algebra all checked
by hand and correct. Tests in the money paths use hand numbers or a closed form coded in
the test, not the implementation against itself.

**Open follow-ups (check these first on the next pass):**
1. Nothing purges DIGITAL / touch QL_OPTIONS_PRICER marks written BEFORE the unit fix
   (they are 1/S too small). A same-date re-pull overwrites; older dates stay and make
   Daily / 5d / MTD jump by about quantity x premium x b2u. An identical terms re-save
   deletes nothing, so the user cannot clear them that way.
2. Blotter rebuilds warn only for a populated-but-not-a-number cell. A BLANK Quantity
   (option notional = |NetInvoice| / Price, futures contracts = Notional / multiplier)
   and blank FX amounts / rate are rebuilt silently. No Price-vs-amounts consistency
   check when all three are present.
3. `_guarded_row` blanks a SETTLED trade whose frozen realised row is healthy when
   trades.price / quantity is text (verified: 30,000 frozen -> NaN). The frozen figure
   does not need the fill.
4. Expiry-day option DELTA: CLAUDE.md's delta SQL and engine/ladder/ladder.py have no
   expiry filter on the option branches; engine/ladder/exposure_adapter.py (the live
   Ladder tab) has `l.settle_date > :as_of`. They agreed only while no DELTA mark
   existed on expiry day; they now differ for one day per option.
5. Expiry-day PREMIUM is "never rewritten" once present, so it is the last live pull's
   spot, not the 17:00 close a later backfill writes for that date. True payoff is at
   the cut (10:00 NY on most tickets). A digital near its strike can be wrong by the
   whole payout. Delivery of an exercised vanilla booked at the strike double counts
   the intrinsic (pre-existing, not introduced here).
6. IRS_SIGN_COLUMNS includes 'Gross Amnt/Principal', a cash amount like NetInvoice
   (which was deliberately excluded). reverse_flipped keys before/after on trade_id,
   marks on instrument_id.

**Data facts verified:** 'Gross Amnt/Principal', 'Current Face', 'Original Face' are
blank on every row of new_sample_trades.csv. Futures NetInvoice = contracts x 50 x price
minus fees on a sell (385,798.16 = 385,800 - 1.84). All 10 IRS rows: Side Buy, unsigned,
NetInvoice 0. Five option tickets expire 2026-09-22 (EURUSD straddle) and 2026-09-23
(three EURSEK, one sold put). Dev risk.db: zero marks, zero realised rows, realised_pnl
in the OLD physical column order (product, mark_type last), digital terms already on
file (USDJPY 152 put x2, EURSEK 11.4 call, payoff DIGITAL).

**Process:** the change set was committed while the review was running (git status
emptied mid-review). Use `git diff <parent> <commit> -- <path>` rather than `git diff HEAD`.
The scratchpad/reviewer directory does not exist by default; run probes in memory with a
python heredoc and `schema.connect()` instead.

Related: [[review-findings-pnl]], [[review-findings-ladder]], [[review-findings-upload-blotter-2026-09-16]].
