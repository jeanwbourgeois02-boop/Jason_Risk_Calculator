"""Tests for ui/tabs/curve.py (the Exposure tab): the tab renders `engine.curve.curve_positions`'s
output and recomputes nothing. The screen was rebuilt on 2026-09-28 (the screens tidy, wave 2):
the tests pinned to the former grid, curve chart, sector and contracts tables were deleted, not
re-pinned; `tests/test_ui_smoke.py` is the screens' standing cover. What is left here is the
tab's failure wording and its empty state. The book is built in tmp_path through the real schema
and the real engine: a WTI calendar spread, a CNY copper future with no USDCNY spot, and corn.

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import sys
import types

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.contracts import get_root  # noqa: E402
from data.ingest import schema  # noqa: E402
from ui.tabs import curve  # noqa: E402

AS_OF = "2026-09-15"
CLZ6, CLF7 = "CLZ26 Comdty", "CLF27 Comdty"
CUZ6 = "CUZ26 Comdty"          # SHFE copper, CNY
CORN = "C Z26 Comdty"          # CBOT corn


# --------------------------------------------------------------------------- the book
def _future(conn, tid, instrument_id, root_id, expiry, contracts, fill):
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (instrument_id, root_id, root.currency, root.multiplier, instrument_id, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": instrument_id, "product": "FUTURE",
            "package_id": tid, "trade_date": "2026-09-01", "quantity": contracts, "price": fill,
            "account": "A", "counterparty": "C", "strategy": "", "trader": "T", "description": "d", "theme": "",
            "pb_root": "", "trade_type": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, contracts * root.multiplier * fill, "2026-09-01", expiry, fill))


def _px(conn, instrument_id, expiry, value):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')",
                 (AS_OF, instrument_id, expiry, value))


def _write_book(path, empty=False):
    conn = schema.connect(str(path))
    if not empty:
        _future(conn, "W1", CLZ6, "NYMEX:CL", "2026-11-30", 1, 70.0)
        _future(conn, "W2", CLF7, "NYMEX:CL", "2026-12-31", -1, 69.5)
        _px(conn, CLZ6, "2026-11-30", 71.0)
        _px(conn, CLF7, "2026-12-31", 70.2)
        _future(conn, "CU1", CUZ6, "SHFE:CU", "2026-12-15", 3, 80000.0)
        _px(conn, CUZ6, "2026-12-15", 80500.0)         # no USDCNY on file: no USD figure
        _future(conn, "C1", CORN, "CBOT:ZC", "2026-12-14", 2, 440.0)
        _px(conn, CORN, "2026-12-14", 450.25)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def stub_app(monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    return stub


@pytest.fixture
def book(tmp_path):
    return _write_book(tmp_path / "curve.db")


# --------------------------------------------------------------------------- tree helpers
def _walk(node):
    yield node
    children = getattr(node, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    else:
        yield from _walk(children)


def _text(node) -> str:
    parts = []
    for n in _walk(node):
        if isinstance(n, str):
            parts.append(n)
    return " ".join(parts)


# --------------------------------------------------------------------------- tests
def test_empty_book_shows_the_no_blotter_card(tmp_path, stub_app):
    """No trade on file: the Book's "No blotter loaded" card, with the Upload button under the
    Exposure tab's own pattern id (every tab body is always in the layout; user 2026-09-28)."""
    path = _write_book(tmp_path / "empty.db", empty=True)
    stub_app.active_db_path = lambda: path            # the sample-book link asks which database is active
    out = curve.render(AS_OF, path)
    text = _text(out)
    assert "No blotter loaded" in text and "No commodity position to show" not in text
    assert not [n for n in _walk(out) if getattr(n, "id", None) == curve.GRID_ID]
    assert [n.id for n in _walk(out) if isinstance(getattr(n, "id", None), dict) and n.id.get("type") == "book-empty-upload"]         == [{"type": "book-empty-upload", "idx": "curve"}]


def test_render_says_why_when_there_is_no_date_or_no_database(tmp_path, stub_app):
    assert "No as-of date" in curve.render(None, tmp_path / "x.db").children
    import sqlite3

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    stub_app.connect_readonly = refuse
    assert "Database not available" in curve.render(AS_OF, tmp_path / "missing.db").children
