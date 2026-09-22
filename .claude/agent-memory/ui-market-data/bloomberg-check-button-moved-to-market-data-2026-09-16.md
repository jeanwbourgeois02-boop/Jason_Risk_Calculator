---
name: bloomberg-check-button-moved-to-market-data-2026-09-16
description: "Check Bloomberg connection" button relocated from header.py (shown on every tab) to market_data.py (Market Data tab only)
metadata:
  type: project
---

User decision 2026-09-16: the "Check Bloomberg connection" button, its results panel,
and the diagnostics entry point/placeholder/renderer moved from `ui/tabs/header.py`
into `ui/tabs/market_data.py`. New ids: `market_data.BBG_CHECK_BUTTON_ID` =
"market-data-bbg-check-button", `market_data.BBG_RESULTS_ID` =
"market-data-bbg-check-results" (old header ids `header-bbg-check-button` /
`header-bbg-check-results` no longer exist). Functions moved 1:1:
`_bbg_diagnostics_entry_point`, `_run_bloomberg_diagnostics_placeholder`,
`_render_bbg_results`, `run_bloomberg_diagnostics_safe`, all now on `market_data`.

Why: the button was global (rendered above every tab via the shared header block) but
only makes sense in the context of checking the Bloomberg feed, which is what the
Market Data tab is for.

How to apply: any future test or caller referencing `header.BBG_CHECK_BUTTON_ID` /
`header._render_bbg_results` etc. is stale — use the `ui.tabs.market_data` equivalents.
CSS classes `.bbg-check-*` kept the same names but were re-themed in
`ui/assets/style.css` from white-on-navy (header background) to the light page/card
palette (`var(--navy)` button, `var(--text)`/`var(--muted)` text, `var(--pos)`/
`var(--neg)`/`var(--warn)` status colours) since the block now sits on the light page
background, not inside the dark `.header-block`.

Tests relocated 1:1 in `tests/test_ui.py` from `header.*` to `market_data.*`; added
`test_header_layout_no_longer_includes_bbg_check_button_or_results` as a regression
guard that the button stays out of the shared header.

Unrelated observation: at the time of this change, `tests/test_bloomberg_diagnostic.py`
was failing on main due to a concurrent, out-of-scope change (another agent renamed/
deleted `tools/bloomberg_diagnostic.py` -> `tools/bloomberg_terminal_probe.py` /
`tools/bbg_diagnostics.py`). Confirmed via `git stash` that this failure pre-dates and
is independent of the button move — not a `ui/` regression, report to housekeeper if
seen again.
