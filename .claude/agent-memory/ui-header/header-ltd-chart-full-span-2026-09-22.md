---
name: header-ltd-chart-full-span-2026-09-22
description: The header's LTD line chart spans the whole book (first trade date to as_of), not a 20-day window; user wants every previous close on it. What that cost, what broke elsewhere, and the shared-file drift seen while doing it.
metadata:
  type: project
---

User decision 2026-09-22, verbatim: "yes I want to see the ltd line chart, which requires all
the previous closes". The chart runs from MIN(trade_date) to as_of, oldest first, one
`_cached_ltd` (= one `priced_value_book`) per business day; `_CHART_LOOKBACK_DAYS` and
`_business_days_back` are gone (`_chart_days` replaced them).

**Why:** the user reads the chart as the book's history, not a recent-window sparkline; the
Details is collapsed by default and the chart callback only runs while it is open, so the
full span is paid once per database revision (44 evaluations on the 2026-09-22 dev book,
`data/raw/risk.db`, first trade 2026-07-22). The cost grows with the book: if a user ever
says the chart is slow to open, that is the lever (memoise more, never trim the span
without asking).

**How to apply:**
- Layout choices taken without asking (low stakes, reversible): x axis `type: date`,
  `tickformat "%d %b"` so months of daily points read as dates; height stays 260; a book
  with no trade on or before as_of gets a sentence ("No trades dated on or before <d>:
  nothing to chart."), not an empty graph, per CLAUDE.md "no figure blank without its
  reason". Report these under "What was done", not as questions.
- `tests/test_ui_blotter.py::test_build_chart_returns_graph` (ui-blotter's file) asserted
  `n_points <= header._CHART_LOOKBACK_DAYS`; removing the constant breaks it. Before
  removing a module-level name from header.py, grep `tests/` for `header._<name>`: other
  lanes' tests reach into this module.
- Shared-file drift: while I worked, another lane's uncommitted block (`AS_OF_PICKED_ID`,
  `as_of_after_pick`, `as_of_after_tick`: the header follows today's date unless a picker
  chose another day, user 2026-09-22) appeared in ui/tabs/header.py between my Read and my
  Edit. Re-read (`git diff ui/tabs/header.py`) before every edit and never revert what is
  not yours; report it so the session commits by explicit path knowingly.

Related: [[header-partial-pricing]] (the per-day partial sum and gap the chart keeps).
