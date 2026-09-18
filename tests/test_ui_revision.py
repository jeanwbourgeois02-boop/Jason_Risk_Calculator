"""ui/revision.py: the "data changed" signal that replaces a browser reload, plus the
two pieces of the same 2026-09-18 change that lean on it -- the per-render pricing
snapshot (ui/tabs/blotter_pricing.py) and the header's failure card (ui/tabs/header.py).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dash  # noqa: E402

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui import revision  # noqa: E402
from ui.tabs import blotter_pricing as bp  # noqa: E402
from ui.tabs import header  # noqa: E402


def _db(tmp_path, name="risk.db") -> Path:
    path = tmp_path / name
    conn = sqlite3.connect(path)
    schema.create_schema(conn)
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description) VALUES "
                 "('t1','XLSX','EURUSD','FX_FWD','t1','2026-09-01',1000000,1.10,'acct','cpty','','trader','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("t1", 1, "FX_NEAR", "EUR", 1000000, "2026-09-01", "2026-12-01", 1.10, 1),
        ("t1", 2, "FX_NEAR", "USD", -1100000, "2026-09-01", "2026-12-01", 1.10, 1),
    ])
    conn.commit()
    conn.close()
    return path


# --------------------------------------------------------------------------- decide
def test_decide_publishes_nothing_when_the_file_is_unchanged():
    assert revision.decide("a", None, "a") == (None, None)
    assert revision.decide("a", "b", "a") == (None, None)  # a pending change that reverted is dropped


def test_decide_waits_one_tick_before_publishing_a_change():
    # tick 1: the file just changed -> remember it, publish nothing yet
    assert revision.decide("a", None, "b") == (None, "b")
    # tick 2: still "b" -> the writes have settled -> publish
    assert revision.decide("a", "b", "b") == ("b", None)


def test_decide_keeps_waiting_while_writes_keep_landing():
    """A pull that lands as many batches moves the signature on every tick: nothing is
    published until it holds still, so the page redraws once, not once per batch."""
    current, pending = "a", None
    for now in ("b", "c", "d"):
        publish, pending = revision.decide(current, pending, now)
        assert publish is None
    assert revision.decide(current, pending, "d") == ("d", None)


def test_decide_ignores_a_missing_file():
    assert revision.decide("a", "b", "") == (None, None)


# --------------------------------------------------------------------------- signatures
def test_file_signature_moves_on_a_write_and_is_blank_for_a_missing_file(tmp_path):
    path = _db(tmp_path)
    before = revision.file_signature(path)
    assert before
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO marks VALUES ('2026-09-18','EURUSD','2026-09-18','SPOT',1.17,'BBG_BFXFORWARD','x')")
    conn.commit()
    conn.close()
    os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 1_000_000))  # coarse-clock proof
    assert revision.file_signature(path) != before
    assert revision.file_signature(tmp_path / "absent.db") == ""


def test_book_signature_moves_on_a_trade_change_but_not_on_a_mark(tmp_path):
    path = _db(tmp_path)
    before = revision.book_signature(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO marks VALUES ('2026-09-18','EURUSD','2026-09-18','SPOT',1.17,'BBG_BFXFORWARD','x')")
    conn.commit()
    assert revision.book_signature(path) == before  # marks are not the trade set
    conn.execute("UPDATE trades SET quantity = -1000000 WHERE trade_id = 't1'")  # a re-upload flips a sign
    conn.commit()
    conn.close()
    assert revision.book_signature(path) != before


def test_book_signature_is_blank_when_the_database_cannot_be_read(tmp_path):
    assert revision.book_signature(tmp_path / "absent.db") == ""


# --------------------------------------------------------------------------- wiring
def test_every_view_listens_to_the_revision_signal(tmp_path):
    """The point of the change: header, Ladder, Blotter and Market data all re-run when
    the data changes, so nothing needs a browser reload."""
    app = uiapp.create_app(db_path=_db(tmp_path), start_feed=False)
    listeners = {}
    for key, spec in app.callback_map.items():
        ids = {i["id"] for i in spec["inputs"]}
        if revision.DATA_REVISION_ID in ids or revision.BOOK_REVISION_ID in ids:
            listeners[key] = ids
    joined = " ".join(listeners)
    for output in (f"{header.HEADER_ID}-figures", "cash-ladder-table-container", "blotter-content",
                   "market-data-body", "blotter-datatable-total"):
        assert output in joined, f"{output} does not listen to the revision signal"


def test_poll_publishes_a_settled_change_and_leaves_an_unchanged_file_alone(tmp_path):
    path = _db(tmp_path)
    app = uiapp.create_app(db_path=path, start_feed=False)
    key = next(k for k in app.callback_map if revision.PENDING_ID in k)
    poll = app.callback_map[key]["callback"].__wrapped__
    now, book = revision.file_signature(path), revision.book_signature(path)

    assert poll(1, now, book, None) == (dash.no_update, dash.no_update, dash.no_update)  # nothing changed
    data_rev, book_rev, pending = poll(2, "stale", book, None)                           # changed: wait a tick
    assert data_rev is dash.no_update and pending == now
    data_rev, book_rev, pending = poll(3, "stale", book, now)                            # settled: publish
    assert data_rev == now and book_rev is dash.no_update and pending is None            # trades unchanged
    data_rev, book_rev, _ = poll(4, "stale", "old-book", now)
    assert data_rev == now and book_rev == book                                          # trades changed too


# --------------------------------------------------------------------------- pricing snapshot
def test_pricing_snapshot_prices_one_render_once_per_date_even_while_the_file_changes(tmp_path, monkeypatch):
    """Without the snapshot a write landing mid-render changed the cache key on every
    call, and one Total book render re-ran value_book 76 times instead of 6."""
    path = _db(tmp_path)
    runs = []
    real = bp._priced_value_book_uncached
    monkeypatch.setattr(bp, "_priced_value_book_uncached", lambda c, d: (runs.append(d), real(c, d))[1])
    real_key, tick = bp._db_cache_key, [0.0]

    def moving(conn):  # every lookup sees a newer mtime, as under a concurrent writer
        key = real_key(conn)
        tick[0] += 1.0
        return (key[0], key[1] + tick[0])
    monkeypatch.setattr(bp, "_db_cache_key", moving)
    bp._priced_value_book_cached.cache_clear()

    conn = uiapp.connect_readonly(path)
    try:
        for _ in range(5):
            bp.priced_value_book(conn, "2026-09-18")
        assert len(runs) == 5  # the old behaviour: every call misses

        runs.clear()
        with bp.pricing_snapshot(conn):
            for _ in range(5):
                bp.priced_value_book(conn, "2026-09-18")
            bp.priced_value_book(conn, "2026-09-17")
        assert runs == ["2026-09-18", "2026-09-17"]  # once per distinct date

        runs.clear()
        bp.priced_value_book(conn, "2026-09-18")
        assert runs == ["2026-09-18"]  # released on exit: the next render takes a fresh key
    finally:
        conn.close()
        bp._priced_value_book_cached.cache_clear()


def test_pricing_snapshot_is_reentrant_and_always_released(tmp_path):
    conn = uiapp.connect_readonly(_db(tmp_path))
    try:
        with bp.pricing_snapshot(conn):
            outer = bp._SNAPSHOT.key
            with bp.pricing_snapshot(conn):
                assert bp._SNAPSHOT.key is outer  # the inner block keeps the outer key
            assert bp._SNAPSHOT.key is outer
        assert bp._SNAPSHOT.key is None
        try:
            with bp.pricing_snapshot(conn):
                raise RuntimeError("render failed")
        except RuntimeError:
            pass
        assert bp._SNAPSHOT.key is None  # released even when the render raises
    finally:
        conn.close()


# --------------------------------------------------------------------------- header failure card
def test_header_shows_why_instead_of_staying_blank_when_the_figures_raise(tmp_path, monkeypatch):
    """An exception here used to escape as an HTTP 500 and leave the placeholder on
    screen for good (user, 2026-09-18: "the headlines ... didn't even show")."""
    app = uiapp.create_app(db_path=_db(tmp_path), start_feed=False)
    key = next(k for k in app.callback_map if k.startswith(f"{header.HEADER_ID}-figures"))
    update = app.callback_map[key]["callback"].__wrapped__

    def boom(conn, as_of):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(header, "_build_figures", boom)

    cards = update("2026-09-18", "rev-1")
    assert len(cards) == 1
    assert "headline could not be computed" in str(cards[0])
    assert "database is locked" in str(cards[0])
