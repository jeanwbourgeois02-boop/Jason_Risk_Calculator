"""One row of the curve per open position: a commodity future, an option on one, an LME forward.

Every row has the same keys (see ``engine.curve.positions.curve_positions``). The prices are the
official marks dated ``as_of`` exactly (``engine.pnl.valuation._mark_at``, ``marks_official``
only) and ``S`` the exact official SPOT of ``as_of`` (``engine.ladder.futures_delta.usd_per_unit``):
neither is ever estimated from near marks (hard rule 2), so a missing one leaves the figure that
needs it None with the reason, never zero and never filled.

The delta, in futures-equivalent lots: ``delta_lots = lots x delta_factor`` and
``delta_usd = delta_lots x multiplier x price x S``, with ``price`` the underlying future's for an
option. ``delta_factor`` is 1 for a future (the averaging share for a monthly-average contract,
``engine.curve.averaging``), the official ``DELTA`` mark for an option, 1 for an LME forward.
"""

from __future__ import annotations

import math
import re
from typing import List, Optional, Tuple

from data.contracts import UnknownContract, contract_for, option_for
from engine.curve.averaging import averaging_factor
from engine.ladder.futures_delta import usd_per_unit
from engine.pnl.valuation import _mark_at

FUTURE, OPTION, LME = "FUTURE", "CMDTY_OPTION", "LME_FWD"
PROMPT = "PROMPT"   # dates_source of an LME forward: its prompt date is the ticket's own, never estimated
NO_DELTA = "no DELTA mark: the option has not been priced"


def month_key(year: int, month: int) -> str:
    """The grid's column key: (2026, 12) -> '2026-12'."""
    return f"{int(year):04d}-{int(month):02d}"


def root_key(base_ccy) -> str:
    return re.sub(r"\s+", "", str(base_ccy or "")).upper()


def number(value) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _mark(conn, instrument_id: str, settle: str, mark_type: str, as_of: str,
          label: str) -> Tuple[Optional[float], str, str]:
    """(value, source, reason): the official mark dated `as_of` exactly, or None and why."""
    hit = _mark_at(conn, instrument_id, settle, mark_type, as_of)
    if hit is None:
        return None, "", f"no {mark_type} for {instrument_id} ({label} {settle}) on {as_of}"
    value = number(hit[0])
    if value is None:
        return None, str(hit[1]), f"{mark_type} for {instrument_id} on {as_of} is not a number ({hit[0]!r})"
    return value, str(hit[1]), ""


def _blank(g: dict, product: str, contract_id: str) -> dict:
    lots = g["lots"]
    return {
        "product": product, "root_id": g["root_key"], "name": g["root_key"], "sector": "", "subsector": "",
        "exchange": "", "currency": g["currency"], "contract_id": contract_id,
        "instrument_id": g["instrument_id"], "underlying_id": None, "month": None, "year": None,
        "month_code": "", "expiry": g["expiry"], "first_notice": None, "dates_source": "",
        "lots": lots, "gross_lots": abs(lots), "units": None, "unit": "", "multiplier": None,
        "price": None, "price_source": "", "usd_per_unit": None, "usd_source": "",
        "notional_local": None, "notional_usd": None,
        "delta_factor": None, "delta_lots": None, "delta_units": None, "delta_local": None, "delta_usd": None,
        "trade_ids": list(g["trade_ids"]), "note": "", "reason": "",
    }


def _checks(g: dict, root, row: dict, reasons: List[str], per_lot_multiplier: bool = True) -> bool:
    """Currency and multiplier on file against contract-master's; True when both agree. An LME
    forward's instrument carries the multiplier per tonne (its quantity is tonnes)."""
    ok = True
    if row["currency"] != root.currency:
        reasons.append(f"currency on file {row['currency']!r} is not contract-master's {root.currency!r}")
        ok = False
    expected = root.multiplier if per_lot_multiplier else root.multiplier / root.contract_size
    on_file = number(g["multiplier_on_file"])
    if on_file is None or abs(on_file - expected) > 1e-9 * max(1.0, expected):
        reasons.append(f"multiplier on file {g['multiplier_on_file']!r} is not contract-master's {expected:g}")
        ok = False
    return ok


