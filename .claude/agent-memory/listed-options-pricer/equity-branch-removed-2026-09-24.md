---
name: equity-branch-removed-2026-09-24
description: History - Phase 2 (2026-09-24) removed the SPX/equity branch from equity_commodity.py; the Phase 5 rewrite the same day superseded the rest (see phase5-cmdty-greeks-2026-09-24)
metadata:
  type: project
---

On 2026-09-24 (user yes: the equity index leaves the app) the SPX / equity branch was removed from
`engine/options/equity_commodity.py` (`price_and_store_equity`, dividend yields, the
`equity_dividend_yields` DDL). The `price_all_and_store_equity` shim went the same day.

Later the same day Phase 5 rewrote the module for CMDTY_OPTION: see [[phase5-cmdty-greeks-2026-09-24]].
Superseded facts from this note (do not rely on them): the option's `bbg_ticker` no longer holds
the underlying id (it is the option's own Bloomberg ticker; the underlying comes from
`data.contracts.option_for`), PREMIUM is no longer x multiplier, and the surface / manual-vol
fallback and ASIAN payoff are gone.
