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
- ``inputs.py``  -- curve-rate derivation (OIS CurveSet -> flat discount/forecast rate),
                    manual vol (``rate_vols``) and model-parameter (``rate_model_params``)
                    lookup, resolved-vol precedence (flat -> SABR -> skip).
- ``pricer.py``  -- scalar wrappers over the vendored pricers, unit-notional in, then
                    scaled by signed trade quantity: ``price_swaption``,
                    ``price_swaption_sabr``, ``price_cap_floor``, ``price_bermudan_swaption``.
- ``store.py``   -- ``instrument_rate_options`` table (this package's own, created
                    defensively -- see below), ``write_instrument_rate_option``,
                    ``price_and_store`` / ``price_all_and_store`` SQLite glue.

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

No swaption/cap/floor trades exist in any ingest path today (blotter ``Fin Type`` is one
of FORWARD/CURRENCY/FUTURE/OPTION/INTEREST_RATE_SWAP -- no rate-option kind). This phase
lands the CAPABILITY -- trade shape, pricer, store, tests -- with no live trades to price
yet; wiring an ingest path (parsing a swaption/cap blotter row into
``write_instrument_rate_option``) is an explicit, documented follow-up for data-ingest,
not attempted here.

Flat-curve approximation (real limitation, stated plainly)
---------------------------------------------------------------
The vendored ``options_calc.rates`` engine takes FLAT ``discount_rate``/``forecast_rate``
inputs -- it does not accept a bootstrapped curve object (see
``engine/options/vendor/options_calc/rates/_engine.py``'s own "MULTI-CURVE SUPPORT"
docstring section: "BOTH curves are still flat ... not bootstrapped from real market
instruments"). This package bridges that gap by DERIVING those two flat numbers, per
trade, from the correctly-bootstrapped ``engine.rates`` OIS ``CurveSet``:
  - ``forecast_rate`` = the forward-starting swap's own fair/par rate on the underlying
    (``engine.rates.instruments.build_instrument(...).ql_swap.fairRate()``), i.e. the
    genuine curve-implied forward swap rate for this option's own underlying tenor.
  - ``discount_rate`` = the curve's own continuously-compounded zero rate
    (``Actual365Fixed``) from ``as_of`` to the option's expiry.
The result is Black-76 priced ON THE CORRECTLY-BOOTSTRAPPED FORWARD, but discounted with
a single flat zero rate rather than the curve's own full discount-factor term structure
for every intermediate cashflow the way ``engine/rates``'s swap pricer does. This is
"good enough for risk" (a correct forward, correct order-of-magnitude discounting) but
explicitly **NOT good enough for a mid quote** -- a real swaption desk discounts every
leg cashflow off the actual curve, not one flat zero rate to expiry. Remove this
approximation only if/when the vendored ``options_calc.rates`` engine itself grows a
real curve-object input (tracked as a vendor-upstream follow-up, see the final report --
this package does not patch the vendored copy to add one, per this repo's "vendored
as-is" rule).

A related, separate limitation: the underlying swap used to derive ``forecast_rate`` is
built as an OVERNIGHT-INDEXED (OIS) swap off ``engine/rates``'s Phase 1 OIS curve (the
only curve this repo bootstraps) -- a real USD swaption's underlying is typically a
term-SOFR swap, not an OIS swap. Treated here as an economically reasonable stand-in
for a first-pass forward-rate estimate, not a claim that OIS and term-SOFR forwards are
identical.

Manual-input tables (this phase's vol/model-parameter source -- Bloomberg swaption-vol
pull is a later phase, mirroring ``engine/options``'s Phase 2 (manual) -> Phase 5a (live)
progression for FX vol)
------------------------------------------------------------------------------------------
- ``rate_vols`` -- flat lognormal (or normal, REJECTED -- see below) vol per
  (as_of, ccy, index, expiry, underlying_tenor, strike-or-ATM). ``set_manual_rate_vol``.
- ``rate_model_params`` -- SABR (alpha/beta/rho/nu) and Hull-White (a/sigma) parameters
  per (as_of, ccy, index, model). ``set_rate_model_param``.
Both are created defensively (``CREATE TABLE IF NOT EXISTS``) in ``inputs.py``, matching
``engine/options/inputs.py``'s ``option_vols`` pattern. Nothing defaults: a trade with no
vol on file, or a Bermudan with no Hull-White parameters on file, is a structured SKIP
(``store.py``'s ``PricingOutcome``), never a fabricated mark.

**NORMAL (basis-point) vol is REJECTED, not silently converted.** The vendored engine is
lognormal Black-76 only (zero displacement); a ``rate_vols`` row quoted ``vol_type =
'NORMAL'`` is a real, distinct number (e.g. 80bp on the rate) that cannot be fed into
``sigma`` without a real normal-to-lognormal (or shifted-lognormal) conversion this
package does not implement. ``inputs.py::resolve_swaption_vol`` /
``resolve_capfloor_vol`` skip with an explicit reason rather than guessing.

Numerical quirk: QuantLib ``Settings.evaluationDate`` is a global singleton, and the
vendored ``rates/_engine.py`` build functions call ``ql.Date.todaysDate()`` (the real
system date, NOT an ``as_of`` parameter) and then set
``ql.Settings.instance().evaluationDate`` to it on EVERY pricer call -- this silently
reassigns the global evaluation date away from whatever ``engine.rates.curves`` set it to
when building our OIS ``CurveSet``. Reading anything off a cached ``CurveSet`` (zero
rate, forward fair rate) AFTER any vendored ``rates.*`` pricer call has run, without
resetting the evaluation date back to ``as_of`` first, silently uses the wrong reference
date. ``inputs.py`` guards every such read by resetting
``ql.Settings.instance().evaluationDate`` to ``as_of`` immediately beforehand -- see
``inputs.py``'s own docstring for the guard. This does not affect the vendored pricers
themselves (they always force their own "today"); it only affects OUR OWN reads of the
bootstrapped ``CurveSet``, which is why the guard lives here and not upstream.

Bermudan / Hull-White model risk (explicit reviewer-pass flag, per task instructions)
------------------------------------------------------------------------------------------
``price_bermudan_swaption`` carries REAL, DOCUMENTED model risk, per the vendored
library's own ``MODELS.md``/``bermudan_swaption.py`` docstrings: one-factor Hull-White
(not two-factor G2 -- all points on the curve move perfectly correlated), UNCALIBRATED
``hw_mean_reversion``/``hw_volatility`` (supplied directly via ``rate_model_params``, not
fit to a co-terminal European swaption basket), a trinomial tree with a fixed,
un-converged step count, and mechanically-generated (not deal-schedule-read) exercise
dates. This package's own bump-and-reprice Greeks for Bermudan (``pricer.py``) compound
that risk further: "vega" there bumps the Hull-White model's own ``sigma``, which is NOT
the market-observable lognormal swaption vol Black-76's vega measures elsewhere in this
package -- a materially different, weaker number. **Do not feed a Bermudan-swaption
PV_USD/DV01_USD/VEGA/GAMMA/THETA mark into real P&L or hedging decisions without an
explicit reviewer pass first** -- this is a capability landing, not a production-quality
Bermudan pricer.

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
from .inputs import (
    CurveInputs,
    RateVolLookup,
    VolResolution,
    derive_curve_inputs,
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
    "CurveInputs",
    "RateVolLookup",
    "VolResolution",
    "derive_curve_inputs",
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
