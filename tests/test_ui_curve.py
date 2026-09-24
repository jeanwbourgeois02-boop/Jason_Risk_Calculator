"""Tests for ui/tabs/curve.py: the Curve tab renders `engine.curve.curve_positions`'s output
and recomputes nothing. The book is built in tmp_path through the real schema and the real
engine: a WTI calendar spread, a CNY copper future with no USDCNY spot, and corn.

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import sys
import types

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.contracts import get_root  # noqa: E402
from data.ingest import schema  # noqa: E402
from engine.curve import curve_positions  # noqa: E402
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
            "account": "A", "counterparty": "C", "strategy": "", "trader": "T", "description": "d", "theme": ""}
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


def _engine(path):
    conn = schema.connect(str(path))
    try:
        return curve_positions(conn, AS_OF)
    finally:
        conn.close()


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


def _table(node, table_id):
    hits = [n for n in _walk(node) if getattr(n, "id", None) == table_id]
    assert hits, f"no {table_id}"
    return hits[0]


def _grid_row(table, commodity_root, result):
    name = result["by_commodity"][commodity_root]["name"]
    rows = [(i, r) for i, r in enumerate(table.data) if r["commodity"] == name]
    assert len(rows) == 1, table.data
    return rows[0]


# --------------------------------------------------------------------------- tests
def test_render_shows_the_three_commodities_grouped_by_sector(book, stub_app):
    result = _engine(book)
    body = curve.render(AS_OF, book)
    grid = _table(body, curve.GRID_ID)
    assert [r["root_id"] for r in grid.data] == list(result["by_commodity"])
    assert [r["sector"] for r in grid.data] == ["Agriculture", "Energy", "Metals"]
    text = _text(body)
    assert "As of Tuesday 15 September 2026 (2026-09-15)." in text


def test_grid_has_the_months_as_columns_and_the_offsetting_lots(book, stub_app):
    result = _engine(book)
    grid = _table(curve.render(AS_OF, book), curve.GRID_ID)
    ids = [c["id"] for c in grid.columns]
    assert result["months"] == ["2026-12", "2027-01"]
    assert ids[4:6] == ["m_2026-12", "m_2027-01"]
    assert [c["name"] for c in grid.columns[4:6]] == ["Dec 26", "Jan 27"]
    assert all(c["type"] == "numeric" for c in grid.columns[4:6])
    assert grid.sort_action == "native"
    _, cl = _grid_row(grid, "NYMEX:CL", result)
    assert cl["m_2026-12"] == 1.0 and cl["m_2027-01"] == -1.0
    assert cl["net_lots"] == 0.0 and cl["gross_lots"] == 2.0 and cl["net_units"] == 0.0 and cl["unit"] == "bbl"
    assert cl["net_usd"] == pytest.approx(result["by_commodity"]["NYMEX:CL"]["net_usd"]) == pytest.approx(800.0)
    assert "m_2027-01" not in (_grid_row(grid, "CBOT:ZC", result)[1])   # no corn in Jan 27: blank, not zero


def test_na_usd_cell_carries_the_reason(book, stub_app):
    result = _engine(book)
    grid = _table(curve.render(AS_OF, book, "usd"), curve.GRID_ID)
    i, cu = _grid_row(grid, "SHFE:CU", result)
    assert cu["m_2026-12"] == "n/a" and cu["net_usd"] == "n/a" and cu["gross_usd"] == "n/a"
    assert "no SPOT for CNY" in grid.tooltip_data[i]["m_2026-12"]["value"]
    assert "no SPOT for CNY" in grid.tooltip_data[i]["net_usd"]["value"]
    assert cu["net_lots"] == 3.0 and cu["net_units"] == 15.0          # lots and units still shown
    sectors = _table(curve.render(AS_OF, book), curve.SECTOR_TABLE_ID)
    metals = next((j, r) for j, r in enumerate(sectors.data) if r["sector"] == "Metals")
    assert metals[1]["net_usd"] == "n/a" and "no SPOT for CNY" in sectors.tooltip_data[metals[0]]["net_usd"]["value"]
    footer = _table(curve.render(AS_OF, book), f"{curve.SECTOR_TABLE_ID}-footer")
    assert footer.data[0]["sector"] == "Book" and footer.data[0]["net_usd"] == "n/a"
    assert "no SPOT for CNY" in footer.tooltip_data[0]["net_usd"]["value"]


def test_unit_switch_changes_the_cells(book, stub_app):
    result = _engine(book)
    rows = {r["contract_id"]: r for r in result["rows"]}
    grids = {u: _table(curve.render(AS_OF, book, u), curve.GRID_ID) for u in curve.UNITS}
    cl = {u: _grid_row(g, "NYMEX:CL", result)[1] for u, g in grids.items()}
    assert (cl["lots"]["m_2026-12"], cl["lots"]["m_2027-01"]) == (1.0, -1.0)
    assert (cl["units"]["m_2026-12"], cl["units"]["m_2027-01"]) == (rows[CLZ6]["units"], rows[CLF7]["units"]) == (1000.0, -1000.0)
    assert cl["usd"]["m_2026-12"] == pytest.approx(rows[CLZ6]["notional_usd"]) == pytest.approx(71000.0)
    assert cl["usd"]["m_2027-01"] == pytest.approx(rows[CLF7]["notional_usd"]) == pytest.approx(-70200.0)
    corn = _grid_row(grids["units"], "CBOT:ZC", result)[1]
    assert corn["m_2026-12"] == 10000.0 and corn["unit"] == "bu"
    for g in grids.values():                          # the end columns are the engine's, whatever the switch
        assert _grid_row(g, "NYMEX:CL", result)[1]["net_usd"] == pytest.approx(800.0)
    assert "net lots" in _text(curve.grid_section(result, "lots"))
    assert "USD notional" in _text(curve.grid_section(result, "usd"))


def test_estimated_expiry_is_marked(book, stub_app):
    result = _engine(book)
    detail = _table(curve.render(AS_OF, book), curve.DETAIL_TABLE_ID)
    by_id = {r["contract_id"]: (i, r) for i, r in enumerate(detail.data)}
    i, z = by_id[CLZ6]
    assert result["rows"][[r["contract_id"] for r in result["rows"]].index(CLZ6)]["dates_source"] == "ESTIMATED"
    assert z["expiry"] == "2026-11-30 (est.)" and "estimated" in detail.tooltip_data[i]["expiry"]["value"]
    assert z["first_notice"] == "n/a" and "first notice" in detail.tooltip_data[i]["first_notice"]["value"]
    assert z["lots"] == 1.0 and z["price"] == 71.0 and z["notional_usd"] == pytest.approx(71000.0)
    j, cu = by_id[CUZ6]
    assert cu["currency"] == "CNY" and cu["notional_local"] == pytest.approx(3 * 5 * 80500.0)
    assert cu["notional_usd"] == "n/a" and "no SPOT for CNY" in detail.tooltip_data[j]["notional_usd"]["value"]
    # a Bloomberg-dated row is not marked
    row = dict(result["rows"][0], dates_source="BLOOMBERG", first_notice="2026-11-27")
    recs, _ = curve.detail_records(dict(result, rows=[row]))
    assert "(est.)" not in recs[0]["expiry"] and recs[0]["first_notice"] == "2026-11-27"


def test_currency_exposure_is_the_engines(book, stub_app):
    result = _engine(book)
    table = _table(curve.render(AS_OF, book), curve.CURRENCY_TABLE_ID)
    assert [r["currency"] for r in table.data] == list(result["currency_exposure"]) == ["CNY"]
    cny = table.data[0]
    assert cny["pnl_local"] == result["currency_exposure"]["CNY"]["pnl_local"] == 3 * 5 * (80500.0 - 80000.0)
    assert cny["pnl_usd"] == "n/a" and table.tooltip_data[0]["pnl_usd"]["value"]


def test_flat_contracts_are_collapsed(book, stub_app):
    conn = schema.connect(str(book))
    _future(conn, "C2", CORN, "CBOT:ZC", "2026-12-14", -2, 445.0)
    conn.commit()
    conn.close()
    body = curve.render(AS_OF, book)
    flat = _table(body, curve.FLAT_ID)
    assert flat.open is False and "Flat contracts (1)" in _text(flat)
    assert _table(flat, curve.FLAT_TABLE_ID).data[0]["contract_id"] == CORN


def test_empty_book_shows_the_note(tmp_path, stub_app):
    path = _write_book(tmp_path / "empty.db", empty=True)
    text = _text(curve.render(AS_OF, path))
    assert f"No open commodity futures on {AS_OF}." in text
    assert not [n for n in _walk(curve.render(AS_OF, path)) if getattr(n, "id", None) == curve.GRID_ID]


def test_render_says_why_when_there_is_no_date_or_no_database(tmp_path, stub_app):
    assert "No as-of date" in curve.render(None, tmp_path / "x.db").children
    import sqlite3

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    stub_app.connect_readonly = refuse
    assert "Database not available" in curve.render(AS_OF, tmp_path / "missing.db").children


def test_layout_has_the_unit_switch_and_no_date_picker():
    layout = curve.layout(AS_OF)
    ids = [getattr(n, "id", None) for n in _walk(layout) if getattr(n, "id", None)]
    assert curve.BODY_ID in ids and curve.REFRESH_ID in ids and curve.UNIT_ID in ids
    assert all(i.startswith("curve-") for i in ids), ids
    radio = _table(layout, curve.UNIT_ID)
    assert [o["value"] for o in radio.options] == ["lots", "units", "usd"] and radio.value == "lots"
    assert not any(type(n).__name__ == "DatePickerSingle" for n in _walk(layout))
    assert curve.build_layout is curve.layout


def test_register_callbacks_registers_against_the_shared_ids(book, stub_app):
    from ui.revision import DATA_REVISION_ID
    from ui.tabs.header import AS_OF_STORE_ID
    app = dash.Dash(__name__)
    curve.register_callbacks(app, get_db_path=lambda: str(book))
    keys = [k for k in app.callback_map if curve.BODY_ID in k]
    assert len(keys) == 1
    inputs = {(i["id"], i["property"]) for i in app.callback_map[keys[0]]["inputs"]}
    assert inputs == {(AS_OF_STORE_ID, "data"), (DATA_REVISION_ID, "data"),
                      (curve.REFRESH_ID, "n_intervals"), (curve.UNIT_ID, "value")}
    callback = app.callback_map[keys[0]]["callback"].__wrapped__
    body = callback(AS_OF, "rev", 0, "units")
    assert _grid_row(_table(body, curve.GRID_ID), "NYMEX:CL", _engine(book))[1]["m_2026-12"] == 1000.0
