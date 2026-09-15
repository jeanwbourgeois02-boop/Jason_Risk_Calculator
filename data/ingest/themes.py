"""Manual theme tagging (BUILD_PLAN task B point 4), used by the UI's inline theme edit.

A theme can be set on one trade (overrides that row only) or on an instrument (used as
the default new trades in that pair inherit at load time -- see
data/ingest/bnp.py::load and data/ingest/xlsx_futures.py::load_futures_fills).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import List

from data.ingest import schema


def set_theme(conn: sqlite3.Connection, key: str, theme: str, *, is_instrument: bool = False) -> None:
    """Set ``theme`` on a single trade (``key`` = trade_id) or on an instrument's default
    (``key`` = instrument_id, ``is_instrument=True``). Raises ValueError if the key does
    not exist. Existing trades already loaded for an instrument are NOT retroactively
    changed by an instrument-level theme -- only trades loaded afterwards inherit it."""
    schema.create_schema(conn)
    if is_instrument:
        if not conn.execute("SELECT 1 FROM instruments WHERE instrument_id = ?", (key,)).fetchone():
            raise ValueError(f"unknown instrument_id {key!r}")
        with conn:
            conn.execute(
                "INSERT INTO instrument_theme (instrument_id, theme) VALUES (?, ?) "
                "ON CONFLICT(instrument_id) DO UPDATE SET theme = excluded.theme",
                (key, theme))
    else:
        if not conn.execute("SELECT 1 FROM trades WHERE trade_id = ?", (key,)).fetchone():
            raise ValueError(f"unknown trade_id {key!r}")
        with conn:
            conn.execute("UPDATE trades SET theme = ? WHERE trade_id = ?", (theme, key))


# --------------------------------------------------------------------------- bundles
# A bundle is a named view over the same theme mechanism above: `bundles` (schema.py)
# holds only name/description/created_at; membership IS `instrument_theme.theme`
# (and, transitively, `trades.theme` for future trades) equal to the bundle's name.
# Blotter's "Bundles" sub-tab (docs/BUILD_PLAN.md task C, user decision 2026-09-15) is
# the only caller.

def create_bundle(conn: sqlite3.Connection, name: str, description: str = "") -> None:
    """Create (or update the description of) a named bundle. Idempotent on name."""
    schema.create_schema(conn)
    name = name.strip()
    if not name:
        raise ValueError("bundle name must not be empty")
    with conn:
        conn.execute(
            "INSERT INTO bundles (name, description, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET description = excluded.description",
            (name, description, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
        )


def list_bundles(conn: sqlite3.Connection) -> List[dict]:
    """All bundles with their current member pairs (instrument_ids whose
    `instrument_theme.theme` equals the bundle name), ordered by name."""
    schema.create_schema(conn)
    rows = conn.execute("SELECT name, description, created_at FROM bundles ORDER BY name").fetchall()
    return [
        {"name": name, "description": description, "created_at": created_at,
         "pairs": bundle_pairs(conn, name)}
        for name, description, created_at in rows
    ]


def bundle_pairs(conn: sqlite3.Connection, name: str) -> List[str]:
    """instrument_ids currently assigned to bundle `name` via `instrument_theme`."""
    rows = conn.execute(
        "SELECT instrument_id FROM instrument_theme WHERE theme = ? ORDER BY instrument_id", (name,)
    ).fetchall()
    return [r[0] for r in rows]


def add_pair_to_bundle(conn: sqlite3.Connection, name: str, instrument_id: str) -> None:
    """Assign `instrument_id` to bundle `name` (future trades in that pair inherit the
    theme too, per `set_theme`'s instrument-level note). Raises ValueError if the
    bundle or the instrument does not exist."""
    if not conn.execute("SELECT 1 FROM bundles WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"unknown bundle {name!r}")
    set_theme(conn, instrument_id, name, is_instrument=True)


def remove_pair_from_bundle(conn: sqlite3.Connection, name: str, instrument_id: str) -> None:
    """Unassign `instrument_id` from bundle `name` by clearing its instrument theme.
    No-op (not an error) if it was not assigned to this bundle."""
    row = conn.execute(
        "SELECT theme FROM instrument_theme WHERE instrument_id = ?", (instrument_id,)
    ).fetchone()
    if row is not None and row[0] == name:
        set_theme(conn, instrument_id, "", is_instrument=True)
