---
name: risk-chip-phase-b-2026-09-25
description: Screens redesign Phase B VaR chip - book_risk takes 7-18 s so it runs in a chained callback, never in _build_figures; memo layout; width measured with PIL Segoe UI
metadata:
  type: project
---

Phase B added a "Risk" chip after the Data chip: "VaR $33.6k", markers "excl. N" (parts with no
history series + contracts out of a commodity's series, read off `rows_in_series` / `contracts[].in_series`)
and "6.7% of target" (red `_OVER_TARGET_MARKER` when `over_vol_target`). Definitions + target
(placeholder note from config) on hover.

**Why it is built the way it is:** `engine.risk.book_risk` takes 7-8 s on the golden book on a quiet
PC, 10-18 s under load, ~75 % of it in risk-history's `_cm_frame` (constant-maturity frame rebuilt
per position per call, not cached). So:
- `_build_figures` never calls it. It shows `_RISK_LATEST[(path, mtime, as_of)]` or "VaR …".
  `_RISK_LATEST` exists so the figures never call the history loaders: a cold
  `load_commodity_history` alone costs ~0.4 s.
- The chip's own callback (`Output(VAR_CHIP_ID, children)`, `Input(header-block-figures, children)`,
  `State(as-of)`, `prevent_initial_call=True`) runs after the figures and works it out through
  `risk_summary`, memoised on (path, mtime, as_of, history + config identity). Errors are not memoised.
- A/B on the golden book: `_build_figures` warm 0.375 s without vs 0.366 s with the chip (noise).

**How to apply:** if the Risk tab or the chip feels slow, the fix is in risk-history (memoise
`_cm_frame`), not here. Width: measured with PIL + C:/Windows/Fonts/segoeui*.ttf (Inter is not
installed, so Segoe UI renders). The figures row came to ~1490 of 1632 px, ~1550 with the "LTD chart"
toggle. Related: [[slim-header-2026-09-25]].
