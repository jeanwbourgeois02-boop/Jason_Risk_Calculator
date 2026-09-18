---
name: vol-ticker-checks-assumption-verification
description: 2026-09-18 -- live vol pulls now self-verify vol_marketdata.py's 9 UNVERIFIED ticker/field guesses into a vol_ticker_checks table; tools/bbg_diagnostics.py reads it instead of telling the user to run --probe by hand.
metadata:
  type: project
---

Built 2026-09-18 on top of [[vol_marketdata_fx_vol_feed]] and
[[live_pull_boundary_cases_2026_09_17]]'s `_vol_step`. User's framing: "the live pull
itself is the probe" -- do not add new Bloomberg requests, only read more of the
response `_vol_step` already gets.

**Task granted edit access outside the normal directory split**: this session was
explicitly told it owns `data/bloomberg/**`, `tools/bbg_diagnostics.py`, and
`ui/tabs/market_data.py` (diagnostics section) plus their tests, for this one task --
wider than the usual `data/bloomberg/** + tests/test_bloomberg.py` split recorded in
[[diagnostics_module_naming]]. Confirm a session actually has this before editing
`tools/` again; it is not bbg-data's directory by default.

**What changed in `data/bloomberg/vol_marketdata.py`**:
- `VolBloombergSource._fetch` now classifies each ticker's response as OK /
  SECURITY_ERROR / FIELD_EXCEPTION / NO_VALUE (new `_classify_live_ticker` /
  `_classify_hist_ticker` / `_bbg_error_message` / `_bbg_field_exception_message`
  helpers, mirroring `pull_marks.py`'s `_parse_security_error` /
  `_parse_field_exceptions` but kept local -- same "no shared class with
  rates_marketdata/pull_marks" discipline as the rest of this module). This is reading
  MORE of the SAME response, not an extra request. `VolFetchResult.diagnostics` entries
  gained a `bbg_status` key carrying this; the existing `status: "MISSING"` key is
  unchanged (existing tests/consumers keep working).
- `assess_ticker_assumptions(result) -> List[dict]` turns one `VolFetchResult` into a
  verdict per assumption id (`ASSUMPTION_DESCRIPTIONS`, `ALL_ASSUMPTION_IDS` -- 9 ids
  matching the docstring's numbered list). Rule: a confirmed value for an assumption
  beats a failure elsewhere in the same cycle (e.g. one bad tenor doesn't fail the whole
  ticker-shape assumption); among failures, SECURITY_ERROR > FIELD_EXCEPTION > NO_VALUE
  by specificity. An assumption with NO evidence this cycle (nothing requested that
  bears on it) is simply absent from the returned list -- `record_vol_ticker_checks`
  then leaves that assumption's previously recorded row untouched rather than
  overwriting a real confirmation with a false "not checked". `vol_scale` (probe item 9)
  is judged ONLY from ATM values (RR/BF can legitimately be small either way) via
  `_scale_outcome`: magnitude in [0.5, 200] -> OK, else OUT_OF_RANGE (catches both "looks
  like a decimal fraction" and genuinely implausible values).
- `vol_ticker_checks` table (created defensively, PK on `assumption_id` alone -- one row
  per assumption globally, not per as_of/pair, since it's a running verification ledger
  not a daily snapshot): `description, last_checked, tickers, outcome, value, detail`.
  `record_vol_ticker_checks(conn, result)` writes it; `read_vol_ticker_checks(conn)`
  reads it (never raises -- catches `sqlite3.Error`, returns `{}` when the table doesn't
  exist, which is also what happens for free on a READ-ONLY connection when the table
  has never been created: `CREATE TABLE IF NOT EXISTS` on a read-only sqlite3 connection
  is a genuine no-op and succeeds when the table already exists, but raises
  "attempt to write a readonly database" when it doesn't -- verified empirically, this is
  the exact mechanism `tools/bbg_diagnostics.py::check_unverified_assumptions` relies on
  to tell "no record yet" apart from "record exists" using only a read-only connection).
- `data/bloomberg/live.py::_vol_step` calls `record_vol_ticker_checks` right after
  `write_vol_quotes`, in its own nested try/except so a bookkeeping failure there can
  never mask a vol_quotes write that otherwise succeeded (same discipline as the
  fixings-vs-curve-quotes gotcha already recorded in [[live_pull_boundary_cases_2026_09_17]]).

**`tools/bbg_diagnostics.py::check_unverified_assumptions`** now takes `db_path` (was
zero-arg), opens its own read-only connection (same pattern as `check_last_pull`), and
reports: PASS when every one of the 9 assumptions has outcome OK; FAIL naming the
rejected assumption id + exact ticker + Bloomberg's own error message when any assumption
was actually rejected (SECURITY_ERROR/FIELD_EXCEPTION/OUT_OF_RANGE beats a bare "not
checked" even if other assumptions are still pending); WARNING "not yet exercised: no
live pull with options in the book has run" when `vol_ticker_checks` has no rows at all
(covers both `db_path=None` and a real db with no table yet); a distinct WARNING when
some but not all assumptions have evidence so far.

**Testing note**: `tests.test_bloomberg` is importable from `tests.test_bbg_diagnostics`
as `from tests.test_bloomberg import _install_fake_blpapi` (repo has `tests/__init__.py`,
root is on `sys.path`) -- reuse that fake-blpapi harness rather than duplicating it when
a test in a different test file needs a live Bloomberg-shaped session.

**Pre-existing, unrelated failures observed while running the full suite this session
(not caused by this task, confirmed present on `main` before any edit here via
`git stash`)**: `tests/test_auto_backfill.py::test_auto_backfill_fills_from_earliest_trade_to_yesterday`
and `::test_auto_backfill_already_complete_reports_zero_remaining`, plus
`tests/test_bloomberg.py::test_rates_bloomberg_source_get_curve_quotes_with_fake_blpapi`
and `::test_rates_bloomberg_source_get_curve_quotes_too_few_raises` (the latter two: the
fake session's `_fetch_historical_single` path breaks on `date.today()` -- a batched
historical request with >1 security hits `getElementAsString` on a list, not a dict;
looks like a real bug in `rates_marketdata.py`'s historical multi-security parsing, not a
test-harness issue, but out of scope for this task -- report to whoever owns that test
next if asked to fix it). Also saw a ONE-TIME transient `tests/test_ui_blotter.py`
failure ("table trades has 14 columns but 13 values were supplied") immediately after a
concurrent session's commit landed on `main` mid-session (`git stash`/`pop` straddling
commit `7650880`); it did not reproduce on a clean re-run -- confirms
[[multi-session-lanes]]-style concurrent-commit noise is real and worth a re-run before
concluding a failure is caused by your own change.
