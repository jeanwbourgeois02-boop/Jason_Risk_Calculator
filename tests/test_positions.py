"""engine/ladder/positions.py: the Total book's Positions block (user, 2026-09-22)."""
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
        ("ESZ6 Index", "FUTURE", "ES", "USD", 50, 0, "ESZ6 Index", "2026-12-18"),
        ("SPX/E261016P7615", "EQ_OPTION", "SPX", "USD", 100, 0, "SPX Index", "2026-10-16"),
        ("SPX Index", "INDEX", "SPX", "USD", 1, 0, "SPX Index", "9999-12-31"),
        ("IRSOIS-USD-1", "IRS", "USD", "USD", 1, 0, "", "2031-09-01"),
        ("EURUSD111926C-1", "FX_OPTION", "EUR", "USD", 1, 0, "EURUSD111926C-1", "2026-11-19"),
        ("EURUSD", "FX", "EUR", "USD", 1, 0, "EURUSD Curncy", "9999-12-31"),
    ])
    trades = [
        ("j1", "USDJPY", "FX_FWD", 1e6, 150.0), ("g1", "XAUUSD", "FX_FWD", 100.0, 4300.0),
        ("f1", "ESZ6 Index", "FUTURE", -34.0, 7670.0), ("f2", "ESZ6 Index", "FUTURE", 5.0, 7644.0),
        ("p1", "SPX/E261016P7615", "EQ_OPTION", 15.0, 121.5), ("p2", "SPX/E261016P7615", "EQ_OPTION", 15.0, 128.0),
        ("s1", "IRSOIS-USD-1", "IRS", 1e7, 0.035), ("o1", "EURUSD111926C-1", "FX_OPTION", 2e6, 0.01),
    ]
    for tid, inst, product, qty, px in trades:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", inst, product, tid, "2026-09-01", qty, px, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("j1", 1, "FX_NEAR", "USD", 1e6, "2026-09-01", "2026-10-20", 150.0, 1),
        ("j1", 2, "FX_NEAR", "JPY", -150e6, "2026-09-01", "2026-10-20", 150.0, 1),
        ("g1", 1, "FX_NEAR", "XAU", 100.0, "2026-09-01", "2026-10-20", 4300.0, 1),
        ("g1", 2, "FX_NEAR", "USD", -430000.0, "2026-09-01", "2026-10-20", 4300.0, 1),
        ("f1", 1, "NOTIONAL", "USD", -34 * 50 * 7670.0, "2026-09-01", "2026-12-18", 0, 0),
        ("f2", 1, "NOTIONAL", "USD", 5 * 50 * 7644.0, "2026-09-01", "2026-12-18", 0, 0),
        ("p1", 1, "NOTIONAL", "SPX", 15.0, "2026-09-01", "2026-10-16", 0, 0),
        ("p2", 1, "NOTIONAL", "SPX", 15.0, "2026-09-01", "2026-10-16", 0, 0),
        ("s1", 1, "FIXED", "USD", -1e7, "2026-09-01", "2031-09-01", 0.035, 1),
        ("s1", 2, "FLOAT", "USD", 1e7, "2026-09-01", "2031-09-01", 0.0, 1),
        ("o1", 1, "NOTIONAL", "EUR", 2e6, "2026-09-01", "2026-11-19", 0, 0),
    ])
    conn.commit()
    return conn


def _mark(conn, inst, settle, mt, value, source):
    conn.execute("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", (AS_OF, inst, settle, mt, value, source, SNAP))
    conn.commit()


