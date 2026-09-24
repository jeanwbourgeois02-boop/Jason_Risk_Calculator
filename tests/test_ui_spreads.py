"""Tests for ui/tabs/spreads.py: the Spreads tab renders `engine.spreads.book_spreads`'s output
and recomputes nothing. Most tests feed the body builders a synthetic `book_spreads` result; the
end-to-end ones build a small book in tmp_path through the real schema and the real engine: a WTI
calendar spread with marks on two closes and a corn outright with no price at all.

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import sys
import types

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from dash import dash_table, html  # noqa: E402

from data.contracts import get_root  # noqa: E402
from data.ingest import schema  # noqa: E402
from ui.tabs import spreads  # noqa: E402

AS_OF = "2026-09-15"          # a Tuesday: Daily is measured from the 2026-09-14 close
PREV = "2026-09-14"
CLZ6, CLF7 = "CLZ26 Comdty", "CLF27 Comdty"
CORN = "C Z26 Comdty"
PERIODS = ("ltd", "daily", "d5", "mtd", "ytd")


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
    return " ".join(n for n in _walk(node) if isinstance(n, str))


def _by_id(node, node_id):
    hits = [n for n in _walk(node) if getattr(n, "id", None) == node_id]
    assert len(hits) == 1, f"{len(hits)} x {node_id}"
    return hits[0]


def _has(node, node_id) -> bool:
    return any(getattr(n, "id", None) == node_id for n in _walk(node))


# --------------------------------------------------------------------------- a fake result
def _periods(values, reasons=None, notes=None, refs=None):
    return {"pnl_usd": dict(zip(PERIODS, values)),
            "pnl_reasons": {p: (reasons or {}).get(p, "") for p in PERIODS},
            "pnl_notes": {p: (notes or {}).get(p, "") for p in PERIODS},
            "ref_dates": {p: (refs or {}).get(p, AS_OF if p == "ltd" else PREV) for p in PERIODS}}


def _leg(inst, lots, pnl_usd, month, weight=None, reason="", status="open", product="FUTURE"):
    return {"trade_ids": [f"T-{inst}"], "instrument_id": inst, "root_id": "NYMEX:CL", "product": product,
            "contract_month": month, "lots": lots, "open_lots": lots if status == "open" else 0.0,
            "currency": "USD", "pnl_local": pnl_usd, "pnl_usd": pnl_usd, "status": status, "reason": reason,
            "weight": weight}


def _spread(sid, name, ltd, *, kind="calendar", status="open", legs=None, periods=None, leftover=None, **extra):
    s = {"spread_id": sid, "name": name, "kind": kind, "template": "", "family": "", "unit": "USD/bbl",
         "size": 10.0, "size_unit": "lots", "deviation": 0.0, "also_matches": [], "trade_ids": [f"{sid}-1"],
         "accounts": ["ACC"], "trade_dates": ["2026-09-01"], "status": status,
         "legs": legs if legs is not None else [_leg(CLZ6, 10.0, 1000.0, "2026-12", 1.0),
                                                _leg(CLF7, -10.0, -400.0, "2027-01", -1.0)],
         "leftover": leftover if leftover is not None else [{"root_id": "NYMEX:CL", "lots": 0.0,
                                                             "usd_notional": 0.0, "reason": ""}],
         "leftover_basis": "net of the calendar"}
    s.update(periods or _periods([ltd, 100.0, 200.0, 300.0, 400.0]))
    s.update(extra)
    return s


def _outright(tid, ltd, why="", review_ids=()):
    o = {"trade_id": tid, "instrument_id": CORN, "root_id": "CBOT:ZC", "contract_month": "2026-12",
         "account": "ACC", "trade_date": "2026-09-01", "lots": 2.0, "currency": "USD", "status": "open",
         "pnl_local": ltd, "why_outright": why, "review_ids": list(review_ids)}
    o.update(_periods([ltd, 1.0, 2.0, 3.0, 4.0]) if ltd is not None
             else _periods([None] * 5, reasons={p: "C1 (no FUTURE_PX mark)" for p in PERIODS}))
    return o


def _result():
    unpriced = _periods([None, None, None, None, None],
                        reasons={p: "unpriced on 2026-09-15: W9 (no FUTURE_PX mark)" for p in PERIODS})
    return {
        "as_of": AS_OF,
        "spreads": [
            _spread("SPREAD-A", "CL Z26/F27 calendar", 600.0),
            _spread("SPREAD-B", "CL X26/Z26 calendar", -5000.0),
            _spread("SPREAD-C", "Brent/WTI", None, kind="brent_wti", family="energy", template="brent_wti",
                    periods=unpriced,
                    legs=[_leg(CLZ6, 5.0, None, "2026-12", 1.0, reason="W9 (no FUTURE_PX mark)")],
                    leftover=[{"root_id": "ICE:B", "lots": 1.0, "usd_notional": None,
                               "reason": "COZ26 Comdty: no price or USD conversion on 2026-09-15"}]),
            _spread("BUNDLE-old", "old", 50.0, kind="bundle", status="closed", size=None, size_unit="",
                    legs=[_leg(CLZ6, 1.0, 50.0, "2026-12", status="closed")], leftover=[]),
        ],
        "outrights": [_outright("C1", None, why="", review_ids=["ratio_off|C1+C2"]),
                      _outright("C2", 80.0, why="split by hand (spread_overrides)")],
        "review": [{"review_id": "ratio_off|C1+C2", "kind": "ratio_off", "trade_ids": ["C1", "C2"],
                    "instruments": [CORN], "accounts": ["ACC"], "trade_dates": ["2026-09-01"],
                    "candidates": [{"kind": "calendar", "name": "C Z26/H27 calendar", "trade_ids": ["C1", "C2"],
                                    "size": 2.0, "size_unit": "lots", "deviation": 0.1, "also_matches": []}],
                    "reason": "looks like C Z26/H27 calendar but the lots are off by 10.0%; left as outrights"}],
        "reasons": ["template crack_321 could not be sized"],
    }


# --------------------------------------------------------------------------- rows, legs, n/a
def test_open_spreads_are_rows_sorted_by_absolute_ltd_with_na_last():
    table = _by_id(spreads.body(_result()), spreads.TABLE_ID)
    names = [r["name"] for r in table.data]
    assert names == ["CL X26/Z26 calendar", "CL Z26/F27 calendar", "Brent/WTI"]      # closed one not here
    assert [r["rank"] for r in table.data] == [1, 2, 3]
    first = table.data[0]
    assert first["ltd"] == -5000.0 and first["daily"] == 100.0 and first["ytd"] == 400.0
    assert first["legs"] == f"CLZ26 +10 / CLF27 {spreads.MINUS}10"
    assert first["size"] == 10.0 and first["size_unit"] == "lots" and first["kind"] == "Calendar"
    assert first["leftover"] == "none (clean)" and first["leftover_usd"] == 0.0
    assert {c["id"] for c in table.columns} >= set(PERIODS) | {"leftover", "leftover_usd", "size", "legs"}


def test_a_missing_figure_is_na_with_the_engines_reason_never_zero():
    table = _by_id(spreads.body(_result()), spreads.TABLE_ID)
    row = next(i for i, r in enumerate(table.data) if r["name"] == "Brent/WTI")
    rec, tips = table.data[row], table.tooltip_data[row]
    for p in PERIODS:
        assert rec[p] == "n/a"
        assert "W9 (no FUTURE_PX mark)" in tips[p]["value"]
    assert rec["kind"] == "Energy: brent_wti"
    assert rec["leftover"] == "B +1" and rec["leftover_usd"] == "n/a"
    assert "no price or USD conversion" in tips["leftover_usd"]["value"]
    # a figure carries its reference close on hover
    assert "measured from the 2026-09-14 close" in table.tooltip_data[0]["daily"]["value"]


def test_legs_are_collapsed_under_the_table_in_the_same_order():
    legs = _by_id(spreads.body(_result()), spreads.LEGS_ID)
    blocks = [n for n in legs.children if isinstance(n, html.Details)]
    assert len(blocks) == 3 and not any(b.open for b in blocks)
    assert blocks[0].children[0].children.startswith("#1 CL X26/Z26 calendar")
    leg_table = blocks[2].children[1]
    assert isinstance(leg_table, dash_table.DataTable)
    rec, tip = leg_table.data[0], leg_table.tooltip_data[0]
    assert rec["contract"] == "CLZ26" and rec["month"] == "2026-12" and rec["weight"] == 1.0
    assert rec["pnl_usd"] == "n/a" and "no FUTURE_PX mark" in tip["pnl_usd"]["value"]
    ok = blocks[0].children[1].data
    assert [(r["contract"], r["lots"], r["pnl_usd"]) for r in ok] == [("CLZ26", 10.0, 1000.0), ("CLF27", -10.0, -400.0)]


# --------------------------------------------------------------------------- the total line
def test_total_sums_the_known_figures_and_says_what_it_excludes():
    table_div = _by_id(spreads.body(_result()), spreads.TABLE_ID + "-footer")
    footer, tips = table_div.data[0], table_div.tooltip_data[0]
    assert footer["name"] == "Total"
    assert footer["ltd"] == pytest.approx(600.0 - 5000.0)
    assert footer["daily"] == pytest.approx(200.0)
    assert footer["leftover_usd"] == pytest.approx(0.0)
    assert "excludes 1 of 3 spreads" in tips["ltd"]["value"] and "Brent/WTI" in tips["ltd"]["value"]
    assert footer["legs"].startswith("excludes n/a:") and "LTD 1" in footer["legs"]


def test_total_is_na_when_no_spread_has_the_figure_and_plain_when_all_known():
    rec, tip = spreads.total_record([{"name": "x"}], [{"ltd": "n/a"}], ["ltd"], "name", "legs", "spread")
    assert rec["ltd"] == "n/a" and "no spread has a LTD figure" in tip["ltd"]["value"]
    rec, tip = spreads.total_record([{"name": "x"}, {"name": "y"}], [{"ltd": 1.0}, {"ltd": 2.5}],
                                    ["ltd"], "name", "legs", "spread")
    assert rec["ltd"] == 3.5 and rec["legs"] == "2 spreads" and not tip


# --------------------------------------------------------------------------- outrights and review
def test_outrights_render_with_period_pnl_and_why():
    body = spreads.body(_result())
    table = _by_id(body, spreads.OUTRIGHTS_TABLE_ID)
    c1, c2 = table.data
    assert c1["trade_id"] == "C1" and c1["ltd"] == "n/a" and c1["why"] == "no spread fits"
    assert "no FUTURE_PX mark" in table.tooltip_data[0]["ltd"]["value"]
    assert c1["review"] == "yes (1)" and "ratio_off|C1+C2" in table.tooltip_data[0]["review"]["value"]
    assert c2["ltd"] == 80.0 and c2["why"] == "split by hand (spread_overrides)"
    footer = _by_id(body, spreads.OUTRIGHTS_TABLE_ID + "-footer").data[0]
    assert footer["ltd"] == 80.0 and "LTD 1" in footer["why"]


def test_review_renders_with_its_reason_and_the_bundle_hint():
    body = spreads.body(_result())
    table = _by_id(body, spreads.REVIEW_TABLE_ID)
    (rec,) = table.data
    assert rec["kind"] == "Ratio off" and rec["trades"] == "C1, C2" and rec["contracts"] == "C Z26"
    assert "off by 10.0%" in rec["reason"] and rec["candidates"] == "C Z26/H27 calendar"
    assert "Bundles sub-tab" in _text(_by_id(body, spreads.REVIEW_ID))


def test_book_reasons_are_listed_in_the_caption():
    assert "template crack_321 could not be sized" in _text(spreads.body(_result()))


# --------------------------------------------------------------------------- closed spreads
def test_closed_spreads_are_collapsed_below_the_open_ones():
    body = spreads.body(_result())
    closed = _by_id(body, spreads.CLOSED_ID)
    assert isinstance(closed, html.Details) and closed.open is False
    assert closed.children[0].children == "Closed spreads (1)"
    table = _by_id(closed, spreads.CLOSED_TABLE_ID)
    assert [r["name"] for r in table.data] == ["old"]
    assert table.data[0]["size"] == "n/a" and "no calendar or template fits" in table.tooltip_data[0]["size"]["value"]
    assert table.data[0]["leftover_usd"] == "n/a" and "no futures leg" in table.tooltip_data[0]["leftover_usd"]["value"]
    order = [n.id for n in body.children if getattr(n, "id", None)]
    assert order.index(spreads.CLOSED_ID) < order.index(spreads.OUTRIGHTS_ID)
    # no closed spread: no section
    result = _result()
    result["spreads"] = [s for s in result["spreads"] if s["status"] == "open"]
    assert not _has(spreads.body(result), spreads.CLOSED_ID)


# --------------------------------------------------------------------------- empty book, shell
def test_empty_book_is_a_plain_sentence():
    body = spreads.body({"as_of": AS_OF, "spreads": [], "outrights": [], "review": [], "reasons": []})
    assert f"No commodity futures in the book on {AS_OF}" in _text(body)
    assert not _has(body, spreads.TABLE_ID)


def test_layout_follows_the_header_and_all_ids_are_prefixed():
    layout = spreads.layout(AS_OF)
    ids = [getattr(n, "id", None) for n in _walk(layout) if getattr(n, "id", None)]
    assert spreads.BODY_ID in ids and spreads.REFRESH_ID in ids
    assert all(i.startswith("spreads-") for i in ids), ids
    assert not any(type(n).__name__ == "DatePickerSingle" for n in _walk(layout))
    assert spreads.build_layout is spreads.layout
    body_ids = [getattr(n, "id", None) for n in _walk(spreads.body(_result())) if getattr(n, "id", None)]
    assert all(i.startswith("spreads-") for i in body_ids), body_ids


# --------------------------------------------------------------------------- the real engine
def _future(conn, tid, inst, root_id, expiry, lots, fill):
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root_id, root.currency, root.multiplier, inst, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": inst, "product": "FUTURE", "package_id": tid,
            "trade_date": "2026-09-01", "quantity": lots, "price": fill, "account": "ACC", "counterparty": "C",
            "strategy": "", "trader": "JB", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, "2026-09-01", expiry, fill))


def _px(conn, inst, expiry, value, day):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')", (day, inst, expiry, value))


def _write_book(path, empty=False):
    conn = schema.connect(str(path))
    if not empty:
        _future(conn, "W1", CLZ6, "NYMEX:CL", "2026-12-31", 2, 70.0)
        _future(conn, "W2", CLF7, "NYMEX:CL", "2027-01-29", -2, 69.5)
        for day, z, f in ((PREV, 70.5, 69.8), (AS_OF, 71.0, 70.2)):
            _px(conn, CLZ6, "2026-12-31", z, day)
            _px(conn, CLF7, "2027-01-29", f, day)
        _future(conn, "C1", CORN, "CBOT:ZC", "2026-12-14", 2, 440.0)       # no price at all
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def stub_app(monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    return stub


def test_render_on_a_real_book(tmp_path, stub_app):
    body = spreads.render(AS_OF, _write_book(tmp_path / "spreads.db"))
    table = _by_id(body, spreads.TABLE_ID)
    (row,) = table.data
    ltd = 2 * 1000 * (71.0 - 70.0) - 2 * 1000 * (70.2 - 69.5)
    prev = 2 * 1000 * (70.5 - 70.0) - 2 * 1000 * (69.8 - 69.5)
    assert row["name"] == "CL Z26/F27 calendar" and row["legs"] == f"CLZ26 +2 / CLF27 {spreads.MINUS}2"
    assert row["ltd"] == pytest.approx(ltd) and row["daily"] == pytest.approx(ltd - prev)
    outs = _by_id(body, spreads.OUTRIGHTS_TABLE_ID)
    (corn,) = outs.data
    assert corn["trade_id"] == "C1" and corn["ltd"] == "n/a" and outs.tooltip_data[0]["ltd"]["value"]


def test_render_on_an_empty_book_and_without_a_date_or_database(tmp_path, stub_app):
    assert f"No commodity futures in the book on {AS_OF}" in _text(
        spreads.render(AS_OF, _write_book(tmp_path / "empty.db", empty=True)))
    assert "No as-of date" in spreads.render(None, tmp_path / "x.db").children
    import sqlite3

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    stub_app.connect_readonly = refuse
    assert "Database not available" in spreads.render(AS_OF, tmp_path / "missing.db").children


def test_register_callbacks_registers_against_the_shared_ids(tmp_path, stub_app):
    from ui.revision import DATA_REVISION_ID
    from ui.tabs.header import AS_OF_STORE_ID
    path = _write_book(tmp_path / "cb.db")
    app = dash.Dash(__name__)
    spreads.register_callbacks(app, get_db_path=lambda: str(path))
    keys = [k for k in app.callback_map if spreads.BODY_ID in k]
    assert len(keys) == 1
    inputs = {(i["id"], i["property"]) for i in app.callback_map[keys[0]]["inputs"]}
    assert inputs == {(AS_OF_STORE_ID, "data"), (DATA_REVISION_ID, "data"), (spreads.REFRESH_ID, "n_intervals")}
    callback = app.callback_map[keys[0]]["callback"].__wrapped__
    assert _has(callback(AS_OF, "rev", 0), spreads.TABLE_ID)
