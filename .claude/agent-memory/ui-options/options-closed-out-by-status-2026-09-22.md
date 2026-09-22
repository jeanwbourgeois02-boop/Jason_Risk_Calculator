---
name: options-closed-out-by-status-2026-09-22
description: The Options sub-tab tells a closed-out option by the book's status CLOSED (never by its marks or a zero value); by pair / ladder / structure / in-play tables are live rows only, the Total and Portfolio Totals keep the closed P&L; how a test builds a closed-out pair
metadata:
  type: project
---

User, 2026-09-22: "In the options tab, the by pair, where the risk is, I only want to see live
options, no need show closed out options." Replaced the 2026-09-21 heuristic (a group whose
value summed to ~0 was "closed" and dropped), which kept a pair that also had live options and
folded the closed legs' premium, value and Greeks into it.

**Why:** the pricer skips closed-out trades from that day on, so they have NO PREMIUM / Greek
marks on the date valued; the only reliable tell is `value_book`'s status `CLOSED`
(`engine/pnl/valuation.py::closed_out_from_rows`, same terms under two instrument ids,
quantities netting to zero, all terms on file). A stale mark left on file from before must not
show as risk either.

**How to apply:** `_leg_row` reads `book["status"] == "CLOSED"` -> `closed_out` on the leg,
premium = the book's own `mark` (the closing fill, so MktVal - Start = the book's P&L), Greeks
blanked, note = the book's note ("closed out <d>: bought and sold back in full ..."), and it
uses the book's spot (the close-out date's). `_row` carries `closed_count`; `_live(legs)` is
the mask; `grouped_rows` groups live legs, Total over all legs with `closed` count
("Total (incl. 2 closed out)"); `in_play_rows` uses the same mask (the old net-position
guess is gone); `_fmt_label` appends "(closed out)" when every trade of a row is closed.
The trade summary still lists the closed-out group (buy and sell fills show the P&L).

**Test recipe** (`_add_closed_out_pair`): two `_insert_option_leg` calls with the same terms,
a strike different from the live legs', opposite quantities, `UPDATE trades SET trade_date` on
the sell-back, and an official SPOT on or before the close-out date (`last_usd_conversion`),
`marks=False`. The flat frame turns None into NaN: assert with `options._is_missing`, not
`is None`.
