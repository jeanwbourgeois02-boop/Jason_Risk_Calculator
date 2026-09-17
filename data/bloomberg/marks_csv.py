"""Canonical marks CSV format: read/write helpers and the ``marks`` table loader.

This is the shared format used by:
- data/bloomberg/pull_marks.py (the standalone Bloomberg-machine script; it duplicates
  MARKS_COLUMNS rather than importing this module, since it must be copyable as a
  single file with no repo imports)

Columns are exactly the ``marks`` table columns, in the same order as CLAUDE.md
"Data contract -> Tables" (see data/ingest/schema.py).
"""
from __future__ import annotations

import csv
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Union

MARKS_COLUMNS = ["as_of_date", "instrument_id", "settle_date", "mark_type", "value", "source", "snapped_at"]

# CLAUDE.md "Data contract -> Tables" (marks.mark_type comment).
MARK_TYPES = {
    "SPOT", "FWD_OUTRIGHT", "FUTURE_PX", "PAR_RATE", "PV_USD", "DV01_USD", "PREMIUM", "DELTA",
}

# CLAUDE.md "Data contract -> Tables" (marks.source comment) plus BBG_INTERP: a new
# non-official source for FWD_OUTRIGHT rows produced by linear interpolation of forward
# points between standard tenors (see pull_marks.py). It is never official: the
# marks_official view (data/ingest/schema.py OFFICIAL_MARK_SOURCE) is unchanged, so
# BBG_INTERP rows never win over BBG_BFXFORWARD.
SOURCES = {"BNP_BVAL", "BBG_BFXFORWARD", "BBG_BDH", "BBG_BDP", "MANUAL", "BBG_INTERP", "WORKBOOK_REFERENCE"}


@dataclass(frozen=True)
class MarkRow:
    as_of_date: str
    instrument_id: str
    settle_date: str
    mark_type: str
    value: float
    source: str
    snapped_at: str

    def as_tuple(self) -> tuple:
        return (self.as_of_date, self.instrument_id, self.settle_date, self.mark_type,
                self.value, self.source, self.snapped_at)


@dataclass(frozen=True)
class MarkReject:
    row_no: int  # 1-based data row number (header excluded, i.e. first data row = 1)
    detail: str


