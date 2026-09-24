---
name: retired-macro-underlyers
description: Since 2026-09-24 the Risk tab never renders the macro book's rates (DV01, swap rates) or equity-index (SPX / ES) underlyers, their missing entries or the scenarios' equity line; unknown kinds still render
metadata:
  type: project
---

The app was forked from a macro FX / rates monitor into Jason's commodity book (CLAUDE.md
"Commodity conversion plan"). On 2026-09-24 the user approved the macro products leaving
(Phase 2 removal pass, top-down: screens first, risk-metrics next wave). ui/tabs/risk.py
filters at render time: `RETIRED_KINDS` (RATES, EQUITY_INDEX) rows, `missing` entries
starting "rates:" / "equity index:" / "<retired underlyer>:", the history's `swap_rates`
file; no DV01 card or column; scenario table is Total + FX total only.

**Why:** the screen stopped reading those outputs before the engine stopped producing them.
**How to apply:** the filters are safe to delete once `book_risk` no longer returns them.
Any other kind renders as the engine names it (KIND_LABELS
fallback); COMMODITY, SECTOR, SPREAD got labels on 2026-09-24 ([[commodity-sections-2026-09-24]]). The Book row's note and
figures stay the engine's (rows_in_series unfiltered), so they stay honest about what is summed.
Related: [[risk-tab-layout-decisions]].
