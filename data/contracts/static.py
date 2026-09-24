"""Bloomberg's own contract dates (``FUT_LAST_TRADE_DT``, ``FUT_NOTICE_FIRST``), kept in this
lane's own table, created defensively the way ``engine/rates_vol/`` keeps its tables.

This module never asks Bloomberg for anything: bbg-live fetches the dates on request and
stores them through ``store_static_dates``. Rows are keyed by the canonical contract id
(``'CLZ26 Comdty'``); a ticker in Bloomberg's one-digit form is stored under its canonical id,
its year read from the last trade date it comes with.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

from data.contracts.tickers import canonical_from_ticker, make_contract_id, parse_bbg_ticker, to_date

TABLE = "contract_static"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  contract_id        TEXT PRIMARY KEY,   -- canonical: 'CLZ26 Comdty', 'C Z26 Comdty'
  last_trade_date    TEXT NOT NULL,      -- ISO date, Bloomberg's FUT_LAST_TRADE_DT
  first_notice_date  TEXT NOT NULL DEFAULT '',  -- ISO date, FUT_NOTICE_FIRST; '' = none / not given
  source             TEXT NOT NULL,      -- who supplied the dates: 'BBG_BDP', ...
  fetched_at         TEXT NOT NULL       -- ISO timestamp (UTC) of the store
)
"""


def ensure_static_table(conn: sqlite3.Connection) -> None:
    """Create ``contract_static`` if it is not there yet."""
    conn.execute(_DDL)


def _has_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone()
    return row is not None


def store_static_dates(conn: sqlite3.Connection, rows: Iterable[Mapping]) -> int:
    """Upsert ``[{contract_id, last_trade_date, first_notice_date, source}]``; returns the count.

    Dates may be ``date`` objects or ISO text; a blank or missing ``first_notice_date`` is
    stored as ''. A row without a readable last trade date or a source raises ValueError before
    anything is written; the rest are committed together (``with conn``, as engine/rates_vol does).
    """
    ensure_static_table(conn)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    prepared = []
    for row in rows:
        ltd = to_date(row["last_trade_date"])
        fnd_raw = row.get("first_notice_date") or ""
        fnd = to_date(fnd_raw).isoformat() if str(fnd_raw).strip() else ""
        source = str(row.get("source") or "").strip()
        if not source:
            raise ValueError(f"contract {row.get('contract_id')!r}: a source is required")
        contract_id = canonical_from_ticker(str(row["contract_id"]), ltd)
        prepared.append((contract_id, ltd.isoformat(), fnd, source, str(row.get("fetched_at") or now)))
    with conn:
        conn.executemany(
            f"INSERT INTO {TABLE} (contract_id, last_trade_date, first_notice_date, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (contract_id) DO UPDATE SET "
            "last_trade_date = excluded.last_trade_date, first_notice_date = excluded.first_notice_date, "
            "source = excluded.source, fetched_at = excluded.fetched_at",
            prepared,
        )
    return len(prepared)


def static_dates(conn: sqlite3.Connection, contract_id: str) -> Optional[dict]:
    """The stored row for a canonical contract id, or None (also when the table does not exist)."""
    if conn is None or not _has_table(conn):
        return None
    key = " ".join(str(contract_id).split())
    parts = parse_bbg_ticker(key)
    if parts is not None and len(parts[2]) == 2:
        # the same contract however it is spelt: 'CZ26 comdty' -> 'C Z26 Comdty'
        key = make_contract_id(parts[0], parts[1], int(parts[2]), parts[3])
    cur = conn.execute(
        f"SELECT contract_id, last_trade_date, first_notice_date, source, fetched_at FROM {TABLE} "
        "WHERE contract_id = ?", (key,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(("contract_id", "last_trade_date", "first_notice_date", "source", "fetched_at"), row))
