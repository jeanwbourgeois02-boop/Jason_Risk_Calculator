---
name: curve-positions-conventions
description: Choices behind engine/curve (exact spot not usd_per_quote, gross_lots, flat contracts, currency exposure, multiplier check; Phase 5 delta of options, LME forwards, averaging contracts; golden-book pin)
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

**Phase 5 (2026-09-24), delta for options, LME forwards, averaging contracts** (`rows.py`, `averaging.py`):
- Options and LME forwards are rows too; `by_commodity` / `by_sector` lots, units, net/gross USD and
  `months` stay futures + LME only (an option is not a lot of the future); `delta_*` totals cover
  every product. Option `notional_*` is None by design: consumers reading notional_usd must filter
  on `product` (commodity-stress, book-positions were told).
- Option underlying price key = the underlying future's `instruments.expiry_date` when on file
  (Bloomberg's date once applied), else contract-master's last_trade_date. marks has an FK to
  instruments, so a FUTURE_PX can only exist for an instrument on file: tests must insert it.
- LME row contract_id = 'LME:CA <prompt>' (the instrument id alone is shared by every prompt);
  dates_source 'PROMPT'; instrument multiplier checked against per-tonne (multiplier/contract_size).
- Averaging factor: remaining bdays after as_of / period bdays on root.calendar; 1 before, 0 on/after
  the last day. Not applied to options (the DELTA mark is the pricer's).
- Adding any key to the output shows as "not in golden" in tests/test_golden_book.py: the pinned
  book.json holds the whole curve_positions output; a re-pin needs the user's yes (infra).
