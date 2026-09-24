---
name: phase3-commodities-block-2026-09-24
description: book_positions' "commodities" block (Phase 3) - built from curve-positions only, None not NaN, partial sums with "excludes N of M", and where futures still leak into the Ladder's Net/Gross
metadata:
  type: project
---

Phase 3 (housekeeper brief 2026-09-24): `book_positions` = {fx, fx_options, commodities}.
`commodity_positions(conn, as_of)` reads `engine.curve.curve_positions` (imported inside the
function so tests can monkeypatch `engine.curve.curve_positions`) and recomputes nothing.

- Shape: {available, note, reason, sectors: [{sector, net_usd, gross_usd, missing (root ids),
  reason, commodities: [{root_id, name, exchange, currency, net_lots, gross_lots, net_units,
  unit, net_usd, gross_usd, missing (curve's contract ids), reason}]}], net_usd, gross_usd,
  missing (root ids), currency_exposure: {ccy: {pnl_local, pnl_usd, reason}}}.
- n/a is None (curve-positions' convention), not NaN like the FX blocks.
- curve-positions' `_usd_totals` gives None for a sector with any missing contract (no partial
  sum). The brief said "left out of the sums ... say excludes N", so this block sums the known
  commodities' figures itself when a sector is incomplete and says "excludes N of M commodities
  with no USD figure: <name> (<root>): <why>". A complete sector takes curve's own figure.
  So the Blotter can show a partial sector total where the Curve tab shows n/a.
- Nothing open / all flat: sectors [], net/gross None, reason = curve's note.
- Order: sectors by gross USD desc, commodities by |net USD| desc; None last; ties by name.
- Futures and FX net: fx_positions' path (exposure_records_from_db) never reads FUTURE trades,
  so the FX net/gross holds. But ui/tabs/exposure.py (ui-ladder) adds `futures_usd_delta`'s
  value into the Ladder's "Delta (FX + futures)" card and the risk table's gross, and
  ladder.py's `delta_per_ccy` SQL (unused by the app) includes FUTURE notional legs.

Related: [[phase2-fx-only-2026-09-24]], [[futures-usd-at-spot-2026-09-24]]
