---
name: log-cubic-bootstrap-nonconvergence
description: Why the OIS curve default is flat forwards (LogLinear) since 2026-09-22 - QuantLib 1.43 PiecewiseLogCubicDiscount fails to converge on some evaluation dates with 1W/2W/3W pillars; the bootstrap is lazy; no fallback, a non-converging curve raises
metadata:
  type: project
---

Default interpolation of `engine/rates/curves.py::build_curve_set` is `LogLinear` (flat
overnight forwards) since 2026-09-22, user decision: "yes switch to flat forwards and rerun
the past days" (hard rule 7 satisfied). `LogCubicDiscount` (the reference project's choice
and the default before) stays available only by explicit argument.

**Why:** on 2026-09-22 the Bloomberg PC's pull failed every IRS (18, since removed from the
app) and 16 of 25 FX options (the USD curve is shared through `engine/options/rates.py`) with
`RuntimeError: convergence not reached after 99 iterations; last improvement 0.0178479,
required accuracy 1e-12`. QuantLib 1.43's non-local log-cubic iterative bootstrap oscillates
at the short end on some **evaluation dates** (09-22 quotes converge on 09-18 / 09-21 /
09-24 / 09-25 and fail on 09-22 / 09-23). Dropping any one of 1W / 2W / 3W converges;
`maxAttempts=5` and `accuracy=1e-10` do not help; log-linear converges every day. The
QuantLib bootstrap is lazy: the error surfaces on the first `discount()`, so
`build_curve_set` forces it (`curve.discount(curve.maxDate())`).

**How to apply:** a curve that does not converge raises `CurveBuildError` naming ccy, index,
date, interpolation and QuantLib's message: never a silent curve, and no fallback to another
interpolation. `CurveSet.bootstrap_note` is kept as "" because `engine/options` reads it.
Every caller (`store.bootstrap_and_store`, `engine/options/rates.py`) calls
`build_curve_set` without an interpolation argument, so the default is the one place to
change. Flat forwards vs log-cubic differ by up to ~1e-4 in DF between pillars, so a SWPM
comparison built on another interpolation is not a bug at that size. Tests pin the
2026-09-22 regression and the stored node DFs in `tests/test_rates_pricing.py` (explicit
log-cubic still raises on that date; if a newer QuantLib converges, that test is what to
revisit). See [[curves-only-scope-2026-09-24]].