@dataclass
class LoadResult:
    n_read: int = 0
    n_loaded: int = 0
    rows: List[MarkRow] = field(default_factory=list)
    rejects: List[MarkReject] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejects


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_iso_date(s: str) -> bool:
    """Strict YYYY-MM-DD only. date.fromisoformat() alone is not enough on 3.11+, which
    also accepts compact 'YYYYMMDD' and other ISO 8601 variants; the regex is checked
    first so e.g. '20260817' is rejected rather than silently accepted."""
    if not isinstance(s, str) or not _DATE_RE.match(s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def _is_valid_snapped_at(s) -> bool:
    """A timezone-aware ISO timestamp (CLAUDE.md "P&L conventions -> Mark time": every
    mark row carries snapped_at with the resolved offset for that row). A naive
    timestamp (no offset) is rejected, not just an empty string."""
    if not isinstance(s, str) or not s.strip():
        return False
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return False
    return dt.tzinfo is not None


def _validate_mark_row(row: "MarkRow", known_instruments: set, existing_keys: set) -> Optional[str]:
    """Validate an already-typed MarkRow against MARK_TYPES/SOURCES/instruments/date
    formats/value finiteness/snapped_at tz-awareness/duplicate primary key. Returns a
    reject reason string, or None if the row is good (in which case its key is added to
    ``existing_keys`` so a later duplicate in the same batch is also caught).

    Shared by load_marks_csv (CSV-sourced rows) and load_mark_rows (already-typed rows,
    e.g. from bnp_marks.extract_bnp_marks), so both paths reject duplicates and unknown
    instruments the same way instead of one of them hitting a raw sqlite IntegrityError
    or foreign-key failure.
    """
    if row.mark_type not in MARK_TYPES:
        return f"unknown mark_type: {row.mark_type!r}"
    if row.source not in SOURCES:
        return f"unknown source: {row.source!r}"
    if row.instrument_id not in known_instruments:
        return f"unknown instrument_id (not in instruments): {row.instrument_id!r}"
    if not _is_iso_date(row.as_of_date):
        return f"as_of_date not ISO YYYY-MM-DD: {row.as_of_date!r}"
    if not _is_iso_date(row.settle_date):
        return f"settle_date not ISO YYYY-MM-DD: {row.settle_date!r}"
    if not isinstance(row.value, (int, float)) or isinstance(row.value, bool) or not math.isfinite(row.value):
        return f"value is not a finite number: {row.value!r}"
    if not _is_valid_snapped_at(row.snapped_at):
        return f"snapped_at is not a timezone-aware ISO timestamp: {row.snapped_at!r}"

    key = (row.as_of_date, row.instrument_id, row.settle_date, row.mark_type, row.source)
    if key in existing_keys:
        return f"duplicate primary key (in file or already in marks): {key}"
    existing_keys.add(key)
    return None


def write_marks_csv(rows: Iterable[Union[MarkRow, Sequence, dict]], path: Union[str, Path]) -> None:
    """Write ``rows`` to ``path`` in the canonical marks CSV format (MARKS_COLUMNS header).

    Each row may be a MarkRow, a dict keyed by MARKS_COLUMNS, or a 7-tuple/list in
    MARKS_COLUMNS order.
    """
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(MARKS_COLUMNS)
        for r in rows:
            if isinstance(r, MarkRow):
                w.writerow(r.as_tuple())
            elif isinstance(r, dict):
                w.writerow([r[c] for c in MARKS_COLUMNS])
            else:
                w.writerow(list(r))


def _validate_rows(
    reader: Iterable[Dict[str, str]],
    known_instruments: set,
    existing_keys: set,
) -> tuple:
    """Validate raw CSV dict-rows. Returns (good: List[MarkRow], rejects: List[MarkReject]).

    ``existing_keys`` is mutated in place as good rows are accepted, so a duplicate
    within the file (not just against the DB) is caught on the second occurrence.

    CSV-format-only concerns (missing/blank columns, value not parsing as a float at
    all, e.g. "abc") are checked here before a MarkRow is built; everything else
    (mark_type/source/instrument validity, date format, value finiteness -- "nan" and
    "inf" both parse as float but are rejected here, snapped_at tz-awareness, duplicate
    key) is delegated to the shared ``_validate_mark_row`` so CSV-sourced and
    object-sourced (bnp_marks) rows are held to the same standard.
    """
    good: List[MarkRow] = []
    rejects: List[MarkReject] = []
    for i, raw in enumerate(reader):
        row_no = i + 1
        missing = [c for c in MARKS_COLUMNS if c not in raw or raw[c] is None or raw[c] == ""]
        # value is allowed to be "0"; only truly missing/blank cells count as missing.
        if missing:
            rejects.append(MarkReject(row_no, f"missing/blank column(s): {missing}"))
            continue
        try:
            value = float(raw["value"])
        except (TypeError, ValueError):
            rejects.append(MarkReject(row_no, f"value does not parse as float: {raw['value']!r}"))
            continue

        row = MarkRow(
            raw["as_of_date"], raw["instrument_id"], raw["settle_date"], raw["mark_type"],
            value, raw["source"], str(raw["snapped_at"]),
        )
        reason = _validate_mark_row(row, known_instruments, existing_keys)
        if reason:
            rejects.append(MarkReject(row_no, reason))
            continue
        good.append(row)
    return good, rejects


def load_mark_rows(
    rows: Sequence["MarkRow"],
    conn: sqlite3.Connection,
    strict: bool = True,
    name: str = "rows",
) -> LoadResult:
    """Validate already-typed MarkRow objects the same way load_marks_csv validates a
    CSV (unknown mark_type/source/instrument, bad date format, non-finite value, naive
    snapped_at, duplicate primary key against the DB or within the batch) and insert the
    survivors into ``marks``.

    This is what bnp_marks.load_bnp_marks calls instead of inserting directly, so
    re-running it on the same file (or against a DB that already has BNP_BVAL rows for
    that date) produces duplicate rejects rather than a raw sqlite IntegrityError, and an
    unknown instrument_id is a reject rather than a foreign-key failure.

    strict=True (default): any reject raises ValueError (listing the first ones) before
    anything is inserted. strict=False: good rows are inserted, rejects are returned.
    """
    known_instruments = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    existing_keys = {
        tuple(r) for r in conn.execute(
            "SELECT as_of_date, instrument_id, settle_date, mark_type, source FROM marks"
        )
    }

    rows = list(rows)
    good: List[MarkRow] = []
    rejects: List[MarkReject] = []
    for i, row in enumerate(rows):
        row_no = i + 1
        reason = _validate_mark_row(row, known_instruments, existing_keys)
        if reason:
            rejects.append(MarkReject(row_no, reason))
        else:
            good.append(row)

    result = LoadResult(n_read=len(rows), n_loaded=0, rows=good, rejects=rejects)

    if strict and rejects:
        head = [f"row {r.row_no}: {r.detail}" for r in rejects[:10]]
        raise ValueError(
            f"{name}: {len(rejects)} reject(s); nothing loaded (strict=True). First: "
            + " | ".join(head)
        )

    with conn:
        conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [r.as_tuple() for r in good])
    result.n_loaded = len(good)
    return result


