"""Open-futures USD delta for the ladder's Net/Gross and the stress block.

USD delta of a future = contracts x multiplier x settlement price on as_of, summed over
open futures (expiry > as_of). Uses the official FUTURE_PX mark at the contract's own
expiry date, as value_book does. A future without a mark makes the total unavailable
(NaN) with its instrument named; nothing is substituted.
"""
from __future__ import annotations

import sqlite3

from engine.pnl.valuation import _mark_at

_OPEN_FUTURES_SQL = """
SELECT t.instrument_id, SUM(t.quantity) AS contracts, i.multiplier, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id)
JOIN trade_legs l ON l.trade_id = t.trade_id AND l.leg_no = 1
WHERE t.product = 'FUTURE' AND t.trade_date <= :as_of AND l.settle_date > :as_of
GROUP BY t.instrument_id, i.multiplier, l.settle_date
"""


def futures_usd_delta(conn: sqlite3.Connection, as_of: str) -> dict:
    """{'value': float or NaN, 'by_instrument': {id: usd_delta}, 'missing': [ids], 'reason': str,
    'details': {id: {'contracts', 'multiplier', 'price', 'expiry', 'source'}}} (price None when missing)."""
    rows = conn.execute(_OPEN_FUTURES_SQL, {"as_of": as_of}).fetchall()
    by_instrument, missing, details = {}, [], {}
    for instrument_id, contracts, multiplier, expiry in rows:
        hit = _mark_at(conn, instrument_id, expiry, "FUTURE_PX", as_of)
        details[instrument_id] = {"contracts": float(contracts), "multiplier": float(multiplier),
                                  "price": None if hit is None else float(hit[0]), "expiry": expiry,
                                  "source": "" if hit is None else hit[1]}
        if hit is None:
            missing.append(instrument_id)
            continue
        by_instrument[instrument_id] = float(contracts) * float(multiplier) * float(hit[0])
    if missing:
        return {"value": float("nan"), "by_instrument": by_instrument, "missing": missing,
                "reason": f"no FUTURE_PX on {as_of} for {', '.join(sorted(missing))}", "details": details}
    return {"value": float(sum(by_instrument.values())), "by_instrument": by_instrument,
            "missing": [], "reason": "", "details": details}
