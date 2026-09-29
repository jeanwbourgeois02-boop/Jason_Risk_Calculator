---
name: phase-g-round3a-2026-09-29
description: Phase G round 3a - engine additions wired (closed fold, leg mark/value hovers, price-mode levels, red flags for UNRECOGNISED, move in sigma, by_type track record, FX forwards + Greeks folds, usd_per_pct, vol target, legs_risk, stress by_position, Data/Blotter need-fix); traps and proof recipe
metadata:
  type: project
---

2026-09-29, round 3a (the look pass is 3b). What is true after it:

- Every row loads: UNRECOGNISED trades are shown, never dropped. Book legs `unrecognised` -> red name +
  `tk-leg--unrec`; flags carry `severity` ('red' only for unrecognised): `book.is_red` / `flag_sentence`,
  `.tk-flags--red`, `.tk-flag-line--red`. `tf.trade_type_label(t)` says "Not typed" for a trade of only
  unrecognised legs (engine type '' otherwise reads "Hedges only"); use it, not `type_label(type_code(t))`.
- Book `display_level(data, t)`: closed trade -> closed block entry/exit (Next column "closed 16 Sep");
  `mode == 'price'` -> the leg's quoted unit so price_text ticks right. Leg Mark / Value hovers read the
  engine's mark_source/snapped_at/as_of/prev_mark* and value_local; the panel no longer re-prices the prev day.
- `_trade_risk_compute` passes `trade_book=shared_trade_book(...)` -> move_sigma / level_sd (Today hover).
- Risk: folds read curve rows' `usd_per_pct` (PCT gone); Currency fold ends with `fx_positions_block`
  (book_positions, whole book; engine's fx.net_usd is ALREADY + = long USD: never negate); Option Greeks fold
  (`rf.FOLD_GREEKS`) only when options are held; stress reads `by_position` / `by_position_detail`;
  headline "Of vol target" = subset_var vol_vs_target_pct (VaR share on hover); panel legs from `legs_risk`.
- Data: `data_checks.problem_rows(..., unrecognised)` from `inventory.unrecognised` (red "Not recognised").
- Blotter: `need_fix_count(report, issues)` -> "N rows need a fix" (red link to the fold, which lists need-fix,
  not-loaded and row warnings); None (old upload) keeps "not loaded" wording. `ui/app.py::_reresolve_unrecognised`
  runs in ensure_schema; `uploads.is_sticky` counts `need_fix`.

**Traps:** a bash heredoc turns "\\n" into a real newline inside Python source (broke data_checks once): use Write
for snippets + `ui-g3a/splice.py`. Working-tree `data/ingest` may be mid-edit by another lane: build scratch DBs
from `git archive HEAD data engine config` put first on sys.path. `tr.cells` misses cells wrapped in the
static-block (display: contents) - read row innerText / `[title]` instead in CDP drives.

**Proof recipe:** scratch `ui-g3a/`: `mk.py` (real.db + sample.db via HEAD ingest, hand-inserted UNRECOGNISED
trades), `unrec_real.py`, `verify.py <db> <as_of>` (direct renders), `serve.py <db> <port> [as_of]` (patches
`today_ny` in every ui module to pin the as-of), `drive_3a.py <url> <cdp> 1680 <tag> sample|real`.