def load_marks_csv(path: Union[str, Path], conn: sqlite3.Connection, strict: bool = True) -> LoadResult:
    """Load a canonical marks CSV into ``marks``.

    Validation per row: ISO YYYY-MM-DD dates for as_of_date/settle_date, mark_type in
    MARK_TYPES, source in SOURCES, value parses as float, instrument_id exists in
    ``instruments``, snapped_at non-empty, and no duplicate primary key
    (as_of_date, instrument_id, settle_date, mark_type, source) either within the file
    or against rows already present in ``marks``.

    Row numbers in rejects are 1-based *data* rows (the header line is not counted;
    the first data row is row 1).

    strict=True (default): any reject raises ValueError (listing all rejects) before
    anything is inserted. strict=False: good rows are inserted, rejects are returned
    in the result, nothing else changes.
    """
    path = Path(path)
    known_instruments = {r[0] for r in conn.execute("SELECT instrument_id FROM instruments")}
    existing_keys = {
        tuple(r) for r in conn.execute(
            "SELECT as_of_date, instrument_id, settle_date, mark_type, source FROM marks"
        )
    }

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        good, rejects = _validate_rows(reader, known_instruments, existing_keys)

    result = LoadResult(n_read=len(good) + len(rejects), n_loaded=0, rows=good, rejects=rejects)

    if strict and rejects:
        head = [f"row {r.row_no}: {r.detail}" for r in rejects[:10]]
        raise ValueError(
            f"{path.name}: {len(rejects)} reject(s); nothing loaded (strict=True). First: "
            + " | ".join(head)
        )

    with conn:
        conn.executemany(
            "INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [r.as_tuple() for r in good]
        )
    result.n_loaded = len(good)
    return result


# --------------------------------------------------------------------- export_request
# Request CSV columns for pull_marks.py (the Bloomberg-machine script).
REQUEST_COLUMNS = ["instrument_id", "bbg_ticker", "settle_date", "mark_type"]

_OPEN_FX_LEGS_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, l.settle_date
FROM trade_legs l
JOIN trades t ON t.trade_id = l.trade_id
JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE i.asset_class = 'FX' AND l.settle_date > :as_of
ORDER BY i.instrument_id, l.settle_date
"""

_FX_INSTRUMENTS_WITH_OPEN_LEGS_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker
FROM trade_legs l
JOIN trades t ON t.trade_id = l.trade_id
JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE i.asset_class = 'FX' AND l.settle_date > :as_of
ORDER BY i.instrument_id
"""

_FUTURE_INSTRUMENTS_SQL = """
SELECT DISTINCT i.instrument_id, i.bbg_ticker, i.expiry_date
FROM instruments i
WHERE i.asset_class = 'FUTURE'
  AND i.expiry_date > :as_of
  AND EXISTS (
      SELECT 1 FROM trade_legs l JOIN trades t ON t.trade_id = l.trade_id
      WHERE t.instrument_id = i.instrument_id AND l.settle_date > :as_of
  )
ORDER BY i.instrument_id
"""


def export_request(conn: sqlite3.Connection, as_of_date: str, path: Union[str, Path]) -> int:
    """Write a Bloomberg request CSV (REQUEST_COLUMNS) to ``path``.

    Rows:
    - SPOT for every FX instrument (asset_class = 'FX') that has at least one open leg
      (trade_legs.settle_date > as_of_date via trades), settle_date = as_of_date.
    - FWD_OUTRIGHT for each open FX leg (trade_legs.settle_date > as_of_date), at that
      leg's OWN settle_date -- CLAUDE.md "P&L conventions -> Mark date": each leg is
      marked at its own value date, never one shared WORKDAY(as_of,5) date.
    - FUTURE_PX for every FUTURE instrument that is not yet expired (expiry_date > as_of)
      and has an open trade_legs row (settle_date > as_of). An expired future, or one
      with no open leg, is never requested.

    Returns the number of rows written. Reads from instruments and trade_legs/trades
    (no marks): this is a pure "what do we need marks for" export, independent of what
    has already been pulled.
    """
    path = Path(path)
    rows: List[dict] = []

    fx = conn.execute(_FX_INSTRUMENTS_WITH_OPEN_LEGS_SQL, {"as_of": as_of_date}).fetchall()
    for instrument_id, bbg_ticker in fx:
        rows.append({
            "instrument_id": instrument_id, "bbg_ticker": bbg_ticker,
            "settle_date": as_of_date, "mark_type": "SPOT",
        })

    fwd = conn.execute(_OPEN_FX_LEGS_SQL, {"as_of": as_of_date}).fetchall()
    for instrument_id, bbg_ticker, settle_date in fwd:
        rows.append({
            "instrument_id": instrument_id, "bbg_ticker": bbg_ticker,
            "settle_date": settle_date, "mark_type": "FWD_OUTRIGHT",
        })

    fut = conn.execute(_FUTURE_INSTRUMENTS_SQL, {"as_of": as_of_date}).fetchall()
    for instrument_id, bbg_ticker, expiry_date in fut:
        rows.append({
            "instrument_id": instrument_id, "bbg_ticker": bbg_ticker,
            "settle_date": expiry_date, "mark_type": "FUTURE_PX",
        })

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REQUEST_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)
