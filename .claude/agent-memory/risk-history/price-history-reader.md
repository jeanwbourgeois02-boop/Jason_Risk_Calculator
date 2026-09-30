---
name: price-history-reader
description: Since 2026-09-30 commodity_history.py reads the book db's own price_history (Bloomberg), not rv.sqlite; LME prompt ids, strip extras, memory-db copy, cache key
metadata:
  type: project
---

User decision 2026-09-30 ("lets do it all in here ... linking the two apps and all the potential future fuckups are best avoided"): the app stops reading `../Commodity Dashboard/var/rv.sqlite`. `engine/risk/commodity_history.py` reads the book database's `price_history` (filled by bbg-backfill on "Pull Bloomberg now", ids listed by `data.bloomberg.library.risk_history_needs`). `research_spreads.py` and its test were deleted the same day (git rm); the Book's z / percentile move to `engine.risk.trades.trade_risk` (the trade's own level on its own Bloomberg history).

Non-obvious choices made that day:
- **LME**: only 'LME:CA CASH' / 'LME:CA 3M' are stored. `contracts` carries one synthetic row per monthly prompt ('LME:CA 2026-12-16', third Wednesday) so risk-metrics' `lme_history_contract` (which looks up `history.contracts` by root/year/month) keeps working unchanged; any 'LME:CA <ISO date>' also resolves. A prompt's close = linear in time between that day's `engine.lme.cash_date` and `three_month_date`, cash before, 3M flat beyond. LME roots refuse constant maturity; `months_ahead_of` returns None.
- **Strip extras**: `contracts` adds the root's cycle months with no rows up to last close + 36 months, so a held contract Bloomberg returned nothing for still has a strip rank (falls back to constant maturity), like the research app's contract table did.
- **window_move** picks only contracts with a close on the start day (the research version could pick a contract with no rows).
- **Cache** per (path, db mtime_ns/size, contracts.csv stamp): any upload or pull reloads (journal_mode=delete). An in-memory connection is copied (price_history + contract_static) into a private `check_same_thread=False` db, not cached.
- Scratch proof: golden book into a file db, random walks for every `risk_history_needs` id: book_risk ~0.9 s, every commodity underlyer 519 days, LME included.

FX follow-up the same day: `history.py` (the FX / metal underlyers of book_risk) derives from `load_commodity_history(db).fx` (same cache): one column per currency, USD per unit via `usd_per_unit` (USD<ccy> inverted first, CNY and CNH both from USDCNH), `yields` always empty (no short-rate history is pulled, so carry is out and the row note says so). Memo keyed on the CommodityHistory object's id with the object held; in-memory books not memoised. Scratch proof on the golden book (FX needs EURUSD, GBPUSD, USDCNH, USDJPY, XAUUSD): every FX/METAL row has VaR, book_risk 0.45 s.

**Why:** consumers of other lanes had to keep working before they are re-pointed.
**How to apply:** when risk-metrics switches `lme_history_contract` to the ticket's own prompt id, the synthetic monthly rows can go. See [[research-db-quirks]] (now history only).
