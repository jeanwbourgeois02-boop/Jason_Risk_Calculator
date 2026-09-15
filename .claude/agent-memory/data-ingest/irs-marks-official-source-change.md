---
name: irs-marks-official-source-change
description: PAR_RATE/PV_USD/DV01_USD official mark source changed BBG_BDH -> QL_PRICER (2026-09-15); curve_quotes staging table added; upload.py generic column-copy loop must quote column names
metadata:
  type: project
---

2026-09-15, housekeeper-authorized contract change (part of the same task that wired
INTEREST_RATE_SWAP rows into `data/ingest/irs.py`): in `data/ingest/schema.py`,
`OFFICIAL_MARK_SOURCE['PAR_RATE']`, `['PV_USD']` and `['DV01_USD']` now map to
`'QL_PRICER'` instead of `'BBG_BDH'`. `BBG_BDH` becomes reconciliation-only for these
three mark_types (mirrors `BNP_BVAL` being reconciliation-only for FX). No other
mark_type mapping changed. `rates-pricer`'s own bootstrap is expected to write
`QL_PRICER` marks; until it does, `marks_official` will simply have no row for these
mark_types (same "missing stays missing" behaviour as any other gap).

Also added `curve_quotes` (raw Bloomberg curve-quote staging table, columns
`as_of_date, ccy, index, tenor, ticker, value, quote_type, field, source`, PK
`(as_of_date, ccy, index, tenor, source)`) for `data/bloomberg` to write into and the IRS
pricer bootstrap to read. This is NOT `curves` (which holds bootstrapped discount
factors/par rates) — it's the pre-bootstrap raw quotes.

Gotcha: `curve_quotes.index` is a bare column named `index`, a reserved-ish SQL word.
`data/ingest/upload.py`'s generic per-table copy loop (`for table in schema.TABLES: ...
INSERT INTO {table} ({names}) ...`) previously built column lists unquoted and broke with
`sqlite3.OperationalError: near "index": syntax error` the moment `curve_quotes` appeared
in `schema.TABLES`. Fixed by double-quoting every column name in that loop's `names` /
`updates`. If any other agent adds a generic "iterate all tables, build a column list"
loop anywhere in the codebase, it must quote column names too or it will break the same
way the instant `curve_quotes` (or any future table with a reserved-word column) exists.

**Why:** rates-pricer becomes the source of truth for IRS valuation marks per housekeeper
authorization; QuantLib-style pricers commonly need raw curve inputs staged separately
from bootstrapped output, hence the new table.
**How to apply:** don't be surprised if `marks_official` has no PAR_RATE/PV_USD/DV01_USD
rows until rates-pricer's bootstrap lands — that's expected, not a regression. When adding
any new table with an unusual column name, grep for other bare (unquoted) column-name
loops across the repo before assuming they're safe.
