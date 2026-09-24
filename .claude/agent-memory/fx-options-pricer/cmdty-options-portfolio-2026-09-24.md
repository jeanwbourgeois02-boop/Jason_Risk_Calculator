---
name: cmdty-options-portfolio-2026-09-24
description: Phase 5 (2026-09-24): how portfolio.py reads a CMDTY_OPTION's underlying (contract-master + FUTURE_PX, never bbg_ticker), duplicated rule, CNY/MYR/SGD rate contract
metadata:
  type: project
---

Since 2026-09-24 (commodity Phase 5) a CMDTY_OPTION's `instruments.bbg_ticker` is the option's OWN Bloomberg ticker. `portfolio.build_positions` finds the underlying with `data.contracts.option_for(base_ccy, instrument_id, conn=conn).underlying.contract_id` (lazy import) and reads that future's official FUTURE_PX: the row at the future's own expiry, else the single row. It never calls `equity_commodity.price_listed_commodity_option`, because that re-solves the implied vol.

**Why:** the old rule read a SPOT on `bbg_ticker`, so every listed option landed in `skipped`. The request came from listed-options-pricer.

**How to apply:**
- The FUTURE_PX pick rule is duplicated from `equity_commodity._future_price` (listed-options-pricer's). If that lane changes its rule, change `portfolio._underlying_future_price` to match.
- Asset classes other than FX_OPTION / CMDTY_OPTION are skipped with a reason, not read through a SPOT.
- `rates.resolve_ccy_rate_with_source` for a currency with no CCY_RFR entry (CNY, MYR, SGD) returns `(None, "no curve/rate <CCY>")` without raising, or a MANUAL rate when one is on file. listed-options-pricer relies on that to fall back to USD SOFR. A test in test_options_pricing.py pins it.
- Store's `PricingOutcome(product='CMDTY_OPTION')` also feeds portfolio (same attribute shape).
