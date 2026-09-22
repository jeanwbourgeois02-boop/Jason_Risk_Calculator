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

IRS (2026-09-17, closes docs/open-questions.md item 52): a swap whose maturity is before
`as_of` is frozen at the last official `PV_USD + CASHFLOW_USD` on or before maturity
(PV_USD is 0 on the maturity date itself once the pricer has run, so the frozen value is
the swap's settled coupons). Stored with `mark_type='PV_USD'`, `local_amount` = the
frozen USD P&L, `spot_usd_per_local = 1.0`, `usd_entry_amount = 0.0`, so the generic
identity above still holds. FX_OPTION: frozen at the last official `PREMIUM` on or before
expiry, converted at that day's spot, same columns as an FX trade (`local_amount` =
base notional, `mark_type='PREMIUM'`). An expiry-day intrinsic-value mark would be more
exact than the last premium; until the options pricer writes one, the note says which
date's premium was used. A closed-out option (bought and sold back in full,
`engine.pnl.valuation.closed_out_options`) is frozen at its closing fill instead, converted
at the close-out date's spot (or the last official one before it), `mark_type='CLOSE_OUT'`:
it needs no PREMIUM, and the row records the figure value_book has shown since the close-out.

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
from engine.pnl.valuation import (PRESENT_SPOT_NOTE, _BadValue, _number, close_out_ccy, closed_out_options,
                                  last_usd_conversion, option_fill_is_per_ounce, present_spot_for_ndf, usd_per_quote,
                                  value_book)

GROUP_KEYS = ("instrument_id", "product", "strategy", "theme")

_OPEN_FX_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.quote_ccy, t.quantity, t.price, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ('FX_SPOT','FX_FWD','FX_SWAP') AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""

_OPEN_FUTURE_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.multiplier, t.quantity, t.price, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ('FUTURE', 'EQ_OPTION') AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""


_OPEN_IRS_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.base_ccy, t.quantity, t.price, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product = 'IRS' AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""

_OPEN_OPTION_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.base_ccy, t.quantity, t.price, l.settle_date, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product = 'FX_OPTION' AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""


