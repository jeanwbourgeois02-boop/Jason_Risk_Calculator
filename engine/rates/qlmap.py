"""conventions strings -> QuantLib objects.

Ported near-verbatim from the reference project's ``swapcalc/pricing/qlmap.py``
(Rates Swap Calculator), trimmed to the currencies/index kinds this module's Phase 1
scope needs (single-currency OIS: USD/EUR/GBP/JPY/CHF/CAD/AUD). Every function here is
pure (no global state) and raises `PricingConfigError` on an unrecognised string, naming
the allowed values. Nothing outside this module should construct a `ql.Calendar`,
`ql.DayCounter`, `ql.Period`, business day convention or index directly from a
convention string.
"""
from __future__ import annotations

import datetime
import re
from typing import List, Optional

import QuantLib as ql

from .errors import PricingConfigError

_CALENDAR_BUILDERS = {
    "UnitedStates/SOFR": lambda: ql.UnitedStates(ql.UnitedStates.SOFR),
    "UnitedStates/FederalReserve": lambda: ql.UnitedStates(ql.UnitedStates.FederalReserve),
    "UnitedStates/GovernmentBond": lambda: ql.UnitedStates(ql.UnitedStates.GovernmentBond),
    "TARGET": lambda: ql.TARGET(),
    "UnitedKingdom": lambda: ql.UnitedKingdom(),
    "Japan": lambda: ql.Japan(),
    "Switzerland": lambda: ql.Switzerland(),
    "Canada": lambda: ql.Canada(),
    "Australia": lambda: ql.Australia(),
}

_DAY_COUNTERS = {
    "Actual360": lambda: ql.Actual360(),
    "Actual365Fixed": lambda: ql.Actual365Fixed(),
    "Thirty360": lambda: ql.Thirty360(ql.Thirty360.BondBasis),
    "Thirty360E": lambda: ql.Thirty360(ql.Thirty360.European),
    "ActualActual": lambda: ql.ActualActual(ql.ActualActual.ISDA),
}

_FREQUENCIES = {
    "Annual": ql.Annual,
    "Semiannual": ql.Semiannual,
    "Quarterly": ql.Quarterly,
    "Monthly": ql.Monthly,
    "Once": ql.Once,
}

_BDC = {
    "Following": ql.Following,
    "ModifiedFollowing": ql.ModifiedFollowing,
    "Preceding": ql.Preceding,
    "Unadjusted": ql.Unadjusted,
}

_PERIOD_RE = re.compile(r"^(\d+)([DWMY])$")
_UNIT_MAP = {
    "D": ql.Days,
    "W": ql.Weeks,
    "M": ql.Months,
    "Y": ql.Years,
}


def calendar(names: List[str]) -> ql.Calendar:
    if not names:
        raise PricingConfigError("calendar", names, sorted(_CALENDAR_BUILDERS.keys()))
    built = []
    for name in names:
        builder = _CALENDAR_BUILDERS.get(name)
        if builder is None:
            raise PricingConfigError("calendar", name, sorted(_CALENDAR_BUILDERS.keys()))
        built.append(builder())
    if len(built) == 1:
        return built[0]
    if len(built) == 2:
        return ql.JointCalendar(built[0], built[1], ql.JoinHolidays)
    raise PricingConfigError("calendar", names, ["at most 2 calendar names supported"])


def day_counter(name: str) -> ql.DayCounter:
    builder = _DAY_COUNTERS.get(name)
    if builder is None:
        raise PricingConfigError("day_counter", name, sorted(_DAY_COUNTERS.keys()))
    return builder()


def frequency(name: str) -> int:
    if name not in _FREQUENCIES:
        raise PricingConfigError("frequency", name, sorted(_FREQUENCIES.keys()))
    return _FREQUENCIES[name]


def bdc(name: str) -> int:
    if name not in _BDC:
        raise PricingConfigError("business_day_convention", name, sorted(_BDC.keys()))
    return _BDC[name]


def period(tenor: str) -> ql.Period:
    tenor = tenor.strip().upper()
    m = _PERIOD_RE.match(tenor)
    if not m:
        raise PricingConfigError("period", tenor, ["e.g. 1W, 18M, 2Y"])
    number = int(m.group(1))
    unit = _UNIT_MAP[m.group(2)]
    return ql.Period(number, unit)


def ql_date(d: datetime.date) -> ql.Date:
    return ql.Date(d.day, d.month, d.year)


def py_date(d: ql.Date) -> datetime.date:
    return datetime.date(d.year(), d.month(), d.dayOfMonth())


def make_index(
    ccy: str,
    index: str,
    conv,
    forwarding: Optional[ql.YieldTermStructureHandle] = None,
) -> ql.Index:
    """Build the QuantLib OIS index for (ccy, index) purely from `conv` (see
    `conventions.py`). `forwarding` is an (optionally empty) `ql.YieldTermStructureHandle`;
    pass an empty handle to obtain a "quote only" index usable for helper construction
    before the curve it forwards off exists. Phase 1 scope is OIS only (see module
    docstring); TERM/basis index kinds from the reference project are not ported.
    """
    handle = forwarding if forwarding is not None else ql.YieldTermStructureHandle()
    cal = calendar(conv.calendar)
    ccy_obj = _ql_currency(ccy)
    if conv.type != "OIS":
        raise PricingConfigError("index.type", conv.type, ["OIS"])
    return ql.OvernightIndex(
        index,
        int(conv.fixing_lag),
        ccy_obj,
        cal,
        day_counter(conv.float_day_count),
        handle,
    )


_CURRENCY_BUILDERS = {
    "USD": ql.USDCurrency,
    "EUR": ql.EURCurrency,
    "GBP": ql.GBPCurrency,
    "JPY": ql.JPYCurrency,
    "CHF": ql.CHFCurrency,
    "CAD": ql.CADCurrency,
    "AUD": ql.AUDCurrency,
}


def _ql_currency(ccy: str) -> ql.Currency:
    builder = _CURRENCY_BUILDERS.get(ccy)
    if builder is None:
        raise PricingConfigError("currency", ccy, sorted(_CURRENCY_BUILDERS.keys()))
    return builder()
