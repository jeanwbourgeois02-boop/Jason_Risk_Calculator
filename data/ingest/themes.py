"""Manual theme tagging (BUILD_PLAN task B point 4), used by the UI's inline theme edit.

A theme can be set on one trade (overrides that row only) or on an instrument (used as
the default new trades in that pair inherit at load time -- see
data/ingest/bnp.py::load and data/ingest/xlsx_futures.py::load_futures_fills).
"""
from __future__ import annotations

import sqlite3

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
