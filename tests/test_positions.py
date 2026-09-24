"""engine/ladder/positions.py: the Total book's Positions block (user, 2026-09-22).

Since 2026-09-24 (commodity conversion Phase 2) the block is FX and FX options only: the
equity index line and the rates DV01 left with the macro trader's products, and every
currency is at its official spot (no 1M NDF rate)."""
import math

import pytest

from data.ingest import schema
from engine.ladder import positions

AS_OF = "2026-09-22"
SNAP = f"{AS_OF}T15:00:00-04:00"


def _book():
    conn = schema.connect()
    conn.executemany("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)", [
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
        ("XAUUSD", "FX", "XAU", "USD", 1, 0, "XAUUSD Curncy", "9999-12-31"),
        ("CLZ6 Comdty", "FUTURE", "NYMEX:CL", "USD", 1000, 0, "CLZ6 Comdty", "2026-11-19"),
        ("EURUSD111926C-1", "FX_OPTION", "EUR", "USD", 1, 0, "EURUSD111926C-1", "2026-11-19"),
        ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
    ])
    trades = [
        ("j1", "USDJPY", "FX_FWD", 1e6, 150.0), ("g1", "XAUUSD", "FX_FWD", 100.0, 4300.0),
        ("f1", "CLZ6 Comdty", "FUTURE", -3.0, 70.0), ("o1", "EURUSD111926C-1", "FX_OPTION", 2e6, 0.01),
    ]
    for tid, inst, product, qty, px in trades:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", inst, product, tid, "2026-09-01", qty, px, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-09-01", "2026-10-20", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-09-01", "2026-10-20", 150.0, 1),
        ("g1", 1, "FX_NEAR", "XAU", 100.0, "2026-09-01", "2026-10-20", 4300.0, 1),
        ("g1", 2, "FX_NEAR", "USD", -430000.0, "2026-09-01", "2026-10-20", 4300.0, 1),
        ("f1", 1, "NOTIONAL", "USD", -3 * 1000 * 70.0, "2026-09-01", "2026-11-19", 0, 0),
        ("o1", 1, "NOTIONAL", "EUR", 2e6, "2026-09-01", "2026-11-19", 0, 0),
    ])
    conn.commit()
    return conn


def _mark(conn, inst, settle, mt, value, source, as_of=AS_OF):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (as_of, inst, settle, mt, value, source, SNAP))
    conn.commit()


def test_book_positions_is_the_fx_fx_options_and_commodities_blocks_only():
    pos = positions.book_positions(_book(), AS_OF)
    assert set(pos) == {"fx", "fx_options", "commodities"}                 # no equity_index, no rates
    assert not hasattr(positions, "equity_index_positions") and not hasattr(positions, "rates_positions")


def test_positions_sum_the_fx_delta_and_the_option_delta():
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    _mark(conn, "XAUUSD", AS_OF, "SPOT", 4350.0, "BBG_BFXFORWARD")
    _mark(conn, "EURUSD", AS_OF, "SPOT", 1.10, "BBG_BFXFORWARD")
    _mark(conn, "CLZ6 Comdty", "2026-11-19", "FUTURE_PX", 71.0, "BBG_BDH")
    _mark(conn, "EURUSD111926C-1", "2026-11-19", "DELTA", 0.55, "QL_OPTIONS_PRICER")
    pos = positions.book_positions(conn, AS_OF)

    # FX option: 2m EUR x 0.55 = 1.1m EUR of delta, at 1.10 = $1.21m
    assert pos["fx_options"]["by_pair"] == {"EURUSD": 2e6 * 0.55 * 1.10} and pos["fx_options"]["options"] == 1
    # FX, the header's own number: long 1m USD against JPY (+1m, + = long USD) and the option's
    # EUR delta (1.1m EUR = $1.21m, short USD against it) net to -210,000; gross adds them.
    # Gold is on its own line, never in the FX total; the oil future is not an FX position.
    fx = pos["fx"]
    assert fx["available"] and fx["net_usd"] == pytest.approx(1_000_000.0 - 1_210_000.0)
    assert fx["gross_usd"] == pytest.approx(1_000_000.0 + 1_210_000.0)
    # the key table: one row per currency, largest |USD delta| first, USD last; a metal row flagged
    rows = {c["ccy"]: c for c in fx["by_ccy"]}
    assert [c["ccy"] for c in fx["by_ccy"]] == ["EUR", "JPY", "XAU", "USD"]
    assert (rows["JPY"]["local_delta"], rows["JPY"]["usd_delta"], rows["JPY"]["quoted"], rows["JPY"]["label"]) == \
        (-150e6, pytest.approx(-1_000_000.0), 150.0, "USDJPY")
    assert (rows["EUR"]["local_delta"], rows["EUR"]["usd_delta"]) == (pytest.approx(1.1e6), pytest.approx(1_210_000.0))
    assert rows["EUR"]["label"] == "EURUSD" and rows["XAU"]["label"] == "XAUUSD"
    assert rows["XAU"]["metal"] and rows["XAU"]["usd_delta"] == 435000.0 and not rows["JPY"]["metal"]
    assert rows["USD"]["quoted"] == 1.0 and rows["USD"]["label"] == "USD" and rows["USD"]["reason"] == ""
    assert [(m["ccy"], m["units"], m["usd_delta"]) for m in fx["metals"]] == [("XAU", 100.0, 435000.0)]


