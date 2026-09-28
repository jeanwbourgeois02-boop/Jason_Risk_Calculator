---
name: review-findings-daily-split-2026-09-28
description: 2026-09-28 read-only review of engine/spreads/daily_split.py + strategies.py (strategy Daily split spread/fx/hedge): no criticals; hedge set wider than approved, settled hedge misbucketed, prev frame diverges when a trade is unpriced today, no tests yet
metadata:
  type: project
---

Review of the uncommitted Daily split (user yes 2026-09-28): `daily_split(rows_t, rows_prev, trade_ids, hedge_ids)`
over two value_book frames, identity `(L_t-L_p)*S_t + L_p*(S_t-S_p)`; wired by `strategies.py::strategy_entry`
into `book_spreads(...)["strategies"]` (new key; no ui consumer yet, no test names it).

**Findings (no criticals).**
- Identity holds in every branch; the residual is put on spread, NaN never read as 0 (`_num` -> None -> excluded or
  "counted whole as spread" with a reason).
- W: `_build_legs` drops closed/SETTLED trades before the hedge test, so a settled SGX:XUC hedge's Daily (settlement
  day, re-freeze) lands in *spread*. Hedge ids should be by product/root regardless of status.
- W: hedge set = FX_SPOT/FX_FWD/FX_SWAP products + any `fx`-sector root, wider than the approved "SGX USD/CNH future".
- W: `_prev_frame` falls back to the raw `refs["daily"]` records (no step-back, no fill) when `_period_pnl` returned
  early (a trade unpriced today), and `frames.records(day)` there can raise (no try) and take `book_spreads` down.
- W: `daily.total` can differ from the strategy's `pnl_usd["daily"]` (None with reason when any trade is unpriced today
  vs partial split with `excluded`): the ui must never show the split total as the Daily.
- W: `_coverage` treats the CNY legs' notional sign as the currency exposure; CLAUDE.md says a FUTURE's NOTIONAL leg
  is not currency delta (the exposure is on the P&L). Analytics, not P&L, but a convention drift.
- No test in tests/test_spreads.py for either module at review time (another session still editing; pytest not run).

**Why:** hard rule 7: the split is new P&L arithmetic; the identity must hold to the cent against period_rows.
**How to apply:** on the next pass check the hedge-id fix, the `_prev_frame` guard, tests, and the Handoff naming
`strategies` / `daily` for the ui lane.
