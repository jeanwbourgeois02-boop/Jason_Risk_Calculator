"""Blotter, commodity conversion Phase 3 (ui-blotter's own file): the Positions block's
Commodities section (book-positions' `commodities` block rendered, above the FX lines), the
Futures sub-tab grouped by sector and commodity with USD subtotals under the header's display
rule, and the P&L-by-asset-class placing of CMDTY_OPTION (Options) and a leftover IRS (Other).
The screen renders the engine's figures; nothing here is recomputed but the sums of them."""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("dash", reason="the Blotter is a Dash view")

import dash  # noqa: E402

from data.contracts import get_root  # noqa: E402
from data.ingest import schema  # noqa: E402
from ui.tabs import blotter  # noqa: E402

AS_OF = "2026-06-20"
_CLOSE = f"{AS_OF}T17:00:00-04:00"


# --------------------------------------------------------------------------- helpers
def _db():
    conn = sqlite3.connect(":memory:")
    schema.create_schema(conn)
    return conn


def _future(conn, trade_id, instrument, root_id, lots, fill, expiry, price=None):
    """A commodity future the way the parser books it (base_ccy = the root id, quote_ccy = the
    contract's currency, multiplier from the contract master), its FUTURE_PX on AS_OF if given."""
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (instrument, root_id, root.currency, root.multiplier, instrument, expiry))
    conn.execute("INSERT INTO trades VALUES (?,'XLSX',?,'FUTURE',?,'2026-06-01',?,?,'ACC','CPTY','','TR','fut','')",
                 (trade_id, instrument, trade_id, lots, fill))
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,'2026-06-01',?,?,0)",
                 (trade_id, root.currency, lots * root.multiplier * fill, expiry, fill))
    if price is not None:
        conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH',?)", (AS_OF, instrument, expiry, price, _CLOSE))
    conn.commit()


def _usdcny(conn, spot=7.2):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES ('USDCNY','FX','USD','CNY',1,0,'USDCNY Curncy','9999-12-31')")
    conn.execute("INSERT INTO marks VALUES (?,'USDCNY',?,'SPOT',?,'BBG_BFXFORWARD',?)", (AS_OF, AS_OF, spot, _CLOSE))
    conn.commit()


def _eurusd_forward(conn):
    """One FX forward so the FX part of the Positions block has a line (long 1m EUR at 1.10)."""
    conn.execute("INSERT INTO instruments VALUES ('EURUSD','FX','EUR','USD',1,0,'EURUSD Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES ('T1','XLSX','EURUSD','FX_FWD','T1','2026-06-01',1000000,1.10,"
                 "'ACC','CPTY','','TR','buy eur','')")
    conn.executemany("INSERT INTO trade_legs VALUES ('T1',?,'FX_NEAR',?,?,'2026-06-01','2026-09-20',1.10,1)",
                     [(1, "EUR", 1_000_000), (2, "USD", -1_100_000)])
    conn.execute("INSERT INTO marks VALUES (?,'EURUSD','2026-09-20','FWD_OUTRIGHT',1.108,'BBG_BFXFORWARD',?)", (AS_OF, _CLOSE))
    conn.execute("INSERT INTO marks VALUES (?,'EURUSD',?,'SPOT',1.105,'BBG_BFXFORWARD',?)", (AS_OF, AS_OF, _CLOSE))
    conn.commit()


def _book(hg_price=True):
    """Energy: 2 WTI Dec26 at 68 marked 70 (+4,000 USD), 1 Brent Nov26 at 70 marked 72 (+2,000 USD).
    Metals: -2 SHFE copper at 79,000 marked 80,000 (-10,000 CNY at USDCNY 7.2), 3 COMEX copper at
    440 marked 450 (US cents, or unpriced with `hg_price=False`). Plus one EURUSD forward."""
    conn = _db()
    _eurusd_forward(conn)
    _usdcny(conn)
    _future(conn, "F1", "CLZ26 Comdty", "NYMEX:CL", 2.0, 68.0, "2026-11-19", 70.0)
    _future(conn, "B1", "COX26 Comdty", "ICE:B", 1.0, 70.0, "2026-09-30", 72.0)
    _future(conn, "C1", "CUZ26 Comdty", "SHFE:CU", -2.0, 79_000.0, "2026-12-15", 80_000.0)
    _future(conn, "H1", "HGZ26 Comdty", "COMEX:HG", 3.0, 440.0, "2026-12-29", 450.0 if hg_price else None)
    return conn


def _walk(component):
    """Every node under `component`, depth first, in document order."""
    out, stack = [], [component]
    while stack:
        node = stack.pop()
        out.append(node)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend(reversed(children))
        elif children is not None and not isinstance(children, str):
            stack.append(children)
    return out


def _tables(component) -> list:
    return [n for n in _walk(component) if isinstance(n, dash.dash_table.DataTable)]


def _texts(component) -> list:
    return [n if isinstance(n, str) else getattr(n, "children", None) for n in _walk(component)
            if isinstance(n, str) or isinstance(getattr(n, "children", None), str)]


