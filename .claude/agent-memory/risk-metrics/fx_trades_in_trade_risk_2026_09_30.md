---
name: fx-trades-in-trade-risk-2026-09-30
description: Why trade_risk / subset_var now equal book_risk's VaR - FX trades and settled currency cash as position legs (positions.py fx_exposure), the attribution rules, and what it did to hedge % of China trades.
metadata:
  type: project
---

User 2026-09-30: one answer for "on a bad day the book can lose $X": subset_var(all trades) must equal
book_risk's book VaR. Before, FX trades (FX options, XAU forward, CNH/EUR/JPY forward hedges) had no
series in position_risk ("Excl. 5"), while book_risk counted them in its currency rows.

Built (positions.py, trades.py, metrics.py passes fx_history=history):
- `fx_exposure(conn, as_of)`: the exposure path book_positions sums (exposure_records_from_db +
  build_exposure's per-currency OK/STALE fx_rate), split per trade: sum over trades == by_ccy usd_delta.
- An FX trade = one leg (pair + value date; an FX option its instrument id), series = sum over its
  non-USD ccys of usd x `metrics.unit_moves` (lazy import: metrics imports positions).
- Settled deliverable cash (the exposure path's SETTLED records) is in book_risk's currency rows too:
  attached as a leg "<ticket> (settled cash)" to the open position of the same trades.strategy
  (PBRoot name), else one 'CASH-SETTLED' "Settled currency cash" position. On the sample the two
  settled tickets belong to COPAR1 and TTFNBP1, so no cash row appears.
- Scratch sample: subset_var(all) = book_risk VaR to 1e-11; only CMDTY options with no DELTA mark stay
  out (book_risk drops them too).

**Why:** the Risk headline must be one figure (user).
**How to apply:** any new FX-like exposure in book_risk's currency rows must also get a leg here, or the
two VaRs diverge. Known remaining gap: a leg with no series excludes its WHOLE position while book_risk
drops only that underlyer. Side effect to remember: the CNH forward hedges now count in hedge %, and a
futures position's FX exposure is only its open P&L (no notional FX in the held-constant series), so a
notional-sized CNH hedge reads as FX risk (sample IRON1 hedge % -20 -> -300). Raised with the user.
