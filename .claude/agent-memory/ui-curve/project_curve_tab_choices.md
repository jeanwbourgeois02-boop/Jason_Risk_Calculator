---
name: project-curve-tab-choices
description: Judgement calls made when the Curve tab was first built (2026-09-24) that the user has not yet confirmed; revisit if he comments on the tab
metadata:
  type: project
---

The Curve tab (Phase 1 step 3 of the commodity conversion, built 2026-09-24 on a synthetic book, before Jason's real blotter) made these unconfirmed calls:

- The sector table pins a "Book" line summing the sectors' engine net / gross USD (n/a if any sector is n/a). No book total of lots or units: they are not additive across commodities.
- The grid has no sector subtotal rows: Sector is the first column and the engine's order groups it, because subtotal rows would break native column sorting (every table must rank).
- The unit switch (lots / physical units / USD) only changes the month cells; the end columns (net/gross lots, net units, net/gross USD) are always the engine's per-commodity totals.
- A contract with no contract month from contract-master counts in the totals but in no month column; the grid's Note column names it.

**Why:** the housekeeper's brief fixed the sections but not these details; each is reversible.
**How to apply:** if the user asks for subtotals, a book lot total or a different switch behaviour, these were defaults, not his decisions; change freely. If he confirms one, record it as feedback.