def _block():
    """`book_positions(...)["commodities"]` as book-positions documents it (Handoff 2026-09-24):
    energy fully known; metals with COMEX copper n/a (no price), so the sector and the book
    sums exclude it and say so; CNY P&L held, and a JPY one the engine could not convert."""
    hg_reason = "no FUTURE_PX for HGZ26 Comdty on 2026-06-20"
    return {
        "available": True, "note": "",
        "reason": f"excludes 1 of 3 commodities with no USD figure: COMEX copper (COMEX:HG): {hg_reason}",
        "sectors": [
            {"sector": "energy", "net_usd": 212_000.0, "gross_usd": 212_000.0, "missing": [], "reason": "",
             "commodities": [
                 {"root_id": "NYMEX:CL", "name": "NYMEX WTI light sweet crude", "exchange": "NYMEX", "currency": "USD",
                  "net_lots": 2.0, "gross_lots": 2.0, "net_units": 2000.0, "unit": "bbl", "net_usd": 140_000.0,
                  "gross_usd": 140_000.0, "missing": [], "reason": ""},
                 {"root_id": "ICE:B", "name": "ICE Brent crude", "exchange": "ICE", "currency": "USD",
                  "net_lots": 1.0, "gross_lots": 1.0, "net_units": 1000.0, "unit": "bbl", "net_usd": 72_000.0,
                  "gross_usd": 72_000.0, "missing": [], "reason": ""}]},
            {"sector": "metals", "net_usd": -111_111.0, "gross_usd": 111_111.0, "missing": ["COMEX:HG"],
             "reason": f"excludes 1 of 2 commodities with no USD figure: COMEX copper (COMEX:HG): {hg_reason}",
             "commodities": [
                 {"root_id": "SHFE:CU", "name": "SHFE copper cathode", "exchange": "SHFE", "currency": "CNY",
                  "net_lots": -2.0, "gross_lots": 2.0, "net_units": -10.0, "unit": "t", "net_usd": -111_111.0,
                  "gross_usd": 111_111.0, "missing": [], "reason": ""},
                 {"root_id": "COMEX:HG", "name": "COMEX copper", "exchange": "COMEX", "currency": "USD",
                  "net_lots": 3.0, "gross_lots": 3.0, "net_units": 75_000.0, "unit": "lb", "net_usd": None,
                  "gross_usd": None, "missing": ["HGZ26 Comdty"], "reason": hg_reason}]},
        ],
        "net_usd": 100_889.0, "gross_usd": 323_111.0, "missing": ["COMEX:HG"],
        "currency_exposure": {"CNY": {"pnl_local": -10_000.0, "pnl_usd": -1_388.89, "reason": ""},
                              "JPY": {"pnl_local": 5_000.0, "pnl_usd": None, "reason": "no official SPOT for JPY"}},
    }


def _fx_blocks():
    return {
        "fx": {"available": True, "net_usd": 1_000_000.0, "gross_usd": 2_000_000.0, "reason": "", "metals": [],
               "by_ccy": [
                   {"ccy": "JPY", "quoted": 150.0, "label": "USDJPY", "local_delta": -150e6, "usd_delta": -1e6,
                    "reason": "", "metal": False},
                   {"ccy": "USD", "quoted": 1.0, "label": "USD", "local_delta": 1e6, "usd_delta": 1e6,
                    "reason": "", "metal": False}]},
        "fx_options": {"by_pair": {"EURUSD": 55_000.0}, "usd_delta": 55_000.0, "options": 1, "missing": [], "reason": ""},
    }


# --------------------------------------------------------------------------- Positions: Commodities
def test_commodities_section_renders_sectors_commodities_total_and_currency_exposure():
    rows = blotter.commodity_positions_rows(_block())
    lines = [(r["kind"], r["position"].strip()) for r in rows["records"]]
    assert lines == [("sector", "Energy"), ("commodity", "NYMEX WTI light sweet crude"), ("commodity", "ICE Brent crude"),
                     ("sector", "Metals"), ("commodity", "SHFE copper cathode"), ("commodity", "COMEX copper")]
    by_name = {r["position"].strip(): r for r in rows["records"]}
    energy, wti, cu = by_name["Energy"], by_name["NYMEX WTI light sweet crude"], by_name["SHFE copper cathode"]
    assert (energy["net_usd"], energy["gross_usd"]) == (212_000.0, 212_000.0)
    assert energy["net_lots"] == "" and energy["detail"] == ""                   # lots are per commodity; nothing left out
    assert rows["tooltips"][0]["detail"]["value"] == "2 commodities"
    assert (wti["exchange"], wti["net_lots"], wti["gross_lots"], wti["net_units"], wti["unit"]) == ("NYMEX", 2.0, 2.0, 2000.0, "bbl")
    assert (wti["net_usd"], wti["gross_usd"], wti["sector"]) == (140_000.0, 140_000.0, "Energy")
    assert (cu["net_lots"], cu["gross_lots"], cu["net_usd"]) == (-2.0, 2.0, -111_111.0)
    assert cu["detail"] == "CNY"                                                  # a marker; the sentence on hover
    assert rows["tooltips"][[r["position"].strip() for r in rows["records"]].index("SHFE copper cathode")]["detail"] \
        == {"value": "CNY contract, in USD at the day's spot", "type": "text"}
    assert all(r["position"].startswith("    ") for r in rows["records"] if r["kind"] == "commodity")

    assert rows["footer"] == [{"kind": "total", "position": blotter.COMMODITY_TOTAL_LABEL, "sector": "", "exchange": "",
                               "net_lots": "", "gross_lots": "", "net_units": "", "unit": "", "net_usd": 100_889.0,
                               "gross_usd": 323_111.0, "detail": blotter.COMMODITY_TOTAL_DETAIL}]
    assert [(r["position"], r["currency"], r["pnl_local"], r["pnl_usd"]) for r in rows["ccy_records"]] == [
        ("P&L held in CNY", "CNY", -10_000.0, -1_388.89), ("P&L held in JPY", "JPY", 5_000.0, None)]
    assert rows["caption"] == _block()["reason"] and rows["caption_marker"] == "excl. 1"

    section = blotter.commodity_positions_section(_block())
    ids = [t.id for t in _tables(section)]
    assert ids == [blotter.COMMODITY_POSITIONS_TABLE_ID, blotter.COMMODITY_POSITIONS_TABLE_ID + "-footer",
                   blotter.COMMODITY_CCY_TABLE_ID]
    # Phase A: the reason is a marker beside the title, its sentence on hover; no paragraph
    marker = next(n for n in _walk(section) if getattr(n, "className", "") == "marker")
    assert (marker.children, marker.title) == ("excl. 1", _block()["reason"])
    assert _block()["reason"] not in _texts(section) and "P&L held in foreign currency" in _texts(section)
    assert not [n for n in _walk(section) if getattr(n, "className", "") == "section-kicker"]
    # summary money in k / m: the cells stay numbers, whole units, the full figure on hover
    tables = {t.id: t for t in _tables(section)}
    body = tables[blotter.COMMODITY_POSITIONS_TABLE_ID]
    usd_col = next(c for c in body.columns if c["id"] == "net_usd")
    assert "s" in usd_col["format"]["specifier"]
    cu_at = [r["position"].strip() for r in body.data].index("SHFE copper cathode")
    assert body.data[cu_at]["net_usd"] == -111_111.0 and body.tooltip_data[cu_at]["net_usd"]["value"] == "(111,111) USD"


