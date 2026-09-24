"""P&L ledger: realisation on settlement, LTD, and period P&L. docs/BUILD_PLAN.md
section 3. Built on `engine.pnl.valuation.value_book`; no relationship to
`engine.ladder.exposure` (that is a delta table, not a ledger) and no relationship to
the workbook reconciliation view in `engine/pnl/pnl.py` / `aggregate.py`.

Realisation: a trade whose last leg settles before `as_of` and is not yet in
`realised_pnl` is frozen at the SPOT observed on its settle date (or the last official
SPOT/FUTURE_PX before it, noted) -- FX and futures alike, including crosses with no USD
leg and non-USD futures (conversion to USD uses `valuation.usd_per_quote` at the mark's own
date for FX, the last spot on or before expiry for a future, below; never an invented leg). If no such
mark exists the trade is "unrealisable" and every LTD from that date on is Unavailable
with the trade id in the reason (surfaced via value_book's reason column, since
value_book reads settled rows straight from realised_pnl).

Re-freeze at the close (user decision 2026-09-22): a frozen row is what the official marks on
file give for its date and mark type, or it is dropped and frozen again by the same rule in
the same call (`purge_superseded`; 'refrozen' in the result says, per trade, what changed and
the figure before and after, 'kept' names a row the rule could not recompute). That is how a trade frozen at a
live press (the last pull before the day roll) takes the day's 15:00 close once the backfill
lands it; a future or listed option frozen at a live PX_LAST takes that day's PX_SETTLE; an
option frozen at a PREMIUM takes its close-out once the other side's strike is typed. A row whose
inputs did not change is untouched and keeps its `frozen_at`, so settlement still does not
move LTD except by this re-freeze; a row the rule cannot compute today (a mark since gone)
keeps its figure. Only rows with `settle_date < as_of` are looked at, the ones this call
freezes again (reviewer, 2026-09-22: a past-day call from the backfill must never drop a row
it cannot freeze again).

`realised_pnl` storage keeps its original 12-column shape (schema owned by
data-ingest); the columns are repurposed slightly to stay generic across USD-quote
pairs, JPY-style USD-base pairs, crosses and futures:
    local_amount        = trades.quantity (signed base amount, or contracts for a future)
    usd_entry_amount     = local_amount * fill * S   (x multiplier for a future)
    spot_usd_per_local   = mark * S                  (mark = pair SPOT, or multiplier x FUTURE_PX)
    pnl_usd              = local_amount * spot_usd_per_local - usd_entry_amount
This is algebraically identical to `quantity * (mark - fill) * S` and, when the quote
currency is USD (S = 1), identical to the original USD-pair-only formula, so it stays
compatible with a plain "spot dated / last before settlement" note.

Futures and listed options (FUTURE, EQ_OPTION, CMDTY_OPTION: `valuation.FUTURE_PRODUCTS`; user
decisions 2026-09-24, "Spot of valuation date" and C9, "the spot of its expiry date ... the last
spot on or before expiry, as FX"): S is USD per unit of the contract's quote currency at the last
official SPOT on or before the expiry (`valuation.usd_per_quote_on_or_before`, exact rows only,
1.0 for a USD contract), never the spot of the price's own date, so a CNY / JPY / EUR / GBP
contract freezes at `contracts x multiplier x (m - fill) x S`, `currency` = its quote currency,
and the note names the conversion pair and date. With no spot to convert it stays unfrozen with
the reason. A conversion that changes (the backfill's close replacing a live press, a row frozen
under the earlier price's-date rule) moves both `usd_entry_amount` and `spot_usd_per_local`, so
the row is re-frozen like any other and the `why` names the conversion.

LME forwards (LME_FWD, one of `valuation.FX_PRODUCTS`; user decision 2026-09-24, "P&L
conventions -> LME forwards"): the FX rule on the metal's root id ('LME:CA'), frozen at the
metal's last official cash price (SPOT) on or before the prompt date (`engine.lme.settlement_price`,
the lookup value_book's provisional row uses), S = 1, `mark_type` SPOT, the note naming the
cash price and its date (`_lme_freeze`).

FX_OPTION: frozen at the last official `PREMIUM` on or before
expiry, converted at that day's spot, same columns as an FX trade (`local_amount` =
base notional, `mark_type='PREMIUM'`). An expiry-day intrinsic-value mark would be more
exact than the last premium; until the options pricer writes one, the note says which
date's premium was used. A closed-out option (bought and sold back in full,
`engine.pnl.valuation.closed_out_options`) is frozen at its closing fill instead, converted
at the close-out date's spot (or the last official one before it), `mark_type='CLOSE_OUT'`:
it needs no PREMIUM, and the row records the figure value_book has shown since the close-out.

Rates swaps and NDFs left the app on 2026-09-24 (user decision, "Commodity conversion plan",
Phase 2): the ledger freezes neither any more. A row an older database still holds for one
(a retired product, `valuation.RETIRED_PRODUCTS`: IRS, SWAPTION, CAP_FLOOR; `mark_type` NDF_FIX or
PV_USD; or an NDF frozen at the fixing date's SPOT substitute, told by its note) is left exactly
as it is, never recomputed and never dropped, and named under 'kept' with that reason
(`_retired_row`; user decision 2026-09-24, C10: "an NDF keeps its figure"); the next upload
replaces the book and clears it. A leftover trade of a retired product with no frozen row is
never frozen by today's rules (value_book shows it blank with its reason).

`ltd(conn, d)` = sum of value_book(d).pnl_usd, NaN if any row is NaN, 0.0 for an empty
book (first trading day, not Unavailable). Periods subtract `ltd` at a reference
business day from a Mon-Fri + `config/holidays.txt` calendar (engine/pnl/aggregate.py).
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from collections import namedtuple
from operator import itemgetter
from typing import Dict, Optional

import re

import pandas as pd

from engine.pnl.aggregate import (_last_business_day_of_prev_month, _last_business_day_of_prev_year,
                                  _n_business_days_back, _prev_business_day, load_holidays)
from engine.lme import settlement_price as lme_settlement_price
from engine.pnl.valuation import (FUTURE_PRODUCTS, FX_PRODUCTS, LME_PRODUCTS, RETIRED_PRODUCTS, _BadValue, _number,
                                  close_out_ccy, closed_out_options, last_usd_conversion,
                                  option_fill_is_per_ounce, usd_per_quote, usd_per_quote_on_or_before,
                                  value_book)

GROUP_KEYS = ("instrument_id", "product", "strategy", "theme")


def _sql_in(products) -> str:
    return ", ".join(f"'{p}'" for p in products)


_OPEN_FX_SQL = f"""
SELECT t.trade_id, t.instrument_id, t.product, i.quote_ccy, t.quantity, t.price, l.settle_date
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ({_sql_in(FX_PRODUCTS)}) AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""

