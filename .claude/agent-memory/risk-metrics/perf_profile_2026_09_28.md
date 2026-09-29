---
name: perf-profile-2026-09-28
description: Where book_risk's time goes on the sample book (cold 3 s / warm 0.6 s in isolation), which caches survive a sample-book switch, why a 16 s "slow render" of the header's Risk chip is not reproducible in isolation, and the two cheap fixes that belong to other lanes.
metadata:
  type: project
---

Measured 2026-09-28 on the synthetic sample book (`tests.golden_book.build_book(through=today)` +
`realise_settled`), unprofiled wall time, this PC (no pyarrow, research DB `../Commodity Dashboard/var/rv.sqlite`
588 MB, 201 roots, 1.5m price_daily rows):

- `book_risk` cold (first call in the process): 2.9 s. Warm (same as-of): 0.6 s. On a FRESH database file
  in the same process (the sample switch): 0.65 s: the risk-history caches key on the research DB (path,
  mtime, size, WAL) and the per-object memos (`_roots`, `_cm`, `_pnl`), not on our database, so a
  throw-away sample file does not defeat them. Only the header's `_RISK_MEMO` (keyed on db mtime) recomputes
  once per switch, correctly. The header's own path (`header.risk_summary` under `pricing_snapshot`) gives
  the same 2.6 s / 0.6 s.
- Cold cost split: risk-history `_position_pnl` 1.5 s (of which `_root_prices` 1.0 s = `read_sql_query` +
  `pivot_table` per root, ~19 roots; SQLite itself is indexed and fast, 40 ms for CL); spreads-engine
  `book_spreads` 0.8 s (templates YAML with the pure-Python SafeLoader 0.40 s vs CSafeLoader 0.06 s,
  identical output; then `value_book` x7 at the reference closes); my own `series_metrics` 0.10 s for 30 rows,
  `commodity_underlyers` glue ~0.05 s, `commodity_stress` 0.07 s. cProfile inflates the pandas/yaml parts
  ~2.5x (7.7 s under the profiler), so always quote unprofiled timings.
- Cold vs warm results compared key by key: IDENTICAL (the warm memo path is exact).
- A "Header risk chip took 16.3s" in the app could not be reproduced: the server is werkzeug `threaded=True`,
  so after a sample switch every tab's render competes for the GIL with the chip's `book_risk`; a cold OS
  file cache on the 588 MB research DB adds to a first call. Not a cache keyed on the sample file.

2026-09-29 speed pass on trade_risk (Jason's book; this PC is loaded by other lanes, so time CPU as
well as wall, interleave before/after, and take several runs; rebuild the "before" module in scratch
by reversing the edit scripts): cold 1.9-3.1 s -> 1.0-1.2 s (0.75-0.9 s with spreads/curve passed);
sample 6-8 s -> 1.65 s. Causes found: a row-wise `DataFrame.agg("|".join)` (3 s profiled),
pandas deep-copying a Series' attrs dict (the constant-maturity date->contract map) on EVERY
arithmetic step or copy (build a plain Series from `.to_numpy()` instead), one research query per
root (`history.prefetch_roots`), and book_spreads built twice (curve_positions(spreads=) now gets the
one `_commodity_positions` builds). Outputs compared dict-for-dict: 0 differences.

**Why:** the user asked (2026-09-28) why the chip took 16 s on the sample book.
**How to apply:** do not vectorise `series_metrics` or touch `_sum_series` for speed (0.1 s total, equality
risk); the real wins are risk-history's `_root_prices` (`pivot` instead of `pivot_table`, or one query for
the book's roots) and spreads-engine's `yaml.CSafeLoader`, both Requests. Tests: the pickle-parquet plugin
lives in the session scratchpad and must be recreated each session (see [[commodity-phase4-2026-09-24]]).