def test_a_commodity_with_no_usd_figure_reads_na_with_its_reason_never_zero():
    rows = blotter.commodity_positions_rows(_block())
    records, tips = rows["records"], rows["tooltips"]
    i = [r["position"].strip() for r in records].index("COMEX copper")
    hg, hg_tip = records[i], tips[i]
    assert hg["net_usd"] is None and hg["gross_usd"] is None                       # printed n/a, not 0
    assert hg_tip["net_usd"]["value"] == hg_tip["gross_usd"]["value"] == "no FUTURE_PX for HGZ26 Comdty on 2026-06-20"
    assert hg["net_lots"] == 3.0 and "net_lots" not in hg_tip                     # lots are known
    # the sector summed over its known commodities, the exclusion in sight and its reason on hover
    j = [r["position"].strip() for r in records].index("Metals")
    assert records[j]["net_usd"] == -111_111.0
    assert records[j]["detail"] == "excl. 1"
    assert tips[j]["detail"]["value"].startswith("2 commodities; excludes 1 of 2 commodities with no USD figure: COMEX copper")
    assert tips[j]["net_usd"]["value"].startswith("excludes 1 of 2 commodities with no USD figure: COMEX copper")
    assert rows["footer_tooltips"][0]["net_usd"]["value"].startswith("excludes 1 of 3 commodities")
    # a currency the engine could not convert: n/a with the reason
    jpy_tip = rows["ccy_tooltips"][[r["currency"] for r in rows["ccy_records"]].index("JPY")]
    assert jpy_tip == {"pnl_usd": {"value": "no official SPOT for JPY", "type": "text"}}
    table = next(t for t in _tables(blotter.commodity_positions_section(_block()))
                 if t.id == blotter.COMMODITY_POSITIONS_TABLE_ID)
    assert {c["id"]: c["format"]["nully"] for c in table.columns if c["type"] == "numeric"} == {
        "net_lots": "n/a", "gross_lots": "n/a", "net_units": "n/a", "net_usd": "n/a", "gross_usd": "n/a"}


def test_no_commodities_shows_the_reason_and_no_empty_table():
    for block, why in (({"available": True, "note": "", "sectors": [], "net_usd": None, "gross_usd": None, "missing": [],
                         "currency_exposure": {}, "reason": "no open commodity futures on 2026-06-20"},
                        "no open commodity futures on 2026-06-20"),
                       (None, "commodity positions were not returned by the positions engine")):
        rows = blotter.commodity_positions_rows(block)
        assert rows["records"] == [] and rows["footer"] == [] and rows["caption"] == why
        section = blotter.commodity_positions_section(block)
        assert _tables(section) == [] and why in _texts(section)   # no table: the reason stays in sight


def test_the_commodities_sit_above_the_fx_lines_and_the_fx_lines_are_unchanged(monkeypatch):
    from engine.ladder import positions
    with_commodities = {**_fx_blocks(), "commodities": _block()}
    monkeypatch.setattr(positions, "book_positions", lambda conn, as_of: with_commodities)
    table = blotter.positions_table(None, AS_OF)
    ids = [t.id for t in _tables(table)]
    assert ids == [blotter.COMMODITY_POSITIONS_TABLE_ID, blotter.COMMODITY_POSITIONS_TABLE_ID + "-footer",
                   blotter.COMMODITY_CCY_TABLE_ID, blotter.POSITIONS_TABLE_ID, blotter.POSITIONS_TABLE_ID + "-footer"]
    texts = _texts(table)
    assert texts.index("Commodities") < texts.index("FX")

    # the FX lines: exactly what they are without a commodity block
    assert blotter.positions_rows(None, AS_OF, pos=with_commodities) == blotter.positions_rows(None, AS_OF, pos=_fx_blocks())
    fx_table = next(t for t in _tables(table) if t.id == blotter.POSITIONS_TABLE_ID)
    fx_footer = next(t for t in _tables(table) if t.id == blotter.POSITIONS_TABLE_ID + "-footer")
    assert [r["position"].strip() for r in fx_table.data] == ["JPY", "USD", "EURUSD options delta"]
    assert [r["position"] for r in fx_footer.data] == ["FX net USD delta (+ = long USD)", "FX gross USD delta",
                                                       "FX options delta (USD)"]
    assert next(r for r in fx_footer.data if r["position"].startswith("FX net"))["usd"] == 1_000_000.0   # commodities not in it


def test_total_book_positions_render_the_engine_commodity_figures_on_a_real_book():
    """Integration: the book's own futures through book-positions -> curve-positions, rendered
    as the engine gives them (the figures compared are the engine's, never recomputed here)."""
    from engine.ladder.positions import book_positions
    conn = _book()
    try:
        block = book_positions(conn, AS_OF)["commodities"]
        assert block["available"] and block["sectors"], block.get("reason")
        layout = blotter.scope_layout("total", conn, AS_OF, with_notices=False)
        tables = {t.id: t for t in _tables(layout)}
        body = tables[blotter.COMMODITY_POSITIONS_TABLE_ID].data
        sectors = [r["position"] for r in body if r["kind"] == "sector"]
        assert sorted(sectors) == ["Energy", "Metals"]
        by_name = {r["position"].strip(): r for r in body}
        for s in block["sectors"]:
            for c in s["commodities"]:
                line = by_name[c["name"]]
                # summary USD in k / m (Phase A): the engine's figure in whole units, never recomputed
                assert (line["net_lots"], line["net_usd"], line["gross_usd"]) == (
                    c["net_lots"], round(c["net_usd"]), round(c["gross_usd"]))
        total = tables[blotter.COMMODITY_POSITIONS_TABLE_ID + "-footer"].data[0]
        assert (total["net_usd"], total["gross_usd"]) == (round(block["net_usd"]), round(block["gross_usd"]))
        cny = tables[blotter.COMMODITY_CCY_TABLE_ID].data[0]
        assert (cny["currency"], cny["pnl_local"], cny["pnl_usd"]) == (
            "CNY", round(block["currency_exposure"]["CNY"]["pnl_local"]),
            round(block["currency_exposure"]["CNY"]["pnl_usd"]))
        assert cny["pnl_local"] == pytest.approx(-2 * 5 * (80_000 - 79_000))   # the engine's figure is the book's
        order = list(tables)
        assert order.index(blotter.COMMODITY_POSITIONS_TABLE_ID) < order.index(blotter.POSITIONS_TABLE_ID)
    finally:
        conn.close()


