---
name: curves-only-scope-2026-09-24
description: Since 2026-09-24 engine/rates prices no swap - only OIS discount curves for the option pricers; who reads what, curve_id / curve_quotes source rules, and what was removed
metadata:
  type: project
---

On 2026-09-24 the user approved that rates / IRS leave the app (commodity conversion
Phase 2, CLAUDE.md "Commodity conversion plan"). `engine/rates/` now keeps only the OIS
discount curves: `curves.py` (`build_curve_set`, `CurveSet`, flat forwards), `conventions.py`
(`CCY_RFR`, the seven OIS conventions), `qlmap.py`, `errors.py`, and `store.py` with
`bootstrap_and_store` and `snapped_at`. Removed: `valuation.py`, `instruments.py`,
`price_and_store`, `price_all_and_store`, `recalc_on_file`, `reverse_direction_marks`,
`load_fixings`, `FixingMissingError`; no PAR_RATE / PV_USD / DV01_USD / CASHFLOW_USD is
written. The old IRS sign / DV01 / direction-flip conventions are in git history only.

**Why:** Jason's book is commodities; the option pricers still need a discount curve.

**How to apply:**
- Readers outside the lane: `data/bloomberg/live.py::_curves_step` calls
  `bootstrap_and_store` (writes `curves` only); `engine/options/rates.py` imports
  `build_curve_set` / `CurveSet` / `CCY_RFR` and builds its curves in memory from
  `curve_quotes` (it does NOT read `curves` rows, and reads `bootstrap_note` via getattr);
  `snapped_at` is imported by `engine/options/store.py`, `engine/options/equity_commodity.py`,
  `data/bloomberg/vol_marketdata.py` and `tests/test_bloomberg.py`. Grep these before
  removing or renaming anything.
- `curve_id` = `f"{ccy}-{index}-OIS"`; `curve_quotes` source precedence
  (`store._read_curve_quotes`): BBG_BDP if present, else alphabetically first source;
  `engine/options/rates.py` has its own copy of that rule.
- A swap request coming back would need the user's yes (hard rule 7); restore from git,
  don't reinvent conventions. See [[log-cubic-bootstrap-nonconvergence]].
