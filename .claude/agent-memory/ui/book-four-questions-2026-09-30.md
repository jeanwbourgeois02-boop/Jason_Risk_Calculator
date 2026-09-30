---
name: book-four-questions-2026-09-30
description: Book By trade rebuilt around the trader's four morning questions (12 cols, Open / Locked in from the engine, Type/Entry date/Move in the panel facts, empty columns hidden with a tfoot line); supersedes the 13-col layout of book-one-line-table-2026-09-30
metadata:
  type: feedback
---

User, 2026-09-30 (via the housekeeper): keep only columns that answer one of four questions in a
sentence: what and how big, where in and where now, making money, anything coming or broken.
Trade | What it is | Quantity | Entry level | Level now | z | P&L today | Open | Locked in |
P&L since entry | Next date | Check.

**Why:** the 13-column row read as clutter; Type, Entry date and Move answer none of the four.

**How to apply (ui/tabs/book.py):**
- Open / Locked in = spreads-engine's `pnl_open` / `pnl_locked` / `pnl_split_reason` / `unwound` on
  trade rows and legs (`split_value`, `trade_split`: .get(), "Split not available yet" when the key is
  absent). Closed trade: Locked in = its P&L since entry, Open blank. Locked in hover adds "In USD at
  today's rate ..." on a non-USD trade/leg (reviewer). Round to the cent (`_cents`) before colouring:
  the engine's never-reduced 0.0 and sums' -2e-10 must show a plain 0.
- Type / Entry date / Move today moved to `panel_facts(t, data, r, ready, check)`; the Type filter lives
  in the What it is funnel (`LIST_FUNNELS["what"] = ("commodity", "type")`).
- `hidden_columns(data, risk)` (all trades, never the filtered rows): z, open+locked, next, flags;
  `columns(hidden)`; own_cols/view_filtered take `hidden` (store keeps the filter, ignored while hidden);
  a sort on a hidden key falls back to default. The "why" line is a `tfoot` row (`tk-hidden-note`):
  a Div under the table would be a LOOSE_BLOCK in ui-check. Text through `cap_parts` (ui-check flags a
  lowercase part after ": ").
- Legs table: P&L today | Open | Locked in | P&L since entry; part rows sum the legs' split.
- CSS at the end of style.css: What it is 320/310 px for 1401-1999 px; fits 1680 with no scroll.
