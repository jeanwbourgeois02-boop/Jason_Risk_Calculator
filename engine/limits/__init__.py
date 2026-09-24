"""Margin and limits (margin-limits lane, CLAUDE.md "Commodity conversion plan", Phase 5).

- ``margin_estimate(conn, as_of)``: an ESTIMATE of the initial margin the commodity book ties
  up, per contract month, root, sector and book, with spread credits (``margin.py``). Every
  figure carries ``basis`` = "estimate (config/limits.yaml), not exchange SPAN": the app has no
  exchange margin feed and never presents a rate as the exchange's or the clearer's.
- ``limit_checks(conn, as_of)``: the book against the desk's own limits (gross lots, gross USD,
  net USD per commodity and sector, lots per contract month) and the exchanges' position limits
  (spot month, single month, all months) (``checks.py``).
- ``load_limits``: ``config/limits.yaml``, checked (``config.py``). Every rate and limit there
  is the user's; an unset rate makes the commodity n/a, an unset limit NOT_SET.

Positions come from ``engine.curve.curve_positions`` and spreads from
``engine.spreads.book_spreads``; nothing here reads a mark or re-values a trade.
"""

from engine.limits.checks import BREACH, LEVELS, NA, NOT_SET, OK, WARN, limit_checks
from engine.limits.config import DEFAULT_LIMITS_PATH, LimitsConfig, LimitsConfigError, load_limits
from engine.limits.margin import BASIS, credit_key, margin_estimate

__all__ = [
    "BASIS", "BREACH", "DEFAULT_LIMITS_PATH", "LEVELS", "LimitsConfig", "LimitsConfigError", "NA", "NOT_SET",
    "OK", "WARN", "credit_key", "limit_checks", "load_limits", "margin_estimate",
]
