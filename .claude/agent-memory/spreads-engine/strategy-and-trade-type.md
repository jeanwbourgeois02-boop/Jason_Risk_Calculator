---
name: strategy-and-trade-type
description: 2026-09-28 decisions on Jason's broker labels - trades.strategy is the position before every other rule, trade_type inference rules and the choices the brief left open (flat calendars, same-exchange products, outrights, LME/option months)
metadata:
  type: project
---

Jason's real export carries `PBRoot = JSHY10[.3|.4|.5]_<STRATEGY>` (.3 cross exchange, .4 cross
product, .5 term structure, plain = no type); the parser writes `trades.strategy` (the suffix),
`trades.trade_type` (the decimal's code) and `trades.pb_root` (raw). Built into engine/spreads
on 2026-09-28 (user decisions relayed by the housekeeper):

- **The strategy is the position**, kind `strategy`, `STRATEGY-<name>`, before bundles, pins and
  the finder; a trade with a strategy is never taken by anything else (the elif chain in
  `_Book.group`). Legs / P&L / leftover as a bundle; a level only when `best_cover` fits exactly.
- **Type per position** (`trade_type.py`): label wins when the labelled legs agree; disagreeing
  labels -> '' / `mixed labels`; else inferred from the open legs' roots; finder's kinds take the
  shape's type (calendar -> TERM_STRUCTURE, benchmark -> CROSS_EXCHANGE, processing /
  substitution -> CROSS_PRODUCT). A label the legs disagree with stands, note says what they look like.
- Choices I took (low stakes, say so if the user objects):
  - Roots that each net to ZERO lots over several months are calendars: several of them and
    nothing else -> TERM_STRUCTURE before the cross rules. **Why:** the brief's SCO1 example
    (HRC Oct/Nov beside iron ore Oct/Feb, Nov/Mar, labelled .3) must read "look like term
    structure"; by subsector alone it would read cross product.
  - Several roots of one subsector on one exchange (CBOT ZW vs KE) -> CROSS_PRODUCT, note says so.
  - Outright rows: their own label if any, else '' / '' / '' (no "inferred outright" there).
  - Notes name roots by root_id ('CME:HRC Oct26/Nov26') and products by subsector words.
  - An LME prompt spread or an option leg has no 'YYYY-MM' month key, so months are not
    counted for them (never a false term structure); LME:CA vs COMEX:HG still reads cross
    exchange from the roots.
- `positions_from(spreads, roots=None)` re-runs the type on the summed legs with the members'
  labels together; `book_spreads` passes its own roots. **How to apply:** an old database without
  the three columns reads them as '' (`_has_column` guards) and behaves as before.
See [[grouping-rule-decisions]].
