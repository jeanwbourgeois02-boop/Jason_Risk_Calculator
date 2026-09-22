"""Shared fixtures."""
import pytest


@pytest.fixture
def strict_marks(monkeypatch):
    """Pin the near-marks rule off (engine.pnl.valuation._mark_near -> the exact lookup)
    for tests of what sits behind it: the fill (engine/pnl/reference.py), the reference
    step-back and the "(sample)" cell only ever act on a trade the near rule could not
    price, so their fixtures of "a day with no mark" need the rule out of the way (user
    decision 2026-09-22: "if there is no price, we should always interpolate/extrapolate
    with near marks")."""
    from engine.pnl import valuation
    monkeypatch.setattr(valuation, "_mark_near", valuation._mark_at)
