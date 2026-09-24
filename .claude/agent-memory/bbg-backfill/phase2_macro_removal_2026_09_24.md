---
name: phase2-macro-removal-2026-09-24
description: Commodity Phase 2 removal in backfill.py -- NDF_FIX history, swap re-pricing (recalc_on_file) and the rates_* day keys gone; status block "rates" renamed "inputs"; what stayed and why options need no rates bootstrap.
metadata:
  type: project
---

Housekeeper brief 2026-09-24 (user yes the same day: rates / IRS, NDFs, equity index leave the app).

- Removed: `_fetch_ndf_fix_history` and the NDF_FIX branch of pass 1; `_import_recalc_rates`,
  `_irs_open_on`, `RATES_RECALC_UNAVAILABLE`, `_price_rates_close`; day-result keys
  `rates_priced` / `rates_failed` / `rates_note`; the NDF ticker tables in `state_version`'s
  digest (it now hashes USDJPY's tenor tickers, PX_LAST, vol and OIS tickers only).
- Status file: the backfill block's "rates" became "inputs" = {day: {vol_quotes, curve_quotes,
  missing_inputs}}; `_rates_block` -> `_inputs_block`. No UI reader existed (checked by grep).
- Kept: FX 15:00 closes, conversion spots, futures / listed-option PX_LAST (commodity request
  tickers), OIS + vol-smile history for FX options, `price_close`, closing `realise_settled`,
  snapshot save.
- **Why no rates bootstrap is needed for options:** `engine/options` reads `curve_quotes`
  directly (engine.options.rates), never the `curves` table that `engine.rates.store.recalc_on_file`
  wrote. So dropping the swaps' re-pricing costs past-day option pricing nothing.
- bbg-library 2026-09-24.2: no IRS OIS_CURVE rows, HISTORY_INPUT_PRODUCTS = (FX_OPTION,),
  LIVE_ONLY_KINDS = (CONTRACT_DATES,); `library.NDF_FIX` is a retired name -- backfill no longer
  reads it.
- Tests: swap fixtures replaced by a USDJPY FX option + AUDUSD forward (`_option_and_forward_db`),
  price_close faked to record the day's OIS / vol row counts. ES / SPX inline fixtures kept: the
  generic future and listed-option paths stay for Phase 5. Four backfill files: 82 passed, ~30 s.
- Still owed elsewhere: `.claude/agents/bbg-backfill.md` description still says "NDF fixings";
  CLAUDE.md "Official marks" backfill paragraph still describes NDF fixes and swaps (session, step 9).

Related: [[commodity-futures-request-ticker-2026-09-24]]