# --------------------------------------------------------------------------- Futures sub-tab
def _futures_table(conn):
    layout = blotter.scope_layout("futures", conn, AS_OF, with_notices=False)
    return next(t for t in _tables(layout) if t.id == "blotter-datatable-futures")


def test_futures_sub_tab_groups_by_sector_then_commodity_with_usd_subtotals(strict_marks):
    conn = _book(hg_price=False)
    try:
        table = _futures_table(conn)
        engine = blotter.scope_df(conn, "futures", AS_OF).set_index("trade_id")
        shape = [(r["row_kind"], r["sector"], r["commodity"], r["trade_id"]) for r in table.data]
        assert shape == [
            ("sector", "Energy", "", ""),
            ("commodity", "Energy", "ICE Brent crude", ""), ("trade", "Energy", "ICE Brent crude", "B1"),
            ("commodity", "Energy", "NYMEX WTI light sweet crude", ""),
            ("trade", "Energy", "NYMEX WTI light sweet crude", "F1"),
            ("sector", "Metals", "", ""),
            ("commodity", "Metals", "COMEX copper", ""), ("trade", "Metals", "COMEX copper", "H1"),
            ("commodity", "Metals", "SHFE copper cathode", ""), ("trade", "Metals", "SHFE copper cathode", "C1"),
        ]
        rows = table.data
        tips = table.tooltip_data
        # Energy: both priced, the engine's USD figures summed
        assert rows[0]["pnl_usd"] == pytest.approx(engine.loc["B1", "pnl_usd"] + engine.loc["F1", "pnl_usd"])
        assert rows[0]["pnl_usd"] == pytest.approx(6_000) and rows[0]["instrument_id"] == "2 trades"
        assert rows[1]["pnl_usd"] == pytest.approx(2_000) and rows[1]["exchange"] == "ICE"
        assert rows[3]["pnl_usd"] == pytest.approx(4_000) and rows[3]["instrument_id"] == "1 trade"
        # Metals: COMEX copper has no price -> left out of the sum and named, never 0
        assert rows[5]["pnl_usd"] == pytest.approx(engine.loc["C1", "pnl_usd"])
        assert rows[5]["instrument_id"] == "2 trades; excludes 1 of 2 unpriced"
        assert tips[5]["pnl_usd"]["value"].startswith("excludes 1 of 2 trades unpriced: H1: ")
        assert "no FUTURE_PX" in tips[5]["pnl_usd"]["value"]
        assert rows[6]["pnl_usd"] is None and rows[6]["instrument_id"] == "1 trade; excludes 1 of 1 unpriced"
        assert rows[7]["pnl_usd"] is None and "no FUTURE_PX" in tips[7]["pnl_usd"]["value"]
        assert "no FUTURE_PX" in tips[7]["mark"]["value"]                     # the n/a mark says why too
        # a trade row: exchange, contract, lots, fill, mark, local P&L with its currency, USD P&L
        cu = rows[9]
        assert (cu["instrument_id"], cu["exchange"], cu["quantity"], cu["fill"], cu["mark"], cu["pnl_ccy"]) == (
            "CUZ26 Comdty", "SHFE", 2, 79_000.0, 80_000.0, "CNY")
        assert cu["pnl_local"] == pytest.approx(-10_000) and cu["pnl_usd"] == pytest.approx(engine.loc["C1", "pnl_usd"])
        # subtotal rows: USD only (their local cell says so), and no trade id, so the P&L strip never counts them
        assert all(r["pnl_local"] == "" for r in rows if r["row_kind"] != "trade")
        assert all(t["pnl_local"]["value"] == blotter.SUBTOTAL_LOCAL_NOTE for r, t in zip(rows, tips) if r["row_kind"] != "trade")
        assert [r["trade_id"] for r in rows if r.get("trade_id")] == ["B1", "F1", "H1", "C1"]
        ids = [c["id"] for c in table.columns]
        assert ids[:4] == ["sector", "commodity", "instrument_id", "exchange"]
        assert ids.index("fill") + 1 == ids.index("mark")
        assert ids.index("pnl_local") + 1 == ids.index("pnl_ccy") == ids.index("pnl_usd") - 1
        kinds = {s["if"].get("filter_query") for s in table.style_data_conditional}
        assert {"{row_kind} = 'sector'", "{row_kind} = 'commodity'"} <= kinds
    finally:
        conn.close()


def test_futures_filter_refresh_keeps_the_grouping_and_the_filter_bar_offers_sector_and_commodity():
    conn = _book()
    try:
        df = blotter.scope_df(conn, "futures", AS_OF)
        cols, labels = blotter.scope_columns("futures")
        records, _tips, _styles = blotter._table_rows("futures", df[df["sector"] == "Metals"], cols, labels)
        assert [(r["row_kind"], r["commodity"]) for r in records] == [
            ("sector", ""), ("commodity", "COMEX copper"), ("trade", "COMEX copper"),
            ("commodity", "SHFE copper cathode"), ("trade", "SHFE copper cathode")]
        layout = blotter.scope_layout("futures", conn, AS_OF, with_notices=False)
        dropdowns = {n.id for n in _walk(layout) if isinstance(n, dash.dcc.Dropdown)}
        assert {"blotter-datatable-futures-filter-sector", "blotter-datatable-futures-filter-commodity"} <= dropdowns
        # the Total book's own table is flat, as before
        flat, _t, _s = blotter._table_rows("total", blotter.scope_df(conn, "total", AS_OF), *blotter.scope_columns("total"))
        assert all("row_kind" not in r for r in flat)
    finally:
        conn.close()


