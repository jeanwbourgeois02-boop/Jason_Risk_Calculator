---
name: history-inputs-and-futures-settle-2026-09-22
description: 2026-09-22 backfill change - past days ask the daily history for vol smiles and OIS curves (quote_fetch, inputs_missing) and price swaps via engine.rates.store.recalc_on_file; a future's close is PX_SETTLE stamped 17:00 (settle_stamp) and a live PX_LAST row is replaced; present-spot NDF caller retired. Traps and out-of-lane fallout.
metadata:
  type: project
---

Three user-approved changes in one run (session-coordinated, 2026-09-22). Follows
[[backfill-prices-options-2026-09-22]] and [[day-boundary-1700-ny-2026-09-22]].

## Task C: a past day's inputs come from Bloomberg's daily history
- `library.needed_on(historical=True)` / `needed_in_range` now include OIS_CURVE / VOL_SMILE
  rows of FX_OPTION and IRS (`HISTORY_INPUT_KINDS`, `HISTORY_INPUT_PRODUCTS`); `LIVE_ONLY_KINDS`
  = (FIXINGS, NDF_1M, DIV_YIELD). EQ_OPTION's OIS_CURVE row stays today-only (Greeks).
  `library.history_inputs_needed(conn, day)` -> [{kind, key}], OIS only for ccys in
  `rates_marketdata.OIS_INDEX` (SEK never asked). No LIBRARY_VERSION bump: `compute` unchanged.
- `inventory.inputs_missing(conn, day)` and a NEW `close_completeness` column `inputs_missing`.
  `needed/present/complete/missing` stay marks-only ON PURPOSE: ui/tabs/header.py::needed_marks
  shows "(N of M needed marks)" from them. The backfill's `todo` and auto_backfill's `due` OR the
  two; `_record_outcome` treats a day as done only when marks complete AND no inputs missing;
  `_signature` folds (key, kind) in; `_plain_reasons` appends `missing_inputs` reasons.
- `backfill(..., quote_fetch=None)`: defaults to pm.fetch_historical_series when the call has a
  session, else to `fut_fetch` (tests inject three fetchers and no session). A test injecting
  `fut_fetch=_never_called` on a book with an option/swap MUST pass quote_fetch or it raises in
  `_fetch_vol_history`. One HistoricalDataRequest per kind per stretch (`_fetch_vol_history`
  = 45 tickers/pair via vm.vol_ticker; `_fetch_ois_history` = rm.ois_curve specs), only for
  pairs/ccys some day of the stretch lacks. Writes via the live writers (vm.write_vol_quotes,
  rm.write_curve_quotes) under source BBG_BDH (=SRC_FUTURE); a curve with < rm._MIN_QUOTES (4)
  quotes is not written (reason names the failed tickers in spec order).
- ASSUMPTION stated to the user: daily PX_LAST is Bloomberg's own close of those quotes (15:00
  rule was for FX closes). UNVERIFIED that vol tickers serve history at all.
- Per DONE day, order: closes -> forwards -> `_write_day_inputs` -> `_price_rates_close`
  (engine.rates.store.recalc_on_file(conn, day, since=day), guarded by `_import_recalc_rates`)
  -> `_price_options_close` -> realise. Result keys added: vol_quotes, curve_quotes,
  missing_inputs, rates_priced, rates_failed, rates_note (`_STEP_KEYS`, on every status).
  Status block: sibling "rates" block {priced, failed, note, vol_quotes, curve_quotes,
  missing_inputs}; "days" shape still pinned {status, missing_count, missing}.
- A swaps-only book no longer bails out at `if not pairs and not has_futures` (has_inputs).
- Cost on the Bloomberg PC: the first run after this re-works every past option/swap day
  (all lack inputs), so the stretch's SPOT/tenor intraday requests go again once; rows at the
  close are not rewritten (`_drop_already_official`).

## Task B: futures' past close = PX_SETTLE stamped 17:00
- `SETTLE_HOUR_NY = 17`, `settle_stamp(day)`; `is_close_row("FUTURE_PX", ...)` true only at that
  stamp (NDF_FIX still always true). Live rows are 15:00 of the book date (build_future_rows)
  or the press time, so they differ. Old backfill rows were ALSO 15:00 -> re-asked once (one
  HistoricalDataRequest per stretch) and replaced; same PK so INSERT OR REPLACE, no delete.
- auto_backfill's restamp note reworded ("hold marks that are not that day's close ...").

## Task A: present-spot caller retired
- `_freeze_ndfs_at_present_spot` -> `_realise_after_backfill` (plain realise_settled(conn,
  today)); snapshot._realise likewise. pnl-engine removed the kwarg in parallel.

## Out-of-lane fallout to report (not fixed here)
- tests/test_ui_market_data.py::test_past_close_rows_count_like_the_header_and_repeat_its_sentence:
  its `_mark` helper stamps a PREV-day FUTURE_PX row at 15:00 -> no longer a close (present
  6 != 7). ui-market-data must stamp it with backfill.settle_stamp / 17:00.
- tests/test_live.py pin of the real options recalc dict loosened: options-pricer's uncommitted
  store.recalc_on_file adds `closed_out` keys.

## Second wave (same day)
- `fwd_curve.spot_date_for` delegates to `engine.pnl.calendar.spot_date(day, pair, holidays)`;
  `_SPOT_LAG_ONE_DAY` lives in calendar.py only. No circular import (calendar imports nothing
  of data/bloomberg). Passing the empty default means "no holidays", not load_holidays().
- Closed-out options (options-pricer's `PricingOutcome.closed_out`, price_close /
  recalc_on_file "closed_out"): `live._options_step` -> status["options"]["closed_out"]
  (trade ids) + "closed_out_summary" sentence (`live.closed_out_sentence`), never in
  skipped; `recalc_summary` appends "; N closed-out option(s) not priced"; backfill result key
  `options_closed_out` and status "options" block key "closed_out". ui-market-data shows it.

