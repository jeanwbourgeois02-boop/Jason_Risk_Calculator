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

**Upstream change landed and wired up (2026-09-17 follow-on task)**: the curve-input /
explicit-evaluation-date / explicit-exercise-dates upgrade described below (originally
written when it was only upstream, not yet re-vendored) has now been re-vendored into
`engine/options/vendor/options_calc/rates/` (confirmed against
`engine/options/vendor/tests/rates/test_curve_inputs.py`) AND wired into this package:
- **Flat-curve approximation REMOVED from the live pricing path.** `inputs.py::
  derive_curve_inputs` no longer derives `discount_rate`/`forecast_rate` at all — it
  returns `CurveInputs.curve` (`engine.rates.curves.CurveSet.discount`, a
  `ql.RelinkableYieldTermStructureHandle` — confirmed it duck-types fine as a
  `YieldTermStructureHandle` argument, no cast needed) and passes THAT as BOTH
  `discount_curve` and `forecast_curve` to every vendored pricer (`pricer.py`), i.e.
  single-curve OIS, matching `engine/rates`'s own swap pricer. `CurveInputs.forward_rate`
  (renamed from `forecast_rate`) is KEPT — still needed as the SABR smile's
  evaluation point / vol-resolution key — but is no longer fed into the pricing engine as
  a rate. The old flat-rate derivation is KEPT ONLY as `inputs.py::
  derive_flat_rate_inputs` (a deprecated fallback, never called by `store.py`/`pricer.py`)
  specifically so a regression test can prove the two prices differ on the non-flat
  `ois_snapshot_v1.json` fixture curve
  (`tests/test_rates_vol.py::test_curve_input_atm_swaption_parity_and_differs_from_old_flat_approximation`).
- **DV01 now uses a curve-spread parallel bump**, NOT the old two-flat-rate rebuild:
  `pricer.py::_bump_curve` wraps the curve in a `ql.ZeroSpreadedTermStructure` fed a
  `ql.SimpleQuote` (the SAME technique `finite_difference_curve_greeks`/`_bumped_curve`
  use internally upstream), applied to BOTH the discount and forecast role AT ONCE (since
  they're the same curve object here) — deliberately NOT reusing the vendored engine's
  own split delta(forecast-only)/rho(discount-only) bump, which is only meaningful for a
  genuine two-curve setup. Sign verified against `engine/rates/valuation.py`'s own DV01
  convention (`NPV(+1bp) - NPV(base)`) — a long payer's DV01 is positive, a long
  receiver's is negative (`test_price_swaption_dv01_sign_matches_engine_rates_convention`).
- **Bermudan exercise dates are now the trade's OWN staged dates, never generated.**
  `store.py::_parse_exercise_dates` reads the `;`-joined ISO list straight off
  `instrument_rate_options.exercise_dates` and passes it through to the vendored
  `exercise_dates=` kwarg; `_infer_exercise_frequency_years` (average-spacing
  approximation) is DELETED. Empty `exercise_dates` on a `BERMUDAN_SWAPTION` trade is a
  skip with reason exactly `"no exercise dates"`, checked BEFORE the Hull-White
  param-presence check (so a Bermudan with neither never reports "Hull-White" as the
  reason — order matters for a caller parsing `PricingOutcome.reason`).
- `evaluation_date=as_of` (converted via `engine.rates.qlmap.ql_date`) is now passed
  explicitly on every vendored pricer call from `pricer.py`. `inputs.py::_set_eval_date`
  (used only by `_forward_par_rate`/the deprecated flat fallback) is now
  belt-and-suspenders, not load-bearing, per the note below — kept anyway.

**New in this same task: `calibration.py` and `book.py` (2026-09-17)**
- `calibration.py` is new math OWNED by this app (not re-vendored, not a vendor patch):
  `calibrate_sabr` (scipy `least_squares` fit of alpha/rho/nu against the vendored
  `sabr_swaption_vol`, beta FIXED by the caller — SABR convention, not fittable from one
  bucket's handful of strikes) and `calibrate_hull_white` (QuantLib's OWN `HullWhite` +
  `SwaptionHelper` + `LevenbergMarquardt`, NOT the vendored `bermudan_swaption.py`, which
  has no calibration routine). Both write `rate_model_params` rows with
  `source='CALIBRATED'`.
- **Non-obvious numerical gotcha found while testing SABR round-trip**: when generating
  synthetic test quotes at a chosen "forward" rate, that forward MUST match EXACTLY what
  `calibrate_sabr` will independently recompute from the curve
  (`inputs._forward_par_rate` on `[expiry_date, expiry_date + tenor]`) — even a ~0.3%
  mismatch (e.g. assuming forward=0.045 when the curve actually implies ~0.0418) makes
  the "ATM" quote not genuinely at-the-money from the calibrator's point of view, and the
  least-squares fit silently compensates via a biased `rho`. Always derive the test's
  forward the same way the code under test does, don't hardcode a plausible-looking
  number.
- `get_rate_model_params` (SABR and Hull-White both) now prefers a `MANUAL` row over a
  `CALIBRATED` row per param, never the reverse — an explicit desk override must not be
  silently superseded by an automated fit. Verified end-to-end through `store.py::
  price_and_store` (`test_price_and_store_prefers_manual_hull_white_params_over_calibrated`),
  not just at the `get_rate_model_params` unit level.
- `book.py` (`py -m engine.rates_vol.book`) is the ONLY trade-entry path this package has
  — no blotter `Fin Type` maps to a rate option yet. Booking a `BERMUDAN_SWAPTION` with no
  `--exercise-dates` is refused AT BOOKING TIME (not silently left unpriceable forever) —
  fail-fast beats a structured pricing skip discovered later. `--notional` is always
  positive; sign comes from the required, mutually-exclusive `--long`/`--short` flag.
  `instrument_id` is derived as `== trade_id` (one instrument per manually-booked trade in
  this simple CLI, unlike the FX/futures side where several trades can share one listed
  instrument).
