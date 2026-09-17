---
name: rates-vol-conventions
description: engine/rates_vol/ sign conventions, table shapes, and numerical quirks decided during Phase 6 of the options_calc merge (2026-09-17)
metadata:
  type: project
---

Phase 6 of the options_calc merge (interest-rate options: swaption/cap-floor/SABR/
Bermudan) landed in `engine/rates_vol/` (sibling of `engine/rates/`, not nested inside
it) + `tests/test_rates_vol.py`, per the housekeeper's scope ledger in
`engine/options/__init__.py` and agent memory `options-calc-merge-2026-09-17`.

**Why:** `engine/rates/` only prices linear OIS swaps off a bootstrapped curve; the
vendored `options_calc.rates` library (Black-76 swaption/cap-floor/SABR, Hull-White
trinomial-tree Bermudan) is a genuinely separate model family that needed its own
package, own tables, and its own sign convention (long/short an option, not pay/receive
fixed).

**How to apply / key facts for future work in this area:**
- `trades.quantity` sign here means **long/short the option** (+ = bought), NOT
  pay/receive fixed the way `engine/rates`' IRS `quantity` is. Every
  `engine/rates_vol/pricer.py` function returns ALREADY sign-and-notional-scaled
  totals (`npv_total`, `dv01`, `vega`, `gamma`, `theta`) — unlike `engine/options`'
  FX-option DELTA mark, nothing downstream needs to re-apply `quantity`.
- New, package-owned tables (not in `data/ingest/schema.py`, created defensively):
  `instrument_rate_options` (trade shape: payoff/option_type/strike/index/underlying
  dates/exercise_dates/fixed_freq/float_freq), `rate_vols` (flat lognormal-or-normal
  vol; NORMAL is staged but always REJECTED at resolve time, never silently converted),
  `rate_model_params` (SABR alpha/beta/rho/nu, Hull-White a/sigma).
- **CLAUDE.md/schema follow-ups flagged, not applied** (out of this agent's directory):
  `trades.product` needs `SWAPTION`/`CAP_FLOOR` added to its documented value set;
  `instruments.asset_class` needs `IRS_OPTION`. No new mark_type or
  `OFFICIAL_MARK_SOURCE` entry needed — PV_USD/DV01_USD (QL_PRICER) and
  VEGA/GAMMA/THETA (QL_OPTIONS_PRICER) already exist in schema.py for other pricers.
- **Flat-curve approximation**: the vendored `options_calc.rates` engine takes flat
  `discount_rate`/`forecast_rate`, not a curve object. `inputs.py::derive_curve_inputs`
  bridges this by deriving both from the correctly-bootstrapped `engine.rates` OIS
  `CurveSet` (forecast = forward par swap rate via `build_instrument(...).fairRate()`;
  discount = the curve's own continuously-compounded zero rate to the option's expiry).
  Good for risk, explicitly NOT good enough for a mid quote (see module docstrings).
- **Numerical quirk (important, easy to reintroduce a bug around)**: the vendored
  `rates/_engine.py` calls `ql.Date.todaysDate()` (real system date) and resets the
  GLOBAL `ql.Settings.instance().evaluationDate` to it on every single pricer call —
  completely ignoring any `as_of` you intended. Since QuantLib's evaluation date is a
  process-wide singleton, this silently poisons any cached `CurveSet` you read from
  AFTER calling a vendored pricer (e.g. `price_all_and_store`'s per-currency curve
  cache, reused across trades interleaved with pricing calls). `inputs.py` guards this
  by resetting `ql.Settings.instance().evaluationDate` to `as_of` immediately before
  every `zeroRate`/`fairRate` read (`_zero_rate`, `_forward_par_rate`). Any new code
  that reads off a `CurveSet` in this package must do the same reset first, not assume
  the evaluation date is still what `build_curve_set` left it as.
- **`ql.Period(years, ql.Years)` needs an Integer**, not a float — the vendored
  `build_forward_swap` does this for `swap_tenor_years` directly (unlike `expiry_years`,
  which it internally day-count-rounds). `pricer.py`'s `price_swaption`/
  `price_bermudan_swaption` round `swap_tenor_years` to the nearest whole year before
  calling the vendored functions; a real underlying tenor is virtually always whole
  years anyway.
- **Bermudan/Hull-White carries real, documented model risk** (uncalibrated params,
  one-factor curve dynamics, un-converged tree step count) — flagged prominently in
  `engine/rates_vol/__init__.py` and `pricer.py` docstrings per the task's explicit
  instruction to not let it feed real P&L quietly alongside the closed-form (Black-76)
  pieces. This package's own Bermudan Greeks (vega/gamma/theta, not vendor-provided)
  compound that risk further: "vega" there bumps the HW model's own sigma, not a
  market-observable lognormal vol.
- Found while auditing `engine/rates/__init__.py`'s docstring: it still describes "no
  FX-spot source wired in" for non-USD IRS notional as an open gap, but
  `engine/rates/store.py`'s OWN docstring (dated 2026-09-17) says that was already
  fixed via `engine.pnl.valuation.usd_per_quote` with a raise-if-missing. This package
  reuses the same `usd_per_quote` function and is NOT materially stricter than current
  `engine/rates/store.py` — the `__init__.py` docstring there is just stale. Flagged
  to the housekeeper as a doc-freshness follow-up, not fixed here (out of directory).
