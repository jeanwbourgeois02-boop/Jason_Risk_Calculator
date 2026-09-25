---
name: open-spreads-positions-2026-09-25
description: Header "Open spreads" counts book_spreads' `positions` (one per distinct spread across trade dates), not `spreads`; sample book = 5 open positions vs 6 spreads
metadata:
  type: project
---

Since 2026-09-25 (Phase B, "One place per number") the header's Open spreads card counts
`book_spreads(...)["positions"]` with status 'open', not the per-trade-date `spreads` list.
The sample book (tests.golden_book.build_book, as-of 2026-09-18) has 6 open spreads but 5 open
positions: CL Z26/F27 was put on twice (910000001, 910000003). Hover label per position:
"<name> <direction> (N entries, traded d1, d2)".

**Why:** the Spreads tab's main table shows one row per position; the header must agree with it.
**How to apply:** any test stub of `engine.spreads.book_spreads` must return a `positions` key,
or the card reads 0. Related: [[commodity-strip-phase3-2026-09-24]], [[slim-header-2026-09-25]].
