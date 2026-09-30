---
name: book-by-contract-pnl-contract-slice-2026-09-30
description: Book "By contract" view (ui/tabs/book_contracts.py) and the P&L Contract slice sharing its grouping; view switch, c- filter prefix, group "contract" read as none on Book/Risk, P&L slice labels Spread/Strategy/Commodity family/Contract; proof recipe
metadata:
  type: project
---

User, 2026-09-30: the Book gets a By trade | By contract switch in the table's strip (one table area, total
once); the P&L slice gets Spread (the trade) | Strategy (type, renamed) | Commodity family | Contract.
The Contract slice is the ONE place a trade is split (user's decision that day, reversing Phase G for it).

- `book_contracts.rows_of(conn, data)`: one row per engine leg key (`trade_book` legs' `contract_id`: future
  = instrument, LME '<root> <prompt>', FX '<pair> <value date>'); the fills-on-no-trade pseudo trade gets
  legs built from fills + df. Every fill on exactly one row, so totals = trade view = header (checked).
  Clearer = `clearer_name(trades.account)`: BOCF -> BOC, GSIL -> Goldman, else the account as written.
  Next = `engine.expiry.expiry_schedule` row, else stored expiry / prompt grey "≈" (net-zero contracts have
  no schedule row), FX forwards "Value <d>" (NEXT_ABBR "value date").
- Book: `VIEW_ID` "book-view" RadioItems (persistence session) in `.tk-strip-lead`; `TITLE_ID` and
  `CARD_ID` className are outputs of `_render` (9 outputs now); `.book-view--contract` hides the Group switch.
  Own stores `book-open-contracts`, `book-contract-sort-store`, `book-contract-folds`; column filters stored as
  `book:c-<col>` (book.own_cols strips them for the trade view; `view_filtered` replaces tf.is_filtered there).
  Panel "Open trade" = `tf.link(..., "book", trade, "bc-<key>")`; `_to_trade` sets the view to trade and
  opens that trade's panel.
- Shared group: `tf.GROUP_BY_CONTRACT` is in GROUPS; Book and Risk read `tf.trade_group(state)` (contract
  -> none); the Book/Risk switch shows None then.
- P&L: `base()["contracts"]`, `contract_items` (only the fills of the trades showing), `chart_groups`,
  `contract_table`, CSV per contract x trade. Column heads follow the slice title (SLICE_TITLES).

**Why:** Jason checks positions against broker statements per clearer; the user wants contract-level P&L.
**How to apply:** proof scripts in the session scratchpad (verify_bc.py, verify_pnl.py, shot_bc.py run with
.venv python for playwright; click the switch's label, its input is display:none).
