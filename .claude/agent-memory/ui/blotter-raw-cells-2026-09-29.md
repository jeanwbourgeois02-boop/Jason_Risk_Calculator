---
name: blotter-raw-cells-2026-09-29
description: Blotter "Every fill as uploaded" reads trades.broker_symbol / broker_price (raw file cells, TEXT) since 2026-09-29; '' reasons; the ×100 marker rule
metadata:
  type: project
---

Since 2026-09-29 the upload stores the file's Symbol and Price cells as written (`trades.broker_symbol`, `trades.broker_price`, TEXT, '' = not recorded). The Blotter fills table (`ui/tabs/blotter.py::fills_frame`) shows them as-is: As written = broker_symbol (else the description in grey, hover "symbol not recorded: loaded before 29 Sep, re-upload to fill"); Broker price = the text exactly (else an em dash: no symbol either -> not recorded; symbol but no price -> rebuilt from NetInvoice). The broker price is never derived from price ÷ broker_price_scale any more. The ×100 marker on Our price shows only when stored price == parsed cell × the root's scale (frame key `price_scaled`).

**Why:** user approved storing raw cells so the audit trail shows the file, not our inverse.
**How to apply:** `_read_trades` falls back through three SELECTs for older databases; the search matches broker_symbol; CSV carries As written (symbol) and Description separately. Proof recipe: scratch DB + `import_blotter(payload, name, db)` and a copy of data/raw/sample.db (old rows ''). Sample fixtures: NBP (MX6-GBAA 0.8895 -> 88.95 ×100), HOX6 2.385 -> 238.50. See [[blotter-tab-audit-trail-2026-09-29]].