def test_positions_name_every_missing_mark_and_never_take_it_as_zero():
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    pos = positions.book_positions(conn, AS_OF)
    assert math.isnan(pos["fx_options"]["usd_delta"]) and pos["fx_options"]["missing"][0].startswith("o1: no official DELTA mark")
    assert pos["fx"]["metals"][0]["ccy"] == "XAU" and pos["fx"]["metals"][0]["reason"]   # no gold price: says so
    rows = {c["ccy"]: c for c in pos["fx"]["by_ccy"]}
    assert rows["XAU"]["label"] == "" and math.isnan(rows["XAU"]["usd_delta"]) and rows["XAU"]["reason"]


def test_an_option_delta_with_no_spot_to_convert_it_is_named_not_zero():
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    _mark(conn, "EURUSD111926C-1", "2026-11-19", "DELTA", 0.55, "QL_OPTIONS_PRICER")
    opt = positions.book_positions(conn, AS_OF)["fx_options"]
    assert opt["by_pair"] == {} and math.isnan(opt["usd_delta"])
    # the Ladder's own option records name the missing pair spot (exposure_adapter's reason)
    assert opt["missing"] == [f"o1: no official SPOT for EURUSD on {AS_OF} (option delta not converted)"]


def test_a_stored_value_that_is_not_a_number_blanks_one_block_with_its_reason_not_the_table():
    conn = _book()
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 'j1'")
    conn.commit()
    _mark(conn, "EURUSD", AS_OF, "SPOT", 1.10, "BBG_BFXFORWARD")
    _mark(conn, "EURUSD111926C-1", "2026-11-19", "DELTA", 0.55, "QL_OPTIONS_PRICER")
    pos = positions.book_positions(conn, AS_OF)
    assert not pos["fx"]["available"] and "24-Jul" in pos["fx"]["reason"] and pos["fx"]["metals"] == []
    assert pos["fx_options"]["by_pair"] == {"EURUSD": 2e6 * 0.55 * 1.10}     # the other block still computes


# --- a former NDF currency is at its official spot (2026-09-24: the 1M NDF rate left) ------


def _krw_forward(conn):
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('USDKRW','FX','USD','KRW',1,0,'USDKRW Curncy','9999-12-31')")
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("k1", "XLSX", "USDKRW", "FX_FWD", "k1", "2026-09-01", 1e6, 1400.0, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("k1", 1, "FX_NEAR", "USD", 1e6, "2026-09-01", "2026-10-20", 1400.0, 1),
        ("k1", 2, "FX_NEAR", "KRW", -1.4e9, "2026-09-01", "2026-10-20", 1400.0, 1)])
    conn.commit()


def test_a_krw_forward_is_at_the_usdkrw_spot_never_a_1m_ndf_price():
    conn = _book()
    _krw_forward(conn)
    for pair, spot in (("USDJPY", 150.0), ("XAUUSD", 4350.0), ("EURUSD", 1.10), ("USDKRW", 1400.0)):
        _mark(conn, pair, AS_OF, "SPOT", spot, "BBG_BFXFORWARD")
    _mark(conn, "USDKRW", AS_OF, "NDF_1M", 1380.0, "BBG_BFXFORWARD")        # on file, never read
    fx = positions.book_positions(conn, AS_OF)["fx"]
    krw = {c["ccy"]: c for c in fx["by_ccy"]}["KRW"]
    assert (krw["label"], krw["quoted"], krw["local_delta"]) == ("USDKRW", 1400.0, -1.4e9)
    assert krw["usd_delta"] == pytest.approx(-1.4e9 / 1400.0) and krw["reason"] == ""
    # long 1m USD against JPY and 1m against KRW; the option has no DELTA mark (named in fx_options)
    assert fx["available"] and fx["net_usd"] == pytest.approx(2_000_000.0)


