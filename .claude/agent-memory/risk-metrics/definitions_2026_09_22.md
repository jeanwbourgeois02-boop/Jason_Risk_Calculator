# What engine/risk/metrics.py implements (brief changed mid-task on 2026-09-22 to "match the dashboard exactly")

Source of truth: nm-dashboard `fx_alpha/dashboard.py` ~2700-2930 (`_var95_1y_worst_day`, `_blended_vol_ann`,
`_risk_metrics`, per-leg / Portfolio rows), `core/risk.py` constants, `core/bbg_loader.py::daily_moves_and_carry`.
- Row P&L: FX/METAL/EQUITY `usd_delta x (dln(close) + carry)`, carry from the yields file lagged 1 day, ffilled
  onto the close dates; RATES `dv01 x 100 x d(par %)` of `<CCY>_SWAP10Y`.
- lag2 = last close on/before as_of - BusinessDay(2); s_hist = to lag2. Vol (500 obs, 2/3 trailing + 1/3 crisis
  2008-2010 when last >= 2011-01-01) and worst-ex-shocks (from 2008-01-01, shock dates zeroed) on s_hist; VaR
  (last 252 obs, -q05) and worst-raw on the FULL series. Book = concat(rows).sum(axis=1, min_count=1) then the
  same functions. net/gross = FX-like rows (currencies + metals + SPX), the header's FX net passed as fx_net_usd.
- Cap: stress_cap = vol_target x stress_pct/100 (4.5m x 50 %); over_cap = -worst_ex > cap.
Deliberately NOT replicated (told the user): the payer's coupon accrual in the rates P&L (MTM only); the
NDF-implied carry override for KRW/IDR/INR/TWD/BRL (`bbg_loader.load_bbg_frames` replaces their deposit yield
with the 1M-3M NDF curve yield when the ndf_fwds file exists); the dashboard's 60m vol target (ours 4.5m).
Tests: synthetic history 2007-01-01..2026-09-22 built from designed log returns (CHF: -15 % SNB day, -3 % on
2020-03-16, -4 % on the last day past the lag-2 cut, fifteen -1 % days in the VaR window so q05 = -1 % exactly);
MXN never moves so its P&L is pure carry; PLN/CZK short histories for the 500/252-obs rules; USD_SWAP10Y with
-30bp (2008-10-15), -20bp, +40bp days for the sign test. Book built like tests/test_positions.py::_book.
