"""``spread_overrides``: the user's hand-made corrections to the grouping rule, this lane's own
table, created defensively (``CREATE TABLE IF NOT EXISTS``, never in ``data/ingest/schema.py``).

- ``PIN``: the trade belongs to the hand-made spread ``group_name``; every trade pinned to the
  same name forms one spread (kind ``pinned``), whatever the automatic rule would say.
- ``SPLIT``: the trade is never grouped by the automatic rule; it stays an outright.

A bundle (``trades.theme`` / ``instrument_theme``) still comes first: a trade in a bundle is in
the bundle's spread whatever its override says. There is no foreign key (an upload rewrites the
whole book, as for ``bbg_library``): an override naming a trade no longer on file is ignored.

Only the table and its read exist so far (2026-09-24); nothing in the app writes a row yet (the
Spreads screen's pin / split control is for later).
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List

TABLE = "spread_overrides"
PIN = "PIN"
SPLIT = "SPLIT"
ACTIONS = (PIN, SPLIT)

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  trade_id    TEXT PRIMARY KEY,          -- no foreign key: an upload rewrites the book
  action      TEXT NOT NULL,             -- PIN | SPLIT
  group_name  TEXT NOT NULL DEFAULT '',  -- PIN: the hand-made spread's name; '' for SPLIT
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL DEFAULT ''
)
"""


def _exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone()
    return row is not None


def ensure_overrides_table(conn: sqlite3.Connection) -> bool:
    """Create the table when it is missing. False (nothing raised) on a read-only connection
    that does not have it: the book then simply has no overrides."""
    if _exists(conn):
        return True
    try:
        with conn:
            conn.execute(DDL)
    except sqlite3.OperationalError:
        return False
    return True


def read_overrides(conn: sqlite3.Connection) -> Dict[str, dict]:
    """{trade_id: {action, group_name, note, created_at}} for every row with a known action;
    {} when the table is not there. A row with another action, or a PIN with no group name, is
    left out here and named by ``override_problems``."""
    return {r["trade_id"]: r for r in _rows(conn) if _usable(r)}


def override_problems(conn: sqlite3.Connection) -> List[str]:
    """One sentence per stored override that cannot be applied."""
    out = []
    for r in _rows(conn):
        if not _usable(r):
            out.append(f"spread override for trade {r['trade_id']} ignored: action {r['action']!r}"
                       + (" with no group name" if r["action"] == PIN else f" is not one of {', '.join(ACTIONS)}"))
    return out


def _usable(r: dict) -> bool:
    return r["action"] in ACTIONS and (r["action"] != PIN or bool(r["group_name"]))


def _rows(conn: sqlite3.Connection) -> List[dict]:
    if not _exists(conn):
        return []
    rows = conn.execute(f"SELECT trade_id, action, group_name, note, created_at FROM {TABLE} ORDER BY trade_id")
    return [{"trade_id": str(t), "action": str(a or "").strip().upper(), "group_name": str(g or "").strip(),
             "note": str(n or ""), "created_at": str(c or "")} for t, a, g, n, c in rows]