# Futures and the listed options valued like one (EQ_OPTION, CMDTY_OPTION; user decision 2026-09-24, C12).
_OPEN_FUTURE_SQL = f"""
SELECT t.trade_id, t.instrument_id, t.product, i.multiplier, t.quantity, t.price, l.settle_date, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product IN ({_sql_in(FUTURE_PRODUCTS)}) AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""

_OPEN_OPTION_SQL = """
SELECT t.trade_id, t.instrument_id, t.product, i.base_ccy, t.quantity, t.price, l.settle_date, i.quote_ccy
FROM trades_official t JOIN instruments i USING (instrument_id) JOIN trade_legs l USING (trade_id)
WHERE t.product = 'FX_OPTION' AND l.leg_no = 1 AND l.settle_date < :as_of
  AND t.trade_id NOT IN (SELECT trade_id FROM realised_pnl)
"""

# Every frozen row this call could freeze again, with what the standard rule needs to recompute it.
_FROZEN_SQL = """
SELECT r.trade_id, t.instrument_id, t.product, i.base_ccy, i.quote_ccy, i.multiplier, t.quantity, t.price,
       l.settle_date, r.mark_type, r.spot_as_of_date, r.local_amount, r.usd_entry_amount, r.spot_usd_per_local,
       r.pnl_usd, r.note
