---
name: price-history-in-snapshot-2026-09-30
description: price_history (Bloomberg risk history) travels in the snapshot by month; why it groups by day, how changed months are read, measured costs (2026-09-30)
metadata:
  type: project
---

On 2026-09-30 the user decided that risk history comes from Bloomberg into this app's own `price_history` table, replacing the research app's database. ingest-schema owns the DDL: PK (instrument_id, as_of_date), index `ix_price_history_date` on as_of_date, no FK to instruments. It has no MANUAL rows.

- **Export:** it is in MARKET_TABLES. `MONTHLY_TABLES` maps table to folder, so it writes `price_history/<YYYY-MM>.csv`. Rows inside each file are in PK order (instrument, then date). Putting the months together does NOT give one PK-ordered file; this differs from marks.
- **Fingerprint:** `_monthly_plan` uses GROUP BY as_of_date, which walks the as_of_date index. A month's fp is its list of day rows, the same shape as marks.
  **Why:** GROUP BY substr(month) sorts. It cost 0.68 s against 0.33 s by day on 270k rows.
- **Reading changed months:** each is read with a date range (`_month_where`). When every month changed (a first export), it does one ordered pass split in Python instead. A range per month without the index would scan the table once per month.
- **Stale month files** are removed only for the monthly tables the source database has. If the source has no price_history table at all, an existing `price_history/` folder is left alone, like the other single-table CSVs.
- **Import:** it reads `price_history/*.csv`, or a `price_history.csv` if there are none. There is no instrument filter. The source column guards the drop: without one, the whole table is dropped. A snapshot without price_history leaves this PC's rows as they were. A database without the table gets it from the manifest's DDL, and `create_schema` adds the index on the next start.
- **Costs** (270k rows, 60 months): the first export takes 4.5 s. An unchanged export takes 0.33 s. With two months touched it takes 0.39 s. The import takes about 5 s.

Related: [[marks-by-month-and-skip-2026-09-30]].
