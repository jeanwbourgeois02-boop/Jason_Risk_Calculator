---
name: unrecognised-trades
description: 2026-09-29 "every row loads": value_book lists product UNRECOGNISED trades blank (NaN, quantity/fill too) with status UNRECOGNISED and a reason read from upload_issues by symbol
metadata:
  type: project
---

Since 2026-09-29 (user: "all rows need to load thats non negotiable") a blotter row the parser cannot resolve is a trade with product UNRECOGNISED, instrument 'UNRECOGNISED:<symbol>', no trade_legs, multiplier 0. `value_book` reads them with their own SQL (no join to instruments or legs) and `_unrecognised_row` gives status UNRECOGNISED, settle_date '', every figure NaN including quantity and fill (file units, no contract behind them; the note carries "as uploaded: quantity q, price p"), reason "contract not recognised: <why>; P&L can't be computed until it is mapped".

**Why:** hard rule 2 (never zero, never dropped) with the user's every-row-loads rule; the shared definition was the housekeeper's scratchpad `every-row-loads.md`.

**How to apply:**
- <why> comes from `upload_issues` matched on the symbol (upper-cased, whitespace-collapsed); that table is ingest-booking's and replaced per upload, so an older trade falls back to "symbol X is not in the contract list". A leading "contract not recognised:" in the issue text is stripped so it is not doubled.
- The ledger needs no change: every freeze SQL filters by product list and joins trade_legs. `ledger.ltd` goes NaN while one is on file (engine stays unfilled, by design).
- `fill_book` walks 5 earlier days for it uselessly unless pnl-series adds the reason to `_NEVER_FILLED` (requested 2026-09-29). Related: [[phase3-5-freeze-retired-cmdty]] (the retired-product blank row is the same pattern).
