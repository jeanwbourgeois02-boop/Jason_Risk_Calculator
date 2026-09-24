---
name: phase2-removal-and-contract-dates-2026-09-24
description: Commodity conversion Phase 2 on the Market data tab - macro displays removed, library filter by used_for words (transitional), gaps, contract dates panel, not_requestable list, MANUAL never official
metadata:
  type: project
---

2026-09-24, user approved the macro trader's products leaving the app (NDF, IRS, SPX/ES, fixings, rates vols). Removal runs top-down: screens first, Bloomberg lanes next wave.

- A temporary used_for filter for the removed library kinds was added then deleted the same day, once bbg-library stopped producing them (LIBRARY_VERSION 2026-09-24.2). The library list now shows every row.
- bbg-live renamed the pull's rates block to `status["curves"]` ({currencies: {ccy: {quotes, nodes, error}}, bootstrapped, seconds} + `skipped` / `error`), timings key "rates" -> "curves"; backfill's "rates" -> "inputs"; `status["dividends"]` gone. `curves_block` shows it in brief.
- `blocked_by_mark` links a non-USD future's USD-conversion SPOT to its trades via `live._usd_pair_name` (private import, the same one the library uses); a future's notional is its NOTIONAL leg in the contract currency.
- A library row with `requestable` False is a gap: ticker cell `LIBRARY_GAP_TICKER`, flag "gap", listed first. Never show '' as a ticker.
- Contract dates panel reads `inventory.contract_dates_inventory` (uses `library.rows`, so ON_FILE contracts still list after the need is met; `needed_on` drops them).
- Feed status now carries `status["contract_dates"]` (summary + failed collapsed) and `status["not_requestable"]` (short list, trade ids on hover).
- The old manual-form text claimed MANUAL was official for DELTA/PREMIUM; it has not been since 2026-09-17 (QL_OPTIONS_PRICER). MANUAL is never official.
- `diagnostics_panel` has no caller left (cash_ladder stopped importing it); flagged to infra as dead code, not deleted.
- Test fixtures: futures re-pinned from 'ESZ6 Index' to 'CLZ26 Comdty' (base 'NYMEX:CL', a contract root, so CONTRACT_DATES rows appear); a '' bbg_ticker future of root 'LME:LA' gives a gap.

Related: [[market-data-whole-book-panels-2026-09-21]]
