---
name: bbg-fixes-in-app-2026-09-30
description: Data tab Bloomberg card applies the check's suggested fixes (tick, pick, dry run, confirm, reresolve_roots, git commit+push in a thread) and downloads the report; ids, runner API, verification recipe
metadata:
  type: project
---

User 2026-09-30: "i should be able to do this in the app" (bbg-check worksheet applied from the Data tab, not the terminal).

- Runner (`ui/diagnostics_runner.py`): check_book gets include_pull/include_search via inspect.signature (only what it takes). Fixes state in `_FIXES` per db: `prepare_fixes(db, fixes)` (temp worksheet, apply_fixes dry run, trade counts by instruments.base_ccy), `confirm_fixes(db)` (load_roots copy as `before`, apply_fixes real, `reresolve_roots(conn, roots, before=)` only for non-bbg_verified fields, then `_publish` thread: git add + `commit --only -- config/contracts.csv` + push, GIT_TERMINAL_PROMPT=0), `cancel_fixes`, `fix_state`, `busy_reason` (pull or check running). A new check start clears the fixes unless git runs. `REPO_ROOT` is patchable for proofs.
- Card (`market_data.bloomberg_card`): Download report (disabled until a check; hover "Send this file to Claude"; filename bbg_report_YYYYMMDD_HHMM.txt from finished_at local), BBG_SUMMARY_ID above BBG_FIXES_ID, static Apply / Confirm / Cancel hidden by style, BBG_FIX_RESULT_ID, BBG_DONE_ID store (set only on change: "running" / finished_at) redraws the fixes once so ticks are not reset by the poll; ticks re-render from the dry run's rows. Pattern ids `md-fix-tick`, `md-fix-pick` are State only.
- Verified-only apply says "Marked as confirmed by Bloomberg: X." with no rebuild sentence; any other applied fix says amber "Press Pull Bloomberg now to price them" (a new root = a new contract id, priced at the next pull).
- Proof recipe: fake state into `dr._RUNS[dr._key(db)]`, monkeypatch `data.contracts.apply_fixes` to `contracts_path=<scratch repo>/config/contracts.csv`, set `dr.REPO_ROOT` to a scratch `git init` repo (no remote: push fails with a sentence). Never run confirm on the real config/contracts.csv.
- "Contract roots verified" wording lives in tools/bbg_diagnostics.py (infra), not ui.
