---
name: closed-out-options-no-delta-2026-09-29
description: A closed-out FX option group adds no currency delta (user yes 2026-09-29); ladder imports the valuation's grouping; not reported in `unresolved`
metadata:
  type: project
---

User, 2026-09-29 ("this needs to be fixed surely"): every trade of an FX option group closed out as of the date valued is left out of `delta_per_ccy` (both FX_OPTION branches and the missing-SPOT check) and out of `exposure_adapter.option_records_from_db`, whatever DELTA marks exist.

**Why:** the sample db (`data/raw/sample.db`, 2026-09-18) carries DELTA marks on the closed EURUSD put pair 910000044 / 910000045 (0.539 vs 0.388, different instrument ids), so the pair netted +754,964 EUR (+878k USD) of phantom delta. Real data has no marks on closed groups, so it only showed on synthetic data; the rule must not rely on a mark being absent.

**How to apply:** always take the grouping from `engine.pnl.valuation.closed_out_options` (never re-derive). The SQL takes the ids as a JSON list in `:closed_out` via `json_each` (`ladder.closed_out_param`). Closed-out trades are NOT put in `unresolved`: `positions.fx_option_positions` turns `unresolved` into "missing" gaps. `ladder.closed_out_fx_options` names them. Golden book's `delta_per_ccy` pin needs a re-pin (user's yes). See [[ndf-removal-2026-09-24]].
