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

**Why:** a wrong baseline would have reported a slowdown or hidden the win.
**How to apply:** any future speed claim about my files. See [[research-db-quirks]].
