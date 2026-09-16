---
name: reconciliation-upload-declutter-2026-09-16
description: Reconciliation tab's futures-import card no longer sits under the as-of date picker; date picker moved to its own toolbar
metadata:
  type: project
---

2026-09-16: user found the date-picker directly above "Import futures fills from
workbook" confusing — it looked like part of the upload step but
`data.ingest.xlsx_futures.load_futures_fills()` reads trade dates from the workbook
rows, never from that UI field. The date picker actually only drives which day
`_update_content`'s reconciliation tables show.

Fix in `ui/tabs/reconciliation.py`: `build_layout` now puts `build_date_picker` +
`build_source_dropdown` together in a labelled `.toolbar` block ("Reconciliation
tables show"), physically separate from `futures_import_control()`, which is now a
standalone `.upload-card` (new CSS in `ui/assets/style.css`) with just a title, one
hint line, choose-file button + filename, and Confirm — no date field. Component ids
(`DATE_PICKER_ID`, `FUTURES_UPLOAD_ID`, `FUTURES_CONFIRM_ID`) unchanged, so the content
callback's `Input(DATE_PICKER_ID, "date")` wiring and `_futures_confirm` callback both
still work exactly as before; only the layout grouping changed.

**Why:** proximity in a layout reads as functional coupling to users even when the
code has none — worth flagging/fixing whenever a control sits next to an unrelated
action button.
**How to apply:** when a tab has one control area feeding two independent callbacks,
give each its own visually distinct block (card vs toolbar) rather than stacking them,
even if it means duplicating a a small CSS class.

See [[futures-upload-diagnostics-2026-09-16]] for the underlying upload/parse plumbing.
