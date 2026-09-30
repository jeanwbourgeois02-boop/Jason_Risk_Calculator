---
name: book-headline-size-flags-2026-09-30
description: Book 2026-09-30 - no headline line above the card (Gross/Net + flag count on the total row, filtered MTD/YTD inline in the strip), one signed Size form, flags named in words, readable muted text (--muted #4b5563, --ink-2)
metadata:
  type: feedback
---

User, 2026-09-30, on the Book: "net usd and flags ... alone in this row its clunky", "too faint you cant
read anything", "the size column is cooked - looks horrible".

**Why:** a lone line of two figures above a card reads as a gap; grey #6b7280 / #8a919c / #9ca3af text at
10-12 px was unreadable; "30 : 4" / "2 lots" / dashes on options mixed three forms in one column.

**How to apply:**
- Never put a headline line between the header and a card for two or three figures: they go on the table's
  total row (Book: Gross · Net in the What-it-is cell, "9 flags · 2 red" in Flags). `book.headline` now
  returns only the filtered MTD / YTD pair (`tk-strip-figs`) or None; `HEADLINE_ID` is a Span inside
  `.tk-strip-lead` next to the title (ui_check still fills it by id).
- Size = `book.size_parts`: signed lots per side "+30 / −4" + grey "lots" suffix; outright or options the
  legs' lots (long / short sums); FX / metal forwards and FX options the notional in its base unit
  ("−100 oz", "+10.0m EUR"); several parts = the largest + a "+N" (rest on hover). CSV "Size" = `size_text`.
- Flags cell = `flag_name` of the most severe (`FLAG_NAMES` order, red first) + " +N"; column left-aligned.
- CSS kit: `--muted` #4b5563, new `--ink-2` #374151 for strip controls (switch labels, ghost buttons,
  CSV, Group label, headline labels), `.cell-unit` #5f6876, `.cell-missing` #6b7280, placeholders
  `--muted` opacity 1. Keep secondary text at or above 4.5:1; never go back to #8a919c / #9ca3af for text.
