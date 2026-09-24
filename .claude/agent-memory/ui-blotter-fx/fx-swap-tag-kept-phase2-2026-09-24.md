---
name: fx-swap-tag-kept-phase2
description: Phase 2 removal (2026-09-24) left "FX_SWAP" in blotter_fx.FX_PRODUCTS on purpose; drop it only together with ui-blotter's fx scope and the engine
metadata:
  type: project
---

Phase 2 of the commodity conversion (user yes 2026-09-24) removes the FX-swap package rule, IRS, NDFs and the equity index. In the screens pass this lane only cleaned docstrings (FX_SWAP / IRS / `ui.tabs.rates` mentions) and kept `"FX_SWAP"` in `FX_PRODUCTS`.

**Why:** the Total book's FX scope (`ui/tabs/blotter.py`, ui-blotter) and the engine's sets (`engine/pnl/fx_blotter.py`, `valuation.py`, `ledger.py`, ladder) still carry FX_SWAP, and an existing database may still hold packaged trades. Dropping it here alone would hide them from this sub-tab while the Total book counts them: a silent drop, and the strip would stop matching the Total's FX row.

**How to apply:** remove `"FX_SWAP"` from `FX_PRODUCTS` only when ingest-booking has retired the package rule and no FX_SWAP trade can remain, and in the same wave as ui-blotter's scope. The sub-tab now serves Jason's FX hedges (USDCNH, EURUSD, USDJPY, GBPUSD, EURGBP, XAUUSD). `data/sample/blotter_sample.csv` is a synthetic book since 2026-09-24; my tests read inline fixtures, never `data/raw/new_sample_trades.csv`. New tests go in `tests/test_ui_blotter_fx.py`. Older notes on this module sit in ui-blotter's memory (blotter-fx-* files).
