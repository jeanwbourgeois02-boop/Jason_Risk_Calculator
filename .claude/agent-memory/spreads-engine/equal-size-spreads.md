---
name: equal-size-spreads
description: 2026-10-01 user rule - a part of 3+ legs is cut into equal-size spreads (trades._split_parts), unmatched remainder, size_sides units, Title Case names, closed spec/sub_spreads; what the real book became
metadata:
  type: project
---
User decisions 2026-10-01 ("yes to those decisions"), built in engine/spreads/trades.py (+ strategies._pair_all).

- **Equal size first**: a part of 3+ legs splits into 2-leg spreads, long against short of equal size (lots
  within 5 % on one root; shared physical unit within 10 % for one commodity across exchanges / cracks; USD at
  the fill within 10 % between commodities). Then one leg left on one side is split across the other side
  (unique, so not a guess: STEEL V+175 X+25 Z-200 -> Oct/Dec 175 + Nov/Dec 25). Anything else -> one sub of type
  UNMATCHED, blank level + reason; the trade's type uses `of_kind`, the trade level goes blank when any is unmatched.
- **Kept whole on purpose**: CATTLE (one_spread rule, value side against side), template-covered parts (crack,
  crush), cross parts where no piece is found (pairs' own level). **Why:** the brief said keep what already pairs.
- strategies' term-structure pairing also takes equal lots first: SCO1 pairs are now Oct/Feb 2521 + Nov/Mar 1000
  (supersedes the Nov/Feb 1000, Oct/Feb 1521, Oct/Mar 1000 of [[strategies-pairs]]).
- Trade level when every real spread is one kind on the same roots: the largest (ZNA1 month pairs, STEEL).
- `size_sides` {basis, unit, long, short (>= 0), long_label, short_label, text, reason}; money text in full figures
  ("$15,094,650 v $14,928,200"), not the m form of the brief, per the CLAUDE.md full-figures rule.
- Each level carries `legs` and `entry_date` (risk-metrics computes z per spread from it); `closed` carries `spec`
  and `sub_spreads` (each with `exit_date`).
- Title Case: commodity_words, leg names, part / what words ("Calendar", "Options"), flag labels and sentences
  start capital; link words stay lower (same set as ui formatting.title_name). family_of stays lower (a key).
- **Entry-level gap fixed 2026-10-01** (approved, display only): `book.entry_level` now averages each leg by
  `strategies._entry_value` (open lots' average cost; all trades when none open), a converted leg through a
  `_FillView` of its fills in the spread's unit, so Entry agrees with the leg rows' avg_fill. Real book moved:
  STEEL Nov/Dec 130 -> -8, COPAR3 62.88 -> 65.00 $/t; CATTLE did not (its level is the Oct/Oct pair, one fill a
  leg). `levels.py` line 39 docstring still says lots-weighted (not edited: brief was book.py only).

**How to apply:** verify on the real export loaded into a scratch DB with made-up marks (load.py / marks.py /
run.py pattern); force UNMATCHED by making a root net to zero with 2 unequal legs each way.
