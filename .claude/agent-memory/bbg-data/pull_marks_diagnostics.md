---
name: pull_marks_diagnostics
description: diag JSON structure, --probe mode, exit codes, "partial" = requested-vs-written key sets (not just TIMEOUT), correlation ids, probe outcome, tz-prerequisite exit 6
metadata:
  type: project
---

pull_marks.py diagnostics layer (data/bloomberg/pull_marks.py), current as of 2026-09-14 (item 39 resolved):

- Exit codes: 0 OK, 2 bad/stale `--as-of`, 3 unhandled exception, 4 session/service open
  failure, 5 marks CSV partial, 6 tz prerequisite missing (America/New_York zoneinfo not
  resolvable -- e.g. no `tzdata` pip package on stock Windows Python).
- `_ny()` lazily resolves and caches `ZoneInfo("America/New_York")` in module global
  `_NY_ZONE` -- deliberately NOT a module-level constant, since constructing it eagerly
  would raise `ZoneInfoNotFoundError` before argparse even runs, with no diag JSON ever
  written. `main()` resolves `_ny()` in a `try/except ZoneInfoNotFoundError` immediately
  after `diag = Diagnostics()` is created and *before* the first
  `diag.record_environment(...)` call (which itself calls `_ny()` internally for
  `machine_time_america_new_york` -- that was the original bug: record_environment ran
  first, outside any try/except/finally, so a missing-tzdata machine crashed with zero
  diagnostics). On failure: writes a diag with `summary.failures` containing
  `{"stage": "tz_prerequisite", "error": ..., "hint": "py -3 -m pip install tzdata"}`,
  prints the same hint to stderr, writes the `.diag.json` via the normal
  `write_diagnostics()` path, returns 6.
- "Partial" marks CSV (exit 5) is defined purely by comparing the requested
  `(instrument_id, settle_date, mark_type)` key set against what was actually written --
  not by whether `failures` happens to be non-empty. Any silent per-row skip still trips
  this reconciliation in `run_pull()`.
- `--probe` mode always exits 0 once the session/service open (individual probe step
  failures are exploratory data); `outcome` is `"PROBE_COMPLETE_WITH_FAILURES"` if any
  *non-candidate* step failed, else `"OK"`. Candidate steps are the three
  `fwd_outright_direct_*` alternatives tagged `candidate=True` -- expected to have some
  failures since they're guesses at unverified field/override names (item 27).
- Every `ReferenceDataRequest`/`HistoricalDataRequest` is sent with its own
  `blpapi.CorrelationId`; a response whose `correlationIds()` doesn't match is a stale
  reply from a previous (e.g. timed-out) request and is discarded from `out` but still
  recorded under that request's `late_responses` diag entry.
- Batched requests (e.g. 8 standard-tenor tickers in one `fetch_tenor_points()` call) are
  classified as a whole (priority TIMEOUT > SECURITY_ERROR > FIELD_EXCEPTION > NO_VALUE >
  OK) -- one bad ticker marks the whole request record failed even if other tickers in
  the batch resolved fine. Per-row `summary.failures` is still accurate because it's
  derived from the requested-vs-written key reconciliation, not from this classification.
- When testing exit-6 behaviour: `_ny()`'s cache (`pull_marks._NY_ZONE`) must be cleared
  via monkeypatch before monkeypatching `pull_marks.ZoneInfo` to raise
  `ZoneInfoNotFoundError`, otherwise a previously-cached zone silently makes the test
  useless.
