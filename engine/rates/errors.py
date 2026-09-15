"""Pricing errors, ported near-verbatim from the reference project's
``swapcalc/pricing/errors.py`` (Rates Swap Calculator)."""
from __future__ import annotations

from typing import List


class PricingError(Exception):
    """Base error for the pricing package."""


class PricingConfigError(PricingError):
    def __init__(self, field: str, value: object, allowed: List[str]) -> None:
        self.field = field
        self.value = value
        self.allowed = list(allowed)
        super().__init__(
            "Unknown {0!r}: {1!r}. Allowed: {2}".format(field, value, ", ".join(str(a) for a in self.allowed))
        )


class CurveBuildError(PricingError):
    def __init__(self, key: object, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__("Failed to build curve {0!r}: {1}".format(key, reason))


class FixingMissingError(PricingError):
    def __init__(self, index_name: str, date: object) -> None:
        self.index_name = index_name
        self.date = date
        super().__init__("Missing fixing for index {0!r} on {1}".format(index_name, date))