def _conversion(conn, root, row: dict, reasons: List[str], as_of: str) -> Optional[float]:
    s, s_note = usd_per_unit(conn, root.currency, as_of)
    if s is None:
        reasons.append(s_note)
    else:
        row["usd_per_unit"], row["usd_source"] = s, s_note
    return s


def _delta(row: dict, root, price: Optional[float], s: Optional[float], inputs_ok: bool) -> None:
    """delta_lots / units / local / usd from the row's lots and delta_factor."""
    if row["delta_factor"] is None or row["units"] is None:
        return
    row["delta_lots"] = row["lots"] * row["delta_factor"]
    row["delta_units"] = row["delta_lots"] * root.contract_size
    if price is not None and inputs_ok:
        row["delta_local"] = row["delta_lots"] * root.multiplier * price
        if s is not None:
            row["delta_usd"] = row["delta_local"] * s


def _finish(row: dict, reasons: List[str], notes: List[str]) -> dict:
    row["reason"] = "; ".join(reasons)
    row["note"] = "; ".join(n for n in notes if n)
    return row


def future_row(conn, g: dict, root, as_of: str) -> dict:
    """An open commodity future (the Phase 1 row, plus its delta)."""
    reasons: List[str] = []
    notes: List[str] = []
    row = _blank(g, FUTURE, g["instrument_id"])
    row["underlying_id"] = g["instrument_id"]
    if g["bad_quantity"]:
        reasons.append(f"quantity is not a number on {', '.join(g['bad_quantity'])}")
    if root is None:
        reasons.append(f"contract root {g['root_key']} is not in config/contracts.csv")
        return _finish(row, reasons, notes)
    row.update(name=root.name, sector=root.sector, subsector=root.subsector, exchange=root.exchange,
               unit=root.size_unit, multiplier=root.multiplier)
    if not g["bad_quantity"]:
        row["units"] = row["lots"] * root.contract_size
    cm = None
    try:
        cm = contract_for(root.root_id, g["instrument_id"], conn=conn)
    except UnknownContract as exc:
        reasons.append(str(exc))
    else:
        row.update(month=cm.month, year=cm.year, month_code=cm.month_code, dates_source=cm.dates_source,
                   first_notice=cm.first_notice_date.isoformat() if cm.first_notice_date else None)
    inputs_ok = _checks(g, root, row, reasons) and not g["bad_quantity"]
    price, source, why = _mark(conn, g["instrument_id"], g["expiry"], "FUTURE_PX", as_of, "expiry")
    row["price"], row["price_source"] = price, source
    if why:
        reasons.append(why)
    s = _conversion(conn, root, row, reasons, as_of)
    if price is not None and inputs_ok:
        row["notional_local"] = row["lots"] * root.multiplier * price
        if s is not None:
            row["notional_usd"] = row["notional_local"] * s
    if not root.averaging:
        row["delta_factor"] = 1.0
    elif cm is None:
        reasons.append("an averaging contract with no contract month: its delta is not known")
    else:
        factor, note, why = averaging_factor(root, cm.month, cm.year, as_of)
        row["delta_factor"] = factor
        notes.append(note)
        if why:
            reasons.append(why)
    _delta(row, root, price, s, inputs_ok)
    return _finish(row, reasons, notes)


def _future_expiry(conn, contract_id: str, fallback: str) -> str:
    """The key the underlying future's FUTURE_PX is stored under: its instrument's expiry when
    it is on file (Bloomberg's date once applied), else contract-master's."""
    hit = conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = ?", (contract_id,)).fetchone()
    return str(hit[0]) if hit and hit[0] else fallback