def test_positions_add_the_spx_option_delta_to_the_es_futures_and_sum_dv01_and_option_delta():
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    _mark(conn, "XAUUSD", AS_OF, "SPOT", 4350.0, "BBG_BFXFORWARD")
    _mark(conn, "EURUSD", AS_OF, "SPOT", 1.10, "BBG_BFXFORWARD")
    _mark(conn, "ESZ6 Index", "2026-12-18", "FUTURE_PX", 7700.0, "BBG_BDH")
    _mark(conn, "SPX Index", AS_OF, "SPOT", 7650.0, "BBG_BFXFORWARD")
    _mark(conn, "SPX/E261016P7615", "2026-10-16", "DELTA", -0.40, "QL_OPTIONS_PRICER")
    _mark(conn, "IRSOIS-USD-1", "2031-09-01", "DV01_USD", -4200.0, "QL_PRICER")
    _mark(conn, "EURUSD111926C-1", "2026-11-19", "DELTA", 0.55, "QL_OPTIONS_PRICER")
    pos = positions.book_positions(conn, AS_OF)

    # ES futures: -29 contracts x 50 = -1,450 index units at 7,700; SPX puts: 30 x -0.40 x 100 = -1,200 index units at 7,650
    eq = pos["equity_index"]
    assert eq["index_units"] == -1450.0 - 1200.0
    assert eq["usd_delta"] == -1450.0 * 7700.0 + -1200.0 * 7650.0
    assert eq["es_contracts"] == (-1450.0 - 1200.0) / 50 and eq["missing"] == []
    assert [(l["label"], l["kind"], l["contracts"], l["index_units"]) for l in eq["lines"]] == [
        ("ESZ6 Index", "future", -29.0, -1450.0), ("SPX/E261016P7615", "option", 30.0, -1200.0)]

    assert pos["rates"] == {"swaps": 1, "by_ccy": {"USD": -4200.0}, "dv01_usd": -4200.0, "missing": [], "reason": ""}
    # FX option: 2m EUR x 0.55 = 1.1m EUR of delta, at 1.10 = $1.21m
    assert pos["fx_options"]["by_pair"] == {"EURUSD": 2e6 * 0.55 * 1.10} and pos["fx_options"]["options"] == 1
    # FX, the header's own number: long 1m USD against JPY (+1m, + = long USD) and the option's
    # EUR delta (1.1m EUR = $1.21m, short USD against it) net to -210,000; gross adds them.
    # Gold is on its own line, never in the FX total.
    fx = pos["fx"]
    assert fx["available"] and fx["net_usd"] == pytest.approx(1_000_000.0 - 1_210_000.0)
    assert fx["gross_usd"] == pytest.approx(1_000_000.0 + 1_210_000.0)
    # the key table: one row per currency, largest |USD delta| first, USD last; a metal row flagged
    rows = {c["ccy"]: c for c in fx["by_ccy"]}
    assert [c["ccy"] for c in fx["by_ccy"]] == ["EUR", "JPY", "XAU", "USD"]
    assert (rows["JPY"]["local_delta"], rows["JPY"]["usd_delta"], rows["JPY"]["quoted"], rows["JPY"]["label"]) == \
        (-150e6, pytest.approx(-1_000_000.0), 150.0, "USDJPY")
    assert (rows["EUR"]["local_delta"], rows["EUR"]["usd_delta"]) == (pytest.approx(1.1e6), pytest.approx(1_210_000.0))
    assert rows["XAU"]["metal"] and rows["XAU"]["usd_delta"] == 435000.0 and not rows["JPY"]["metal"]
    assert rows["USD"]["quoted"] == 1.0 and rows["USD"]["reason"] == ""
    assert [(m["ccy"], m["units"], m["usd_delta"]) for m in fx["metals"]] == [("XAU", 100.0, 435000.0)]


def test_positions_name_every_missing_mark_and_never_take_it_as_zero():
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    _mark(conn, "ESZ6 Index", "2026-12-18", "FUTURE_PX", 7700.0, "BBG_BDH")   # option: no DELTA; index: no level
    pos = positions.book_positions(conn, AS_OF)
    eq = pos["equity_index"]
    assert eq["index_units"] == -1450.0 and eq["usd_delta"] == -1450.0 * 7700.0     # the future alone, the option named
    assert eq["missing"] == ["SPX/E261016P7615: no DELTA mark on 2026-09-22 (the Greeks are written by Pull Bloomberg now)"]
    assert math.isnan(pos["rates"]["dv01_usd"]) and pos["rates"]["missing"] == ["IRSOIS-USD-1: no DV01_USD on 2026-09-22"]
    assert math.isnan(pos["fx_options"]["usd_delta"]) and pos["fx_options"]["missing"][0].startswith("o1: no official DELTA mark")
    assert pos["fx"]["metals"][0]["ccy"] == "XAU" and pos["fx"]["metals"][0]["reason"]   # no gold price: says so


def test_a_stored_value_that_is_not_a_number_blanks_one_block_with_its_reason_not_the_table():
    conn = _book()
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = 'j1'")
    conn.commit()
    _mark(conn, "ESZ6 Index", "2026-12-18", "FUTURE_PX", 7700.0, "BBG_BDH")
    pos = positions.book_positions(conn, AS_OF)
    assert not pos["fx"]["available"] and "24-Jul" in pos["fx"]["reason"] and pos["fx"]["metals"] == []
    assert pos["equity_index"]["usd_delta"] == -1450.0 * 7700.0            # the other blocks still compute


def test_positions_table_renders_the_lines_with_reasons_on_hover():
    from ui.tabs.blotter import positions_rows
    conn = _book()
    _mark(conn, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD")
    records, tips = positions_rows(conn, AS_OF)
    by_label = {r["position"].strip(): r for r in records}
    assert by_label["JPY"]["rate"] == "USDJPY 150.00" and by_label["JPY"]["units"] == "(150,000,000)"
    assert by_label["JPY"]["usd"] == "(1,000,000)" and by_label["JPY"]["kind"] == "ccy"
    assert "EUR" not in by_label                                               # the option has no DELTA mark: no row, named below
    assert by_label["XAU"]["detail"] == "metal, not in the FX net" and by_label["XAU"]["usd"] == "n/a"   # no gold price
    assert by_label["FX net USD delta (+ = long USD)"]["usd"] == "1,000,000"
    assert by_label["FX options delta (USD)"]["usd"] == "n/a"
    assert by_label["Equity index delta (ES futures + SPX options)"]["usd"] == "n/a"
    assert by_label["ESZ6 Index"]["units"] == "-1,450.00" and by_label["ESZ6 Index"]["usd"] == "n/a"
    assert by_label["Rates DV01 (USD, +1bp parallel)"]["usd"] == "n/a"
    tip = tips[[r["position"].strip() for r in records].index("Rates DV01 (USD, +1bp parallel)")]
    assert tip["usd"]["value"] == "IRSOIS-USD-1: no DV01_USD on 2026-09-22"
