"""The page's "the data changed" signal, so nothing needs a browser reload (2026-09-18,
user: "when uploading an excel file you still have to reload the site").

Before this, an upload wrote the database and updated its own status line, and that was
all: the header, the Ladder and the Blotter are driven by their own date pickers and
never heard about it, so the page kept showing the book as it stood at page load until
the browser was reloaded. The same went for marks the Bloomberg pull writes a few
seconds after an upload.

Two `dcc.Store`s, both published by one cheap poll (`POLL_MS`):

  DATA_REVISION_ID  the database FILE changed (trades, marks, option terms -- anything).
                    Everything that shows a figure listens to it and refreshes IN
                    PLACE: the header, the Ladder, Market data, the Blotter's Total
                    book / Futures rows (filters, sort and page are kept), the Options
                    table (its own module: `ui.tabs.options._render`), the P&L strip
                    above it and the Blotter's banners (missing option terms, stored
                    values that are not numbers). Only the Blotter's FX and Bundles
                    views, which are one static block, are rebuilt on it.
  BOOK_REVISION_ID  the book's fingerprint moved (`book_signature`): a trade added,
                    replaced or deleted, but ALSO option terms typed in (it sums the
                    strikes) or a quantity's sign changed (it sums signed quantities).
                    Publishers: this poll, `uploads._confirm` and `ui.tabs.options`
                    (Save in the terms editor).

What rebuilds a Blotter sub-tab (2026-09-18). Tearing a sub-tab down and building it
again resets whatever the user is doing in it -- a strike half typed into an Options
cell -- and the first thing the user does after a restart is type three strikes in a
row. So `ui.tabs.blotter._update`
rebuilds on a book revision only when the TRADE SET really moved: it compares
`trade_set_signature` -- blind to option terms and to the SIGN of a quantity or a leg --
with the one the content on screen was built from (kept in the page, per browser tab).
A genuine book change (an upload) still rebuilds; a
saved term is, for the Blotter, a data revision like any other,
and everything that depends on it refreshes in place. The decision is taken THERE, not by
the publishers, on purpose: Dash fires a store's listeners even when a callback sets it to
the value it already holds (checked in a real browser, Dash 4.4.1), so a publisher that
re-publishes an unchanged book signature would otherwise still cause a rebuild. A
callback that publishes to a store whose value may not have moved should go through
`publish_if_changed`.

The poll costs one `os.stat` per tick and opens the database only when the file has
actually changed. A change is published once the file has been quiet for a whole tick
(`decide`), so a pull that lands as dozens of write batches over several seconds causes
ONE redraw when it settles rather than one per batch. `uploads._confirm` publishes both
revisions itself, immediately, so an upload never waits for the poll; so does the terms
editor's Save, and `ui.tabs.blotter` publishes the data revision the
moment an Options cell edit has been saved, so the banner and the figures never wait 5-10
seconds for the poll to notice.

The page's build (2026-09-28, user: "never a stale page"). A browser tab left open across
a restart keeps the old layout and bundles while its callbacks land on the new process.
So every page bakes the fingerprint it was built with (`ui.launch.source_fingerprint`,
the same string the launcher's identity route answers) into `BUILD_ID`; the poll's
companion `_build_check` compares it with the process's own on every tick and, when they
differ, sets `STALE_ID`, whose clientside listener does `window.location.reload()`: the
one browser reload in the app, and only because the CODE changed, never for data (data is
the in-place refresh above). `_build_check`'s ids never change, so a page from any older
build still reaches it.

Nothing here reads a mark or computes a number; every listener re-runs its own
unchanged render.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from dash import Input, Output, State, dcc, html, no_update

DATA_REVISION_ID = "data-revision"
BOOK_REVISION_ID = "book-revision"
PENDING_ID = "data-revision-pending"
POLL_ID = "data-revision-poll"
POLL_MS = 5_000
BUILD_ID = "build-fingerprint"      # the source fingerprint the page was built with
STALE_ID = "page-stale"             # True once the process answering is another build
RELOAD_SINK_ID = "page-reload-sink"  # the clientside reload's (unused) output


def page_is_stale(page_build, build) -> bool:
    """True when the page was built by another build than the process answering it. An
    empty fingerprint on either side (a test app, a page that carried none) is never
    stale: a reload is only ever forced on a known difference."""
    return bool(build) and bool(page_build) and page_build != build


def file_signature(db_path: Union[str, Path]) -> str:
    """Modification time and size of the database file; "" when it is missing. One
    `os.stat`, no connection opened. The rollback journal is deliberately not part of
    it: it only exists while a write is in flight."""
    try:
        st = os.stat(db_path)
    except OSError:
        return ""
    return f"{st.st_mtime_ns}:{st.st_size}"


def trade_set_signature(db_path: Union[str, Path]) -> str:
    """A fingerprint of the TRADE SET alone: how many trades and legs there are and how
    big, blind to the sign of a quantity or a leg amount and to option terms. It moves on
    an upload; it does NOT move when a strike,
    type or payoff is typed in, nor on a sign alone (the retired IRS direction flip's
    case: a negated quantity and legs are not a new trade). This is what the
    Blotter compares to decide whether a sub-tab must be rebuilt (module docstring). ""
    when the database cannot be read right now -- the caller then rebuilds, the safe side."""
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        trades = conn.execute(
            "SELECT COUNT(*), TOTAL(ABS(quantity)), TOTAL(price), MAX(trade_date) FROM trades").fetchone()
        legs = conn.execute("SELECT COUNT(*), TOTAL(ABS(amount)) FROM trade_legs").fetchone()
        return repr((tuple(trades), tuple(legs)))
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def publish_if_changed(now: Optional[str], current: Optional[str]):
    """`now` when it is a real signature that differs from what the store holds, else
    `no_update`. For a callback that publishes to a revision store: Dash fires the store's
    listeners even when it is set to the value it already has, so an unchanged signature
    must not be returned as if it were news."""
    return now if now and now != current else no_update


def book_signature(db_path: Union[str, Path]) -> str:
    """A fingerprint of the book as the BOOK revision publishes it: counts and sums that
    move whenever a trade is added, replaced or deleted, option terms are typed in (the
    strikes are summed) or a quantity's sign changes (quantities are summed signed).
    Wider than the trade set on purpose -- see `trade_set_signature` for the narrower one
    the Blotter rebuilds on. "" when the database cannot be read right now (the caller
    then leaves the book revision alone and the next tick tries again)."""
    try:
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        trades = conn.execute(
            "SELECT COUNT(*), TOTAL(quantity), TOTAL(price), MAX(trade_date) FROM trades").fetchone()
        legs = conn.execute("SELECT COUNT(*), TOTAL(amount) FROM trade_legs").fetchone()
        try:
            terms = conn.execute("SELECT COUNT(*), TOTAL(strike) FROM instrument_options").fetchone()
        except sqlite3.Error:  # defensively-created table, absent on an older database
            terms = ()
        return repr((tuple(trades), tuple(legs), tuple(terms)))
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def decide(current: Optional[str], pending: Optional[str], now: str) -> Tuple[Optional[str], Optional[str]]:
    """`(publish, new_pending)` for one poll tick: `publish` is the signature to publish
    as the data revision, or None to publish nothing.

    A signature is published only when it differs from the published one AND was already
    seen on the previous tick, i.e. the file has been quiet for a whole tick. The first
    tick after a change only records it as pending."""
    if not now or now == current:
        return None, None       # nothing new (or no file): drop any pending change
    if now == pending:
        return now, None        # unchanged since the last tick: the writes have settled
    return None, now            # changed just now: wait one more tick


def components(db_path: Union[str, Path], build: str = "") -> list:
    """The stores and the poll timer, for `ui.app.build_layout`. Both revisions start at
    the file's current state, so the first tick publishes nothing; `build` is the
    fingerprint this page is built by (module docstring)."""
    return [
        dcc.Store(id=DATA_REVISION_ID, data=file_signature(db_path)),
        dcc.Store(id=BOOK_REVISION_ID, data=book_signature(db_path)),
        dcc.Store(id=PENDING_ID),
        dcc.Store(id=BUILD_ID, data=build),
        dcc.Store(id=STALE_ID, data=False),
        html.Div(id=RELOAD_SINK_ID, style={"display": "none"}),
        dcc.Interval(id=POLL_ID, interval=POLL_MS, n_intervals=0),
    ]


def register(app, get_db_path: Callable[[], object], build: str = "") -> None:
    @app.callback(
        Output(STALE_ID, "data"),
        Input(POLL_ID, "n_intervals"),
        State(BUILD_ID, "data"),
        prevent_initial_call=True,
    )
    def _build_check(_n, page_build):
        return True if page_is_stale(page_build, build) else no_update

    # The one browser reload: the page was built by another build than this process.
    app.clientside_callback(
        "function(stale) { if (stale) { window.location.reload(); } "
        "return window.dash_clientside.no_update; }",
        Output(RELOAD_SINK_ID, "children"),
        Input(STALE_ID, "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output(DATA_REVISION_ID, "data"),
        Output(BOOK_REVISION_ID, "data"),
        Output(PENDING_ID, "data"),
        Input(POLL_ID, "n_intervals"),
        State(DATA_REVISION_ID, "data"),
        State(BOOK_REVISION_ID, "data"),
        State(PENDING_ID, "data"),
        prevent_initial_call=True,
    )
    def _poll(_n, data_rev, book_rev, pending):
        db_path = get_db_path()
        publish, new_pending = decide(data_rev, pending, file_signature(db_path))
        if publish is None:
            return no_update, no_update, (new_pending if new_pending != pending else no_update)
        book = book_signature(db_path)
        return publish, (book if book and book != book_rev else no_update), new_pending
