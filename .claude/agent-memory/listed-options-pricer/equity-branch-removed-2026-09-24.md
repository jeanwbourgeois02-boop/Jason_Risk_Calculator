---
name: equity-branch-removed-2026-09-24
description: What equity_commodity.py became in the Phase 2 removal (2026-09-24): commodity-only, futures underlying via FUTURE_PX, implied vol from the option's FUTURE_PX; open Phase 5 seams (product code, portfolio SPOT read, discount curve)
metadata:
  type: project
---

On 2026-09-24 (user yes: the equity index leaves the app) the SPX / equity branch was removed from
`engine/options/equity_commodity.py`: `price_and_store_equity`, `resolve_equity_inputs`,
`set_dividend_yield` / `get_dividend_yield`, the `equity_dividend_yields` DDL, `_dispatch_equity`.
What stays is the path Phase 5 wires options on commodity futures into (product `CMDTY_OPTION`).

Behaviour after the change:
- Underlying = the option row's `bbg_ticker` = the future's instrument_id; its price is the
  future's official FUTURE_PX of the same day (at the future's own expiry; a single row at another
  date accepted). It used to read an underlying SPOT under BBG_BFXFORWARD (a quirk).
- Vol: implied from the option's own FUTURE_PX (VANILLA via `commodity.implied_volatility`,
  AMERICAN via `equity.implied_vol.implied_volatility_american` with q = r, i.e. Black-76 BAW),
  then surface, then manual, else "no vol". A price with no implied vol skips, no substitute.
- Rate: `resolve_ccy_rate(quote_ccy)`: day's OIS curve, else manual_rates, else skip
  "no curve/rate USD". bbg-library stopped pulling a USD OIS curve for listed options
  (2026-09-24); Phase 5 adds a discount-curve rule back.
- Classes renamed: `CommodityInputs`, `CommodityInputsResult`, `CommodityOutcome`; private
  `_read_trade`, `_price_row` (tests monkeypatch these).
- The `price_all_and_store_equity` shim was deleted the same day, once bbg-live dropped the call.

**Why:** Phase 2 removal runs top-down so no lane removes what another still imports.
**How to apply (Phase 5):** open seams, all outside this file: engine/pnl/valuation.py's
listed P&L path reads product `EQ_OPTION` only, while this pricer prices `CMDTY_OPTION` (one
code must be chosen); `engine/options/portfolio.py::build_positions` reads the underlying's
SPOT, not FUTURE_PX; contract price scales (cents vs dollars) are not applied here.
Related: [[eq-cmdty-loop-guard-2026-09-22]] (in options-pricer's memory; the per-trade guard stays).
