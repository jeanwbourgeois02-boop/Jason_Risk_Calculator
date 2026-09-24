"""commodity-stress: scenarios on the book's commodity positions (engine/stress/)."""
import datetime as dt
import sqlite3
import sys

import pytest

from data.contracts import get_root
from data.ingest.schema import create_schema
from engine.spreads import book_spreads
from engine.stress import ScenarioError, commodity_stress, load_scenarios, validate_scenarios

AS_OF = "2026-09-15"
S_CNY = 1 / 7.1


def _pos(root_id, contract_id, lots, price, expiry, s=1.0, factor=1.0, product="FUTURE", delta_usd="auto",
         reason=""):
    """A curve-positions row, figures as that lane gives them (delta = lots x delta_factor)."""
    root = get_root(root_id)
    local = lots * root.multiplier * price if price is not None else None
    usd = local * s if local is not None and s is not None else None
    dlots = lots * factor if factor is not None else None
    dusd = (dlots * root.multiplier * price * s if None not in (dlots, price, s) else None) \
        if delta_usd == "auto" else delta_usd
    option = product == "CMDTY_OPTION"
    return {"product": product, "root_id": root.root_id, "name": root.name, "sector": root.sector,
            "subsector": root.subsector, "exchange": root.exchange, "currency": root.currency,
            "contract_id": contract_id, "instrument_id": contract_id.split(" 20")[0] if product == "LME_FWD"
            else contract_id, "expiry": expiry, "lots": lots, "multiplier": root.multiplier, "price": price,
            "usd_per_unit": s, "notional_local": None if option else local, "notional_usd": None if option else usd,
            "delta_factor": factor, "delta_lots": dlots, "delta_usd": dusd, "note": "", "reason": reason}


def _book(rows, exposure=None):
    return {"as_of": AS_OF, "available": True, "rows": rows, "currency_exposure": exposure or {}, "reasons": []}


# Three roots, two currencies: WTI (USD), LME copper (USD), SHFE copper (CNY).
WTI = _pos("NYMEX:CL", "CLZ26 Comdty", 2, 70.0, "2026-11-20")               # 2 x 1000 x 70 = 140,000
LCU = _pos("LME:CA", "LPZ26 Comdty", -1, 9000.0, "2026-12-16")              # -1 x 25 x 9000 = -225,000
SCU = _pos("SHFE:CU", "CUZ26 Comdty", 3, 80000.0, "2026-12-15", s=S_CNY)    # 3 x 5 x 80000 / 7.1


def _one(out, name):
    hits = [s for s in out["scenarios"] if s["name"] == name]
    assert len(hits) == 1, [s["name"] for s in out["scenarios"]]
    return hits[0]


def _run(rows, scenario, **kw):
    out = commodity_stress(kw.pop("conn", None), AS_OF, [scenario], positions=_book(rows, kw.pop("exposure", None)),
                           **kw)
    return _one(out, scenario["name"])


ALL_DOWN_10 = {"name": "All -10%", "kind": "outright", "all": -0.10}


# ---- outright ------------------------------------------------------------------------------

def test_outright_on_three_roots_in_two_currencies():
    s = _run([WTI, LCU, SCU], ALL_DOWN_10)
    scu_usd = 3 * 5 * 80000.0 * S_CNY
    assert s["basis"] == "delta"
    assert s["total_usd"] == pytest.approx(-0.1 * (140000.0 - 225000.0 + scu_usd))
    roots = {r["root_id"]: r["pnl_usd"] for r in s["by_root"]}
    assert roots["NYMEX:CL"] == pytest.approx(-14000.0)
    assert roots["LME:CA"] == pytest.approx(22500.0)
    assert roots["SHFE:CU"] == pytest.approx(-0.1 * scu_usd)
    assert s["by_sector"]["energy"] == pytest.approx(-14000.0)
    assert s["by_sector"]["metals"] == pytest.approx(22500.0 - 0.1 * scu_usd)
    assert s["missing"] == [] and s["reason"] == ""


