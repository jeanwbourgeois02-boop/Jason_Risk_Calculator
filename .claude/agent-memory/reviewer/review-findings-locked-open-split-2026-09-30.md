---
name: review-findings-locked-open-split-2026-09-30
description: 2026-09-30 review of trade_book LTD split into pnl_open / pnl_locked (engine/spreads/trades.py _split_leg/_split_trade/_unwound): no criticals; float-dust red "0", FX-option sell-back under a new id stays "open"
metadata:
  type: project
---
Uncommitted change reviewed 2026-09-30 (user yes under hard rule 7). No criticals.

- Verified: trade_book output identical to HEAD except the 4 new keys (HEAD copy imported from scratchpad, golden book at
  3 AS_OF_DATES + 2026-09-30); open + locked = LTD per leg and trade; injected reductions / cross-zero / same-day
  netting on CNY future, USD future, LME, FX fwd match hand average-cost to the cent.
- W: never-reduced legs get locked ~ -1e-10 (float dust) -> Book shows a red "0" (km_text rounds, sign_class negative).
- W: FX options: `_contract_key` groups by instrument_id and the export books a sell-back under a NEW id, so a partial
  sell-back is two legs, both "open", locked 0, unwound False. Dormant product in Jason's book.
- Info: non-USD locked-in moves with the day's spot (by convention: still-open trades re-convert daily).
- Same-day buy+sell uses `_entry_value`'s day rule (matched within the day), not running average cost; spec-approved.
- No test in test_spreads.py and not logged in docs/tests-owed.md.

**How to apply:** on a re-review, check the dust snap and whether FX-option sell-backs are grouped by terms
(valuation's `_option_terms_key`) rather than id. Recipe: [[review-findings-c9-c10-c12-2026-09-24]] isolated-copy idea,
here `git show HEAD:file > scratchpad` + importlib to diff outputs.
