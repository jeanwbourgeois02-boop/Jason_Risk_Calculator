---
name: spread-levels
description: Phase B (2026-09-25) level / position / research-key conventions in engine/spreads/levels.py, and the research app facts behind them (quote units, calendar ids, CNH vs CNY)
metadata:
  type: project
---

Built 2026-09-25 (Screens redesign Phase B), confirmed by risk-history against the real research db:

- Levels are in the research app's QUOTE units: raw FUTURE_PX / fill x `price_scale` (corn and RBOB
  are quoted in cents, scale 0.01, so a corn calendar reads 0.02 USD/bu, not 2). A raw-cents level
  makes the research sigma / z-score 100x off. `usd_per_unit` is per 1.0 of that unit.
- Template level = sum w x price x scale x (1/qty_factor, else 1/quantity_factor) x S_leg/S_unit
  (fx only when currencies differ) + constant. `qty_conv` = lot_in_quote_units / units_per_lot.
- usd_per_unit = fitted open size x S_unit (template, size in spread qty) or lots x lot_in_quote_units
  x S (calendar). Proven: level_change x usd_per_unit == Daily exactly on a clean USD spread;
  across currencies it differs by the FX move on the fill.
- Research key: templates = template id, instance ''; calendars `cal.<exch>_<code>.<near>_<far>`
  lower case, instance = near year; far year must be near + (0 if far month > near else 1). Research
  only carries adjacent calendars plus a few named ones: an unfound key is risk-history's to report.
- CNY: our valuation converts through USDCNY (usd_per_quote); the research app through USDCNH unless a
  template names USDCNY. The level follows the valuation, so it agrees with P&L, not the research app
  (a CNH-CNY basis gap). Chosen as the brief's "same marks as the valuation".
- Position identity includes direction (brief): a calendar bought then sold on a later date shows
  as a long and a short position, not netted. **How to apply:** if the user wants them netted, it is
  a change to `_position_key` only.
- Phase C (2026-09-25) `history.position_history`: LTD per date excludes a whole member spread
  when one of its trades is unpriced (the positions' period rule, so LTD(a)-LTD(ref) == period P&L);
  the identity breaks only where the period stepped back or filled a trade on its reference close.
  The level reads every member's trades per leg (position `level_spec`), not the first member's.
  Cost is one whole-book valuation per date: ui-spreads should pass the memoised filled reader.
- Golden book mark dates skip most trade dates, so cross-currency entries there are n/a (no spot on
  the trade date): expected, not a bug. See [[template-units]], [[grouping-rule-decisions]].
