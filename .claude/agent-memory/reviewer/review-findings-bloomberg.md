---
name: review-findings-bloomberg
description: Status of findings from the 2026-09-13 reviews (first pass + second pass) of data/bloomberg (marks_csv.py, bnp_marks.py, pull_marks.py, tests/test_bloomberg.py); what is closed, what is still open, what to re-check on the next bbg-data drop
metadata:
  type: project
---

Two review passes on 2026-09-13. Second pass: 79/79 tests pass; pull_marks imports standalone with no blpapi in sys.modules and no repo imports; ownership clean (only data/bloomberg, tests/test_bloomberg.py, CLAUDE.md, .claude/agent-memory touched); marks_official view unchanged (BBG_INTERP and BNP_BVAL never official).

**CLOSED on second pass (verified by running code, not by reading claims):**
1. C1 XXXUSD SPOT = 1.0 -- fixed. Real-file dump: 14 SPOT rows, all USDXXX, none equal 1.0; AUD/EUR/GBP/XAU produce a warning each and no SPOT. Test `test_bnp_marks_no_spot_is_1_0_and_xxxusd_pairs_have_no_spot` covers it.
2. W1 nan/inf/compact date/naive snapped_at -- all rejected by `_validate_mark_row` (regex + isfinite + tzinfo check); tests exist for each.
3. W2 load_bnp_marks bypassing validation -- now routes through `load_mark_rows`; re-run gives duplicate rejects, unknown instrument is a reject; both strict/non-strict tests exist. Object path and CSV path give identical outcomes on identical inputs (only reject wording differs for pre-parse cases like str/bool values).
4. W3 live SPOT mislabeled -- SPOT now HistoricalDataRequest PX_LAST for as_of; `check_not_stale` only guards FWD_OUTRIGHT and is only called from `main()`, so `run()`/fake-blpapi tests are unaffected. Verified SPOT/FUTURE_PX-only request with stale as_of passes without the flag.
5. W4 ON/TN -- removed from STANDARD_TENORS; pre-spot targets produce a "before spot" warning, never interpolated.
6. W5 FWD_CURVE bulk -- non-scalar falls back to tenor interpolation with warning; test exists.
7. W6 expired futures -- `_FUTURE_INSTRUMENTS_SQL` filters `expiry_date > as_of` and requires an open leg or positions row; test exists.
8. Notes (nextEvent timeout, dead loop, fieldnames assert, docstring) -- all addressed.

**Still open after second pass (all minor):**
- bnp_marks.py `float(row["Price"]) / float(row["Fx"])` accepts pandas NaN: a blank Price/Fx on a FORWARD row yields NaN FWD_OUTRIGHT/SPOT rows from `extract_bnp_marks` and, if two rows share the key, a spurious "conflicting ... nan vs nan" reject (NaN != NaN). `load_mark_rows` catches the NaN as non-finite so nothing bad reaches the DB, but strict mode aborts with a misleading reason. Real file has no blank forward numerics so it is latent. Fix: `math.isfinite` check at extract time -> reject.
- pull_marks SPOT/FUTURE_PX historical PX_LAST/PX_SETTLE are Bloomberg daily closes (Curncy ~17:00 NY), but snapped_at is labelled 15:00 NY per CLAUDE.md. Convention drift that cannot be verified without a terminal; needs an explicit decision (either label snapped_at with the true close time or accept the 15:00 label as nominal).
- pull_marks: on nextEvent TIMEOUT the fetchers return partial results and the script still exits 0 with a partial CSV.
- Direct `FROM marks` reads exist only in marks_csv.py loaders (duplicate-key check) -- acceptable, not a P&L/delta read.

**Why:** the parent agent wants explicit closed/open status per finding on re-review; do not re-derive.
**How to apply:** on the next data/bloomberg review, check the "still open" list first, then re-run the real-file SPOT dump (`extract_bnp_marks(...).rows` filtered to SPOT) and confirm no 1.0 values and no XXXUSD spots. Related: [[bnp-file-facts]], [[review-findings-ladder]], [[review-findings-ingest]].
