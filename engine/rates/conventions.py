"""OIS index conventions, transcribed from the reference project's
``swapcalc/config/conventions.yaml`` (Rates Swap Calculator), OIS entries only -- this
module's Phase 1 scope is single-currency OIS (see ``engine/rates/__init__.py``).

Hard-coded here rather than read from a yaml file: this repo has no equivalent
``conventions.yaml`` and none is being added (out of ownership scope for this task), so
the seven OIS conventions in scope are transcribed directly and kept in a tiny dict
rather than introducing a new config file format for one table. If term-rate/basis/XCCY
scope is ever added, port the fuller yaml-driven ``Conventions`` class from the
reference project's ``swapcalc/parser/conventions.py`` instead of growing this dict
ad hoc.

Each entry's `source` field documents where the reference project attributed the
convention (Bloomberg SWPM default / ISDA); this is carried over unchanged and is
UNVERIFIED against a live terminal from this repo.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, Tuple

from .errors import PricingConfigError

CcyIndex = Tuple[str, str]

# CCY -> canonical RFR (OIS) index name, Phase 1 scope only.
CCY_RFR: Dict[str, str] = {
    "USD": "SOFR",
    "EUR": "ESTR",
    "GBP": "SONIA",
    "JPY": "TONA",
    "CHF": "SARON",
    "CAD": "CORRA",
    "AUD": "AONIA",
}

_OIS_CONVENTIONS: Dict[CcyIndex, dict] = {
    ("USD", "SOFR"): dict(
        type="OIS", fixed_day_count="Actual360", float_day_count="Actual360",
        fixed_frequency="Annual", calendar=["UnitedStates/SOFR"], payment_lag=2,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=2,
        short_swap_single_payment=True,
    ),
    ("EUR", "ESTR"): dict(
        type="OIS", fixed_day_count="Actual360", float_day_count="Actual360",
        fixed_frequency="Annual", calendar=["TARGET"], payment_lag=1,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=2,
        short_swap_single_payment=True,
    ),
    ("GBP", "SONIA"): dict(
        type="OIS", fixed_day_count="Actual365Fixed", float_day_count="Actual365Fixed",
        fixed_frequency="Annual", calendar=["UnitedKingdom"], payment_lag=0,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=0,
        short_swap_single_payment=True,
    ),
    ("JPY", "TONA"): dict(
        type="OIS", fixed_day_count="Actual365Fixed", float_day_count="Actual365Fixed",
        fixed_frequency="Annual", calendar=["Japan"], payment_lag=2,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=2,
        short_swap_single_payment=True,
    ),
    ("CHF", "SARON"): dict(
        type="OIS", fixed_day_count="Actual360", float_day_count="Actual360",
        fixed_frequency="Annual", calendar=["Switzerland"], payment_lag=2,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=2,
        short_swap_single_payment=True,
    ),
    ("CAD", "CORRA"): dict(
        type="OIS", fixed_day_count="Actual365Fixed", float_day_count="Actual365Fixed",
        fixed_frequency="Annual", calendar=["Canada"], payment_lag=1,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=1,
        short_swap_single_payment=True,
    ),
    ("AUD", "AONIA"): dict(
        type="OIS", fixed_day_count="Actual365Fixed", float_day_count="Actual365Fixed",
        fixed_frequency="Annual", calendar=["Australia"], payment_lag=1,
        fixing_lag=0, business_day_convention="ModifiedFollowing", spot_lag=1,
        short_swap_single_payment=True,
    ),
}


class Conventions:
    """Minimal stand-in for the reference project's yaml-driven `Conventions` class,
    exposing the same `.get(ccy, index)` -> attribute-access object interface used by
    `curves.py`/`instruments.py` (`conv.calendar`, `conv.fixed_day_count`, ...)."""

    def get(self, ccy: str, index: str) -> SimpleNamespace:
        entry = _OIS_CONVENTIONS.get((ccy, index))
        if entry is None:
            raise PricingConfigError(
                "index", (ccy, index), [f"{c}/{i}" for c, i in sorted(_OIS_CONVENTIONS)]
            )
        return SimpleNamespace(**entry)


CONVENTIONS = Conventions()
