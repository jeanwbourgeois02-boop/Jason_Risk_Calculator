---
name: commodity-phase4-2026-09-24
description: How book_risk's commodity underlyers (COMMODITY / SECTOR / SPREAD) are built, the part-vs-view rule, the choices taken as low-stakes (crisis fallback, shock days, LME mapping, spread net = leftover), and how the tests run on this PC.
metadata:
  type: project
---

Phase 4 (2026-09-24) added `engine/risk/commodity.py`; `book_risk` gained `commodity_history=` and
`commodity_scenarios_path=`, top-level keys `commodity_history` and `commodity_scenarios`.

- Parts vs views: FX, METAL, COMMODITY (one per root, curve-positions rows at `delta_lots`) are
  PARTS, summed into the book. SECTOR (sum of its COMMODITY rows) and SPREAD (spreads-engine open
  spreads, futures legs at `open_lots`) are VIEWS, never added to the book. Each row has `key`
  (FX: the ccy; 'COMMODITY:<root>', 'SECTOR:<sector>', 'SPREAD:<spread_id>'), `role`, `parts`.
- Book net/gross USD stay FX+METAL (the dashboard's); commodity delta is apart as
  `commodity_net_usd` / `commodity_gross_usd` (curve-positions' net/gross_delta_usd).
- SPREAD net/gross USD = its leftover outright (spreads-engine `leftover` usd_notional), 0 for a
  clean calendar.
- Commodity lag-2 = last settlement <= as_of among the commodity series less 2 BD; book lag-2 = the
  later of FX and commodity.
- Crisis window 2008-10 absent from the research history (it starts 2020-09-21): trailing vol
  alone, `vol_note` = "crisis window not in history: trailing vol only", appended to the row's
  `reason` (applies to any row in that case, FX too; values unchanged vs the old silent rule).
- Shock dates added (one list for all rows): 2020-04-20, 2020-04-21 (WTI), 2022-03-07, 2022-03-08
  (LME nickel). Config gained `vol_target_placeholder` / `vol_target_note` (4.5m is still the macro
  fund's figure, open-questions C5).
- LME_FWD history = the research contract of the prompt month (research has LME:CA etc. with monthly
  contracts like LAF21), else the listed one with last trade nearest the prompt. Options use their
  `underlying_id`'s history at delta lots.
- Series are computed per lot once (`_PerLot`) and scaled: daily_pnl_series_for_position is linear.

**Why:** brief from the housekeeper, Phase 4 of the commodity conversion plan.
**How to apply:** new underlyer kinds must declare part or view; never let a view into the book sum.
Tests: 13 commodity tests on a synthetic research sqlite in tmp_path run without pyarrow; an autouse
fixture points COMMODITY_HISTORY_DB at a missing file so no test reads the sibling repo. The 18 FX
tests need parquet: run with a scratchpad plugin `-p pickle_parquet` (PYTHONPATH=scratchpad) that
patches `DataFrame.to_parquet` / `pd.read_parquet` to pickle. Never `py -3 -` heredocs in Git Bash
here: they hang; write scripts to the scratchpad instead. See [[definitions-2026-09-22]].
