---
name: bloomberg-errors-one-place-2026-09-30
description: Pull / backfill / feed error text lives only on the Data tab's Bloomberg card; every other screen says a state plus "See Bloomberg on the Data tab"
metadata:
  type: feedback
---

User, 2026-09-30: "this error thing for the bloomberg pull i see it everywhere in the headlines and other pages - lets all have it in the bloomberg diagnostics section in the data tab please - all in one place as it looks disgusting".

**Why:** error sentences (terminal not reachable, backfill could not fill, options step failed) were repeated in the top bar, header card hovers, P&L and Options; the user wants one place.

**How to apply:**
- Outside the Data tab (market_data.py, data_checks.py, data_kit.py, card id `market-data-bloomberg`) never render `status["reason"]`, step details, backfill reasons or `past_close_explanation`. Say the state and `formatting.DATA_POINTER` ("See Bloomberg on the Data tab"); link with `tab_link(..., formatting.DATA_TAB_KEY, idx)` and undo its faded style inline (`style={"opacity": 1, "fontSize": "inherit", "color": "inherit"}`, assets belong to whoever owns CSS that wave).
- Top bar: `feed_controls.bar_state` words = state only ("Last pull 09:14 NY · 2 problems", "Not connected", "No pull yet", "Pulling…", "No report yet"); N = `data_checks.pull_problems(status)` less its "No pull" row; the element is a tab link, idx `top-feed-status`.
- Header: `_reference_reason` keeps the P&L fact (ref date, N of M unpriced, mark kinds) then the pointer; `backfill` arg kept, unused. Marks chip hover = latest close time + "N prices missing: see the Data tab", chip wrapped as a link, idx `header-marks`.
- `feed_headline` still carries the full reason: only the Data tab reads it now. `past_close_explanation` likewise Data tab only.
- A per-trade reason (an option skipped by the pricer) stays on its row; only whole-step errors become the pointer.