def test_most_specific_move_wins():
    sc = {"name": "Mixed", "kind": "outright", "sector": {"metals": -0.10},
          "subsector": {"copper": -0.05}, "root": {"SHFE:CU": -0.20}, "all": -0.01}
    s = _run([WTI, LCU, SCU], sc)
    assert {c["root_id"]: c["move"] for c in s["by_contract"]} == {"NYMEX:CL": -0.01, "LME:CA": -0.05,
                                                                   "SHFE:CU": -0.20}


def test_option_row_is_stressed_through_its_delta():
    # an option on WTI: no notional by design, 10 lots at DELTA 0.4, priced at its underlying
    opt = _pos("NYMEX:CL", "CLZ26C75 Comdty", 10, 70.0, "2026-11-16", factor=0.4, product="CMDTY_OPTION")
    assert opt["notional_usd"] is None
    s = _run([opt], ALL_DOWN_10)
    assert s["missing"] == []
    assert s["total_usd"] == pytest.approx(10 * 0.4 * 1000 * 70.0 * -0.10)
    assert s["by_contract"][0]["product"] == "CMDTY_OPTION" and s["by_contract"][0]["delta_lots"] == 4.0


def test_unpriced_option_is_named_in_missing():
    opt = _pos("NYMEX:CL", "CLZ26C75 Comdty", 10, 70.0, "2026-11-16", factor=None, product="CMDTY_OPTION",
               reason="no DELTA mark: the option has not been priced")
    s = _run([WTI, opt], ALL_DOWN_10)
    assert s["total_usd"] == pytest.approx(-14000.0)
    assert [m["contract_id"] for m in s["missing"]] == ["CLZ26C75 Comdty"]
    assert "no DELTA" in s["missing"][0]["reason"]


def test_averaging_contract_at_half_delta():
    tio = _pos("CME:TIO", "TIOU26 Comdty", 4, 100.0, "2026-09-30", factor=0.5)   # 4 x 500 t x 100 USD/t
    s = _run([tio], ALL_DOWN_10)
    assert s["total_usd"] == pytest.approx(0.5 * 4 * 500 * 100.0 * -0.10)
    assert s["total_usd"] == pytest.approx(0.5 * tio["notional_usd"] * -0.10)


def test_averaging_contract_through_curve_positions():
    conn = _db()
    _future(conn, "T1", "TIOU26 Comdty", "CME:TIO", "2026-09-30", 4, 100.0)
    _px(conn, "TIOU26 Comdty", "2026-09-30", 102.0)
    out = commodity_stress(conn, AS_OF, [ALL_DOWN_10])
    s = _one(out, "All -10%")
    (c,) = s["by_contract"]
    assert 0.0 < c["delta_lots"] < 4.0                       # inside its September pricing month
    assert s["total_usd"] == pytest.approx(c["delta_lots"] * 500 * 102.0 * -0.10)


def test_lme_forward_row_moves_with_metals():
    lme = _pos("LME:CA", "LME:CA 2026-12-16", 2, 9000.0, "2026-12-16", product="LME_FWD")
    s = _run([lme, WTI], {"name": "Metals -10%", "kind": "outright", "sector": {"metals": -0.10}})
    assert s["total_usd"] == pytest.approx(2 * 25 * 9000.0 * -0.10)
    assert [c["product"] for c in s["by_contract"]] == ["LME_FWD"]


# ---- curve and spread ----------------------------------------------------------------------

def test_curve_steepen_across_three_months():
    rows = [_pos("NYMEX:CL", "CLX26 Comdty", 1, 70.0, "2026-10-15"),    # inside a month: front
            _pos("NYMEX:CL", "CLK27 Comdty", 1, 68.0, "2027-03-22"),    # mid-curve: interpolated
            _pos("NYMEX:CL", "CLZ27 Comdty", -2, 66.0, "2027-11-19")]   # beyond a year: back
    s = _run(rows, {"name": "Steepen", "kind": "curve", "front": -0.05, "back": 0.01,
                    "front_months": 1, "back_months": 12})
    moves = {c["contract_id"]: c["move"] for c in s["by_contract"]}
    months_mid = (dt.date(2027, 3, 22) - dt.date(2026, 9, 15)).days / (365.25 / 12)
    expected_mid = -0.05 + (months_mid - 1) / 11 * (0.01 - -0.05)
    assert moves["CLX26 Comdty"] == -0.05 and moves["CLZ27 Comdty"] == 0.01
    assert moves["CLK27 Comdty"] == pytest.approx(expected_mid) and -0.05 < expected_mid < 0.01
    assert s["total_usd"] == pytest.approx(70000 * -0.05 + 68000 * expected_mid + -132000 * 0.01)


