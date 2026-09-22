"""OIS curve bootstrap: (currency, index) -> discount term structure.

Ported and simplified from the reference project's ``swapcalc/pricing/curves.py``
(Rates Swap Calculator). Phase 1 scope for this module is single-currency OIS only
(see ``engine/rates/__init__.py``): term-rate, basis and cross-currency curve building
from the reference project is deliberately NOT ported.

Unlike the reference project, this module does not depend on a `MarketDataSource` ABC:
`build_curve_set` takes a plain list of `(tenor, value)` OIS quotes directly (the glue
in `store.py` reads these from this repo's own `curve_quotes` SQLite table), since this
repo's Phase 1 scope has no term/basis/FX-spot inputs to abstract over.

Interpolation and the log-linear fallback (2026-09-22). The default is
``LogCubicDiscount`` (the reference project's choice). QuantLib's bootstrap is lazy: a
non-converging iteration only surfaces on the first ``discount()`` / ``zeroRate()``
call, which on the Bloomberg PC's pull of 2026-09-22 was inside the swap pricer and the
options pricer, so every IRS and every FX option needing the USD curve failed with
``convergence not reached after 99 iterations`` and no ``curves`` rows were written.
The cause was the evaluation date, not the quotes: with 1W / 2W / 3W / 1M pillars the
non-local log-cubic iterative bootstrap oscillates at the short end on some dates
(the 09-22 USD SOFR quotes converge at a 09-21 evaluation date and fail at 09-22 and
09-23; dropping any one of the week pillars also makes it converge), and log-linear
converges every day. ``build_curve_set`` therefore forces the bootstrap before
returning; if the requested interpolation does not converge it rebuilds with
``LogLinear``, logs a warning and records the fact on the returned ``CurveSet``
(``interpolation`` = what was actually used, ``bootstrap_note`` = a sentence naming
the currency, index, date and QuantLib's message, empty when the requested one
converged). A day that converges is untouched: same interpolation, same discount
factors. If log-linear fails too, ``CurveBuildError`` is raised: never a silent curve.
DV01 (``valuation.py``) bumps the live quotes of the same curve object, so a bumped
re-bootstrap always uses the interpolation the base curve ended up with.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import QuantLib as ql

from . import qlmap
from .conventions import CONVENTIONS, CCY_RFR
from .errors import CurveBuildError, PricingConfigError

CcyIndex = Tuple[str, str]

DEFAULT_INTERPOLATION = "LogCubicDiscount"
FALLBACK_INTERPOLATION = "LogLinear"

log = logging.getLogger(__name__)


@dataclass
class CurveSet:
    """Single-currency OIS curve set. `quotes` stores the live `ql.SimpleQuote`
    objects behind the bootstrapped curve so DV01 bucket bumping (`valuation.py`) can
    perturb one pillar at a time and rebuild.
    """

    valuation_date: datetime.date
    ccy: str
    index: str
    discount: ql.RelinkableYieldTermStructureHandle
    discount_curve: object  # concrete ql.PiecewiseXXX curve, kept for direct access
    ql_index: ql.OvernightIndex
    quotes: List[Tuple[str, ql.SimpleQuote]] = field(default_factory=list)
    curve_date: datetime.date = None
    # The interpolation actually used (the requested one, or the log-linear fallback
    # when the requested one did not converge; see module docstring).
    interpolation: str = DEFAULT_INTERPOLATION
    # "" when the requested interpolation converged; otherwise one plain sentence, e.g.
    # "USD SOFR bootstrap 2026-09-22: log-cubic did not converge (<QuantLib's
    # message>); log-linear used". Read by engine/options via getattr as well.
    bootstrap_note: str = ""

    def get_discount(self) -> ql.YieldTermStructureHandle:
        return self.discount

    def get_index(self) -> ql.OvernightIndex:
        return self.ql_index


def _quote_handle(value: float) -> Tuple[ql.SimpleQuote, ql.QuoteHandle]:
    q = ql.SimpleQuote(value)
    return q, ql.QuoteHandle(q)


def _piecewise(cal, helpers, interpolation: str):
    if interpolation == "LogLinear":
        curve = ql.PiecewiseLogLinearDiscount(0, cal, helpers, ql.Actual365Fixed())
    elif interpolation == "LogCubicDiscount":
        curve = ql.PiecewiseLogCubicDiscount(0, cal, helpers, ql.Actual365Fixed())
    else:
        raise PricingConfigError("interpolation", interpolation, ["LogCubicDiscount", "LogLinear"])
    curve.enableExtrapolation()
    return curve


def _force_bootstrap(curve) -> None:
    """Run QuantLib's lazy bootstrap now, so a non-convergence surfaces here (as the
    RuntimeError QuantLib raises) and not on the first discount() call inside a pricer.
    Reading the last node touches every pillar."""
    curve.discount(curve.maxDate())


def _interpolation_label(interpolation: str) -> str:
    return {"LogCubicDiscount": "log-cubic", "LogLinear": "log-linear"}.get(interpolation, interpolation)


def build_curve_set(
    quotes: List[Tuple[str, float]],
    as_of: datetime.date,
    ccy: str,
    index: str = None,
    interpolation: str = DEFAULT_INTERPOLATION,
) -> CurveSet:
    """Bootstrap a single self-discounted OIS curve for (ccy, index) from `quotes`
    (a list of `(tenor, value)` pairs, value = decimal par OIS rate, e.g. 0.0398 for
    3.98%). `index` defaults to the currency's canonical RFR index (`CCY_RFR`).

    The bootstrap is forced before returning. If `interpolation` does not converge the
    curve is rebuilt with `LogLinear` (a warning is logged; the returned CurveSet's
    `interpolation` and `bootstrap_note` say so); if that fails too, `CurveBuildError`.
    """
    index = index or CCY_RFR.get(ccy)
    if index is None:
        raise PricingConfigError("ccy", ccy, sorted(CCY_RFR.keys()))

    ql.Settings.instance().evaluationDate = qlmap.ql_date(as_of)
    conv = CONVENTIONS.get(ccy, index)
    cal = qlmap.calendar(conv.calendar)

    # Quote-only index (empty forwarding handle) to build the helpers against, per the
    # reference project's own bootstrap pattern (curves.py::_build_rfr_curve).
    quote_only_index = qlmap.make_index(ccy, index, conv)

    helpers = []
    stored: List[Tuple[str, ql.SimpleQuote]] = []
    for tenor, value in quotes:
        simple, handle = _quote_handle(float(value))
        stored.append((tenor, simple))
        helper = ql.OISRateHelper(
            int(conv.spot_lag),
            qlmap.period(tenor),
            handle,
            quote_only_index,
            discountingCurve=ql.YieldTermStructureHandle(),
            telescopicValueDates=True,
            paymentLag=int(conv.payment_lag),
            paymentConvention=qlmap.bdc(conv.business_day_convention),
            paymentFrequency=qlmap.frequency(conv.fixed_frequency),
            paymentCalendar=cal,
        )
        helpers.append(helper)
    if not helpers:
        raise CurveBuildError((ccy, index), "no OIS quotes supplied")

    used = interpolation
    note = ""
    curve = _piecewise(cal, helpers, interpolation)
    try:
        _force_bootstrap(curve)
    except RuntimeError as exc:  # QuantLib's own error, e.g. "convergence not reached after 99 iterations; ..."
        first_message = str(exc)
        if interpolation == FALLBACK_INTERPOLATION:
            raise CurveBuildError((ccy, index), f"{interpolation} bootstrap on {as_of.isoformat()} failed: {first_message}") from exc
        used = FALLBACK_INTERPOLATION
        note = (
            f"{ccy} {index} bootstrap {as_of.isoformat()}: {_interpolation_label(interpolation)} did not converge "
            f"({first_message}); {_interpolation_label(used)} used"
        )
        log.warning("%s", note)
        curve = _piecewise(cal, helpers, used)
        try:
            _force_bootstrap(curve)
        except RuntimeError as exc2:
            raise CurveBuildError(
                (ccy, index),
                f"{interpolation} bootstrap on {as_of.isoformat()} failed ({first_message}) and the "
                f"{used} fallback failed too ({exc2})",
            ) from exc2

    handle = ql.RelinkableYieldTermStructureHandle()
    handle.linkTo(curve)

    fwd_handle = ql.RelinkableYieldTermStructureHandle()
    fwd_handle.linkTo(curve)
    ql_index = qlmap.make_index(ccy, index, conv, fwd_handle)

    return CurveSet(
        valuation_date=as_of,
        ccy=ccy,
        index=index,
        discount=handle,
        discount_curve=curve,
        ql_index=ql_index,
        quotes=stored,
        curve_date=as_of,
        interpolation=used,
        bootstrap_note=note,
    )
