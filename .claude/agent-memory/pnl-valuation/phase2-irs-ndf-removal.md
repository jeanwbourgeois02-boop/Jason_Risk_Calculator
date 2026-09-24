---
name: phase2-irs-ndf-removal
description: 2026-09-24 Phase 2: IRS and NDF valuation removed from valuation.py; what stays; how the no-move proof was pinned
metadata:
  type: project
---

- User approved 2026-09-24 (CLAUDE.md "Commodity conversion plan", Phase 2) that rates / IRS, NDFs
  and the equity index leave the app. valuation.py lost `_irs_sql`, `_open_irs_row`,
  `_settled_irs_row`, `_IRS_NUMBERS`, `ndf_fix`, `ndf_fixing`, `_ndf_fixed_on`,
  `ndf_fixed_valuation`, `_ndf_fixed_row`, the NDF_FIX / NDF_1M / CASHFLOW_USD special cases of
  the near-marks rule, and `_fx_sql`'s `i.is_ndf` column. `_guarded_row`'s "shown only" figure
  (swap quantity / fixed rate) went with IRS: the numbers tuples are now (attr, column).
- **Why:** macro trader's products; Jason's book is commodity futures, FX hedges, FX options.
- **How to apply:** an `is_ndf = 1` forward is now an ordinary deliverable forward; a leftover IRS
  trade gets no value_book row. EQ_OPTION stays as the generic listed-option path (Phase 5 retargets
  it to options on futures); FX options and the closed-out rule stay (dormant, not approved for removal).
- Proof pattern that worked: `git archive HEAD | tar -x` into the scratchpad (never `git stash`, the
  tree is shared), copy the new test file in, compute the figures there, pin them to the bit in
  tests/test_valuation.py (`_PIN_AT_HEAD`, HEAD e660974).
- My old IRS / NDF tests in tests/test_pnl.py were deleted too once pnl-series had finished with that
  shared file (the housekeeper sequences it); its `_vb_conn` future is now CLU6 Comdty (x1000), not ES.
