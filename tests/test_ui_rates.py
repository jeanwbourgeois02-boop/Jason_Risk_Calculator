"""Tests for ui/tabs/rates.py (the Blotter "Rates" sub-tab). Owned by ui-shell.

Builds a tiny synthetic DB via data.ingest.schema with an IRS trade + QL_PRICER marks,
mirroring the shape engine/rates/store.py actually writes (one instrument per swap,
settle_date = maturity, source='QL_PRICER'), plus an optional reconciliation-only
BBG_BDH mark straight in `marks` (never `marks_official`, since BBG_BDH is not
official for PAR_RATE/PV_USD/DV01_USD any more -- data/ingest/schema.py
OFFICIAL_MARK_SOURCE).
"""
from __future__ import annotations

import contextvars
import sqlite3
import sys
import types
from pathlib import Path

import dash
import pytest

from data.ingest import irs_direction, schema
from ui.tabs import rates

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "data" / "raw" / "new_sample_trades.csv"
# The three swaps of the reference sample the desk holds as receivers (625M, 995M,
# 158.22M); nothing in the file says so, which is why the direction is set by hand.
SAMPLE_RECEIVERS = {"918421481": -625_000_000.0, "920118423": -995_000_000.0, "932385416": -158_220_000.0}


def _make_db_with_irs(pay_fixed=True, with_bbg=True, bbg_value=1_050_000.0):
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES "
        "('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')"
    )
    quantity = 10_000_000.0 if pay_fixed else -10_000_000.0
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','IRSOIS-USD-1','IRS','T1','2026-06-01',"
        f"{quantity},0.04,'ACC','CPTY','HAHY7','TR','irs swap','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',1,'FIXED','USD',-1,'2026-06-01','2031-06-01',0.04,0)"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',2,'FLOAT','USD',1,'2026-06-01','2031-06-01',0.0,0)"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','PAR_RATE',0.0398,"
        "'QL_PRICER','2026-06-20T17:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','PV_USD',1000000.0,"
        "'QL_PRICER','2026-06-20T17:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','DV01_USD',-4200.0,"
        "'QL_PRICER','2026-06-20T17:00:00-04:00')"
    )
    if with_bbg:
        conn.execute(
            "INSERT INTO marks VALUES ('2026-06-20','IRSOIS-USD-1','2031-06-01','PV_USD',"
            f"{bbg_value},'BBG_BDH','2026-06-20T17:00:00-04:00')"
        )
    conn.commit()
    return conn


def _make_db_no_irs():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    return conn


# --------------------------------------------------------------------------- irs_rows

def test_irs_rows_renders_trade_and_official_marks():
    conn = _make_db_with_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["trade_id"] == "T1"
        assert row["instrument_id"] == "IRSOIS-USD-1"
        assert row["ccy"] == "USD"
        assert row["notional"] == 10_000_000.0
        assert row["par_rate"] == 0.0398
        assert row["pv_usd"] == 1000000.0
        assert row["dv01_usd"] == -4200.0
    finally:
        conn.close()


def test_irs_rows_direction_pay_fixed_when_quantity_positive():
    conn = _make_db_with_irs(pay_fixed=True)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["direction"] == "PAY"  # irs_direction's code; the dropdown's label says "Pay fixed"
    finally:
        conn.close()


def test_irs_rows_direction_receive_fixed_when_quantity_negative():
    conn = _make_db_with_irs(pay_fixed=False)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["direction"] == "RECEIVE"
        assert df.iloc[0]["notional"] == -10_000_000.0  # signed: a short is negative
        records, _style = rates.format_rows(df)
        assert records[0]["notional"] == "(10,000,000)"  # brackets, as the book shows a short
    finally:
        conn.close()


def test_irs_rows_empty_when_no_irs_trades():
    conn = _make_db_no_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.empty
    finally:
        conn.close()


def test_irs_rows_missing_marks_does_not_crash():
    """No marks at all for the trade/date -- mark columns stay NaN, row still renders."""
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','IRSOIS-USD-1','IRS','T1','2026-06-01',"
        "10000000,0.04,'ACC','CPTY','HAHY7','TR','irs swap','')"
    )
    conn.commit()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["par_rate"] != row["par_rate"]  # NaN
        assert row["pv_usd"] != row["pv_usd"]
        assert row["recon_status"] == "MISSING"
    finally:
        conn.close()


# --------------------------------------------------------------------------- recon_status

def test_recon_status_ok_within_tolerance():
    assert rates.recon_status(1_000_000.0, 1_000_500.0) == "OK"


