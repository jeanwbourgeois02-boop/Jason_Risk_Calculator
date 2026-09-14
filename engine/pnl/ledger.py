"""P&L ledger: realised on settlement, unrealised on open positions, daily snapshot
series for Daily / 5d / MTD / YTD. Additive to the exposure engine; nothing here changes
build_exposure or the workbook formulas.

Conventions (same as engine/ladder/exposure.py):
    P&L per trade = local_amount x USD-per-local spot - usd_entry_amount

Realised: when a trade's settle_date < as_of it is frozen ONCE in `realised_pnl` at the
official SPOT mark dated its settle date, or the last official SPOT before it (noted).
If no spot exists on or before the settle date the trade is "unrealisable" and reported,
never guessed.

Snapshots: `take_snapshot` stores one row per as_of date (last write of the day wins):
realised LTD, unrealised (open trades at the latest spot), total LTD, net/gross USD,
trading P&L (trades dated as_of). A snapshot is `complete` only when every open currency
had a rate and every settled trade was realisable. Period figures compare today's total
LTD with the snapshot on the reference business day (prev bd / 5 bd / last bd of prior
month / prior year); a missing or incomplete reference snapshot -> Unavailable + reason.
Calendar is Monday-Friday only (engine/pnl/aggregate helpers).
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Dict, List, Optional

import pandas as pd

from engine.pnl.aggregate import (_last_business_day_of_prev_month, _last_business_day_of_prev_year,
                                  _n_business_days_back, _prev_business_day)

_SETTLED_FX_SQL = """
SELECT t.trade_id, t.instrument_id, i.base_ccy, i.quote_ccy, t.trade_date,
       l.ccy, l.amount, l.settle_date