def test_spread_scenario_moves_one_group_against_another():
    rb = _pos("NYMEX:RB", "XBZ26 Comdty", 1, 2.0, "2026-11-30")
    ho = _pos("NYMEX:HO", "HOZ26 Comdty", 1, 2.5, "2026-11-30")
    cl = _pos("NYMEX:CL", "CLZ26 Comdty", -2, None, "2026-11-20", reason="no FUTURE_PX")
    s = _run([rb, ho, cl], {"name": "Crack", "kind": "spread",
                            "legs": [{"select": {"subsector": ["gasoline", "gasoil"]}, "move": -0.15},
                                     {"select": {"subsector": ["crude_oil"]}, "move": 0.0}]})
    assert s["total_usd"] == pytest.approx(-0.15 * (rb["delta_usd"] + ho["delta_usd"]))
    assert s["missing"] == []          # crude's leg does not move, so its missing price does not matter
    assert {c["root_id"]: c["pnl_usd"] for c in s["by_contract"]}["NYMEX:CL"] == 0.0


def test_spread_legs_equally_specific_are_ambiguous():
    s = _run([SCU], {"name": "Clash", "kind": "spread",
                     "legs": [{"select": {"exchange": ["SHFE"]}, "move": -0.05},
                              {"select": {"sector": ["metals"], "exchange": ["SHFE"]}, "move": 0.02}]})
    assert s["total_usd"] is None and s["missing"][0]["contract_id"] == "CUZ26 Comdty"
    assert "equally specific" in s["missing"][0]["reason"]


# ---- the book's spreads (spreads-engine's real output) -------------------------------------------

def _db():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _future(conn, tid, inst, root_id, expiry, lots, fill):
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root.root_id, root.currency, root.multiplier, inst, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": inst, "product": "FUTURE", "package_id": tid,
            "trade_date": "2026-09-01", "quantity": lots, "price": fill, "account": "ACC", "counterparty": "C",
            "strategy": "", "trader": "JB", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, "2026-09-01", expiry, fill))


def _px(conn, inst, expiry, value):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')", (AS_OF, inst, expiry, value))


def _crack_and_calendar():
    """A 3-2-1 crack (a processing template) and a WTI calendar 20 / -19 (one lot left over)."""
    conn = _db()
    for tid, inst, root, exp, lots, fill, px in (
            ("K1", "CLX26 Comdty", "NYMEX:CL", "2026-11-30", -3, 69.2, 70.0),
            ("K2", "XBX26 Comdty", "NYMEX:RB", "2026-11-30", 2, 205.4, 207.0),
            ("K3", "HOX26 Comdty", "NYMEX:HO", "2026-11-30", 1, 238.5, 240.0),
            ("W1", "CLF27 Comdty", "NYMEX:CL", "2027-01-29", 20, 70.0, 71.0),
            ("W2", "CLG27 Comdty", "NYMEX:CL", "2027-02-26", -19, 69.5, 70.2)):
        _future(conn, tid, inst, root, exp, lots, fill)
        _px(conn, inst, exp, px)
    return conn


def _spread(res, kind_word):
    hits = [s for s in res["by_spread"] if kind_word in (s["template"] or "calendar")]
    assert len(hits) == 1, res["by_spread"]
    return hits[0]


