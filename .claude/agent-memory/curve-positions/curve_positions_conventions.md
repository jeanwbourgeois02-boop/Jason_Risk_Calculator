---
name: curve-positions-conventions
description: Choices behind engine/curve/curve_positions (exact spot not usd_per_quote, gross_lots, flat contracts, currency exposure scope, multiplier check) set 2026-09-24
metadata:
  type: project
---

Built 2026-09-24 (Phase 1 step 3). Choices a future change should keep or revisit knowingly:

- USD conversion uses book-positions' `futures_delta.usd_per_unit` (exact SPOT of as_of), not
  `valuation.usd_per_quote`, although the lane file names the latter: usd_per_quote goes through
  `_mark_near` and estimates; a notional / delta is never estimated (hard rule 2). The housekeeper
  brief asked for usd_per_unit explicitly.
- Commodity future = FUTURE whose `instruments.base_ccy` (spaces stripped, upper) is a
  `load_roots()` key; a base_ccy with ':' that is not in contracts.csv still gets a row (lots only,
  reason) so lots never vanish. ES etc. are ignored.
- Row `gross_lots` = |lots|; commodity gross = sum of |row lots|; sector/commodity gross_usd = sum
  of |row notional_usd|.
- Multiplier comes from contract-master; if `instruments.multiplier` disagrees the USD cell is
  None with both named (a wrong multiplier is a wrong P&L; never pick one silently).
- `currency_exposure` includes flat (round-tripped but unexpired) non-USD contracts: their P&L is
  still held in that currency. It sums value_book's pnl_local (value_book may use near marks; that
  is valuation's rule, not ours).
- Grouping is per (instrument, leg expiry) like futures_usd_delta; two expiries for one contract
  give two rows plus a reason line.

**Why:** hard rule 2 (delta never estimated), contract-master is the only source of units.
**How to apply:** when Phase 5 adds monthly-average declining delta, extend `_row`, keep these.
