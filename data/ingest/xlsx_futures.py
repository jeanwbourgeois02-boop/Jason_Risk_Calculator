"""Futures fills from the workbook's 'All FX trades' sheet.

The BNP snapshot carries only one netted position per futures contract with an average
cost (see data/ingest/bnp.py module docstring), never per-fill price or date, so futures
trades / trade_legs must come from the xlsx blotter instead. FX rows of the same sheet
are still NOT imported (CLAUDE.md: "FX rows of the workbook are still NOT imported").

A futures row is any 'All FX trades' row whose pair (column B) ends in ' Index'. Column
C ('Quantity') holds a saved formula of the shape '=<n>*E<row>*50' or '=<n>*50*E<row>'
(the two orders both occur in the reference workbook) recovering the contract count
`n` from the fill in column E and the fixed multiplier 50. If the workbook was saved
without the formula (a plain cached number only -- openpyxl always sees the formula
string when one is present, data_only load is not needed here) contracts are instead
recovered as `C / (50 * E)`.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Union

import openpyxl

from data.ingest import schema
from data.ingest.bnp import third_friday

SHEET = "All FX trades"
FUTURES_FORMULA_RE = re.compile(r"^=(-?\d+)\*E\d+\*50$|^=(-?\d+)\*50\*E\d+$")
INDEX_PAIR_RE = re.compile(r"^([A-Z]+)([FGHJKMNQUVXZ])(\d) Index$")
MONTH_CODES = "FGHJKMNQUVXZ"


@dataclass(frozen=True)
class FuturesFill:
    row: int
    trade_date: str
    pair: str          # e.g. 'ESU6 Index'
    root: str           # e.g. 'ES'
    contracts: float
    fill: float
    settle_date: str    # column D, the value/expiry date saved in the workbook


def _as_iso(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _extract_contracts(cell_value, fill: float, row: int) -> float:
    if hasattr(cell_value, "text"):  # openpyxl ArrayFormula
        cell_value = cell_value.text
    if isinstance(cell_value, str) and cell_value.strip().startswith("="):
        m = FUTURES_FORMULA_RE.match(cell_value.replace(" ", ""))
        if not m:
            raise ValueError(f"row {row}: unrecognised futures Quantity formula {cell_value!r}")
        return float(m.group(1) or m.group(2))
    # No formula saved: only the computed value is on disk. Recover contracts from it.
    if fill == 0:
        raise ValueError(f"row {row}: fill is zero, cannot recover contracts from a bare value")
    return float(cell_value) / (50.0 * fill)


def _expiry_for(pair: str, trade_date: date, row: int) -> str:
    m = INDEX_PAIR_RE.match(pair)
    if not m:
        raise ValueError(f"row {row}: unrecognised futures pair {pair!r}, expected '<ROOT><code><digit> Index'")
    root, mcode, ydigit = m.groups()
    month = MONTH_CODES.index(mcode) + 1
    year = (trade_date.year // 10) * 10 + int(ydigit)
    if year < trade_date.year - 1:
        year += 10
    return third_friday(year, month).isoformat()


def read_futures_fills(xlsx_path: Union[str, Path]) -> List[FuturesFill]:
    """Pure parse: every row of 'All FX trades' whose pair ends in ' Index'."""
    book = openpyxl.load_workbook(str(xlsx_path), data_only=False)
    try:
        sheet = book[SHEET]
        out: List[FuturesFill] = []
        for row_no in range(2, sheet.max_row + 1):
            pair = sheet.cell(row_no, 2).value
            if not isinstance(pair, str) or not pair.endswith(" Index"):
                continue
            trade_date_raw = sheet.cell(row_no, 1).value
            fill = sheet.cell(row_no, 5).value
            settle_raw = sheet.cell(row_no, 4).value
            if trade_date_raw is None or fill is None or settle_raw is None:
                continue
            fill = float(fill)
            trade_date_iso = _as_iso(trade_date_raw)
            contracts = _extract_contracts(sheet.cell(row_no, 3).value, fill, row_no)
            m = INDEX_PAIR_RE.match(pair)
            if not m:
                raise ValueError(f"row {row_no}: unrecognised futures pair {pair!r}")
            root = m.group(1)
            out.append(FuturesFill(
                row=row_no, trade_date=trade_date_iso, pair=pair, root=root,
                contracts=contracts, fill=fill, settle_date=_as_iso(settle_raw),
            ))
        return out
    finally:
        book.close()


def load_futures_fills(xlsx_path: Union[str, Path], conn: sqlite3.Connection) -> int:
    """Insert xlsx futures fills as trades/trade_legs. Idempotent: trade_id is
    deterministic ('XL-<row>'), so a row already present is left untouched."""
    schema.create_schema(conn)
    fills = read_futures_fills(xlsx_path)
    existing = {r[0] for r in conn.execute("SELECT trade_id FROM trades WHERE source = 'XLSX'")}
    inherited = dict(conn.execute("SELECT instrument_id, theme FROM instrument_theme"))
    inserted = 0
    with conn:
        for f in fills:
            trade_id = f"XL-{f.row}"
            if trade_id in existing:
                continue
            instrument_id = f.pair
            multiplier = 50.0
            conn.execute(
                "INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                (instrument_id, "FUTURE", f.root, "USD", multiplier, 0, instrument_id,
                 _expiry_for(f.pair, date.fromisoformat(f.trade_date), f.row)))
            conn.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade_id, "XLSX", instrument_id, "FUTURE", trade_id, f.trade_date,
                 f.contracts, f.fill, "", "", "", "", f"XLSX futures fill row {f.row}",
                 inherited.get(instrument_id, "")))
            conn.execute(
                "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                (trade_id, 1, "NOTIONAL", "USD", f.contracts * multiplier * f.fill,
                 f.trade_date, f.settle_date, 0.0, 0))
            inserted += 1
    return inserted