def option_row(conn, g: dict, root, as_of: str) -> dict:
    """An open option on a commodity future: its lots under the underlying's contract month, its
    delta from the official DELTA mark, priced at the underlying future. No notional."""
    reasons: List[str] = []
    notes = ["an option: its exposure is its delta in futures-equivalent lots; it has no notional"]
    row = _blank(g, OPTION, g["instrument_id"])
    if g["bad_quantity"]:
        reasons.append(f"quantity is not a number on {', '.join(g['bad_quantity'])}")
    if root is None:
        reasons.append(f"contract root {g['root_key']} is not in config/contracts.csv")
        return _finish(row, reasons, notes)
    row.update(name=root.name, sector=root.sector, subsector=root.subsector, exchange=root.exchange,
               unit=root.size_unit, multiplier=root.multiplier)
    if not g["bad_quantity"]:
        row["units"] = row["lots"] * root.contract_size
    und = None
    try:
        oc = option_for(root.root_id, g["instrument_id"], conn=conn)
    except UnknownContract as exc:
        reasons.append(str(exc))
    else:
        und = oc.underlying
        row.update(underlying_id=und.contract_id, month=und.month, year=und.year, month_code=und.month_code,
                   dates_source=oc.dates_source)
    inputs_ok = _checks(g, root, row, reasons) and not g["bad_quantity"]
    delta, _src, why = _mark(conn, g["instrument_id"], g["expiry"], "DELTA", as_of, "expiry")
    if delta is None:
        reasons.append(NO_DELTA if not _src else why)
    row["delta_factor"] = delta
    price = None
    if und is not None:
        key = _future_expiry(conn, und.contract_id, und.last_trade_date.isoformat())
        price, source, why = _mark(conn, und.contract_id, key, "FUTURE_PX", as_of, "expiry")
        row["price"], row["price_source"] = price, source
        if why:
            reasons.append(f"underlying: {why}")
    s = _conversion(conn, root, row, reasons, as_of)
    _delta(row, root, price, s, inputs_ok)
    return _finish(row, reasons, notes)


def lme_row(conn, g: dict, root, per_lot: float, as_of: str) -> dict:
    """An open LME forward (``root`` its metal's contract root, ``per_lot`` its tonnes per lot,
    ``engine.lme.lot_tonnes``): its tonnes as lots under the prompt's month, priced at the exact
    official FWD_OUTRIGHT for its prompt date, in USD (S = 1 for a USD metal)."""
    reasons: List[str] = []
    notes: List[str] = []
    prompt = g["expiry"]
    tonnes = g["lots"]
    lots = tonnes / per_lot
    row = _blank(g, LME, f"{g['instrument_id']} {prompt}")
    row.update(lots=lots, gross_lots=abs(lots), dates_source=PROMPT, name=root.name, sector=root.sector,
               subsector=root.subsector, exchange=root.exchange, unit=root.size_unit, multiplier=root.multiplier)
    if g["bad_quantity"]:
        reasons.append(f"quantity is not a number on {', '.join(g['bad_quantity'])}")
    else:
        row["units"] = tonnes
    try:
        row.update(year=int(prompt[:4]), month=int(prompt[5:7]))
    except (TypeError, ValueError):
        reasons.append(f"prompt date {prompt!r} is not a date")
    if abs(lots - round(lots)) > 1e-9:
        notes.append(f"{tonnes:g} t is not a whole number of {per_lot:g} t lots")
    inputs_ok = _checks(g, root, row, reasons, per_lot_multiplier=False) and not g["bad_quantity"]
    price, source, why = _mark(conn, g["instrument_id"], prompt, "FWD_OUTRIGHT", as_of, "prompt")
    row["price"], row["price_source"] = price, source
    if why:
        reasons.append(why)
    s = _conversion(conn, root, row, reasons, as_of)
    if price is not None and inputs_ok:
        row["notional_local"] = lots * root.multiplier * price
        if s is not None:
            row["notional_usd"] = row["notional_local"] * s
    row["delta_factor"] = 1.0
    _delta(row, root, price, s, inputs_ok)
    return _finish(row, reasons, notes)
