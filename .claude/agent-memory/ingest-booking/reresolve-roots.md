---
name: reresolve-roots
description: 2026-09-30 upload.reresolve_roots rebuilds every trade on fixed contract roots from its stored broker fields after data.contracts.apply_fixes; the traps it works around (canonical id moves, LME lots, blank broker_price)
metadata:
  type: project
---

User 2026-09-30: "i should be able to do this in the app" (apply Bloomberg's contract fixes from the
Data tab). The ui lane calls `apply_fixes` then `upload.reresolve_roots(conn, root_ids, before=None)`.

Non-obvious facts it rests on:
- The canonical contract id is built from `bbg_root` + yellow key, so a bbg_root fix MOVES the trade to
  a new instrument_id ('CLZ26 Comdty' -> 'WTIZ26 Comdty'). Marks stay under the old id (never touched);
  the next pull prices the new one. Bundle membership (instrument_theme) is copied to the new id; old
  instrument rows with no trade and no mark are removed (not when an option of the pass failed).
- `resolve_stored` was written for UNRECOGNISED rows (stored quantity = the file's Quantity). For a
  recognised LME ticket quantity is TONNES, so lots = tonnes / lot size; the lot size can itself be a
  fix (contract_size), and the old csv is gone once apply_fixes ran -> optional `before` roots.
- A blank broker_price: trades.price is always in Bloomberg units, so the Price cell is rebuilt as
  price / broker_price_scale (not fixable) and the parser scales it back.
- Currency: tried '' first (Jason's export has no Currency column), then the booked quote_ccy; the
  result must be the same trade, product and root, else `failed` and left as it was (hard rule 6).
- fin_type / broker_price / broker_symbol / theme on the rewritten trade are the stored ones (Trade
  is a frozen dataclass: dataclasses.replace).
- bbg_verified alone changes nothing on file -> every trade "unchanged".

**Why:** a contract fix must reach the book without a re-upload, and never drop or mangle a trade.
**How to apply:** keep the same-root check and the no-marks-write rule; verified on the sample with
`universe.CONTRACTS_CSV` pointed at a temp copy (load_roots reads that global at call time).
Related: [[every-row-loads]], [[contract-dates]].
