# nm-dashboard market history (verified 2026-09-22, pandas + pyarrow reads)

Two copies on this Mac, both siblings of the repo:
- `/Users/legend/PycharmProjects/bbg_data/bbg_data/` — fresh, ends 2026-09-16 (fx_marks 6975 rows).
- `/Users/legend/PycharmProjects/nm-dashboard/bbg_data/` — the dashboard's own folder
  (`core/bbg_loader.py:393` = `<fx_alpha's parent>/bbg_data`), but its copy ends 2026-08-13.
- `/Users/legend/PycharmProjects/nm-dashboard/fx_alpha/bbg_data` does NOT exist (the brief assumed it).
`engine/risk/history.py::DEFAULT_DIRS` lists fx_alpha/bbg_data, nm-dashboard/bbg_data, bbg_data/bbg_data; since
the user's 2026-09-22 decision the existing copy whose spot index runs latest wins (ties keep the order), and
`History.note` names every copy seen with its last date; `RISK_HISTORY_DIR` overrides. On this Mac the fresh
bbg_data/bbg_data copy is chosen.

Files (index `date`, DatetimeIndex, weekdays incl. holidays, all float64, no NaN in the last 300 rows):
- `bbg_raw_fx_marks.parquet` 26 cols: AUD BRL CAD CHF CNH CZK EUR GBP HKD IDR ILS INR JPY KRW MXN NOK TWD NZD PLN
  SEK SGD SPX THB TRY XAU ZAR. USD per ONE unit (AUD 0.7129, CHF 1.2212, JPY 0.00644, BRL 0.1928). SPX = index
  level (17007.9 on 2026-09-16), XAU = USD/oz (4315). KRW/IDR/INR/TWD/BRL = 1 / `<CCY>_NDF1M` of
  `bbg_raw_fx_ndf_fwds.parquet` exactly (checked 2026-09-16), i.e. the 1M NDF outright, not spot.
- `bbg_raw_fx_yields.parquet` 28 cols: the 26 above minus nothing, plus USD and USD_LIBOR3M; percent
  (USD 3.84, BRL 12.3, CHF -0.22); XAU pinned to 0, SPX = USD. Carry = (y_ccy[t-1] - y_USD[t-1])/100/365.
- `bbg_raw_fx_vols.parquet` 24 cols, 1M ATM implied vol in percent (AUD 7.34, BRL 18.83); no XAU/SPX. NOT used
  since the change of brief (the dashboard blends trailing and 2008-10 crisis vol, not implied).
- `bbg_raw_rates.parquet` 138 cols `<CCY>_SWAP<tenor>` (+ `_LEG` variants), percent (USD_SWAP10Y 4.589).
  Ccys: AUD CAD CHF CNY EUR GBP JPY NOK NZD SEK USD. USD tenors: 3M 6M 9M 1Y 2Y 3Y 5Y 10Y 20Y 30Y.
- `bbg_raw_gold.parquet` GOLD = XAU column. Other macro files not used.
- CHF on 2015-01-15: 0.9816 -> 1.1916 (+19.4 % in USD-per-CHF terms) — the SNB day is in the data.

DV01: this app's `DV01_USD` = NPV(+1bp) - NPV(base) (engine/rates/valuation.py, positive for a payer) which is
already the dashboard's "+DV01 = payer, yields up -> P&L up" (core/rates_loader.py docstring). No flip.

Sample DB `data/raw/risk.db` (1615 trades, marks to 2026-09-21): book_risk runs in ~0.2 s, 21 underlyers.
