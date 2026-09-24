---
name: phase2-macro-removal-2026-09-24
description: What the schema kept and dropped when rates/IRS, NDFs, the FX-swap rule and the equity index left the app (commodity conversion Phase 2); which old tables survive on existing DBs and why
metadata:
  type: project
---

2026-09-24, commodity conversion Phase 2 (user decision recorded in CLAUDE.md "Commodity conversion plan" → Decisions; that entry, not the housekeeper brief, is the hard-rule-7 authority):

- `OFFICIAL_MARK_SOURCE` lost PAR_RATE, PV_USD, DV01_USD, CASHFLOW_USD, NDF_1M, NDF_FIX. Left: SPOT, FWD_OUTRIGHT (+ BBG_INTERP fallback), FUTURE_PX, DELTA, DELTA_PA, PREMIUM, GAMMA, THETA, VEGA, RHO. Old rows of the removed types stay in `marks` but the recreated view never shows them (a CASE with no branch returns NULL, so no source matches).
- New databases no longer get `swap_review`, `irs_direction_overrides`, `index_fixings`; `IRS_DIRECTION_DDL` / `IRS_DIRECTION_TABLES` / `_SWAP_REVIEW_DDL` are gone and `index_fixings` left `TABLES` (upload.py's per-table merge loop iterates `TABLES` and would fail on a table a new DB lacks).
- `purge_retired_sources` drops `swap_review` on old DBs (its FK to trades can block trade deletes; count key `swap_review_table_dropped`). `index_fixings` and `irs_direction_overrides` are deliberately left on old DBs: the brief allowed dropping only swap_review.
- `instruments.is_ndf` stays (always 0 now) so old INSERTs and old DBs keep working.

**Why:** the brief's rule was "never DROP a table on an existing database except swap_review"; old tables are harmless and the upload replaces the book.
**How to apply:** if a later brief asks to drop `index_fixings` / `irs_direction_overrides` on old DBs, it's a new purge step, and snapshot import (bbg-snapshot) already tolerates their absence. Leftover readers at the time: blotter.load (irs_direction import + DELETE FROM swap_review, ingest-parser), engine/rates/store.py (index_fixings, rates-pricer), engine/ladder/ndf.py (NDF_1M, ladder-grid), tools/bbg_diagnostics.py (_EXPECTED_OFFICIAL, check_index_fixings, infra). Grep for them before assuming they are gone. See [[schema-migration-rules]].
