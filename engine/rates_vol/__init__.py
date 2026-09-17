"""Interest-rate OPTIONS pricing: European/Bermudan swaptions, caps/floors, SABR smile,
priced from the vendored ``options_calc.rates`` library (``engine/options/vendor/``).

This is Phase 6 of the options_calc merge (see ``engine/options/__init__.py``'s scope
ledger and agent memory ``options-calc-merge-2026-09-17``). It is a SIBLING of
``engine/rates/`` (that package's OIS discount-curve swap pricer -- ``curves.py`` /
``instruments.py`` / ``valuation.py`` shapes do NOT carry over unchanged here), not an
extension of it: ``engine/rates/`` prices a linear instrument (an OIS swap) off a
bootstrapped discount curve; this package prices OPTIONS on a forward swap rate / forward
LIBOR-style rate via Black-76 (European swaption, cap/floor, SABR smile) or a one-factor
Hull-White trinomial tree (Bermudan swaption) -- a genuinely separate model family, per
the vendored library's own ``rates/_engine.py`` docstring ("DELIBERATELY SEPARATE ...").

What IS reused from ``engine/rates/``: the bootstrapped OIS ``CurveSet``
(``engine.rates.curves.build_curve_set``, fed from the same ``curve_quotes`` table and
the same ``CCY_RFR`` canonical-index mapping) and ``build_instrument`` (used once, to
derive a forward par swap rate off that curve -- see "Flat-curve approximation" below).
Everything else is new: this package's own tables (below), its own pricer/store glue, and
its own sign convention (long option, not pay/receive fixed).

Module map
----------
- ``inputs.py``       -- OIS CurveSet -> the curve handle passed directly to the vendored
                         pricers (2026-09-17: replaces the earlier flat discount/forecast
                         rate derivation -- see "Curve inputs" below), manual vol
                         (``rate_vols``) and model-parameter (``rate_model_params``)
                         lookup, resolved-vol precedence (flat -> SABR -> skip),
                         MANUAL-over-CALIBRATED model-parameter source preference.
- ``pricer.py``       -- scalar wrappers over the vendored pricers, unit-notional in,
                         then scaled by signed trade quantity: ``price_swaption``,
                         ``price_swaption_sabr``, ``price_cap_floor``,
                         ``price_bermudan_swaption`` -- all curve-input, all take an
                         explicit ``as_of_date``.
- ``calibration.py``  -- ``calibrate_sabr`` (least-squares fit of alpha/rho/nu, beta
                         fixed, to staged strike-offset ``rate_vols`` quotes) and
                         ``calibrate_hull_white`` (QuantLib ``HullWhite`` +
                         ``SwaptionHelper`` + ``LevenbergMarquardt`` fit of a/sigma to a
                         staged ATM swaption grid) -- NEW math owned by this app, not the
                         vendor (which explicitly does no calibration -- see that
                         module's docstring). Writes ``rate_model_params`` rows with
                         ``source='CALIBRATED'``.
- ``store.py``        -- ``instrument_rate_options`` table (this package's own, created
                         defensively -- see below), ``write_instrument_rate_option``,
                         ``price_and_store`` / ``price_all_and_store`` SQLite glue.
- ``book.py``         -- ``py -m engine.rates_vol.book`` manual booking CLI -- see
                         "No automated trade feed" below.

Sign convention (binding across this package)
-----------------------------------------------
``trades.quantity`` is SIGNED NOTIONAL, **+ = long the option (bought)**, not pay/receive
fixed the way ``engine/rates``'s IRS ``quantity`` sign is. A payer swaption is the right
to PAY fixed (valuable when rates rise); ``option_type`` on the option instrument encodes
PAYER/RECEIVER independently of the position's long/short sign. PV written here is the
long-holder's Black-76 (or Hull-White tree) value **times sign(quantity)** -- a short
option (quantity < 0) carries a negative PV_USD. Every pricer.py function takes the
signed ``quantity`` directly and returns already-scaled, already-signed totals; nothing
downstream needs to re-apply the sign (unlike ``engine/options``'s FX-option DELTA mark,
which is deliberately left per-unit for the ladder query to scale -- restated in
``pricer.py``).

Trade shape (flags for CLAUDE.md -- not applied here, this package does not edit
CLAUDE.md or data/ingest/schema.py; see the final report for the exact follow-up)
------------------------------------------------------------------------------------
- ``trades.product`` gains two new values: ``SWAPTION``, ``CAP_FLOOR``.
- ``instruments.asset_class`` gains ``IRS_OPTION``; ``base_ccy = quote_ccy`` = the
  notional currency; ``expiry_date`` = the option's own expiry (a cap/floor: its final
  payment date, i.e. the end of the strip -- there is no single "option expiry" for a
  strip of caplets, so the LAST payment date is used as the settle-date/staleness anchor).
- ``trades.price`` = premium fill, in notional-fraction (0.0 if unknown -- NEVER
  fabricated; a 0 premium fill is not treated as "free", it is treated as "not on file"
  and simply not compared against the computed PV for any P&L purpose this package
  performs -- this package only ever writes PV_USD/DV01_USD/VEGA/GAMMA/THETA, never a
  premium-vs-fill P&L, so the 0 sentinel does not currently bite anything here).
- Option attributes live in THIS package's own ``instrument_rate_options`` table
  (``store.py``, created defensively with ``CREATE TABLE IF NOT EXISTS`` -- not in
  ``data/ingest/schema.py``, which this package does not edit).

No automated trade feed -- manual booking only (``py -m engine.rates_vol.book``)
------------------------------------------------------------------------------------
No swaption/cap/floor trades exist in any AUTOMATED ingest path today (blotter
``Fin Type`` is one of FORWARD/CURRENCY/FUTURE/OPTION/INTEREST_RATE_SWAP -- no
rate-option kind). ``book.py``'s CLI (``py -m engine.rates_vol.book --db <path>
--trade-id ... --payoff SWAPTION|CAP|FLOOR|BERMUDAN_SWAPTION ...``) is the ONLY way a
trade currently enters this package -- it writes ``instruments``/
``instrument_rate_options``/``trades``/``trade_legs`` directly, refusing to overwrite an
existing ``trade_id``. Wiring a real ingest path (parsing a swaption/cap blotter row
into ``write_instrument_rate_option``) remains an explicit, documented follow-up for
data-ingest, not attempted here.

Curve inputs (single-curve OIS) -- replaces the earlier flat-curve approximation
(2026-09-17)
---------------------------------------------------------------------------------------
As of the 2026-09-17 re-vendoring, the vendored ``options_calc.rates`` engine accepts a
``ql.YieldTermStructureHandle`` directly (``discount_curve``/``forecast_curve``,
mutually exclusive with the flat ``discount_rate``/``forecast_rate`` it also still
supports) and an explicit ``evaluation_date`` that is ALWAYS restored on exit (see
``engine/options/vendor/options_calc/rates/_engine.py``'s "CURVE-INPUT SUPPORT" and
"EVALUATION DATE" docstring sections). This package now passes the bootstrapped
``engine.rates`` OIS ``CurveSet``'s own discount handle (``CurveSet.discount``) as BOTH
``discount_curve`` AND ``forecast_curve`` -- single-curve OIS, exactly matching
``engine/rates``'s own swap pricer's discounting/forecasting convention -- and
``evaluation_date=as_of`` explicitly, rather than deriving a flat zero rate / forward par
rate from the curve and feeding THAT to the pricer (the pre-2026-09-17 approximation,
removed from the live path; see ``inputs.py``'s ``derive_flat_rate_inputs`` for that old
behaviour, kept ONLY as a documented fallback for the regression test proving the
approximation is gone). Every option here is now priced on the curve's own full
discount-factor term structure, not one flat zero rate to expiry.

What is STILL simplified, stated plainly: single-curve OIS discounting AND forecasting
off the SAME curve (a real USD swaption's floating leg typically forecasts off a
term-SOFR curve, distinct from the OIS discounting curve -- this repo bootstraps only
OIS, Phase 1 scope, same limitation ``engine/rates`` itself has); no convexity
adjustment; no CMS measure change; no cross-currency basis. ``CurveInputs.forward_rate``
(the forward par swap rate on the option's own underlying, still derived via
``engine.rates.instruments.build_instrument(...).ql_swap.fairRate()``) is kept ONLY as
the SABR smile's evaluation point / vol-resolution key -- it is NOT fed back into the
pricing engine as a rate anymore, only the curve object is.

DV01 now comes from a curve-spread parallel bump (``pricer.py::_bump_curve``, a
``ql.ZeroSpreadedTermStructure`` fed a ``ql.SimpleQuote`` -- the same TECHNIQUE the
vendored engine's own ``finite_difference_curve_greeks`` uses internally, reimplemented
here so this package can bump BOTH the discount and forecast role at once, since they
are literally the same curve object), sign-verified to match
``engine/rates/valuation.py``'s own DV01 convention: ``NPV(+1bp bump) - NPV(base)``.

Manual-input tables and calibration (Bloomberg swaption-vol pull is a later phase,
mirroring ``engine/options``'s Phase 2 (manual) -> Phase 5a (live) progression for FX
vol)
------------------------------------------------------------------------------------------
- ``rate_vols`` -- flat lognormal (or normal, REJECTED -- see below) vol per
  (as_of, ccy, index, expiry, underlying_tenor, strike-or-ATM). ``set_manual_rate_vol``.
- ``rate_model_params`` -- SABR (alpha/beta/rho/nu) and Hull-White (a/sigma) parameters
  per (as_of, ccy, index, model), each row carrying its own ``source`` (``MANUAL`` or
  ``CALIBRATED``). ``set_rate_model_param``; ``get_rate_model_params`` prefers MANUAL
  over CALIBRATED per param (see ``inputs.py``'s module docstring for why an explicit
  desk override must never be silently superseded by an automated fit).
Both are created defensively (``CREATE TABLE IF NOT EXISTS``) in ``inputs.py``, matching
``engine/options/inputs.py``'s ``option_vols`` pattern. Nothing defaults: a trade with no
vol on file, or a Bermudan with no Hull-White parameters on file, is a structured SKIP
(``store.py``'s ``PricingOutcome``), never a fabricated mark.

``calibration.py`` (2026-09-17) fits both parameter sets FROM staged ``rate_vols`` market
data instead of requiring them to be hand-typed: ``calibrate_sabr`` (least-squares fit of
alpha/rho/nu against the vendored ``sabr_swaption_vol``, beta fixed by the caller) and
``calibrate_hull_white`` (QuantLib's own ``HullWhite``/``SwaptionHelper``/
``LevenbergMarquardt`` fit of a/sigma against a staged ATM swaption grid). Both write
``source='CALIBRATED'`` rows and both return a structured skip (never raise, never write)
when the staged data is insufficient (SABR: fewer than 3 strikes or no ATM anchor;
Hull-White: fewer than 2 ATM grid points, or a fitted sigma outside the vendored engine's
own plausible ``(0, 0.05]`` range).

**NORMAL (basis-point) vol is REJECTED, not silently converted.** The vendored engine is
lognormal Black-76 only (zero displacement); a ``rate_vols`` row quoted ``vol_type =
'NORMAL'`` is a real, distinct number (e.g. 80bp on the rate) that cannot be fed into
``sigma`` without a real normal-to-lognormal (or shifted-lognormal) conversion this
package does not implement. ``inputs.py::resolve_swaption_vol`` /
``resolve_capfloor_vol`` skip with an explicit reason rather than guessing; NORMAL rows
are also excluded (not converted) from ``calibration.py``'s SABR sample.

Numerical quirk (largely resolved upstream, 2026-09-17): QuantLib
``Settings.evaluationDate`` is a global singleton. The OLD vendored ``rates/_engine.py``
build functions used to call ``ql.Date.todaysDate()`` (the real system date, NOT an
``as_of`` parameter) and set ``ql.Settings.instance().evaluationDate`` to it on EVERY
pricer call, leaving it there -- silently reassigning the global evaluation date away
from whatever ``engine.rates.curves`` set it to when building our OIS ``CurveSet``. As of
the 2026-09-17 re-vendoring, every vendored ``rates/*`` entry point now accepts an
explicit ``evaluation_date`` and ALWAYS restores whatever it found on exit
(``evaluation_date_scope``) -- ``pricer.py`` passes ``as_of_date`` through explicitly on
every call, so this bug no longer reaches this package's own pricing calls. ``inputs.py``
still resets ``ql.Settings.instance().evaluationDate`` to ``as_of`` immediately before its
own ``CurveSet`` reads (``_forward_par_rate``) -- now belt-and-suspenders, not
load-bearing, kept because a curve read should not trust an assumed evaluation date
regardless of what any particular caller currently guarantees.

Bermudan / Hull-White model risk (explicit reviewer-pass flag, per task instructions)
------------------------------------------------------------------------------------------
``price_bermudan_swaption`` carries REAL, DOCUMENTED model risk, per the vendored
library's own ``MODELS.md``/``bermudan_swaption.py`` docstrings: one-factor Hull-White
(not two-factor G2 -- all points on the curve move perfectly correlated), a trinomial
tree with a fixed, un-converged step count. ``hw_mean_reversion``/``hw_volatility`` MAY
now come from ``calibration.py`` (fit to a co-terminal ATM swaption grid) instead of a
hand-typed guess, which narrows but does NOT remove this risk -- a one-factor model fit
to European swaptions is still evaluated for early-exercise value it was never
calibrated against, and the calibration itself has no goodness-of-fit gate beyond the
plausible-sigma-range check. Exercise dates are now the trade's OWN staged dates
(``store.py::_parse_exercise_dates``), no longer mechanically generated -- this removes
ONE source of model risk but not the others above. This package's own bump-and-reprice
Greeks for Bermudan (``pricer.py``) compound the remaining risk further: "vega" there
bumps the Hull-White model's own ``sigma``, which is NOT the market-observable lognormal
swaption vol Black-76's vega measures elsewhere in this package -- a materially
different, weaker number. **Do not feed a Bermudan-swaption
PV_USD/DV01_USD/VEGA/GAMMA/THETA mark into real P&L or hedging decisions without an
explicit reviewer pass first** -- this is a capability landing, not a production-quality
Bermudan pricer.

Other standing caveats, stated plainly for a reviewer pass
------------------------------------------------------------------------------------------
One-factor Hull-White only (no two-factor G2, see above); ``sabr.py``'s SABR is UNSHIFTED
(assumes strike and forward are both positive -- no negative-rate support, whether the
parameters are hand-typed or ``calibration.py``-fit); no automated trade feed (manual
booking only via ``book.py``, see above).

Official marks (per CLAUDE.md "Data contract -> Official marks", unchanged mapping,
this package writes into it, does not alter it)
------------------------------------------------------------------------------------------
``PV_USD`` / ``DV01_USD`` -> official source ``QL_PRICER`` (same family as
``engine/rates``, sharing the official-source designation -- both are QuantLib-bootstrap-
derived rate marks). ``VEGA`` / ``GAMMA`` / ``THETA`` -> official source
``QL_OPTIONS_PRICER`` (same as ``engine/options``'s FX-option Greeks). No new mark_type
or OFFICIAL_MARK_SOURCE entry is needed -- both already exist in
``data/ingest/schema.py`` for the other pricers in this app.
"""
from .calibration import (
    HullWhiteCalibrationResult,
    SabrCalibrationResult,
    calibrate_hull_white,
    calibrate_sabr,
)
from .inputs import (
    CurveInputs,
    FlatRateInputs,
    RateVolLookup,
    VolResolution,
    derive_curve_inputs,
    derive_flat_rate_inputs,
    get_rate_model_params,
    get_rate_vol,
    resolve_capfloor_vol,
    resolve_swaption_vol,
    set_manual_rate_vol,
    set_rate_model_param,
)
from .pricer import RateOptionResult, price_bermudan_swaption, price_cap_floor, price_swaption, price_swaption_sabr
from .store import PricingOutcome, price_all_and_store, price_and_store, write_instrument_rate_option

__all__ = [
    "HullWhiteCalibrationResult",
    "SabrCalibrationResult",
    "calibrate_hull_white",
    "calibrate_sabr",
    "CurveInputs",
    "FlatRateInputs",
    "RateVolLookup",
    "VolResolution",
    "derive_curve_inputs",
    "derive_flat_rate_inputs",
    "get_rate_model_params",
    "get_rate_vol",
    "resolve_capfloor_vol",
    "resolve_swaption_vol",
    "set_manual_rate_vol",
    "set_rate_model_param",
    "RateOptionResult",
    "price_bermudan_swaption",
    "price_cap_floor",
    "price_swaption",
    "price_swaption_sabr",
    "PricingOutcome",
    "price_all_and_store",
    "price_and_store",
    "write_instrument_rate_option",
]
