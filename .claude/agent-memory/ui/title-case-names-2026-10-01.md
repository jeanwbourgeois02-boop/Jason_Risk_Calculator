---
name: title-case-names-2026-10-01
description: Commodity and spread names in Title Case on every tab (user 2026-10-01) — formatting.title_name, plain_leg_name, titled_trade_book in the shared memo; joining words stay lower
metadata:
  type: feedback
---

Names are Title Case: "CME Feeder Cattle Oct26", "SGX Iron Ore Oct/Nov26 & Feb/Mar27 Calendar", "SHFE Zinc vs LME Zinc, Oct26 · Cross-exchange", "Long Feeder Cattle / short Live and Feeder Cattle". Joining words (vs, and, long, short, call, put, forward, hedged ...) stay lower mid-name; acronyms untouched; a hyphenated word capitalised on its first part only.

**Why:** user, 2026-10-01: "can the spreads be capitalised".

**How to apply:**
- `ui/tabs/formatting.py::title_name` (keep-lower list `_NAME_LOWER`; only all-lower-case words change). `SHORT_ROOT_NAMES` written in Title Case; `_short_from_name`, `short_template_name`, `spread_name` fallback go through it.
- The engine still writes lower case (`engine.spreads.trades.leg_name`, `what_it_is`, `commodity_words`): the screens title them. `blotter_pricing.titled_trade_book` edits the fresh `trade_book` result inside `shared_trade_book`'s compute (legs' name/commodity, parts' and trades' what_it_is, next.leg) before memoising — never mutate the memo afterwards.
- Every screen call of the engine's `leg_name` is `formatting.plain_leg_name` (imported `as leg_name` in book_contracts, blotter_fills, data_checks, risk_folds).
- Also titled: book `_commodity_words`, `commodity_label`, part head kind ("· Cross-exchange"), size hover side labels; `tf.family_label`, book_contracts family; Risk subsector names, ratio legs / bigger leg hovers, leftover lines.
- Engine prose sentences (flags, reasons, level sources) are left as the engine writes them: they are sentences, not names. A Request asks spreads-engine to title at source; title_name is idempotent so nothing breaks when it does.
