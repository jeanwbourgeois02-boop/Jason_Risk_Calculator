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


def test_trade_set_signature_moves_on_a_trade_added_or_resized_but_not_on_a_sign_or_on_option_terms(tmp_path):
    """What the Blotter rebuilds a sub-tab on (2026-09-18). A flipped sign is a swap's
    pay/receive set under Rates; option terms are a strike typed under Options: the user
    makes several of each in a row and none may tear down the sub-tab he is editing in."""
    path = _db(tmp_path)
    before = revision.trade_set_signature(path)
    assert before
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO marks VALUES ('2026-09-18','EURUSD','2026-09-18','SPOT',1.17,'BBG_BFXFORWARD','x')")
    conn.execute("UPDATE trades SET quantity = -quantity")
    conn.execute("UPDATE trade_legs SET amount = -amount")
    conn.execute("INSERT INTO instruments VALUES ('EURUSD1C','FX_OPTION','EUR','USD',1,0,'','2026-12-15')")
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type) VALUES ('EURUSD1C', 1.15, 'CALL')")
    conn.commit()
    assert revision.trade_set_signature(path) == before
    assert revision.book_signature(path)                      # the wider fingerprint is still there, unchanged in shape
    conn.execute("UPDATE trades SET quantity = quantity * 2")  # a re-upload with a different size
    conn.commit()
    resized = revision.trade_set_signature(path)
    assert resized != before
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description) VALUES "
                 "('m1','MANUAL','EURUSD','FX_FWD','m1','2026-09-02',1,1.10,'a','c','','t','')")  # a manual trade booked
    conn.commit()
    conn.close()
    assert revision.trade_set_signature(path) not in (before, resized)
    assert revision.trade_set_signature(tmp_path / "absent.db") == ""


def test_publish_if_changed_never_republishes_the_value_a_store_already_holds():
    """Dash fires a store's listeners even when a callback sets it to the value it already
    has (checked in a real browser, Dash 4.4.1), so an unchanged signature is `no_update`."""
    assert revision.publish_if_changed("b", "a") == "b"
    assert revision.publish_if_changed("b", None) == "b"
    assert revision.publish_if_changed("a", "a") is dash.no_update
    assert revision.publish_if_changed("", "a") is dash.no_update   # file missing / unreadable: not news


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
                   "market-data-body", "blotter-datatable-total",
                   # refreshed IN PLACE rather than by a rebuild of their sub-tab (2026-09-18):
                   "blotter-notices", "blotter-strip-options", "blotter-strip-rates",
                   "options-datatable", "rates-datatable"):
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


def test_a_slow_render_says_so_in_the_terminal_and_a_fast_one_stays_quiet(tmp_path, monkeypatch, caplog):
    """A view that errors prints a traceback; a view that is merely slow printed nothing,
    so a tab that "did not load" on the Bloomberg PC left nothing to diagnose from."""
    import logging
    conn = uiapp.connect_readonly(_db(tmp_path))
    bp._priced_value_book_cached.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger=bp.log.name):
            with bp.pricing_snapshot(conn, "Blotter total"):  # fast: under the threshold
                bp.priced_value_book(conn, "2026-09-18")
            assert not caplog.records

            monkeypatch.setattr(bp, "SLOW_RENDER_SECONDS", 0.0)  # now everything counts as slow
            bp._priced_value_book_cached.cache_clear()
            with bp.pricing_snapshot(conn, "Blotter total"):
                bp.priced_value_book(conn, "2026-09-18")
                bp.priced_value_book(conn, "2026-09-18")  # cache hit: not a re-pricing
                bp.priced_value_book(conn, "2026-09-17")
            (record,) = caplog.records
            message = record.getMessage()
            assert "slow render: Blotter total took" in message
            assert "(2 full re-pricings of the book)" in message

            caplog.clear()
            with bp.pricing_snapshot(conn):  # no label: never logs, as before
                bp.priced_value_book(conn, "2026-09-16")
            assert not caplog.records
    finally:
        conn.close()
        bp._priced_value_book_cached.cache_clear()


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
