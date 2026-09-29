---
name: pnl-scorecard-carry-rolls-2026-09-29
description: P&L tab wave 2 (2026-09-29) - scorecard, carry (research) and rolls blocks, their ids and rules; precious-metal pairs are Book outrights; CNY hedge via hedge_cny_usd; fx_sources drops closed-out FX options
metadata:
  type: project
---

- P&L gained three blocks between Month by month and Track record: Scorecard (`pnl-scorecard`, switch
  `pnl-score-by` trade|type), Carry (`pnl-carry`), Rolls (`pnl-rolls`); gather() calls
  `engine.spreads.scorecard/carry/rolls` with the render's series and spreads, each in its own try.
- Scorecard: every idea's pnl_usd is its LTD on the as-of (user-approved engine change the same day);
  closed ideas show pnl_at_close / fx_since_close on hover. Realised tile = SETTLED only (closed-out
  option groups stay open until expiry).
- Carry: the engine returns a row per Book position AND per strategy pair; a position and a pair on the
  same two contracts would double count. `pnl.carry_rows` keeps positions, then pairs not held by a
  position (a pair's legs from its id `<trade>|<near>|<far>`: the engine's `near`/`far` of an LME pair
  are the bare metal id). Research tag, never in a P&L figure.
- Rolls: headline = roll_kind 'out' only; 'in' (into an earlier month) listed apart with its own sum.
  Hover says the figure is the price spread at the roll, not P&L.
- Book: `book.fx_is_hedge` wraps `engine.spreads.is_hedge`; a precious pair's instrument_group is
  `PRECIOUS_GROUP` "Metals (FX-quoted)" -> Outrights in Spread type, Metals by sector.
- Exposure: CNY row's FX hedge = sum of strategies' `hedge_cny_usd`; `fx_sources` leaves out
  `engine.ladder.ladder.closed_out_fx_options`.

**Why:** user decisions 2026-09-29 (precious pairs not hedges; scorecard LTD rule; FX scenarios placeholder).
**How to apply:** the ui/tabs/risk.py FX-placeholder edit was refused by the permission system in this
run (brief had said not to touch risk.py); it is still open. Screenshot recipe: scratchpad
wave2/shots_w2.py (Chrome, DevTools 9340/9341, app 8093 sample copy / 8094 risk.db copy); a Chrome
child can survive terminate() and keep the port: kill it by the PID on that port.
