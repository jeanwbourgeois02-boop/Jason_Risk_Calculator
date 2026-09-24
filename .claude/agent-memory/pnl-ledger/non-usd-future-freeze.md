---
name: non-usd-future-freeze
description: 2026-09-24 user-approved ("Spot of valuation date"): a settled future / EQ_OPTION freezes in its quote currency, converted at usd_per_quote of the FUTURE_PX's own date; column meanings; golden book holds no settled future
metadata:
  type: project
---

`_future_freeze(conn, pair, multiplier, qty, fill, settle, quote_ccy)`: m = last official
FUTURE_PX on or before expiry (dated m_day); S = `valuation.usd_per_quote(conn, quote_ccy, m_day)`
(near-marks estimate included, like the FX freeze; the note names an INTERP source). No S ->
`_Unrealisable("no SPOT for USD conversion of <ccy> on <m_day>")`, never frozen at 1.

realised_pnl for a future: currency = quote_ccy; local_amount = contracts; usd_entry_amount =
qty x multiplier x fill x S; spot_usd_per_local = multiplier x m x S; pnl_usd = qty x that - entry;
spot_as_of_date = m_day; spot_source = the FUTURE_PX's source (not the conversion's); note adds
"<CCY> P&L converted at <pair> spot of <m_day>" for a non-USD contract. S is recoverable as
usd_entry_amount / (local_amount x multiplier x fill) (fails for a fill of 0).

USD contract: S = 1.0 exactly, so every figure is bit for bit the old one (x 1.0 is exact).
The golden book has NO settled future on its three dates (all 11 futures OPEN), so it does not
prove the futures freeze; `tests/test_ledger.py::_es_row_as_before` does, with non-round prices.

A changed conversion spot on m_day moves entry and mark by the same factor -> generic re-freeze,
`why` = "FUTURE_PX <d> mark a -> b, entry c -> d (the marks of that date changed)".
