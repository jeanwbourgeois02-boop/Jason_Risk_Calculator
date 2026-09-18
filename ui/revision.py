"""The page's "the data changed" signal, so nothing needs a browser reload (2026-09-18,
user: "when uploading an excel file you still have to reload the site").

Before this, an upload wrote the database and updated its own status line, and that was
all: the header, the Ladder and the Blotter are driven by their own date pickers and
never heard about it, so the page kept showing the book as it stood at page load until
the browser was reloaded. The same went for marks the Bloomberg pull writes a few
seconds after an upload.

Two `dcc.Store`s, both published by one cheap poll (`POLL_MS`):

  DATA_REVISION_ID  the database FILE changed (trades or marks). Header, Ladder and
                    Market data listen to it; the Blotter's Total book / Futures tables
                    refresh their rows in place from it (filters, sort and page are
                    kept) and its FX / Rates / Options / Bundles views re-render.
  BOOK_REVISION_ID  the TRADE SET changed (an upload, a manually booked or deleted
                    trade, option terms typed in). The Blotter rebuilds its current
                    sub-tab from it, since its filter options depend on the trades.

The poll costs one `os.stat` per tick and opens the database only when the file has
actually changed. A change is published once the file has been quiet for a whole tick
(`decide`), so a pull that lands as dozens of write batches over several seconds causes
ONE redraw when it settles rather than one per batch. `uploads._confirm` publishes both
revisions itself, immediately, so an upload never waits for the poll.

Nothing here reads a mark or computes a number; every listener re-runs its own
unchanged render.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from dash import Input, Output, State, dcc, no_update

DATA_REVISION_ID = "data-revision"
BOOK_REVISION_ID = "book-revision"
PENDING_ID = "data-revision-pending"
POLL_ID = "data-revision-poll"
POLL_MS = 5_000


def file_signature(db_path: Union[str, Path]) -> str:
    """Modification time and size of the database file; "" when it is missing. One
    `os.stat`, no connection opened. The rollback journal is deliberately not part of
    it: it only exists while a write is in flight."""
    try:
        st = os.stat(db_path)
    except OSError:
        return ""
    return f"{st.st_mtime_ns}:{st.st_size}"


def book_signature(db_path: Union[str, Path]) -> str:
    """A fingerprint of the trade set: counts and sums that move whenever a trade is
    added, replaced or deleted, or option terms are typed in. "" when the database
    cannot be read right now (the caller then leaves the book revision alone and the
    next tick tries again)."""
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


def components(db_path: Union[str, Path]) -> list:
    """The stores and the poll timer, for `ui.app.build_layout`. Both revisions start at
    the file's current state, so the first tick publishes nothing."""
    return [
        dcc.Store(id=DATA_REVISION_ID, data=file_signature(db_path)),
        dcc.Store(id=BOOK_REVISION_ID, data=book_signature(db_path)),
        dcc.Store(id=PENDING_ID),
        dcc.Interval(id=POLL_ID, interval=POLL_MS, n_intervals=0),
    ]


def register(app, get_db_path: Callable[[], object]) -> None:
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
