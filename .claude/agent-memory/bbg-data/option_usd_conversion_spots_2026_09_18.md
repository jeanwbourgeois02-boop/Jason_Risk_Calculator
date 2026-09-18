---
name: option-usd-conversion-spots-2026-09-18
description: FX options now drive their own base->USD / quote->USD SPOT requests (live, backfill, inventory); historical option needs are SPOT-only and ignore the realised filter; why
metadata:
  type: project
---

FX options request the SPOT of their own USD-conversion pairs (EURSEK option -> EURUSD, USDSEK), live and in the historical backfill, and the inventory lists them by name (2026-09-18).

**Why:** an option's value/P&L is in the BASE currency and converts base->USD at spot (the user confirmed the EURSEK digital pays EUR and "needs to be converted back to dollars"); its vega/theta/rho go quote->USD. Only forwards used to drive conversion-spot requests, so the digital (expiry 2026-11-25) would have gone "no SPOT for USD conversion of EUR" the day the last EUR forward settled. Nothing was broken in the reference book: on every business day 2026-08-17..2026-11-30 its request list is unchanged (forwards / cross legs / the option's own pair already cover every conversion pair).

**How to apply:**
- One orientation table only: `live._usd_pair_name`. `live._usd_pair_rows` (shared by `_cross_usd_legs` and `_option_usd_legs`) and `live.option_spot_pair_names(base, quote)` are the only ways a currency becomes a USD pair; backfill imports them. USDJPY yields just `['USDJPY']`, never `USDUSD`.
- The readers are orientation-agnostic: `engine/pnl/valuation.usd_per_quote` tries `USD<ccy>` then `<ccy>USD`; `engine/options/portfolio._quote_ccy_to_usd` tries `<ccy>USD` then `USD<ccy>`; the ledger converts an option at the base->USD spot of the PREMIUM mark's own date (`m_day`), which is why conversion closes are backfilled for every open day, not just expiry.
- `historical=True` (`live.option_needed_marks`, `inventory._needed_marks`, used by `close_completeness` and the backfill loop): for options SPOT only, NO FWD_OUTRIGHT at expiry, and NO `realised_pnl` filter. The filter must not apply to past closes: engine/options' expiry-day catch-up re-freezes an option already frozen from an older premium, but only once the expiry date's closing SPOT is on file. The FWD_OUTRIGHT was dropped from past closes because nothing prices an option on a past date (no historical vol) and an option-only pair has no curve history, so it left such days incomplete and re-requested from Bloomberg every 2-minute cycle forever.
- Live SQL (`_OPEN_OPTION_PAIRS_SQL`) uses `expiry_date >= as_of`: expiry day is included, as the expiry-day payoff mark needs.
- `backfill.backfill` now calls `live._ensure_fx_instruments` itself (for cross conversion pairs too): the live pull only creates pair rows for what is open TODAY, so a cross or option that ended before the PC first connected had closes nobody could write. See [[historical-backfill-fwd-future-2026-09-18]] and [[live-pull-boundary-cases-2026-09-17]].
- Test trick for "before/after" on the real sample: monkeypatch `live._option_usd_legs` to return `[]` to get the pre-change request list.
