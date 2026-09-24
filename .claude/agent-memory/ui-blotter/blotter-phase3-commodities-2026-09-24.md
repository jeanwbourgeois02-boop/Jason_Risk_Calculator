---
name: blotter-phase3-commodities-2026-09-24
description: Phase 3 on the Blotter - Positions block gained a Commodities section (own tables, above FX), the Futures table is grouped by sector/commodity with USD subtotal rows, CMDTY_OPTION = Options, asset-class hover names each unpriced trade's reason
metadata:
  type: project
---

Commodity conversion Phase 3 (2026-09-24), ui/tabs/blotter.py:

- **Positions block** = `commodity_positions_section(pos["commodities"])` (tables `blotter-commodity-positions-table` + `-footer` with the "Commodities total", then `blotter-commodity-ccy-table` "P&L held in foreign currency"), THEN the FX table exactly as before. One `book_positions` call feeds both (`positions_rows(conn, as_of, pos=...)`). Separate tables, not extra rows in the FX table: the column sets differ (lots / units / net+gross USD vs rate / local delta / USD delta), and `tests/test_positions.py` (book-positions' file) pins `positions_rows`' FX records.
- Sector lines leave lot / unit cells as "" (a sector mixes units; hover says so); "" is not nully in Dash's formatter, so it prints blank, while None prints "n/a".
- **Futures table** is grouped by `futures_grouped_rows` (also used by the filter callback through `_table_rows`): sector row, commodity row, trades. Group rows carry `trade_id ""` so the strip (which sums `derived_virtual_data` trade ids) and the row-click panel ignore them; `row_kind` styles them; Sector / Commodity sit on every row so sorting by them keeps groups. Subtotal label in the Contract cell: "2 trades; excludes 1 of 2 unpriced". Tests must pick trade rows by `row_kind == "trade"`, not `data[0]`.
- `SCOPE_PRODUCTS["options"]` now includes CMDTY_OPTION (the Options sub-tab's own grid lists it); `ASSET_CLASS_OF["CMDTY_OPTION"] = "Options"`.
- ui-shell's `_reason_tag` shortens any reason it does not know to "unpriced" (a leftover IRS read "1 irs: unpriced"), so `asset_class_pnl_rows` rows carry `unpriced` ("<id>: <value_book reason>") and the table appends it to the hover.

**Why:** CLAUDE.md "Commodity conversion plan" Phase 3; the brief said commodities are Jason's main book, so they go above FX.
**How to apply:** new tests of this go in `tests/test_ui_blotter_commodity.py` (mine). The Bash tool's cwd here was `.claude/agents`, so the PostToolUse lint hook fails with "can't open tools/lint_hook.py" on every edit; the edits still land. Run ruff from the repo root instead. Never run `py -3 -` (no heredoc): it opens a REPL that spins on an invalid handle and writes 40 MB of output.
