---
name: book-one-line-table-2026-09-30
description: Book By trade = one line per trade (research app's Open trades table): 12 columns, z on Level now's hover, Check column = actions only, no Gross/Net, no Group/Expand, CSV link; fits 1680 px
metadata:
  type: feedback
---

User, 2026-09-30 (via the housekeeper; supersedes the two-line What of book-what-two-lines-chips-2026-09-30
for the Book only, Risk still uses `book._what_td` two lines):
Trade | Type | What it is | Quantity | Entry date | Entry level | Level now | Move | P&L today | P&L since entry |
Next date | Check. No second line under any cell. Headings in words (P&L today / P&L since entry / Price now
on By contract).

**Why:** the two-line cells and the flags read as clutter; modelled on ../Commodity Dashboard rvapp
`_trades_table`.

**How to apply (ui/tabs/book.py):**
- `_with_words` stores `what_one` (line 1 + ", <cross months>" + " · + N more spread") and `what_qty`
  (line 2's sizes); "COMEX leg closed" = `what_closed`, hover only. Hedge coverage lives in the panel's
  hedge line only.
- Entry level / Level now: unit slot on both ("ratio" for a ratio); leg fills / marks (`leg_price_lines`)
  and z (`z_words`) on hover. No z column (COLUMNS has no "z"; `columns(show_z)` ignores the flag;
  `own_cols` drops stale filter keys not in COLUMNS).
- Check (key still "flags"): `check_items` = flags in CHECK_CODES (unrecognised, leg_without_price,
  no_trade, price_check) + `hedge_problem` ("Hedge wrong way / too big / left alone"). Unbalanced and type
  mismatch only in the panel flags line, the Type hover and the Blotter's amber "Type differs"
  (`blotter_fills` frame column `type_differs`). Total row "N to check · M red".
- Strip: "book" in `trade_filter.NO_GROUP_TABS`, table ignores the shared group; no Expand/Collapse
  ids; CSV = `book-link-button book-csv-link`. Width: table class `tk-trades`, CSS at the end of
  style.css (What 280 px, Quantity clip 140 px, 8 px padding) fits the ui-check 1680 shot.