def test_a_contract_the_master_does_not_know_is_grouped_as_unclassified_last():
    conn = _book()
    try:
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES ('QQZ26 Comdty','FUTURE','XX:QQ','USD',1000,0,'','2026-12-18')")
        conn.execute("INSERT INTO trades VALUES ('X1','XLSX','QQZ26 Comdty','FUTURE','X1','2026-06-01',1,50,"
                     "'ACC','CPTY','','TR','fut','')")
        conn.execute("INSERT INTO trade_legs VALUES ('X1',1,'NOTIONAL','USD',50000,'2026-06-01','2026-12-18',50,0)")
        conn.commit()
        rows = _futures_table(conn).data
        assert (rows[-2]["row_kind"], rows[-2]["sector"], rows[-2]["commodity"]) == (
            "commodity", blotter.UNCLASSIFIED_SECTOR, "XX:QQ")
        assert rows[-1]["trade_id"] == "X1" and rows[-1]["exchange"] == ""
    finally:
        conn.close()


# --------------------------------------------------------------------------- P&L by asset class
def test_asset_classes_put_a_commodity_option_under_options_and_a_leftover_irs_under_other():
    conn = _book()
    try:
        # an option on a commodity future (valued on the listed path at Bloomberg's own price)
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES ('CLZ6C75 Comdty','CMDTY_OPTION','CL','USD',1000,0,"
                     "'CLZ6C75 Comdty','2026-11-17')")
        conn.execute("INSERT INTO trades VALUES ('CO1','XLSX','CLZ6C75 Comdty','CMDTY_OPTION','CO1','2026-06-01',2,3.10,"
                     "'ACC','CPTY','','TR','call','')")
        conn.execute("INSERT INTO trade_legs VALUES ('CO1',1,'NOTIONAL','USD',2000,'2026-06-01','2026-11-17',3.10,0)")
        conn.execute("INSERT INTO marks VALUES (?,'CLZ6C75 Comdty','2026-11-17','FUTURE_PX',3.60,'BBG_BDH',?)", (AS_OF, _CLOSE))
        # a rate swap left on an old database: blank P&L with its reason, never dropped
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES ('USD-SOFR-5Y','IRS','USD','USD',1,0,'','2031-06-03')")
        conn.execute("INSERT INTO trades VALUES ('S1','XLSX','USD-SOFR-5Y','IRS','S1','2026-06-01',10000000,0.04,"
                     "'ACC','CPTY','','TR','pay fixed','')")
        conn.execute("INSERT INTO trade_legs VALUES ('S1',1,'NOTIONAL','USD',10000000,'2026-06-01','2031-06-03',0.04,0)")
        conn.commit()

        total = blotter.scope_df(conn, "total", AS_OF)
        rows = {r["asset_class"]: r for r in blotter.asset_class_pnl_rows(conn, AS_OF, total)}
        assert list(rows) == ["Futures", "Options", "FX", "Other", "Total"]   # commodity classes first (Phase A)
        assert rows["Options"]["trades"] == 1 and rows["Options"]["ltd"]["available"]
        assert rows["Options"]["ltd"]["value"] == pytest.approx(total.set_index("trade_id").loc["CO1", "pnl_usd"])
        assert rows["Options"]["ltd"]["value"] == pytest.approx(2 * 1000 * (3.60 - 3.10))
        assert rows["Futures"]["trades"] == 4 and rows["Other"]["trades"] == 1
        other = rows["Other"]["ltd"]
        assert not other["available"] and "1 irs" in other["reason"]            # the strip's own wording
        assert rows["Other"]["unpriced"] == [f"S1: {total.set_index('trade_id').loc['S1', 'reason']}"]
        assert "rate swaps left the app" in rows["Other"]["unpriced"][0]      # value_book's reason, whole
        assert rows["Options"]["unpriced"] == [] and rows["Total"]["unpriced"] == rows["Other"]["unpriced"]
        assert rows["Total"]["trades"] == 7

        table = next(t for t in _tables(blotter.asset_class_pnl_table(conn, AS_OF, total))
                     if t.id == blotter.ASSET_TABLE_ID)
        i = [r["asset_class"] for r in table.data].index("Other")
        assert table.data[i]["ltd"] is None                                  # printed n/a, not 0
        assert "rate swaps left the app" in table.tooltip_data[i]["ltd"]["value"]
        # the option on a future is never on the Futures sub-tab
        assert "CO1" not in blotter.scope_df(conn, "futures", AS_OF)["trade_id"].tolist()
        assert "CO1" in blotter.scope_df(conn, "options", AS_OF)["trade_id"].tolist()
    finally:
        conn.close()


# --------------------------------------------------------------------------- LME forwards (Phase 5)
def _lme(conn, trade_id, root_id, tonnes, fill, prompt, outright=None):
    """An LME prompt-date forward the way the parser books it (data/ingest/blotter.py::
    _parse_lme_forward): instrument = the metal's root id, USD-quoted, tonnes signed; the metal
    leg and the USD leg on the prompt date. Its official outright for the prompt on AS_OF if given."""
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'LME_FWD',?,'USD',1,0,'','9999-12-31')", (root_id, root_id))
    conn.execute("INSERT INTO trades VALUES (?,'XLSX',?,'LME_FWD',?,'2026-06-01',?,?,'ACC','CPTY','','TR','lme','')",
                 (trade_id, root_id, trade_id, tonnes, fill))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,'FX_NEAR',?,?,'2026-06-01',?,?,?)", [
        (trade_id, 1, root_id, tonnes, prompt, fill, 0), (trade_id, 2, "USD", -tonnes * fill, prompt, fill, 1)])
    if outright is not None:
        conn.execute("INSERT INTO marks VALUES (?,?,?,'FWD_OUTRIGHT',?,'BBG_BFXFORWARD',?)",
                     (AS_OF, root_id, prompt, outright, _CLOSE))
    conn.commit()


def _lme_book():
    """`_book()` plus: long 100 t LME copper for 2026-09-16 at 9,800 marked 9,900 (+10,000 USD),
    short 75 t LME aluminium for 2026-12-16 at 2,640 with no price on file."""
    conn = _book()
    _lme(conn, "L1", "LME:CA", 100.0, 9_800.0, "2026-09-16", 9_900.0)
    _lme(conn, "L2", "LME:AH", -75.0, 2_640.0, "2026-12-16")
    return conn


