---
name: feedback-strategy-means-spread-type
description: On the Book, "strategy" = the spread type (cross exchange / cross product / term structure / unassigned) as in the research app; Jason's PBRoot name (COPAR3, CATTLE) is his "trade"; never call a PBRoot name a strategy
metadata:
  type: feedback
---

On every Book label, hover and CSV header: **strategy** = the spread type (Cross exchange, Cross
product, Term structure, Unassigned for a plain JSHY10 label), **trade** = Jason's PBRoot name
(COPAR3, ZNA1, SCO1, SILARB1, STEEL, CATTLE; `trades.strategy`). A blotter row is a "fill" where
the word "trade" would otherwise be ambiguous ("6 open of 25 fills on file").

**Why:** the user's shouted correction of 2026-09-28: the research app's Book (`../Commodity
Dashboard/rvapp/app/app.py`, "By spread type") is what he means by "P&L by strategy"; the Book
had been calling each PBRoot name a strategy, which read as nonsense to him.

**How to apply:** the code keeps `strategy` as the *field* name of the PBRoot label
(spreads-engine's kind `strategy`, `trades.strategy`, `GROUP_STRATEGY`, `strategy_positions`);
only the words on screen changed. A position's strategy is the engine's per-position type
(`trade_types`: the broker's label first, inference only where none), so SCO1, labelled cross
exchange with calendar legs, sits under Cross exchange with the engine's "legs look like term
structure" note on hover, not under Term structure. The Trades tab still has columns "Strategy"
(PBRoot) and "Type": not renamed yet (out of the 2026-09-28 brief; a candidate for the next pass).
