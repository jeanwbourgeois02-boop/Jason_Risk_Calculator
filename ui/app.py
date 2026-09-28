"""Dash application skeleton for the risk monitor.

Owns: ui/. Reads (read-only) from the SQLite database produced by data/ingest and
data/bloomberg; never recomputes P&L or delta -- that lives in engine/.

Six tabs (CLAUDE.md "Screens redesign plan", user 2026-09-25; UI redesign waves 1 to 3, user
2026-09-28), in this order: Book, Exposure, P&L, Risk, Trades, Data. "Exposure" is
the former Curve (ui/tabs/curve.py, key "curve"), "P&L" is new (ui/tabs/pnl.py, key "pnl"),
"Trades" the former Blotter (ui/tabs/blotter.py, key "blotter") and "Data" the former Market data
(ui/tabs/market_data.py). The Spreads tab (ui/tabs/spreads.py), the FX & cash tab
(ui/tabs/cash_ladder.py, ui/tabs/exposure.py) and the Timing & cash tab (ui/tabs/expiries.py, the
former Expiries) were deleted on 2026-09-28 (user's yes): the Book tab now holds the spread
detail helpers and shows each position's next date in its Next column;
`HIDDEN_TAB_KEYS` only records their old keys so a stale link is ignored. Each tab has a stable key (`TAB_KEYS`) that names its body's DOM id and the tab bar's value,
separate from the label the user reads, so a rename never moves an id. The app opens on the
first tab, Book (ui/tabs/book.py): the book's home. A slim header (ui/tabs/header.py) sits
above the tabs on every view: the as-of date, Daily / MTD / YTD / LTD and the Data chip.

Options are not a top-level tab: they live inside the Trades tab as a grouped,
collapsible trade summary (ui/tabs/options.py; docs/open-questions.md item 61).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Union

import dash
import flask
from dash import ALL, Input, Output, State, dcc, html

from ui.tabs import blotter, book, curve, header, market_data, pnl, risk
from ui.tabs.controls import today_ny
from ui.tabs.formatting import TAB_LINK_TYPE
from ui import revision, uploads
# The database path rule lives below every layer (data/paths.py) so the Bloomberg CLI mains
# can read it without importing ui/; re-exported here because `ui.app.get_db_path` is the
# name every screen, script and test uses.
from data.paths import DEFAULT_DB_PATH, REPO_ROOT, get_db_path  # noqa: F401

# The ACTIVE database (2026-09-28, user: the sample book reachable from inside the app). The
# process is single-user and local, so one process-wide holder is right: `create_app` sets it
# to the database it was built for, "View the sample book" (ui/sample_book.py) points it at
# the throw-away sample and "Back to my book" points it back. Every render reads it at call
# time (`active_db_path` is the callable every tab's `register_callbacks` receives), and the
# revision poll keys on it, so a switch refreshes every tab in place. The real database
# (`get_db_path()`) is never written by a switch.
ACTIVE_DB: dict = {"path": None}


def active_db_path() -> Path:
    """The database every screen reads right now: the one `set_active_db` last named, else
    the real database (`get_db_path()`)."""
    path = ACTIVE_DB["path"]
    return Path(path) if path is not None else get_db_path()


def set_active_db(path: Union[str, Path, None]) -> Path:
    """Make `path` the active database (None: the real one). Returns the active path."""
    ACTIVE_DB["path"] = Path(path) if path is not None else None
    return active_db_path()

# Order per the UI redesign waves 2 and 3 (user, 2026-09-28: one question per screen:
# Book, Exposure, P&L, Risk, Trades, plus Data), after the screens redesign of
# 2026-09-25 ("the spread is the unit"); the app opens on the first. Each label maps to its
# stable key: the tab bar's value and the body id `tab-body-<key>`. The key never holds '&' or
# a space, and a renamed tab keeps its key ("Exposure" is still "curve", "Trades" still
# "blotter", "Data" still "market-data"), so nothing keyed on a body id moves. The Spreads
# ("spreads") and FX & cash ("ladder") tabs left the bar in wave 3, Timing & cash ("expiries")
# later the same day (`HIDDEN_TAB_KEYS`): a tab link to any of those keys is dead
# (`tab_from_link_click` ignores it), so no screen renders one.
TAB_KEYS = {
    "Book": "book",
    "Exposure": "curve",
    "P&L": "pnl",
    "Risk": "risk",
    "Trades": "blotter",
    "Data": "market-data",
}
VISIBLE_TABS = list(TAB_KEYS)
# Deleted tabs' keys (2026-09-28): nothing of theirs is in the layout; a stale link is ignored.
HIDDEN_TAB_KEYS = {"Spreads": "spreads", "FX & cash": "ladder", "Timing & cash": "expiries"}


def tab_body_id(label: str) -> str:
    """The DOM id of a tab's always-present body: `tab-body-<key>` ("Exposure" ->
    "tab-body-curve"). Only a label in `TAB_KEYS` has a body."""
    return f"tab-body-{TAB_KEYS[label]}"


# Lazy tab bodies (2026-09-28, the page's first load fired every tab's render callback, six
# full-book passes of 0.8-2.4 s in parallel, for five tabs nobody was looking at). A body's
# `html.Div` is always in the layout (its id is what the show/hide callback and every tab link
# key on), but only the first tab's layout is built with the page; the others are built the
# first time their tab is selected (`build_tab_bodies`), and Dash then fires the callbacks whose
# outputs the new layout holds, exactly as on a page load. The keys already built are kept in
# the `TAB_BUILT_ID` store, so a tab is built once per page and keeps its state after that.
TAB_BUILT_ID = "tab-built"
TAB_BUILDERS = {
    "book": book.build_layout,
    "curve": curve.build_layout,
    "pnl": pnl.build_layout,
    "risk": risk.build_layout,
    "blotter": blotter.build_layout,
    "market-data": market_data.build_layout,
}


def tab_layout(key: str) -> html.Div:
    """A tab's own layout, built now: today in New York is its default date (the header's
    picker, `ui/tabs/header.py::DATE_PICKER_ID`, is the one place the as-of changes)."""
    return TAB_BUILDERS[key](default_date=today_ny())


def build_tab_bodies(selected, built) -> list:
    """The lazy-build callback's outputs: for every visible tab its body's children (the tab's
    layout when it is the selected tab and not built yet, else `dash.no_update`), then the
    store's new list of built keys (`dash.no_update` when nothing was built). Pure, so a test can
    call it without a server."""
    built = [k for k in (built or []) if k in TAB_BUILDERS]
    out = []
    added = None
    for label in VISIBLE_TABS:
        key = TAB_KEYS[label]
        if key == selected and key not in built:
            out.append(tab_layout(key))
            added = key
        else:
            out.append(dash.no_update)
    out.append(built + [added] if added else dash.no_update)
    return out


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


FINGERPRINTED_PATHS = ("/_dash-component-suites/", "/assets/")


def no_store_for(path: str) -> bool:
    """True when a response at `path` must carry `Cache-Control: no-store` (the index,
    `_dash-layout`, `_dash-dependencies`, `_dash-update-component`, the launcher's own
    routes): everything but the resources Dash fingerprints in their URL
    (`FINGERPRINTED_PATHS`: the component bundles' `?v=<version>&m=<mtime>`, the `assets/`
    files' `?m=<mtime>`), which may be cached because a changed file is a new URL."""
    return not any(marker in path for marker in FINGERPRINTED_PATHS)


def build_layout(data: dict, db_path=None, build: str = "") -> html.Div:
    """Top-level layout (user decision 2026-09-15, item A, revised 2026-09-15): the tab
    bar (`dcc.Tabs`, holding plain `dcc.Tab(label=..., value=<key>)` objects with NO
    children of their own -- Dash nests a Tab's children inside its own styled wrapper,
    which was pushing the navy `.top-bar` around the whole page) sits at the very top
    with the upload control pinned to its right; the P&L header sits directly under the
    tab bar, on every tab. Below that, every tab body lives in one always-present
    `html.Div(id="tab-bodies")`, each wrapped in its own `html.Div(id=tab_body_id(label))`
    -- the wrappers never leave the layout, so a tab's own callbacks (registered against
    ids inside their body) keep firing once built, regardless of which tab is selected. Since
    2026-09-28 only the first tab's layout is built with the page; the lazy-build callback
    (`build_tab_bodies`, in `create_app`) builds each other tab the first time it is selected.
    One show/hide callback (registered in `create_app`) toggles the bodies' `style` on
    `main-tabs`' `value` (the selected tab's key, `TAB_KEYS`).

    Each tab module owns its own controls/table via `build_layout(default_date)`; this
    module only assembles them and wires the header's date picker (`header.DATE_PICKER_ID`, the
    one picker of the app since 2026-09-28) into `header.AS_OF_STORE_ID`, which every tab reads.

    Defaults: today in New York everywhere (user, 2026-09-22: "always price pnl as of today")."""
    today = today_ny()
    # Every tab names today: the header's picker (ui/tabs/header.py::DATE_PICKER_ID) is the one
    # place the as-of changes since 2026-09-28; the Trades and Data tabs read the header's store.
    # The last uploaded trade date (`data["as_of_date"]`) opens no tab any more.
    first = TAB_KEYS[VISIBLE_TABS[0]]
    tabs = [dcc.Tab(label=label, value=TAB_KEYS[label], className="tab", selected_className="tab--selected")
            for label in VISIBLE_TABS]
    bodies = [
        html.Div(tab_layout(TAB_KEYS[label]) if TAB_KEYS[label] == first else None,
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
        dcc.Store(id=TAB_BUILT_ID, data=[first]),
        dcc.Store(id=header.AS_OF_STORE_ID, data=today),
        dcc.Store(id=header.AS_OF_PICKED_ID, data=False),
        # "The data changed" signal (ui/revision.py): every tab listens, so an upload or a
        # Bloomberg pull shows up without a browser reload; and the build this page is of,
        # so a tab left open across a restart reloads itself once (the code changed).
        *revision.components(db_path if db_path is not None else active_db_path(), build=build),
    ])


def create_app(db_path: Union[str, Path, None] = None, start_feed: bool = False,
               build_fingerprint: Union[str, None] = None) -> dash.Dash:
    """Build the Dash app. db_path defaults to RISK_DB / data/raw/risk.db.
    start_feed=True (the launcher) starts the Bloomberg live feed thread when available.
    build_fingerprint is the source fingerprint this process runs (the launcher passes the
    one its identity route answers; computed here otherwise): every page bakes it in and
    reloads itself once a restart serves another (ui/revision.py)."""
    from ui.launch import source_fingerprint
    resolved = Path(db_path) if db_path is not None else get_db_path()
    ensure_schema(resolved)
    # The database this app was built for is the active one until a switch (module docstring
    # of ui/sample_book.py); a stale sample file from an earlier run is not the active one.
    set_active_db(resolved)
    build = build_fingerprint if build_fingerprint is not None else source_fingerprint()
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
    # Never a stale page (user, 2026-09-28: "make sure there is never a stale page that loads
    # when you load the app"). Every response but the fingerprinted resources is told
    # `Cache-Control: no-store`: the index, `_dash-layout` (the layout the page starts from),
    # `_dash-dependencies` and every `_dash-update-component` answer, so a reload, a Back or
    # a bookmark always fetches the live layout and figures, never a copy the browser kept.
    # The fingerprinted resources keep Dash's own caching, because a changed file is a new
    # URL there: Dash appends `?m=<mtime>` to every `assets/` file it serves and
    # `?v=<version>&m=<mtime>` to each component bundle, so an old cached copy is never
    # served for a new file (`no_store_for` decides, so the rule is testable without a server).
    @app.server.after_request
    def _never_cache(response):
        if no_store_for(flask.request.path):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # A callable layout: Dash builds it on EVERY page load, so the as-of defaults (today in
    # New York) and the upload summary are fresh for a page opened days after `pnl`
    # started, instead of frozen at start-up (user, 2026-09-22: "by default, always price
    # pnl as of today"). `_layout_value()` is what tests walk.
    app.layout = lambda: build_layout(load_summary(active_db_path()), db_path=active_db_path())

    # The deleted tabs (`HIDDEN_TAB_KEYS`) register nothing: their bodies are not in the layout.
    header.register_callbacks(app, get_db_path=active_db_path)
    blotter.register_callbacks(app, get_db_path=active_db_path)
    curve.register_callbacks(app, get_db_path=active_db_path)
    pnl.register_callbacks(app, get_db_path=active_db_path)
    book.register_callbacks(app, get_db_path=active_db_path)
    risk.register_callbacks(app, get_db_path=active_db_path)
    market_data.register_callbacks(app, get_db_path=active_db_path)
    uploads.register(app, get_db_path=active_db_path)
    revision.register(app, get_db_path=active_db_path, build=build)

    # The header's as-of (user, 2026-09-22: "always price pnl as of today ... unless changed
    # specifically otherwise"): today in New York on every page load (the callable layout
    # above), following the header's own date picker when the user changes it (the one picker
    # of the app since 2026-09-28: the Trades and Data tabs read the store), and rolling
    # to the new day at the book's day roll -- header and picker together -- unless a day
    # other than today was picked. `prevent_initial_call`: the picker's initial value is the
    # same default and must not count as a pick.
    @app.callback(Output(header.AS_OF_STORE_ID, "data"),
                  Output(header.AS_OF_PICKED_ID, "data"),
                  Input(header.DATE_PICKER_ID, "date"),
                  prevent_initial_call=True)
    def _follow_pickers(picked_date):
        return header.as_of_after_pick(picked_date, today_ny())

    @app.callback(Output(header.AS_OF_STORE_ID, "data", allow_duplicate=True),
                  Output(header.DATE_PICKER_ID, "date", allow_duplicate=True),
                  Input(revision.POLL_ID, "n_intervals"),
                  State(header.AS_OF_STORE_ID, "data"),
                  State(header.AS_OF_PICKED_ID, "data"),
                  prevent_initial_call=True)
    def _roll_to_today(_n, store, picked):
        today = header.as_of_after_tick(store, bool(picked), today_ny())
        if today is None:
            return dash.no_update, dash.no_update
        return today, today

    # Show/hide the always-present tab bodies (see build_layout docstring) on the
    # dcc.Tabs' own `value`, rather than nesting bodies inside dcc.Tab.children.
    # The tab bar's value is the selected tab's key (`TAB_KEYS`).
    body_outputs = [Output(tab_body_id(label), "style") for label in VISIBLE_TABS]
    app.callback(*body_outputs, Input(MAIN_TABS_ID, "value"))(
        lambda selected: [{} if TAB_KEYS[label] == selected else {"display": "none"} for label in VISIBLE_TABS]
    )

    # Build a tab's layout the first time it is selected (`TAB_BUILT_ID` above): the page loads
    # with the first tab alone, so only its callbacks (and the header's) run on load.
    app.callback(*[Output(tab_body_id(label), "children") for label in VISIBLE_TABS],
                 Output(TAB_BUILT_ID, "data"),
                 Input(MAIN_TABS_ID, "value"), State(TAB_BUILT_ID, "data"))(build_tab_bodies)

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
    # A developer shortcut only; the app is launched by `2_launcher.py` (hard rule 9), which
    # serves `app.server` straight from werkzeug and never calls `app.run`, so Dash's dev
    # tools (hot reload re-serves stale bundles on Windows) are never enabled there.
    app = create_app()
    app.run(debug=False, dev_tools_hot_reload=False)
