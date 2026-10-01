---
name: level-precision-2026-10-01
description: Book spread levels at their legs' precision (book.level_decimals, level_text legs=); tonne prices under 1,000 get 2 decimals (1 in CNY) in formatting.price_decimals when a fill is passed
metadata:
  type: project
---

Bug 2026-10-01: SCO1's SGX iron ore calendar showed Entry "0" / Now "−1 $/t" (legs 96.5 / 92.5): `level_text` called `price_text(v, unit)` with no reference, and a USD/t floor was 0.

Fix:
- `formatting.price_decimals`: a "/t" price whose `fill` (the reference) is under 1,000 gets floor 2 (1 for CNY); copper / zinc / CNY tonnes in the thousands keep 0. Only when a fill is passed: with no fill (Data tab mark lists, `data_checks._signed` moves) it is unchanged, so an iron ore mark there still reads "96" (found, not done: would need the value as reference, which would give copper moves "35.00").
- `book.level_decimals(level, legs)`: for a calendar / price / premium level (or a one-root spec) the max of `price_decimals(unit, leg avg_fill or mark)` over the legs the level is built on (spec legs' / price_legs' instrument ids). `level_text(..., legs=)`, `_level_cell(legs=)`, `sigma_words(..., legs)`, `_part_level_td(..., legs)`, `part_level_words(sub, legs)` thread it; ratios and template-unit levels keep the unit's tick.
- An averaged fill (96.5229) shows 3 decimals (floor + 1), so the level does too: SCO1 entry "−0.195".

**Why:** the user wants the level read at the legs' precision, never coarser.
**How to apply:** any new level display goes through `level_text` with the trade's (or part's) legs.