def test_a_krw_forward_with_no_spot_is_named_with_the_spot_reason_even_with_a_1m_price():
    conn = _book()
    _krw_forward(conn)
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    _mark(conn, "USDKRW", AS_OF, "NDF_1M", 1380.0, "BBG_BFXFORWARD")
    fx = positions.book_positions(conn, AS_OF)["fx"]
    assert not fx["available"] and math.isnan(fx["net_usd"]) and math.isnan(fx["gross_usd"])
    assert fx["reason"] == f"no official SPOT for {AS_OF}: KRW"
    krw = {c["ccy"]: c for c in fx["by_ccy"]}["KRW"]
    assert krw["label"] == "" and math.isnan(krw["usd_delta"]) and krw["reason"]


def test_positions_table_renders_the_lines_with_reasons_on_hover():
    from ui.tabs.blotter import positions_rows
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    records, tips = positions_rows(conn, AS_OF)
    by_label = {r["position"].strip(): r for r in records}
    assert by_label["JPY"]["rate"] == "USDJPY 150.00" and by_label["JPY"]["units"] == -150_000_000
    assert by_label["JPY"]["usd"] == pytest.approx(-1_000_000) and by_label["JPY"]["kind"] == "ccy"
    assert "EUR" not in by_label                                               # the option has no DELTA mark: no row, named below
    assert by_label["XAU"]["detail"] == "metal, not in the FX net" and by_label["XAU"]["usd"] is None   # no gold price
    assert by_label["FX net USD delta (+ = long USD)"]["usd"] == pytest.approx(1_000_000)
    assert by_label["FX options delta (USD)"]["usd"] is None
    tip = tips[[r["position"].strip() for r in records].index("FX options delta (USD)")]
    assert tip["usd"]["value"].startswith("o1: no official DELTA mark")


# --- commodities: curve-positions' figures by sector, then by commodity (Phase 3) ------------
#
# The book's futures: energy = WTI CLZ6 -3 lots (the base fixture's) at 71 and Brent COF27 +2 at
# 72 (USD, 1000 bbl); metals = COMEX copper HGZ26 +3 at 450 US cents/lb (multiplier 250) and
# SHFE copper CUZ26 -2 at 80,000 CNY/t (multiplier 5), converted at USDCNY 7.2.
CL, BRENT, HG, CU = "CLZ6 Comdty", "COF27 Comdty", "HGZ26 Comdty", "CUZ26 Comdty"
CL_USD, BRENT_USD = -3 * 1000 * 71.0, 2 * 1000 * 72.0
HG_USD, CU_USD = 3 * 250 * 450.0, -2 * 5 * 80_000.0 / 7.2


def _fx_spots(conn):
    for pair, spot in (("USDJPY", 150.0), ("XAUUSD", 4350.0), ("EURUSD", 1.10)):
        _mark(conn, pair, AS_OF, "SPOT", spot, "BBG_BFXFORWARD")
    _mark(conn, "EURUSD111926C-1", "2026-11-19", "DELTA", 0.55, "QL_OPTIONS_PRICER")
    conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                 "bbg_ticker, expiry_date) VALUES ('USDCNY','FX','USD','CNY',1,0,'USDCNY Curncy','9999-12-31')")
    _mark(conn, "USDCNY", AS_OF, "SPOT", 7.2, "BBG_BFXFORWARD")


