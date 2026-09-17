---
name: schema-generic-migration
description: data/ingest/schema.py::_migrate_columns is now DDL-driven (parses every CREATE TABLE IF NOT EXISTS block) instead of a hand-maintained additions list, after the list going stale caused a live 500 (instrument_options.payoff missing)
metadata:
  type: project
---

**Bug (reported live by the user, 2026-09-17):** `ui/tabs/options.py`'s Options sub-tab threw
`sqlite3.OperationalError: no such column: o.payoff` on the user's real dev DB
(`data/raw/risk.db`). Root cause: `instrument_options.payoff` was added to the DDL (`_DDL` in
`schema.py`) with a `DEFAULT 'VANILLA'`, but `_migrate_columns`'s old `additions` list (a
hand-maintained `[(table, column, coldef), ...]`) was never updated to include it — so
`CREATE TABLE IF NOT EXISTS` (a no-op on an existing table) never added the column to any DB
created before that DDL change. `ui-shell` worked around it defensively in
`ui/tabs/options.py::_instrument_options_columns` (a live `PRAGMA table_info` check + COALESCE
in the SQL), correctly identified the real fix belonged here, and reported it rather than
editing schema.py itself.

**Fix:** `_migrate_columns` now parses its own DDL text generically
(`_parse_ddl_columns` / `_split_top_level` / `_CREATE_TABLE_RE` in `schema.py`) — for every
`CREATE TABLE IF NOT EXISTS <name> (...)` block across `_DDL + _LEDGER_DDL + _SWAP_REVIEW_DDL
+ _BUNDLES_DDL`, it extracts `(column_name, column_definition_sql)` pairs in order, skipping
table-level constraint lines (`PRIMARY KEY (...)`, `FOREIGN KEY (...)`, ...) by checking
whether the first token of each comma-split fragment is a constraint keyword rather than a
column name. Then for every existing table (checked via `PRAGMA table_info`), any DDL column
missing from the live table gets `ALTER TABLE {table} ADD COLUMN "{name}" {coldef}` — so
adding a column to the DDL is now the ONLY place that needs editing; there is no second list
to remember to update. Verified against all 12 tables in the current schema (correctly
extracts every column, correctly skips every `PRIMARY KEY (...)` line, correctly handles the
quoted-identifier columns `curve_quotes."index"` / `index_fixings."index"`).

**Constraint this imposes on future DDL edits:** a column added to `_migrate_columns`-covered
DDL that's `NOT NULL` MUST carry a `DEFAULT` other than `NULL` in its own DDL text — SQLite
refuses `ALTER TABLE ADD COLUMN` on a `NOT NULL` column with no default. Every column in the
current schema that plausibly needs migrating onto a pre-existing table already has one (the
schema's own "documented sentinel" convention — `''`, `0`, `'VANILLA'`, `'9999-12-31'`, etc.),
so this wasn't a problem in practice, just worth remembering next time a column is added.

**Test fixture pattern** (see `tests/test_upload.py::OLD_INSTRUMENT_OPTIONS_DDL` and the two
tests using it): to test a migration path, hand-write the OLD `CREATE TABLE` (pre-column) as a
raw SQL string fixture, execute it directly (not via `schema.create_schema`) against a fresh
`sqlite3.connect(':memory:')`, then call `schema.create_schema(conn)` and assert the new
column appears with the DDL's own default. This is the only way to exercise
`_migrate_columns` at all, since a freshly-created DB never has anything to migrate.
