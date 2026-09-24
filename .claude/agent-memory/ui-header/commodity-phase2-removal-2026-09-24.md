---
name: commodity-phase2-removal-2026-09-24
description: Phase 2 (macro products leave: IRS, NDF, FX-swap rule, ES/SPX) as it touched the header; the header's only NDF dependency is indirect, via ui-ladder's net_gross_usd
metadata:
  type: project
---

User approved 2026-09-24 that IRS, NDFs, the FX-swap package rule and the equity index leave the app (commodity conversion Phase 2, removal top-down by layer). The header's own part was tiny (FX_SWAP stays: manual entry books 4-leg FX swaps and Jason may roll hedges that way; only the blotter package rule left): the IRS label in `_PRODUCT_LABELS` and ES / USDTWD strings in two tests.

**Why:** the book is now Jason's commodity futures plus FX hedges; the sample is the synthetic `data/sample/blotter_sample.csv` (futures, FX fwd/spot, FX options).

**How to apply:** the header's Net / Gross USD delta goes through `ui/tabs/cash_ladder.py::net_gross_usd` (ui-ladder's), which is where NDF_1M rates were applied, so NDF clean-up of those cards is ui-ladder's, not mine. The same product-label map is duplicated in `ui/tabs/blotter_pricing.py` (ui-shell's). Phase 3 reworks the header for the commodity book; Net / Gross cards stay until then.
