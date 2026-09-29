---
name: broker-raw-cells
description: Trade.broker_symbol / broker_price (2026-09-29) carry the file's Symbol and Price cells as written for the Trades tab; the '' rule and why
metadata:
  type: project
---
User approved 2026-09-29 storing each trade's raw Symbol and Price cells so the Trades tab shows the blotter exactly as the broker wrote it (e.g. '3.32049167' $/lb feeder cattle next to the stored 332.049167 cents).

- Filled once, in `blotter._common(row, price_percent)`, so every product gets them. FX options pass `PERCENT_FRACTION` (a '0.58%' premium is a number there).
- `broker_price` is '' when the Price cell is blank or not a number under the row's percent rule: the fill then came from NetInvoice / Premium / the amounts. A numeric Price cell that the FX consistency check overrides (the date-serial case) is still shown as written.
- `broker_symbol` is the Symbol cell only, never the Underlying Symbol fallback.
- Display only: never read by a calculation. ingest-schema adds `trades.broker_symbol` / `broker_price` (TEXT, default ''); ingest-booking writes them.

**Why:** Jason reads his own file; the broker's units differ from Bloomberg's on cents-quoted roots.
**How to apply:** a new row kind must build its Trade through `**_common(row)`, or the two fields stay ''.

Found, not fixed (2026-09-29): `blotter.parse(DataFrame)` with NaN cells (pd.read_csv without keep_default_na=False) raises TypeError in `detect_day_first`; file / bytes input is fine.
