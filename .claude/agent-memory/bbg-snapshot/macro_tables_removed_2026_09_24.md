---
name: macro-tables-removed-2026-09-24
description: MARKET_TABLES lost index_fixings, rate_vol_quotes, equity_dividend_yields in commodity Phase 2; the macro trader's data/bbg_snapshot/ was deleted; how old snapshots are handled
metadata:
  type: project
---

Commodity conversion Phase 2 (user yes, 2026-09-24): the macro trader's products left the app.
MARKET_TABLES is now ("marks", "curves", "curve_quotes", "vol_quotes", "contract_static").
curve_quotes / curves stay because the OIS curves still discount options; vol_quotes is FX options (dormant).

- The macro trader's own snapshot files under data/bbg_snapshot/ (AUDUSD, USDKRW, USDTRY marks) were deleted with plain rm, not git rm (shared index); the next real pull on the Bloomberg PC writes Jason's snapshot there.
- An older snapshot still carrying index_fixings.csv / rate_vol_quotes.csv / equity_dividend_yields.csv (and their DDL in the manifest) imports fine: the import loops MARKET_TABLES only, so those files are ignored, no table is created and this PC's own rows in them are not dropped. Pinned by test_an_older_snapshot_carrying_the_removed_tables_imports_without_them.
- The test fixture `_bloomberg_pc` no longer imports rates_vol_marketdata or engine.options.equity_commodity (other lanes are deleting them); tests for the removed tables use raw DDL (LEGACY_DDL) instead.

**Why:** the app became Jason's commodity book; swaps, swaptions / caps and SPX options are gone.
**How to apply:** do not re-add those tables to MARKET_TABLES without the user's say-so; keep the fixture free of modules other lanes own and may delete.

Related: [[contract-static-in-snapshot-2026-09-24]].
