---
name: stress-api-2026-09-24
description: stress.py lost the EQUITY move and futures line in Phase 2 (2026-09-24); load_scenarios drops EQUITY and equity-only scenarios; commodity scenarios are Phase 4's commodity-stress lane
metadata:
  type: project
---

2026-09-24 (commodity conversion Phase 2, user approved removing the equity index): stress.py
is currency moves only. `apply_scenario(delta, moves)` / `run_scenarios(delta, scenarios)`
return `{fx_pnl, fx_total, total}`; the futures kwargs, `futures_pnl`, `EQUITY_KEY`,
`fx_moves` and `futures_pct_by_scenario` are gone. `load_scenarios` ignores an EQUITY key
and leaves out a scenario that moved only EQUITY (e.g. "Equities -10%"), my call so a
column of zeros never reads as "no loss".

**Why:** the equity index left the app; config/stress.yaml (infra's) may still carry EQUITY
keys until infra trims it.

**How to apply:** commodity scenarios arrive in Phase 4 via the commodity-stress lane; do not
re-add a futures line here without that brief. Consumers: ui-ladder, risk-metrics.

Related: [[lane-inheritance]]
