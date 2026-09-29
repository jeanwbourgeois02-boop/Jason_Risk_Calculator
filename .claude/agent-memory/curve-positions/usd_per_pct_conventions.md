---
name: usd-per-pct-conventions
description: curve_positions' USD per 1 % move keys (2026-09-29, Phase G Risk grid): per row, long/short/net/gross per aggregate, months, leftover priced at the month's own USD per delta lot
metadata:
  type: project
---

Added 2026-09-29 because the ui was scaling delta USD x 0.01 on the screen (screens may sum, never scale).

- Row: `usd_per_pct` = delta_usd x `rows.PCT_MOVE` (0.01), `usd_per_pct_reason`; set in `rows._finish`.
- by_commodity, by_subsector (+ split entries), by_sector: `long_/short_/net_/gross_usd_per_pct`,
  `usd_per_pct_reason` (via `_pct_totals` inside `_delta_totals`); all None when any row has no
  delta USD. by_commodity / by_subsector / split: `months_usd_per_pct`.
- Leftover in USD (`leftover_months_usd_per_pct`, `leftover_usd_per_pct`, `..._reason`) = leftover
  delta lots x the month's rows' delta_usd / delta_lots; they must agree (two LME prompts of one
  month at different outrights -> None). It needs no common unit, so UK gas (ICE:M vs ICE:TFM) has a
  USD leftover while its units leftover is None.
- The ui's Risk leftover column reads spreads-engine's trade_book `usd_per_1pct`, not these.
- Scratch verification: `build_sample_db` may fail while ingest-parser has blotter.py mid-edit;
  load HEAD's `data/ingest/{common,blotter,upload}.py` from `git show` into sys.modules in scratch.

**Why:** "screens never scale a figure"; hard rule 2 (never a partial sum).
**How to apply:** keep the keys in step with the curve_positions docstring; see [[leftover-conventions]].