def test_lme_forwards_are_their_own_asset_class_never_other(strict_marks):
    conn = _lme_book()
    try:
        total = blotter.scope_df(conn, "total", AS_OF)
        engine = total.set_index("trade_id")
        assert engine.loc["L1", "pnl_usd"] == pytest.approx(100 * (9_900 - 9_800))   # value_book's own figure
        rows = {r["asset_class"]: r for r in blotter.asset_class_pnl_rows(conn, AS_OF, total)}
        assert list(rows) == ["Futures", "LME forwards", "FX", "Total"]
        lme = rows["LME forwards"]
        assert lme["trades"] == 2 and rows["Futures"]["trades"] == 4
        assert lme["ltd"]["available"] and lme["ltd"]["value"] == pytest.approx(engine.loc["L1", "pnl_usd"])
        assert "excludes 1 of 2" in lme["ltd"]["excluded_summary"]              # L2 unpriced: out of the sum, named
        assert lme["unpriced"] == [f"L2: {engine.loc['L2', 'reason']}"]
        assert "no LME price of LME:AH" in lme["unpriced"][0]
        table = next(t for t in _tables(blotter.asset_class_pnl_table(conn, AS_OF, total))
                     if t.id == blotter.ASSET_TABLE_ID)
        assert "LME forwards" in [r["asset_class"] for r in table.data]
        assert "Other" not in [r["asset_class"] for r in table.data]
        assert blotter._fmt_product("LME_FWD") == "LME forward"
    finally:
        conn.close()


def test_lme_forwards_sit_under_their_metal_on_the_futures_and_lme_sub_tab(strict_marks):
    conn = _lme_book()
    try:
        assert blotter.SCOPE_LABELS["futures"] == "Futures & LME"
        engine = blotter.scope_df(conn, "futures", AS_OF).set_index("trade_id")
        assert {"L1", "L2"} <= set(engine.index)
        table = _futures_table(conn)
        shape = [(r["row_kind"], r["sector"], r["commodity"], r["trade_id"]) for r in table.data]
        copper, alu = get_root("LME:CA").name, get_root("LME:AH").name
        metals = shape[shape.index(("sector", "Metals", "", "")):]
        assert ("commodity", "Metals", copper, "") in metals and ("trade", "Metals", copper, "L1") in metals
        assert ("commodity", "Metals", alu, "") in metals and ("trade", "Metals", alu, "L2") in metals
        assert metals.index(("trade", "Metals", copper, "L1")) == metals.index(("commodity", "Metals", copper, "")) + 1
        rows = {r["trade_id"]: (r, t) for r, t in zip(table.data, table.tooltip_data) if r["row_kind"] == "trade"}
        l1, _ = rows["L1"]
        assert (l1["product"], l1["quantity"], l1["qty_unit"], l1["exchange"], l1["pnl_ccy"]) == (
            "LME forward", 100, "t", "LME", "USD")
        assert (l1["fill"], l1["mark"], l1["settle_date"]) == (9_800.0, 9_900.0, "2026-09-16")
        assert l1["pnl_usd"] == pytest.approx(engine.loc["L1", "pnl_usd"]) == pytest.approx(10_000)
        assert l1["pnl_local"] == pytest.approx(l1["pnl_usd"])                  # USD-quoted: local = USD
        assert rows["F1"][0]["product"] == "Future" and rows["F1"][0]["qty_unit"] == "lots"
        l2, tip2 = rows["L2"]
        assert l2["pnl_usd"] is None and l2["pnl_local"] is None and l2["mark"] is None   # n/a, never 0
        assert "no LME price of LME:AH" in tip2["pnl_usd"]["value"]
        # the metal's subtotal row: priced rows summed, the unpriced one named
        cu_row = next(r for r in table.data if r["row_kind"] == "commodity" and r["commodity"] == copper)
        assert cu_row["pnl_usd"] == pytest.approx(10_000) and cu_row["exchange"] == "LME"
        names = [c["name"] for c in table.columns]
        assert "Quantity" in names and "Unit" in names and "Product" in names and "Expiry / prompt" in names
    finally:
        conn.close()


def test_a_settled_lme_forward_shows_its_frozen_usd_figure_as_its_local_pnl():
    conn = _db()
    try:
        _lme(conn, "L9", "LME:NI", 12.0, 15_420.0, "2026-06-17")
        conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
                     "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, "
                     "frozen_at, note) VALUES ('L9','LME:NI','LME_FWD','USD','2026-06-17',-1200,0,'SPOT',1,"
                     "'2026-06-17','BBG_BDH',-1200,'2026-06-18T00:00:00','')")
        conn.commit()
        engine = blotter.scope_df(conn, "futures", AS_OF).set_index("trade_id")
        assert engine.loc["L9", "status"] == "SETTLED" and engine.loc["L9", "pnl_usd"] == pytest.approx(-1200)
        rec = next(r for r in _futures_table(conn).data if r["trade_id"] == "L9")
        assert rec["pnl_local"] == pytest.approx(-1200) and rec["pnl_ccy"] == "USD"
    finally:
        conn.close()


# --------------------------------------------------------------------------- Total book, Phase A
# Screens redesign Phase A (user, 2026-09-25): the Total book shows no P&L cards (the header is
# the total book) and its trade table reads in commodity terms, every figure value_book's own.
T1_CLOSE = "2026-06-18"          # the previous business day of AS_OF (a Saturday; the 19th is a holiday)


def _total_table(conn):
    layout = blotter.scope_layout("total", conn, AS_OF, with_notices=False)
    table = next(t for t in _tables(layout) if t.id == "blotter-datatable-total")
    return layout, table, {r["trade_id"]: (r, table.tooltip_data[i]) for i, r in enumerate(table.data)}