def test_recon_status_warn_outside_tolerance():
    assert rates.recon_status(1_000_000.0, 1_100_000.0) == "WARN"


def test_recon_status_missing_when_no_bbg_mark():
    assert rates.recon_status(1_000_000.0, None) == "MISSING"


def test_recon_status_missing_when_no_official_mark():
    import math
    assert rates.recon_status(float("nan"), 1_000_000.0) == "MISSING"
    assert rates.recon_status(math.nan, 1_000_000.0) == "MISSING"


def test_irs_rows_recon_status_end_to_end_ok():
    conn = _make_db_with_irs(with_bbg=True, bbg_value=1_000_500.0)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["recon_status"] == "OK"
    finally:
        conn.close()


def test_irs_rows_recon_status_end_to_end_warn():
    conn = _make_db_with_irs(with_bbg=True, bbg_value=1_200_000.0)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["recon_status"] == "WARN"
    finally:
        conn.close()


def test_irs_rows_recon_status_end_to_end_missing():
    conn = _make_db_with_irs(with_bbg=False)
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        assert df.iloc[0]["recon_status"] == "MISSING"
    finally:
        conn.close()


# --------------------------------------------------------------------------- formatting / table

def test_format_rows_formats_rate_and_usd_columns():
    conn = _make_db_with_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        records, _style = rates.format_rows(df)
        row = records[0]
        assert row["par_rate"] == "3.9800%"
        assert row["pv_usd"] == "1,000,000"
        assert row["notional"] == "10,000,000"
    finally:
        conn.close()


def test_format_rows_missing_marks_show_na():
    conn = _make_db_with_irs(with_bbg=False)
    try:
        conn.execute("DELETE FROM marks")
        df = rates.irs_rows(conn, "2026-06-20")
        records, _style = rates.format_rows(df)
        assert records[0]["par_rate"] == "n/a"
        assert records[0]["pv_usd"] == "n/a"
        assert records[0]["dv01_usd"] == "n/a"
    finally:
        conn.close()


def test_rates_table_has_recon_status_column_and_conditional_styling():
    conn = _make_db_with_irs()
    try:
        df = rates.irs_rows(conn, "2026-06-20")
        table = rates.rates_table(df)
        ids = {c["id"] for c in table.columns}
        assert "recon_status" in ids
        assert "direction" in ids
        colours = {s["color"] for s in table.style_data_conditional if "color" in s}
        assert "var(--pos)" in colours
        assert "var(--neg)" in colours
    finally:
        conn.close()


def test_rates_table_empty_frame_still_renders_columns():
    import pandas as pd
    empty = pd.DataFrame(columns=rates._DISPLAY_COLUMNS)
    table = rates.rates_table(empty)
    assert table.data == []
    assert len(table.columns) == len(rates._DISPLAY_COLUMNS)


# --------------------------------------------------------------------------- build_layout

def test_build_layout_renders_trade_rows():
    conn = _make_db_with_irs()
    try:
        layout = rates.build_layout(conn, "2026-06-20")
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert len(table.data) == 1
        assert table.data[0]["trade_id"] == "T1"
    finally:
        conn.close()


def test_build_layout_no_irs_trades_shows_message_and_empty_table():
    conn = _make_db_no_irs()
    try:
        layout = rates.build_layout(conn, "2026-06-20")
        text = layout.children[0].children
        assert "No IRS trades on file" in text
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.data == []
    finally:
        conn.close()


def test_build_layout_missing_mark_does_not_crash():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO instruments VALUES ('IRSOIS-USD-1','IRS','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('T1','XLSX','IRSOIS-USD-1','IRS','T1','2026-06-01',"
        "10000000,0.04,'ACC','CPTY','HAHY7','TR','irs swap','')"
    )
    conn.commit()
    try:
        layout = rates.build_layout(conn, "2026-06-20")
        table = next(c for c in layout.children if isinstance(c, dash.dash_table.DataTable))
        assert table.data[0]["par_rate"] == "n/a"
    finally:
        conn.close()


# =========================================================================== pay / receive
# Set by hand in the table (2026-09-18): the export carries no pay/receive marker, so
# `data/ingest/irs_direction.py` stores the user's choice and this tab is its front end.

AS_OF = "2026-06-20"
SWAPS = (("T1", 10_000_000.0), ("T2", 25_000_000.0), ("T3", 5_000_000.0))


