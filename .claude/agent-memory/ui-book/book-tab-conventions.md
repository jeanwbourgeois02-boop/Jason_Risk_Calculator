---
name: book-tab-conventions
description: How ui/tabs/book.py is built (Phase B 2026-09-25, rewritten wave 1 and extended wave 3 on 2026-09-28): gather/body split, memo, the chained VaR and history callbacks, the Last load section's source, golden-book test pitfalls (read-only URI)
metadata:
  type: project
---

The Book tab is `gather(conn, as_of)` -> `body(data)`: each lane read sits in its own try so one
failure costs one section. Since wave 1 (2026-09-28) it is one table by sector (spread positions +
outright contracts), "Needs you" (alerts) and "Last load". Every number is another lane's.

**Why:** the user wants one home screen ("what is my book, as spreads; what needs me") in plain
words; one place per number.

**How to apply:**
- The VaR is never computed in the body: `_risk_if_ready` reads the header's memo; the body callback
  outputs `VAR_PENDING_ID`; a chained callback calls `header.risk_summary` and re-renders `ALERTS_ID`
  (inside the body: needs the app's suppress_callback_exceptions, which ui/app.py sets).
- The row detail's own-marks history (wave 3, 2026-09-28) is the same pattern: `history_slot` puts a
  `dcc.Store(DETAIL_HISTORY_REQUEST_ID)` (data = `spreads_ui.history_request(payload, as_of)`) and a
  `dcc.Loading` in the detail; a callback on that store calls `render_history`, which is
  `spreads_ui.history_block` with every `dcc.Graph` re-pointed to `DETAIL_HISTORY_GRAPH_ID`
  (`_repoint_graphs`). Rule: anything borrowed from spreads.py gets a `book-detail-*` id, because the
  hidden Spreads body may hold the original id on the same page. About 2 s on the sample book.
- "Last load" reads the persisted `upload_report` row through `data.ingest.upload.last_upload_report`
  (`load["report"]`, None when absent -> "No upload recorded on this database", the trades-on-file
  count on hover); the whole upload `summary` is the first line's hover. Zero kind counts are left
  out of the sentence.
- `book_spreads` and `needed_marks` are memoised on (path, mtime, as_of) in `_MEMO`.
- Tests / direct renders: open the golden db with a read-only sqlite URI, never `schema.connect` (it
  rewrites the views, so the mtime moves and every render is cold). Patch `book._open`, not `ui.app`.
  Console prints of the rendered tree need `PYTHONIOENCODING=utf-8` (the about() glyph).
- On the golden book at 2026-09-18 the research db on this PC has no stats before 2026-09-21, so every
  sigma is n/a; test sigma with a synthetic stats entry.
- Tab pointers are clickable (`book.pointer(label, idx)` wraps `formatting.tab_link`; `book.TAB_KEYS`
  copies `ui.app.TAB_KEYS`). idx rule: "book-<spot>", alerts "book-alert-<n>".
- `tests/test_ui_book.py` is stale since wave 1 (it still names `pnl_rows`, `PNL_TABLE_ID`): the user
  wants tests once at the end of the batch, not per wave.

Related: [[ui-redesign-wave1-2026-09-28]] (ui-shell's memory), [[ui-spreads conventions in .claude/agent-memory/ui-spreads]]
