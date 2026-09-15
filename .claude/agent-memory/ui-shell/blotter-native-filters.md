---
name: blotter-native-filters
description: 2026-09-15 blotter rewrite to DataTable native header filter/sort instead of toolbar dropdowns
metadata:
  type: project
---

User decision 2026-09-15: `ui/tabs/blotter.py` main trade table filtering/sorting
moved from toolbar dropdowns into `dash_table.DataTable`'s own header row
(`filter_action="native"`, `sort_action="native"`, `sort_mode="multi"`).

**Why:** `dash_table.DataTable` has no built-in dropdown *filter* widget — the
`dropdown` prop only affects cell *editing*, never the filter row. So even the
categorical columns named in the task (status, product, instrument_id, strategy,
theme) use the native text filter row, which accepts bare values plus operators
(`>=`, `<=`, `contains`, ...). This is also how the trade-date range filter works now
(type `>= 2026-06-05` into the Trade Date filter cell) — the old
`DATE_FROM_ID`/`DATE_TO_ID` toolbar inputs were removed.

**How to apply:** Toolbar keeps only the as-of date picker, group-by dropdown (it
reshapes the table into a summary, so stays a real control) and the theme-edit
input. The subtotal line above the detail table is driven by a *second* callback on
`Input(DATATABLE_ID, "derived_virtual_data")` (id `blotter-subtotal`) so it reflects
only rows visible after native filtering; `subtotal_line()` parses `pnl_usd` back out
of its `format_cell` display string ("1,234" / "(1,234)" / "" / "Unavailable") since
`derived_virtual_data` only sees the table's already-formatted string cells, not the
underlying numeric value_book frame. The group-by summary table stays whole-book
(a `period_pnl_by` call unfiltered by the DataTable), with an explicit one-line
caption saying so — mixing it with the row filter would silently disagree with the
per-row totals.

A row counts Unavailable only when its `reason` column is non-empty (never merely
because `pnl_usd` is NaN); `note` is always a plain grey informational column
(`style_data_conditional` on `column_id == "note"`), never a driver of row status.

See also [[wiring-c5]] for how `ui/app.py` assembles the tab, and CLAUDE.md's
"Six tabs as views" Blotter row for the underlying `value_book` contract.
