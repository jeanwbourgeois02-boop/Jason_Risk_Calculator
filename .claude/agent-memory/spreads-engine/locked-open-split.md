---
name: locked-open-split
description: 2026-09-30 LTD split into locked in / open per Book trade and leg (trades.py _split_leg, _split_trade, _unwound): rule, checks, what the sample shows, non-USD locked moves with spot
metadata:
  type: project
---
User yes under hard rule 7 (2026-09-30, "these changes sound good apply them"): each trade's LTD split into
`pnl_open` (open net x mult x (mark - avg entry) x S) and `pnl_locked` (= LTD - open), keys on trade rows and legs,
plus `pnl_split_reason`, `unwound`. The ui lane built against these exact names.

- Avg entry = `strategies._entry_value(open_trade_ids, quoted=True)` per LEG (one contract): a roll is a reduction of
  the old month's leg (locked) and an add to the new one, not carried (the carry-over only happens within one leg set).
- Safety check: every open value_book row must equal lots x mult x (mark - fill) x spot on its own mark/spot, marks and
  spots equal across the leg's open rows; else None + reason (a per-ounce metal FX option fails it on purpose).
  Mult 1 for FX / LME / FX_OPTION (value_book's FX rule), trades multiplier otherwise.
- Locked = -sum(q x fill) + Q x avg, times today's S: independent of the mark, zero on a never-reduced leg (no FX
  remainder, since value_book converts every row at the as-of spot), but a non-USD leg's locked figure moves with the
  day's spot (it is still inside open LTD, re-converted daily by convention).
- Trade: open = legs summed; locked = trade LTD - open. Any leg None (unrecognised) -> trade None with reasons.
- Settled FX spot / forward hedges and expired options count as locked in and set `unwound` True.
- Verified on the sample (in-memory `tests.golden_book.build_book`) plus injected half-reductions on USD, CNY, JPY, GBP,
  LME and FX-forward legs: locked = reduced lots x (exit - avg) x mult x S to the cent.

**2026-10-02** ([[trade-structures]]): the average is now the spec's 7.1 (`structures.avg_cost`, trade date then trade id) per (structure, contract); `_split_leg` only for legs with a fill outside every structure.
