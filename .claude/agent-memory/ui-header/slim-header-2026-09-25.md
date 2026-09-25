---
name: slim-header-2026-09-25
description: Screens redesign Phase A slim header - card shape other lanes' tests depend on, markers as 3rd child in a grid, Data chip memo keyed on book_today, width budget at 1680 px
metadata:
  type: project
---

Phase A of CLAUDE.md "Screens redesign plan" (user, 2026-09-25) made the header one row:
seven P&L figures in `short_money` ("−$51.0k", full figure on the value's hover), Trades small
(open / settled on hover), divider, Gross notional, Net outright (sectors on hover), Open spreads
("review N"), Next expiry chip ("CUX26 last trade · 8 bd", "est." marker), Data chip
("31 marks missing"). FX Net / Gross USD delta left for the FX & cash tab. Toggle reads "LTD chart".

**Why the card shape is what it is:** other lanes' tests (test_ui.py, test_ui_blotter.py) walk
`_build_figures` output as `card.children[0].children` = title string, `children[1].children` =
value string, and `len(children) == 2` meaning "no caption". So markers are a third child
(`html.Span` of `formatting.marker` spans), and the card is an inline-style 2-column grid
(title spans both columns) so markers sit beside the value on one line. Don't put markers
inside the title or value div: it breaks those tests.

**How to apply:**
- Visible sentences are gone: n/a has its reason on hover; "excl. N", "filled N", "ref 16 Sep"
  carry the sentence on hover. `_priced_single` / `_priced_diff` dicts are unchanged (test_pnl
  pins `_priced_diff`); the excl count is parsed from `excluded_summary`.
- `_needs_cached` memoises `needed_marks` on (db path, mtime, as_of, book_today) — book_today
  is in the key because it picks the past-close vs live needs list (tests monkeypatch it).
  `needed_marks` itself stays un-memoised (market_data and tests call it directly).
- Width budget on the golden book at 1680 px: ~1340 px of ~1630 available. Seven P&L cards
  each with a marker would push past it and wrap to a second row (graceful, not silent).
- Warm render on the golden book went 0.50 s -> 0.27 s (as-of needs read once).
- Related: [[commodity-strip-phase3-2026-09-24]], [[header-visible-reasons-and-trade-counts-2026-09-17]]
  (its "visible caption" rule is superseded by the markers).
