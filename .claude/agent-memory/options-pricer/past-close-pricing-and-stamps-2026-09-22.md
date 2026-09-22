---
name: past-close-pricing-and-stamps-2026-09-22
description: store.price_close (past-close option pricing for the backfill), live vs close snapped_at stamps, and what the 2026-09-22 "options Daily = 0" investigation ruled in and out -- read before touching store.py's write path, the catch-up, or anything that deletes pricer marks.
metadata:
  type: project
---

Landed 2026-09-22 after the user: "options daily pnl 0, that cannot be right, everything
is moving" / "I expect every time I pull bloomberg now, the options are repriced with the
latest data, and that the latest data is also logged / overwriting previous marks".

**Root cause (Bloomberg PC snapshot, data/bbg_snapshot/*.csv):** QL_OPTIONS_PRICER marks
existed for 2026-09-21 only; SPOT / vol_quotes / curve_quotes / IRS marks existed for
09-17..09-21. `engine/pnl/valuation.py::_mark_near` then carried 09-21's PREMIUM back to
09-18 ("nearest later close, none earlier") -> LTD(18) = LTD(21) -> Daily 0. Nothing in
engine/options had ever written a PAST day's marks; the live pull writes today's only.

**What shipped:**
- `store.price_close(conn, day) -> {"day", "priced", "skipped": [{"trade_id","reason"}]}`
  (+ `"error"` on an exception; never raises). Scope = FX_OPTION with `trade_date <= day
  AND expiry_date >= day` (SQL `_CLOSE_BOOK_SQL`); a trade dealt after `day` is neither
  priced nor listed; expiry < day is left to `price_all_and_store`'s catch-up; expiry ==
  day writes the payoff with `refreeze=True` (drops a realised_pnl row frozen from an
  older premium) UNLESS `_frozen_from_expiry_mark` (W-2: frozen from an expiry-dated mark
  -> skip, leave both alone). Skip reasons are prefixed `"<day> close: "`. Runs the
  one-time unit purge first so the purge can never eat what it writes. bbg-data still has
  to CALL it from `data/bloomberg/backfill.py` per day, after that day's closes are on file.
- **No cross-day fallback existed to remove:** `inputs.get_spot`, `vol_marketdata.vol_smile`
  / `atm_vol_for_expiry`, `inputs.get_manual_vol`, `rates._read_curve_quotes` /
  `_get_manual_rate` / `_get_fwd_outright_points` / `_get_pair_spot` all key on the exact
  as_of. `cut_time_factor` reads the pricing moment off the SPOT's own `snapped_at` on
  as_of (15:00 for a close row, the live time for a live row) -- correct for both paths.
- **Stamps.** `_insert_marks(..., snapped)` now takes the stamp explicitly. Live run =
  `store.live_stamp()` = `_now_ny().isoformat(timespec="seconds")` (America/New_York, the
  actual pricing time; before this every pricer mark said a flat 15:00 of its as_of via
  `engine.rates.store.snapped_at`, so a 23:08 pull looked like the close). Past close and
  the catch-up's expiry-dated marks = `store.close_stamp(day)` = 15:00 NY of the date.
  `price_and_store` / `price_all_and_store` take optional `snapped`. Tests pin the clock
  by monkeypatching `store._now_ny` (two runs in one second must still order).
- **Live path scope deliberately unchanged** (the brief asked to skip `trade_date > as_of`
  in `price_all_and_store`; I did not): a mark dated before the trade date never enters a
  valuation (valuation.py reads `trade_date <= as_of`), and a trade dealt in HK after the
  17:00 NY roll is dated tomorrow while the pull's marks keep today's calendar date
  (`live.book_today`) -- tomorrow's book prices it off today's mark via the near-marks
  rule, so skipping it would blank a freshly dealt option until the next day's pull.
- `set_option_terms` now `.strip()`s option_type / payoff before validating (hard rule 6);
  `_same_terms` already stripped. `_frozen_from_expiry_mark(conn, row)` is shared by the
  catch-up and price_close.

**Deletion investigation (task D), for the record:** the only code that deletes pricer
marks across dates is `set_option_terms` (per instrument, on a REAL change; every UI shape
of an identical re-save -- strike as text/float/int, barrier None/0/"0", type/payoff any
case or ''/None -- is proven NOT a change, tests/test_options_close.py) and the one-time
`purge_old_unit_cash_payoff_marks` (DIGITAL/ONE_TOUCH/NO_TOUCH instruments only). Ruled
out: `backfill.py:625` (deletes only SRC_SPOT_FWD/SRC_INTERP FX rows failing
`is_close_row`, which returns True for every non-SPOT/FWD type), the upload (trade-keyed
tables only; its `instrument_options` upsert overwrites only populated blotter values, so
a re-upload cannot reset typed terms), `schema.purge_retired_sources` (BNP sources),
`irs_direction.py` (IRS types), `manual.py:323` (a deleted MANUAL trade's instrument),
`snapshot.py:250` (marks-import, on the importing PC). Unexplained: the vanillas' 09-18/19/20
marks on the Bloomberg PC (live pulls DID run those days: SPOT stamps 22:27 / 22:31 /
12:46 / 12:52 NY; the ON vol tenor is quoted so short-dated EURUSD options were in range)
-- the exported `pull_status.json` holds only the LAST pull, so whether those days'
options steps priced, skipped or errored is not in the snapshot. bbg-data's lane.

**Test gotchas:** `tests/test_options_close.py` imports fixtures from
`tests/test_options_pricing.py` (`_seed_pair_spot` seeds SPOT + both OIS curves;
`_seed_bare_pair` SPOT only; `_seed_vol` = manual vol). Pre-existing ruff F821 in
`engine/options/inputs.py:325-326` (`"Optional[RateInput]"` string annotation, no import)
-- lint-only, not mine, left for infra. See [[expiry-day-intrinsic-mark-2026-09-18]] and
[[premium-delta-conventions]].