def _cmdty_option(conn):
    """2 lots of a WTI Dec26 75 call on the listed path, as the parser books it (base_ccy = the
    root id), at 3.10, Bloomberg's price 3.60 on AS_OF."""
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('CLZ26C 75 Comdty','CMDTY_OPTION','NYMEX:CL','USD',1000,0,"
                 "'CLZ6C 75 Comdty','2026-11-17')")
    conn.execute("INSERT INTO trades VALUES ('CO1','XLSX','CLZ26C 75 Comdty','CMDTY_OPTION','CO1','2026-06-01',2,3.10,"
                 "'ACC','CPTY','','TR','call','')")
    conn.execute("INSERT INTO trade_legs VALUES ('CO1',1,'NOTIONAL','USD',6200,'2026-06-01','2026-11-17',3.10,0)")
    conn.execute("INSERT INTO marks VALUES (?,'CLZ26C 75 Comdty','2026-11-17','FUTURE_PX',3.60,'BBG_BDH',?)",
                 (AS_OF, _CLOSE))
    conn.commit()


def test_total_book_trade_table_reads_in_commodity_terms_with_value_books_own_figures():
    conn = _book()
    _cmdty_option(conn)
    conn.execute("INSERT INTO marks VALUES (?,'CLZ26 Comdty','2026-11-19','FUTURE_PX',69.0,'BBG_BDH',?)",
                 (T1_CLOSE, f"{T1_CLOSE}T17:00:00-04:00"))
    conn.commit()
    try:
        engine = blotter.scope_df(conn, "total", AS_OF).set_index("trade_id")
        layout, table, rows = _total_table(conn)
        names = [c["name"] for c in table.columns]
        assert names[:4] == ["Instrument", "Commodity / pair", "Exchange", "Product"]
        assert "Pair" not in names and "Amount" not in names and "Notional (USD)" not in names

        wti, wti_tip = rows["F1"]
        assert (wti["instrument_id"], wti["commodity"], wti["exchange"], wti["product"]) == (
            "CLZ26 Comdty", get_root("NYMEX:CL").name, "NYMEX", "Future")
        assert (wti["side"], wti["quantity"], wti["qty_unit"], wti["fill"], wti["mark"]) == ("Buy", 2, "lots", 68.0, 70.0)
        assert wti["prev_close"] == 69.0 and wti_tip["prev_close"]["value"].startswith(f"{T1_CLOSE} close, BBG_BDH")
        assert (wti["pnl_local"], wti["pnl_ccy"]) == (pytest.approx(4_000.0), "USD")
        assert wti["pnl_usd"] == pytest.approx(engine.loc["F1", "pnl_usd"])
        assert wti_tip["mark"]["value"] == "BBG_BDH"

        cu, _ = rows["C1"]   # a CNY contract: local P&L in CNY beside the engine's USD figure
        assert (cu["exchange"], cu["side"], cu["qty_unit"], cu["pnl_ccy"]) == ("SHFE", "Sell", "lots", "CNY")
        assert cu["pnl_local"] == pytest.approx(-10_000.0) and cu["pnl_usd"] == pytest.approx(engine.loc["C1", "pnl_usd"])

        opt, _ = rows["CO1"]
        assert (opt["product"], opt["commodity"], opt["qty_unit"], opt["quantity"]) == (
            "Option on future", get_root("NYMEX:CL").name, "lots", 2)

        fx, _ = rows["T1"]   # an FX hedge: the pair in the commodity column, OTC, the base currency as unit
        assert (fx["commodity"], fx["exchange"], fx["product"], fx["qty_unit"], fx["pnl_ccy"]) == (
            "EURUSD", "OTC", "FX forward", "EUR", "USD")
        assert fx["quantity"] == 1_000_000 and fx["pnl_usd"] == pytest.approx(engine.loc["T1", "pnl_usd"])

        # the filters follow the columns: Commodity and Instrument, no Strategy
        labels = [n.children for n in _walk(layout) if getattr(n, "className", "") == "blotter-filter"
                  for n in [n.children[0]]]
        assert labels[:2] == ["Commodity / pair", "Instrument"] and "Strategy" not in labels
    finally:
        conn.close()


def test_total_book_missing_figures_say_why_and_are_listed_in_the_data_issues_drawer(strict_marks):
    conn = _book(hg_price=False)   # COMEX copper: no price on any date
    try:
        layout, table, rows = _total_table(conn)
        hg, tip = rows["H1"]
        assert hg["mark"] is None and hg["pnl_usd"] is None and hg["prev_close"] is None   # n/a, never 0
        assert "no FUTURE_PX" in tip["mark"]["value"] and "no FUTURE_PX" in tip["pnl_usd"]["value"]
        assert "no FUTURE_PX" in tip["prev_close"]["value"]
        drawer = next(n for n in _walk(layout) if getattr(n, "id", None) == blotter.TOTAL_ISSUES_ID)
        assert drawer.open is False and drawer.children[0].children == "Data issues (1)"
        assert "H1" in _texts(drawer)
        # no P&L cards on the Total book: the header is the total book
        assert not [n for n in _walk(layout) if getattr(n, "className", "") in ("cards", "card")]
    finally:
        conn.close()


def test_a_trade_dealt_after_the_previous_close_has_no_prev_close_and_says_so():
    conn = _book()
    conn.execute("UPDATE trades SET trade_date = ? WHERE trade_id = 'B1'", (AS_OF,))
    conn.commit()
    try:
        _, _, rows = _total_table(conn)
        brent, tip = rows["B1"]
        assert brent["prev_close"] is None and tip["prev_close"]["value"] == f"not in the book on the {T1_CLOSE} close (dealt after it)"
    finally:
        conn.close()


