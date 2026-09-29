---
name: blotter-tab-audit-trail-2026-09-29
description: Trades tab relabelled Blotter (key "blotter") on 2026-09-29 as the audit trail of the upload, no P&L; blotter_view / fills_frame, Options sub-tab only with an open option, Bundles sub-tab gone
metadata:
  type: project
---
User, 2026-09-29: the Trades tab is the **Blotter** again, "What did I load, and did it load right?". No P&L, mark, Greek or period column (the Book is the one place for them). Label only changed: key stays "blotter" (`ui/app.py::TAB_KEYS`).

- `ui/tabs/blotter.py::blotter_view` = last upload (from `data.ingest.upload.last_upload_report` / `last_upload_issues`; a WARNING has row_no 0: shown as an "About the file" line, never "row 0"), every fill (`fills_frame`: one row per trade on file, an `html.Table` reusing `book-table`, newest trade date first, max 500 drawn, CSV = every row the filters keep), the upload history fold (`upload_history`), one Data issues drawer. The Uploads column shows only when `upload_history` is non-empty; "No upload recorded on this database" is said once.
- The file's raw Symbol and raw Price cell are NOT stored: "As written" is `trades.description`; "Broker price" is `trades.price / broker_price_scale` (the parser's own multiplication read back). A Request went to the ingest lanes to keep both raw cells.
- Spread column = `spread_of_trades` over book `_spreads(conn, as_of)`: strategy pairs (named like the Book's pair rows, legs joined " / "), hedges "FX hedge", residuals "outright", then non-hand positions by `formatting.spread_name`. Type = `book.trade_types`. Status = the shared reader's status.
- Sub-tab bar (`All fills | Options`) is always in the layout, hidden by style; `_update` has a third Output `(SUBTABS_ID, "style")` and shows it only when `has_open_option`. So the smoke test's callback key for the Blotter body is the 3-output key now.
- Bundles sub-tab removed from the tab; `ui/tabs/blotter_bundles.py` stays on disk, imported by nothing but `tests/test_ui_bundles.py`.
- The priced-table code (`scope_df`, `detail_table`, strips, `total_book_issues`) is dead on this tab but kept: `add_instrument_fields` / `add_trade_labels` are imported by `ui/tabs/pnl.py`; tests still pin the rest.

**How to apply:** a later "options cut down" wave edits only the options scope; keep the Blotter free of P&L. Proof recipe: sample server on a spare port, `blotter_shots/shots.py` clicking ".tab" "Blotter"; the sample book prices slowly (10 s+), wait 20 s before a shot.
