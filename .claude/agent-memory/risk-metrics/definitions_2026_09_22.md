---
name: definitions-2026-09-22
description: The nm-dashboard metric definitions engine/risk/metrics.py implements, what was deliberately not replicated, the Phase 2 removal of rates/equity rows (2026-09-24), and the synthetic-parquet test design.
metadata:
  type: project
---

# What engine/risk/metrics.py implements (brief changed mid-task on 2026-09-22 to "match the dashboard exactly")

Source of truth: nm-dashboard `fx_alpha/dashboard.py` ~2700-2930 (`_var95_1y_worst_day`, `_blended_vol_ann`,
`_risk_metrics`, per-leg / Portfolio rows), `core/risk.py` constants, `core/bbg_loader.py::daily_moves_and_carry`.
- Row P&L: FX/METAL `usd_delta x (dln(close) + carry)`, carry from the yields file lagged 1 day, ffilled
  onto the close dates.
- lag2 = last close on/before as_of - BusinessDay(2); s_hist = to lag2. Vol (500 obs, 2/3 trailing + 1/3 crisis
  2008-2010 when last >= 2011-01-01) and worst-ex-shocks (from 2008-01-01, shock dates zeroed) on s_hist; VaR
  (last 252 obs, -q05) and worst-raw on the FULL series. Book = concat(rows).sum(axis=1, min_count=1) then the
  same functions. net/gross = rows of `DELTA_KINDS` (FX + METAL), the header's FX net passed as fx_net_usd.
- Cap: stress_cap = vol_target x stress_pct/100 (4.5m x 50 %); over_cap = -worst_ex > cap.
Deliberately NOT replicated (told the user): the NDF-implied carry override; the dashboard's 60m vol target (ours 4.5m).

**Phase 2 removal (user approval 2026-09-24, commodity conversion):** the RATES rows (DV01 x 100 x d(10Y par %))
and the EQUITY_INDEX row (ES + SPX as "SPX") are gone, with `dv01_usd` (row and book), the scenarios'
`futures_pnl` / `equity_pct` / equity `reason`; a scenario is now `{total (= fx_total), fx_pnl, fx_total}`.
`rows_from_positions` reads only `fx` and `fx_options` of `book_positions`, so it stays right while
book-positions still returns `equity_index` / `rates` blocks. Commodity kinds did NOT join `DELTA_KINDS` /
`SCENARIO_KINDS` in Phase 4: they are separate (see [[commodity-phase4-2026-09-24]]).
**Why:** Jason's commodity book has no swaps or equity index. **How to apply:** do not reintroduce a rates or
equity kind; new kinds declare part or view (PART_KINDS / VIEW_KINDS).

Tests: synthetic history 2007-01-01..2026-09-22 built from designed log returns (CHF: -15 % SNB day, -3 % on
2020-03-16, -4 % on the last day past the lag-2 cut, fifteen -1 % days in the VaR window so q05 = -1 % exactly);
MXN never moves so its P&L is pure carry; PLN/CZK short histories for the 500/252-obs rules. The book is FX
forwards + XAU only (ES and IRS removed 2026-09-24); its blended vol is ~30k, so the "tiny target" test uses 20k.
On a PC without pyarrow, 18 of 21 tests error; a scratch pytest plugin that monkeypatches
`DataFrame.to_parquet` / `pd.read_parquet` to pickle (kept in the session scratchpad, never the repo) runs all 21.
