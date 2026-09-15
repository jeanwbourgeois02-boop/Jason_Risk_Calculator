---
name: blotter-headline-rebuild-2026-09-15
description: Blotter rebuilt to headline strip (Excel Portfolio header order) + trade list with click-to-expand detail; a Dash gotcha that silently blanked the tab
metadata:
  type: project
---

2026-09-15 user verdict "Blotter tab is chaotic" -> each sub-tab is now exactly two
things: a headline card strip, then one `dash_table.DataTable`. Coordinator followed up
mid-task with the exact card order/columns from the old Excel Portfolio header row;
`ui/tabs/blotter_pricing.py::row_scoped_headline` computes LTD P&L, Daily P&L, Trades
(count), Trading P&L, LTD-1 daily, LTD-1 P&L, LTD-2 P&L, Trading P&L T-1, 5d, MTD, YTD,
all row-scoped to the trade list's currently *visible* (post header-filter) trade_ids.
`add_row_display_fields` adds Notional (USD) (`quantity * usd_per_quote(base_ccy)`,
reusing `engine.pnl.valuation.usd_per_quote` -- that helper converts *any* ccy to USD
via spot, not just quote_ccy, despite its name) and T-1 rate (FWD_OUTRIGHT on the prior
close, read straight from `marks_official`, ui-side since it's a plain read not new
P&L logic). Per-row "Marks used" `html.Details` expanders were replaced by a single
click-to-expand panel below the table (`active_cell` callback), and the free-text
"Set theme" toolbar box was dropped now that Bundles covers pair-level theme
assignment.

**Dash gotcha that blanked the whole tab for ~40 minutes of debugging**: a callback
`Input`/`State` pointing at a component that is not in the layout *at page load* (e.g.
a `dcc.Store` only rendered once some other callback's output has fired once, like
`blotter_bundles.BUNDLE_REVISION_ID`) makes Dash silently refuse to ever fire that
callback client-side -- no Python exception, no failing pytest (the
`test_every_static_callback_id_exists_in_layout` allow-list explicitly permits these
dynamic ids as *outputs*, not as load-time inputs), just a permanently empty
`children`. Screenshots taken with `--virtual-time-budget` looked identical
(sub-tab labels faded, table area blank) regardless of budget size because the page
really had reached steady state, not because it needed more time. Diagnosed by
comparing a direct POST to `/_dash-update-component` (worked, proved the Python side
was fine) against `msedge --headless --dump-dom` (showed `id="blotter-content"`
immediately self-closing with no children, while every other tab's content was
present). Lesson: any `Input`/`State` on a callback that should fire on load must name
a component present in the *initial* `build_layout()` tree, never one only created
inside another callback's return value.