FROM trades t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
ORDER BY t.trade_id, l.leg_no
"""

_SPOT_ON_OR_BEFORE_SQL = """
SELECT value, as_of_date, source FROM marks_official
WHERE instrument_id = :pair AND mark_type = 'SPOT' AND as_of_date <= :day
ORDER BY as_of_date DESC, snapped_at DESC LIMIT 1
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- realise
def realise_settled(conn: sqlite3.Connection, as_of: str) -> dict:
    """Freeze P&L for FX trades whose settle date is before `as_of` and are not yet in
    realised_pnl. Returns {'realised': n, 'unrealisable': [{trade_id, reason}]}."""
    by_trade: Dict[str, list] = {}
    for row in conn.execute(_SETTLED_FX_SQL, {"as_of": as_of}):
        by_trade.setdefault(row[0], []).append(row)
    realised, unrealisable = 0, []
    for trade_id, legs in by_trade.items():
        _, pair, base, quote, trade_date = legs[0][:5]
        usd = [l for l in legs if l[5] == "USD"]
        local = [l for l in legs if l[5] != "USD"]
        if len(legs) != 2 or len(usd) != 1 or len(local) != 1:
            unrealisable.append({"trade_id": trade_id, "reason": "not a USD pair with two legs"})
            continue
        ccy, local_amount, settle = local[0][5], float(local[0][6]), local[0][7]
        usd_entry = -float(usd[0][6])
        spot_row = conn.execute(_SPOT_ON_OR_BEFORE_SQL, {"pair": pair, "day": settle}).fetchone()
        if spot_row is None:
            unrealisable.append({"trade_id": trade_id, "reason": f"no official SPOT for {pair} on or before {settle}"})
            continue
        value, spot_day, source = spot_row
        spot = 1.0 / float(value) if base == "USD" else float(value)
        pnl = local_amount * spot - usd_entry
        note = "" if spot_day == settle else f"spot dated {spot_day} (last before settlement)"
        conn.execute("INSERT INTO realised_pnl VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (trade_id, pair, ccy, settle, local_amount, usd_entry, spot, spot_day, source, pnl, _now(), note))
        realised += 1
    conn.commit()
    return {"realised": realised, "unrealisable": unrealisable}


def realised_rows(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """Frozen trades with settle_date < as_of (so an earlier as_of excludes later settlements)."""
    return pd.read_sql_query("SELECT * FROM realised_pnl WHERE settle_date < :as_of ORDER BY settle_date, trade_id",
                             conn, params={"as_of": as_of})


# --------------------------------------------------------------------------- snapshot
def compute_ledger(conn: sqlite3.Connection, as_of: str, rates: Dict[str, dict]) -> dict:
    """Realised / unrealised / total / trading for `as_of` at the given rates. Pure read."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    from engine.ladder.exposure_adapter import records_from_db
    records, _ = records_from_db(conn, as_of)
    result = build_exposure(records, rates)
    totals = portfolio_totals(result)
    realised = realised_rows(conn, as_of)
    pending = realise_settled_preview(conn, as_of)
    trading = float("nan")
    if not totals["missing"]:
        today_recs = [r for r in records if r["trade_date"] == as_of]
        trading = float(portfolio_totals(build_exposure(today_recs, rates))["exposure_pnl"]) if today_recs else 0.0
    realised_ltd = float(realised["pnl_usd"].sum()) if not realised.empty else 0.0
    unrealised = totals["exposure_pnl"]
    complete = not totals["missing"] and not pending
    total = realised_ltd + unrealised if complete or not totals["missing"] else float("nan")
    return {"as_of_date": as_of, "realised_ltd_usd": realised_ltd, "unrealised_usd": unrealised,
            "total_ltd_usd": total, "net_usd": totals["net_usd"], "gross_usd": totals["gross_usd"],
            "trading_usd": trading, "open_trades": len(records), "realised_trades": int(len(realised)),
            "complete": complete, "missing": totals["missing"], "unrealisable": pending, "realised_table": realised}


def realise_settled_preview(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """Settled-but-not-frozen trades that could not be realised (read-only check)."""
    ids = [r[0] for r in conn.execute(_SETTLED_FX_SQL, {"as_of": as_of})]
    out = []
    for trade_id in sorted(set(ids)):
        out.append({"trade_id": trade_id, "reason": "settled but not yet realised (no spot on/before settle date, or not a USD pair)"})
    return out


def take_snapshot(conn: sqlite3.Connection, as_of: str, rates: Dict[str, dict]) -> dict:
    """realise_settled + compute_ledger + upsert pnl_snapshots for as_of. Returns the row."""
    realise_settled(conn, as_of)
    led = compute_ledger(conn, as_of, rates)
    missing = ",".join(led["missing"] + [u["trade_id"] for u in led["unrealisable"]])

    def num(v):
        return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    row = (as_of, _now(), num(led["realised_ltd_usd"]), num(led["unrealised_usd"]), num(led["total_ltd_usd"]),
           num(led["net_usd"]), num(led["gross_usd"]), num(led["trading_usd"]), led["open_trades"],
           led["realised_trades"], 1 if led["complete"] else 0, missing)
    conn.execute("INSERT OR REPLACE INTO pnl_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
    conn.commit()
    led["snapped_at"] = row[1]
    return led


def snapshots(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM pnl_snapshots ORDER BY as_of_date", conn)


# --------------------------------------------------------------------------- periods
def _snapshot_on_or_before(conn: sqlite3.Connection, day: dt.date) -> Optional[tuple]:
    return conn.execute("SELECT as_of_date, total_ltd_usd, complete FROM pnl_snapshots "
                        "WHERE as_of_date <= ? ORDER BY as_of_date DESC LIMIT 1", (day.isoformat(),)).fetchone()


def period_pnl(conn: sqlite3.Connection, as_of: str, total_ltd_today: float) -> Dict[str, dict]:
    """Daily / d5 / mtd / ytd = total LTD today - total LTD at the reference snapshot.
    Each entry: {value, ref_date, snapshot_date, available, reason}."""
    d = dt.date.fromisoformat(as_of)
    refs = {"daily": _prev_business_day(d), "d5": _n_business_days_back(d, 5),
            "mtd": _last_business_day_of_prev_month(d), "ytd": _last_business_day_of_prev_year(d)}
    out = {}
    for key, ref in refs.items():
        entry = {"value": float("nan"), "ref_date": ref.isoformat(), "snapshot_date": "", "available": False, "reason": ""}
        if total_ltd_today is None or (isinstance(total_ltd_today, float) and math.isnan(total_ltd_today)):
            entry["reason"] = "today's total LTD unavailable (missing rates or unrealised trades)"
        else:
            snap = _snapshot_on_or_before(conn, ref)
            if snap is None:
                entry["reason"] = f"no snapshot on or before {ref.isoformat()}"
            elif not snap[2]:
                entry["reason"] = f"snapshot {snap[0]} incomplete (missing rates or unrealisable trades)"
            else:
                entry.update(value=float(total_ltd_today) - float(snap[1]), snapshot_date=snap[0], available=True)
                if snap[0] != ref.isoformat():
                    entry["reason"] = f"reference snapshot dated {snap[0]} (last on or before {ref.isoformat()})"
        out[key] = entry
    return out


def ledger_summary(conn: sqlite3.Connection, as_of: str, rates: Dict[str, dict]) -> dict:
    """Everything the UI block needs, read-only (no realisation, no snapshot write)."""
    led = compute_ledger(conn, as_of, rates)
    led["periods"] = period_pnl(conn, as_of, led["total_ltd_usd"])
    last = conn.execute("SELECT as_of_date, snapped_at, complete FROM pnl_snapshots ORDER BY as_of_date DESC LIMIT 1").fetchone()
    led["last_snapshot"] = {"as_of_date": last[0], "snapped_at": last[1], "complete": bool(last[2])} if last else None
    led["snapshot_count"] = conn.execute("SELECT COUNT(*) FROM pnl_snapshots").fetchone()[0]
    led["snapshot_table"] = snapshots(conn)
    return led
