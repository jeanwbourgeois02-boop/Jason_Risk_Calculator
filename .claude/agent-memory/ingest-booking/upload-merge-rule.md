---
name: upload-merge-rule
description: Since 2026-09-28 an upload MERGES by Trade Id (replace / add / remove cancelled), no longer a full replace; what stays, what is dropped, and the pins that changed
metadata:
  type: project
---

User decision 2026-09-28 ("yeah that makes sense"): an upload is a merge by Trade Id, replacing
the full replace of 2026-09-17. **Why:** the broker's export is cumulative and corrects fills in
place, so a file is not the whole book any more.

Rules built into `upload._stage_and_publish` (the accounting comes back as `change`):
- replaced Trade Id: `trades` upserted, `trade_legs` and `realised_pnl` of that id deleted from
  live before the merge loop (legs re-inserted from the file, the ledger re-freezes); a hand-set
  `trades.theme` is restored where the file's row carries '' (the parser never fills theme, so the
  upsert would blank it); `instrument_theme` untouched.
- removed: `ParseResult.cancelled_trade_ids` (read via getattr) that are on file and not also
  loaded, plus every `source = 'MANUAL'` row the file does not name. Deleted from both staged and
  live, or the merge loop copies them back.
- a trade the file does not name stays. A BNP leftover is NOT removed by an upload any more:
  `schema.purge_retired_sources` at start-up does that.
- `upload_report` gained `added`, `replaced`, `removed`, `on_file_after`; `record_upload_report`
  ALTERs a missing column in, `last_upload_report` fills defaults without migrating (read-only safe).

**How to apply:** never reintroduce a DELETE of the whole book; a wording test pins
"N trades in the file: a added, r already on file (replaced by the file's rows), c removed as
cancelled; t trades on file." The old data-ingest note "Upload full replace" is superseded.
Related: [[phase2-removal]].