FROM realised_pnl r JOIN trades_official t USING (trade_id) JOIN instruments i ON i.instrument_id = t.instrument_id
JOIN trade_legs l ON l.trade_id = r.trade_id AND l.leg_no = 1
WHERE l.settle_date < :as_of
ORDER BY r.trade_id
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


# --------------------------------------------------------------------------- the freeze rule
# One function per product computes the frozen row from the official marks on file, and nothing
# else does: `realise_settled` writes what they return for a trade not yet in `realised_pnl`, and
# `purge_superseded` compares what they return now against every row already there.
_Freeze = namedtuple("_Freeze", "currency local_amount usd_entry_amount mark_type spot_usd_per_local "
                                "spot_as_of_date spot_source pnl_usd note conversion", defaults=(None,))
# `conversion`, a future's or listed option's only (never stored): _Conversion(price, s, pair, spot_date),
# the FUTURE_PX and the USD conversion it was frozen at, so a re-freeze can say which of the two moved.
_Conversion = namedtuple("_Conversion", "price s pair spot_date")


class _Unrealisable(Exception):
    """The trade cannot be frozen from the marks on file today; `str(exc)` is the reason."""


def _fx_freeze(conn, pair: str, quote_ccy: str, qty: float, fill: float, settle: str) -> _Freeze:
    m_hit = _last_on_or_before(conn, pair, "SPOT", settle)
    if m_hit is None:
        raise _Unrealisable(f"no official SPOT for {pair} on or before {settle}")
    m, m_day, m_src = m_hit
    s, _s_pair, _s_src = usd_per_quote(conn, quote_ccy, m_day)
    if s != s:
        raise _Unrealisable(f"no SPOT to convert {quote_ccy} to USD on or before {settle}")
    entry = qty * fill * s
    combined = m * s
    note = "" if m_day == settle else f"spot dated {m_day} (last before settlement)"
    return _Freeze(quote_ccy, qty, entry, "SPOT", combined, m_day, m_src, qty * combined - entry, note)


def _lme_freeze(conn, root: str, quote_ccy: str, qty: float, fill: float, settle: str) -> _Freeze:
    """An LME forward past its prompt date (user decision 2026-09-24, CLAUDE.md "P&L conventions
    -> LME forwards"): the FX-forward rule on the metal's root id, frozen at the metal's last
    official cash price (its SPOT) on or before the prompt, read by `engine.lme.settlement_price`,
    the very lookup `value_book`'s provisional row uses, so the frozen figure is the one shown.
    USD-quoted, so S = 1 (`usd_per_quote('USD')`); `mark_type` SPOT, as an FX forward."""
    m_hit = lme_settlement_price(conn, root, settle)
    if m_hit is None:
        raise _Unrealisable(f"no official cash price (SPOT) for {root} on or before its prompt {settle}")
    m, m_day, m_src = m_hit
    s, _s_pair, _s_src = usd_per_quote(conn, quote_ccy, m_day)
    if s != s:
        raise _Unrealisable(f"no SPOT to convert {quote_ccy} to USD on or before {settle}")
    entry = qty * fill * s
    combined = m * s
    note = f"cash price dated {m_day}" + ("" if m_day == settle else " (last before settlement)")
    return _Freeze(quote_ccy, qty, entry, "SPOT", combined, m_day, m_src, qty * combined - entry, note)


def _fx_or_lme_freeze(conn, product, pair, quote_ccy, qty, fill, settle) -> _Freeze:
    """The freeze of a product in `FX_PRODUCTS`: an LME forward's cash price, else the pair's SPOT."""
    if product in LME_PRODUCTS:
        return _lme_freeze(conn, pair, quote_ccy, qty, fill, settle)
    return _fx_freeze(conn, pair, quote_ccy, qty, fill, settle)


