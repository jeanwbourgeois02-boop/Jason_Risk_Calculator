---
name: lme-and-futures-options-phase5-2026-09-24
description: Phase 5 on the Market data tab - status["lme"] / options-on-futures / backfill per-day option lines, library "what it is for" table, LME_CURVE in What is missing and the strip, LME curve blocks in Futures curves; shell quoting trap
metadata:
  type: project
---

2026-09-24, Phase 5 (options on futures, LME forwards). What the tab reads and why it looks the way it does:

- Feed status extras, in order: contract dates, not requestable, OIS curves, LME (`lme_block`, status["lme"] summary already starts "LME curves:"), options on futures (`futures_options_block`, `futures_options_summary`), recalc, ledger, backfill per-day options (`backfill_options_block`, status["backfill"]["options"][day], `futures_options_priced` may be absent on an older file).
- Library panel: a "What it is for" table (`library_kind_rows` over `library.needed_on(include_unrequestable=True)`, words from kind/role/product in `library_need_words`) above the ticker table. The ticker table's own `used_for` comes from `library.tickers` unchanged.
- LME forwards: instrument id = root id ('LME:CA'), asset_class 'LME_FWD', two FX_NEAR legs (metal tonnes settles_cash 0, USD settles_cash 1). `blocked_by_mark` links SPOT(as_of), FWD_OUTRIGHT(prompt) and the inventory item (root, 'LME_CURVE', as_of); notional = USD leg.
- An LME_CURVE item is complete when cash and 3M are official (`inventory.lme_curve_status`). Live needs list drops the `source` column, so the tab re-reads `lme_curve_status` for "on file"; a past close's item carries `detail`.
- Futures curves LME block: pillars from `library.lme_curve_pillars(root, as_of, through=furthest open prompt)` plus open prompts, sorted by DATE (not open-first: a curve reads by date). Previous close = the same pillar kind on the latest earlier day with the metal's marks (cash / 3M sit on other dates each day).
- An option on a future's underlying FUTURE_PX is linked to its trades through the library's role UNDERLYING rows (the sample showed CUZ26 with 0 trades blocked before).

**Why:** the next change to these status keys needs to know which reader is where.
**How to apply:** Bash commands are wrapped in eval '...': an apostrophe anywhere in a heredoc breaks the whole command (nothing runs). Write test snippets with the Write tool to the scratchpad and `cat >>` them. Other agents share the scratchpad: use uniquely named files (uimd_*).

Related: [[futures-curves-phase3-2026-09-24]]
