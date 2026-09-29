---
name: upload-history
description: Since 2026-09-29 every successful upload appends a row to upload_history (counts + added/replaced/removed trade ids as JSON); upload_report stays the one-row last load
metadata:
  type: project
---

User approved 2026-09-29 a Blotter tab that is the audit trail of Jason's uploaded file, with an
upload history so a missing trade can be traced to the file it came from.

- `upload_history` is written by `_append_history` inside `record_upload_report`'s own
  transaction, so a history row exists exactly when an `upload_report` row was written.
- `removed_ids` holds cancelled AND old-MANUAL removals; the counts stay apart
  (`removed` = cancelled, as upload_report; `removed_manual`).
- Never back-filled from `upload_report` (user brief): an old database's history starts at its
  next upload; the sample book (`ui/sample_book.py`, no upload) has none.
- Readers are read-only (no CREATE): `upload_history`, `trade_upload_trail`, `last_upload_issues`.

**Why:** the audit trail must stay trustworthy, so only real uploads write it.
**How to apply:** do not add a second writer, and keep `upload_report` unchanged: the Book reads it.
Related: [[upload-merge-rule]].