def _future_freeze(conn, pair: str, multiplier: float, qty: float, fill: float, settle: str,
                   quote_ccy: str) -> _Freeze:
    """A future or listed option past expiry: the last official FUTURE_PX on or before expiry
    (`m`, dated `m_day`), the P&L in the contract's quote currency converted to USD at the spot of
    its expiry date (user decisions 2026-09-24, "Spot of valuation date": a CNY, JPY, EUR or GBP
    contract is never frozen as dollars; and C9: "the last spot on or before expiry, as FX"). `S`
    is `valuation.usd_per_quote_on_or_before(quote_ccy, settle)`, exact official rows only, the
    conversion `value_book`'s provisional row shows (1.0 exactly for a USD contract, so a USD
    future freezes bit for bit as before); with no S the trade stays unfrozen with the reason,
    never frozen at 1. Entry and mark are both converted at that one S, so
    `pnl_usd = qty x multiplier x (m - fill) x S`. `spot_as_of_date` stays the price's date."""
    m_hit = _last_on_or_before(conn, pair, "FUTURE_PX", settle)
    if m_hit is None:
        raise _Unrealisable(f"no official FUTURE_PX for {pair} on or before {settle}")
    m, m_day, m_src = m_hit
    s, s_pair, _s_src, s_day = usd_per_quote_on_or_before(conn, quote_ccy, settle)
    if s != s or s_pair is None:
        raise _Unrealisable(f"no SPOT for USD conversion of {quote_ccy} on or before its expiry {settle}")
    combined = multiplier * m * s
    entry = qty * multiplier * fill * s
    notes = [] if m_day == settle else [f"settlement price dated {m_day} (last before expiry)"]
    if quote_ccy != "USD":
        notes.append(f"{quote_ccy} P&L converted at {s_pair} spot of {settle}" if s_day == settle else
                     f"{quote_ccy} P&L converted at {s_pair} spot dated {s_day} (last before expiry)")
    return _Freeze(quote_ccy, qty, entry, "FUTURE_PX", combined, m_day, m_src, qty * combined - entry, "; ".join(notes),
                   _Conversion(m, s, s_pair, str(s_day)))


def _option_freeze(conn, inst: str, base_ccy: str, quote_ccy: str, qty: float, fill: float, settle: str,
                   closed) -> _Freeze:
    if closed is not None:
        # Bought and sold back in full (engine.pnl.valuation, "Closed-out options"): frozen at the
        # closing fill, never a PREMIUM mark, and at the close-out date's spot, the figure value_book
        # has shown since the close-out, so every trade of the group is frozen alike.
        ccy = close_out_ccy(base_ccy, quote_ccy, fill)
        s_hit = last_usd_conversion(conn, ccy, closed.date)
        if s_hit is None:
            raise _Unrealisable(f"closed out {closed.date}; no official SPOT to convert {ccy} to USD on or before that date")
        s, s_day, s_src = s_hit
        entry = qty * fill * s
        combined = closed.price * s
        note = (f"closed out {closed.date} at the closing fill {closed.price:.10g}"
                + ("" if s_day == closed.date else f"; spot dated {s_day} (last before the close-out)"))
        return _Freeze(ccy, qty, entry, "CLOSE_OUT", combined, s_day, s_src, qty * combined - entry, note)
    m_hit = _last_on_or_before(conn, inst, "PREMIUM", settle)
    if m_hit is None:
        raise _Unrealisable(f"no official PREMIUM for {inst} on or before {settle}")
    m, m_day, m_src = m_hit
    s, _s_pair, _s_src = usd_per_quote(conn, base_ccy, m_day)
    if s != s:
        raise _Unrealisable(f"no SPOT to convert {base_ccy} to USD on or before {settle}")
    entry = qty * fill * s
    if option_fill_is_per_ounce(base_ccy, fill):
        # a metal option dealt in quote currency per ounce: its start value is in the
        # QUOTE currency (engine.pnl.valuation.option_fill_is_per_ounce)
        q, _q_pair, _q_src = usd_per_quote(conn, quote_ccy, m_day)
        if q != q:
            raise _Unrealisable(f"no SPOT to convert {quote_ccy} to USD on or before {settle}")
        entry = qty * fill * q
    combined = m * s
    note = f"premium dated {m_day}" + ("" if m_day == settle else " (last before expiry)")
    return _Freeze(base_ccy, qty, entry, "PREMIUM", combined, m_day, m_src, qty * combined - entry, note)


