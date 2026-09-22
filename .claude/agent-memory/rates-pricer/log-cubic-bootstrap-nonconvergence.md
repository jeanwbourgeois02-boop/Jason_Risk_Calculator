---
name: log-cubic-bootstrap-nonconvergence
description: QuantLib 1.43 PiecewiseLogCubicDiscount OIS bootstrap can fail to converge on some evaluation dates with 1W/2W/3W/1M pillars; the bootstrap is lazy; build_curve_set falls back to LogLinear and records it
metadata:
  type: project
---

On 2026-09-22 the Bloomberg PC's pull failed every IRS and every FX option needing the
USD curve with `RuntimeError: convergence not reached after 99 iterations; last
improvement 0.0178479, required accuracy 1e-12`. Root cause, reproduced with the
snapshot's own USD SOFR quotes (17 pillars, 1W 2W 3W 1M 2M 3M 6M 9M 1Y 2Y 3Y 5Y 7Y
10Y 15Y 20Y 30Y): QuantLib 1.43's non-local log-cubic iterative bootstrap oscillates at
the short end for this pillar spacing on some **evaluation dates** (the 09-22 quotes
converge at a 09-21 evaluation date and fail at 09-22 / 09-23; the 09-21 quotes fail at
09-22). Dropping any one of 1W / 2W / 3W makes it converge; log-linear converges every
day and differs from log-cubic by ~1e-6 in DF at 1Y. EUR / JPY / CHF (fewer short
pillars) never failed.

**Why:** the QuantLib bootstrap is lazy: the curve object builds fine and the error only
surfaces on the first `discount()` / `zeroRate()` call, i.e. inside the swap pricer or
the options pricer, so "curve built OK" proves nothing until a discount is read.

**How to apply:** `engine/rates/curves.py::build_curve_set` now forces the bootstrap
(`curve.discount(curve.maxDate())`), falls back to `LogLinear` on a RuntimeError, logs a
warning and sets `CurveSet.interpolation` / `CurveSet.bootstrap_note` (names read by
engine/options via getattr; `store.price_all_and_store` entries carry them as
`interpolation` / `note`). If log-linear also fails it raises `CurveBuildError`. Default
interpolation is still LogCubicDiscount (user never asked to change it; recommended to
the user as an open choice). DV01 bumps perturb the live quotes of the same curve
object so a bump never changes interpolation. Reconciliation tolerance implication: a
day priced on the fallback differs from a log-cubic day by a few 1e-6 in DF, i.e. a few
USD per 10mm on PV; look at `note` before chasing such a difference against SWPM. See
[[sign-and-scope-conventions]].
