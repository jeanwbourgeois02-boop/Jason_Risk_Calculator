---
name: commodity-futures-request-ticker-2026-09-24
description: Backfill asks a commodity future's PX_LAST history under data.contracts.request_ticker(contract_for(base_ccy, instrument_id, conn), TODAY) -- one-digit year while live, two-digit canonical id once expired; placeholders (bbg_ticker '') never asked; library's requestable flag; test-suite traps.
metadata:
  type: project
---

Commodity conversion Phase 1 (housekeeper brief 2026-09-24). Follows the bbg-data notes
(history_inputs_and_futures_settle_2026_09_22 etc. in `.claude/agent-memory/bbg-data/`).

- Why: Bloomberg names a live contract with a one-digit year ('CLZ6 Comdty') and an expired
  one with two ('CLZ26 Comdty'); after expiry the one-digit name means the contract ten years
  on, so history under the stored bbg_ticker returns nothing. The form is chosen for the
  REQUEST day (backfill's `today`), not the historical day being worked.
- `backfill._future_request_tickers(conn, library_rows, today)` -> ({ticker: instrument_id},
  {instrument_id: reason}). Commodity = asset_class FUTURE and `library.is_contract_root(base_ccy)`
  ('EXCHANGE:CODE'). ES / macro futures and listed options keep the library ticker. Marks still
  land on the instrument's own id and expiry, PX_LAST stamped `settle_stamp` (17:00 NY; user
  2026-09-24: futures close stays PX_LAST).
- bbg-library (same day) annotates every row with `requestable` / `reason` and
  `needed_in_range` / `needed_on` DROP unrequestable rows by default, so a placeholder never even
  reaches the backfill's `needed`; the gap is listed by the library / Market data tab, not by the
  backfill's day result. Housekeeper 2026-09-24: "use the default filter rather than a filter of
  your own" -- so the helper has NO empty-ticker guard of its own; only UnknownContract is named.
- Housekeeper 2026-09-24: run lane tests in the FOREGROUND under `timeout 900`, never in the
  background (the PC is slow when several runs overlap).
- Estimated last trade date (last weekday of the contract month) is LATER than the real one for
  e.g. CL (~20th of the prior month): until bbg-live stores Bloomberg's FUT_LAST_TRADE_DT, an
  expired contract in that window is asked under the one-digit form and gets nothing. Found, not
  done (contract-master / bbg-live territory).
- A non-USD future's USD-conversion SPOT (role CONVERSION, from bbg-library) flows through
  `spot_only_pair_names` / `traded_pairs` with no backfill change.
- Traps: test_backfill.py pins `library.LIVE_ONLY_KINDS` (bbg-library added CONTRACT_DATES).
  The machine is slow: the four backfill test files take ~1.5-3 min.
  New test file `tests/test_backfill_commodity.py` (brief allowed it; not yet in the lane table).