def _freeze_for(conn, product, pair, base_ccy, quote_ccy, multiplier, qty, fill, settle, closed) -> _Freeze:
    """The standard rule for one trade, whatever its product (`_Unrealisable` when it cannot
    be frozen today; a product this ledger does not freeze raises it too). `closed`: the
    trade's `CloseOut` when it is one of a closed-out option's trades, else None."""
    qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
    if product in FX_PRODUCTS:   # LME_FWD included (valuation.LME_PRODUCTS)
        return _fx_or_lme_freeze(conn, product, pair, quote_ccy, qty, fill, settle)
    if product in FUTURE_PRODUCTS:
        return _future_freeze(conn, pair, _number(multiplier, "instruments.multiplier"), qty, fill, settle, quote_ccy)
    if product == "FX_OPTION":
        return _option_freeze(conn, pair, base_ccy, quote_ccy, qty, fill, settle, closed)
    raise _Unrealisable(f"product {product} is not frozen by the ledger")


def _same_freeze(stored: tuple, fresh: _Freeze) -> bool:
    """`stored` = (mark_type, spot_as_of_date, local_amount, usd_entry_amount, spot_usd_per_local,
    pnl_usd) as read back from `realised_pnl`. The note and the source are not compared: a
    row whose figures did not change is left alone whatever its wording."""
    mark_type, spot_day, *numbers = stored
    if mark_type != fresh.mark_type or str(spot_day) != str(fresh.spot_as_of_date):
        return False
    fresh_numbers = (fresh.local_amount, fresh.usd_entry_amount, fresh.spot_usd_per_local, fresh.pnl_usd)
    return all(math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9) for a, b in zip(numbers, fresh_numbers))


_STORED_CONVERSION = re.compile(r"converted at (\S+) spot (of|dated) (\d{4}-\d{2}-\d{2})( \(INTERP)?")


def _conversion_why(stored: tuple, fresh: _Freeze, multiplier, note: str, settle: str) -> str:
    """The `why` of a non-USD future or listed option whose FUTURE_PX and amount are unchanged but
    whose USD conversion moved ('' when that is not what happened). The stored conversion is
    recovered as `spot_usd_per_local / (multiplier x price)`, and confirmed by the entry moving by
    the same factor. The stored note says which rule converted it: 'spot of <d>' with <d> not the
    expiry, or an estimated (INTERP) spot, is the price's-date rule that C9 replaced (user decision
    2026-09-24): "conversion moved to the spot of its expiry date"."""
    conv = fresh.conversion
    if conv is None or fresh.currency == "USD" or not multiplier or not conv.price:
        return ""
    _mark_type, _spot_day, local_amount, entry, combined, _pnl = stored
    s_old = float(combined) / (float(multiplier) * conv.price)
    close = lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)   # noqa: E731
    if not close(float(local_amount), float(fresh.local_amount)) or not close(float(entry) * conv.s,
                                                                                fresh.usd_entry_amount * s_old):
        return ""   # the price, the amount or the fill moved too: the generic sentence says which
    ccy, moved = fresh.currency, f"{s_old:.10g} -> {conv.s:.10g} USD per {fresh.currency}"
    found = _STORED_CONVERSION.search(note or "")
    if found is None:
        return f"{ccy} conversion now at {conv.pair} spot dated {conv.spot_date}, {moved}"
    old_day = found.group(3)
    if (found.group(2) == "of" and old_day != settle) or found.group(4):
        return (f"conversion moved to the spot of its expiry date: {ccy} at {conv.pair} spot dated {conv.spot_date}, "
                f"{moved} (was the spot of {old_day})")
    if old_day != conv.spot_date:
        return f"{ccy} conversion at {conv.pair} spot dated {conv.spot_date} replaced the one dated {old_day}, {moved}"
    return f"{ccy} conversion at {conv.pair} spot dated {conv.spot_date} {moved} (the marks of that date changed)"


