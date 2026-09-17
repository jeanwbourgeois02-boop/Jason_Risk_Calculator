---
name: stale-marks-official-view-2026-09-17
description: data/ingest/schema.py's marks_official/trades_official VIEW never gets regenerated on an existing DB, so IRS/Options marks are invisible even when engine/rates or engine/options write them correctly -- not a "missing data" problem, a stale-DDL one
metadata:
  type: project
---

**Not mine to fix (schema.py is data-ingest's), but load-bearing for every P&L/header
investigation in this app, so recording it here.**

`data/ingest/schema.py::_views_ddl()` generates `CREATE VIEW IF NOT EXISTS marks_official
...` from the `OFFICIAL_MARK_SOURCE` dict. The dict itself is current and correct (matches
CLAUDE.md exactly: QL_PRICER for PAR_RATE/PV_USD/DV01_USD/CASHFLOW_USD, QL_OPTIONS_PRICER
for DELTA/PREMIUM/GAMMA/THETA/VEGA/RHO/DELTA_PA). But `CREATE VIEW IF NOT EXISTS` is a
no-op when a view with that name already exists in the target database -- SQLite does not
diff/replace the view body. `ensure_schema()` (`ui/app.py`, runs at every app startup) calls
`create_schema()` -> this DDL on every launch, but on a database whose `marks_official` view
was created before the 2026-09-15 (QL_PRICER) / 2026-09-17 (QL_OPTIONS_PRICER, CASHFLOW_USD,
GAMMA/THETA/VEGA/RHO/DELTA_PA) updates to `OFFICIAL_MARK_SOURCE`, the view silently keeps its
OLD definition forever (still checks `PV_USD -> BBG_BDH`, `DELTA/PREMIUM -> MANUAL`, and has
no CASE branch at all for CASHFLOW_USD/GAMMA/THETA/VEGA/RHO/DELTA_PA, so those mark_types can
*never* be official through that view no matter what writes them).

**Confirmed live on `data/raw/risk.db` 2026-09-17**: `SELECT sql FROM sqlite_master WHERE
name='marks_official'` on that file still has the pre-2026-09-15 CASE (BBG_BDH for
PAR_RATE/PV_USD/DV01_USD, MANUAL for DELTA/PREMIUM, no CASHFLOW_USD/Greeks branch at all) --
compare against `schema._views_ddl()` called fresh in the same process, which generates the
correct, current CASE. Verified via a throwaway copy in the scratchpad + a synthetic
`QL_PRICER` PV_USD/CASHFLOW_USD mark insert: `marks_official` returned zero rows for it even
though the row existed in `marks` with exactly the right `(as_of_date, instrument_id,
settle_date, mark_type)` key -- only the `source` check was wrong.

**Effect app-wide**: IRS rows in the header/Blotter/`ui/tabs/rates.py` (owned by ui-shell)
show "no PV_USD mark" / recon_status MISSING forever, and FX_OPTION rows show DELTA/PREMIUM
as permanently unavailable, **even after a correct Bloomberg pull and a correct engine/rates
or engine/options run** -- this is not "no marks pulled yet", it is "marks were pulled/priced
correctly and the view still can't see them". Concurrent 2026-09-17 data-ingest work on
`schema.py` (a generic DDL-diffing column migrator, `_migrate_columns`/`_parse_ddl_columns`)
does not touch this -- it only handles `ALTER TABLE ADD COLUMN`, not view recreation.

**Suggested fix for whoever owns schema.py**: make view creation unconditional (`DROP VIEW
IF EXISTS marks_official` then `CREATE VIEW marks_official AS ...`, same for
`trades_official`) inside `create_schema()`/`_views_ddl()`'s caller, so every app startup
re-syncs the view body to the current `OFFICIAL_MARK_SOURCE` dict. Views have no data of
their own, so dropping and recreating one is always safe/non-destructive (unlike a table).

**How to apply:** before trusting *any* "no official mark" result from `marks_official` on
an existing (non-freshly-created) database, sanity-check `SELECT sql FROM sqlite_master
WHERE name='marks_official'` against `data.ingest.schema._views_ddl()`'s current output --
if they differ, the view is stale and every mark_type it disagrees on will silently look
"missing" regardless of what is actually in `marks`. See [[header-visible-reasons-and-trade-counts]]
for the header-side workaround (a concrete mark_inventory-based reason is still useful, but
`mark_inventory`/`marks_official` share the same blind spot -- neither can see a mark this
view drops).
