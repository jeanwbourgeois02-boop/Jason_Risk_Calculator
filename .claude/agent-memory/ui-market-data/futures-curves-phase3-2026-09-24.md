---
name: futures-curves-phase3-2026-09-24
description: Phase 3 Futures curves section on the Market data tab (per root by sector, prices, previous close, missing reasons), listed options in blocked_by_mark, and the py -3 - heredoc hang
metadata:
  type: project
---

2026-09-24, commodity conversion Phase 3 ("ui-market-data: the futures curve per commodity").

- `futures_curve_rows` / `futures_curves_panel` (container `FUTURES_CURVES_PANEL_ID`, an 8th Output of `_update_body`, under the pair section). Roots = those `engine.curve.curve_positions` lists (rows + flat_contracts). Contracts = `instruments` of that root not expired by as_of, or marked that day, plus the open ones. Open positions first, then by expiry.
- Price = official FUTURE_PX at the contract's expiry (exact key, like the engine). Previous = the latest official FUTURE_PX strictly before as_of, its date shown (chosen over the previous business day so exchange holidays need no calendar). Change is display subtraction only, digits follow the price.
- Missing-price reason order: library row `requestable` False -> its reason; not in the library -> "no trade on file needs it..."; last pull's own reason for that key; past date -> backfill sentence; else "not pulled yet". A MANUAL row is appended as "on file instead".
- "(est.)" comes from `data.contracts.contract_for(root, id, conn=conn).estimated` (the same stored dates the contract dates panel reads).
- Chart per root only with 2+ priced contracts (the sample book has 17 roots of 1 contract each; 17 one-point charts was clutter).
- The Phase 2 "0 trades blocked" conversion spot: the future path was already linked in a876051; the remaining unlinked case was a listed option (product EQ_OPTION / CMDTY_OPTION), now linked with no notional (its face is contracts).
- Test root for "no verified ticker": LME:AH (Bloomberg root LA) with bbg_ticker ''; `LME:LA` in the older fixture is not a root of config/contracts.csv any more.

**Why:** keeps the next Phase (options on futures, LME) from re-deriving these choices.
**How to apply:** never run `py -3 -` with a heredoc here: it hangs until the tool timeout and leaves an orphan py.exe (other lanes' orphans were seen too). Write a scratch .py file instead.

Related: [[phase2-removal-and-contract-dates-2026-09-24]], [[market-data-tab]]
