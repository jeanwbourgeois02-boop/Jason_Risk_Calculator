---
name: parse-check
description: data/ingest/parse_check.py (2026-09-30), the Data tab's parsing diagnostic: row-by-row dry run of an upload, read-only; _PARSE_LOCK in blotter.parse; choices made
metadata:
  type: project
---
User 2026-09-30 wanted an in-app "parsing diagnostic" on the Data tab: drop a blotter, see per row how it
reads and what an upload would do, nothing saved. Engine side is `parse_check.check_file(payload, filename,
db_path) -> dict` (ui lane builds the screen).

**Why:** Jason and the user fix the file themselves; they need to see each row's outcome before uploading.

**How to apply:**
- It must never write: db opened `mode=ro` (missing db = empty book, never created); only `blotter.parse`,
  never `load` / `write_parsed`. It never raises (ok False + plain `error`).
- `blotter.parse` is now wrapped in `_PARSE_LOCK` (RLock; body moved to `_parse_locked`) because
  `_DAY_FIRST` / `_CONTRACT_CONN` are module globals and Dash callbacks overlap. Keep any new global
  per-parse state under that lock.
- The parser does not record book-filter exclusions or superseded rows per row: parse_check recomputes
  them (`BookFilter.excluded_by`, `_dedupe_versions` on the same canonicalized frame; row_no = index + 2).
  If the parser ever records them, read those instead.
- My choices (reversible): a superseded row is status EXCLUDED with "Replaced by row N ..."; counts carry
  an extra `superseded`, merge extra `remove_manual` / `on_file_before` / `on_file_after`; rows carry an
  extra `trade_date`. The parser's information notes naming instrument ids (options without strike, LME
  prompt notes) are rewritten in plain words by row number; unrecognised reasons still name root ids
  (e.g. 'ZCE:ZC, CBOT:ZC') because they are the parser's reason text.
Related: [[every-row-loads]], [[broker-raw-cells]].