_FROZEN_OPTION_SQL = """
SELECT t.trade_id, i.base_ccy, i.quote_ccy, t.price, r.mark_type, r.spot_as_of_date
FROM realised_pnl r JOIN trades_official t USING (trade_id) JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE t.product = 'FX_OPTION'
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


_REALISED_COLUMNS = ("trade_id", "instrument_id", "product", "currency", "settle_date", "local_amount",
                     "usd_entry_amount", "mark_type", "spot_usd_per_local", "spot_as_of_date", "spot_source",
                     "pnl_usd", "frozen_at", "note")
_REALISED_NUMERIC = ("local_amount", "usd_entry_amount", "spot_usd_per_local", "pnl_usd")


def _insert_realised(conn, trade_id, instrument_id, product, currency, settle_date, local_amount,
                      usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source,
                      pnl_usd, note):
    """Columns are NAMED (2026-09-18, root cause of the Bloomberg-PC "could not convert
    string to float: '<a date>'" that blanked every Blotter view and every headline).
    This INSERT used to be positional (`VALUES (?,?,...)` x 14 in the DDL's order), but
    `realised_pnl`'s physical column order depends on the database's age: `product` and
    `mark_type` were added on 2026-09-15, in the MIDDLE of the DDL for a fresh database
    and, through `data.ingest.schema._migrate_columns`' `ALTER TABLE ADD COLUMN`, at the
    END of the table for a database created before that (the dev database and the
    Bloomberg PC's are both that shape). On such a database every value landed two
    columns off: `pnl_usd` received `spot_as_of_date` (a date string, which SQLite keeps
    as TEXT in a REAL column), `local_amount` the settle date, `currency` the product.
    It only ever showed with live marks, because only then does anything get realised."""
    conn.execute(
        f"INSERT INTO realised_pnl ({', '.join(_REALISED_COLUMNS)}) VALUES ({','.join('?' * len(_REALISED_COLUMNS))})",
        (trade_id, instrument_id, product, currency, settle_date, local_amount, usd_entry_amount,
         mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, _now(), note))


def purge_unreadable_realised(conn: sqlite3.Connection) -> list:
    """Delete the `realised_pnl` rows the positional INSERT above misaligned (see
    `_insert_realised`) and return their trade ids. A row is unreadable when any of its
    four REAL columns holds something that is not a number -- always true of a misaligned
    row (`pnl_usd` holds a date, `local_amount` the settle date) and never of a healthy
    one, whose figures this module computed as floats. Nothing is recomputed here: the
    deleted trades simply are "not yet in realised_pnl" again, so `realise_settled`
    freezes them afresh, with the same arithmetic as any other trade, in the same call.
    Not committed here; `realise_settled` commits once at its end."""
    where = " OR ".join(f"typeof({c}) NOT IN ('real','integer')" for c in _REALISED_NUMERIC)
    ids = [r[0] for r in conn.execute(f"SELECT trade_id FROM realised_pnl WHERE {where}")]
    if ids:
        conn.execute(f"DELETE FROM realised_pnl WHERE {where}")
    return ids


def purge_superseded_present_spot(conn: sqlite3.Connection) -> list:
    """Delete the `realised_pnl` rows frozen at a present spot (`valuation.PRESENT_SPOT_NOTE`:
    an NDF with no past fix on file) whose pair now HAS an official SPOT on or before the
    settlement date, and return their trade ids (user, 2026-09-21: the true close replaces the
    present spot once it lands). As above, nothing is recomputed here: `realise_settled`
    freezes them afresh, by the strict rule, in the same call."""
    ids = []
    for trade_id, pair, settle in conn.execute(
            "SELECT trade_id, instrument_id, settle_date FROM realised_pnl WHERE mark_type = 'SPOT' AND note LIKE ?",
            (f"%{PRESENT_SPOT_NOTE}%",)).fetchall():
        try:
            if _last_on_or_before(conn, pair, "SPOT", _freeze_day(conn, pair, settle)) is not None:
                ids.append(trade_id)
        except (TypeError, ValueError):   # a past SPOT that is not a number replaces nothing
            continue
    conn.executemany("DELETE FROM realised_pnl WHERE trade_id = ?", [(t,) for t in ids])
    return ids


def _unrealisable(trade_id, exc: Exception) -> dict:
    """The `unrealisable` entry for a trade one of whose stored figures is not a number
    (`engine.pnl.valuation._BadValue` names the table.column and the value) or whose
    arithmetic otherwise failed: that ONE trade is skipped and named, the rest of the
    book is still realised -- before 2026-09-18 the exception aborted the whole step."""
    detail = str(exc) if isinstance(exc, _BadValue) else f"could not be realised ({type(exc).__name__}: {exc})"
    return {"trade_id": trade_id, "reason": f"trade {trade_id}: {detail}"}


# --------------------------------------------------------------------------- realise
def realise_settled(conn: sqlite3.Connection, as_of: str, ndf_present_spot: bool = False) -> dict:
    """Freeze P&L for FX and future trades whose settle date is before `as_of` and are
    not yet in `realised_pnl`. Returns {'realised': n, 'unrealisable': [{trade_id, reason}],
    'repaired': [trade_id, ...]}.

    `ndf_present_spot` (user decision 2026-09-21: "we can use a present spot for the past
    fixes"): an NDF ticket whose pair has no official SPOT on or before its settlement is
    frozen at the latest official SPOT on or before `as_of` (`valuation.present_spot_for_ndf`,
    NDF pairs only) instead of staying unrealisable, and its note says so. Off by default: the
    live pull's own call runs BEFORE the backfill of the same button press, so only
    data.bloomberg.backfill.auto_backfill passes it, once the backfill has tried for the
    settlement date's own close. Such a row lasts only until that close is on file: every call
    then drops it and freezes the trade again by the strict rule (`purge_superseded_present_spot`).

    `repaired` (2026-09-18): rows an earlier, positional INSERT misaligned
    (`_insert_realised`) are deleted first (`purge_unreadable_realised`), so the trades
    they belonged to are frozen afresh below like any trade not yet realised -- a
    database the old INSERT corrupted heals itself on the next Bloomberg pull, no manual
    step. One trade with a stored figure that is not a number is reported as
    unrealisable (`_unrealisable`) instead of aborting the step for the whole book."""
    realised, unrealisable = 0, []
    repaired = purge_unreadable_realised(conn)
    purge_superseded_present_spot(conn)

    for trade_id, pair, product, quote_ccy, qty, fill, settle in conn.execute(_OPEN_FX_SQL, {"as_of": as_of}).fetchall():
        try:
            qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
            # An NDF is done at its fixing (user, 2026-09-22: "they just disappears as they
            # expired"): frozen at the last official SPOT on or before the FIXING date, the same
            # figure value_book shows from the fixing on, not the value date's spot two days later.
            m_hit = _last_on_or_before(conn, pair, "SPOT", _freeze_day(conn, pair, settle))
            present = m_hit is None and ndf_present_spot
            if present:
                m_hit = present_spot_for_ndf(conn, pair, as_of)
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
            fix_day = _freeze_day(conn, pair, settle)
            note = ("" if m_day == settle else
                    f"spot dated {m_day} ({PRESENT_SPOT_NOTE if present else ('NDF fixing' if m_day == fix_day else 'last before ' + ('fixing' if fix_day != settle else 'settlement'))})")
            _insert_realised(conn, trade_id, pair, product, quote_ccy, settle, qty, entry, "SPOT", combined, m_day, m_src, pnl, note)
            realised += 1
        except (TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    for trade_id, pair, product, multiplier, qty, fill, settle in conn.execute(_OPEN_FUTURE_SQL, {"as_of": as_of}).fetchall():
        try:
            qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
            multiplier = _number(multiplier, "instruments.multiplier")
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
        except (TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    for trade_id, inst, product, ccy, qty, fill, settle in conn.execute(_OPEN_IRS_SQL, {"as_of": as_of}).fetchall():
        try:
            pv_hit = _last_on_or_before(conn, inst, "PV_USD", settle)
            if pv_hit is None:
                unrealisable.append({"trade_id": trade_id, "reason": f"no official PV_USD for {inst} on or before {settle}"})
                continue
            pv, m_day, m_src = pv_hit
            cf_row = conn.execute(
                "SELECT value FROM marks_official WHERE instrument_id = :i AND mark_type = 'CASHFLOW_USD' "
                "AND as_of_date = :d ORDER BY snapped_at DESC LIMIT 1", {"i": inst, "d": m_day}).fetchone()
            if cf_row is None:
                unrealisable.append({"trade_id": trade_id, "reason": f"no official CASHFLOW_USD for {inst} on {m_day}"})
                continue
            pnl = pv + _number(cf_row[0], f"marks.value (CASHFLOW_USD for {inst} on {m_day})")
            note = "" if m_day == settle else f"PV + cashflows dated {m_day} (last before maturity)"
            _insert_realised(conn, trade_id, inst, product, ccy, settle, pnl, 0.0, "PV_USD", 1.0, m_day, m_src, pnl, note)
            realised += 1
        except (TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    closed_out = closed_out_options(conn, as_of)
    # Every trade of a closed-out option is frozen alike, at the closing fill and the close-out
    # date's spot, or the group's total is wrong. A row frozen any other way -- at a PREMIUM before
    # the group could be recognised (the other trade's strike was typed after expiry), or at the
    # expiry date's spot (the rule of a few hours on 2026-09-21) -- is dropped here and frozen again
    # below, and only when the close-out spot is on file, so a trade that has a figure keeps one.
    for trade_id, base_ccy, quote_ccy, fill, mark_type, spot_day in conn.execute(_FROZEN_OPTION_SQL).fetchall():
        closed = closed_out.get(trade_id)
        if closed is None or (mark_type == "CLOSE_OUT" and spot_day <= closed.date):
            continue
        try:
            ccy = close_out_ccy(base_ccy, quote_ccy, _number(fill, "trades.price"))
            if last_usd_conversion(conn, ccy, closed.date) is not None:
                conn.execute("DELETE FROM realised_pnl WHERE trade_id = ?", (trade_id,))
        except (TypeError, ValueError, ArithmeticError):
            pass   # a figure that is not a number: the row is left as it is
    option_rows = conn.execute(_OPEN_OPTION_SQL, {"as_of": as_of}).fetchall()
    for trade_id, inst, product, base_ccy, qty, fill, settle, quote_ccy in option_rows:
        try:
            qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
            closed = closed_out.get(trade_id)
            if closed is not None:
                # Bought and sold back in full (engine.pnl.valuation, "Closed-out options"): frozen at
                # the closing fill, never a PREMIUM mark, and at the close-out date's spot, the figure
                # value_book has shown since the close-out, so every trade of the group is frozen alike.
                ccy = close_out_ccy(base_ccy, quote_ccy, fill)
                s_hit = last_usd_conversion(conn, ccy, closed.date)
                if s_hit is None:
                    unrealisable.append({"trade_id": trade_id, "reason": f"closed out {closed.date}; no official SPOT to convert {ccy} to USD on or before that date"})
                    continue
                s, s_day, s_src = s_hit
                entry = qty * fill * s
                combined = closed.price * s
                pnl = qty * combined - entry
                note = (f"closed out {closed.date} at the closing fill {closed.price:.10g}"
                        + ("" if s_day == closed.date else f"; spot dated {s_day} (last before the close-out)"))
                _insert_realised(conn, trade_id, inst, product, ccy, settle, qty, entry, "CLOSE_OUT", combined, s_day, s_src, pnl, note)
                realised += 1
                continue
            m_hit = _last_on_or_before(conn, inst, "PREMIUM", settle)
            if m_hit is None:
                unrealisable.append({"trade_id": trade_id, "reason": f"no official PREMIUM for {inst} on or before {settle}"})
                continue
            m, m_day, m_src = m_hit
            s, s_pair, s_src = usd_per_quote(conn, base_ccy, m_day)
            if s != s:
                unrealisable.append({"trade_id": trade_id, "reason": f"no SPOT to convert {base_ccy} to USD on or before {settle}"})
                continue
            entry = qty * fill * s
            if option_fill_is_per_ounce(base_ccy, fill):
                # a metal option dealt in quote currency per ounce: its start value is in the
                # QUOTE currency (engine.pnl.valuation.option_fill_is_per_ounce)
                q, _q_pair, _q_src = usd_per_quote(conn, quote_ccy, m_day)
                if q != q:
                    unrealisable.append({"trade_id": trade_id, "reason": f"no SPOT to convert {quote_ccy} to USD on or before {settle}"})
                    continue
                entry = qty * fill * q
            combined = m * s
            pnl = qty * combined - entry
            note = f"premium dated {m_day}" + ("" if m_day == settle else " (last before expiry)")
            _insert_realised(conn, trade_id, inst, product, base_ccy, settle, qty, entry, "PREMIUM", combined, m_day, m_src, pnl, note)
            realised += 1
        except (TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    conn.commit()
    return {"realised": realised, "unrealisable": unrealisable, "repaired": repaired}


def _freeze_day(conn: sqlite3.Connection, pair: str, settle: str) -> str:
    """The day an FX ticket's freeze reads its spot on or before: the fixing date (value
    date less 2 business days) for an NDF pair, the value date for a deliverable one."""
    from engine.ladder.ndf import fixing_date, is_ndf_pair
    row = conn.execute("SELECT is_ndf FROM instruments WHERE instrument_id = ?", (pair,)).fetchone()
    if is_ndf_pair(pair, int((row[0] if row else 0) or 0)):
        return fixing_date(settle)
    return settle


def _last_on_or_before(conn: sqlite3.Connection, instrument_id: str, mark_type: str, day: str) -> Optional[tuple]:
    row = conn.execute(
        "SELECT value, as_of_date, source FROM marks_official WHERE instrument_id = :i "
        "AND mark_type = :m AND as_of_date <= :d ORDER BY as_of_date DESC, snapped_at DESC LIMIT 1",
        {"i": instrument_id, "m": mark_type, "d": day},
    ).fetchone()
    if row is None:
        return None
    return (_number(row[0], f"marks.value ({mark_type} for {instrument_id} on {row[1]})"), row[1], row[2])


def realised_rows(conn: sqlite3.Connection, as_of: str) -> pd.DataFrame:
    """Frozen trades with settle_date < as_of (so an earlier as_of excludes later settlements)."""
    return pd.read_sql_query("SELECT * FROM realised_pnl WHERE settle_date < :as_of ORDER BY settle_date, trade_id",
                             conn, params={"as_of": as_of})


# --------------------------------------------------------------------------- LTD
def ltd(conn: sqlite3.Connection, as_of: str) -> float:
    """sum(value_book(as_of).pnl_usd); NaN if any row is NaN; 0.0 for an empty book."""
    vb = value_book(conn, as_of)
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


def period_pnl(conn: sqlite3.Connection, as_of: str) -> Dict[str, dict]:
    """Daily / d5 / mtd / ytd = ltd(as_of) - ltd(reference business day); plus `trading`
    = sum of pnl_usd for rows with trade_date = as_of. Each period:
    {value, ref_date, available, reason}."""
    holidays = load_holidays()
    refs = _period_refs(as_of, holidays)
    ltd_today = ltd(conn, as_of)
    out: Dict[str, dict] = {}
    for key, ref in refs.items():
        entry = {"value": float("nan"), "ref_date": ref.isoformat(), "available": False, "reason": ""}
        if math.isnan(ltd_today):
            entry["reason"] = "today's LTD unavailable (a trade has a missing mark; see value_book reason)"
        else:
            ltd_ref = ltd(conn, ref.isoformat())
            if math.isnan(ltd_ref):
                entry["reason"] = f"LTD on {ref.isoformat()} unavailable (a trade has a missing mark)"
            else:
                entry.update(value=ltd_today - ltd_ref, available=True)
        out[key] = entry

    vb = value_book(conn, as_of)
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


def period_pnl_by(conn: sqlite3.Connection, as_of: str, key: str) -> Dict[str, dict]:
    """period_pnl's subtraction applied per group of value_book rows, grouped by `key`
    (one of instrument_id, product, strategy, theme). Returns {group_value: {period: {...}}}."""
    if key not in GROUP_KEYS:
        raise ValueError(f"key must be one of {GROUP_KEYS}, got {key!r}")
    holidays = load_holidays()
    refs = _period_refs(as_of, holidays)

    def grouped_ltd(day: str) -> Dict[str, float]:
        vb = value_book(conn, day)
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
