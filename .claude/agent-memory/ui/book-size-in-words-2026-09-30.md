---
name: book-size-in-words-2026-09-30
description: Book Size column deleted 2026-09-30; the lots are said in words in What it is ("Short 30 SHFE copper vs long 4 COMEX copper, Nov/Dec26"), the Size hover moved onto What it is; rules of the sentence builder
metadata:
  type: feedback
---

User, 2026-09-30, third time asking: "this shitty, clunky lot this is still around", "the size column is
cooked". Bare signed numbers ("−30 / +4") in their own column cannot be read without hovering: which
number is which leg? Never bring back a lots-only column; say the size in words where the contract is named.

**Why:** a size means nothing apart from the contract it belongs to; one sentence reads at a glance.

**How to apply (ui/tabs/book.py):**
- `what_parts(t, roots)` -> (sentence, other parts); `_gather` copies each trade (`_with_words`, never
  edits the shared dicts) with `what_words` / `what_others`; `trade_filter._search_blob` reads
  `what_words`, so the search finds "short", "long", "calls". CSV "What it is" and the sort use
  `what_line` (sentence + hedge words); CSV "Size" still `size_text`.
- Sides: the engine's `size.sides` first side first (its roots, or its sign when both sides are one
  root); a several-commodity side against one (crack, crush) goes second so the input reads first.
  Largest first inside a side; sign word only when it changes.
- Several roots: lots summed per (side, root, option type), months once at the end ("Nov/Dec26",
  "Dec26/Jan27", "Oct26–Jan27"). One root: per contract, month inline (calendar "Long 15 NYMEX WTI
  Dec26 vs short 15 Jan27"), or at the end when all share one month and no option ("Long 2 COMEX gold, Dec26").
- Exchange said when it changes, the name when the root changes; commodity lower-cased only where
  `config/contracts.csv`'s name writes it lower ("gold", "heating oil"; "WTI", "Brent" stay).
- Options: the strike only when needed (one option, or two of the same type); "calls"/"puts".
  FX / metal: "Long 10,000,000 EUR EURUSD 18 Nov26 1.1800 call", "Short 100 oz XAUUSD 16 Dec26 forward".
- "+ N more" for extra sub-spreads, "+ N not recognised" for UNRECOGNISED legs, the rest under "Also held:"
  on hover. Closed, pseudo and all-unrecognised rows keep `what_text` (the engine's words).
- Grey tail = `hedge_words`: coverage figure, else the engine's ", JPY hedged" / "GBP/USD hedged".
- Hover = sentence, level note, Also held, then `size_hover` (legs' lots, value per side, balance, USD per
  move, hedge %), then legs not already listed (hedges, closed).
- Column count 11; group and closed-fold rows colSpan 7. CSS `.tk-what` 530 / clip 510 / with-tail 400
  at 1680 (every sample row whole), 720 at >= 2000, 280 at <= 1400, 210 at <= 1200.