def _add_futures(conn, prices=True, hg_price=True):
    from data.contracts import get_root
    for tid, inst, root_id, expiry, lots, fill in (
            ("b1", BRENT, "ICE:B", "2026-11-30", 2.0, 70.0),
            ("h1", HG, "COMEX:HG", "2026-12-29", 3.0, 440.0),
            ("c1", CU, "SHFE:CU", "2026-12-15", -2.0, 79_000.0)):
        root = get_root(root_id)
        conn.execute("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
                     (inst, "FUTURE", root_id, root.currency, root.multiplier, 0, inst, expiry))
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", inst, "FUTURE", tid, "2026-09-01", lots, fill, "acc", "cp", "", "t", "d", ""))
        conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                     (tid, 1, "NOTIONAL", root.currency, lots * root.multiplier * fill, "2026-09-01", expiry, fill, 0))
    conn.commit()
    if prices:
        _mark(conn, CL, "2026-11-19", "FUTURE_PX", 71.0, "BBG_BDH")
        _mark(conn, BRENT, "2026-11-30", "FUTURE_PX", 72.0, "BBG_BDH")
        _mark(conn, CU, "2026-12-15", "FUTURE_PX", 80_000.0, "BBG_BDH")
        if hg_price:
            _mark(conn, HG, "2026-12-29", "FUTURE_PX", 450.0, "BBG_BDH")


def _two_sector_book(**kw):
    conn = _book()
    _fx_spots(conn)
    _add_futures(conn, **kw)
    return conn


def _no_cl(conn):
    conn.execute("DELETE FROM trade_legs WHERE trade_id = 'f1'")
    conn.execute("DELETE FROM trades WHERE trade_id = 'f1'")
    conn.commit()
    return conn


def test_commodities_by_sector_then_commodity_in_order_with_the_totals():
    com = positions.book_positions(_two_sector_book(), AS_OF)["commodities"]
    assert com["available"] and com["reason"] == "" and com["missing"] == []
    # metals' gross (337,500 + 111,111) beats energy's (213,000 + 144,000): metals first
    assert [s["sector"] for s in com["sectors"]] == ["metals", "energy"]
    metals, energy = com["sectors"]
    # inside a sector by |net USD|: HG before SHFE copper, WTI (short 213k) before Brent (long 144k)
    assert [c["root_id"] for c in metals["commodities"]] == ["COMEX:HG", "SHFE:CU"]
    assert [c["root_id"] for c in energy["commodities"]] == ["NYMEX:CL", "ICE:B"]
    hg, cu = metals["commodities"]
    assert (hg["net_lots"], hg["gross_lots"], hg["net_units"], hg["unit"]) == (3.0, 3.0, 75_000.0, "lb")
    assert hg["net_usd"] == pytest.approx(HG_USD) and hg["gross_usd"] == pytest.approx(HG_USD)
    assert (cu["currency"], cu["exchange"], cu["net_units"]) == ("CNY", "SHFE", -10.0)
    assert cu["net_usd"] == pytest.approx(CU_USD) and cu["gross_usd"] == pytest.approx(-CU_USD)
    assert metals["net_usd"] == pytest.approx(HG_USD + CU_USD) and metals["gross_usd"] == pytest.approx(HG_USD - CU_USD)
    assert energy["net_usd"] == pytest.approx(CL_USD + BRENT_USD)
    assert energy["gross_usd"] == pytest.approx(-CL_USD + BRENT_USD)
    assert metals["missing"] == [] and metals["reason"] == "" and energy["missing"] == []
    # the book: the sectors summed
    assert com["net_usd"] == pytest.approx(CL_USD + BRENT_USD + HG_USD + CU_USD)
    assert com["gross_usd"] == pytest.approx(-CL_USD + BRENT_USD + HG_USD - CU_USD)


def test_commodities_are_curve_positions_figures_never_recomputed():
    from engine.curve import curve_positions
    conn = _two_sector_book()
    cp = curve_positions(conn, AS_OF)
    com = positions.book_positions(conn, AS_OF)["commodities"]
    for s in com["sectors"]:
        assert (s["net_usd"], s["gross_usd"]) == (cp["by_sector"][s["sector"]]["net_usd"],
                                                  cp["by_sector"][s["sector"]]["gross_usd"])
        for c in s["commodities"]:
            mine = cp["by_commodity"][c["root_id"]]
            assert all(c[k] == mine[k] for k in ("net_lots", "gross_lots", "net_units", "net_usd", "gross_usd"))


