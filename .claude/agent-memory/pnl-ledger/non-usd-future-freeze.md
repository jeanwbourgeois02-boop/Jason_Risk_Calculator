---
name: non-usd-future-freeze
description: 2026-09-24 user-approved (C9): a settled future / EQ_OPTION / CMDTY_OPTION freezes in its quote ccy at usd_per_quote_on_or_before(ccy, EXPIRY), exact rows; column meanings; re-freeze why wording; golden book has no settled future
metadata:
  type: project
---

`_future_freeze(conn, pair, multiplier, qty, fill, settle, quote_ccy)`: m = last official
FUTURE_PX on or before expiry (dated m_day); S = `valuation.usd_per_quote_on_or_before(conn,
quote_ccy, settle)` -> (S, pair, source, spot_day), exact official SPOT rows only, NO near-marks
estimate (C9, user 2026-09-24: "the spot of its expiry date ... last spot on or before expiry, as
FX"). Before C9 (same day, earlier) it was `usd_per_quote(ccy, m_day)`, the price's date, with INTERP.
No S -> `_Unrealisable("no SPOT for USD conversion of <ccy> on or before its expiry <settle>")`
(same wording as valuation's provisional row), never frozen at 1.

Products: `valuation.FUTURE_PRODUCTS` (FUTURE, EQ_OPTION, CMDTY_OPTION); import the constants,
never new literals (pnl-valuation's request).

realise_pnl columns for a future: currency = quote_ccy; local_amount = contracts; usd_entry_amount =
qty x multiplier x fill x S; spot_usd_per_local = multiplier x m x S; pnl_usd = qty x that - entry;
spot_as_of_date = m_day (the PRICE's date, not the conversion's); spot_source = the FUTURE_PX's
source. Note: "<CCY> P&L converted at <pair> spot of <settle>" when the spot is dated the expiry
(identical to the pre-C9 wording then, so `_PIN_ROWS` stayed bit for bit), else
"... spot dated <d> (last before expiry)".

`_Freeze.conversion` = `_Conversion(price, s, pair, spot_date)` (in memory only, never stored)
lets `_conversion_why` tell a conversion-only move: stored S recovered as combined / (multiplier x
fresh price), confirmed by entry moving by the same factor. Why sentences:
- old price's-date rule (note "spot of <d>" with d != expiry, or "(INTERP"): "conversion moved to
  the spot of its expiry date: CNY at USDCNY spot dated <d>, a -> b USD per CNY (was the spot of <old>)"
- same date, value changed: "CNY conversion at USDCNY spot dated <d> a -> b USD per CNY (the marks of
  that date changed)"
- price or fill moved too: the generic "FUTURE_PX <d> mark ..., entry ..." sentence.

USD contract: S = 1.0 exactly, so every figure is bit for bit the old one. The golden book has NO
settled future on its three dates, so it does not prove the futures freeze;
`tests/test_ledger.py::_es_row_as_before` and `_PIN_ROWS` do.

Related: [[irs-ndf-removed]]
