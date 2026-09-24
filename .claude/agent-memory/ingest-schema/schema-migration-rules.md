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

**Why:** a stale hand-kept migration list caused a live 500 in 2026-09-17 (`instrument_options.payoff`).
**How to apply:** check these when you add a table, add a column or retire a table. Related: [[phase2-macro-removal-2026-09-24]].
