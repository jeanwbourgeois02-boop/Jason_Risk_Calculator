---
name: currency-tables-k-m-2026-09-25
description: FX sub-tab's two P&L-by-currency tables print k / M (amount_short on whole_units), full figure on hover; trade table stays full; tests compare numbers
metadata:
  type: project
---

The two P&L-by-currency tables (fixed and rows-shown) print money in k / M since 2026-09-25 (user yes, "Screens redesign plan" → "Natural units"): `rk.amount_short(nully="n/a")` on cells rounded by `rk.whole_units`, and `_with_full_figures` puts "1,650,590 USD" on every money cell's hover, with the cell's own note (excludes N / earlier close) on the next line; an n/a cell keeps its reason alone. The Trades column stays `rk.count()`. The FX trade table keeps full figures (`rk.amount()`).

**Why:** summary tables read in k / m app-wide; trade rows keep full figures. d3's SI prefix prints 0.4 as "400m" (milli), hence whole_units is mandatory.

**How to apply:** the rows-shown sums still come from the hidden `*_num` columns of the trade table, never from these rounded display cells. Tests: `_CellView.value` in tests/test_ui.py is the number the cell carries; the strip-equals-Total test compares numbers (`row_scoped_headline` value → rounded cell value), not printed text. The combined `-k fx` run over tests/test_ui.py takes ~12 min; run with a long timeout or in the background.