def test_positions_definitions_sit_on_hover_of_the_titles_and_summary_usd_is_k_m(monkeypatch):
    from engine.ladder import positions
    monkeypatch.setattr(positions, "book_positions", lambda conn, as_of: {**_fx_blocks(), "commodities": _block()})
    block = blotter.positions_table(None, AS_OF)
    assert not [n for n in _walk(block) if getattr(n, "className", "") == "section-kicker"]   # no paragraph
    titles = [n for n in _walk(block) if "about-title" in (getattr(n, "className", "") or "")]
    assert [t.children[0] for t in titles] == ["Positions", "Commodities", "P&L held in foreign currency", "FX"]
    assert all(t.title for t in titles)                                                      # definitions on hover
    fx = next(t for t in _tables(block) if t.id == blotter.POSITIONS_TABLE_ID)
    usd = next(c for c in fx.columns if c["id"] == "usd")
    assert "s" in usd["format"]["specifier"]
    footer = next(t for t in _tables(block) if t.id == blotter.POSITIONS_TABLE_ID + "-footer")
    net = next(i for i, r in enumerate(footer.data) if r["position"].startswith("FX net"))
    assert footer.data[net]["detail"] == "incl. options" and "FX options' delta included" in footer.tooltip_data[net]["detail"]["value"]
    assert footer.tooltip_data[net]["usd"]["value"] == "1,000,000 USD"                      # the full figure on hover


# --------------------------------------------------------------------------- the key date's label
# The Futures & LME sub-tab's key date is value_book's settle_date: a future's expiry, an LME
# ticket's prompt. Its header says which (user yes, 2026-09-25, to the "Settlement" label found
# wrong); the old "Settlement" column (mark_date, the same date again on an open row) is gone.
def _names(table) -> list:
    return [c["name"] for c in table.columns]


def test_the_key_date_reads_expiry_for_futures_alone_with_the_date_per_product_on_hover(strict_marks):
    conn = _book()
    try:
        table = _futures_table(conn)
        names = _names(table)
        assert "Expiry" in names and "Settlement" not in names and "Expiry / prompt" not in names
        assert "mark_date" not in [c["id"] for c in table.columns]
        tip = table.tooltip_header["settle_date"]
        assert "Future: the contract's last trade date" in tip and "LME forward: the prompt date" in tip
        f1 = next(r for r in table.data if r["trade_id"] == "F1")
        assert f1["settle_date"] == "2026-11-19"                     # the expiry, as value_book gives it
        # every figure and id stays in sight
        assert {"Fill", "Mark", "P&L (local)", "P&L (USD)", "Contract", "Trade id"} <= set(names)
    finally:
        conn.close()


def test_the_key_date_reads_prompt_for_lme_alone_and_expiry_prompt_for_both(strict_marks):
    conn = _db()
    try:
        _lme(conn, "L1", "LME:CA", 100.0, 9_800.0, "2026-09-16", 9_900.0)
        assert "Prompt" in _names(_futures_table(conn))
    finally:
        conn.close()
    conn = _lme_book()
    try:
        assert "Expiry / prompt" in _names(_futures_table(conn))
    finally:
        conn.close()
    assert blotter.futures_key_date_label(None) == "Expiry / prompt"
    # the filter refresh and the empty table keep the both-products label, never a stale one
    assert blotter.scope_columns("futures")[1]["settle_date"] == "Expiry / prompt"
    assert blotter.scope_columns("total")[1]["settle_date"] == "Expiry / value date"
    assert blotter.scope_header_tips("total") == {}


def test_a_settled_ticket_says_on_hover_the_date_of_the_price_it_was_frozen_at():
    conn = _db()
    try:
        _lme(conn, "L9", "LME:NI", 12.0, 15_420.0, "2026-06-17")
        conn.execute("INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
                     "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, "
                     "frozen_at, note) VALUES ('L9','LME:NI','LME_FWD','USD','2026-06-17',-1200,0,'SPOT',1,"
                     "'2026-06-16','BBG_BDH',-1200,'2026-06-18T00:00:00','')")
        conn.commit()
        table = _futures_table(conn)
        rec, tip = next((r, t) for r, t in zip(table.data, table.tooltip_data) if r["trade_id"] == "L9")
        assert rec["settle_date"] == "2026-06-17"
        assert tip["settle_date"]["value"] == ("settled: frozen at the official price of 2026-06-16, "
                                               "the last on or before this date")
    finally:
        conn.close()
    assert blotter._key_date_tip("OPEN", "2026-12-16") == ""        # an open row's mark_date is the key date itself
    assert blotter._key_date_tip("SETTLED", "") == ""


def test_the_row_panel_words_the_mark_date_for_what_it_is():
    """value_book's mark_date on an open row is the key the mark is read at, never the close it
    came from; on a settled row it is the frozen price's date; with no mark, the reason."""
    fut = {"mark": 70.0, "mark_source": "BBG_BDH", "mark_date": "2026-11-19", "status": "OPEN", "product": "FUTURE"}
    assert blotter.mark_used_text(fut) == "70.0 (BBG_BDH; keyed on the expiry 2026-11-19)"
    lme = {**fut, "product": "LME_FWD", "mark_date": "2026-09-16", "mark_source": "BBG_BFXFORWARD"}
    assert blotter.mark_used_text(lme) == "70.0 (BBG_BFXFORWARD; keyed on the prompt 2026-09-16)"
    fwd = {**fut, "product": "FX_FWD", "mark_date": "2026-09-20"}
    assert "keyed on the value date 2026-09-20" in blotter.mark_used_text(fwd)
    settled = {**fut, "status": "SETTLED", "mark_date": "2026-06-16"}
    assert blotter.mark_used_text(settled) == "70.0 (BBG_BDH; frozen at the official price of 2026-06-16)"
    frozen = {**settled, "mark": float("nan"), "reason": ""}
    assert blotter.mark_used_text(frozen) == (f"n/a ({blotter.SETTLED_MARK_REASON}; "
                                              "frozen at the official price of 2026-06-16)")
    unpriced = {**fut, "mark": float("nan"), "mark_date": "", "reason": "no official FUTURE_PX"}
    assert blotter.mark_used_text(unpriced) == "n/a (no official FUTURE_PX)"
    assert "dated" not in blotter.mark_used_text(fut)


def test_the_row_panel_on_a_real_future_reads_keyed_on_the_expiry(strict_marks):
    conn = _book()
    try:
        df = blotter.scope_df(conn, "futures", AS_OF)
        panel = blotter.row_expand_panel(conn, "F1", df[df["trade_id"] == "F1"].iloc[0])
        text = next(n.children for n in _walk(panel) if getattr(n, "className", "") == "blotter-row-marks")
        assert "keyed on the expiry 2026-11-19" in text and "dated" not in text
    finally:
        conn.close()
