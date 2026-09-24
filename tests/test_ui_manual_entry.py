"""Tests for ui/tabs/manual_entry.py (the Blotter's Manual entry sub-tab, 2026-09-18; the
FX swap choice, 2026-09-24). Owned by ui-manual-entry. Callbacks are exercised through
their wrapped functions, the same way tests/test_ui_options.py does it."""
from __future__ import annotations

import contextlib
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


# --------------------------------------------------------------------------- FX swap (2026-09-24)
@contextlib.contextmanager
def _registered(tmp_path):
    """The sub-tab's callbacks on a throwaway app over a fresh database: yields
    (lookup of a callback's raw function by one of its output ids, db path)."""
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
            match = next(v for k, v in app.callback_map.items() if f"{output_id}." in k)
            return getattr(match["callback"], "__wrapped__", match["callback"])

        yield wrapped, db_path
    finally:
        if original is not None:
            sys.modules["ui.app"] = original
        else:
            del sys.modules["ui.app"]


def _swap_args(n, near_date="2026-09-28", near_rate=147.10, far_date="2026-12-28", far_rate=146.40,
               amount=5_000_000, side="BUY"):
    """Positional arguments of the Book callback for an FX swap: the option and outright
    fields blank, the swap's four fields last."""
    return (n, "FX_SWAP", "usd/jpy", side, "2026-09-24", "SBILUK",
            None, None, None, None, None, None, None,
            amount, None, None,
            near_date, near_rate, far_date, far_rate)


def test_product_dropdown_offers_fx_swap_with_its_fields():
    assert {"label": "FX swap", "value": "FX_SWAP"} in manual_entry.PRODUCT_OPTIONS
    values = [o["value"] for o in manual_entry.PRODUCT_OPTIONS]
    assert values == ["FX_OPTION", "FX_FWD", "FX_SWAP"]   # the option and forward choices stay
    ids = _ids(manual_entry.booking_form())
    for needed in (manual_entry.NEAR_DATE_ID, manual_entry.NEAR_RATE_ID, manual_entry.FAR_DATE_ID,
                   manual_entry.FAR_RATE_ID, manual_entry.AMOUNT_ID, manual_entry.PAIR_ID,
                   manual_entry.SIDE_ID, manual_entry.SWAP_FIELDS_ID, manual_entry.OUTRIGHT_FIELDS_ID):
        assert needed in ids
    words = " ".join(_text(manual_entry.build_layout(schema.connect()))).upper()
    assert "IRS" not in words and "NDF" not in words and "INTEREST RATE" not in words


def test_swap_toggle_shows_amount_and_swap_fields_only(tmp_path):
    with _registered(tmp_path) as (wrapped, _):
        toggle = wrapped(manual_entry.OPTION_FIELDS_ID)
        swap_toggle = wrapped(manual_entry.SWAP_FIELDS_ID)
        # FX swap: option group hidden, the amount group shown, outright fields hidden.
        assert toggle("FX_SWAP") == ({"display": "none"}, {})
        assert swap_toggle("FX_SWAP") == ({"display": "none"}, {"display": "contents"})
        # Forward and option: the swap fields stay hidden, the outright fields as before.
        assert swap_toggle("FX_FWD") == ({"display": "contents"}, {"display": "none"})
        assert swap_toggle("FX_OPTION") == ({"display": "contents"}, {"display": "none"})


def test_booking_a_swap_writes_two_trades_with_their_package(tmp_path):
    with _registered(tmp_path) as (wrapped, db_path):
        book = wrapped(manual_entry.BOOK_STATUS_ID)
        status, listing = book(*_swap_args(1))
        assert status.className == "source-result--info"
        assert "Booked FX swap SWAP-MANUAL-1" in status.children
        assert "near date MANUAL-1" in status.children and "far date MANUAL-2" in status.children

        conn = schema.connect(str(db_path))
        rows = conn.execute("SELECT trade_id, product, package_id, quantity, price, instrument_id "
                            "FROM trades ORDER BY trade_id").fetchall()
        assert [tuple(r) for r in rows] == [
            ("MANUAL-1", "FX_SWAP", "SWAP-MANUAL-1", 5_000_000.0, 147.10, "USDJPY"),
            ("MANUAL-2", "FX_SWAP", "SWAP-MANUAL-1", -5_000_000.0, 146.40, "USDJPY")]
        conn.close()

        table = next(c for c in listing.children if isinstance(c, dash_table.DataTable))
        assert "package" in [c["id"] for c in table.columns]
        by_id = {r["trade_id"]: r for r in table.data}
        assert by_id["MANUAL-1"]["package"] == by_id["MANUAL-2"]["package"] == "SWAP-MANUAL-1"
        assert by_id["MANUAL-1"]["terms"] == "Near date" and by_id["MANUAL-2"]["terms"] == "Far date"
        assert by_id["MANUAL-1"]["settle_date"] == "2026-09-28"
        assert by_id["MANUAL-2"]["settle_date"] == "2026-12-28"
        assert by_id["MANUAL-1"]["side"] == "Buy" and by_id["MANUAL-2"]["side"] == "Sell"
        assert by_id["MANUAL-1"]["product"] == "Swap"
        pick = next(c for c in _walk(listing) if getattr(c, "id", None) == manual_entry.DELETE_PICK_ID)
        assert all("both dates go" in o["label"] for o in pick.options)


