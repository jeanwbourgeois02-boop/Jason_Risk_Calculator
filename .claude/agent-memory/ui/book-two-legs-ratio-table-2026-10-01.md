---
name: book-two-legs-ratio-table-2026-10-01
description: Book By trade rebuilt again 2026-10-01 (user: "this is shit - its cooked"): 13 cols Trade|Qty|Entry price|Price now|Spread/Ratio at entry/now|Usual ratio|Z|P&L, legs always showing, no caret, action chip after the name; supersedes book-trade-table-three-levels
metadata:
  type: project
---

User 2026-10-01: "surely you want both legs of the trade there - two rows - each one - price, qty etc /
their usual ratio, z score - this at entry and now - the spread - and then the pnl"; ratio = "price ratio -
in the same currency - always usd - with the chinese [leg] as numerator".

- `trade_rows` -> `trade_blocks(t)` (shape by `trade_shape`: pseudo | multi | outright | spread) -> `trade_row`,
  `spread_row` (id {"type": SPREAD_ROW_TYPE, "idx": "<trade>|<n>"}, click opens the trade's panel via `_toggle`),
  `leg_line` (depth 1 under a spread). An outright's one leg sits on the trade row, never repeated.
- Engine keys read: spreads-engine `spread_usd_entry/now/unit/reason`, `ratio_entry/now/basis/reason/
  estimate_note` (on subs and on a one-spread trade: `spread_source`); risk-metrics `z_basis`, `z_basis_reason`,
  `ratio_usual`, `ratio_usual_window` (zone = trade_level or `sub_zone`).
- Spread falls back to the old level (`_fallback_level`) for 3+ legs, option structures, closed trades; a
  2-leg pair without spread_usd -> em dash with reason. Spread decimals: calendar = legs' precision; else
  price_decimals(unit, round(a leg's mark in the same physical unit)).
- Labels: `spread_label` ("COMEX vs LME Copper Dec26", calendar = engine name, 3+ legs / options = position
  words); multi = `spreads_summary`; closed = `what_text`.
- Open / Locked in only on P&L since entry hover (`_split_line`) and in panel_facts. `action_chip` replaces the
  Action column; total row "N to act on" in the Trade cell. Funnel only on Trade ("trade", "type").
- CSV: one row per trade/spread/leg (Row column), plain numbers, + Open / Locked in.
- The caret JS left book_filters.js; dead helpers removed by an AST "unused" sweep (see recipe in session).
- Proof: scratch `sums.py <db> <as_of>` checks legs+spreads = trade (0 mismatches), totals = header, CSV, every
  column's sort; `shot.py` / `shot2.py` (.venv python) shoot + click a trade and a spread row + console.
