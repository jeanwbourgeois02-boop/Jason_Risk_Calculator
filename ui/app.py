"""Dash application skeleton for the risk monitor.

Owns: ui/. Reads (read-only) from the SQLite database produced by data/ingest and
data/bloomberg; never recomputes P&L or delta -- that lives in engine/.

Six tabs mirror CLAUDE.md "Data contract -> Six tabs as views":
Cash ladder, FX, Rates, Options, Delta, Overall book.

For now each tab renders a placeholder built from `summary()`. As engine/ modules
land, each tab should move to its own ui/<tab>.py module that queries the
documented tables/views for that tab.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Union

import dash
from dash import dcc, html

from ui.tabs import cash_ladder, pnl
from ui import uploads, workbook_rates

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "raw" / "risk.db"

TAB_LABELS = ("Cash ladder", "FX", "Rates", "Options", "Delta", "Overall book")


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
    on an existing database applies the idempotent DDL so additive tables (e.g. the
    P&L ledger) exist. Never alters existing tables or rows. Writes an initial status
    file ("no pull has run yet") only when none exists."""
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
    """Row counts and as_of_date for the placeholder tabs.

    as_of_date = max(positions.as_of_date), or 'none' if positions is empty.
    No P&L / ladder logic here -- just counts, so it is safe before engine/ exists.
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


def build_tab_placeholder(label: str, data: dict) -> html.Div:
    """Placeholder content for a single tab: as_of_date and the four row counts."""
    message = data.get("message")
    children = [
        html.H3(label),
        html.P(f"as_of_date: {data['as_of_date']}"),
        html.Ul([
            html.Li(f"trades: {data['trades']}"),
            html.Li(f"trade_legs: {data['trade_legs']}"),
            html.Li(f"marks: {data['marks']}"),
            html.Li(f"positions: {data['positions']}"),
        ]),
    ]
    if message:
        children.append(html.P(message, style={"color": "gray"}))
    return html.Div(children)


def build_layout(data: dict) -> html.Div:
    """Top-level layout: a dcc.Tabs bar with the six tabs. Cash ladder and Overall book
    render their real controls + tables (ui/tabs/cash_ladder.py, ui/tabs/pnl.py); the
    other four stay placeholders until their engine/ views land."""
    default_date = data["as_of_date"] if data["as_of_date"] != "none" else None
    tabs = []
    for label in TAB_LABELS:
        if label == "Cash ladder":
            children = [cash_ladder.build_layout(default_date=default_date)]
        elif label == "Overall book":
            children = [pnl.build_layout(default_date=default_date)]
        else:
            children = [build_tab_placeholder(label, data)]
        tabs.append(dcc.Tab(label=label, children=children,
                            className="tab", selected_className="tab--selected"))
    return html.Div([
        html.H1("Risk monitor"),
        uploads.layout(data),
        dcc.Tabs(children=tabs, parent_className="tabs-bar", className="tabs-strip"),
        workbook_rates.layout(default_date),
    ])


def create_app(db_path: Union[str, Path, None] = None, start_feed: bool = False) -> dash.Dash:
    """Build the Dash app. db_path defaults to RISK_DB / data/raw/risk.db.
    start_feed=True (the launcher) starts the Bloomberg live feed thread when available."""
    resolved = Path(db_path) if db_path is not None else get_db_path()
    ensure_schema(resolved)
    data = load_summary(resolved)
    app = dash.Dash(__name__)
    app.layout = build_layout(data)
    cash_ladder.register_callbacks(app, get_db_path=lambda: resolved)
    pnl.register_callbacks(app, get_db_path=lambda: resolved)
    uploads.register(app, get_db_path=lambda: resolved)
    workbook_rates.register(app, get_db_path=lambda: resolved)
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
