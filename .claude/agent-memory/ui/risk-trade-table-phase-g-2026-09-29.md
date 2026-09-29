---
name: risk-trade-table-phase-g-2026-09-29
description: Phase G round 2b - Risk rebuilt as the per-trade table + four store-driven folds (Net by commodity with month grid and curves, Currency, Stress, Price check); curve.py deleted; margin code moved to risk_limits.py; proof recipe
metadata:
  type: project
---

2026-09-29, Phase G round 2b (Exposure merged into Risk). Where things live now:

- `ui/tabs/risk.py`: the tab (bar via `tf.register_bar(app, "risk", ...)`, headline, per-trade table, panel, CSV,
  two render callbacks: `_render` (headline/table/foot) and `_folds` (FOLDS_ID) so a fold toggle never rebuilds
  the table). Rows = `trade_risk` rows joined to `shared_trade_book` trades by name (pseudo dict when no trade).
  Headline and total/group rows call `engine.risk.trades.subset_var` on the names showing (cheap once the
  trade_risk block is memoised; never call it before `shared_trade_risk` is ready or it rebuilds the block).
- `ui/tabs/risk_folds.py`: folds are clickable heads `{"type": "risk-fold", "idx": key}` -> session store
  `risk-folds-store` (html.Details would close on every re-render). Commodity row -> `risk-commodity-store`,
  the grid's unit radio (pattern id) -> `risk-grid-unit-store`. Curve rows are counted under a filter only when
  every trade on the row is showing; shared contracts are "excl." (engine does not split per trade).
  Stress = `engine.stress.commodity_stress` (memo "risk-stress", computed only when the fold opens), attributed
  per trade through curve rows' trade_ids; currency scenarios whole-book only.
- `ui/tabs/risk_limits.py`: the old margin/limits code moved unchanged, plus `plain_reason` (re-exported by risk).
- FX unhedged per trade = trade_book hedge `exposure_usd + hedge_usd` (the Book's own basis, at the fill before a
  pull). "USD per 1 %" in Net by commodity = engine delta USD x 0.01 (said on hover).
- Dropped with curve.py: the Option Greeks card, the FX forwards by currency / Net USD line, sector tilt, the
  worst-day line and the macro FX (stress.yaml) scenarios. Asked of risk-metrics: VaR as % of vol target for a
  subset, per-leg daily risk and correlation.

**Traps:** a fold table in an `overflow-x:auto` slot gets the book-grid sticky head at `top: var(--top-bar-h)`
inside the slot, covering the first rows: `.risk-fold ... thead th { position: static }`. The kit's amber
(#8a4b00) reads near-black in screenshots: check classes in the DOM, not the picture. Dropdown option innerText
is upper-cased by CSS: match with toUpperCase in CDP drives.

**Proof recipe:** scratch `ui-g2b/`: `mk.py` (real.db from the template + sample.db), `verify.py <db> [as_of]`
(direct renders, filter, group, open, three units, CSV), `stress_check.py`, `appcheck.py` (dup ids/outputs),
`serve.py` + `drive_risk.py <url> <cdp port> 1680 <tag> <family>` (tab click, filter, clear, sort cycle, expand,
Expand/Collapse all, group, every fold, N more, grid units, tab-switch persistence, CSV download, 1100 px).
Sample at 2026-09-18 has USD deltas; at today it has none (exact marks only).
