"""OIS swap pricing: NPV, par rate, DV01.

Ported and simplified from the reference project's ``swapcalc/pricing/valuation.py``
(Rates Swap Calculator). Phase 1 scope is OIS-only (see ``engine/rates/__init__.py``):
carry/roll-down and the cashflow-table builder are not ported (not needed for
`PV_USD`/`DV01_USD`/`PAR_RATE` marks, the only outputs `store.py` writes).

Sign convention (must stay internally consistent with CLAUDE.md and
``instruments.py``):
  - `npv` is QuantLib's own `ql_swap.NPV()` for the Payer/Receiver type built in
    `instruments.py::build_instrument` (`pay_fixed=True` -> `ql.Swap.Payer`). Positive
    NPV = asset to the fund. This is `PV_USD` for a USD-notional OIS swap (Phase 1
    scope has no non-USD-notional legs to convert).
  - `dv01_parallel` is the PV change for a +1bp (`BUMP = 1e-4`) parallel bump of every
    curve quote, i.e. `NPV(bumped) - NPV(base)` -- consistent with the reference
    project's own DV01 sign (a positive-DV01 payer position loses value when rates
    fall, since `NPV(bumped_up) - NPV(base) > 0` for a payer). The bump perturbs the
    live `ql.SimpleQuote`s of the base `CurveSet`'s own curve object, so the bumped
    re-bootstrap uses the interpolation the base curve was built with (flat forwards
    by default since 2026-09-22, recorded in `CurveSet.interpolation`, see curves.py):
    a bump never rebuilds through `build_curve_set` and can never flip interpolation.
  - `PnL_USD = PV_USD(t) - PV_USD(trade date)` per CLAUDE.md; nothing in this module
    computes P&L directly, `store.py`/the P&L engine layer does by differencing two
    `PV_USD` marks written here.
"""
from __future__ import annotations

import contextlib
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import QuantLib as ql

from . import qlmap
from .curves import CurveSet
from .instruments import BuiltSwap, build_instrument

BUMP = 1e-4  # 1 basis point


@dataclass
class SwapResult:
    npv: float
    par_rate: Optional[float]
    dv01_parallel: float
    dv01_buckets: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    # Net cashflows already settled on or before the valuation date, signed from the
    # fund's side (+ = received), in the swap's own currency. 0.0 for a forward-starting
    # swap. `npv + realised_cashflows` is the swap's life-to-date P&L for a swap dealt
    # with no upfront payment (2026-09-17, see store.py / CLAUDE.md "P&L conventions").
    realised_cashflows: float = 0.0


def _realised_cashflows(ql_swap: ql.OvernightIndexedSwap, pay_fixed: bool, today: ql.Date) -> float:
    """Sum of every cashflow on either leg whose payment date is on or before `today`,
    signed from the fund's side (fixed leg paid by a payer). Uses the same "on today's
    date = already occurred" rule as QuantLib's DiscountingSwapEngine (which excludes
    reference-date flows from NPV by default), so PV + realised never double-counts or
    drops a coupon. Raises (never substitutes) if a past float coupon lacks fixings."""
    signs = ((0, -1.0 if pay_fixed else 1.0), (1, 1.0 if pay_fixed else -1.0))
    total = 0.0
    for leg_no, sign in signs:
        for cf in ql_swap.leg(leg_no):
            if cf.date() <= today:
                total += sign * cf.amount()
    return total


@contextlib.contextmanager
def _bumped_quotes(curve_set: CurveSet, bp: float, tenor: Optional[str] = None):
    touched = []
    try:
        for t, quote in curve_set.quotes:
            if tenor is not None and t != tenor:
                continue
            old = quote.value()
            quote.setValue(old + bp)
            touched.append((quote, old))
        yield
    finally:
        for quote, old in touched:
            quote.setValue(old)


def price_swap(
    curve_set: CurveSet,
    effective_date: datetime.date,
    maturity_date: datetime.date,
    fixed_rate: float,
    notional: float,
    pay_fixed: bool,
) -> SwapResult:
    today = qlmap.ql_date(curve_set.valuation_date)
    ql.Settings.instance().evaluationDate = today
    built = build_instrument(curve_set, effective_date, maturity_date, fixed_rate, notional, pay_fixed)
    ql_swap = built.ql_swap

    npv = ql_swap.NPV()  # 0.0 once the swap has expired (QuantLib's own expired-instrument rule)
    realised = _realised_cashflows(ql_swap, pay_fixed, today)

    try:
        par_rate = ql_swap.fairRate()
    except (RuntimeError, AttributeError):
        par_rate = None

    dv01_parallel = 0.0
    with _bumped_quotes(curve_set, BUMP):
        dv01_parallel = ql_swap.NPV() - npv

    dv01_buckets: Dict[str, float] = {}
    for tenor, _ in curve_set.quotes:
        with _bumped_quotes(curve_set, BUMP, tenor=tenor):
            bumped = ql_swap.NPV()
        dv01_buckets["{0}/{1}:{2}".format(curve_set.ccy, curve_set.index, tenor)] = bumped - npv

    return SwapResult(npv=npv, par_rate=par_rate, dv01_parallel=dv01_parallel, dv01_buckets=dv01_buckets,
                      realised_cashflows=realised)
