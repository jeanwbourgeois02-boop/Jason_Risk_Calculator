---
name: phase2-macro-removal
description: 2026-09-24 Phase 2 removal in blotter.py / common.py -- how IRS, ES/NQ/RTY/YM, SPX options and NDFs are now handled, which retired names survive and why, test-writing traps met
metadata:
  type: project
---

On 2026-09-24 (commodity conversion Phase 2, user-approved removal) the parser dropped the
macro products. A row of a removed kind is now SKIPPED, never rejected (hard rule 6):
counted in `n_skipped_other` + `n_skipped_retired`, named on `skipped_other_rows` with a plain
reason (`_not_loaded_reason`). `upload.py` already lists `skipped_other_rows` as "NOT LOADED",
which is why the skips go there rather than to a new field.

**Why:** the user approved the removal; tolerant imports mean an old macro row in an export
must still leave a visible trace and must not fail the file.

**How to apply:**
- IRS: `_kind_of` still RECOGNISES rates labels (returns INTEREST_RATE_SWAP) only so they are
  skipped with `RETIRED_REASON_IRS` and never mistaken for an FX swap fill. Keep that.
- ES/NQ/RTY/YM: detected by root on Symbol / Underlying Symbol before the contract master
  (which would otherwise reject 'unknown root'). No contracts.csv root collides (checked).
- Listed options ('ROOT/[EA]yymmdd[CP]strike'): SPX/SPXW/NDX/RUT/SX5E and ES/NQ/RTY/YM are
  retired; since Phase 5 (same day) any other root is an option on a commodity future, see
  [[phase5-options-and-lme]] (the "not loaded yet" skip is gone).
- NDFs: every FX pair is written is_ndf 0, settles_cash 1. `NDF_CCYS` and `NDF_1M_TICKERS` were
  kept retired for a few hours, then deleted the same day once manual.py and
  engine/ladder/ndf.py stopped importing them: common.py carries no NDF name at all now.
- `_future_fill` lost `notional_rebuild` (it was ES-only). `parse` lost `direction_overrides`,
  `load` lost `turn_swap_marks` and all swap_review / FX_SWAP SQL (schema no longer has
  swap_review).
- Test trap: contract-master filled every placeholder Bloomberg root, so the placeholder rule
  is tested by monkeypatching `data.contracts.universe.CONTRACTS_CSV` to a csv-module copy with
  SHFE:SS set to 'ZZSS' (the notes column has quoted commas: never split lines on ',').
- The sample is read day-first file-wide (many days > 12); every ambiguous cell (1/9, 10/8,
  8/9 ...) lands on the intended date. Confirmed 2026-09-24 for the housekeeper.
- NEVER run `py -3 - <<EOF` heredocs on this PC: they hang (pyrepl WinError 123). Write the
  script to the scratchpad and run the file. Related: [[commodity-futures-ingest]],
  [[synthetic-sample-book]].
