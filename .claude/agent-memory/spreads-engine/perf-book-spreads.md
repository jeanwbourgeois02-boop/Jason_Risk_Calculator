---
name: perf-book-spreads
description: 2026-09-28 speed-up of book_spreads (0.74 s -> 0.13 s cold): why the reference close cannot be resolved once per book, the shortcut's invariant, how identity was proved, and what makes a trade unpriced in a test
metadata:
  type: project
---

Performance pass 2026-09-28 (user: the Book tab's 14 s first render; output identical, no P&L change).

- The period reference close DEPENDS ON THE SPREAD: `resolve_reference` decides the step back
  (REFERENCE_MISSING) and the per-trade fill on the counts within the scope it is given, so a spread
  with a leg unpriced on T-1 steps back where the book does not. Never hoist it to one call per book.
  **Why:** the housekeeper's brief assumed it was spread-independent; it is not.
- What IS spread-independent is the per-date data: `_Frames.records(day)` / `unpriced(day)` are read
  once per date. Shortcut in `_period_pnl`: when no trade of the group is in `unpriced(ref)`,
  `diff_split` has nothing blocked (all of the group is priced on as_of by then, or `_period_pnl`
  returned early), so `resolve_reference` returns the close untouched with no note; the figure is
  taken from the cached rows. Every other case still calls pnl-series' `resolve_reference` unchanged.
  **How to apply:** if pnl-series ever changes what an all-priced reference means, this shortcut must
  follow; say so in the Handoff if that lane's rule moves.
- Proof method that worked: pickle `book_spreads` on the golden sample before, compare every key
  with exact floats after; plus old-vs-new module on forced scenarios. Deleting marks does NOT make a
  trade unpriced (hard rule 2 estimates from the near marks); a non-numeric stored value
  (`UPDATE marks SET value='abc'`) does, per date, and is how to force step back / not found / fill.
- `yaml.CSafeLoader` (present in this PyYAML 6.0.3 build) parses `config/spreads/*.yaml` 7x faster
  and identically; `templates._LOADER` falls back to SafeLoader where the C extension is absent.
- Measured on the sample at 2026-09-28: cold 0.743 s -> 0.125 s, warm 0.288 s -> 0.057 s; what is
  left is 5 `value_book` calls (pnl-valuation's) and the levels' SQL reads.
