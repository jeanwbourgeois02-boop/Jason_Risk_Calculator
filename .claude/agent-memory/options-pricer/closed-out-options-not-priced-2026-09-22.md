---
name: closed-out-options-not-priced-2026-09-22
description: Closed-out FX options are skipped by the bulk pricer passes (store.closed_out_options wraps the P&L's rule); PricingOutcome.closed_out flag and the dicts' closed_out key; live.py still lists them under skipped (bbg-data owes the split); test gotcha on trade_legs
metadata:
  type: project
---

Landed 2026-09-22: `price_all_and_store` (catch-up included), `price_close` and so `recalc_on_file` skip every FX_OPTION trade of a group closed out as of the date priced. The grouping is the P&L's own (`engine/pnl/valuation.py::closed_out_options`), wrapped by `store.closed_out_options` with a LAZY import: no cycle exists (engine.pnl imports nothing of engine.options), lazy mirrors engine/rates/store.py and keeps engine.options importable without pandas.

**Why:** user: "we dont need to price all options, as some of them might be closed out already ... we just present the buy and sell price as pnl". The P&L never reads a closed-out trade's mark (status CLOSED, ledger CLOSE_OUT freeze), so pricing them was wasted work and noise in the pull status.

**How to apply:**
- Reporting shape: `PricingOutcome.closed_out=True, priced=False`, reason "closed out <date>: bought and sold back with <ids>; ..."; `price_close` returns `"closed_out": [trade_ids]` beside `skipped`; `recalc_on_file` a `"closed_out"` total. Any exact-dict assertion on those returns must carry the key.
- `data/bloomberg/live.py::_options_step` reads only `priced` / `reason`, so until bbg-data splits on `o.closed_out` the status line counts them under skipped (with the clear reason). Follow-up owed to bbg-data.
- `price_and_store` (one trade by name: Options grid after a terms edit, structures.py) still prices whatever it is given, on purpose; listed options unchanged (the P&L rule is FX_OPTION only).
- Test gotcha: `closed_out_options` joins `trade_legs` leg 1 (settle_date = expiry), and `tests/test_options_pricing.py::_seed_option_trade` writes NO legs and a fixed trade_date 2026-08-18: seed a NOTIONAL leg per trade and UPDATE the sell-back's trade_date (helper `_seed_bought_and_sold_back` in tests/test_options_close.py).
- `price_all_and_store` prices every trade on file whatever its trade_date (a tomorrow-dated trade still gets today's mark), so on a day before the sell-back both trades are priced; only `price_close` limits to `trade_date <= day`.
