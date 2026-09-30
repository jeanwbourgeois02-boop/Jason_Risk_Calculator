---
name: upload-box-two-lines-2026-09-30
description: The top bar's upload result box is two short lines (loaded counts, problems + link to Data Trades card); no summary paragraph or notes list
metadata:
  type: project
---

User, 2026-09-30, on the box under the top bar after "Confirm insert": "massive text thing ... i dont like it".
`ui/uploads.py::report_result(report, filename)` now renders at most two lines:
- `loaded_line`: "Loaded <file>: N new, N updated[, N removed as cancelled] · N trades on file" (counts from the
  ingest report's added / replaced / removed / on_file_after, carried in `normalise_report`'s `merge`).
- `problems_text` + `tab_link("See Trades on the Data tab", "market-data", "top-bar-trade-problems")`, only when
  `is_sticky` (need_fix, rejects, warnings, or the REJECTS_PHRASE sentence). Info-only notes never make line 2.
`headline_without_notes`, `NOTES_MAX_HEIGHT` and the notes list are gone; the unstructured `import_result` path stays.

**Why:** the whole paragraph and every problem are already on the Blotter's last-upload line and the Data tab's
Trades card (the one place a trade problem is written out).
**How to apply:** never put the ingest's summary or notes back in the top bar. `.top-bar .tab-link` is white (navy
bar), so a link inside the white result card needs `.top-bar .source-result-box .tab-link` (style.css) to be navy.
