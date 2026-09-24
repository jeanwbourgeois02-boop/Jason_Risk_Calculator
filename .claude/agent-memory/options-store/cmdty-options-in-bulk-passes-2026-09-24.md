---
name: cmdty-options-in-bulk-passes-2026-09-24
description: How CMDTY_OPTION (options on commodity futures) joined price_all_and_store / price_close / recalc_on_file; why the closed-out rows are selected in store.py; the new outcome fields and dict key consumers read; what other lanes still owe
metadata:
  type: project
---

Landed 2026-09-24 (Phase 5, housekeeper brief): `store.price_listed_options(conn, as_of, snapped)` calls listed-options-pricer's `price_listed_commodity_option` + `write_listed_marks` once per CMDTY_OPTION instrument open on the date (trade_date <= day, NOTIONAL leg settle_date, else instrument expiry, >= day). All three bulk passes use it.

**Closed-out finding:** `engine.pnl.valuation.closed_out_options` / `_opt_sql` read `product = 'FX_OPTION'` only, so it never closes a CMDTY group. Changing it would move CMDTY P&L (CLOSED status, CLOSE_OUT freeze) = hard rule 7. So `store.closed_out_listed_options` selects CMDTY rows in `_opt_sql`'s column shape and feeds the imported `closed_out_from_rows` (the rule itself is not copied). The terms key has no instrument id, so a buy and sell-back on ONE canonical id group fine.

**Why:** the P&L of a CMDTY_OPTION is Bloomberg's price (FUTURE_PX), so its store marks are Greeks only; a flat group's delta nets to zero whether priced or not.

**How to apply:**
- Outcome shape: `PricingOutcome.product` ('FX_OPTION' default | 'CMDTY_OPTION'), `.implied_vol`; CMDTY outcomes appended after FX in `price_all_and_store`. Dicts: `"futures_options_priced"` in price_close, recalc_on_file and each recalc day. Exact-dict test asserts must carry it.
- `price_close(conn, day, products=ALL_PRODUCTS)`: recalc passes only the products that have data that day (FX: pair SPOT; CMDTY: option AND underlying FUTURE_PX, `_listed_days_on_file`).
- No expiry-day payoff / catch-up for CMDTY: on its expiry date the pricer's own reason "expiry X is not after as_of X" lands in skipped.
- Owed elsewhere (Requests 2026-09-24): bbg-live `_options_step` returns early when no FX_OPTION exists; bbg-backfill `_options_open_on` checks FX_OPTION only; bbg-library lists no OIS curve for CMDTY options (Greeks need the contract currency's curve, else USD SOFR).
