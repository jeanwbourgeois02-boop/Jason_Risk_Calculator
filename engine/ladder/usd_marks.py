"""USD-per-unit marks for cash-ladder legs, by currency and value date.

User's cash-ladder spec (2026-09-18, "Marks and the USD ladder"): each leg gets one
mark, ``usd_per_unit_at(ccy, value_date)``, and its USD equivalent is
``amount x mark``. The rule, reproduced here on this app's own marks table:

  * spot date = the app's one spot-date rule, `engine.pnl.calendar.spot_date`, per pair
    (since 2026-09-22, reviewer finding m-6, user yes): as-of + 2 weekdays, + 1 for
    USDCAD / USDTRY / USDPHP / USDRUB, rolled forward off a config/holidays.txt holiday.
    The spec's own "as-of + 2 weekdays, no holidays" put a USDCAD leg at T+2 at spot here
    and one day along the curve in the Blotter; the P&L's curve pillar and the backfill
    already used the shared rule, so the Ladder now reads the same date;
  * value date <= spot date -> SPOT;
  * value date > spot date -> the forward outright for that date: an official
    FWD_OUTRIGHT row at exactly that date when Bloomberg (or the official BBG_INTERP
    fallback) wrote one, otherwise linear interpolation between the two bracketing
    official outrights, with spot at the spot date as the first pillar; flat beyond
    the last pillar; no forward pillars at all for the pair -> spot;
  * usd per unit = outright when the currency is the base of its USD pair (EURUSD ->
    EUR), 1 / outright when it is the quote (USDJPY -> JPY); USD = 1. A cross leg
    (EURSEK) uses each currency's own USD curve; there is no cross curve.

Undiscounted throughout. Local amounts never change; only the mark moves. This is the
mark for the ladder's USD-equivalent cells only: the per-currency DELTA rows keep the
SPOT mark (CLAUDE.md, "spot for delta"), and P&L stays in engine/pnl.

Every returned entry names its `basis` so a caller can say where the number came from
and never silently pass a spot-for-forward substitute off as a quote: 'spot' (on or
before the spot date), 'outright' (exact official row), 'interpolated', 'flat beyond
last tenor', or 'spot (no forward curve)'. A currency with no SPOT entry in `rates`
gets no entry here at all (NaN downstream, exactly like a missing rate).

Every currency follows this one rule (2026-09-24, commodity conversion Phase 2: NDFs
left the app, so the 1M NDF basis of 2026-09-21 is gone and KRW, BRL, ... are ordinary
currencies at their official spot and outrights).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Dict, Iterable, Mapping, Tuple

from engine.pnl import calendar as _calendar

BASIS_SPOT = "spot"
BASIS_OUTRIGHT = "outright"
BASIS_INTERPOLATED = "interpolated"
BASIS_FLAT = "flat beyond last tenor"
BASIS_NO_CURVE = "spot (no forward curve)"

_FWD_SQL = """
SELECT settle_date, value FROM marks_official
WHERE instrument_id = :pair AND mark_type = 'FWD_OUTRIGHT' AND as_of_date = :as_of AND value > 0
ORDER BY settle_date
"""


def spot_date(as_of: str, pair: str = "", holidays=None) -> str:
    """ISO spot value date of `pair` dealt on `as_of`, from the app's one spot-date rule
    (`engine.pnl.calendar.spot_date`): + 2 weekdays, + 1 for USDCAD / USDTRY / USDPHP /
    USDRUB, then rolled forward while the date is a weekend or a config/holidays.txt
    holiday (`load_holidays()` when `holidays` is None). `pair=""` keeps the plain T+2
    rule for a caller that has no pair; every "spot or forward" decision in this module
    passes the currency's USD pair."""
    return _calendar.spot_date(as_of, pair, holidays).isoformat()


def _quoted_at(pillars: list, day: str, spot_day: str, spot_value: float) -> Tuple[float, str]:
    """Quoted (pair) rate for `day` from the spec's pillar rule. `pillars` are
    (settle_date, value) sorted ascending and all strictly after `spot_day`."""
    if day <= spot_day:
        return spot_value, BASIS_SPOT
    if not pillars:
        return spot_value, BASIS_NO_CURVE
    points = [(spot_day, spot_value)] + pillars
    for d, v in points:
        if d == day:
            return v, BASIS_OUTRIGHT
    if day > points[-1][0]:
        return points[-1][1], BASIS_FLAT
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if d0 < day < d1:
            t0, t1, t = (dt.date.fromisoformat(x).toordinal() for x in (d0, d1, day))
            w = (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * w, BASIS_INTERPOLATED
    return spot_value, BASIS_NO_CURVE  # unreachable: `day` is inside [spot_day, last]


def forward_usd_rates(conn: sqlite3.Connection, rates: Mapping[str, Mapping],
                      needed: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], dict]:
    """{(ccy, value_date): {"rate": USD per unit, "quoted": pair rate, "basis": ...,
    "pair": ...}} for every (ccy, ISO date) in `needed` whose currency has a SPOT entry
    in `rates` (the `data.bloomberg.live.rates_from_marks` shape: rate, inverted, pair,
    as_of_date). Forward pillars are read from `marks_official` for the SAME as_of_date
    the spot entry carries, so spot and outrights are always one day's curve. USD is
    the identity on every date. A date that is not an ISO date (the settled-cash
    sentinel) is marked at spot."""
    out: Dict[Tuple[str, str], dict] = {}
    holidays = _calendar.load_holidays()  # once per call: spot_date would re-stat the file per pair
    by_ccy: Dict[str, list] = {}
    for ccy, day in needed:
        by_ccy.setdefault(ccy, []).append(day)
    curves: Dict[str, list] = {}
    for ccy, days in by_ccy.items():
        if ccy == "USD":
            for day in days:
                out[(ccy, day)] = {"rate": 1.0, "quoted": 1.0, "basis": BASIS_SPOT, "pair": "USD"}
            continue
        entry = rates.get(ccy)
        if entry is None:
            continue
        spot_value = float(entry["rate"])
        if not spot_value > 0:
            continue
        inverted = bool(entry.get("inverted"))
        pair = str(entry.get("pair") or ("USD" + ccy if inverted else ccy + "USD"))
        as_of = str(entry.get("as_of_date") or "")
        spot_day = spot_date(as_of, pair, holidays) if as_of else ""  # per pair: USDCAD is T+1
        if pair not in curves:
            pillars = []
            if as_of:
                pillars = [(str(d), float(v)) for d, v in conn.execute(_FWD_SQL, {"pair": pair, "as_of": as_of})
                           if str(d) > spot_day]
            curves[pair] = pillars
        for day in days:
            iso = _iso_or_none(day)
            if iso is None or not spot_day:
                quoted, basis = spot_value, BASIS_SPOT
            else:
                quoted, basis = _quoted_at(curves[pair], iso, spot_day, spot_value)
            rate = 1.0 / quoted if inverted else quoted
            out[(ccy, day)] = {"rate": rate, "quoted": quoted, "basis": basis, "pair": pair}
    return out


def _iso_or_none(day: str):
    try:
        return dt.date.fromisoformat(str(day)).isoformat()
    except ValueError:
        return None
