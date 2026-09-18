---
name: expiry-day-intrinsic-mark-2026-09-18
description: Expiry-day intrinsic PREMIUM + catch-up in engine/options/store.py -- the conventions chosen, what the ledger/live pull were verified to do, and the limits deliberately left open.
metadata:
  type: project
---

Landed 2026-09-18 (user approved the same day), ahead of five tickets expiring Tue 22 /
Wed 23 Sep 2026. Full text: `store.py` docstring "Expiry day", `pricer.price_fx_at_expiry`.

- **What:** `as_of == expiry` writes the PAYOFF at the pair's official SPOT of that date,
  same unit as every PREMIUM; `as_of > expiry` still skips; `price_all_and_store` catches
  up a missed expiry day (marks DATED THE EXPIRY DATE, once, never rewritten, stale
  `realised_pnl` rows with `spot_as_of_date < expiry` dropped in the same transaction).
  **Why:** the ledger froze the T-1 MODEL premium: ~EUR 45-50k of time value per 35M ATM
  ticket locked into realised P&L and the last day's spot move lost.

- **Verified read-only, do not re-derive:** `engine/pnl/ledger.py::_OPEN_OPTION_SQL` uses
  `settle_date < :as_of` (freezes only once expiry day is OVER) -- no ledger change was
  needed; `live.pull_once` runs rates -> vol -> options -> `realise_settled`; on expiry
  day `live._OPEN_OPTION_PAIRS_SQL` (`expiry_date >= as_of`) still requests the pair's SPOT;
  the ledger converts at `usd_per_quote(base_ccy, <mark's date>)`, EXACT date, so the
  base->USD SPOT must exist on the expiry date itself.

- **Convention decisions:** "in the money" is STRICT (QuantLib CashOrNothingPayoff /
  PlainVanillaPayoff return 0 at S == K; vendored docstrings say S > K / S < K).
  DELTA = payoff's own d(PREMIUM x S)/dS: +1 ITM call, -1 ITM put, 0 otherwise. A
  BASE-payout digital ITM is +1 for put AND call (one base unit about to be received is
  worth S in quote ccy; DELTA_PA = 0) -- the coordinator's brief said "0 for a digital";
  I kept +1 because it is the limit of the T-1 model delta (tested) and the documented
  unit; flagged for a decision, no digital expires before 2026-11-19. One line to flip in
  `price_fx_at_expiry`.

- **Gaps reported, not mine to fix:** `live.build_requests` never requests the base->USD
  (or quote->USD) SPOT for an OPTION whose base is not USD unless some FX leg needs it
  (`_cross_usd_legs` filters `asset_class = 'FX'`); `backfill.traded_pairs` likewise has
  no option-only pairs, so the catch-up cannot fire for a pair held only through options.
  In the real book EURUSD/EURSEK/USDSEK/USDJPY are all covered by open forwards.

- **Known limits documented, deliberately unsolved:** cut time (sample rows: 10:00 NY on
  six, 15:00 TYO on one USDJPY digital, one 'NONE' location; app day = NEW YORK date, so
  an Asia-morning pull still rewrites the expiry date's mark after the NY close);
  delivery double count if an exercised option is booked as a spot trade at the strike.
  See [[premium-delta-conventions]].
