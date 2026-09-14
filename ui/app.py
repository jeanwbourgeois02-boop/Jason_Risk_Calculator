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
    """Top-level layout: a dcc.Tabs bar with the six tabs, each a placeholder."""
    tabs = [
        dcc.Tab(label=label, children=[build_tab_placeholder(label, data)])
        for label in TAB_LABELS
    ]
    return html.Div([
        html.H1("Risk monitor"),
        dcc.Tabs(children=tabs),
    ])


def create_app(db_path: Union[str, Path, None] = None) -> dash.Dash:
    """Build the Dash app. db_path defaults to RISK_DB / data/raw/risk.db."""
    resolved = Path(db_path) if db_path is not None else get_db_path()
    data = load_summary(resolved)
    app = dash.Dash(__name__)
    app.layout = build_layout(data)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
