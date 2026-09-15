"""Dash application skeleton for the risk monitor.

Owns: ui/. Reads (read-only) from the SQLite database produced by data/ingest and
data/bloomberg; never recomputes P&L or delta -- that lives in engine/.

Four tabs per docs/BUILD_PLAN.md section 5 ("Tabs (layer 3)"): Ladder, Blotter,
Market data, Reconciliation. A header (ui/tabs/header.py) sits above the tabs on
every view, showing LTD / Daily / 5d / MTD / YTD / trading from engine.pnl.ledger.

The "Overall book" tab and the six-tab CLAUDE.md layout are retired by
docs/BUILD_PLAN.md (2026-09-15): that plan supersedes CLAUDE.md's "Six tabs as
views" table as the app's headline structure. The workbook reconciliation view
(formerly the Overall book / P&L tab) now lives inside the Reconciliation tab.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Union

import dash
from dash import Input, Output, dcc, html

from ui.tabs import blotter, cash_ladder, header, market_data, reconciliation
from ui import uploads

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "raw" / "risk.db"

VISIBLE_TABS = ["Ladder", "Blotter", "Market data", "Reconciliation"]


def get_db_path() -> Path:
    """Resolve the database path from RISK_DB, defaulting to data/raw/risk.db."""
    raw = os.environ.get("RISK_DB")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (REPO_ROOT / p)
    return DEFAULT_DB_PATH


def ensure_schema(path: Union[str, Path]) -> None:
    """Make sure the database and the Bloomberg status file exist so a fresh computer
    can launch with nothing copied across. Creates an EMPTY database with the schema
    when the file is absent (no trades, no marks: upload a BNP report to fill it);
    on an existing database applies the idempotent DDL so additive tables exist.
    Never alters existing tables or rows. `pnl_snapshots` is retired (docs/BUILD_PLAN.md
    section 3): the schema no longer creates it, and this function does not check for
    it. Writes an initial status file ("no pull has run yet") only when none exists."""
    p = Path(path)
    try:
        from data.ingest import schema
        created = not p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
        try:
            schema.create_schema(conn)
        finally:
            conn.close()
        if created:
            print(f"created empty database {p} (upload a BNP report to fill it)", flush=True)
    except (sqlite3.Error, OSError) as exc:
        print(f"schema check skipped ({exc})", flush=True)
    try:
        from data.bloomberg.live import read_status, write_status, _now_iso
        if read_status(p) is None:
            write_status(p, {"time": _now_iso(), "connected": False, "reason": "no pull has run yet",
                             "requested": 0, "written": 0, "failed": 0, "items": [], "warnings": []})
    except OSError as exc:
        print(f"status file not written ({exc})", flush=True)


def connect_readonly(path: Union[str, Path]) -> sqlite3.Connection:
    """Open the database read-only. Missing file -> caller handles via summary()."""
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def summary(conn: sqlite3.Connection) -> dict:
    """Row counts and as_of_date for the upload strip.

    as_of_date = max(positions.as_of_date), or 'none' if positions is empty.
    No P&L / ladder logic here -- just counts.
    """
    def count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    positions_count = count("positions")
    if positions_count:
        as_of_date = conn.execute("SELECT MAX(as_of_date) FROM positions").fetchone()[0]
    else:
        as_of_date = "none"

    return {
        "as_of_date": as_of_date,
        "trades": count("trades"),
        "trade_legs": count("trade_legs"),
        "marks": count("marks"),
        "positions": positions_count,
    }


def empty_summary(message: str = "database not found") -> dict:
    """Placeholder summary used when the DB file is missing."""
    return {
        "as_of_date": "none",
        "trades": 0,
        "trade_legs": 0,
        "marks": 0,
        "positions": 0,
        "message": message,
    }


def load_summary(db_path: Union[str, Path]) -> dict:
    """Read the summary from db_path, handling a missing file gracefully."""
    db_path = Path(db_path)
    if not db_path.exists():
        return empty_summary(f"database not found: {db_path}")
    conn = connect_readonly(db_path)
    try:
        return summary(conn)
    finally:
        conn.close()


def build_layout(data: dict) -> html.Div:
    """Top-level layout: the header block, then a dcc.Tabs bar with the four tabs
    (docs/BUILD_PLAN.md section 5). Each tab module owns its own controls/table via
    `build_layout(default_date)`; this module only assembles them and wires the as-of
    date picker (owned by the Ladder tab) into `header.AS_OF_STORE_ID` so the header
    reflects whichever date the user has picked."""
    default_date = data["as_of_date"] if data["as_of_date"] != "none" else None
    tab_builders = {
        "Ladder": cash_ladder.build_layout,
        "Blotter": blotter.build_layout,
        "Market data": market_data.build_layout,
        "Reconciliation": reconciliation.build_layout,
    }
    tabs = []
    for label in VISIBLE_TABS:
        children = [tab_builders[label](default_date=default_date)]
        tabs.append(dcc.Tab(label=label, children=children,
                            className="tab", selected_className="tab--selected"))
    return html.Div([
        html.H1("Risk monitor"),
        uploads.layout(data),
        header.layout(),
        dcc.Store(id=header.AS_OF_STORE_ID, data=default_date),
        dcc.Tabs(children=tabs, parent_className="tabs-bar", className="tabs-strip"),
    ])


def create_app(db_path: Union[str, Path, None] = None, start_feed: bool = False) -> dash.Dash:
    """Build the Dash app. db_path defaults to RISK_DB / data/raw/risk.db.
    start_feed=True (the launcher) starts the Bloomberg live feed thread when available."""
    resolved = Path(db_path) if db_path is not None else get_db_path()
    ensure_schema(resolved)
    data = load_summary(resolved)
    app = dash.Dash(__name__)
    app.layout = build_layout(data)

    header.register_callbacks(app, get_db_path=lambda: resolved)
    cash_ladder.register_callbacks(app, get_db_path=lambda: resolved)
    blotter.register_callbacks(app, get_db_path=lambda: resolved)
    market_data.register_callbacks(app, get_db_path=lambda: resolved)
    reconciliation.register_callbacks(app, get_db_path=lambda: resolved)
    uploads.register(app, get_db_path=lambda: resolved)

    # Mirror the Ladder tab's date picker into the header's as-of store so the header
    # figures track whichever date the user has selected there. The Ladder tab is the
    # only date-picker on any tab that changes the book-wide as-of (Market data's own
    # date picker only scopes that tab's inventory/completeness view).
    app.callback(Output(header.AS_OF_STORE_ID, "data"),
                 Input(cash_ladder.DATE_PICKER_ID, "date"))(lambda date_value: date_value)

    app.bloomberg_feed = start_bloomberg_feed(resolved) if start_feed else None
    return app


def start_bloomberg_feed(db_path: Path):
    """Start the 2-minute Bloomberg feed when blpapi and a Bloomberg API service are
    present on this computer (data.bloomberg.live). Otherwise record why in the status
    file and run without live rates; no prices are invented. RISK_LIVE=0 disables."""
    if os.environ.get("RISK_LIVE", "1") == "0":
        return None
    from data.bloomberg.live import start_feed_if_available
    host = os.environ.get("BLP_HOST", "localhost")
    port = int(os.environ.get("BLP_PORT", "8194"))
    feed, why = start_feed_if_available(db_path, host=host, port=port)
    print(f"Bloomberg feed: {'started (every 2 min)' if feed else 'not started: ' + why}", flush=True)
    return feed


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