def _why_refrozen(stored: tuple, fresh: _Freeze, multiplier=None, note: str = "", settle: str = "") -> str:
    """The `why` of a `refrozen` entry: one sentence naming what changed between the stored row
    and the fresh freeze. The mark type ("CLOSE_OUT replaced PREMIUM"), else the mark date, else a
    non-USD future's USD conversion alone (`_conversion_why`), else which stored figures moved for
    the same mark and date: `mark` is `spot_usd_per_local` (USD per unit of `local_amount`: the
    pair SPOT times its USD conversion, multiplier x FUTURE_PX times its USD conversion for a
    future), `entry` is `usd_entry_amount`, `amount` is `local_amount`."""
    mark_type, spot_day, local_amount, entry, combined, pnl = stored
    fresh_day = str(fresh.spot_as_of_date)
    if mark_type != fresh.mark_type:
        dates = "" if str(spot_day) == fresh_day else f" ({spot_day} -> {fresh_day})"
        return f"{fresh.mark_type} replaced {mark_type}{dates}"
    if str(spot_day) != fresh_day:
        return f"{mark_type} dated {fresh_day} replaced the one dated {spot_day}"
    conversion = _conversion_why(stored, fresh, multiplier, note, settle)
    if conversion:
        return conversion
    moved = [f"{label} {float(old):.10g} -> {float(new):.10g}"
             for label, old, new in (("mark", combined, fresh.spot_usd_per_local),
                                     ("entry", entry, fresh.usd_entry_amount),
                                     ("amount", local_amount, fresh.local_amount))
             if not math.isclose(float(old), float(new), rel_tol=1e-9, abs_tol=1e-9)]
    if not moved:
        moved = [f"pnl_usd {float(pnl):.10g} -> {float(fresh.pnl_usd):.10g}"]
    return f"{mark_type} {spot_day} {', '.join(moved)} (the marks of that date changed)"


_Purge = namedtuple("_Purge", "refrozen kept")

# Frozen rows of the kinds that left the app on 2026-09-24 (user decision, rates swaps and NDFs):
# an older database may still hold them until its next upload replaces the book.
_RETIRED_MARK_TYPES = ("NDF_FIX", "PV_USD")
# An NDF frozen at a SPOT under a retired NDF rule, told by the note that rule wrote (git history):
# 'spot dated <fixing date> (NDF fixing)...' (2026-09-21 to 09-24, the fixing date's spot substitute
# when no fix was on file), 'spot dated <d> (last before fixing)' (2026-09-21 to 09-22), and the
# 'present spot' of 2026-09-21. Today's FX rule never writes any of them.
_RETIRED_NDF_SPOT_NOTES = ("(NDF fixing)", "(last before fixing)", "present spot: none on file on or before settlement")


def _retired_row(product, mark_type, note="") -> str:
    """The `kept` reason for a frozen row of a retired kind (a swap or rate option, an NDF frozen at
    its fix, or an NDF frozen at the fixing date's SPOT substitute), '' for any other row. Such a
    row is never recomputed and never dropped (user decision 2026-09-24, C10: "an NDF keeps its
    figure"): the rule that froze it is gone, so recomputing it by another product's rule would
    move a figure nobody approved."""
    if product in RETIRED_PRODUCTS or mark_type in _RETIRED_MARK_TYPES:
        return (f"{product} row frozen at {mark_type}: rates swaps and NDFs left the app on 2026-09-24, "
                "so it is left as frozen until the next upload replaces the book")
    if mark_type == "SPOT" and product in FX_PRODUCTS and any(n in str(note or "") for n in _RETIRED_NDF_SPOT_NOTES):
        return (f"{product} row frozen at SPOT by the NDF fixing rule: rates swaps and NDFs left the app on "
                "2026-09-24, so it is left as frozen until the next upload replaces the book")
    return ""


