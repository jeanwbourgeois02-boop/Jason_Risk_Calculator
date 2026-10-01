---
name: book-trade-table-three-levels-2026-10-01
description: Book trade table of 2026-10-01 — trade / spread rows / legs behind a client-side caret, Size "a side" rule, Spread at entry/now with a build sentence, one Action column, funnels on text columns only, strip labels sentence case app-wide
metadata:
  type: project
---

User approved 2026-10-01 ("lets make these improvements straight away"); replaces the 2026-09-30 "legs always
showing" rule and the Next date / Check columns.

- Columns: Trade | What it is | Size | Spread at entry | Spread now | Z at entry | Z now | P&L today | Open |
  Locked in | P&L since entry | Action (key "action"; "next"/"flags" gone; CSV columns unchanged).
- `is_multi(t)` (open, >1 sub_spreads with legs): trade row blank Size/levels/z (plain "" Td, never a dash),
  What it is = `spreads_summary` ("3 calendar spreads: Iron Ore and HRC", "2 Zinc spreads, SHFE vs LME").
  One spread or an outright: the trade row carries size, level, z.
- Legs: `leg_row` carries `data-legs-of=<trade>`; CSS `#book-table tr.tk-leg-row {display:none}`. The caret
  (`legs_caret`, id {"type": LEGS_TYPE, "idx": name}, `data-caret-of`) has NO callback: book_filters.js takes
  the click in document capture phase (stopPropagation, so the row's n_clicks/panel never fires) and writes
  a <style id="book-legs-style"> of open trades (sessionStorage), so re-renders keep it. Caret text stays "▸",
  rotated by CSS — ui_check's open shot clicks each `.tk-chev` once via closest('[id]') = the caret.
- Hedges: tag "FX hedge", class tk-leg-row--top (spread-level indent) in multi trades.
- Size: `size_display(size_sides)`: equal → "200 lots a side"; gap = |L−S| / min(L,S) ≤ 10 % → "≈ avg a
  side"; beyond → amber "Long 386 t / short 350 t" + Action "Sides off by 10 %" (`sides_off`, per spread on
  multi). Engine gives size_sides "nothing open" for FX forwards/options: one open leg → leg_qty_words.
- `level_words(level, legs)`: sentence from level.spec legs/weights ("A minus B, in $/st", "A in USD at spot
  divided by B, a ratio", "The price of X, in CNY/kg"), first line of both level hovers and spread rows.
- `action_items(t, as_of)` (SEV_RED/AMBER/GREY): checks + `date_action` (engine level RED/AMBER only; estimated
  → grey "≈ Last trade 30 Nov") + sides off; total row "N to act on"; summary count uses it too.
- Funnels only Trade, What it is, Action (OWN_FUNNELS); book_contracts LIST_FILTERS family/clearer; stale
  number comparisons in a session are ignored.
- CSS: `.tk-strip .tk-title` and `.tk-strip .tk-k` no uppercase on every tab. Panel "HEDGE"/"LEVEL SINCE..."
  (.tk-hedge-line .tk-k, .book-h) still uppercase — not asked.

**Why:** the user found the table hard to follow; one shape per trade, three levels.
**How to apply:** proof = scratch `shot.py <db> <as_of> <prefix> TRADE,...` with `.venv/Scripts/python.exe`
(playwright lives there, not py -3): counts panels and visible leg rows after caret clicks. Heredoc trap again:
a `\\` in a bash heredoc JS regex became `\` — write JS with Edit. See [[book-z-entry-now-sectors-2026-10-01]].
