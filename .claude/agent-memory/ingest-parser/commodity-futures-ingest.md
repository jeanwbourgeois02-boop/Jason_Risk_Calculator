---
name: commodity-futures-ingest
description: How blotter.py books commodity futures via data.contracts (2026-09-24), the guesses behind the synthetic sample, and the hazards found (Notional rebuild, price scale, bare-code collisions)
metadata:
  type: project
---

Phase 1 step 2 of the commodity conversion (2026-09-24). Non-index futures resolve through
contract-master's `resolve_future`; ES/NQ/RTY/YM keep the old path until Phase 2.

**Why:** Jason's book is commodity RV; no real commodity export exists yet (gate C1 in
docs/open-questions.md), so everything rests on a synthetic sample and on guesses.

**How to apply:**
- The PB export's commodity symbol format is UNKNOWN. The sample guesses the PB form
  '<exchange code><M><Y>-<4 letters>' for Western contracts ('CLZ6-USAA', Brent 'BZ6-USAA'
  since ICE's exchange code is B; Bloomberg root CO) and the Chinese form for Chinese ones
  ('CU2611', 'I2701', no suffix). Re-check both the day a real export arrives.
- Bare codes collide across exchanges: ZS (CBOT / LME), SI (COMEX / LME), ZC (CBOT / ZCE),
  CO (LME cobalt / Brent Bloomberg root). The parser relies on the row's Currency and
  Execution Venue cells to narrow; if a real export leaves Execution Venue blank, soybeans
  and silver will reject as ambiguous. Watch for that first.
- Fills are assumed in the contract list's quoted scale (cents for RB, HO, ZS, ZL, ZC, HG;
  pence for NBP). A PB quoting USD/gal would be 100x off; the NetInvoice cross-check warns
  "another unit" when the ratio is ~100 or ~0.01 (warn only, never rejects).
- Quantity is never rebuilt from Notional on the commodity path: a Notional in gallons /
  bushels over a cents-scaled multiplier gives a whole number 100x too big. Only
  NetInvoice / (multiplier x Price) rebuilds it.
- Currency cell is read with `_ccy` (accepts 'DOL.C-USAA' and bare 'USD'), a superset of
  common.cash_ccy; blank or unreadable = not given, never a reject.
- The contract master's expiry is an ESTIMATE (last weekday of the contract month), later
  than the real one for CL/Brent etc. `parse(conn=...)` / `load` pass the connection so
  stored Bloomberg dates win.
- The book filter lives in config/book.yaml (`BookFilter`, `load_book_filter`); missing file
  = the old NMMF default; a malformed file raises. `n_skipped_status_or_fund` stays the
  total (upload.py reads it); per-filter counts and `kept_by_trader` are new fields.
- Macro sample checked identical to the pre-change parser on 2026-09-24 (857 trades, same
  trades/legs/instruments/rejects/warnings).