def test_by_spread_on_book_spreads_with_the_leftover():
    conn = _crack_and_calendar()
    spreads = book_spreads(conn, AS_OF)
    assert {s["family"] for s in spreads["spreads"]} == {"processing", "calendar"}
    s = _one(commodity_stress(conn, AS_OF, [ALL_DOWN_10], spreads=spreads), "All -10%")
    crack = _spread(s, "crack")
    assert crack["pnl_usd"] == pytest.approx(-0.1 * (-3 * 1000 * 70.0 + 2 * 420 * 207.0 + 1 * 420 * 240.0))
    assert crack["leftover_pnl_usd"] == 0.0
    cal = _spread(s, "calendar")
    assert cal["pnl_usd"] == pytest.approx(-0.1 * (20 * 1000 * 71.0 - 19 * 1000 * 70.2))
    assert cal["leftover"] == [{"root_id": "NYMEX:CL", "lots": 1.0, "pnl_usd": pytest.approx(-7100.0), "reason": ""}]
    assert cal["spread_pnl_usd"] == pytest.approx(19 * 1000 * (71.0 - 70.2) * -0.1)
    assert sum(x["pnl_usd"] for x in s["by_spread"]) == pytest.approx(s["total_usd"])


def test_curve_scenario_cannot_place_a_calendar_leftover():
    conn = _crack_and_calendar()
    sc = {"name": "Steepen", "kind": "curve", "front": -0.05, "back": 0.0}
    s = _one(commodity_stress(conn, AS_OF, [sc], spreads=book_spreads(conn, AS_OF)), "Steepen")
    cal = _spread(s, "calendar")
    assert cal["pnl_usd"] is not None and cal["leftover_pnl_usd"] is None
    assert "move differently" in cal["leftover"][0]["reason"]


def test_spread_scenario_selects_by_family_and_by_spread_id():
    conn = _crack_and_calendar()
    spreads = book_spreads(conn, AS_OF)
    crack_id = [s["spread_id"] for s in spreads["spreads"] if s["family"] == "processing"][0]
    fam = {"name": "Processing -10%", "kind": "spread",
           "legs": [{"select": {"family": ["processing"]}, "move": -0.10},
                    {"select": {"family": ["calendar"]}, "move": 0.0}]}
    by_id = {"name": "That crack -10%", "kind": "spread",
             "legs": [{"select": {"spread": [crack_id]}, "move": -0.10},
                      {"select": {"sector": ["energy"]}, "move": 0.0}]}
    out = commodity_stress(conn, AS_OF, [fam, by_id])       # spreads read from the database itself
    for name in ("Processing -10%", "That crack -10%"):
        s = _one(out, name)
        moved = {c["instrument_id"] for c in s["by_contract"] if c["move"]}
        assert moved == {"CLX26 Comdty", "XBX26 Comdty", "HOX26 Comdty"}
        assert s["total_usd"] == pytest.approx(_spread(s, "crack")["pnl_usd"])


# ---- fx ------------------------------------------------------------------------------------------

def test_cny_move_on_a_cny_future():
    exposure = {"CNY": {"pnl_local": 7500.0, "pnl_usd": 7500.0 * S_CNY, "contracts": ["CUZ26 Comdty"],
                        "missing": [], "reason": ""}}
    s = _run([WTI, SCU], {"name": "CNY -5%", "kind": "fx", "moves": {"CNY": -0.05}}, exposure=exposure)
    assert s["total_usd"] == pytest.approx(-0.05 * 7500.0 * S_CNY)
    (c,) = s["by_currency"]
    assert c["currency"] == "CNY" and c["pnl_local"] == 7500.0
    assert c["delta_usd"] == pytest.approx(SCU["delta_usd"])
    assert c["delta_usd_change"] == pytest.approx(-0.05 * SCU["delta_usd"])
    # a CNH move stands in for CNY on the futures when no CNY move is given
    s2 = _run([WTI, SCU], {"name": "CNH -5%", "kind": "fx", "moves": {"CNH": -0.05}}, exposure=exposure)
    assert s2["total_usd"] == pytest.approx(s["total_usd"])


# ---- missing -------------------------------------------------------------------------------------

def test_position_with_no_price_is_named_in_missing():
    conn = _db()
    _future(conn, "T1", "CLZ26 Comdty", "NYMEX:CL", "2026-11-20", 2, 70.0)
    out = commodity_stress(conn, AS_OF, [{"name": "Energy -20%", "kind": "outright", "sector": {"energy": -0.20}}])
    s = _one(out, "Energy -20%")
    assert s["total_usd"] is None
    assert [m["contract_id"] for m in s["missing"]] == ["CLZ26 Comdty"]
    assert "no FUTURE_PX" in s["missing"][0]["reason"]
    assert any("Energy -20%" in r for r in out["reasons"])


