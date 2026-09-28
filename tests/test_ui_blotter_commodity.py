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


# --------------------------------------------------------------------------- Futures sub-tab
def _futures_table(conn):
    layout = blotter.scope_layout("futures", conn, AS_OF, with_notices=False)
    return next(t for t in _tables(layout) if t.id == "blotter-datatable-futures")


# --------------------------------------------------------------------------- P&L by asset class
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