def test_swap_with_far_date_before_near_date_is_refused_in_words(tmp_path):
    with _registered(tmp_path) as (wrapped, db_path):
        book = wrapped(manual_entry.BOOK_STATUS_ID)
        status, listing = book(*_swap_args(1, near_date="2026-12-28", far_date="2026-09-28"))
        assert status.className == "source-result--error"
        assert status.children.startswith("Not booked:")
        assert "far date 2026-09-28 must be after the near date 2026-12-28" in status.children
        assert listing is dash.no_update
        # Both rates must be positive.
        status, _ = book(*_swap_args(2, far_rate=0))
        assert "Not booked" in status.children and "far rate must be greater than 0" in status.children
        status, _ = book(*_swap_args(3, near_rate=-1))
        assert "near rate must be greater than 0" in status.children
        # A blank field is named, not reported as "got None".
        status, _ = book(*_swap_args(4, near_rate=None, far_date=""))
        assert status.children == "Not booked: the near rate and far date are blank."
        status, _ = book(*_swap_args(5, amount=None))
        assert status.children == "Not booked: the amount is blank."
        conn = schema.connect(str(db_path))
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        conn.close()


def test_deleting_one_date_of_a_swap_removes_both_and_says_so(tmp_path):
    with _registered(tmp_path) as (wrapped, db_path):
        book = wrapped(manual_entry.BOOK_STATUS_ID)
        book(*_swap_args(1))
        # An outright alongside it is left alone.
        book(2, "FX_FWD", "EURUSD", "SELL", "2026-09-24", "", None, None, None, None, None, None, None,
             1_000_000, 1.17, "2026-12-15")
        delete = wrapped(manual_entry.DELETE_STATUS_ID)
        status, listing = delete(1, "MANUAL-2")
        assert status.children == "Deleted MANUAL-2 and MANUAL-1: both dates of the FX swap go together."
        conn = schema.connect(str(db_path))
        assert [r["trade_id"] for r in manual.manual_trades(conn)] == ["MANUAL-3"]
        assert conn.execute("SELECT COUNT(*) FROM trade_legs WHERE trade_id IN ('MANUAL-1','MANUAL-2')"
                            ).fetchone()[0] == 0
        conn.close()
        assert "Manual trades on file (1)" in " ".join(_text(listing))
        # A single trade still reads as before.
        status, _ = delete(2, "MANUAL-3")
        assert status.children == "Deleted MANUAL-3."


def test_forward_and_option_paths_unchanged_by_the_swap(tmp_path):
    with _registered(tmp_path) as (wrapped, db_path):
        book = wrapped(manual_entry.BOOK_STATUS_ID)
        # The callback receives the swap fields too; for a forward or an option they are ignored.
        status, _ = book(1, "FX_FWD", "USDJPY", "SELL", "2026-09-18", "", None, None, None, None, None, None,
                         None, 2_000_000, 147.25, "2026-12-15", "2026-01-01", 1.0, "2025-01-01", 2.0)
        assert status.children.startswith("Booked MANUAL-1.")
        status, _ = book(2, "FX_OPTION", "eursek", "BUY", "24/8/2026", "", "CALL", "VANILLA", 11.4, None,
                         "2026-11-25", 1_000_000, 0.02, None, None, None, "x", "y", "z", "w")
        assert status.children.startswith("Booked MANUAL-2.")
        conn = schema.connect(str(db_path))
        rows = {r[0]: r for r in conn.execute("SELECT trade_id, product, package_id, quantity FROM trades")}
        assert rows["MANUAL-1"][1:] == ("FX_FWD", "MANUAL-1", -2_000_000.0)
        assert rows["MANUAL-2"][1:] == ("FX_OPTION", "MANUAL-2", 1_000_000.0)
        legs = conn.execute("SELECT leg_type, ccy, amount FROM trade_legs WHERE trade_id = 'MANUAL-1' "
                            "ORDER BY leg_no").fetchall()
        assert [tuple(r) for r in legs] == [("FX_NEAR", "USD", -2_000_000.0), ("FX_NEAR", "JPY", 294_500_000.0)]
        records = manual_entry._list_records(manual.manual_trades(conn))
        conn.close()
        assert {r["trade_id"]: r["package"] for r in records} == {"MANUAL-1": "", "MANUAL-2": ""}
        assert {r["trade_id"]: r["product"] for r in records} == {"MANUAL-1": "Forward", "MANUAL-2": "Option"}


def _walk(component):
    if component is None or isinstance(component, (str, int, float)):
        return
    if isinstance(component, (list, tuple)):
        for c in component:
            yield from _walk(c)
        return
    yield component
    yield from _walk(getattr(component, "children", None))