def test_a_missing_price_leaves_the_commodity_na_with_its_reason_and_out_of_the_sums():
    com = positions.book_positions(_two_sector_book(hg_price=False), AS_OF)["commodities"]
    # metals' known gross is SHFE copper's alone (111k) < energy's 357k: energy first now
    assert [s["sector"] for s in com["sectors"]] == ["energy", "metals"]
    metals = com["sectors"][1]
    assert [c["root_id"] for c in metals["commodities"]] == ["SHFE:CU", "COMEX:HG"]     # n/a last
    hg = metals["commodities"][1]
    assert hg["net_usd"] is None and hg["gross_usd"] is None and hg["net_lots"] == 3.0    # lots kept, never zero
    assert hg["missing"] == [HG] and "no FUTURE_PX" in hg["reason"]
    assert metals["net_usd"] == pytest.approx(CU_USD) and metals["gross_usd"] == pytest.approx(-CU_USD)
    assert metals["missing"] == ["COMEX:HG"]
    assert metals["reason"].startswith("excludes 1 of 2 commodities with no USD figure: ")
    assert "COMEX:HG" in metals["reason"] and "no FUTURE_PX" in metals["reason"]
    assert com["missing"] == ["COMEX:HG"] and com["reason"].startswith("excludes 1 of 4 commodities")
    assert com["net_usd"] == pytest.approx(CL_USD + BRENT_USD + CU_USD)
    assert com["gross_usd"] == pytest.approx(-CL_USD + BRENT_USD - CU_USD)


def test_a_sector_with_nothing_priced_is_none_not_zero_and_sorts_last():
    conn = _two_sector_book(hg_price=False)
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDCNY'")                    # SHFE copper: no spot
    conn.commit()
    com = positions.book_positions(conn, AS_OF)["commodities"]
    energy, metals = com["sectors"]
    assert metals["sector"] == "metals" and metals["net_usd"] is None and metals["gross_usd"] is None
    assert sorted(metals["missing"]) == ["COMEX:HG", "SHFE:CU"] and "excludes 2 of 2" in metals["reason"]
    assert "no SPOT for CNY" in {c["root_id"]: c for c in metals["commodities"]}["SHFE:CU"]["reason"]
    assert com["net_usd"] == pytest.approx(CL_USD + BRENT_USD)                          # energy alone, named
    assert com["reason"].startswith("excludes 2 of 4 commodities")


def test_currency_exposure_is_passed_through():
    com = positions.book_positions(_two_sector_book(), AS_OF)["commodities"]
    # SHFE copper: short 2 lots x 5 t x (80,000 - 79,000) = -10,000 CNY of P&L held in CNY
    assert set(com["currency_exposure"]) == {"CNY"}
    cny = com["currency_exposure"]["CNY"]
    assert set(cny) == {"pnl_local", "pnl_usd", "reason"}
    assert cny["pnl_local"] == pytest.approx(-10_000.0) and cny["pnl_usd"] == pytest.approx(-10_000.0 / 7.2)
    assert cny["reason"] == ""


def test_no_open_commodity_futures_is_none_with_the_note():
    com = positions.book_positions(_no_cl(_book()), AS_OF)["commodities"]
    assert com["available"] and com["sectors"] == [] and com["net_usd"] is None and com["gross_usd"] is None
    assert com["reason"] == com["note"] == f"no open commodity futures on {AS_OF}"


def test_the_commodities_block_keeps_its_empty_shape_when_it_cannot_be_computed(monkeypatch):
    import engine.curve

    def boom(conn, as_of):
        raise RuntimeError("universe broken")
    monkeypatch.setattr(engine.curve, "curve_positions", boom)
    pos = positions.book_positions(_two_sector_book(), AS_OF)
    assert pos["commodities"] == {**positions._EMPTY["commodities"],
                                  "reason": "could not be computed (RuntimeError: universe broken)"}
    assert pos["fx"]["available"] and pos["fx_options"]["by_pair"]                   # the others still compute


def test_fx_net_and_gross_are_unchanged_by_adding_futures():
    conn = _no_cl(_book())
    _fx_spots(conn)
    before = positions.book_positions(conn, AS_OF)
    _add_futures(conn)                                        # USD and CNY futures, all priced
    after = positions.book_positions(conn, AS_OF)
    assert after["commodities"]["net_usd"] is not None       # the futures are there, in their own block
    assert after["fx"]["available"]
    assert (after["fx"]["net_usd"], after["fx"]["gross_usd"]) == (before["fx"]["net_usd"], before["fx"]["gross_usd"])
    assert after["fx"]["by_ccy"] == before["fx"]["by_ccy"] and after["fx"]["metals"] == before["fx"]["metals"]
    assert "CNY" not in {c["ccy"] for c in after["fx"]["by_ccy"]}                    # SHFE copper is not CNY delta
    assert after["fx_options"] == before["fx_options"]
