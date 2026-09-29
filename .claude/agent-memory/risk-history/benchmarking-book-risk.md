---
name: benchmarking-book-risk
description: How to time book_risk before/after a change to my files without fooling myself (history path trap, noisy PC)
metadata:
  type: project
---

Timing `engine/risk/metrics.py::book_risk` on the sample book (`tests/golden_book.py::build_book` into an in-memory db after `schema.create_schema`, as of 2026-09-18), learned 2026-09-25:

- **The path trap.** Loading the HEAD copy of `commodity_history.py` from the scratchpad (to compare before / after in one run) makes `DEFAULT_PATH` resolve from the scratchpad, so the history is silently unavailable and "before" looks fast (0.9 s). Set `COMMODITY_HISTORY_DB` to `../Commodity Dashboard/var/rv.sqlite` and print `load_commodity_history().available` in the bench.
- **Noise.** Other lanes' agents run on the PC at the same time; single timings swing 2x. Alternate before / after runs in fresh processes, 4 calls each, and report ranges.
- **Identity.** Pickle `book_risk`'s output before and after and compare recursively (NaN-equal floats, `assert_series_equal(check_exact=True)`, attrs).
- Result that day: before 5.9-10 s per call, after 2-4 s cold and 1.1-1.8 s warm; what is left is spreads-engine's `book_spreads` (~0.5 s) and `series_metrics` (~0.4 s).

- 2026-09-28: the SQL of `_root_prices` is cheap (57k rows in 60-80 ms raw); the per-root cost is pandas (`read_sql_query` frame build ~35 ms a root, `pivot_table` > `pivot`). Reading the whole `price_daily` (1.5M rows) takes 18 s, so any batch must be the book's roots only (`prefetch_roots`). One batched read of the sample's 19 roots saves ~0.25 s of a ~2.3 s cold `book_risk`: the rest is not in my files.

**Why:** a wrong baseline would have reported a slowdown or hidden the win.
**How to apply:** any future speed claim about my files. See [[research-db-quirks]].

- 2026-09-29: cProfile inflates pure-Python work. A perf run blamed `_position_pnl` for 3.1 s and `deepcopy` for 1 s. Timed without the profiler, the whole position-P&L share of `book_risk` was about 1.2 s on the sample and 0.8 s on risk.db. The deepcopy was pandas 3 copying `attrs` on every operation: the `contracts` date→contract dict that `constant_maturity_changes` puts in attrs. `_position_pnl` now reads `_cm_frame` directly. Result: sample 1.2-1.3 → 1.0-1.1 s, risk.db 0.72-0.84 → 0.47-0.56 s. What is left is the first read of each root's `price_daily`: the SQL is already a clustered-PK search, and the cost is disk. Keep big dicts out of a Series' attrs on any hot path. Time with a wrapper around the function; use the profiler only to find where the time goes.

- 2026-09-29 (cold-start pass): `SELECT MIN(date), MAX(date)` in one query scanned the whole `ix_price_daily_date` index (0.29 s); split into two it is two seeks. `load_commodity_history` cold 0.40-0.46 → 0.14-0.22 s, frames identical. The batched root read is SQLite-bound (0.16 s of SQL for 91k rows over Jason's 10 roots; dropping the join column or pandas tweaks gained nothing), so it moved to `warm()` in a background thread instead. The contract lookup cold is 0.04-0.06 s here, not the 0.47 s another lane reported. `latest_settles` reads a few levels in ~0.02 s with no history load.
- 2026-09-29, later: `attrs['contracts']` is now a `ContractMap` (immutable, `__deepcopy__` returns itself, dict built lazily): 300 x 3 pandas steps on a 1557-date series 12.1 s → 0.4 s. Proof harness that worked: load the pre-change file as `engine.risk.commodity_history` into `sys.modules` BEFORE importing `engine.risk`, with COMMODITY_HISTORY_DB set; pickle book_risk + trade_risk on the golden sample (as of 2026-09-18) and Jason's scratch db; compare recursively (27k values, 0 diffs).
