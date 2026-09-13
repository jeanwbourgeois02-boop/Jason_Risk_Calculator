---
name: review-findings-ingest
description: Status of findings from the 2026-09-13 reviews of data/ingest (bnp.py, schema.py); what is closed, what is still open, and residual risks to re-check
metadata:
  type: project
---

Two review passes on 2026-09-13 against data-ingest's drop (bnp.py / schema.py / tests/test_ingest.py). Second pass: 31/31 tests pass.

**Closed on the second pass (verified in code, not just claimed):**
- C1 synthetic FUTURE trade: `_parse_future` now writes instrument + positions only; real-file test asserts no FUTURE trades / NOTIONAL legs. 229 trades all FX_FWD, 458 legs, 31 positions.
- W1 NOTIONAL rate: moot (no futures legs written).
- W2: `previous_business_day` renamed `_previous_weekday`, private, docstring says it is not a trading calendar; test asserts the public name is gone.
- W3: `load(strict=True)` default raises ValueError before the `with conn:` block; rejects and recon failures logged as warnings; `strict=False` still never inserts rejected rows.
- W4: `CheckResult.passed` returns False on NaN; `max_deviation` propagates NaN.
- W6: netting failures cite the first row of the group and list all rows in `detail`.
- W7: failing-path tests exist for local_cost, netting mismatch, blank Trade Factor, unknown root, blank Price, blank Fx, strict/non-strict load, duplicate load.
- W8 PNL_TOL = 0.01 and the fx_to_usd = 0.0 cash sentinel are logged in docs/open-questions.md (items on lines ~10 and 18); positions netting is open question 17.
- Suggestions (unknown futures roots rejected, _MIN_HEADER always used, tmp_path, NDF_CCYS provisional comment, CONTRACT_CHECKS/EXTRA_CHECKS split) all done.

**Still open / residual (re-check next time):**
1. `__pycache__/` still not in `.gitignore` (root file only has `data/raw/`). Not data-ingest's directory; housekeeper.
2. Default `as_of_date` is still silently wrong on the day after a US holiday; `load` does not log which as_of it chose. Documented limitation, not a bug in scope.
3. `_f()` (blank -> 0.0) is used for forward P&L columns (DTD/MTD/Start Dirty/Prev ME) while price/Fx/MV use `float()` (blank -> NaN -> failure). A blank `DTD Total` + blank `DTD Trading` passes `dtd_total_eq_trading` silently. Reference file has no such blanks.
4. Non-strict mode with a NaN Fx/Price on a forward: `_position` uses `_f` for mark (0.0 written) but `fx` is raw NaN -> sqlite binds NaN as NULL -> IntegrityError inside the transaction (rolled back). Surprising but safe.

**Why:** the parent agent asks for explicit closed/open status per finding; do not re-derive these.
**How to apply:** on a further data-ingest review, only items 1-4 above need attention; grep `_f(row[` to see if item 3 changed. Related: [[bnp-file-facts]], [[windows-bash-paths]].