@pytest.fixture
def ui_app_stub(monkeypatch):
    """`ui.app.connect_readonly` without importing the whole app (every other tab's
    module, several of them other agents' lanes): a genuinely read-only handle, as in the
    app, so a test would fail if rendering ever tried to write."""
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    return stub


@pytest.fixture(autouse=True)
def _no_leftover_message():
    rates._LAST_MESSAGE.clear()
    yield
    rates._LAST_MESSAGE.clear()


def _file_db(tmp_path, swaps=SWAPS, pv=1_000_000.0):
    """A database FILE (the callbacks open their own connections): pay-fixed swaps as the
    blotter loads them (FIXED = -quantity, FLOAT = +quantity), each with official
    QL_PRICER marks, plus one FX forward that is NOT a swap."""
    db_path = tmp_path / "risk.db"
    conn = schema.connect(str(db_path))
    for n, (trade_id, quantity) in enumerate(swaps, start=1):
        inst = f"IRSOIS-USD-{n}"
        conn.execute("INSERT INTO instruments VALUES (?,'IRS','USD','USD',1,0,'','9999-12-31')", (inst,))
        conn.execute("INSERT INTO trades VALUES (?,'XLSX',?,'IRS',?,'2026-06-01',?,0.04,'ACC','CPTY','','TR','irs','')",
                     (trade_id, inst, trade_id, quantity))
        conn.execute("INSERT INTO trade_legs VALUES (?,1,'FIXED','USD',?,'2026-06-01','2031-06-01',0.04,0)",
                     (trade_id, -quantity))
        conn.execute("INSERT INTO trade_legs VALUES (?,2,'FLOAT','USD',?,'2026-06-01','2031-06-01',0.0,0)",
                     (trade_id, quantity))
        for mark_type, value in (("PAR_RATE", 0.0398), ("PV_USD", pv * n), ("DV01_USD", -4200.0 * n),
                                 ("CASHFLOW_USD", 0.0)):
            conn.execute("INSERT INTO marks VALUES (?,?,'2031-06-01',?,?,'QL_PRICER','2026-06-20T17:00:00-04:00')",
                         (AS_OF, inst, mark_type, value))
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('FX1','XLSX','EURUSD','FX_FWD','FX1','2026-06-01',1000000,1.10,"
                 "'ACC','CPTY','','TR','fx fwd','')")
    conn.commit()
    conn.close()
    return db_path


