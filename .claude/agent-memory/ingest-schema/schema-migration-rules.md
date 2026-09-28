---
name: schema-migration-rules
description: Non-obvious constraints of schema.py carried over from data-ingest: DDL-parsed _migrate_columns, quoted "index" columns, how to test a migration
metadata:
  type: project
---

Carried from `.claude/agent-memory/data-ingest/schema-generic-migration.md` and `irs-marks-official-source-change.md` (lane split 2026-09-24).

- `_migrate_columns` parses the DDL strings themselves (every `CREATE TABLE IF NOT EXISTS` block in the concatenation it is given). A new DDL string must be added both to `create_schema`'s executescript and to `_migrate_columns`' parse list. In 2026-09-22 the library DDL was missing from that list, so `code_version` never reached old DBs.
- SQL comments inside a DDL string are stripped before parsing, so comments between CREATE blocks are safe.
- `curve_quotes."index"` is a reserved word. Any generic loop over `TABLES` that builds column lists must quote names (upload.py does).
- To test a migration, write the old CREATE by hand on a raw sqlite3 connection, then call `schema.connect(path)` / `create_schema`. A fresh DB never has anything to migrate.
- An index (`ix_marks_key_date`, 2026-09-28) lives in `_DDL` as `CREATE INDEX IF NOT EXISTS`; `_CREATE_TABLE_RE` only matches CREATE TABLE blocks, so the migration parser ignores it and the IF NOT EXISTS alone puts it on an old DB at start-up. Verified with `PRAGMA index_list('marks')` on a DDL-minus-index copy. Both branches of `marks_official` pick it up in EXPLAIN QUERY PLAN.
- Adding a column to `trades` (2026-09-28: `pb_root`, `trade_type`, both `TEXT NOT NULL DEFAULT ''`) breaks nothing in app code: `blotter.py` names its INSERT columns from the `Trade` dataclass in `common.py` (a column the dataclass lacks takes its DEFAULT), `upload.py`'s merge loop reads columns from `PRAGMA table_info`, and `trades_official` is `SELECT *`. What it does break is every test fixture written as `INSERT INTO trades VALUES (...)` with the old value count (about 100 statements in 25 test files outside this lane at the time); the same happened when `theme` was added. Report them by owning lane in the Handoff; the housekeeper decides whether infra fixes them in one mechanical pass.
- A `trades` column's `-- comment` in the DDL may span two lines (continuation lines start with `--`); `_strip_sql_comments` removes them before parsing, and `_CREATE_TABLE_RE` still needs the block to end in `
);`.

**Why:** a stale hand-kept migration list caused a live 500 in 2026-09-17 (`instrument_options.payoff`).
**How to apply:** check these when you add a table, add a column or retire a table. Related: [[phase2-macro-removal-2026-09-24]].
