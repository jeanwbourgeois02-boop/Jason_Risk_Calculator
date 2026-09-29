---
name: exposure-layout-2026-09-29
description: Exposure tab layout rebuild (2026-09-29, user-approved brief): Physical | Lots | USD, tiles, sector tilt, commodity grid with Later / Net / Gross / Leftover, row-click curve panel, Currency card (legs vs hedge), conventions and the proof recipe
metadata:
  type: project
---

Exposure (`ui/tabs/curve.py`) was rebuilt on 2026-09-29 (layout only; the polish pass is later).

- **Views** `UNIT_ID`: `physical` (default) | `delta_lots` | `delta_usd`. Physical reads curve-positions'
  `months_units` / `net_delta_units` / `gross_units`; USD reads the subsector `delta_months` (USD per
  month on the subsector, lots per month on a `split` entry: same key, different unit) and the
  exchange rows' `rows[].delta_usd` summed per month. Lots: a multi-exchange commodity row has no
  month cells (lots do not add) and shows its physical Net / Gross / Leftover with the unit suffix.
- **Rows**: a one-exchange commodity is ONE row (exchange small after the name); exchange rows only
  when `split` has more than one root (a low-stakes choice, taken to halve the rows).
- **Fallbacks** when the engine gives None: Net = the known (exchange, month) cells summed with
  `excl. N` (`_month_parts`); Gross = a dash (a month sum is not a gross); Leftover = the known
  (exchange, month) leftover cells; Later = a dash if any month is None (never partial).
  Units that do not add (UK gas therm vs TTF MWh): commodity row dashes, exchange rows in their own
  unit from `rows[].delta_units` (`_root_units_months`).
- **Book line / tiles** sum the per-root `split` delta USD of the non-FX subsectors (root granularity
  leaves less out than subsector); the FX hedges (sector `fx`) are never in them.
- **Currency card**: legs per non-USD currency (CNY+CNH as one) = split `net_delta_usd`; FX hedge =
  the spreads engine's open `strategies[].hedges[].usd_notional` in that currency (USD/CNH-position
  sign, short = minus), NOT book_positions (its CNH delta has the opposite sign convention);
  unhedged added for CNY only (Jason's convention: long China leg vs short USD/CNH); others a dash
  with the reason. The FX forwards table, Net USD line and P&L-held line follow.
- **Panel**: `PANEL_ID = "curve-chart"` inside the body, `SELECTED_ID = "curve-chart-data"` static
  store, rows `{"type": "curve-row", "idx": <subsector>}`; both ids were already in test_ui.py's
  dynamic_ok list. The panel reads `marks_official` directly (FUTURE_PX per contract; LME: SPOT +
  FWD_OUTRIGHT by prompt), because the Data tab's `futures_curve_rows` was removed the same day.
  Plotly: a category x axis (a date axis with 1-2 months collapses to hours) and an explicit y2
  range from zero (`rangemode` did not hold with `overlaying`).

**Why:** user-approved brief of 2026-09-29 ("Where is my risk, and is each spread actually as hedged
as I think?"). **How to apply:** proof = scratch sample via `ui.sample_book.build_sample_db`, a
direct `curve.render(as_of, db, unit)` per view and `curve.commodity_panel(conn, as_of, "copper")`,
then a server on 8098 and Edge DevTools on 9334 (never 9333, the Book agent's). Other agents edit
files mid-run (market_data.py was half-rewritten for a while): when `import ui.app` breaks on a
file that is not yours, load HEAD's copy in memory in a scratch launcher rather than wait or edit.
Never `taskkill /IM python.exe`; stop your own PID from netstat.
