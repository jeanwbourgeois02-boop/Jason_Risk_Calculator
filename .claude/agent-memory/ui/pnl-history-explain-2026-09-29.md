---
name: pnl-history-explain-2026-09-29
description: The P&L tab rebuilt 2026-09-29 as history + explanation (period switch, explain tiles, LTD + daily bars chart, best/worst positions, month table, track record) off engine/pnl/series.py and engine.spreads.period_explain; what stayed for the Book; proof recipe
metadata:
  type: project
---

Since 2026-09-29 `ui/tabs/pnl.py` answers "How did the P&L get here, and what drove it?" (user
approved the layout that day). The attribution table, its group switch and the realised / open
lines left (the Book is the cross-section now).

- One callback fills eight static slots (note, tiles, sections style, chart, contributors,
  months, track, issues slot) on the as-of store, `pnl-period` (default mtd), `pnl-chart-by`
  (book | type | commodity), `pnl-month-by` (type | commodity), the revision and the interval.
  The switches live in the static layout (a static callback input must always be rendered).
- `gather(conn, as_of, key)` = one `daily_series` + the Book's memoised `book._spreads` passed to
  `period_explain(series=, spreads=)`, plus `period_pnl` (for the header's markers: excl. /
  filled / ref), `track_record`, `monthly_pnl`. ~0.5-2 s per render on the sample.
- The engine takes any as-of (a weekend's too) since pnl-series' same-day update: never step
  the as-of back in the UI. `track_record['daily']` is a list of dicts (value, n_excluded,
  excluded ...), each day the header's own Daily.
- Grouping for the chart and month table: `book.trade_types({"labels": book._labels(conn),
  "spreads": ...})` for the type (else Outrights / Options on futures / LME forwards / FX
  hedges), `engine.curve.subsector_name` for the commodity; both wrapped in try (book.py is
  often mid-rebuild by another agent).
- `period_rows` / `PeriodView` / `PERIODS` stay in pnl.py: the Book reads them, the smoke test too.
- `header._build_chart` reads `daily_series(...).chart_points()`; `_cached_ltd` kept unused.

**Why:** the user's approved P&L layout; the header-match rule (Total tile = header figure).

**How to apply:** proof = `pnl.gather(conn, d, key)` for each key vs `header._build_figures`
(`_fmt_usd` of the card's hover first line); matched on 2026-09-18, 09-29 and Saturday 09-26.
Screenshot recipe: `scratchpad/pnl/shots/shots_pnl.py` (DevTools port 9335, app on 8097).
Contributor rows are `formatting.tab_link` buttons with components as the label (grid via
`.tab-link.pnl-contrib-row`).
