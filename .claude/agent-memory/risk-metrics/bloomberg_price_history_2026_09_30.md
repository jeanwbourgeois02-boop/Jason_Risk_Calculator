---
name: bloomberg-price-history-2026-09-30
description: Since 2026-09-30 the commodity risk history is Bloomberg's, in the book database's own price_history table (no research DB, no mock / source_kind); how LME tickets and level legs map now; the two-year depth caveat.
metadata:
  type: project
---

User decision 2026-09-30: the app stops reading the research app's rv.sqlite. `load_commodity_history(db)`
takes the BOOK database (path or conn; None = data.paths.get_db_path()); bbg-backfill fills
`price_history` (instrument_id, as_of_date, settle, volume, open_interest) on "Pull Bloomberg now".

**Why:** one data source (Bloomberg), no mock data read as real.
**How to apply:**
- FX / METAL rows too (same day): `load_history(conn)` reads the pairs from `price_history`; no yields,
  so every FX row is spot move only with "carry not included" in its note. No nm-dashboard parquet any more.
- Always pass conn: `book_risk` / `trade_risk` / `subset_var` default to `load_commodity_history(conn)`.
- No source_kind / source_note / price_check / "mock history" / "research" in my outputs or wording
  (removed 2026-09-30). risk-history's series attr is still named `research_contract_id`; I copy it into
  `history_contract` (the COMMODITY row detail no longer has a research_contract_id key).
- LME: `lme_history_contract` returns the ticket's own 'LME:CA 2026-12-10' (reader interpolates cash->3M
  in time); a level leg with only a month key uses `engine.lme.monthly_prompt` (third Wednesday).
- History is ~2 years: blended vol needs 500 observations (trail_window_bd); Chinese exchanges (~245
  days/yr) may fall short -> NaN with reason. Shock days of 2020/2022 fall outside it. Raised 2026-09-30.
- Scratch check: rebuild a sample book via tests.golden_book.build_book + random-walk price_history for
  `data.bloomberg.library.risk_history_needs(conn, today)` (scratchpad/riskmetrics/rm_verify.py pattern):
  book_risk ~1.7 s, trade_risk ~1.2 s on the sample.
