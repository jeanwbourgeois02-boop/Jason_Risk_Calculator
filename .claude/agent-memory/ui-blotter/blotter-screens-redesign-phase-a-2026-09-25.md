---
name: blotter-screens-redesign-phase-a-2026-09-25
description: Phase A on the Blotter - Total book has no strip, commodity-terms trade table (prev close from value_book at the Daily reference date), compact strip shared with blotter_fx, about()/marker/drawer, k/m summary tables; traps hit
metadata:
  type: project
---

Screens redesign Phase A (user-approved 2026-09-25), ui/tabs/blotter.py:

- **Total book has no P&L strip** (`_STRIPLESS_SCOPES`; no `blotter-strip-total` callback). The header is the total book. A collapsed "Data issues (N)" drawer (`total_book_issues`, id `blotter-issues-total`) lists unpriced and filled trades.
- **Total trade table in commodity terms** (`_DISPLAY_COLUMNS`): Instrument, Commodity / pair, Exchange, Product (plain names: "Option on future", "FX forward", ...), Trade date, Side, Quantity + Unit (lots / t / base ccy), Fill, Mark, Prev close, P&L (local) + Ccy, P&L (USD), Status, Expiry / value date, Bundle, Trade id. `add_instrument_fields` (alias `add_future_fields`) handles FX (pair, OTC, pnl_ccy = quote, FX_OPTION = base) and CMDTY_OPTION (root lookup, lots).
- **Prev close** = `priced_value_book(conn, period_reference_dates(as_of)["daily"])`'s own `mark` (`add_prev_close`), never a separate lookup. On the test date 2026-06-20 the daily ref is 2026-06-18 (06-19 is a holiday in config/holidays.txt).
- `value_book`'s `mark_date` on an open row is the mark's KEY date (a future's expiry, a leg's value date), not the close: never label it "dated" (the Futures sub-tab's "Settlement" column shows it; that label is arguably wrong, not changed).
- **`render_headline_strip` is compact** and is also what `blotter_fx._fx_strip` renders: short_money value, full figure + ref date in the value's `title`, markers "excl. N" / "from MM-DD" in a `card-note` row. Card structure kept (children[0] label, [1] value) because test_ui.py FX tests read it.
- `positions_rows` records must keep XAU detail "metal, not in the FX net" (tests/test_positions.py, book-positions' file, pins it); other details became short markers with the sentence in `tips[i]["detail"]`.
- Summary money: `rk.amount_short` + `rk.whole_units` on USD columns only; never on a column holding "" (whole_units turns "" into None, printed "n/a").

**Why:** CLAUDE.md "Screens redesign plan" Phase A.
**How to apply:** `_all_text` in test_ui_blotter.py now includes `title` hovers. Traps: `bash -c "..."` with backticks inside Python strings runs them as commands and silently deletes the text (write scripts with the Write tool instead); `py -3 -` with a heredoc still opens a REPL here (happened again).
