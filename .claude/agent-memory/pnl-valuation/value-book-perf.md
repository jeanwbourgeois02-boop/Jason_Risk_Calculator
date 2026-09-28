---
name: value-book-perf
description: 2026-09-28 profiling: value_book is 30-90 ms per call on the sample; the "slow render: Book tab" label wraps book_spreads only; the real cliff is marks with no index led by instrument_id (9.9 s per call at 300k rows on an unmarked day, 30 ms with one)
metadata:
  type: project
---

- 2026-09-28 brief "Book tab took 14.1s (4 full re-pricings)" assumed 3.5 s per `value_book`. Measured on
  the app's own sample (`tests.golden_book.build_book(through=today)` + `realise_settled`, 50 trades,
  1489 marks): 27-90 ms per call, marked or unmarked day; the fill 6 ms. Nothing in valuation.py or
  calendar.py was worth touching (the 2026-09-17 `_BookConn` preload already did the job).
- `pricing_snapshot(conn, "Book tab")` in `ui/tabs/book.py::_spreads` wraps `book_spreads` ALONE, so that
  log line's seconds are spreads-engine's grouping (22 `_period_pnl` x 4 periods = 88 `resolve_reference`
  calls with pandas filtering) plus a one-off 1.4 s pure-Python YAML parse of `config/spreads/` (cached per
  process); `book_risk` (4 s here) is in the separate alerts callback.
- The genuine cliff is the real book: `marks` has only its PK index (as_of_date first). Every near-marks /
  last-on-or-before query (`_neighbour`, `_last_official_query`, `_nearest_curve_day`, `engine.lme.
  settlement_price`) filters on instrument_id and scans the whole table through the `marks_official`
  view. At 300k rows: 0.8 s on a marked day, 9.9 s on a day with no marks of its own (today before the
  pull, the default as-of); `CREATE INDEX ... ON marks (instrument_id, mark_type, settle_date, as_of_date)`
  gives 20-30 ms for both. Requested from ingest-schema (schema DDL, not my lane).
- **How to apply:** before optimising valuation.py, reproduce on the scratch DB and check the slow-render
  label's scope; the fallback if the index is refused is a one-pass preload of the book's instruments'
  official rows in `_BookConn` answering the neighbour lookups in memory (tie-break: nearest date, then
  max snapped_at), a P&L-module change that needs the reviewer.
- Scratch pattern that worked: build into a file DB in the scratchpad, pickle the "before" frames per date
  (incl. a Sunday as-of and `trade_ids=` subset), `assert_frame_equal(check_exact=True)` after; the golden
  `--diff` writes `reports/golden_diff_*.txt` (git-ignored).
