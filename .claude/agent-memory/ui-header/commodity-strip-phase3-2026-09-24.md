---
name: commodity-strip-phase3-2026-09-24
description: Phase 3 header commodity strip (gross notional, net by sector, open spreads, next first notice) - sources, the book_spreads cost and its mtime memo, other lanes' tests that read _build_figures
metadata:
  type: project
---

Phase 3 (2026-09-24) added four cards after the FX Net / Gross, behind a divider: gross commodity notional and net outright by sector from `book_positions(...)["commodities"]`, open spreads + review count from `engine.spreads.book_spreads`, next first notice / last trade = `expiry_schedule(...)["rows"][0]` (rows are worst first, so an EXPIRED row outranks a nearer RED one). A book with no open commodity futures gets one "Commodities: none" card.

**Why:** Jason's first glance (housekeeper brief, commodity conversion plan Phase 3).

**How to apply:**
- `book_spreads` values every group's P&L periods: ~1-1.7 s on the synthetic sample even through `priced_value_book` (vs ~0.15 s for the rest of the header warm), so `_spread_summary` memoises it on (db path, mtime, as_of). An engine flag to skip P&L was requested of spreads-engine; if it lands, the memo can go.
- `expiry_schedule` rows can also be 'option expiry' and 'LME prompt' events, and `business_days` counts to `alert_date` (held early for estimated physical contracts), not to the event date, so the caption names the alert date when they differ and drops the count for EXPIRED.
- The machine is noisy while other agents run tests: time with best-of-3, not one shot.
- Other lanes' tests read `_build_figures` cards (`tests/test_ui_blotter.py` filters className "header-figure" and reads `children[0].children`; `tests/test_ui.py` skips "header-divider"): every new card keeps className "header-figure" with the title first. See [[header-ltd-chart-full-span-2026-09-22]].
