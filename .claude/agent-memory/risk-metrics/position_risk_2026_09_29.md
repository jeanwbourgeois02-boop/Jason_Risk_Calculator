---
name: position-risk-2026-09-29
description: Risk by position (Risk tab blocks 2 and 3): positions keyed by spreads-engine's positions_of ids, the component-VaR tail rule (5 ranks nearest the quantile, rescaled), the correlation window, and the user-approved hedge exception (a currency-hedge leg with no history is left out alone, partial_reason).
metadata:
  type: project
---

`engine/risk/positions.py::position_risk`, called at the end of `book_risk` (top-level key
`position_risk`; the other keys untouched). User approved the four-block Risk tab 2026-09-29.

- Positions = `engine.spreads.period_explain.positions_of(spreads, trade_rows)` (public since 2026-09-29): `POSITION-...` (book_spreads positions; a whole strategy is one,
  `POSITION-STRATEGY-<name>`), `OUTRIGHT-<instrument_id>`, `TRADE-<trade_id>`. Reverted on the
  coordinator's word from the Book's old PAIR-/LEG-/HEDGE- ids the same day: the Book no longer uses them.
  trade_rows = the open trades only (curve-positions rows' trade_ids + FX trades settling after as_of).
- Each trade's contract (curve row id, else the same id built from the fill) and booked quantity;
  netted per contract inside the position; a flat contract adds nothing; else lots x the row's
  `delta_factor` on commodity.contract_series. A leg with no history -> the whole position out.
- Tail rule: ranks nearest h = (1-conf)(n-1) = 12.55 -> ranks 12..16 worst (1-based); raw = -mean,
  rescaled so the sum = the positions' book VaR (diff ~1e-11). Config `var_contribution_days` 5,
  `correlation_min_days` 60 (config/risk.yaml).
- Hedge exception (user yes 2026-09-29): a leg that `engine.spreads.hedges.is_hedge` calls a currency
  hedge (called, never copied) and has no series is left out ALONE: position kept, `partial` True,
  `partial_reason`, `hedges_left_out`; book-level `partial_note` / `partial_count`. Any other leg with
  no series still drops the whole position. A precious-metal FX pair is not a hedge.
- Jason's real book 2026-09-29: all 6 strategies in (CATTLE 44 %, ZNA1 30 %, SCO1 24 %), ZNA1 and
  SILARB1 partial (SGX:XUC not in the research DB); positions' book VaR = headline VaR exactly.
- FX trades (FX options too): excluded, no per-trade FX delta from book_positions.

**Why:** brief from the housekeeper, 2026-09-29, then the id scheme follow-up.
**How to apply:** keep contributions an exact decomposition; never add a partial position series; if
period_explain's id rule changes, nothing here needs copying: it is imported.
