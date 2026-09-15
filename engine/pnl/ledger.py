"""P&L ledger: realisation on settlement, LTD, and period P&L. docs/BUILD_PLAN.md
section 3. Built on `engine.pnl.valuation.value_book`; no relationship to
`engine.ladder.exposure` (that is a delta table, not a ledger) and no relationship to
the workbook reconciliation view in `engine/pnl/pnl.py` / `aggregate.py`.

Realisation: a trade whose last leg settles before `as_of` and is not yet in
`realised_pnl` is frozen ONCE at the SPOT observed on its settle date (or the last
official SPOT/FUTURE_PX before it, noted) -- FX and futures alike, including crosses
with no USD leg (conversion to USD uses `valuation.usd_per_quote`, never an invented
leg). If no such mark exists the trade is "unrealisable" and every LTD from that date
on is Unavailable with the trade id in the reason (surfaced via value_book's reason
column, since value_book reads settled rows straight from realised_pnl).

`realised_pnl` storage keeps its original 12-column shape (schema owned by
data-ingest); the columns are repurposed slightly to stay generic across USD-quote
pairs, JPY-style USD-base pairs, crosses and futures:
    local_amount        = trades.quantity (signed base amount, or contracts for a future)
    usd_entry_amount     = local_amount * fill * S   (S = 1 for futures)
    spot_usd_per_local   = mark * S                  (mark = pair SPOT, or FUTURE_PX)
    pnl_usd              = local_amount * spot_usd_per_local - usd_entry_amount
This is algebraically identical to `quantity * (mark - fill) * S` and, when the quote
currency is USD (S = 1), identical to the original USD-pair-only formula, so it stays
compatible with a plain "spot dated / last before settlement" note.

`ltd(conn, d)` = sum of value_book(d).pnl_usd, NaN if any row is NaN, 0.0 for an empty
book (first trading day, not Unavailable). Periods subtract `ltd` at a reference
business day from a Mon-Fri + `config/holidays.txt` calendar (engine/pnl/aggregate.py).
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from typing import Dict, Optional

import pandas as pd

from engine.pnl.aggregate import (_last_business_day_of_prev_month, _last_business_day_of_prev_year,
                                  _n_business_days_back, _prev_business_day, load_holidays)
from engine.pnl.valuation import usd_per_quote, value_book

GROUP_KEYS = ("instrument_id", "product", "strategy", "theme")

_OPEN_FX_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.quote_ccy, t.quantity, t.price, l.settle_date
FROM trades t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""

_OPEN_FUTURE_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.multiplier, t.quantity, t.price, l.settle_date
FROM trades t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product = 'FUTURE' AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _insert_realised(conn, trade_id, instrument_id, product, currency, settle_date, local_amount,
                      usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source,
                      pnl_usd, note):
    conn.execute("INSERT INTO realised_pnl VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount,
                  mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, _now(), note))


# --------------------------------------------------------------------------- realise
def realise_settled(conn: sqlite3.Connection, as_of: str) -> dict:
    """Freeze P&L for FX and future trades whose settle date is before `as_of` and are
    not yet in `realised_pnl`. Returns {'realised': n, 'unrealisable': [{trade_id, reason}]}."""
    realised, unrealisable = 0, []

    for trade_id, pair, product, quote_ccy, qty, fill, settle in conn.execute(_OPEN_FX_SQL, {"as_of": as_of}).fetchall():
        m_hit = _last_on_or_before(conn, pair, "SPOT", settle)
        if m_hit is None:
            unrealisable.append({"trade_id": trade_id, "reason": f"no official SPOT for {pair} on or before {settle}"})
            continue
        m, m_day, m_src = m_hit
        s, s_pair, s_src = usd_per_quote(conn, quote_ccy, m_day)
        if s != s:
            unrealisable.append({"trade_id": trade_id, "reason": f"no SPOT to convert {quote_ccy} to USD on or before {settle}"})
            continue
        entry = qty * fill * s
        combined = m * s
        pnl = qty * combined - entry
        note = "" if m_day == settle else f"spot dated {m_day} (last before settlement)"
        _insert_realised(conn, trade_id, pair, product, quote_ccy, settle, qty, entry, "SPOT", combined, m_day, m_src, pnl, note)
        realised += 1

    for trade_id, pair, product, multiplier, qty, fill, settle in conn.execute(_OPEN_FUTURE_SQL, {"as_of": as_of}).fetchall():
        m_hit = _last_on_or_before(conn, pair, "FUTURE_PX", settle)
        if m_hit is None:
            unrealisable.append({"trade_id": trade_id, "reason": f"no official FUTURE_PX for {pair} on or before {settle}"})
            continue
        m, m_day, m_src = m_hit
        combined = multiplier * m
        entry = qty * multiplier * fill
        pnl = qty * combined - entry
        note = "" if m_day == settle else f"settlement price dated {m_day} (last before expiry)"
        _insert_realised(conn, trade_id, pair, product, "USD", settle, qty, entry, "FUTURE_PX", combined, m_day, m_src, pnl, note)
        realised += 1

    conn.commit()
    return {"realised": realised, "unrealisable": unrealisable}


def _last_on_or_before(conn: sqlite3.Connection, instrument_id: str, mark_type: str, day: str) -> Optional[tuple]:
    row = conn.execute(
        "SELECT value, as_of_date, source FROM marks_official WHERE instrument_id = :i "
        "AND mark_type = :m AND as_of_date <= :d ORDER BY as_of_date DESC, snapped_at DESC LIMIT 1",
        {"i": instrument_id, "m": mark_type, "d": day},
    ).fetchone()
    return None if row is None else (float(row[0]), row[1], row[2])


def realised_rows(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """Frozen trades with settle_date < as_of (so an earlier as_of excludes later settlements)."""
    return pd.read_sql_query("SELECT * FROM realised_pnl WHERE settle_date < :as_of ORDER BY settle_date, trade_id",
                             conn, params={"as_of": as_of})


# --------------------------------------------------------------------------- LTD
def ltd(conn: sqlite3.Connection, as_of: str, marks_source: Optional[str] = None) -> float:
    """sum(value_book(as_of).pnl_usd); NaN if any row is NaN; 0.0 for an empty book."""
    vb = value_book(conn, as_of, marks_source=marks_source)
    if vb.empty:
        return 0.0
    if vb["pnl_usd"].isna().any():
        return float("nan")
    return float(vb["pnl_usd"].sum())


def _period_refs(as_of: str, holidays) -> Dict[str, dt.date]:
    d = dt.date.fromisoformat(as_of)
    return {
        "daily": _prev_business_day(d, holidays),
        "d5": _n_business_days_back(d, 5, holidays),
        "mtd": _last_business_day_of_prev_month(d, holidays),
        "ytd": _last_business_day_of_prev_year(d, holidays),
    }


def period_reference_dates(as_of: str) -> Dict[str, str]:
    """Public wrapper over the business-day reference dates this module's periods use
    (`config/holidays.txt` calendar via `engine.pnl.aggregate`), for callers outside
    `engine/pnl/` that need the same dates without duplicating the calendar logic --
    e.g. `ui.tabs.blotter_pricing`'s row-scoped P&L strip (2026-09-15 addition,
    authorised for this single function; every other signature in this module is
    unchanged). Returns ISO date strings: `daily` (T-1), `previous_day` (T-2, so a
    caller can build a "previous day" period as ltd(daily) - ltd(previous_day)),
    `d5`, `mtd`, `ytd`."""
    holidays = load_holidays()
    refs = _period_refs(as_of, holidays)
    previous_day = _prev_business_day(refs["daily"], holidays)
    out = {k: v.isoformat() for k, v in refs.items()}
    out["previous_day"] = previous_day.isoformat()
    return out


def period_pnl(conn: sqlite3.Connection, as_of: str, marks_source: Optional[str] = None) -> Dict[str, dict]:
    """Daily / d5 / mtd / ytd = ltd(as_of) - ltd(reference business day); plus `trading`
    = sum of pnl_usd for rows with trade_date = as_of. Each period:
    {value, ref_date, available, reason}."""
    holidays = load_holidays()
    refs = _period_refs(as_of, holidays)
    ltd_today = ltd(conn, as_of, marks_source)
    out: Dict[str, dict] = {}
    for key, ref in refs.items():
        entry = {"value": float("nan"), "ref_date": ref.isoformat(), "available": False, "reason": ""}
        if math.isnan(ltd_today):
            entry["reason"] = "today's LTD unavailable (a trade has a missing mark; see value_book reason)"
        else:
            ltd_ref = ltd(conn, ref.isoformat(), marks_source)
            if math.isnan(ltd_ref):
                entry["reason"] = f"LTD on {ref.isoformat()} unavailable (a trade has a missing mark)"
            else:
                entry.update(value=ltd_today - ltd_ref, available=True)
        out[key] = entry

    vb = value_book(conn, as_of, marks_source=marks_source)
    trading_rows = vb[vb["trade_date"] == as_of] if not vb.empty else vb
    if trading_rows.empty:
        trading_value, trading_available, trading_reason = 0.0, True, ""
    elif trading_rows["pnl_usd"].isna().any():
        trading_value, trading_available = float("nan"), False
        trading_reason = "a trade dated today has a missing mark; see value_book reason"
    else:
        trading_value, trading_available, trading_reason = float(trading_rows["pnl_usd"].sum()), True, ""
    out["trading"] = {"value": trading_value, "ref_date": as_of, "available": trading_available, "reason": trading_reason}
    return out


def period_pnl_by(conn: sqlite3.Connection, as_of: str, key: str, marks_source: Optional[str] = None) -> Dict[str, dict]:
    """period_pnl's subtraction applied per group of value_book rows, grouped by `key`
    (one of instrument_id, product, strategy, theme). Returns {group_value: {period: {...}}}."""
    if key not in GROUP_KEYS:
        raise ValueError(f"key must be one of {GROUP_KEYS}, got {key!r}")
    holidays = load_holidays()
    refs = _period_refs(as_of, holidays)

    def grouped_ltd(day: str) -> Dict[str, float]:
        vb = value_book(conn, day, marks_source=marks_source)
        if vb.empty:
            return {}
        out = {}
        for g, gdf in vb.groupby(key):
            out[g] = float("nan") if gdf["pnl_usd"].isna().any() else float(gdf["pnl_usd"].sum())
        return out

    today_ltd = grouped_ltd(as_of)
    ref_ltds = {name: grouped_ltd(ref.isoformat()) for name, ref in refs.items()}
    groups = set(today_ltd)
    for m in ref_ltds.values():
        groups |= set(m)

    result: Dict[str, dict] = {}
    for g in groups:
        t_val = today_ltd.get(g, 0.0)
        periods = {}
        for name, ref in refs.items():
            r_val = ref_ltds[name].get(g, 0.0)
            if (isinstance(t_val, float) and math.isnan(t_val)) or (isinstance(r_val, float) and math.isnan(r_val)):
                periods[name] = {"value": float("nan"), "ref_date": ref.isoformat(), "available": False,
                                 "reason": f"group {g!r} has a trade with a missing mark on {as_of} or {ref.isoformat()}"}
            else:
                periods[name] = {"value": t_val - r_val, "ref_date": ref.isoformat(), "available": True, "reason": ""}
        result[g] = periods
    return result