def _state(db_path, trade_id):
    """(quantity, {leg_type: amount}, stored override or None) for one trade."""
    conn = sqlite3.connect(str(db_path))
    try:
        quantity = conn.execute("SELECT quantity FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()[0]
        legs = dict(conn.execute("SELECT leg_type, amount FROM trade_legs WHERE trade_id = ?", (trade_id,)))
        return quantity, legs, irs_direction.get_overrides(conn).get(trade_id)
    finally:
        conn.close()


def _rows(db_path, as_of=AS_OF):
    """(`irs_rows` frame, the table's records) through a read-only handle, as the app does."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        df = rates.irs_rows(conn, as_of)
        return df, rates.format_rows(df)[0]
    finally:
        conn.close()


def _edited(records, trade_id, direction):
    """The table's `data` after the user picked `direction` in one row's dropdown."""
    return [{**r, "direction": direction} if r["trade_id"] == trade_id else dict(r) for r in records]


def _callback(app, fragment):
    matches = [v for k, v in app.callback_map.items() if fragment in k]
    assert len(matches) == 1, (fragment, list(app.callback_map))
    return matches[0], getattr(matches[0]["callback"], "__wrapped__", matches[0]["callback"])


def _with_trigger(prop_id, fn, *args):
    """Call a wrapped callback the way Dash would for one triggering prop (`dash.ctx`)."""
    from dash._callback_context import context_value
    from dash._utils import AttributeDict

    def _run():
        context_value.set(AttributeDict(triggered_inputs=[{"prop_id": prop_id, "value": None}]))
        return fn(*args)
    return contextvars.copy_context().run(_run)


class _Feed:
    def __init__(self):
        self.woken = 0

    def trigger_now(self):
        self.woken += 1


def _app(db_path, feed=None):
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    app.bloomberg_feed = feed
    rates.register_callbacks(app, get_db_path=lambda: str(db_path))
    _spec, on_direction = _callback(app, f"{rates.STATUS_ID}.children")
    return app, on_direction


def _ids(component) -> set:
    found, stack = set(), [component]
    while stack:
        node = stack.pop()
        if isinstance(getattr(node, "id", None), str):
            found.add(node.id)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend(children)
        elif children is not None and not isinstance(children, str):
            stack.append(children)
    return found


def _by_id(component, wanted):
    stack = [component]
    while stack:
        node = stack.pop()
        if getattr(node, "id", None) == wanted:
            return node
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend(children)
        elif children is not None and not isinstance(children, str):
            stack.append(children)
    raise AssertionError(f"{wanted} not in layout")


# --------------------------------------------------------------------------- the table
def test_direction_is_a_dropdown_cell_and_the_only_editable_column():
    conn = _make_db_with_irs()
    try:
        table = rates.rates_table(rates.irs_rows(conn, AS_OF))
    finally:
        conn.close()
    assert table.editable is False
    by_id = {c["id"]: c for c in table.columns}
    assert by_id["direction"]["presentation"] == "dropdown" and by_id["direction"]["editable"] is True
    assert [c for c in table.columns if c.get("editable")] == [by_id["direction"]]
    choices = table.dropdown["direction"]
    assert choices["clearable"] is False
    assert choices["options"] == [{"label": "Pay fixed", "value": "PAY"},
                                  {"label": "Receive fixed", "value": "RECEIVE"}]
    # the cell holds the dropdown's VALUE, which is exactly what set_direction takes
    assert table.data[0]["direction"] in irs_direction.DIRECTIONS


def test_column_order_keeps_every_existing_column_in_place_with_set_by_after_direction():
    ids = [c["id"] for c in rates.table_columns()]
    assert ids == ["trade_id", "instrument_id", "ccy", "direction", "set_by", "notional", "entry_rate", "par_rate",
                   "pv_usd", "dv01_usd", "cashflow_usd", "pnl_usd", "recon_status"]
    assert {c["id"]: c["name"] for c in rates.table_columns()}["set_by"] == "Set by"


def test_set_by_says_you_file_or_not_set(tmp_path):
    db_path = _file_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        irs_direction.set_direction(conn, "T1", "RECEIVE")                 # the user's choice
        conn.execute("UPDATE trades SET quantity = -quantity WHERE trade_id = 'T2'")  # a short marker in the file
        conn.commit()
        df = rates.irs_rows(conn, AS_OF).set_index("trade_id")
    finally:
        conn.close()
    assert df.loc["T1", "set_by"] == "You" and df.loc["T1", "direction"] == "RECEIVE"
    assert df.loc["T2", "set_by"] == "File" and df.loc["T2", "direction"] == "RECEIVE"
    assert df.loc["T3", "set_by"] == "Not set: assumed pay fixed" and df.loc["T3", "direction"] == "PAY"
    assert list(df["needs_user_choice"]) == [False, False, True]


def test_unusable_quantity_shows_no_direction_rather_than_an_invented_one(tmp_path):
    db_path = _file_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE trades SET quantity = 0 WHERE trade_id = 'T1'")
        conn.commit()
        row = rates.irs_rows(conn, AS_OF).set_index("trade_id").loc["T1"]
    finally:
        conn.close()
    assert row["direction"] == ""
    assert row["set_by"] == "Not set"  # nothing is "assumed pay fixed" about it
    assert rates.direction_code(float("nan")) == "" and rates.direction_code("5") == ""
    assert rates.direction_code(3.0) == "PAY" and rates.direction_code(-3) == "RECEIVE"


def test_flagged_rows_are_styled_with_existing_css_variables_only():
    styles = rates.table_styles()
    flagged = [s for s in styles if s["if"].get("filter_query") == rates._FLAGGED_QUERY]
    assert {"backgroundColor": "var(--warn-bg)"}.items() <= flagged[0].items() and "column_id" not in flagged[0]["if"]
    # a box-shadow, not a border: style.css forces every table cell's border colour
    assert any(s.get("boxShadow", "").endswith("var(--gold)") and s["if"].get("column_id") == "trade_id"
               for s in flagged)
    assert not any("border" in key.lower() for s in styles for key in s if key != "if")
    # the query keys on the displayed wording, for both "not set" cases and neither of the others
    for words in (rates.SET_BY_NOT_SET, rates.SET_BY_UNKNOWN):
        assert "Not set" in words
    assert all("Not set" not in rates.SET_BY_LABELS[s] for s in ("USER", "FILE"))
    # the reconciliation colours are still there, after the row tint (later rules win)
    colours = [s.get("color") for s in styles]
    assert "var(--pos)" in colours and "var(--neg)" in colours


# --------------------------------------------------------------------------- the notice
def test_notice_text_and_count():
    assert rates.notice_text(7, 10) == (
        "7 of 10 swaps have no pay/receive in the file and are assumed PAY FIXED. "
        "Set Receive fixed on the ones you receive on; their P&L and DV01 change sign.")
    assert rates.notice_text(1, 10).startswith("1 of 10 swaps has no pay/receive in the file and is assumed PAY FIXED.")
    assert rates.notice_text(0, 10) == ""
    assert rates.notice_style(0) == {"display": "none"}
    assert rates.notice_style(3)["display"] == "flex"


def test_build_layout_shows_the_notice_with_its_button_while_swaps_are_flagged(tmp_path):
    db_path = _file_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        layout = rates.build_layout(conn, AS_OF)
    finally:
        conn.close()
    assert _by_id(layout, rates.NOTICE_ID).style["display"] == "flex"
    assert _by_id(layout, rates.NOTICE_TEXT_ID).children.startswith("3 of 3 swaps have no pay/receive")
    assert _by_id(layout, rates.CONFIRM_REST_ID).children == "The rest are pay fixed: confirm"
    assert {r["set_by"] for r in _by_id(layout, rates.DATATABLE_ID).data} == {"Not set: assumed pay fixed"}


def test_build_layout_hides_the_notice_when_nothing_is_flagged_but_keeps_every_id(tmp_path):
    db_path = _file_db(tmp_path)
    conn, no_swaps = sqlite3.connect(str(db_path)), _make_db_no_irs()
    try:
        for trade_id, _q in SWAPS:
            irs_direction.set_direction(conn, trade_id, "PAY")
        layout = rates.build_layout(conn, AS_OF)
        empty_layout = rates.build_layout(no_swaps, AS_OF)
    finally:
        conn.close()
        no_swaps.close()
    for built in (layout, empty_layout):
        assert _by_id(built, rates.NOTICE_ID).style == {"display": "none"}
        assert _by_id(built, rates.NOTICE_TEXT_ID).children == ""
        assert {rates.DATATABLE_ID, rates.NOTICE_ID, rates.NOTICE_TEXT_ID, rates.CONFIRM_REST_ID,
                rates.STATUS_ID} <= _ids(built)


def test_every_callback_id_is_rendered_by_build_layout_or_always_on_the_page(tmp_path):
    """Dash's renderer silently drops a callback any of whose Outputs is missing, so the
    notice, button and status line must exist whenever the table does."""
    db_path = _file_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        rendered = _ids(rates.build_layout(conn, AS_OF))
    finally:
        conn.close()
    always_on_page = {rates.DATA_REVISION_ID, rates.BOOK_REVISION_ID, rates.DEFAULT_DATE_PICKER_ID}
    app, _fn = _app(db_path)
    assert len(app.callback_map) == 2
    for key, spec in app.callback_map.items():
        used = {d["id"] for kind in ("inputs", "state") for d in spec.get(kind, [])}
        used |= {part.split(".")[0] for part in key.strip(".").split("...") if part}
        assert used <= rendered | always_on_page, used - rendered - always_on_page


# --------------------------------------------------------------------------- the callback
def test_flip_through_the_callback_changes_direction_quantity_sign_and_shown_pv(tmp_path, ui_app_stub):
    from dash import no_update
    from ui import revision

    db_path = _file_db(tmp_path)
    feed = _Feed()
    _app_obj, on_direction = _app(db_path, feed)
    before_df, before = _rows(db_path)
    assert before_df.set_index("trade_id").loc["T1", "pv_usd"] == 1_000_000.0
    data_rev, book_rev = revision.file_signature(db_path), revision.book_signature(db_path)

    out = _with_trigger(f"{rates.DATATABLE_ID}.data_timestamp", on_direction,
                        1, 0, _edited(before, "T1", "RECEIVE"), before, AS_OF)
    records, notice_style, notice_text, status, new_data_rev, new_book_rev = out

    df = _rows(db_path)[0].set_index("trade_id")
    assert df.loc["T1", "direction"] == "RECEIVE" and df.loc["T1", "set_by"] == "You"
    assert df.loc["T1", "quantity"] == -10_000_000.0
    assert _state(db_path, "T1") == (-10_000_000.0, {"FIXED": 10_000_000.0, "FLOAT": -10_000_000.0}, "RECEIVE")
    # the data layer reverses the swap's own marks in place: PV / DV01 / P&L show the other sign at once
    assert df.loc["T1", "pv_usd"] == -1_000_000.0 and df.loc["T1", "dv01_usd"] == 4200.0
    assert df.loc["T1", "pnl_usd"] == -1_000_000.0 and df.loc["T1", "par_rate"] == 0.0398
    assert df.loc["T2", "quantity"] == 25_000_000.0 and df.loc["T2", "pv_usd"] == 2_000_000.0  # untouched

    row = next(r for r in records if r["trade_id"] == "T1")
    assert row["direction"] == "RECEIVE" and row["notional"] == "(10,000,000)" and row["pv_usd"] == "(1,000,000)"
    assert notice_style["display"] == "flex" and notice_text.startswith("2 of 3 swaps have")
    assert "T1 set to Receive fixed" in status.children and status.className == "source-result--info"
    assert feed.woken == 0      # 2026-09-21: Bloomberg is pulled on request only, never by an edit
    # both revisions published at once, so the header and the strips redraw with no reload
    assert new_data_rev == revision.file_signature(db_path) and new_data_rev != data_rev
    assert new_book_rev == revision.book_signature(db_path) and new_book_rev != book_rev
    assert no_update not in (records, new_data_rev, new_book_rev)


def test_two_flips_leave_the_swap_exactly_as_it_started(tmp_path, ui_app_stub):
    db_path = _file_db(tmp_path)
    _app_obj, on_direction = _app(db_path)
    start_quantity, start_legs, start_override = _state(db_path, "T2")
    start_df, start = _rows(db_path)
    assert start_override is None

    once = _with_trigger(f"{rates.DATATABLE_ID}.data_timestamp", on_direction,
                         1, 0, _edited(start, "T2", "RECEIVE"), start, AS_OF)[0]
    assert _state(db_path, "T2")[0] == -start_quantity
    twice = _with_trigger(f"{rates.DATATABLE_ID}.data_timestamp", on_direction,
                          2, 0, _edited(once, "T2", "PAY"), once, AS_OF)[0]

    quantity, legs, override = _state(db_path, "T2")
    assert quantity == start_quantity == 25_000_000.0
    assert legs == start_legs == {"FIXED": -25_000_000.0, "FLOAT": 25_000_000.0}
    assert override == "PAY"  # the one thing that differs: the choice is now the user's
    end_df = _rows(db_path)[0]
    marks = ["par_rate", "pv_usd", "dv01_usd", "cashflow_usd", "pnl_usd"]
    assert end_df[marks].equals(start_df[marks])
    same_but_set_by = [{**r, "set_by": "Not set: assumed pay fixed"} if r["trade_id"] == "T2" else r for r in twice]
    assert same_but_set_by == start and next(r for r in twice if r["trade_id"] == "T2")["set_by"] == "You"


def test_bad_trade_id_gives_a_message_not_an_exception_and_the_cell_reverts(tmp_path, ui_app_stub):
    from dash import no_update

    db_path = _file_db(tmp_path)
    feed = _Feed()
    _app_obj, on_direction = _app(db_path, feed)
    _df, good = _rows(db_path)
    ghost = {**good[0], "trade_id": "NOPE", "direction": "PAY"}
    previous = good + [ghost]
    rows = good + [{**ghost, "direction": "RECEIVE"}]

    records, _style, _text, status, data_rev, book_rev = _with_trigger(
        f"{rates.DATATABLE_ID}.data_timestamp", on_direction, 1, 0, rows, previous, AS_OF)

    assert "no trade 'NOPE' on file" in status.children and status.className == "source-result--error"
    assert records == good  # rebuilt from the database: the refused row is gone, nothing else moved
    assert data_rev is no_update and book_rev is no_update and feed.woken == 0
    assert all(_state(db_path, t)[2] is None for t, _q in SWAPS)

    # a trade that exists but is not a swap, and a value that is not a direction
    result = rates.apply_directions(db_path, [("FX1", "RECEIVE"), ("T1", "SIDEWAYS"), ("T3", "receive")])
    assert not result.ok and result.written == 1 and result.flipped == 1
    assert "FX1 is a FX_FWD, not an interest rate swap" in result.message
    assert "direction must be PAY or RECEIVE" in result.message and "T3 set to Receive fixed" in result.message
    assert _state(db_path, "T1")[0] == 10_000_000.0 and _state(db_path, "T3")[0] == -5_000_000.0


def test_a_cleared_cell_is_refused_and_reverted(tmp_path, ui_app_stub):
    db_path = _file_db(tmp_path)
    _app_obj, on_direction = _app(db_path)
    _df, before = _rows(db_path)
    records, _style, _text, status, _d, _b = _with_trigger(
        f"{rates.DATATABLE_ID}.data_timestamp", on_direction, 1, 0, _edited(before, "T1", None), before, AS_OF)
    assert status.className == "source-result--error" and "direction must be PAY or RECEIVE" in status.children
    assert records == before and _state(db_path, "T1") == (10_000_000.0, {"FIXED": -10_000_000.0,
                                                                          "FLOAT": 10_000_000.0}, None)


def test_confirm_the_rest_clears_every_flag_and_changes_no_trade(tmp_path, ui_app_stub):
    from ui import revision

    db_path = _file_db(tmp_path)
    feed = _Feed()
    _app_obj, on_direction = _app(db_path, feed)
    conn = sqlite3.connect(str(db_path))
    try:
        irs_direction.set_direction(conn, "T1", "RECEIVE")  # the one the user receives on
    finally:
        conn.close()
    _df, shown = _rows(db_path)
    data_rev = revision.file_signature(db_path)

    records, notice_style, notice_text, status, new_data_rev, _book = _with_trigger(
        f"{rates.CONFIRM_REST_ID}.n_clicks", on_direction, None, 1, shown, None, AS_OF)

    conn = sqlite3.connect(str(db_path))
    try:
        assert irs_direction.needs_user_choice(conn) == []
        assert irs_direction.get_overrides(conn) == {"T1": "RECEIVE", "T2": "PAY", "T3": "PAY"}
    finally:
        conn.close()
    assert notice_style == {"display": "none"} and notice_text == ""
    assert {r["set_by"] for r in records} == {"You"}
    assert [r["direction"] for r in records] == ["RECEIVE", "PAY", "PAY"]
    assert status.children == "2 swaps confirmed as pay fixed."
    assert _state(db_path, "T2")[:2] == (25_000_000.0, {"FIXED": -25_000_000.0, "FLOAT": 25_000_000.0})
    assert new_data_rev == revision.file_signature(db_path) and new_data_rev != data_rev
    assert feed.woken == 0  # nothing turned round, so there is nothing to re-price

    # a second click has nothing left to do, and says so
    again = _with_trigger(f"{rates.CONFIRM_REST_ID}.n_clicks", on_direction, None, 2, records, None, AS_OF)
    assert again[3].children.startswith("Nothing left to confirm")


def test_a_call_with_no_click_and_no_edited_cell_changes_nothing(tmp_path, ui_app_stub):
    """The sub-tab is rendered by the Blotter after page load, so Dash can call this with
    nothing triggered; a stale n_clicks must not confirm anything on a cell edit either."""
    from dash import no_update

    db_path = _file_db(tmp_path)
    _app_obj, on_direction = _app(db_path)
    _df, shown = _rows(db_path)
    assert on_direction(None, 0, shown, None, AS_OF) == (no_update,) * 6
    assert _with_trigger(".", on_direction, None, 0, shown, None, AS_OF) == (no_update,) * 6
    # n_clicks left at 1 by an earlier click, the trigger is the table, and no cell differs
    assert _with_trigger(f"{rates.DATATABLE_ID}.data_timestamp", on_direction,
                         5, 1, shown, shown, AS_OF) == (no_update,) * 6
    assert all(_state(db_path, t)[2] is None for t, _q in SWAPS)


def test_only_the_cell_the_user_touched_is_applied_even_if_the_table_is_stale(tmp_path, ui_app_stub):
    db_path = _file_db(tmp_path)
    _df, shown = _rows(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        irs_direction.set_direction(conn, "T2", "RECEIVE")  # set elsewhere; this table still shows PAY
    finally:
        conn.close()
    assert rates.detect_direction_edits(_edited(shown, "T3", "RECEIVE"), shown) == [("T3", "RECEIVE")]
    event = rates.handle_direction_event(db_path, AS_OF, _edited(shown, "T3", "RECEIVE"), shown, False)
    assert event.result.ok and event.result.written == 1
    assert _state(db_path, "T2")[0] == -25_000_000.0  # not flipped back by the stale PAY in the table
    assert _state(db_path, "T3")[0] == -5_000_000.0
    assert rates.detect_direction_edits(shown, None) == [] and rates.detect_direction_edits(None, shown) == []


def test_revision_refresh_updates_rows_in_place_and_leaves_an_unchanged_table_alone(tmp_path, ui_app_stub):
    from dash import no_update

    db_path = _file_db(tmp_path)
    app, _on_direction = _app(db_path)
    _spec, refresh = _callback(app, f"{rates.NOTICE_TEXT_ID}.children@")
    _df, shown = _rows(db_path)

    rows, style, text = refresh("rev-1", "book-1", shown, AS_OF)
    assert rows is no_update  # nothing on screen changed: an open dropdown is not disturbed
    assert style["display"] == "flex" and text.startswith("3 of 3 swaps")

    conn = sqlite3.connect(str(db_path))
    try:  # new marks land, and a direction is set from elsewhere
        conn.execute("UPDATE marks SET value = 1234567.0 WHERE instrument_id = 'IRSOIS-USD-3' AND mark_type = 'PV_USD'")
        conn.commit()
        irs_direction.set_direction(conn, "T1", "RECEIVE")
    finally:
        conn.close()
    rows, style, text = refresh("rev-2", "book-2", shown, AS_OF)
    by_id = {r["trade_id"]: r for r in rows}
    assert by_id["T3"]["pv_usd"] == "1,234,567" and by_id["T1"]["direction"] == "RECEIVE"
    assert text.startswith("2 of 3 swaps")
    assert refresh("rev-3", "book-3", shown, None) == (no_update,) * 3  # no as-of date: nothing to render


def test_status_message_survives_the_blotters_rebuild_of_the_sub_tab(tmp_path, ui_app_stub):
    db_path = _file_db(tmp_path)
    _df, shown = _rows(db_path)
    rates.handle_direction_event(db_path, AS_OF, _edited(shown, "T1", "RECEIVE"), shown, False)
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        status = _by_id(rates.build_layout(conn, AS_OF), rates.STATUS_ID)
        assert "T1 set to Receive fixed" in status.children.children
        rates._LAST_MESSAGE.clear()
        assert _by_id(rates.build_layout(conn, AS_OF), rates.STATUS_ID).children is None
    finally:
        conn.close()


# --------------------------------------------------------------------------- the real sample
@pytest.mark.skipif(not SAMPLE.exists(), reason="data/raw/new_sample_trades.csv is not on this machine")
def test_sample_receivers_are_set_survive_a_reimport_and_the_rest_confirm(tmp_path, ui_app_stub):
    from data.ingest.upload import import_blotter

    db_path = tmp_path / "sample.db"
    payload = SAMPLE.read_bytes()
    import_blotter(payload, SAMPLE.name, db_path)

    df, shown = _rows(db_path, "2026-09-18")
    assert len(df) == 10 and set(df["direction"]) == {"PAY"} and df["needs_user_choice"].all()
    assert set(SAMPLE_RECEIVERS) <= set(df["trade_id"])

    for trade_id in SAMPLE_RECEIVERS:  # one dropdown change at a time, as the user makes them
        event = rates.handle_direction_event(db_path, "2026-09-18", _edited(shown, trade_id, "RECEIVE"), shown, False)
        assert event.result.ok, event.result.message
        shown = event.records
    assert rates.notice_text(event.flagged, event.total) == (
        "7 of 10 swaps have no pay/receive in the file and are assumed PAY FIXED. "
        "Set Receive fixed on the ones you receive on; their P&L and DV01 change sign.")

    import_blotter(payload, SAMPLE.name, db_path)  # the same file again: the book is rewritten from it

    df = _rows(db_path, "2026-09-18")[0].set_index("trade_id")
    for trade_id, quantity in SAMPLE_RECEIVERS.items():
        assert df.loc[trade_id, "direction"] == "RECEIVE" and df.loc[trade_id, "set_by"] == "You"
        assert df.loc[trade_id, "quantity"] == quantity
        assert _state(db_path, trade_id) == (quantity, {"FIXED": -quantity, "FLOAT": quantity}, "RECEIVE")
    rest = df[~df.index.isin(list(SAMPLE_RECEIVERS))]
    assert len(rest) == 7 and set(rest["direction"]) == {"PAY"} and rest["needs_user_choice"].all()
    assert (rest["quantity"] > 0).all()

    event = rates.handle_direction_event(db_path, "2026-09-18", None, None, True)  # "The rest are pay fixed: confirm"
    assert event.result.ok and event.result.written == 7 and event.flagged == 0
    assert rates.notice_text(event.flagged, event.total) == "" and {r["set_by"] for r in event.records} == {"You"}
    df = _rows(db_path, "2026-09-18")[0]
    assert sorted(df.loc[df["direction"] == "RECEIVE", "trade_id"]) == sorted(SAMPLE_RECEIVERS)
    assert not df["needs_user_choice"].any()
