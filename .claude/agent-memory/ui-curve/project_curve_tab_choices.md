---
name: project-curve-tab-choices
description: Judgement calls on the Curve tab (Phase 1 build and Phase 5 delta views, 2026-09-24) that the user has not yet confirmed; revisit if he comments on the tab
metadata:
  type: project
---

The Curve tab (Phase 1 step 3 of the commodity conversion, built 2026-09-24 on a synthetic book, before Jason's real blotter) made these unconfirmed calls:

- The sector table pins a "Book" line summing the sectors' engine net / gross USD (n/a if any sector is n/a). No book total of lots or units: they are not additive across commodities.
- The grid has no sector subtotal rows: Sector is the first column and the engine's order groups it, because subtotal rows would break native column sorting (every table must rank).
- A contract with no contract month from contract-master counts in the totals but in no month column; the grid's Note column names it.

Phase 5 (same day, options on futures, LME prompts, averaging contracts) added:

- The switch has five views: lots / units / USD notional (futures + LME only, as the engine gives them) and delta lots / delta USD (every product). The end columns follow the view family: outright views end with net/gross lots, units, USD; delta views end with net delta lots, net/gross delta USD. (Phase 1 had kept the end columns fixed whatever the switch.)
- In the outright views a month holding only options is left blank with a tooltip naming the options ("see the delta views"), not n/a: nothing is missing there.
- The sector table and Book line gained net/gross delta USD only, not delta lots (lots of different commodities do not add up, same reasoning as above), though the engine gives sector net_delta_lots.
- An option's notional cells read "option: see delta" (added to the detail table's sort_as_null so it ranks last).

Screens redesign Phase A (2026-09-25, Opus 5.5 stand-in for Fable) added:

- "More columns" on the Contracts table is Dash's native `hideable` + `hidden_columns` (its built-in "Toggle Columns" button, choice persisted via `persisted_props`), not a checklist: no new callback input, so the shell's callback-input test for Curve stays unchanged. The hidden figures are also on hover of the visible cells.
- The Contracts table keeps full figures (it is per-contract rows, like trade rows); only the grid's USD columns and the sector table went to k / m. The currency-exposure table also kept full figures.
- Grid Notes markers: "options", "avg", "no USD N", "no delta N", "no month N", joined by a middle dot; sentences on hover.
- Visible Contracts columns keep Unit and Ccy (narrow) beside the brief's list: units and price are meaningless without them.

**Why:** the housekeeper's briefs fixed the sections but not these details; each is reversible.
**How to apply:** if the user asks for subtotals, a book lot total or a different switch behaviour, these were defaults, not his decisions; change freely. If he confirms one, record it as feedback.
