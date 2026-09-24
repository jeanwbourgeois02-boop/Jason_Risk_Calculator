---
name: commodity-futures-library-2026-09-24
description: How the Bloomberg library handles commodity futures (conversion SPOT, CONTRACT_DATES met at read time, requestable/reason flag and the default filter in needed_on) and why
metadata:
  type: project
---

Commodity conversion Phase 1 step 2 (2026-09-24), LIBRARY_VERSION "2026-09-24.1".

- A FUTURE or EQ_OPTION with quote_ccy != USD gets a CONVERSION SPOT on `live._usd_pair_name(ccy)` (USDCNY, EURUSD, GBPUSD...), trade date to expiry. Historical need too, so the backfill fetches its closes.
- CONTRACT_DATES rows are emitted for EVERY future whose base_ccy is a root id (regex EXCHANGE:CODE, `is_contract_root`), whether or not dates are stored; the need is "met" at read time in `needed_on` via `data.contracts.static_dates`. Deliberate deviation from the brief ("only futures with no dates stored").
  **Why:** contract_static is written by a pull, and no trigger dirties the library on it; if compute depended on it the library would go stale after every pull ("library changes only when trades or code change").
  **How to apply:** anything that must list satisfied contract dates reads `library.rows`, not `needed_on` (see `inventory.contract_dates_inventory`).
- Rows carry `requestable` / `reason`, computed on read in `rows()` (`_annotate`), never stored. `needed_on` and `needed_in_range` drop unrequestable rows unless `include_unrequestable=True`, so the pull and backfill cannot ask for '' tickers by default. `tickers()`, `mark_inventory` and `close_completeness.not_requestable` include them to show the gap.
  **Why:** hard rule 8 is safest enforced at the source, not in every consumer.
- `close_completeness` counts only requestable marks in needed/complete. Otherwise a placeholder future would keep every day incomplete and the backfill would go back to it on every press.
- SET_KINDS (OIS_CURVE, FIXINGS, VOL_SMILE) have '' ticker by design and are always requestable.
