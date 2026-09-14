---
name: review-findings-bloomberg
description: Status of findings from the 2026-09-13 reviews (five passes, rounds 1-3 of the diagnostics/--probe drop) of data/bloomberg (marks_csv.py, bnp_marks.py, pull_marks.py, diagnose.py, tests/test_bloomberg.py); what is closed, what is still open, what to re-check on the next bbg-data drop
metadata:
  type: project
---

Five review passes on 2026-09-13, plus a 2026-09-14 one-line diff check (NY -> _ny()). Fifth pass = round 3 of the diagnostics/--probe drop
(still uncommitted vs 1ddaf6a): 97/97 tests pass; imports stdlib+blpapi only (socket/blpapi
lazy); ownership clean; schema.py/marks_csv.py untouched so BBG_INTERP still never official;
snapped_at still 15:00 America/New_York. No P&L code in this drop.

**CLOSED (verified by executing fake-blpapi scenarios, not by reading claims):**
1. C1 XXXUSD SPOT = 1.0 -- fixed (second pass).
2. W1 nan/inf/compact date/naive snapped_at rejected by `_validate_mark_row`.
3. W2 load_bnp_marks routes through `load_mark_rows`.
4. W3 live SPOT mislabeled -- SPOT now HistoricalDataRequest PX_LAST for as_of.
5. W4 ON/TN removed from STANDARD_TENORS.
6. W5 FWD_CURVE bulk -> tenor fallback with warning.
7. W6 expired futures filtered.
8. TIMEOUT -> exit 5 + marks_csv_partial.
9. C-A (round 2): partial = requested keys != written keys; exit 0 only when every key written.
10. C-B (round 2): probe outcome PROBE_COMPLETE_WITH_FAILURES / PROBE_SESSION_FAILED.
11. W-1 correlation ids: late response after TIMEOUT discarded + recorded.
12. W-2 environment populated on stale refusal / bad --as-of / missing request CSV.
13. W-3/W-7 write_diagnostics mkdirs + try/except; NaN/inf sanitised.
14. W-4 probe records repr and a `scalar` flag; non-scalar FWD_CURVE flagged.
15. Round 3 W-1 (pull side): batched tenor fieldException with all keys written -> pull exit 0,
    diagnose exit 0, BAD_FLD still in report. Verified.
16. Round 3 W-2: SPOT SECURITY_ERROR -> FWD_OUTRIGHT "no SPOT" failure classified SECURITY_ERROR;
    FWD_POINTS_SCALE fieldException -> FWD row classified FIELD_EXCEPTION. Verified by execution,
    but NO test covers either path (grep spot_batch_classification in tests = 0 hits).
17. Round 3 S-1 write_marks_csv mkdirs (verified); S-2 bad --as-of -> exit 2 + diag with environment,
    pull and probe modes (verified); S-3 scalar check on FWD_OUTRIGHT_PRICE candidate (test);
    S-4 fake historical responders answer all securities.

**Open after fifth pass (reported to parent 2026-09-13):**
- W (regression of closed item 10): probe with candidate-only failures -> outcome OK but
  `diagnose.main` exits 1. Cause: run_probe passes ALL failures (candidates included) to
  finalize_summary as summary.failures; diagnose.has_failure checks `summary.failures` (L226)
  before the probe-mode `_hard_failures` filter (L228). The only test for this
  (test_diagnose_probe_mode_still_flags_non_candidate_hard_failure) hand-builds a diag with
  failures=[] so it never sees the real run_probe output. Fix: in has_failure, for mode==probe
  ignore failures with candidate=True (or have run_probe put only non-candidate failures in
  summary.failures). Add a test that runs pull_marks.main(--probe) with candidate-only
  fieldExceptions and asserts diagnose.main == 0.
- W (2026-09-14 re-check, uncommitted diff): tzdata prerequisite is HALF done. `_ny()` lazy
  loader exists (L135) and all 4 call sites use it (L185/222/237/277; L277 was an undefined `NY`
  NameError in HEAD 70a1a25, fixed in the diff -> 115 passed). BUT `_ny()`'s docstring claims
  main() catches ZoneInfoNotFoundError -> exit 6; grep of main() shows NO such handler, no
  "6" in the exit-code table, and the very first `diag.record_environment()` (L1324) is
  before the try/finally that writes the diag. Reproduced with tzdata hidden: probe run raises
  ZoneInfoNotFoundError out of main(), no diag JSON. Net effect: crash moved from import time
  to main() time -- still loud, still no diag. Fix: wrap L1324 (or resolve `_ny()` right after
  Diagnostics()) in try/except ZoneInfoNotFoundError -> failure stage "tz_prerequisite",
  "pip install tzdata" hint, exit 6, write diag; add exit 6 to the docstring table; add a
  test that hides tzdata (meta_path finder blocking `tzdata` + `zoneinfo.reset_tzpath(to=[])`)
  and asserts exit 6 + diag written. Never fall back to fixed -04:00.
- W: no tests for W-2 propagation paths (SPOT SECURITY_ERROR -> FWD SECURITY_ERROR; scale
  FIELD_EXCEPTION -> FWD FIELD_EXCEPTION).
- Still open from second pass: bnp_marks NaN Price/Fx; 15:00 snapped_at label vs 17:00 close.

**Why:** the parent agent wants explicit closed/open status per finding on re-review; do not re-derive.
**How to apply:** on the next data/bloomberg review, re-run the scratchpad driver pattern
(import tests.test_bloomberg, call `_install_fake_blpapi(MP(), responder)` with a tiny object
that has `setitem`, then pull_marks.main / diagnose.main on the diag JSON). Do not trust
bbg-data's memory (`.claude/agent-memory/bbg-data/pull_marks_diagnostics.md`) or hand-built diag
dicts in tests -- the candidate-only regression was invisible to both. Related:
[[bnp-file-facts]], [[review-findings-ladder]], [[review-findings-ingest]], [[windows-bash-paths]].
