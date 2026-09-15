"""IRS trade -> QuantLib OvernightIndexedSwap instrument.

Ported and simplified from the reference project's ``swapcalc/pricing/instruments.py``
(Rates Swap Calculator), OIS-only (see ``engine/rates/__init__.py``); the FIXED_FLOAT/
BASIS/XCCY instrument branches there are not ported.

Sign convention (documented once here, matching CLAUDE.md and ``store.py``): this
module takes `pay_fixed: bool` directly rather than a `Direction` enum. The caller
(`store.py::price_and_store`) derives it from `trades.quantity`: `quantity > 0` means
pay fixed (CLAUDE.md "notional (IRS: + = pay fixed)"), so `pay_fixed = quantity > 0`.
`pay_fixed=True` builds a `ql.Swap.Payer` OIS swap (QuantLib pays the fixed leg,
receives the float leg); `ql_swap.NPV()` is then positive when that position is an
asset to the fund -- i.e. NPV/PV follows QuantLib's own Payer/Receiver sign, which
already agrees with "positive quantity = pay fixed = benefits when rates rise".
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

import QuantLib as ql

from . import qlmap
from .conventions import CONVENTIONS
from .curves import CurveSet

_TYPE = {True: ql.Swap.Payer, False: ql.Swap.Receiver}


@dataclass
class BuiltSwap:
    ql_swap: ql.OvernightIndexedSwap
    conv: object
    fixed_schedule: ql.Schedule
    is_forward_starting: bool


def _make_schedule(effective, maturity, freq, cal, convention) -> ql.Schedule:
    return ql.Schedule(
        qlmap.ql_date(effective),
        qlmap.ql_date(maturity),
        freq,
        cal,
        convention,
        convention,
        ql.DateGeneration.Backward,
        False,
    )


def _ois_schedule_period(conv, effective: datetime.date, maturity: datetime.date) -> ql.Period:
    term_days = (maturity - effective).days
    if getattr(conv, "short_swap_single_payment", False) and term_days <= 366:
        return ql.Period(ql.Once)
    return ql.Period(qlmap.frequency(conv.fixed_frequency))


def build_instrument(
    curve_set: CurveSet,
    effective_date: datetime.date,
    maturity_date: datetime.date,
    fixed_rate: float,
    notional: float,
    pay_fixed: bool,
) -> BuiltSwap:
    conv = CONVENTIONS.get(curve_set.ccy, curve_set.index)
    cal = qlmap.calendar(conv.calendar)
    convention = qlmap.bdc(conv.business_day_convention)
    discount = curve_set.get_discount()
    ois_index = curve_set.get_index()

    freq = _ois_schedule_period(conv, effective_date, maturity_date)
    fixed_schedule = _make_schedule(effective_date, maturity_date, freq, cal, convention)
    fixed_dc = qlmap.day_counter(conv.fixed_day_count)

    ql_swap = ql.OvernightIndexedSwap(
        _TYPE[pay_fixed],
        float(notional),
        fixed_schedule,
        float(fixed_rate),
        fixed_dc,
        ois_index,
        0.0,
        int(conv.payment_lag),
        convention,
        cal,
        True,
        ql.RateAveraging.Compound,
    )
    engine = ql.DiscountingSwapEngine(discount)
    ql_swap.setPricingEngine(engine)

    is_forward_starting = effective_date > curve_set.valuation_date
    return BuiltSwap(ql_swap, conv, fixed_schedule, is_forward_starting)
