---
name: phase5-cmdty-greeks-2026-09-24
description: Phase 5 CMDTY_OPTION Greeks (2026-09-24) - the one entry point, underlying via option_for, BAW vol by Brent not QuantLib, CNY discounts on USD, mark units, open seams in portfolio.py / store.py
metadata:
  type: project
---

Entry point: `price_listed_commodity_option(conn, trade_or_instrument, as_of, curve_cache=None) -> dict`
(pure, writes nothing; keys marks/reason/implied_vol/model/underlying_id/underlying_price/option_price/
rate/rate_source/result/settle_date). `write_listed_marks(conn, priced, snapped=None)` writes. The
per-trade `price_and_store_commodity` / `price_all_and_store_commodity` sit on top (one pricing per
instrument, per-trade guard kept).

Decisions and why:
- **Underlying** = `data.contracts.option_for(base_ccy, instrument_id).underlying.contract_id`, as
  curve-positions does (ingest writes the option's own Bloomberg ticker in bbg_ticker). A serial
  option whose underlying ingest resolved from a hint is not recoverable from the id alone: a seam.
- **BAW implied vol by scipy brentq on `_baw_engine.price`**, not the vendored QuantLib BAW solver:
  that one round-trips only ~1e-3 (bumped vega), which made the Greeks' model price miss Bloomberg's
  by ~3e-4 rel. Greeks for AMERICAN = vendored `finite_difference_greeks` on BAW, carry = r, rho summed.
  CLAUDE.md says BAW for American Greeks, so the CRR tree is no longer used here.
- **Rate**: own ccy via `resolve_ccy_rate_with_source` (OIS, else manual_rates), else USD SOFR with
  "(no CNY curve or manual rate on file)" in rate_source (housekeeper's brief). CNY/MYR/SGD have no
  CCY_RFR entry, so SHFE/DCE options discount on USD unless a manual rate is typed.
- **No vol substitute**: no option price or a price below intrinsic / above cap blanks the Greeks.
  The old surface / manual-vol fallback and ASIAN were removed (the P&L convention says "at the vol
  the option's price implies"). `vol_surface_points` table may linger on old DBs; nothing reads it.
- **Units**: DELTA = dV/dF = futures lots per option lot (discounted Black-76 delta); PREMIUM =
  Bloomberg's price as quoted (not x multiplier); gamma/vega/theta/rho in quoted price units per unit.
  Guards: option multiplier != underlying's, or strike/future ratio > 20 (scale mix-up) blanks.

**How to apply:** open seams outside this file, raised as Requests 2026-09-24: `portfolio.py::build_positions`
reads SPOT on bbg_ticker for listed options (should use underlying FUTURE_PX, `result` from this dict);
`store.py` does not call this module yet; `engine/options/__init__.py` scope ledger names removed
symbols (`_price_all_and_store`, CommodityInputs, vol_surface_points).
