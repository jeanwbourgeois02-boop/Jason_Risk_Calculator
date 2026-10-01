---
name: book-z-entry-now-sectors-2026-10-01
description: Book z entry | z now (z exit in the Closed fold) from trade_risk's spread_z, Quantity = engine size_sides.text, sector order (trade_filter.SECTOR_ORDER) for both views and the Summary's Sector switch, "2 calendars" / "Unmatched legs" in What it is; width CSS and proof recipe
metadata:
  type: project
---

User decisions 2026-10-01 ("yes to those decisions"), built on spreads-engine's `size_sides` / split
sub_spreads / UNMATCHED parts and risk-metrics' per-spread z.

- z: `risk["spread_z"][position_id]` = {trade_level, spreads[i] (i = index in t["sub_spreads"])}.
  `book.z_cell(zd, point, ready, why, extra, level, legs)`; trade row `trade_z_cells` (closed: z now
  column shows z_exit), part rows `part_z_cells`; a trade with no single level (SCO1) gets one
  "3 spreads" cell spanning Entry, Now and both z columns. Sort/filter keys `z_entry` / `z_now`
  (`trade_z`), `sort_rows(..., zmap)`, `filter_value(..., zmap)`. Closed-fold row carries grey
  "z entry" / "z exit" cells over the z columns.
- Part head rows now always show their own level + z + `sub_qty_td` (size_sides); `part_name` words
  UNMATCHED as "Unmatched legs: ...". A leg shared by two calendars (STEEL Dec26) is listed once, under
  the first; the second part row's P&L sums only its own listed legs (left as it was).
- What it is: groups of sub_spreads by root set (+ each UNMATCHED part); the largest leads; "2 calendars"
  / "2 month pairs" for several on the same roots; "+ N more spread" counts spreads on other roots only;
  "Unmatched legs" (capital: ui-check LOWERCASE_PART after " · ") / "+ unmatched legs". `_COUNT_PART`
  decides what `_with_words` puts after " · " vs after the comma.
- Sector: `tf.root_sector`, `leg_sector`, `trade_sector` (larger leg by |value_usd|, hedges out),
  `sector_rank`; Chemicals after Energy, Freight after Livestock (not in the user's list; low-stakes call).
  `tf.default_order` = sector, commodity, name; book_contracts rows carry `sector`. Summary value "family"
  kept, label "Sector"; Sector/Commodity breakdowns in sector order, Spread type still by |LTD|.
- CSS (end of style.css): Quantity clip 185 px; at 1401-1999 px What it is 230/220 and cell padding 6 px —
  the 14-column Book fits 1680 px with the Check column showing.

**Why:** the user wants each spread's z at entry / now / exit and sizes in the spread's own unit.
**How to apply:** proof scratch `ui1001/` (session scratchpad): copy risk-metrics' rm_z.db (real export +
random-walk price_history), `marks.py` adds made-up marks, `render.py <db> <as_of> [1=fold open] [contract]`
prints rows with hovers, `chk.py <db> <tab> [--shots]` runs ui-check's rules (and the 1680 shot with
`.venv` python) on a book WITH history. See [[book-four-questions-2026-09-30]], [[book-legs-inline-summary-2026-09-30]].
