---
name: log-cubic-bootstrap-nonconvergence
description: Why the OIS curve default is flat forwards (LogLinear) since 2026-09-22 - QuantLib 1.43 PiecewiseLogCubicDiscount fails to converge on some evaluation dates with 1W/2W/3W pillars; the bootstrap is lazy; no fallback, a non-converging curve raises; recalc_on_file reruns past days
metadata:
  type: project
---

Default interpolation of `engine/rates/curves.py::build_curve_set` is `LogLinear` (flat
overnight forwards) since 2026-09-22, user decision: "yes switch to flat forwards and rerun
the past days" (hard rule 7 satisfied). `LogCubicDiscount` (the reference project's choice
and the default before) stays available only by explicit argument.

**Why:** on 2026-09-22 the Bloomberg PC's pull failed every IRS (18) and 16 of 25 FX options
(the USD curve is shared through `engine/options/rates.py`) with `RuntimeError: convergence
not reached after 99 iterations; last improvement 0.0178479, required accuracy 1e-12`.
Reproduced with the snapshot's USD SOFR quotes (17 pillars 1W..30Y): QuantLib 1.43's
non-local log-cubic iterative bootstrap oscillates at the short end on some **evaluation
dates** (09-22 quotes converge on 09-18 / 09-21 / 09-24 / 09-25 and fail on 09-22 / 09-23;
09-21 quotes fail on 09-22). Dropping any one of 1W / 2W / 3W converges; `maxAttempts=5`
and `accuracy=1e-10` do not help; log-linear converges every day. The switch moved the
18-swap book on 2026-09-21 by PV 9,895,018 -> 10,019,751 and DV01 318,568 -> 318,663
(~1.3 % of PV; the user accepted). The QuantLib bootstrap is lazy: the curve object builds
fine and the error surfaces on the first `discount()` inside a pricer, so `build_curve_set`
forces it (`curve.discount(curve.maxDate())`).

**How to apply:** a curve that does not converge raises `CurveBuildError` naming ccy, index,
date, interpolation and QuantLib's message: never a silent curve, and no fallback to another
interpolation (the one-day LogLinear fallback of 2026-09-22 was retired with the switch;
`CurveSet.bootstrap_note` is kept as "" because `store.price_all_and_store`'s `note`, the
pull status and `engine/options` read it). Every caller (`store`, `engine/options/rates.py`,
`engine/rates_vol/inputs.py` and `calibration.py`) calls `build_curve_set` without an
interpolation argument, so the default is the one place to change. DV01 bumps perturb the
live quotes of the same curve object, so a bump never changes interpolation. The past days
are rerun from `curve_quotes` by `store.recalc_on_file(conn, as_of, since=None)` (per-day
`price_all_and_store`, INSERT OR REPLACE, never raises, `{days: [{day, priced, failed}]}`);
a day with no swap dealt yet builds and stores no curve. Reconciliation: flat forwards vs
log-cubic differ by up to ~1e-4 in DF between pillars and ~1e-7 at the pillars (the helper's
pillar is its payment date, and its compounded float leg follows the forward path), so a
SWPM comparison built on a different interpolation is not a bug at that size. Tests pin the
2026-09-22 regression in `tests/test_rates_pricing.py` (default converges; explicit
log-cubic still raises on that date, so if a newer QuantLib converges the test is what to
revisit). See [[sign-and-scope-conventions]].
