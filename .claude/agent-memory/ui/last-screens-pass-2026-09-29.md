---
name: last-screens-pass-2026-09-29
description: Last small pass on Data + Blotter (2026-09-29) - Data fold titles sentence case via CSS, Reference closes / Contract dates tables span their card, Blotter Size for unrecognised futures (fin_type) and troy ounces, option strikes through leg_name(strike=, option_type=)
metadata:
  type: project
---

- **Fold titles:** `.book-section-title` is uppercase in the shared CSS; Data's folds (Contract dates, Diagnostics, Bloomberg library, Everything the last pull recorded) are reset to sentence case 13px/600 navy in `/* wave 2: data */`, matching the Data issues drawer. Any new fold elsewhere that reuses `book-section-title` will render in capitals unless reset the same way.
- **Tables that span:** `.tk-table.tk-small` has max-width 640px; Data's Reference closes adds `data-ref-table` (width 100 %, fixed layout, Period 110 px); the Contract dates panel's id is `market-data-contract-dates` (no `-panel`).
- **Blotter Size:** `_read_trades` now reads `t.fin_type` (falls back to '' on an old db). An UNRECOGNISED row whose fin_type says future (has FUT, no OPT) shows lots; else a bare number with "Unit unknown: contract not recognised". FX rows on XAU / XAG / XPT / XPD show "100 oz" (unit "oz" in the CSV), the code on hover.
- **Option names:** `data_checks.price_name` passes `strike=` / `option_type=` to `engine.spreads.trades.leg_name` (added by spreads-engine 2026-09-29), so the strike is "80,000" once, like Book and Blotter.
- Shot artefact: full-page shots draw sticky theads at a table's foot and the top bar mid-page; not a bug.
