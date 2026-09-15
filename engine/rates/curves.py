"""OIS curve bootstrap: (currency, index) -> discount term structure.

Ported and simplified from the reference project's ``swapcalc/pricing/curves.py``
(Rates Swap Calculator). Phase 1 scope for this module is single-currency OIS only
(see ``engine/rates/__init__.py``): term-rate, basis and cross-currency curve building
from the reference project is deliberately NOT ported.

Unlike the reference project, this module does not depend on a `MarketDataSource` ABC:
`build_curve_set` takes a plain list of `(tenor, value)` OIS quotes directly (the glue
in `store.py` reads these from this repo's own `curve_quotes` SQLite table), since this
repo's Phase 1 scope has no term/basis/FX-spot inputs to abstract over.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import QuantLib as ql

from . import qlmap
from .conventions import CONVENTIONS, CCY_RFR
from .errors import CurveBuildError, PricingConfigError

CcyIndex = Tuple[str, str]


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


def build_curve_set(
    quotes: List[Tuple[str, float]],
    as_of: datetime.date,
    ccy: str,
    index: str = None,
    interpolation: str = "LogCubicDiscount",
) -> CurveSet:
    """Bootstrap a single self-discounted OIS curve for (ccy, index) from `quotes`
    (a list of `(tenor, value)` pairs, value = decimal par OIS rate, e.g. 0.0398 for
    3.98%). `index` defaults to the currency's canonical RFR index (`CCY_RFR`).
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

    curve = _piecewise(cal, helpers, interpolation)
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
    )
