---
name: book-spread-rows-2026-09-29
description: The Book's spread rows after spreads-engine sized by value (1e80d61): pair_name / pair_ratio / pair_leftover, Trade and Symbol columns + filters, Needs you card gone; traps (legs > 2, sizes in USD, heredoc backslashes)
metadata:
  type: project
---

2026-09-29, user on the Book: "needs you - kind of useless"; "the pbroot and symbol - thats the kind of stuff
its good to be able to sort by"; spreads with their ratio ("feeder and live cattle ... cross product").

- spreads-engine pairs can hold **more than two legs** (one spread per trade name across months, each leg
  `side` A/B) and `size` / `residual_units` are in the **sizing unit (USD for value)**: never show them in a
  lots column. Pair row: Net lots = `sides[i].net_lots` "+91 / −167", Gross = Σ|legs.lots|, Ratio = the
  bracket of `ratio_text` ("1 : 1.84") with values / balance / weight on hover (`book.pair_ratio`), amber
  "unbalanced" only when `balanced is False` (on the Ratio cell, not Net USD), leftover from `leftover_lots`
  (`book.pair_leftover`) as a muted span in the Name cell. Leg rows show engine lots (LME in lots too).
- Names: `formatting.pair_name(pair, roots)` → (name, months): calendar "HRC Oct26/Nov26", 2-leg "A / B",
  >2 legs "Feeder / Live cattle" + months "Oct26, Nov26 / Oct26, Dec26" (the node's small sub). The Blotter's
  `spread_of_trades` uses it too. The PBRoot name is no longer the small grey sub (it has the Trade column).
- Trade / Symbol: `book._labels` reads `broker_symbol` (fallback query without it); `identity_values` gives
  the cells; filters `book-f-trade`, `book-f-symbol` (choices incl. "No trade name", "(not recorded)").
  data/raw/risk.db has no broker_symbol yet (loaded before the column): every Symbol is a dash until re-upload.
- Needs you card and the Book's liquidity read are gone; MARKS/PAIRS stay first in the drawer (`need_notes`).
- CATTLE's entry shows 112.659 (level_decimals: cwt = 3 decimals, the tick rule), not 112.66.

**Why:** spreads are the unit of an RV book; the boss sorts and filters by PBRoot and symbol.
**How to apply:** any new reader of pairs loops over `legs` / `sides`, never `legs[0]` / `legs[1]`. In Git Bash
heredocs `\\n` inside Python strings arrived as a real newline: write edit scripts with the Write tool.

**Header filters (same day, user: "why are the filters just floating above the table"):** each filterable
`th` holds `.book-th` = the title span (now the `{"type": book-sort}` id, so a funnel click never sorts) and a
`<details class="book-pop">` whose summary `book-funnel-<col>` is the funnel; `ui/assets/book_filters.js`
(DOM only) keeps one open, closes on outside click / Escape, focuses the box and narrows a checklist by its
search. Categorical filters are `dcc.Checklist` (same `book-f-<key>` ids and `options`/`value` props as the
old dropdowns); the Commodity list sits in the Name column's panel. One clientside callback paints the funnels
gold with the summary on hover (`_funnel_js`). The strip is one line. The open head gets `z-index: 8` via
`:has(.book-pop[open])` to sit above the sticky Book row; the popover position is unverified in a browser.
