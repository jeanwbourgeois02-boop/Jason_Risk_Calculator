---
name: unrecognised-trades-2026-09-29
description: UNRECOGNISED trades (every row loads, 2026-09-29) - excluded from the library by name, listed by inventory.unrecognised, not in mark_checks
metadata:
  type: project
---

User 2026-09-29: "every row loads". A blotter row the parser cannot resolve is a trade with product and asset_class 'UNRECOGNISED', instrument 'UNRECOGNISED:<SYMBOL>', no legs.

- library: `UNRECOGNISED` constant; `_FX_LEGS_SQL` / `_FUTURE_LEGS_SQL` exclude the product by name (the other queries filter on product already). Output unchanged, so LIBRARY_VERSION was NOT bumped.
- inventory: `unrecognised(conn)` lists them per trade (trade_id, trade_name, trade_date, quantity, price, broker_symbol, instrument_id, reason, blocks_what "no P&L: contract not recognised"). Reason = the `upload_issues` row whose symbol matches (case-insensitive), else a generic sentence.
- Choice: NOT added to `mark_checks` as MISSING rows. **Why:** every mark_checks row is a mark a pull can bring, and the header's marks count / Data's "43 of 47" read the same needed set; an unrecognised trade needs a contracts.csv fix, not a pull.

Related: [[mark-checks-phase-g-2026-09-29]]
