---
name: rolls
description: 2026-09-29 engine/spreads/rolls.py roll detection from fills - position per (account, trade name, root), trade-then-pooled matching, roll-ins kept and labelled, what Jason's real export holds
metadata:
  type: project
---

Built 2026-09-29 (user: roll carry "a big deal here"). `rolls(conn, as_of, spreads=None)`, fills only,
conversion = `valuation.usd_per_quote_on_or_before` of the trade date.

- **Position is per (account, trade name = trades.strategy, root)**, not book-wide. **Why:** on Jason's
  export SCO1 opened HRC V/X on 2026-09-23 while STEEL held the reverse; book-wide it read as a roll-in.
- The export books one contract per row: no single calendar-spread fill exists to read.
- Matching: trade vs trade, unique both ways; then the day's leftover fills pooled per contract and side.
  Lots rolled = min(two sides) capped at what was held in Mi before the date (CATTLE FC: 120-lot trade,
  91 held -> 91).
- **Roll-ins kept, labelled `roll_kind='in'`** (brief said later month only). On the real book (as of
  2026-09-29) the only rolls: CATTLE FC Nov->Oct long 91 (-261k), CATTLE LE Dec->Oct short 112 (-97k),
  STEEL HRC Nov->Dec short 150 (+24k). Totals split `total_out_usd` / `total_in_usd` so the ui can drop
  roll-ins if the user says they are not rolls. Synthetic sample: no rolls.
See [[grouping-rule-decisions]], [[scorecard-and-carry]].
