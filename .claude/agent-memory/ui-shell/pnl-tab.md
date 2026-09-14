---
name: pnl-tab
description: ui/tabs/pnl.py design -- wired into "Overall book" (not "FX"), lazy engine imports, blocks built from aggregate_by_pair/book_totals/period_pnl
metadata:
  type: project
---

`ui/tabs/pnl.py` (created 2026-09-14, second session) implements the P&L tab task item.

- **Wired into "Overall book"**, not "FX". Reasoning kept in the module docstring: the
  totals block (net/gross/gold/futures USD) and the period block (daily/5d/mtd/ytd) are
  exactly the book-wide "P&L rollups ... LTD series" CLAUDE.md's "Six tabs as views"
  table assigns to Overall book; the FX row in that table is scoped to "FX trades x
  marks_official" (forward-outright-level detail), not aggregate/period rollups. The
  per-pair block (from `aggregate_by_pair`) is included on the same tab as the totals it
  feeds into, rather than split across two tabs.
- Same lazy-import-inside-callback pattern as [[cash-ladder-tab]]: `engine.pnl.pnl` and
  `engine.pnl.aggregate` are imported inside the Dash callback body only, wrapped in
  try/except ImportError -> grey message box, so `ui.tabs.pnl` and `ui.app` import
  cleanly even if engine/pnl/ is absent or mid-change.
- Calls `ltd_per_trade(conn, as_of_date, source=param_source, strict=False)` --
  `strict=False` deliberately, so a missing mark/spot surfaces as NaN (-> blank cell
  per formatting.format_cell) instead of raising and breaking the whole tab.
- Reuses `ui.tabs.controls` (SOURCE_OFFICIAL, source_value_to_param,
  build_source_dropdown, build_date_picker) and `ui.tabs.formatting.format_cell`,
  factored out of [[cash-ladder-tab]] in the same session -- see that memory for why.
- Own component ids, distinct from cash_ladder's, so the two tabs' callbacks never
  collide in `app.callback_map`: `pnl-source`, `pnl-date`, `pnl-content-container`
  (plus inner DataTable ids `pnl-pair-datatable`, `pnl-totals-datatable`,
  `pnl-period-datatable`).
- Three pure, DB-free render functions, each unit-tested with hand-built
  DataFrames/dicts (never call the engine from tests, per task constraint):
  `pairs_table_from_by_pair(by_pair_df)`, `totals_table_from_book_totals(totals_dict)`,
  `period_table_from_period_pnl(period_dict)`. The period table shows one row per
  period (LTD, Daily, 5d, MTD, YTD) with a `ref_date` column sourced from
  `period_pnl`'s `<key>_ref_date` keys -- blank for the LTD row, which has no single
  reference date (cumulative since inception).
