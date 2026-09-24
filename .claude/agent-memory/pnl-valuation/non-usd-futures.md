---
name: non-usd-futures
description: 2026-09-24 "Spot of valuation date": futures / EQ_OPTION P&L in quote_ccy converted at usd_per_quote; how the open, provisional and settled rows do it
metadata:
  type: project
---

- User decision 2026-09-24 ("Spot of valuation date", CLAUDE.md "P&L conventions -> Futures"):
  `pnl_local = contracts x multiplier x (m - f)` in `instruments.quote_ccy`, `pnl_usd = pnl_local x S`,
  S = `usd_per_quote(conn, quote_ccy, as_of)`. EQ_OPTION shares the path (`_fut_sql` selects `i.quote_ccy`).
- USD contract: `usd_per_quote('USD')` is `(1.0, 'USD', 'identity')` and `x * 1.0 == x` exactly, so a USD
  future is bit for bit what it was; the golden book (27 USD FUTURE rows, no EQ_OPTION) stayed green.
- Open row with no S: mark and pnl_local shown, pnl_usd / pnl_spot_usd NaN, spot NaN / '', reason
  "no SPOT for USD conversion of <ccy> on <as_of>" (the FX rows' wording). A non-USD future with no price
  also starts at spot NaN / '' (never 1 / identity); a USD one keeps 1.0 / identity as before.
- `_frozen_row` (settled, no realised row): S at the FUTURE_PX's own date m_day, None when no S (like FX).
- `_settled_future_row`: pnl is the stored figure; `spot` is 1.0 / identity when `realised_pnl.currency`
  is USD, else `usd_per_quote(currency, spot_as_of_date)` (display only; `_BadValue` there gives NaN spot,
  never blanks the frozen P&L). pnl-ledger's `_future_freeze` (same day) stores currency = quote_ccy,
  entry = qty x mult x fill x S, spot_usd_per_local = mult x m x S, S = usd_per_quote at m_day.
- Provisional vs ledger figure agree to ~1e-11 relative (qty*mult*(m-f)*S vs qty*mult*m*S - qty*mult*f*S);
  the same rounding class as the pre-existing USD case.
- Test fixtures: a mark's instrument must exist (marks has a foreign key), so insert the USDCNY / EURUSD
  instrument rows before their SPOT marks.
- Reviewer follow-up (same day): `_frozen_row`'s future branch with the price on file but no S returns an
  `_unpriced` row (mark, mark_date, pnl_local shown) whose reason is "settled trade <id>: no SPOT for USD
  conversion of <ccy> on <m_day>, so it cannot be frozen" (W-3); `_provisional` appends an unreadable
  realised row to that reason. The FX and FX_OPTION branches still return None (generic "no official mark"
  reason) -- left as found, not done. Open row with no S: pnl_carry_usd NaN (N-1).
- W-4 test pins open (expiry day) = provisional (day after) = ledger frozen to 1e-9; it needs pnl-ledger's
  `_future_freeze` conversion to be in the tree. W-1 (a price dated BEFORE expiry converts at that date's
  spot) is the user's question, not changed.
