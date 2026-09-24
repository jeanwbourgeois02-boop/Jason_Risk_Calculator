---
name: ladder-transposed-and-ndf-display-2026-09-21
description: Ladder grid transposed (currency rows, date columns, bottom USD-equivalent row) with the rate/delta block as its own table above; NDF label / 1M rate cell / captions; DataTable facts and the test-file boundary that came up
metadata:
  type: project
---

SUPERSEDED IN PART (2026-09-24): every NDF rule, label, 1M rate and caption below was removed
from the tab, and the test-file boundary note is stale; see [[phase2-macro-removal-2026-09-24]].
The grid-shape and DataTable facts still hold.

2026-09-21, user decisions recorded in CLAUDE.md "Ladder". `ui/tabs/exposure.py`:
`summary_block_frame/_table` (id `SUMMARY_BLOCK_TABLE_ID`, currencies across, rows FX rate
(as quoted) / Local delta / USD delta / Rate source, net USD delta total in `TOTAL_COL`)
sits ABOVE the grid; `combined_frame/_table` (id `COMBINED_TABLE_ID` unchanged) is now one
row per currency, columns = `SETTLED` key ("settled", headed "Settled cash"), ISO dates,
`TOTAL_COL` "Total (columns shown)", plus a bottom "USD equivalent" row (kind
`usd_equivalent`). `grid_shape` gives (dates, currencies) to the block, grid, heatmap and
CSV; `_grid_numbers` is the ONE numeric computation behind both the screen and
`ladder_export_frame`. [[cash-ladder-tab]] and [[exposure-vs-grid-settle-date-split-2026-09-16]]
describe the older shape.

**Why:** the user wants currencies down / dates across, the delta block on top, and NDFs
shown on fixing dates at the 1M NDF price, never spot.

**How to apply / facts worth keeping:**
- Row label = `engine.ladder.ndf.currency_label` ("KRW (NDF, fixing dates)"); the plain code
  lives in data field `currency` and in the block's column ids, so the currency filter and
  ids never see the label.
- Rates now come from `apply_ndf_1m_rates(conn, rates_from_marks(conn))` in BOTH
  `cash_ladder.load_inputs` and `net_gross_usd` (header and ladder must agree). An NDF
  currency with no official NDF_1M mark has NO rates entry even when a SPOT is on file:
  blank FX rate / USD delta, "MISSING: <engine message>" in Rate source, full sentence in
  `rate_reasons_caption`. A stale mark is status STALE and still priced.
- dash 4.4.1 DataTable `filter_query` field regex is `^{(([^{}\\]|\\.)+)}` (checked in the
  bundle): any column id without braces/backslash works, so ISO dates as column ids are
  fine in `{2026-09-24} contains '('`.
- `fixed_columns` was NOT used: the label column is pinned with inline `position: sticky;
  left: 0` + opaque background. NOT checked in a browser (the brief forbade browser
  proofs). If it misbehaves, removing the `sticky` dict in `_grid_datatable` is harmless.
- The test helpers `_all_ids` / `_find_id` in tests/test_ui_ladder.py only walk LIST
  children: `html.Div(single_component)` hides everything under it from them. Wrap in a list.
- The old CSV grand total used `.sum(skipna=False).sum()`, which silently dropped a
  currency with no rate; the CSV now uses the screen's numbers (blank stays blank).
- Boundary: tests/test_ui_ladder.py and tests/test_ui_ladder_view.py are outside my standing
  edit boundary (`ui/` + tests/test_ui.py) even when a caller lists them as mine. Say so in
  the first lines, do the ui work, and hand over rewritten copies (verified by running them
  from the scratchpad with `--rootdir`) for the main session to copy in. tests/test_ui.py
  was being appended to by another ui agent at the time, so it was left alone too.
