---
name: commodity-futures-ingest
description: How blotter.py books commodity futures via data.contracts (2026-09-24), the guesses behind the synthetic sample, and the hazards found (Notional rebuild, price scale, bare-code collisions)
metadata:
  type: project
---

Phase 1 step 2 of the commodity conversion (2026-09-24). Every future resolves through
contract-master's `resolve_future`; ES/NQ/RTY/YM rows are skipped since Phase 2 (same day,
see [[phase2-macro-removal]]).

**Why:** Jason's book is commodity RV; no real commodity export exists yet (gate C1 in
docs/open-questions.md), so everything rests on a synthetic sample and on guesses.

**How to apply:**
- Since 2026-09-28 the suffix (-USAA/-UKAA/-CHAA/-SPAA) names the exchange (contract-master): the
  sample's Brent is BZ6-UKAA, SGX iron ore FEFF7-SPAA, the deliberate ambiguous row bare ZCZ6.
- (Older) The PB export's commodity symbol format was UNKNOWN. The sample guesses the PB form
  '<exchange code><M><Y>-<4 letters>' for Western contracts ('CLZ6-USAA', Brent 'BZ6-USAA'
  since ICE's exchange code is B; Bloomberg root CO) and the Chinese form for Chinese ones
  ('CU2611', 'I2701', no suffix). Re-check both the day a real export arrives.
- Bare codes collide across exchanges: ZS (CBOT / LME), SI (COMEX / LME), ZC (CBOT / ZCE),
  CO (LME cobalt / Brent Bloomberg root). The parser relies on the row's Currency and
  Execution Venue cells to narrow; if a real export leaves Execution Venue blank, soybeans
  and silver will reject as ambiguous. Watch for that first.
- SUPERSEDED 2026-09-28: fills are in the BROKER's units and are multiplied by the root's
  `broker_price_scale` (100 on RB, HO, ZS, ZL, ZC, HG, NBP ...) into Bloomberg's, see
  [[real-export-conventions]]. The NetInvoice check still warns "another unit" at ~100 / ~0.01.
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
