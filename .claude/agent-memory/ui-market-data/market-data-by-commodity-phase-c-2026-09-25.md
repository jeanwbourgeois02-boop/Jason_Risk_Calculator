---
name: market-data-by-commodity-phase-c-2026-09-25
description: Screens redesign Phase C on the Data tab — "Market data by commodity" section (strip, sector-grouped selector, chart with research line, table), new ids, 11-output body callback, what "estimated" means, research units
metadata:
  type: project
---

Phase C (user-approved 2026-09-25): the Futures curves section became "Market data by commodity"
(`FUTURES_CURVES_TITLE` value changed, constant name kept for tests).

Layout: static section Div `COMMODITY_SECTION_ID` = [`COMMODITY_STRIP_ID` (title + strip, body
output 11 = out[10]), `COMMODITY_PICKER_ID` toolbar with static `COMMODITY_DROPDOWN_ID`,
`FUTURES_CURVES_PANEL_ID` (chosen commodity's view, out[7])]; then an "FX" h4 heading before the pair card.
- The dropdown is STATIC and filled by its own callback (`commodity_picker_state`: options, value,
  picker style, section className; date + book revision, State = current value kept while listed).
  Putting the dropdown inside a re-rendered output would reset the choice every interval / loop.
- Body callback gained Input(COMMODITY_DROPDOWN_ID) appended LAST (positional calls in tests keep working).
- One rule for which commodities are listed: `_curve_roots(curve_positions(...))`, shared by
  `futures_curve_rows` and `commodity_options`.
- Default = largest gross_usd from curve_positions by_commodity; gross_usd None ranks after (then gross_lots).
- "Estimated" = official row with source BBG_INTERP (only LME open prompts in practice; FUTURE_PX is
  never interpolated into existence). Engine's on-the-fly near-marks INTERP is NOT counted: the tab shows gaps.
- Chart: x = contract month 'YYYY-MM' for futures, prompt date for LME; missing = y None gap +
  paper-y annotation "missing" with hovertext reason; estimated = "circle-open". Research trace uses
  `raw_settle` (same unit as our FUTURE_PX); LME research x = its expiry. Research read only for the chosen root.
- Rows carry `_x`, `_source`, `_previous_date` for the chart.
- All commodities' missing reasons still go to the Data issues drawer, not only the chosen one's.

**Why:** "nothing hidden behind the selector" (strip) and hard rule 2 (research never substitutes a mark).
**How to apply:** tests monkeypatch `engine.risk.commodity_history.research_curve` (fixture
`research_calls`); the mock research DB on this PC has values unrelated to the synthetic marks (CL 81 vs 68).
Scratchpad is shared with other lanes: use unique file names (md_c_*.py); bash heredocs with long
Python bodies failed twice with "unexpected EOF" here, so write the file with Write and run it.
Related: [[data-tab-phase-a-2026-09-25]], [[futures-curves-phase3-2026-09-24]].
