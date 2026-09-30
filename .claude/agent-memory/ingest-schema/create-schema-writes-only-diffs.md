---
name: create-schema-writes-only-diffs
description: Since 2026-09-30 create_schema / purge_retired_sources write nothing on an up-to-date database; why (mtime-keyed caches) and what must stay true when editing them
metadata:
  type: project
---

2026-09-30 (user asked to cut a slow, wasteful Bloomberg pull; bbg-backfill measured it): every `schema.connect()` used to drop and recreate both views and commit, ~20 ms and a moved file mtime per call. Every (mtime_ns, size)-keyed screen cache (`ui/revision.py`, the shared pricing memo) then re-valued the whole book after each of a pull's connects. Now:

- `create_schema` reads `sqlite_master` first. Tables / index / triggers (`_TABLES_DDL`, the one concatenation `_migrate_columns` parses too) and `_migrate_columns` run only when an object, a DDL column or the `bbg_library_state` row is missing. A view is dropped and recreated only when its whitespace-normalised SQL differs from `_view_definitions()` (split out of `_views_ddl()`), so a definition change still reaches an old database (the CLAUDE.md rule). The writes run in one `BEGIN ... COMMIT` script; on error it rolls back so a read-only connection never keeps a snapshot open.
- SQLite stores view / table SQL without `IF NOT EXISTS`, and a view's stored text is exactly as written; normalising whitespace is enough.
- `purge_retired_sources` checks with SELECTs and returns all-zero counts without opening a write when there is nothing to purge.
- Side effect: an up-to-date DB's `create_schema` no longer commits a caller's pending transaction (executescript used to). No caller relied on it (checked blotter.load, themes, ui/app.ensure_schema, upload's in-memory staging).
- Proof recipe: copy `data/raw/risk.db` to the scratchpad, connect twice, compare `os.stat` (mtime_ns, size); load HEAD's schema.py via `git show HEAD:...` + importlib for the before figure (HEAD moved mtime, ~20 ms; new ~3-5 ms, untouched).

**Why:** the screens' caches key on the file's mtime; any no-op write costs a full re-pricing.
**How to apply:** never add an unconditional write (DROP/CREATE, INSERT OR IGNORE, DELETE) to the connect path; add a new object's name to the DDL so `_expected_objects` sees it. Related: [[schema-migration-rules]].
