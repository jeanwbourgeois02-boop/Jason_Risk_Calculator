"""Tests for ui/tabs/manual_entry.py (the Blotter's Manual entry sub-tab, 2026-09-18).
Owned by ui-blotter. Callbacks are exercised through their wrapped functions, the same
way tests/test_ui_options.py does it."""
from __future__ import annotations

import sys
import types

import dash
from dash import dash_table, html

from data.ingest import manual, schema
from ui.tabs import blotter, manual_entry, options


def _ids(component, out=None):
    out = [] if out is None else out
    if component is None or isinstance(component, (str, int, float)):
        return out
    if isinstance(component, (list, tuple)):
        for c in component:
            _ids(c, out)
        return out
    cid = getattr(component, "id", None)
    if isinstance(cid, str):
        out.append(cid)
    _ids(getattr(component, "children", None), out)
    return out


def _text(component, out=None):
    out = [] if out is None else out
    if component is None:
        return out
    if isinstance(component, (str, int, float)):
        out.append(str(component))
        return out
    if isinstance(component, (list, tuple)):
        for c in component:
            _text(c, out)
        return out
    _text(getattr(component, "children", None), out)
    return out


def test_build_layout_has_terms_editor_form_and_empty_list():
    conn = schema.connect()
    layout = manual_entry.build_layout(conn)
    ids = _ids(layout)
    for needed in (options.TERMS_INSTRUMENT_ID, manual_entry.PRODUCT_ID, manual_entry.PAIR_ID,
                   manual_entry.STRIKE_ID, manual_entry.EXPIRY_ID, manual_entry.NOTIONAL_ID,
                   manual_entry.PREMIUM_ID, manual_entry.AMOUNT_ID, manual_entry.RATE_ID,
                   manual_entry.VALUE_DATE_ID, manual_entry.BOOK_ID, manual_entry.LIST_ID,
                   manual_entry.DELETE_ID):
        assert needed in ids
    assert "No manual trades on file." in " ".join(_text(layout))


def test_manual_list_shows_booked_trades_with_terms():
    conn = schema.connect()
    manual.book_fx_option(conn, pair="USDJPY", side="Buy", option_type="Put", payoff="DIGITAL", strike=147,
                          expiry="2027-03-01", notional=1e7, premium=0.1025, trade_date="2026-09-01")
    card = manual_entry.manual_list(conn)
    table = next(c for c in card.children if isinstance(c, dash_table.DataTable))
    assert len(table.data) == 1
    row = table.data[0]
    assert row["trade_id"] == "MANUAL-1" and row["side"] == "Buy" and row["amount"] == 10_000_000
    assert row["terms"] == "Digital Put strike 147"
    assert "Manual trades on file (1)" in " ".join(_text(card))


def test_blotter_scope_layout_renders_manual_sub_tab():
    conn = schema.connect()
    layout = blotter.scope_layout("manual", conn, "2026-09-18")
    assert manual_entry.BOOK_ID in _ids(layout)
    assert "manual" in blotter.SCOPE_ORDER and blotter.SCOPE_LABELS["manual"] == "Manual entry"
    assert "manual" in blotter._NON_TABLE_SCOPES


def test_callbacks_book_toggle_and_delete_via_wrapped_functions(tmp_path):
    db_path = tmp_path / "risk.db"
    schema.connect(str(db_path)).close()

    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    original = sys.modules.get("ui.app")
    sys.modules["ui.app"] = stub
    try:
        app = dash.Dash(__name__)
        manual_entry.register_callbacks(app, get_db_path=lambda: str(db_path))

        def wrapped(output_id):
            # Multi-output callbacks are keyed "..id.prop...id.prop.." in callback_map.
            match = next(v for k, v in app.callback_map.items() if f"{output_id}." in k)
            return getattr(match["callback"], "__wrapped__", match["callback"])

        toggle = wrapped(manual_entry.OPTION_FIELDS_ID)
        assert toggle("FX_FWD") == ({"display": "none"}, {})
        assert toggle("FX_OPTION") == ({}, {"display": "none"})

        book = wrapped(manual_entry.BOOK_STATUS_ID)
        status, listing = book(1, "FX_OPTION", "eursek", "BUY", "24/8/2026", "SBILUK",
                               "CALL", "DIGITAL", 11.4, None, "2026-11-25", 1_000_000, 0.121,
                               None, None, None)
        assert "Booked MANUAL-1" in status.children
        assert isinstance(listing, html.Div)
        # A rejected booking reports the reason and leaves the list untouched.
        status, listing = book(2, "FX_OPTION", "eursek", "BUY", "24/8/2026", "",
                               "CALL", "DIGITAL", 0, None, "2026-11-25", 1_000_000, 0.121,
                               None, None, None)
        assert "Not booked" in status.children and "strike" in status.children
        assert listing is dash.no_update
        # A forward through the same callback.
        status, _ = book(3, "FX_FWD", "USDJPY", "SELL", "2026-09-18", "",
                         None, None, None, None, None, None, None, 2_000_000, 147.25, "2026-12-15")
        assert "Booked MANUAL-2" in status.children

        delete = wrapped(manual_entry.DELETE_STATUS_ID)
        status, listing = delete(1, "MANUAL-1")
        assert status.children == "Deleted MANUAL-1."
        conn = schema.connect(str(db_path))
        assert [r["trade_id"] for r in manual.manual_trades(conn)] == ["MANUAL-2"]
        conn.close()
        status, listing = delete(2, "MANUAL-1")
        assert "Not deleted" in status.children and listing is dash.no_update
    finally:
        if original is not None:
            sys.modules["ui.app"] = original
        else:
            del sys.modules["ui.app"]
