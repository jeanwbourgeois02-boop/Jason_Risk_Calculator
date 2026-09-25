"""Dash application skeleton for the risk monitor.

Owns: ui/. Reads (read-only) from the SQLite database produced by data/ingest and
data/bloomberg; never recomputes P&L or delta -- that lives in engine/.

Eight tabs (CLAUDE.md "Screens redesign plan", user 2026-09-25), in this order: Book,
Spreads, Curve, Risk, Expiries, Blotter, FX & cash, Data. "FX & cash" is the former Ladder
(ui/tabs/cash_ladder.py) and "Data" the former Market data (ui/tabs/market_data.py); each
tab has a stable key (`TAB_KEYS`) that names its body's DOM id and the tab bar's value,
separate from the label the user reads, so a rename never moves an id. The app opens on the
first tab, Book (ui/tabs/book.py, Phase B): the book's home. A header (ui/tabs/header.py) sits
above the tabs on every view, showing LTD / Daily / 5d / MTD / YTD / trading from
engine.pnl.ledger.

Options are not a top-level tab: they live inside the Blotter tab as a grouped,
collapsible trade summary (ui/tabs/options.py; docs/open-questions.md item 61).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Union

import dash
from dash import ALL, Input, Output, State, dcc, html

from ui.tabs import blotter, book, cash_ladder, curve, expiries, header, market_data, risk, spreads
from ui.tabs.formatting import TAB_LINK_TYPE
from ui import revision, uploads

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "raw" / "risk.db"

# Order per the screens redesign (user, 2026-09-25: "the spread is the unit"), replacing the
# macro book's Blotter-first order of 2026-09-22; the app opens on the first. Each label maps
# to its stable key: the tab bar's value and the body id `tab-body-<key>`. The key never holds
# '&' or a space, and a renamed tab keeps its key ("FX & cash" is still "ladder", "Data" still
# "market-data"), so nothing keyed on a body id moves.
TAB_KEYS = {
    "Book": "book",
    "Spreads": "spreads",
    "Curve": "curve",
    "Risk": "risk",
    "Expiries": "expiries",
    "Blotter": "blotter",
    "FX & cash": "ladder",
    "Data": "market-data",
}
VISIBLE_TABS = list(TAB_KEYS)


def tab_body_id(label: str) -> str:
    """The DOM id of a tab's always-present body: `tab-body-<key>` ("FX & cash" ->
    "tab-body-ladder")."""
    return f"tab-body-{TAB_KEYS[label]}"


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


def tab_from_link_click(triggered) -> Union[str, None]:
    """The tab key a tab-link click asks for (`ui.tabs.formatting.tab_link`), or None.

    `triggered` is Dash's `ctx.triggered`: a list of {"prop_id": '<json id>.n_clicks',
    "value": n_clicks}. Only a real click counts: an entry whose `n_clicks` is None or 0 (a
    link's first render, or a body re-rendered with fresh links) is ignored, and so is any
    id that is not a tab link or names a key not in `TAB_KEYS` -- a stale or mistyped link
    does nothing, it never breaks the app. The first entry that passes wins."""
    known = set(TAB_KEYS.values())
    for entry in triggered or ():
        try:
            prop_id, value = entry.get("prop_id", ""), entry.get("value")
        except AttributeError:
            continue
        if not value or not isinstance(prop_id, str):
            continue
        raw_id = prop_id.rsplit(".", 1)[0]      # the id's own dots (an idx "a.b") stay in the JSON
        try:
            link_id = json.loads(raw_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(link_id, dict) or link_id.get("type") != TAB_LINK_TYPE:
            continue
        key = link_id.get("tab")
        if key in known:
            return key
    return None


def build_layout(data: dict, db_path=None) -> html.Div:
    """Top-level layout (user decision 2026-09-15, item A, revised 2026-09-15): the tab
    bar (`dcc.Tabs`, holding plain `dcc.Tab(label=..., value=<key>)` objects with NO
    children of their own -- Dash nests a Tab's children inside its own styled wrapper,
    which was pushing the navy `.top-bar` around the whole page) sits at the very top
    with the upload control pinned to its right; the P&L header sits directly under the
    tab bar, on every tab. Below that, every tab body lives in one always-present
    `html.Div(id="tab-bodies")`, each wrapped in its own `html.Div(id=tab_body_id(label))`
    -- bodies never leave the layout, so every tab's own callbacks (registered against
    ids inside their body) keep firing regardless of which tab is selected. One
    show/hide callback (registered in `create_app`) toggles the bodies' `style` on
    `main-tabs`' `value` (the selected tab's key, `TAB_KEYS`).

    Each tab module owns its own controls/table via `build_layout(default_date)`; this
    module only assembles them and wires the Blotter's and FX & cash's date pickers into
    `header.AS_OF_STORE_ID` so the header reflects whichever date the user has picked.

    Defaults: FX & cash (the former Ladder) opens on TODAY in America/New_York -- a
    "what's open today" view (`engine.ladder.exposure_adapter.records_from_db` selects
    trades open on whatever as_of it is given). The Blotter keeps defaulting to the last
    uploaded trade date, since it renders the loaded trade file itself. Data (the former
    Market data) opens on today too (2026-09-21): its whole-book panels ask whether TODAY's
    marks can be trusted, and the live pull only writes today's. Book, Spreads, Curve, Risk
    and Expiries have no picker: they follow the header's as-of store, whose default is today."""
    snapshot_date = data["as_of_date"] if data["as_of_date"] != "none" else None
    today = cash_ladder.today_ny()
    tab_builders = {
        "book": book.build_layout,
        "spreads": spreads.build_layout,
        "curve": curve.build_layout,
        "risk": risk.build_layout,
        "expiries": expiries.build_layout,
        "blotter": blotter.build_layout,
        "ladder": cash_ladder.build_layout,
        "market-data": market_data.build_layout,
    }
    # Only the Blotter opens on another day; every other tab names today, the header's default.
    tab_defaults = {key: today for key in tab_builders}
    tab_defaults["blotter"] = snapshot_date
    tabs = [dcc.Tab(label=label, value=TAB_KEYS[label], className="tab", selected_className="tab--selected")
            for label in VISIBLE_TABS]
    bodies = [
        html.Div(tab_builders[TAB_KEYS[label]](default_date=tab_defaults[TAB_KEYS[label]]),
                 id=tab_body_id(label), className="tab-body")
        for label in VISIBLE_TABS
    ]
    return html.Div([
        html.Div(className="top-bar", children=[
            dcc.Tabs(id=MAIN_TABS_ID, value=TAB_KEYS[VISIBLE_TABS[0]], children=tabs,
                     parent_className="tabs-bar", className="tabs-strip"),
            uploads.layout(data),
        ]),
        header.layout(),
        html.Div(id="tab-bodies", children=bodies),
        dcc.Store(id=header.AS_OF_STORE_ID, data=today),
        dcc.Store(id=header.AS_OF_PICKED_ID, data=False),
        # "The data changed" signal (ui/revision.py): every tab listens, so an upload or a
        # Bloomberg pull shows up without a browser reload.
        *revision.components(db_path if db_path is not None else get_db_path()),
    ])


