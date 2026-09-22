---
name: review-findings-load-cli-ui
description: 2026-09-14 review of data/load.py CLI + bnp.load(on_duplicate='skip') and the ui transposed ladder / P&L tab; C-1 conflict fix pass 2 left two gaps (headline count, tolerance); open items to re-check
metadata:
  type: project
---

Reviewed 2026-09-14 (suite 155 passed). Verified by running `py -3 -m data.load data/raw/HA_PNL_20260818.csv --db <scratch>/t.db` twice: run 2 leaves 229/458/31/33 (trades/legs/positions/marks) unchanged and prints `skipped=262` (229 trades + 33 BNP_BVAL marks: 19 FWD_OUTRIGHT + 14 SPOT). No criticals in the P&L display; UI never re-implements engine maths.

**Open items to re-check:**
1. C-1 key-only skip -- fix pass 2 (2026-09-14, suite 158) added value diffing + `conflicts=` + `--allow-conflicts`, but two gaps remained and were reported as CRITICAL: (i) `data/load.py` headline `n_conflicts = trades + marks` only, so legs-only / positions-only conflicts (amended Local Cost, amended CASH balance -- CURRENCY and FUTURES rows have no trades row at all) print `conflicts=0`, rc 0; (ii) `_values_match` uses rel tol 1e-6, so a 1-unit Local Cost change on a 568m JPY leg is within tolerance. Verified: exact equality on an identical re-load of the real file still gives conflicts=0 (CSV float -> SQLite REAL round-trips exactly), so the tolerance can be exact or abs-only ~1e-9. Probe recipe: copy the real CSV under a `HA_PNL_YYYYMMDD.csv` name (file_date_from_name requires it); a +1 Local Cost tweak on row 196243174 trips the recon (|Q*rate-LC|=1.06>1) -- use -1.
2. CLI commits partial loads and still exits 1 on rejects (strict=False); a rerun then reports them as skipped, hiding the earlier failure.
3. `tests/test_ingest.py` idempotency test has a vacuous `... or "skipped=" in out2` assertion.
4. `data/load.py` sits in unowned `data/` root (ownership table only lists data/ingest and data/bloomberg).
5. `--marks` path (`_load_marks_csv_idempotent`) has no test; writes a temp file beside the input; reject row numbers refer to the filtered file.
6. `transpose_ladder` back-derives spot as usd/total; a currency whose ladder total nets to 0 (swap, offsetting forwards) blanks usd_equivalent on every date it appears even though the engine knows the spot.
7. No pnl-tab callback test with a fake engine (cash ladder has one).
8. Real-file note: under the official source every non-USD `usd` is NaN because only BNP_BVAL marks are loaded, so the transposed ladder is nearly all blank until BBG marks land.

**Why:** the parent agent asks for explicit closed/open status per finding on re-review.
**How to apply:** on the next load-CLI or UI review start from items 1-7. Related: [[review-findings-ingest]], [[review-findings-pnl]], [[review-findings-ladder]].
