---
name: pnl-period-columns-2026-10-01
description: P&L tab 2026-10-01 - chart and period switch removed; one table with Today | 5d | MTD | YTD | All columns, split on cell hover, MTD default order, heading sort store, warm-up periods()
metadata:
  type: project
---

User 2026-10-01: "the graph on the p&l tab - i think it needs to be removed - and the today last 2 days
5d onwards - that should be in the table". Done in `ui/tabs/pnl.py`:

- No chart, no period switch, no Custom range (ids `pnl-chart`, `pnl-period`, `pnl-custom*` gone; CSS
  `.pnl-chart*` gone). Never bring the chart back without the user asking.
- `periods(conn, as_of)` -> {today, d5, mtd, ytd, all: `period(...)`} (each still memoised per choice,
  `period_explain` per period). Every table / headline / CSV / drawer function takes `ps` (that dict).
- Columns Today | 5d | MTD | YTD | All; each cell's hover = its split lines (`split_lines`: Spread / FX /
  Hedge / New / Realised / Other, All: Realised / Open, non-zero only); unfiltered total row adds
  "Equals the top bar's ..." (all five verified equal to `period_rows` entries on the sample).
- Order: |MTD| by default; heading click -> `pnl-sort-store` {key, dir} desc -> asc -> default
  (`next_sort`, `_rank`). By month unchanged (|months total|).
- `tab_state` drops stale `pnl:<col>` number filters (old Spread/Hedge columns) so no invisible filter.
- Headline = Best today / Worst today only; the close each period is measured from is on its heading hover.
- Warm-up step `pnl` warms `periods()`; the `pnl_periods` step was removed.

**Why:** the user found the chart useless and wants every period side by side.
**How to apply:** proof = `tools.ui_check.build_sample`, `pnl.periods` sums vs `pnl.period_rows(...).entry`.