def test_missing_position_left_out_of_a_partial_total():
    cl = _pos("NYMEX:CL", "CLZ26 Comdty", 1, None, "2026-11-20", reason="no FUTURE_PX")
    s = _run([cl, LCU], ALL_DOWN_10)
    assert s["total_usd"] == pytest.approx(22500.0)
    assert [m["contract_id"] for m in s["missing"]] == ["CLZ26 Comdty"] and "excludes 1" in s["reason"]


# ---- replays -------------------------------------------------------------------------------------

NEG_WTI = {"name": "Negative WTI", "kind": "replay", "start": "2020-04-17", "end": "2020-04-20"}

_RESEARCH_DDL = """
CREATE TABLE instrument (
    instrument_id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT NOT NULL, subsector TEXT NOT NULL,
    exchange TEXT NOT NULL, country TEXT NOT NULL, exchange_code TEXT NOT NULL, bbg_root TEXT NOT NULL,
    bbg_yellow_key TEXT NOT NULL, bbg_verified INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL,
    contract_size REAL NOT NULL, size_unit TEXT NOT NULL, quote_unit TEXT NOT NULL,
    price_scale REAL NOT NULL DEFAULT 1.0, vat_rate REAL NOT NULL DEFAULT 0.0,
    active_months TEXT NOT NULL DEFAULT '', calendar_depth INTEGER NOT NULL DEFAULT 12,
    foreign_access TEXT NOT NULL, status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    in_universe INTEGER NOT NULL DEFAULT 1, loaded_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE contract (
    contract_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES instrument (instrument_id),
    bbg_ticker TEXT NOT NULL, month_code TEXT NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL,
    first_trade_date TEXT, last_trade_date TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (instrument_id, year, month)
) WITHOUT ROWID;
CREATE TABLE price_daily (
    contract_id TEXT NOT NULL REFERENCES contract (contract_id), date TEXT NOT NULL,
    settle REAL NOT NULL, open_interest REAL, volume REAL, PRIMARY KEY (contract_id, date)
) WITHOUT ROWID;
CREATE TABLE fx_daily (
    pair TEXT NOT NULL, date TEXT NOT NULL, rate REAL NOT NULL, PRIMARY KEY (pair, date)
) WITHOUT ROWID;
"""