def purge_superseded(conn: sqlite3.Connection, as_of: str, closed_out: dict) -> _Purge:
    """Delete every `realised_pnl` row whose frozen figure is no longer what the official marks
    on file give for the same trade by the standard rule (`_freeze_for`: mark type, mark date,
    or any stored figure differs) -- user decision 2026-09-22, the re-freeze at the close (module
    docstring). Returns `(refrozen, kept)`, both sorted by trade_id so a status file written
    from them is stable:
        refrozen: one dict per dropped row, {trade_id, product, mark_type, spot_as_of_date (the
                  fresh freeze's), pnl_from (the dropped row's pnl_usd), pnl_to (the fresh
                  freeze's), why (`_why_refrozen`: what changed, in one sentence)}
        kept:     one dict per row the rule could not recompute today (a mark since gone, a
                  figure that is not a number, a row of a kind that left the app: a swap or an
                  NDF frozen at its fix or at its fixing date's spot, `_retired_row`), {trade_id, product, reason}; the row
                  keeps its figure, a trade that has one is never left blank by this (reviewer
                  m-2, 2026-09-22: it was silent before).
    Nothing is recomputed into the table here: `realise_settled` freezes the dropped rows afresh,
    by the same rule, in the same call -- which is why only tickets with `settle_date < as_of` are
    looked at (reviewer, 2026-09-22): the refreeze covers those alone, so a past-day call from
    the backfill never drops a row it cannot freeze again."""
    refrozen, kept = [], []
    for (trade_id, pair, product, base_ccy, quote_ccy, multiplier, qty, fill, settle,
         mark_type, spot_day, local_amount, entry, combined, pnl, note) in conn.execute(
            _FROZEN_SQL, {"as_of": as_of}).fetchall():
        retired = _retired_row(product, mark_type, note)
        if retired:
            kept.append({"trade_id": trade_id, "product": product, "reason": retired})
            continue
        try:
            fresh = _freeze_for(conn, product, pair, base_ccy, quote_ccy, multiplier, qty, fill, settle,
                                closed_out.get(trade_id))
        except (_Unrealisable, TypeError, ValueError, ArithmeticError) as exc:
            kept.append({"trade_id": trade_id, "product": product, "reason": _unrealisable(trade_id, exc)["reason"]})
            continue
        stored = (mark_type, spot_day, local_amount, entry, combined, pnl)
        if not _same_freeze(stored, fresh):
            refrozen.append({"trade_id": trade_id, "product": product, "mark_type": fresh.mark_type,
                             "spot_as_of_date": str(fresh.spot_as_of_date), "pnl_from": float(pnl),
                             "pnl_to": float(fresh.pnl_usd),
                             "why": _why_refrozen(stored, fresh, multiplier, note, str(settle))})
    refrozen.sort(key=itemgetter("trade_id"))
    kept.sort(key=itemgetter("trade_id"))
    conn.executemany("DELETE FROM realised_pnl WHERE trade_id = ?", [(e["trade_id"],) for e in refrozen])
    return _Purge(refrozen, kept)


def _unrealisable(trade_id, exc: Exception) -> dict:
    """The `unrealisable` entry for a trade one of whose stored figures is not a number
    (`engine.pnl.valuation._BadValue` names the table.column and the value) or whose
    arithmetic otherwise failed: that ONE trade is skipped and named, the rest of the
    book is still realised -- before 2026-09-18 the exception aborted the whole step."""
    if isinstance(exc, _Unrealisable):
        return {"trade_id": trade_id, "reason": str(exc)}
    detail = str(exc) if isinstance(exc, _BadValue) else f"could not be realised ({type(exc).__name__}: {exc})"
    return {"trade_id": trade_id, "reason": f"trade {trade_id}: {detail}"}


