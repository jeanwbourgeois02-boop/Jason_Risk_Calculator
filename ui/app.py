"""Dash application skeleton for the risk monitor.

Owns: ui/. Reads (read-only) from the SQLite database produced by data/ingest and
data/bloomberg; never recomputes P&L or delta -- that lives in engine/.

Three tabs per docs/BUILD_PLAN.md section 5 ("Tabs (layer 3)"), Reconciliation
removed 2026-09-16 (the old Excel workbook it existed to cross-check is no longer
in use): Ladder, Blotter, Market data. A header (ui/tabs/header.py) sits above the
tabs on every view, showing LTD / Daily / 5d / MTD / YTD / trading from
engine.pnl.ledger.

The "Overall book" tab and the six-tab CLAUDE.md layout are retired by
docs/BUILD_PLAN.md (2026-09-15): that plan supersedes CLAUDE.md's "Six tabs as
views" table as the app's headline structure.

Options placement, decided 2026-09-17 (this note and CLAUDE.md's "Six tabs as
views" section now agree, resolving the earlier conflict between the two):
Options is not a fourth top-level tab here either. It lands inside the Blotter
tab as a grouped, collapsible trade summary once engine/options/ (vendored
options_calc, see that package's scope ledger) has real PREMIUM/DELTA marks to
show -- see docs/open-questions.md item 61.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Union

import dash
from dash import Input, Output, dcc, html

from ui.tabs import blotter, cash_ladder, header, market_data
from ui import revision, uploads

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "raw" / "risk.db"

VISIBLE_TABS = ["Ladder", "Blotter", "Market data"]


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
    when the file is absent (no trades, no marks: upload a trade file to fill it);
    on an existing database applies the idempotent DDL so additive tables exist.
    Never alters existing tables or rows, except for `data.ingest.schema.
    purge_retired_sources` (2026-09-17, "no bnp fall back" -- docs/bnp-excel-removal.md),
    which is itself idempotent and deletes only what the retired BNP parser/workbook
    wrote (see that function's docstring) -- run once per startup, counts printed only
    when non-zero. `pnl_snapshots` is retired (docs/BUILD_PLAN.md section 3): the
    schema no longer creates it, and this function does not check for it. Writes an
    initial status file ("no pull has run yet") only when none exists."""
    p = Path(path)
    try:
        from data.ingest import schema
        created = not p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
        try:
            schema.create_schema(conn)
            purged = schema.purge_retired_sources(conn)
            if any(purged.values()):
                print(f"purged retired BNP/workbook data from {p}: {purged}", flush=True)
        finally:
            conn.close()
        if created:
            print(f"created empty database {p} (upload a trade file to fill it)", flush=True)
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

    as_of_date = max(trades.trade_date) -- the last loaded blotter trade date -- or
    'none' if trades is empty. 2026-09-17 ("no bnp fall back"): this used to be
    max(positions.as_of_date), the retired BNP snapshot's own date; the `positions`
    table is gone, and the blotter (trades.trade_date) is the app's only trade source
    now, so it is the natural replacement -- "the last uploaded snapshot date" that
    ui/app.py::build_layout's docstring already describes this value as. No P&L /
    ladder logic here -- just counts.
    """
    def count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    trades_count = count("trades")
    if trades_count:
        as_of_date = conn.execute("SELECT MAX(trade_date) FROM trades").fetchone()[0]
    else:
        as_of_date = "none"

    return {
        "as_of_date": as_of_date,
        "trades": trades_count,
        "trade_legs": count("trade_legs"),
        "marks": count("marks"),
    }


def empty_summary(message: str = "database not found") -> dict:
    """Placeholder summary used when the DB file is missing."""
    return {
        "as_of_date": "none",
        "trades": 0,
        "trade_legs": 0,
        "marks": 0,
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


MAIN_TABS_ID = "main-tabs"


def _slug(label: str) -> str:
    return label.lower().replace(" ", "-")


def build_layout(data: dict, db_path=None) -> html.Div:
    """Top-level layout (user decision 2026-09-15, item A, revised 2026-09-15): the tab
    bar (`dcc.Tabs`, holding plain `dcc.Tab(label=..., value=...)` objects with NO
    children of their own -- Dash nests a Tab's children inside its own styled wrapper,
    which was pushing the navy `.top-bar` around the whole page) sits at the very top
    with the upload control pinned to its right; the P&L header sits directly under the
    tab bar, on every tab. Below that, all three tab bodies live in one always-present
    `html.Div(id="tab-bodies")`, each wrapped in its own `html.Div(id=f"tab-body-{slug}")`
    -- bodies never leave the layout, so every tab's own callbacks (registered against
    ids inside their body) keep firing regardless of which tab is selected. One
    show/hide callback (registered in `create_app`) toggles the three bodies' `style` on
    `main-tabs`' `value`.

    Each tab module owns its own controls/table via `build_layout(default_date)`; this
    module only assembles them and wires the as-of date picker (owned by the Ladder
    tab) into `header.AS_OF_STORE_ID` so the header reflects whichever date the user
    has picked.

    Coordinator addition, 2026-09-15: the Ladder tab's date picker (and the header,
    which mirrors it) default to TODAY in America/New_York, not the last BNP snapshot
    date -- the ladder is a "what's open today" view, not a snapshot replay, and
    `engine.ladder.exposure_adapter.records_from_db` already selects trades open on
    whatever as_of it is given (trade_date <= as_of <= settle_date). The Blotter keeps
    defaulting to the last uploaded snapshot date, since it renders the loaded trade file
    itself. Market data opens on today too (2026-09-21): its whole-book panels ("What is
    missing", "Marks that look wrong") ask whether TODAY's marks can be trusted, and the
    live pull only writes today's -- the last trade date is often a past day."""
    snapshot_date = data["as_of_date"] if data["as_of_date"] != "none" else None
    ladder_default_date = cash_ladder.today_ny()
    tab_builders = {
        "Ladder": cash_ladder.build_layout,
        "Blotter": blotter.build_layout,
        "Market data": market_data.build_layout,
    }
    tab_defaults = {
        "Ladder": ladder_default_date,
        "Blotter": snapshot_date,
        "Market data": ladder_default_date,
    }
    tabs = [dcc.Tab(label=label, value=label, className="tab", selected_className="tab--selected")
            for label in VISIBLE_TABS]
    bodies = [
        html.Div(tab_builders[label](default_date=tab_defaults[label]),
                 id=f"tab-body-{_slug(label)}", className="tab-body")
        for label in VISIBLE_TABS
    ]
    return html.Div([
        html.Div(className="top-bar", children=[
            dcc.Tabs(id=MAIN_TABS_ID, value=VISIBLE_TABS[0], children=tabs,
                     parent_className="tabs-bar", className="tabs-strip"),
            uploads.layout(data),
        ]),
        header.layout(),
        html.Div(id="tab-bodies", children=bodies),
        dcc.Store(id=header.AS_OF_STORE_ID, data=ladder_default_date),
        # "The data changed" signal (ui/revision.py): every tab listens, so an upload or a
        # Bloomberg pull shows up without a browser reload.
        *revision.components(db_path if db_path is not None else get_db_path()),
    ])


def create_app(db_path: Union[str, Path, None] = None, start_feed: bool = False) -> dash.Dash:
    """Build the Dash app. db_path defaults to RISK_DB / data/raw/risk.db.
    start_feed=True (the launcher) starts the Bloomberg live feed thread when available."""
    resolved = Path(db_path) if db_path is not None else get_db_path()
    ensure_schema(resolved)
    data = load_summary(resolved)
    # suppress_callback_exceptions: the Blotter sub-tabs render their tables, filter
    # dropdowns and row-detail panels dynamically inside the `_update` callback's own
    # Output (blotter.CONTENT_ID children), not in the static app.layout tree -- Dash's
    # default id validation rejects callbacks whose Input/Output/State ids aren't present
    # in the initial layout, which silently no-ops every filter dropdown, the row-click
    # detail panel and the P&L strip on every Blotter sub-tab (found 2026-09-16: the
    # filter callback logic itself was correct when called directly in Python, but never
    # fired in the browser because of this). See CLAUDE.md ownership: this is app-wide
    # config, not a per-tab fix.
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    app.layout = build_layout(data, db_path=resolved)

    header.register_callbacks(app, get_db_path=lambda: resolved)
    cash_ladder.register_callbacks(app, get_db_path=lambda: resolved)
    blotter.register_callbacks(app, get_db_path=lambda: resolved)
    market_data.register_callbacks(app, get_db_path=lambda: resolved)
    uploads.register(app, get_db_path=lambda: resolved)
    revision.register(app, get_db_path=lambda: resolved)

    # Mirror the Ladder tab's date picker into the header's as-of store so the header
    # figures track whichever date the user has selected there. The Ladder tab is the
    # only date-picker on any tab that changes the book-wide as-of (Market data's own
    # date picker only scopes that tab's inventory/completeness view).
    app.callback(Output(header.AS_OF_STORE_ID, "data"),
                 Input(cash_ladder.DATE_PICKER_ID, "date"))(lambda date_value: date_value)

    # Show/hide the always-present tab bodies (see build_layout docstring) on the
    # dcc.Tabs' own `value`, rather than nesting bodies inside dcc.Tab.children.
    body_outputs = [Output(f"tab-body-{_slug(label)}", "style") for label in VISIBLE_TABS]
    app.callback(*body_outputs, Input(MAIN_TABS_ID, "value"))(
        lambda selected: [{} if label == selected else {"display": "none"} for label in VISIBLE_TABS]
    )

    # `bloomberg_feed_reason` is why there is no feed ("" when there is one), kept so the
    # top bar's "Pull Bloomberg now" button (ui/feed_controls.py) can say it in plain
    # words instead of doing nothing on a machine without Bloomberg.
    if start_feed:
        app.bloomberg_feed, app.bloomberg_feed_reason = start_bloomberg_feed_with_reason(resolved)
    else:
        app.bloomberg_feed, app.bloomberg_feed_reason = None, FEED_NOT_REQUESTED
    return app


FEED_NOT_REQUESTED = "the live feed was not started for this session (start_feed=False)"
FEED_SWITCHED_OFF = "the live feed is switched off (RISK_LIVE=0)"


def start_bloomberg_feed_with_reason(db_path: Path):
    """`(feed, reason)`: start the Bloomberg feed when blpapi and a Bloomberg API service
    are present on this computer (data.bloomberg.live). Otherwise `feed` is None and
    `reason` says why, in the same words the status file gets; the app runs without live
    rates and no prices are invented. RISK_LIVE=0 disables."""
    if os.environ.get("RISK_LIVE", "1") == "0":
        return None, FEED_SWITCHED_OFF
    from data.bloomberg.live import start_feed_if_available
    from ui.feed_controls import cadence_words, feed_interval_seconds
    host = os.environ.get("BLP_HOST", "localhost")
    port = int(os.environ.get("BLP_PORT", "8194"))
    feed, why = start_feed_if_available(db_path, host=host, port=port)
    seconds = feed_interval_seconds(feed) if feed else None
    started = f"started ({cadence_words(seconds)})" if seconds else "started"
    print(f"Bloomberg feed: {started if feed else 'not started: ' + why}", flush=True)
    from data.bloomberg.backfill import start_auto_backfill
    start_auto_backfill(db_path, host=host, port=port)
    return feed, ("" if feed else why)


def start_bloomberg_feed(db_path: Path):
    """The feed alone (None when unavailable); see `start_bloomberg_feed_with_reason`."""
    return start_bloomberg_feed_with_reason(db_path)[0]


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
