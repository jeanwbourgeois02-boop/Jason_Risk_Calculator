---
name: marks-by-month-and-skip-2026-09-30
description: Why marks travel as marks/<YYYY-MM>.csv, the <db>.snapshot_state.json sidecar, what the fingerprint can miss, and the measured costs (2026-09-30)
metadata:
  type: project
---

2026-09-30, the user asked to speed up a slow Bloomberg pull; bbg-live traced part of it to `save_after_pull` rewriting and re-reading every table whole under a read lock.

- **Layout:** marks are one file per as_of_date month, `data/bbg_snapshot/marks/<YYYY-MM>.csv`. Put the month files together in name order (dropping the repeated headers) and you get the old single `marks.csv` byte for byte, because the PK starts with as_of_date. The export deletes an old `marks.csv`. The import reads `marks/*.csv`, or `marks.csv` if there are none. Each file is read with its own header.
- **Skip:** `<db>.snapshot_state.json` sits next to the database. It is this PC's own record (data/raw is git-ignored), keyed by the resolved out_dir, and holds each file's fp, sha1, size and mtime_ns. A file is left alone, neither read nor written, when its fp and on-disk stat match that record. If only the fp changed, the rows are fetched and the new bytes' sha1 is compared with the record, so the old file is never read. It is read only when the stat no longer matches (a git checkout or a hand edit).
- **Fingerprint** (`_aggregates`): COUNT, SUM(rowid) and MAX(rowid). Each numeric column adds TOTAL(value). Each text column adds TOTAL(length), plus TOTAL(julianday) when its name ends in "date" or "_at". For marks it is GROUP BY as_of_date, which walks the PK autoindex so SQLite does no sort; a month's fp is its list of day rows.
  **Why rowid is not enough:** options store, backfill and contract_dates all delete and reinsert marks. Deleting the top rows and inserting the same number again reuses the same rowids.
  **Blind spot:** a number moving by less than the float rounding of its day's total (about 1e-9), on a row that also kept its rowid and snap time. Accepted as cheap enough; the caller asked for a cheap aggregate.
- **Costs** (326k synthetic marks, 13 months): the old export held its read lock 1.2 to 1.6 s on every press. The new one takes 0.3 s with nothing changed and 0.3 to 0.45 s after a live press, which rewrites only the current month. The first export in the new layout takes about 0.9 s, once. Benchmark: a GROUP BY on substr(month) sorts and costs 0.32 s; GROUP BY as_of_date costs 0.18 s. Per-column MIN/MAX and length() on REAL columns tripled the cost, so they were dropped.
- **Messages:** the save_after_pull message is unchanged when files were written. With nothing new it reads "marks snapshot: N marks through D, data/bbg_snapshot/ already up to date". The public `export_snapshot` still returns the manifest alone; `_export` also returns the names of the files that changed.
- Timing an import in this environment swings from 10 s to 360 s for the same data because other agents load the machine. A profile shows about 10 s, almost all of it in SQLite's executemany.

**How to apply:** if a new writer to a market table changes values in place (UPDATE) without changing any date or text column, widen `_aggregates` for that column.

Related: [[contract-static-in-snapshot-2026-09-24]], [[macro-tables-removed-2026-09-24]].