# --------------------------------------------------------------------------- realise
def realise_settled(conn: sqlite3.Connection, as_of: str) -> dict:
    """Freeze P&L for the trades whose settle date is before `as_of` and are not yet in
    `realised_pnl`, after dropping every frozen row the marks on file no longer support.
    Returns {'realised': n, 'unrealisable': [{trade_id, reason}], 'repaired': [trade_id, ...],
    'refrozen': [{trade_id, product, mark_type, spot_as_of_date, pnl_from, pnl_to, why}],
    'kept': [{trade_id, product, reason}]}.

    `refrozen` (2026-09-22): the rows `purge_superseded` dropped because their frozen mark or
    spot is no longer what the official marks give for the same date and mark type (a live
    press replaced by the day's 15:00 close, a PX_LAST by PX_SETTLE, a PREMIUM by a close-out);
    they are frozen again below, in this same call. Each
    entry records the figure the trade had (`pnl_from`), the one it takes (`pnl_to`, with the
    new row's `mark_type` and `spot_as_of_date`) and `why`, one sentence naming what changed
    (reviewer M-2, user yes 2026-09-22; the status file and the Market data tab show them),
    sorted by trade_id. `kept`: the frozen rows the rule could not recompute today (a mark since
    gone, a stored figure that is not a number, a swap or NDF row of an older database), with the
    reason; each keeps its figure.
    Only rows with `settle_date < as_of` are looked at, the ones this call re-freezes.

    `repaired` (2026-09-18): rows an earlier, positional INSERT misaligned
    (`_insert_realised`) are deleted first (`purge_unreadable_realised`), so the trades
    they belonged to are frozen afresh below like any trade not yet realised -- a
    database the old INSERT corrupted heals itself on the next Bloomberg pull, no manual
    step. One trade with a stored figure that is not a number is reported as
    unrealisable (`_unrealisable`) instead of aborting the step for the whole book."""
    realised, unrealisable = 0, []
    repaired = purge_unreadable_realised(conn)
    closed_out = closed_out_options(conn, as_of)
    refrozen, kept = purge_superseded(conn, as_of, closed_out)

    def freeze(trade_id, inst, product, fresh: _Freeze, settle):
        _insert_realised(conn, trade_id, inst, product, fresh.currency, settle, fresh.local_amount,
                         fresh.usd_entry_amount, fresh.mark_type, fresh.spot_usd_per_local, fresh.spot_as_of_date,
                         fresh.spot_source, fresh.pnl_usd, fresh.note)

    for trade_id, pair, product, quote_ccy, qty, fill, settle in conn.execute(_OPEN_FX_SQL, {"as_of": as_of}).fetchall():
        try:
            qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
            freeze(trade_id, pair, product, _fx_or_lme_freeze(conn, product, pair, quote_ccy, qty, fill, settle), settle)
            realised += 1
        except (_Unrealisable, TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    for trade_id, pair, product, multiplier, qty, fill, settle, quote_ccy in conn.execute(
            _OPEN_FUTURE_SQL, {"as_of": as_of}).fetchall():
        try:
            qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
            multiplier = _number(multiplier, "instruments.multiplier")
            freeze(trade_id, pair, product, _future_freeze(conn, pair, multiplier, qty, fill, settle, quote_ccy), settle)
            realised += 1
        except (_Unrealisable, TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    for trade_id, inst, product, base_ccy, qty, fill, settle, quote_ccy in conn.execute(_OPEN_OPTION_SQL, {"as_of": as_of}).fetchall():
        try:
            qty, fill = _number(qty, "trades.quantity"), _number(fill, "trades.price")
            fresh = _option_freeze(conn, inst, base_ccy, quote_ccy, qty, fill, settle, closed_out.get(trade_id))
            freeze(trade_id, inst, product, fresh, settle)
            realised += 1
        except (_Unrealisable, TypeError, ValueError, ArithmeticError) as exc:
            unrealisable.append(_unrealisable(trade_id, exc))

    conn.commit()
    return {"realised": realised, "unrealisable": unrealisable, "repaired": repaired, "refrozen": refrozen,
            "kept": kept}


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
