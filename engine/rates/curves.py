"""OIS curve bootstrap: (currency, index) -> discount term structure.

Ported and simplified from the reference project's ``swapcalc/pricing/curves.py``
(Rates Swap Calculator). Phase 1 scope for this module is single-currency OIS only
(see ``engine/rates/__init__.py``): term-rate, basis and cross-currency curve building
from the reference project is deliberately NOT ported.

Unlike the reference project, this module does not depend on a `MarketDataSource` ABC:
`build_curve_set` takes a plain list of `(tenor, value)` OIS quotes directly (the glue
in `store.py` reads these from this repo's own `curve_quotes` SQLite table), since this
repo's Phase 1 scope has no term/basis/FX-spot inputs to abstract over.

Interpolation: flat forwards by default (user decision 2026-09-22: "yes switch to flat
forwards and rerun the past days"). ``build_curve_set`` builds
``ql.PiecewiseLogLinearDiscount`` (log-linear in the discount factor, i.e. a constant
overnight forward between pillars, the market standard for an OIS curve) unless
``interpolation="LogCubicDiscount"`` is asked for explicitly, which is the reference
project's monotone log-cubic and the default until 2026-09-22. Why it changed: on the
Bloomberg PC's 2026-09-22 pull the USD SOFR log-cubic bootstrap raised ``convergence
not reached after 99 iterations`` and every IRS (18) and every FX option needing the
USD curve (16 of 25) went unpriced. The cause is the evaluation date, not the quotes:
with the 1W / 2W / 3W pillars QuantLib 1.43's non-local log-cubic iteration oscillates
at the short end on the 22 and 23 September evaluation dates (the same quotes converge
on 09-18, 09-21, 09-24, 09-25; dropping any one week pillar also converges), and
log-linear converges on every date. On 2026-09-21 the 18-swap book's PV moved
9,895,018 -> 10,019,751 and DV01 318,568 -> 318,663 under the switch; the user accepted
that. Every path that builds a curve (``store.bootstrap_and_store``, hence
``price_and_store`` / ``price_all_and_store`` / ``recalc_on_file``, and the callers in
``engine/options/rates.py`` and ``engine/rates_vol/``) calls ``build_curve_set`` with
no interpolation argument and so follows the default. DV01 (``valuation.py``) bumps the
live quotes of the same curve object, so a bumped re-bootstrap always uses the
interpolation the base curve was built with.

QuantLib's bootstrap is lazy: a non-converging iteration only surfaces on the first
``discount()`` / ``zeroRate()`` call, which is why the 2026-09-22 failure appeared
inside the pricers. ``build_curve_set`` therefore forces the bootstrap before returning
and raises ``CurveBuildError`` (naming the currency, index, date, interpolation and
QuantLib's own message) when it does not converge: never a silent curve, and no
fallback to another interpolation (the explicit log-cubic request fails on those dates
rather than being quietly replaced; the log-linear fallback that existed for one day,
2026-09-22, was retired with the switch). ``CurveSet.interpolation`` records what was
built; ``CurveSet.bootstrap_note`` stays for the readers that show it
(``store.price_all_and_store``'s ``note``, the pull status, ``engine/options``) and is
always "" now.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import List, Tuple

import QuantLib as ql

from . import qlmap
from .conventions import CONVENTIONS, CCY_RFR
from .errors import CurveBuildError, PricingConfigError

CcyIndex = Tuple[str, str]

# Flat forwards (log-linear discount), user decision 2026-09-22; see module docstring.
DEFAULT_INTERPOLATION = "LogLinear"
INTERPOLATIONS = ("LogLinear", "LogCubicDiscount")

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
    # The interpolation the curve was built with ("LogLinear" = flat forwards, the
    # default since 2026-09-22; "LogCubicDiscount" only when asked for explicitly).
    interpolation: str = DEFAULT_INTERPOLATION
    # Always "" since the log-linear fallback was retired (2026-09-22): a curve that does
    # not converge raises instead. Kept because store.price_all_and_store's `note`, the
    # pull status and engine/options (via getattr) read it.
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
        raise PricingConfigError("interpolation", interpolation, list(INTERPOLATIONS))
    curve.enableExtrapolation()
    return curve


def _force_bootstrap(curve) -> None:
    """Run QuantLib's lazy bootstrap now, so a non-convergence surfaces here (as the
    RuntimeError QuantLib raises) and not on the first discount() call inside a pricer.
    Reading the last node touches every pillar."""
    curve.discount(curve.maxDate())


def _interpolation_label(interpolation: str) -> str:
    return {"LogCubicDiscount": "log-cubic", "LogLinear": "log-linear (flat forwards)"}.get(interpolation, interpolation)


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

    `interpolation` is `"LogLinear"` (flat forwards, the default) or
    `"LogCubicDiscount"` (see module docstring). The bootstrap is forced before
    returning; if it does not converge, `CurveBuildError` is raised with QuantLib's
    message, never a fallback to another interpolation.
    """
    index = index or CCY_RFR.get(ccy)
    if index is None:
        raise PricingConfigError("ccy", ccy, sorted(CCY_RFR.keys()))
    if interpolation not in INTERPOLATIONS:
        raise PricingConfigError("interpolation", interpolation, list(INTERPOLATIONS))

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

    curve = _piecewise(cal, helpers, interpolation)
    try:
        _force_bootstrap(curve)
    except RuntimeError as exc:  # QuantLib's own error, e.g. "convergence not reached after 99 iterations; ..."
        message = (
            f"{_interpolation_label(interpolation)} bootstrap on {as_of.isoformat()} did not converge: {exc}"
        )
        log.warning("%s %s %s", ccy, index, message)
        raise CurveBuildError((ccy, index), message) from exc

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
        interpolation=interpolation,
        bootstrap_note="",
    )
