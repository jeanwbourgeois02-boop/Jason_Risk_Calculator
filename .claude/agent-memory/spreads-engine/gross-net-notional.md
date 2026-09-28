---
name: gross-net-notional
description: 2026-09-28 gross_usd / net_usd / notional_reason on every spread, position and outright of book_spreads - what they read, the choices taken (options blank the group, closed = 0), how verified
metadata:
  type: project
---

Added 2026-09-28 (user yes: the Book tab shows gross and net USD notional per position, as the
research app's Book does). `_Book.gross_net(tids)` in `engine/spreads/book.py`; keys on every
`spreads`, `positions` and `outrights` entry.

- The figure is the existing `notional(leg, lots)` helper's: open lots x `instruments.multiplier`
  x the leg's own `value_book` mark x its spot on as_of (OPEN rows only). Notional, not P&L: no
  new mark read. Gross = sum of |leg|, net = signed sum (long positive).
- Choices I took (low stakes, say so if the user objects):
  - A group with no open lots is 0 / 0 / '' (known zero), never None.
  - An open OPTION (FX_OPTION, CMDTY_OPTION, EQ_OPTION) inside a strategy / bundle / pin blanks
    the group's notional with a reason: lots x premium is a value, not a notional, and a partial
    sum is forbidden. FX_SPOT / FX_FWD / LME_FWD legs are summed by the same formula (quantity x
    1 x outright x spot = USD of the base amount; tonnes x USD/t). Only the finder's kinds and
    outrights are pure futures.
  - The legs are netted per contract over the whole group (`legs_of(per_group=False)`), so two
    open trades of one contract that cancel add nothing.
- The position merges members with `_sum_or_none`; reasons joined as `<spread_id>: <why>`.
- **How to apply / verify:** build the golden sample into a scratch db (`tests.golden_book.build_book`),
  call `book_spreads`; every open outright's net = lots x mult x mark x spot of its value_book row.
  `UPDATE marks SET value='abc'` on one FUTURE_PX makes the leg unpriced and the group None with
  its reason (see [[perf-book-spreads]] for why deleting a mark does not).