def create_app(db_path: Union[str, Path, None] = None, start_feed: bool = False) -> dash.Dash:
    """Build the Dash app. db_path defaults to RISK_DB / data/raw/risk.db.
    start_feed=True (the launcher) starts the Bloomberg live feed thread when available."""
    resolved = Path(db_path) if db_path is not None else get_db_path()
    ensure_schema(resolved)
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
    # A callable layout: Dash builds it on EVERY page load, so the as-of defaults (today in
    # New York) and the upload summary are fresh for a page opened days after `pnl`
    # started, instead of frozen at start-up (user, 2026-09-22: "by default, always price
    # pnl as of today"). `_layout_value()` is what tests walk.
    app.layout = lambda: build_layout(load_summary(resolved), db_path=resolved)

    header.register_callbacks(app, get_db_path=lambda: resolved)
    cash_ladder.register_callbacks(app, get_db_path=lambda: resolved)
    blotter.register_callbacks(app, get_db_path=lambda: resolved)
    curve.register_callbacks(app, get_db_path=lambda: resolved)
    book.register_callbacks(app, get_db_path=lambda: resolved)
    spreads.register_callbacks(app, get_db_path=lambda: resolved)
    expiries.register_callbacks(app, get_db_path=lambda: resolved)
    risk.register_callbacks(app, get_db_path=lambda: resolved)
    market_data.register_callbacks(app, get_db_path=lambda: resolved)
    uploads.register(app, get_db_path=lambda: resolved)
    revision.register(app, get_db_path=lambda: resolved)

    # The header's as-of (user, 2026-09-22: "always price pnl as of today ... unless changed
    # specifically otherwise"): today in New York on every page load (the callable layout
    # above), following the Blotter's or FX & cash's date picker when the user changes one
    # (the last change wins; Data's own picker only scopes that tab), and rolling to
    # the new day at New York midnight -- header and both pickers together -- unless a day
    # other than today was picked. `prevent_initial_call`: the pickers' initial values are
    # the same default and must not count as a pick.
    @app.callback(Output(header.AS_OF_STORE_ID, "data"),
                  Output(header.AS_OF_PICKED_ID, "data"),
                  Input(cash_ladder.DATE_PICKER_ID, "date"),
                  Input(blotter.DATE_PICKER_ID, "date"),
                  prevent_initial_call=True)
    def _follow_pickers(ladder_date, blotter_date):
        triggered = dash.ctx.triggered_id
        picked = blotter_date if triggered == blotter.DATE_PICKER_ID else ladder_date
        return header.as_of_after_pick(picked, cash_ladder.today_ny())

    @app.callback(Output(header.AS_OF_STORE_ID, "data", allow_duplicate=True),
                  Output(cash_ladder.DATE_PICKER_ID, "date", allow_duplicate=True),
                  Output(blotter.DATE_PICKER_ID, "date", allow_duplicate=True),
                  Input(revision.POLL_ID, "n_intervals"),
                  State(header.AS_OF_STORE_ID, "data"),
                  State(header.AS_OF_PICKED_ID, "data"),
                  prevent_initial_call=True)
    def _roll_to_today(_n, store, picked):
        today = header.as_of_after_tick(store, bool(picked), cash_ladder.today_ny())
        if today is None:
            return dash.no_update, dash.no_update, dash.no_update
        return today, today, today

    # Show/hide the always-present tab bodies (see build_layout docstring) on the
    # dcc.Tabs' own `value`, rather than nesting bodies inside dcc.Tab.children.
    # The tab bar's value is the selected tab's key (`TAB_KEYS`).
    body_outputs = [Output(tab_body_id(label), "style") for label in VISIBLE_TABS]
    app.callback(*body_outputs, Input(MAIN_TABS_ID, "value"))(
        lambda selected: [{} if TAB_KEYS[label] == selected else {"display": "none"} for label in VISIBLE_TABS]
    )

    # A tab's name on another screen is a link (user, 2026-09-25: "make tab names in the
    # screens clickable links"): one callback on every tab link, by pattern, so a link a
    # tab's own callback renders later works too. It writes the tab bar's value, and the
    # show/hide callback above follows. `prevent_initial_call` plus the n_clicks check in
    # `tab_from_link_click`: neither the page load nor a re-rendered body switches tabs.
    @app.callback(Output(MAIN_TABS_ID, "value"),
                  Input({"type": TAB_LINK_TYPE, "tab": ALL, "idx": ALL}, "n_clicks"),
                  prevent_initial_call=True)
    def _follow_tab_link(_clicks):
        key = tab_from_link_click(dash.ctx.triggered)
        return dash.no_update if key is None else key

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
    """`(feed, reason)`: the on-request Bloomberg feed (data.bloomberg.live.LiveFeed). It is
    started asleep: nothing is asked of Bloomberg at start-up, on a timer or after an
    upload (user decision 2026-09-21), only when "Pull Bloomberg now" is pressed, and that
    one request also fills the past closes still missing. `feed` is None with the `reason`
    only when RISK_LIVE=0 switches it off; no prices are invented either way. The Bloomberg
    library is brought up to date here once, so a database from before it existed gets one."""
    if os.environ.get("RISK_LIVE", "1") == "0":
        return None, FEED_SWITCHED_OFF
    from data.bloomberg.live import start_feed_if_available
    host = os.environ.get("BLP_HOST", "localhost")
    port = int(os.environ.get("BLP_PORT", "8194"))
    try:
        from contextlib import closing
        from data.bloomberg import library
        from data.ingest.schema import connect
        with closing(connect(db_path)) as conn:
            if library.is_out_of_date(conn):
                library.sync(conn)
    except Exception as exc:  # noqa: BLE001 -- a reader brings the library up to date itself
        print(f"Bloomberg library: not brought up to date at start ({exc!r})", flush=True)
    feed, why = start_feed_if_available(db_path, host=host, port=port)
    print(f"Bloomberg feed: {'on request only (Pull Bloomberg now)' if feed else 'not started: ' + why}", flush=True)
    return feed, ("" if feed else why)


def start_bloomberg_feed(db_path: Path):
    """The feed alone (None when unavailable); see `start_bloomberg_feed_with_reason`."""
    return start_bloomberg_feed_with_reason(db_path)[0]


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
