---
name: pnl-chart-two-panels-2026-09-30
description: P&L tab chart rebuilt 2026-09-30 after "this graph is complete shit": two stacked panels on one business-day category axis, hover with top 3 trades, padded ranges; plotly hover traps and a hover-screenshot recipe
metadata:
  type: project
---

User 2026-09-30 on the P&L chart: "this graph is complete shit - the pnl should be also in the book
but in the pnl tab its just much more in depth". Rebuilt in `ui/tabs/pnl.py` (chart only; table,
switches, track record untouched):

- `chart_points(b, p, state)` -> {x (ISO), daily, cum, text, ref}: the reference close first at 0
  (not for All), then `_period`'s days summed over the rows showing. `chart_figure(b, p, state, pts)`
  draws a dict figure: top domain [0.38, 1] line filled to zero (the period's P&L to date, ends at the
  table total, last value annotated), bottom [0, 0.28] green / red day bars, `xaxis matches x2`.
- Per-slice coloured bars were dropped (user asked green up / red down); `_PALETTE` gone.
- Category axis values are the readable day ("Tue 8 Sep", year only on an earlier year's day,
  `_categories`), tickvals a subset (`_tick_days`: every day <= 16, weekly <= 70, monthly after, never
  two labels side by side), NO ticktext.

**Why the category text is the label:** in hovermode "x" plotly prints a common x label on the axis
and uses the ticktext there, so ISO values showed "2026-09-08" and ticktext showed "Jul 26" for a
day in month mode. With the category itself readable, tick and hover agree; the hover box then
carries no date line.

**Other traps:** a fill-to-zero or bar panel autoranges with 0 at the edge (label and zero line
collide): set `range` padded 10 % each side. Markers only when <= 31 days.

**How to apply / proof:** `.venv/Scripts/python.exe` has playwright (plain `py -3` does not). Hover
shots: build `tools.ui_check.build_sample(folder)`, `create_app(db_path=..., start_feed=False,
build_fingerprint="ui-check")`, `tools.ui_check._free_server(app)` in a thread, click
`#main-tabs >> text=P&L`, `_settle`, hover `#pnl-chart .scatterlayer .point` nth, screenshot the
`.js-plotly-plot`. A standalone HTML page with plotly.min.js from file rendered blank: use the app.
