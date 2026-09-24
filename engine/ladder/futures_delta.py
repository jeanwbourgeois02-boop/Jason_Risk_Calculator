"""Open-futures USD delta for the ladder's Net/Gross, the stress block and the book's positions.

USD delta of a future = contracts x multiplier x price on as_of x S, summed over open
futures (expiry > as_of). The price is the official FUTURE_PX mark at the contract's own
expiry date, as value_book reads it, in the contract's quote currency; S is USD per unit
of that currency at the official SPOT of as_of exactly (user decision 2026-09-24: a
non-USD future converts at spot of the valuation date): USD<ccy> inverted, else <ccy>USD,
1 for USD. Both lookups are exact (`_mark_at`): the delta is never estimated from near
marks (hard rule 2). A future with no price, or with a price and no conversion spot, makes
the total unavailable (NaN) with its instrument named and the reason given; nothing is
substituted.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Optional, Tuple

from engine.pnl.valuation import _mark_at

_OPEN_FUTURES_SQL = """
SELECT t.instrument_id, SUM(t.quantity) AS contracts, i.multiplier, i.quote_ccy, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
WHERE t.product = 'FUTURE' AND t.trade_date <= :as_of AND l.settle_date > :as_of
GROUP BY t.instrument_id, i.multiplier, i.quote_ccy, l.settle_date
"""


def usd_per_unit(conn: sqlite3.Connection, ccy: str, as_of: str) -> Tuple[Optional[float], str]:
    """(S, source) with S = USD per 1 unit of `ccy` at the official SPOT dated exactly
    `as_of` (USD<ccy> inverted, else <ccy>USD; 1 for USD), or (None, reason). Never a near
    mark or another day's spot: the delta is not estimated (hard rule 2). A stored spot that
    is not a positive number is a data error, reported, never passed over for the other pair."""
    if ccy == "USD":
        return 1.0, "identity"
    if not ccy:
        return None, "no quote currency on file"
    for pair, invert in ((f"USD{ccy}", True), (f"{ccy}USD", False)):
        hit = _mark_at(conn, pair, as_of, "SPOT", as_of)
        if hit is None:
            continue
        try:
            value = float(hit[0])
        except (TypeError, ValueError):
            value = float("nan")
        if not math.isfinite(value) or value <= 0:
            return None, f"SPOT for {pair} on {as_of} is not a usable number ({hit[0]!r})"
        return (1.0 / value if invert else value), f"{pair} {hit[1]}"
    return None, f"no SPOT for {ccy} on {as_of}"


def futures_usd_delta(conn: sqlite3.Connection, as_of: str) -> dict:
    """{'value': float or NaN, 'by_instrument': {id: usd_delta}, 'missing': [ids], 'reason': str,
    'details': {id: {'contracts', 'multiplier', 'price', 'expiry', 'source', 'currency',
    'usd_per_unit', 'reason'}}}. `price` is in the contract's quote currency (`currency`) and
    None when missing; `usd_per_unit` is S (None when there is no conversion spot); a
    detail's `reason` is '' when the future is in `by_instrument`, else why it is not."""
    rows = conn.execute(_OPEN_FUTURES_SQL, {"as_of": as_of}).fetchall()
    by_instrument, missing, details = {}, [], {}
    no_price, no_spot = [], {}
    for instrument_id, contracts, multiplier, ccy, expiry in rows:
        hit = _mark_at(conn, instrument_id, expiry, "FUTURE_PX", as_of)
        s, s_note = usd_per_unit(conn, ccy, as_of)
        if hit is None:
            reason = f"no FUTURE_PX on {as_of}"
        elif s is None:
            reason = s_note
        else:
            reason = ""
        details[instrument_id] = {"contracts": float(contracts), "multiplier": float(multiplier),
                                  "price": None if hit is None else float(hit[0]), "expiry": expiry,
                                  "source": "" if hit is None else hit[1], "currency": ccy,
                                  "usd_per_unit": s, "reason": reason}
        if hit is None:
            missing.append(instrument_id)
            no_price.append(instrument_id)
            continue
        if s is None:
            missing.append(instrument_id)
            no_spot.setdefault(s_note, []).append(instrument_id)
            continue
        by_instrument[instrument_id] = float(contracts) * float(multiplier) * float(hit[0]) * s
    if missing:
        parts = [f"no FUTURE_PX on {as_of} for {', '.join(sorted(no_price))}"] if no_price else []
        parts += [f"{note} ({', '.join(sorted(ids))})" for note, ids in sorted(no_spot.items())]
        return {"value": float("nan"), "by_instrument": by_instrument, "missing": missing,
                "reason": "; ".join(parts), "details": details}
    return {"value": float(sum(by_instrument.values())), "by_instrument": by_instrument,
            "missing": [], "reason": "", "details": details}
