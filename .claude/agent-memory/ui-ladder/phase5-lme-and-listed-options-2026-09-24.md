---
name: phase5-lme-and-listed-options-2026-09-24
description: How LME forwards and options on futures reach the Ladder since Phase 5 (sample grew to 50 trades); pair_label for root-id pairs; new sample pins as of 2026-09-17
metadata:
  type: project
---

2026-09-24, Phase 5 sample (ingest-parser): 4 CMDTY_OPTION + 3 LME_FWD trades added.

- ladder-grid now gives an LME_FWD one record for its USD leg: `currency` 'USD',
  `currency_pair` = the ROOT id ('LME:CA'), prompt date as value date, settled at SETTLED
  past the prompt. The metal leg (ccy 'LME:CA', settles_cash 0) never reaches the grid.
  LME cash therefore lands in the USD row only; Net/Gross USD are untouched (USD legs).
- 'LME:CA' is 6 chars: `engine.ladder.exposure.local_vs_usd`'s contra() still returns ""
  because 'USD' is neither half, so the Local-vs-USD table ignores it. Correct, but fragile.
- `ui.tabs.exposure.pair_label` turns a root-id pair into the root name without its
  parenthetical ('LME copper'); used in `legs_export_frame` (the only place a pair shows).
- Open listed options (CMDTY_OPTION) are neither records nor unresolved (ladder-grid's
  rule); an expired unrealised one is named in the settled caption like a future.
- Sample pins as of 2026-09-17: USD settled 1,990,310 (was 2,175,350; -185,040 LME
  nickel), USD 10 Dec -985,000, USD 16 Dec 1,035,100, USD local delta 1,540,410; CNH/EUR
  unchanged; caption names CLQ26 Comdty and GCQ26C 3300 Comdty.

See [[phase2-macro-removal-2026-09-24]] for the earlier pins.
