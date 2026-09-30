---
name: no-research-db
description: since 2026-09-30 the check never reads the research app's rv.sqlite; desk checks 2, 3 and 7 read the book db's price_history or our own tickers
metadata:
  type: project
---

User decision 2026-09-30: the app stops reading `../Commodity Dashboard/var/rv.sqlite` entirely; `engine.risk.commodity_history` now reads the book database's own `price_history` (Bloomberg PX_LAST / PX_VOLUME / OPEN_INT written by the pull's risk-history step; -1 = not given).

**Why:** one source of history, filled by the user's own Bloomberg pull; no second app on the Bloomberg PC to keep real.

**How to apply:**
- Desk check 2 now compares Bloomberg's OPEN_INT / PX_VOLUME with what the last pull stored (same source), so it cannot settle one- vs two-sided Chinese counting any more: that is the SHFE manual check only.
- Desk check 3's monthly LME tickers are our own `engine.lme.monthly_ticker` (book prompt months + next 3), never the research app's.
- Desk check 7 is "Price history on file" per held root; the pull fetches about two years, so the 2020 / 2022 replay dates are expected NOT to be reached: reported as a fact, not a WARN.
- Never reintroduce `candidates`, `research_source`, `COMMODITY_HISTORY_DB` or a path to the sibling app. See [[bloomberg-field-assumptions]].
