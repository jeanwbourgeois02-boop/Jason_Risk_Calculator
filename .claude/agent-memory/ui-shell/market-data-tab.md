---
name: market-data-tab
description: ui/tabs/market_data.py — per-pair layout (2026-09-15 rewrite) over inventory table (Task C3)
metadata:
  type: project
---

2026-09-15 user decision superseded the flat C3 inventory-table layout: the tab is now
organised BY CURRENCY PAIR. Top bar has the date picker, a single pair dropdown
(`PAIR_DROPDOWN_ID`, the *only* dropdown on the tab, options = every FX instrument with
a trade or a mark on the as-of date, default = the pair with the most open trades),
the pull button and one combined status line (`top_bar_status`, = `feed_headline` +
`" | " + backfill_headline` when there is one). Below: `pair_body` renders the spot
line, the forward-curve table (`forward_curve` — one row per settle_date with a
FWD_OUTRIGHT mark that day, all sources, tenor label from `tenor_label`, forward
points from `forward_points`, "used by book" trade count from `used_by_book`) and a
plotly `dcc.Graph` (`curve_chart`, modebar hidden, official marks as a line, other
sources as diamond markers, spot as the first point). Bottom: `completeness_strip`
(compact coloured squares with a tooltip, replacing the old DataTable) and
`manual_entry_form(default_pair=...)` pre-filled with the selected pair via a
`State`-round-trip callback (the form's instrument `dcc.Input` is also an `Output` of
the body-refresh callback).

The old flat `mark_inventory` DataTable and its `INVENTORY_TABLE_ID` are gone from
this tab entirely — `data.bloomberg.inventory.mark_inventory` is no longer called
here (only `close_completeness` still is). `diagnostics_panel` / `feed_headline` /
`backfill_headline` stay defined at module scope, unrendered by this tab's own body,
because `ui/tabs/cash_ladder.py` still imports them from here; do not delete them.

Public surface C5/others call: `build_layout(default_date)`, `register_callbacks(app,
get_db_path)`, plus `diagnostics_panel`, `feed_headline`, `message_box` (unchanged
names, kept for backward compatibility with `cash_ladder.py`).

Key defaults picked (undocumented in BUILD_PLAN.md / the 2026-09-15 instructions, no
one to ask):
- Tenor buckets and tolerances (calendar days): 1W±2, 2W±2, 1M±4, 2M±4, 3M±5, 6M±6,
  9M±7, 1Y±8; anything else is "broken". Chosen loosely enough to catch standard
  month-end/holiday drift without misclassifying a genuinely broken date.
- Forward points = outright − spot, rounded to the pair's own display decimals (2 for
  any pair containing "JPY", 4 otherwise) — not scaled into "pip" units, since the
  instruction text was ambiguous between the two and the raw difference is simplest.
- "Official first" ordering in the curve table: rows sorted by `(settle_date, not
  official)`, so an official and a reconciliation-only row on the same date both show,
  official first.
- Close-completeness strip still shows the trailing 20 business days
  (`COMPLETENESS_DAYS = 20`); padded start date is `20*1.6+5` calendar days back
  before calling `close_completeness`, then `.tail(20)`.
- Manual entry form is free-text instrument id + settle date (no dropdown/validation
  against `instruments`), mark-type dropdown lists all 8 contract mark_types.

Important: avoid importing `ui.app` from this module. At the time of writing,
`ui/app.py` imports `ui.tabs.pnl` which does not exist yet (mid-refactor across C1-C5),
so any `from ui.app import connect_readonly` blows up this module's tests with an
unrelated ImportError. Wrote a local `_connect_readonly` helper instead (same
`file:...?mode=ro` URI trick). Manual-entry writes use `data.ingest.schema.connect`
(needs a writable connection, unlike the read-only body render) — `write_manual_mark`
calls `conn.commit()` itself.

Schema gotcha: `trades` table has 14 columns as of the theme-column migration (Task B) —
last column `theme TEXT NOT NULL DEFAULT ''`. Any hand-built INSERT INTO trades in tests
needs all 14 values or it raises `OperationalError: table trades has 14 columns but N
values were supplied`.
