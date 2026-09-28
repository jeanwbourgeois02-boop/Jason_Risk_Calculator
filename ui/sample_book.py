"""The sample book, reachable from inside the app (user, 2026-09-28: one command, `chelsea`,
and the sample book reachable from inside the app).

The real database (`data/raw/risk.db`) holds only what Jason uploads (hard rule 1): the sample
is never written into it. "View the sample book" builds a throw-away database at
`SAMPLE_DB_PATH` (`data/raw/sample.db`, git-ignored) from the golden book's own fixture
(`tests.golden_book.build_book`: the synthetic commodity blotter at synthetic marks, extended
day by day to today so Daily and 5d are not zero), then the ledger's freeze of what has
settled, and makes it the process's ACTIVE database (`ui.app.set_active_db`): every render
reads the active path at call time, and the revision poll keys on it, so every tab refreshes
in place. "Back to my book" makes the real database active again and deletes the sample file
(best effort: a reader still holding it on Windows leaves it for the next switch to rebuild).

While the sample is active the top bar shows a chip saying so, and "Upload blotter" is
disabled with the reason on hover (`uploads._confirm` refuses too), so an upload can never
land in the sample. The link is shown always, small, so the user can compare a loaded book
with the sample; the Book tab's empty state offers it too (`ui.tabs.book.body`).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from dash import html

from data.paths import REPO_ROOT

log = logging.getLogger(__name__)

SAMPLE_DB_PATH = REPO_ROOT / "data" / "raw" / "sample.db"
LINK_ID = "sample-book-link"                 # the top bar's "View the sample book"
BACK_ID = "sample-book-back"                 # the chip's "Back to my book"
CHIP_ID = "sample-book-chip"                 # the chip's slot in the top bar
LINK_TYPE = "sample-book-view-link"          # pattern id for links rendered inside a tab body
LINK_LABEL = "View the sample book"
CHIP_TEXT = "SAMPLE BOOK: not Jason's book, the real database is untouched"
UPLOAD_LABEL = "Upload blotter"
UPLOAD_LOCKED_TITLE = "Go back to my book first: an upload goes to the real book"
TABLES = ("instruments", "trades", "trade_legs", "marks", "realised_pnl")


def build_sample_db(path: Path = SAMPLE_DB_PATH, through: Optional[str] = None) -> dict:
    """Build the sample book fresh at `path`: the file is removed first, then the golden
    fixture's book with its marks carried to `through` (today), then the ledger's freeze of
    what has settled by the book date. Returns the row count per table, the last mark date
    and the path."""
    from data.bloomberg.live import book_today
    from data.ingest import schema
    from engine.pnl import ledger
    from tests.golden_book import build_book

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    remove_sample_files(path)
    conn = schema.connect(path)
    try:
        build_book(conn, through=through)
        ledger.realise_settled(conn, book_today().isoformat())
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        last_mark = conn.execute("SELECT MAX(as_of_date) FROM marks").fetchone()[0]
    finally:
        conn.close()
    return {"path": path, "counts": counts, "last_mark_date": last_mark}


def remove_sample_files(path: Path = SAMPLE_DB_PATH) -> bool:
    """Remove the sample database, its journal and its status file. True when nothing is
    left; False (logged) when a reader still holds the file (Windows), never an error."""
    ok = True
    for p in (path, path.with_name(path.name + "-journal"), path.with_name(path.name + ".bloomberg_status.json")):
        try:
            if p.exists():
                p.unlink()
        except OSError as exc:
            log.warning("sample book: %s not removed (%s); the next switch rebuilds it", p.name, exc)
            ok = False
    return ok


def is_sample_active() -> bool:
    from ui.app import active_db_path      # local: ui.app imports this module
    return Path(active_db_path()) == SAMPLE_DB_PATH


def view_link(idx: str = "top", small: bool = True) -> html.A:
    """The "View the sample book" link. `idx` "top" is the top bar's fixed id; any other
    value is a pattern id for a link rendered inside a tab body (the Book tab's empty
    state), so the one switch callback hears it too."""
    link_id = LINK_ID if idx == "top" else {"type": LINK_TYPE, "idx": idx}
    return html.A(LINK_LABEL, id=link_id, n_clicks=0, href="#", role="button",
                  className="sample-book-link" + (" sample-book-link--small" if small else ""),
                  title="Open the synthetic sample book (a made-up blotter at made-up marks) "
                        "in a throw-away database. Your own book and the real database are untouched.")


def chip(active: bool) -> list:
    """The chip's children: the SAMPLE BOOK notice with "Back to my book". Rendered on every
    page load whether or not the sample is active, because "Back to my book" is an Input of
    the switch callback (`uploads._switch_book`) and Dash never fires a callback whose static
    Input is absent from the layout (found 2026-09-28: the link did nothing on the real
    book). Whether the chip shows is `chip_style`, toggled by the same callback."""
    return [html.Span(CHIP_TEXT, className="sample-book-chip-text"),
            html.Button("Back to my book", id=BACK_ID, n_clicks=0, className="btn sample-book-back",
                        title="Make the real database active again and delete the sample file.")]


def chip_style(active: bool) -> dict:
    """The chip container's style: shown while the sample is active, else `display: none`
    (the chip and its button stay in the layout, see `chip`)."""
    return {} if active else {"display": "none"}


def upload_button(locked: bool) -> html.Button:
    """The "Upload blotter" button: disabled with the reason on hover while the sample is active."""
    if locked:
        return html.Button(UPLOAD_LABEL, className="btn btn--locked", disabled=True, title=UPLOAD_LOCKED_TITLE)
    return html.Button(UPLOAD_LABEL, className="btn")


def switch_from_trigger(triggered) -> Optional[str]:
    """"sample", "real" or None from Dash's `ctx.triggered`. Only a real click counts (a link
    just rendered fires with n_clicks None or 0 and is ignored), the first that passes wins."""
    for entry in triggered or ():
        try:
            prop_id, value = entry.get("prop_id", ""), entry.get("value")
        except AttributeError:
            continue
        if not value or not isinstance(prop_id, str):
            continue
        raw_id = prop_id.rsplit(".", 1)[0]
        if raw_id == BACK_ID:
            return "real"
        if raw_id == LINK_ID:
            return "sample"
        try:
            link_id = json.loads(raw_id)
        except (TypeError, ValueError):
            continue
        if isinstance(link_id, dict) and link_id.get("type") == LINK_TYPE:
            return "sample"
    return None


def switch(target: str) -> dict:
    """Make `target` ("sample" or "real") the active database. For "sample" the file is built
    fresh with the marks carried to today; for "real" the sample file is removed (best effort).
    Returns {"active": Path, "sample": bool, "built": the build's dict or None, "removed": bool}."""
    from ui.app import get_db_path, set_active_db      # local: ui.app imports this module
    from ui.tabs.controls import today_ny

    if target == "sample":
        built = build_sample_db(SAMPLE_DB_PATH, through=today_ny())
        set_active_db(SAMPLE_DB_PATH)
        log.info("sample book active: %s (%d trades, %d marks, last mark %s); the real database is untouched",
                 SAMPLE_DB_PATH, built["counts"]["trades"], built["counts"]["marks"], built["last_mark_date"])
        return {"active": SAMPLE_DB_PATH, "sample": True, "built": built, "removed": False}
    real = get_db_path()
    set_active_db(real)
    removed = remove_sample_files(SAMPLE_DB_PATH)
    log.info("back to my book: %s active%s", real, "" if removed else " (the sample file is still held)")
    return {"active": real, "sample": False, "built": None, "removed": removed}