def _research_db(path):
    """A tiny research database: WTI May and July 2020 around the negative-price day."""
    conn = sqlite3.connect(path)
    conn.executescript(_RESEARCH_DDL)
    conn.execute("INSERT INTO instrument (instrument_id, name, sector, subsector, exchange, country, exchange_code, "
                 "bbg_root, bbg_yellow_key, currency, contract_size, size_unit, quote_unit, price_scale, "
                 "foreign_access, status, loaded_at) VALUES ('NYMEX:CL','WTI','energy','crude_oil','NYMEX','US',"
                 "'CL','CL','Comdty','USD',1000,'bbl','USD/bbl',1,'international','active','2026-01-01')")
    for cid, month, ltd, settles in (("CLK20 Comdty", 5, "2020-04-21", (18.27, -37.63)),
                                     ("CLN20 Comdty", 7, "2020-06-22", (25.0, 20.0))):
        conn.execute("INSERT INTO contract VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, "NYMEX:CL", cid, "KN"[month == 7], 2020, month, None, ltd, "2026-01-01"))
        conn.executemany("INSERT INTO price_daily VALUES (?,?,?,1,1)",
                         [(cid, "2020-04-17", settles[0]), (cid, "2020-04-20", settles[1])])
    conn.commit()
    conn.close()
    return path


def test_replay_with_no_history_is_na_with_reason(monkeypatch):
    # the history module absent (None in sys.modules makes its import fail), whatever is on disk
    monkeypatch.setitem(sys.modules, "engine.risk.commodity_history", None)
    s = _run([WTI], NEG_WTI)
    assert s["total_usd"] is None and "commodity_history" in s["reason"]
    assert (s["start"], s["end"]) == ("2020-04-17", "2020-04-20")


def test_replay_with_no_research_database_is_na_with_its_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMODITY_HISTORY_DB", str(tmp_path / "nowhere.sqlite"))
    s = _run([WTI], NEG_WTI)
    assert s["total_usd"] is None and s["by_contract"] == [] and s["reason"]


def test_replay_through_window_move_on_a_research_database(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMODITY_HISTORY_DB", str(_research_db(tmp_path / "rv.sqlite")))
    s = _run([WTI, LCU], NEG_WTI)
    # WTI Z26 is ~2.2 months out: the contract that far out on 2020-04-17 was July 2020, -20 %
    (c,) = s["by_contract"]
    assert c["history"]["contract_id"] == "CLN20 Comdty" and c["move"] == pytest.approx(-0.20)
    assert s["total_usd"] == pytest.approx(140000.0 * -0.20)
    assert [m["root_id"] for m in s["missing"]] == ["LME:CA"] and "not in the research database" in s["missing"][0]["reason"]
    # a window the history does not reach is n/a with the history's reason
    early = dict(NEG_WTI, name="Early", start="2019-01-02", end="2019-01-03")
    e = _run([WTI], early)
    assert e["total_usd"] is None and "no settlement on or before 2019-01-02" in e["missing"][0]["reason"]


def test_replay_with_injected_history_names_roots_without_one():
    def history(root_id, months, start, end):
        return (-3.06, "") if root_id == "NYMEX:CL" else (None, f"no history for {root_id}")
    s = _run([WTI, LCU], NEG_WTI, history=history)
    assert s["total_usd"] == pytest.approx(-3.06 * 140000.0)
    assert [(m["contract_id"], m["reason"]) for m in s["missing"]] == [("LPZ26 Comdty", "no history for LME:CA")]


# ---- the configuration ---------------------------------------------------------------------------

def test_config_loads_with_every_scenario_valid():
    scenarios = load_scenarios()
    assert {s["kind"] for s in scenarios} == {"outright", "curve", "spread", "fx", "replay"}
    names = [s["name"] for s in scenarios]
    for expected in ("Energy -20%", "Metals -10%", "Agriculture -8%", "CNY -5%", "MYR -5%",
                     "Negative WTI (2020-04-20)", "LME nickel squeeze (2022-03-07/08)", "TTF August 2022"):
        assert expected in names
    assert all(s["description"] for s in scenarios)
    out = commodity_stress(None, AS_OF, positions=_book([WTI, LCU, SCU]), history=lambda *a: (0.0, ""))
    assert out["available"] and len(out["scenarios"]) == len(scenarios)


@pytest.mark.parametrize("bad, fault", [
    ({"name": "x", "kind": "outright", "sector": {"enrgy": -0.2}}, "not in config/contracts.csv"),
    ({"name": "x", "kind": "outright", "all": -5.5}, "fractions"),
    ({"name": "x", "kind": "wobble"}, "kind"),
    ({"name": "x", "kind": "spread", "legs": [{"select": {"sector": ["energy"]}, "move": -0.1}]}, "two legs"),
    ({"name": "x", "kind": "spread", "legs": [{"select": {"family": ["procesing"]}, "move": -0.1},
                                             {"select": {"sector": ["energy"]}, "move": 0}]}, "config/spreads/"),
    ({"name": "x", "kind": "curve", "front": -0.05, "back": 0, "front_months": 6, "back_months": 3},
     "front_months"),
    ({"name": "x", "kind": "replay", "start": "2020-04-20", "end": "2020-04-17"}, "before"),
    ({"name": "x", "kind": "fx", "moves": {"USD": 0.05}}, "non-USD"),
])
def test_invalid_scenarios_are_refused_with_the_fault(bad, fault):
    with pytest.raises(ScenarioError, match=fault):
        validate_scenarios([bad])


def test_unreadable_config_is_reported_not_raised(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("scenarios:\n  - name: x\n    kind: outright\n    all: oops\n", encoding="utf-8")
    out = commodity_stress(None, AS_OF, p, positions=_book([WTI]))
    assert not out["available"] and "not a number" in out["reasons"][0]
