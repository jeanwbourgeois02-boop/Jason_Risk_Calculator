"""spreads-engine, Phase B: a spread's level (the research app's formula), USD per 1.0 of it, one
position per spread across trade dates, and the research key. The P&L is untouched: these tests
check that the level and the P&L tell the same story (level change x USD per unit = Daily)."""
import copy
import sqlite3

import pytest

from data.contracts import get_root
from data.ingest.schema import create_schema
from engine.spreads import CALENDAR, KIND_BUNDLE, book_spreads
from engine.spreads.book import positions_from

AS_OF, PREV, TD, TD2 = "2026-09-15", "2026-09-14", "2026-09-01", "2026-09-02"
G_PER_OZ = 31.1034768           # the troy ounce in grams (research app's MASS_KG)

ROOT = {"CLZ26 Comdty": "NYMEX:CL", "CLF27 Comdty": "NYMEX:CL", "CLF28 Comdty": "NYMEX:CL",
        "CLZ27 Comdty": "NYMEX:CL", "CLX26 Comdty": "NYMEX:CL", "COZ26 Comdty": "ICE:B",
        "XBX26 Comdty": "NYMEX:RB", "HOX26 Comdty": "NYMEX:HO",
        "C Z26 Comdty": "CBOT:ZC", "C H27 Comdty": "CBOT:ZC",
        "IOEF27 Comdty": "DCE:I", "SCOF27 Comdty": "SGX:FEF",
        "JGZ26 Comdty": "OSE:JAU", "GCZ26 Comdty": "COMEX:GC"}
EXPIRY = {"CLZ26 Comdty": "2026-11-19", "CLF27 Comdty": "2026-12-17", "CLF28 Comdty": "2027-12-17",
          "CLZ27 Comdty": "2027-11-19", "CLX26 Comdty": "2026-10-20", "COZ26 Comdty": "2026-10-30",
          "XBX26 Comdty": "2026-10-30", "HOX26 Comdty": "2026-10-30", "C Z26 Comdty": "2026-12-14",
          "C H27 Comdty": "2027-03-12", "IOEF27 Comdty": "2027-01-15", "SCOF27 Comdty": "2027-01-29",
          "JGZ26 Comdty": "2026-12-24", "GCZ26 Comdty": "2026-12-29"}
CLZ6, CLF7 = "CLZ26 Comdty", "CLF27 Comdty"


def _db():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _future(conn, tid, inst, lots, fill, account="ACC", trade_date=TD, expiry=None):
    root = get_root(ROOT[inst])
    expiry = expiry or EXPIRY[inst]
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root.root_id, root.currency, root.multiplier, inst, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": inst, "product": "FUTURE", "package_id": tid,
            "trade_date": trade_date, "quantity": lots, "price": fill, "account": account, "counterparty": "C",
            "strategy": "", "trader": "JB", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, trade_date, expiry, fill))


def _px(conn, inst, value, day=AS_OF, expiry=None):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')",
                 (day, inst, expiry or EXPIRY[inst], value))


def _spot(conn, pair, value, day=AS_OF):
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FX',?,?,1,0,?,'9999-12-31')",
                 (pair, pair[:3], pair[3:], pair + " Curncy"))
    conn.execute("INSERT INTO marks VALUES (?,?,?,'SPOT',?,'BBG_BFXFORWARD','t')", (day, pair, day, value))


def _only(items, **match):
    hits = [x for x in items if all(x.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, items
    return hits[0]


def _marks(conn, prices):
    for inst, (prev, now) in prices.items():
        _px(conn, inst, prev, PREV)
        _px(conn, inst, now, AS_OF)


def _tells_the_same_story(s, rel=1e-9):
    """The check the brief asks for: on a clean position with no trade that day, the level's move
    times USD per unit IS the Daily P&L the legs' value_book rows add up to."""
    assert s["pnl_usd"]["daily"] is not None and s["level_change"] is not None and s["usd_per_unit"] is not None
    assert s["level_change"] * s["usd_per_unit"] == pytest.approx(s["pnl_usd"]["daily"], rel=rel, abs=1e-6)


# ------------------------------------------------------------------ calendars
def test_wti_calendar_level_is_near_minus_far_and_matches_daily():
    conn = _db()
    _future(conn, "W1", CLZ6, 2, 70.0)
    _future(conn, "W2", CLF7, -2, 69.5)
    _marks(conn, {CLZ6: (70.5, 71.0), CLF7: (69.8, 70.2)})
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == CALENDAR and s["level_unit"] == "USD/bbl"
    assert s["level_entry"] == pytest.approx(0.5)
    assert s["level_prev"] == pytest.approx(0.7) and s["level_prev_date"] == PREV
    assert s["level_now"] == pytest.approx(0.8) and s["level_change"] == pytest.approx(0.1)
    assert s["usd_per_unit"] == pytest.approx(2 * 1000.0)          # 2 lots x 1,000 bbl per USD 1/bbl
    assert all(s[f"{k}_reason"] == "" for k in ("level_entry", "level_prev", "level_now", "level_change",
                                                 "usd_per_unit"))
    assert s["research_id"] == "cal.nymex_cl.z_f" and s["research_instance"] == "2026" and s["research_reason"] == ""
    assert s["level_sources"]["now"] == f"{CLZ6} BBG_BDH; {CLF7} BBG_BDH"
    assert [(leg["instrument_id"], leg["weight"], leg["entry_price"], leg["prev_price"], leg["now_price"])
            for leg in s["level_legs"]] == [(CLZ6, 1.0, 70.0, 70.5, 71.0), (CLF7, -1.0, 69.5, 69.8, 70.2)]
    _tells_the_same_story(s)


def test_corn_calendar_is_in_usd_per_bushel_not_cents():
    # CBOT corn is quoted in US cents per bushel (price_scale 0.01): the level is in the research
    # app's quote unit, USD/bu, so its sigma compares; USD per unit is per USD 1/bu
    conn = _db()
    _future(conn, "Z1", "C Z26 Comdty", 3, 432.25)
    _future(conn, "Z2", "C H27 Comdty", -3, 440.50)
    _marks(conn, {"C Z26 Comdty": (430.0, 433.5), "C H27 Comdty": (439.0, 441.0)})
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["level_unit"] == "USD/bu"
    assert s["level_entry"] == pytest.approx(-0.0825)
    assert s["level_prev"] == pytest.approx(-0.09) and s["level_now"] == pytest.approx(-0.075)
    assert s["level_change"] == pytest.approx(0.015)
    assert s["usd_per_unit"] == pytest.approx(3 * 5000.0)          # 3 lots x 5,000 bu
    assert s["research_id"] == "cal.cbot_zc.z_h" and s["research_instance"] == "2026"
    _tells_the_same_story(s)


# ------------------------------------------------------------------ templates
def test_brent_against_wti_level():
    conn = _db()
    _future(conn, "B1", "COZ26 Comdty", 5, 72.3)
    _future(conn, "C1", CLZ6, -5, 68.6)
    _marks(conn, {"COZ26 Comdty": (72.6, 73.0), CLZ6: (68.7, 68.9)})
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == "bench.crude.brent_vs_wti" and s["level_unit"] == "USD/bbl"
    assert s["level_entry"] == pytest.approx(3.7) and s["level_now"] == pytest.approx(4.1)
    assert s["level_prev"] == pytest.approx(3.9) and s["usd_per_unit"] == pytest.approx(5000.0)
    assert s["research_id"] == "bench.crude.brent_vs_wti" and s["research_instance"] == ""
    _tells_the_same_story(s)


def test_321_crack_level_converts_cents_per_gallon_to_usd_per_barrel():
    conn = _db()
    _future(conn, "K1", "CLX26 Comdty", -3, 69.2)
    _future(conn, "K2", "XBX26 Comdty", 2, 205.4)
    _future(conn, "K3", "HOX26 Comdty", 1, 238.5)
    _marks(conn, {"CLX26 Comdty": (69.5, 70.0), "XBX26 Comdty": (206.0, 207.0), "HOX26 Comdty": (239.0, 240.0)})
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == "proc.us.crack_321" and s["level_unit"] == "USD/bbl"

    def crack(rb, ho, cl):
        return 0.666667 * rb * 0.01 * 42 + 0.333333 * ho * 0.01 * 42 - cl

    assert s["level_entry"] == pytest.approx(crack(205.4, 238.5, 69.2))
    assert s["level_now"] == pytest.approx(crack(207.0, 240.0, 70.0))
    assert s["level_prev"] == pytest.approx(crack(206.0, 239.0, 69.5))
    assert s["usd_per_unit"] == pytest.approx(3000.0, rel=1e-5)
    _tells_the_same_story(s, rel=1e-5)       # the template's weights are rounded (0.666667)


def test_template_constant_is_added_to_the_level(tmp_path):
    (tmp_path / "t.yaml").write_text(
        "- id: test.crude.brent_wti_less\n  family: benchmark\n  name: Brent vs WTI less freight\n"
        "  sector: energy\n  legs:\n    - {instrument: ICE:B, weight: 1.0}\n"
        "    - {instrument: NYMEX:CL, weight: -1.0}\n  unit: USD/bbl\n  constant: -1.5\n", encoding="utf-8")
    conn = _db()
    _future(conn, "B1", "COZ26 Comdty", 5, 72.3)
    _future(conn, "C1", CLZ6, -5, 68.6)
    _marks(conn, {"COZ26 Comdty": (72.6, 73.0), CLZ6: (68.7, 68.9)})
    s = _only(book_spreads(conn, AS_OF, templates_dir=tmp_path)["spreads"])
    assert s["kind"] == "test.crude.brent_wti_less"
    assert s["level_entry"] == pytest.approx(3.7 - 1.5) and s["level_now"] == pytest.approx(4.1 - 1.5)
    _tells_the_same_story(s)                 # the constant cancels in the move


def _iron_ore(conn, entry_spot=True):
    _future(conn, "I1", "IOEF27 Comdty", 10, 765.5, account="PB-CN")
    _future(conn, "S1", "SCOF27 Comdty", -10, 101.25, account="PB-FUT")
    _marks(conn, {"IOEF27 Comdty": (770.0, 775.0), "SCOF27 Comdty": (102.0, 102.5)})
    _spot(conn, "USDCNY", 7.12, PREV)
    _spot(conn, "USDCNY", 7.15, AS_OF)
    if entry_spot:
        _spot(conn, "USDCNY", 7.10, TD)


def test_dce_against_sgx_iron_ore_converts_cny_at_the_valuations_spot():
    conn = _db()
    _iron_ore(conn)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == "bench.ore.dce_vs_sgx62" and s["level_unit"] == "USD/t"
    assert s["level_entry"] == pytest.approx(765.5 / 7.10 - 101.25)
    assert s["level_prev"] == pytest.approx(770.0 / 7.12 - 102.0)
    assert s["level_now"] == pytest.approx(775.0 / 7.15 - 102.5)
    assert s["usd_per_unit"] == pytest.approx(1000.0)              # 10 lots x 100 t per USD 1/t
    assert "CNY at its USD spot" in s["level_sources"]["now"]
    assert "CNY at the official USD spot of 2026-09-01" in s["level_sources"]["entry"]
    assert s["research_id"] == "bench.ore.dce_vs_sgx62"
    # across currencies the level moves with the CNY too; the Daily differs from level move x USD
    # per unit by the FX move on the CNY leg's fill, and by nothing else
    fx_on_fill = 10 * 100 * 765.5 * (1 / 7.12 - 1 / 7.15)
    assert s["pnl_usd"]["daily"] == pytest.approx(s["level_change"] * s["usd_per_unit"] + fx_on_fill)


def test_an_entry_whose_fx_is_not_on_file_is_na_with_its_reason():
    conn = _db()
    _iron_ore(conn, entry_spot=False)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["level_entry"] is None
    assert "needs the official CNY SPOT of its trade date 2026-09-01" in s["level_entry_reason"]
    assert s["level_now"] is not None and s["level_legs"][0]["entry_price"] is None


def test_ose_gold_against_comex_gold_yen_per_gram_into_usd_per_ounce():
    conn = _db()
    _future(conn, "J1", "JGZ26 Comdty", 3, 15420.0)          # 3 kg = 96.45 oz against 100 oz: 3.5 % off
    _future(conn, "G1", "GCZ26 Comdty", -1, 3412.8)
    _marks(conn, {"JGZ26 Comdty": (15500.0, 15600.0), "GCZ26 Comdty": (3420.0, 3430.0)})
    _spot(conn, "USDJPY", 147.0, TD)
    _spot(conn, "USDJPY", 148.0, PREV)
    _spot(conn, "USDJPY", 148.0, AS_OF)                      # FX unchanged on the day
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == "bench.au.ose_vs_comex" and s["level_unit"] == "USD/oz"
    assert s["level_entry"] == pytest.approx(15420.0 * G_PER_OZ / 147.0 - 3412.8)
    assert s["level_now"] == pytest.approx(15600.0 * G_PER_OZ / 148.0 - 3430.0)
    assert s["level_legs"][0]["conversion"] == pytest.approx(G_PER_OZ)
    oz = 3 * 1000 / G_PER_OZ
    assert s["usd_per_unit"] == pytest.approx(oz)            # the fitted spread: 96.45 oz
    # the leftover (100 - 96.45 oz of COMEX gold, short) is an outright on top of the spread
    leftover_oz = -(100 - oz)
    assert s["pnl_usd"]["daily"] == pytest.approx(s["level_change"] * s["usd_per_unit"] + leftover_oz * 10.0)


# ------------------------------------------------------------------ the marks each level reads
def test_now_reads_the_near_marks_estimate_value_book_uses_and_says_so():
    conn = _db()
    _future(conn, "W1", CLZ6, 2, 70.0)
    _future(conn, "W2", CLF7, -2, 69.5)
    _px(conn, CLZ6, 70.5, PREV)
    _px(conn, CLZ6, 71.0)
    _px(conn, CLF7, 69.8, PREV)                               # no F7 price today: carried from the close
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["level_now"] == pytest.approx(71.0 - 69.8)
    assert "INTERP" in s["level_sources"]["now"]


def test_a_spread_put_on_today_reads_the_previous_close_from_the_official_marks():
    conn = _db()
    _future(conn, "W1", CLZ6, 2, 70.0, trade_date=AS_OF)
    _future(conn, "W2", CLF7, -2, 69.5, trade_date=AS_OF)
    _marks(conn, {CLZ6: (70.5, 71.0), CLF7: (69.8, 70.2)})
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["level_prev"] == pytest.approx(0.7) and "not yet held" in s["level_sources"]["prev"]
    assert s["level_change"] == pytest.approx(0.1)


def test_a_settled_leg_leaves_no_level_now_with_its_reason():
    conn = _db()
    _future(conn, "B1", "COZ26 Comdty", 5, 72.3, expiry="2026-09-10")
    _future(conn, "C1", CLZ6, -5, 68.6)
    _px(conn, "COZ26 Comdty", 73.0, day="2026-09-10", expiry="2026-09-10")
    _px(conn, CLZ6, 68.9)
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["level_now"] is None and "settled" in s["level_now_reason"]
    assert s["level_change"] is None and s["level_change_reason"]
    assert s["usd_per_unit"] is None and "COZ26 Comdty has no open lots" in s["usd_per_unit_reason"]
    assert s["level_entry"] == pytest.approx(3.7)


def test_a_bundle_no_shape_fits_has_no_level_and_says_why():
    conn = _db()
    _future(conn, "B1", "COZ26 Comdty", 5, 72.3)
    conn.execute("UPDATE trades SET theme = 'my brent' WHERE trade_id = 'B1'")
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == KIND_BUNDLE and s["level_unit"] == ""
    assert s["level_now"] is None and "no calendar or template" in s["level_now_reason"]
    assert s["research_id"] == "" and s["research_reason"]


def test_a_bundled_calendar_has_its_level():
    conn = _db()
    _future(conn, "W1", CLZ6, 2, 70.0)
    _future(conn, "W2", CLF7, -2, 69.5)
    _marks(conn, {CLZ6: (70.5, 71.0), CLF7: (69.8, 70.2)})
    conn.execute("UPDATE trades SET theme = 'roll' WHERE trade_id IN ('W1', 'W2')")
    s = _only(book_spreads(conn, AS_OF)["spreads"])
    assert s["kind"] == KIND_BUNDLE and s["level_now"] == pytest.approx(0.8)
    assert s["research_id"] == "cal.nymex_cl.z_f"
    _tells_the_same_story(s)


# ------------------------------------------------------------------ research key
def test_calendar_research_keys():
    conn = _db()
    _future(conn, "A1", CLZ6, 1, 70.0)
    _future(conn, "A2", "CLZ27 Comdty", -1, 66.0)            # Z-Z, one year apart: the next year's Z
    _future(conn, "B1", CLZ6, 1, 70.0, trade_date=TD2)
    _future(conn, "B2", "CLF28 Comdty", -1, 65.0, trade_date=TD2)   # two years wide: none
    out = book_spreads(conn, AS_OF)
    zz = _only(out["spreads"], trade_ids=["A1", "A2"])
    assert (zz["research_id"], zz["research_instance"]) == ("cal.nymex_cl.z_z", "2026")
    wide = _only(out["spreads"], trade_ids=["B1", "B2"])
    assert wide["research_id"] == "" and "wider than the research app's calendars" in wide["research_reason"]


# ------------------------------------------------------------------ one position per spread
def _two_entries(conn):
    _future(conn, "A1", CLZ6, 2, 70.0)
    _future(conn, "A2", CLF7, -2, 69.5)
    _future(conn, "B1", CLZ6, 3, 70.6, trade_date=TD2)
    _future(conn, "B2", CLF7, -3, 70.3, trade_date=TD2)
    _marks(conn, {CLZ6: (70.5, 71.0), CLF7: (69.8, 70.2)})


def test_the_same_spread_on_two_dates_is_one_position():
    conn = _db()
    _two_entries(conn)
    _future(conn, "C1", CLZ6, -1, 70.9, trade_date="2026-09-03")   # the same calendar sold: its own position
    _future(conn, "C2", CLF7, 1, 70.1, trade_date="2026-09-03")
    out = book_spreads(conn, AS_OF)
    assert len(out["spreads"]) == 3
    long_, short = out["positions"]
    assert long_["spread_ids"] == ["SPREAD-A1", "SPREAD-B1"] and long_["direction"] == "long"
    assert short["spread_ids"] == ["SPREAD-C1"] and short["direction"] == "short"
    assert long_["size"] == 5.0 and long_["trade_ids"] == ["A1", "A2", "B1", "B2"]
    assert long_["trade_dates"] == [TD, TD2] and long_["research_id"] == "cal.nymex_cl.z_f"
    assert long_["level_entry"] == pytest.approx((2 * 0.5 + 3 * 0.3) / 5)
    assert long_["level_now"] == pytest.approx(0.8) and long_["usd_per_unit"] == pytest.approx(5000.0)
    members = [_only(out["spreads"], spread_id=i) for i in long_["spread_ids"]]
    for p in ("ltd", "daily", "d5", "mtd", "ytd"):
        assert long_["pnl_usd"][p] == pytest.approx(sum(m["pnl_usd"][p] for m in members))
        assert long_["pnl_excluded"][p] == 0
    assert [(leg["instrument_id"], leg["lots"]) for leg in long_["legs"]] == [(CLZ6, 5.0), (CLF7, -5.0)]
    assert long_["level_legs"][0]["entry_price"] == pytest.approx((2 * 70.0 + 3 * 70.6) / 5)
    assert all(e["lots"] == 0.0 for e in long_["leftover"])
    _tells_the_same_story(long_)
    assert short["usd_per_unit"] == pytest.approx(-1000.0)          # short: a rise of the level loses
    _tells_the_same_story(short)


def test_a_member_without_a_figure_is_counted_not_summed():
    conn = _db()
    _two_entries(conn)
    spreads = copy.deepcopy(book_spreads(conn, AS_OF)["spreads"])
    spreads[1]["pnl_usd"]["daily"] = None
    spreads[1]["pnl_reasons"]["daily"] = "a leg unpriced"
    spreads[1]["level_entry"], spreads[1]["level_entry_reason"] = None, "no fx"
    pos = _only(positions_from(spreads))
    assert pos["pnl_usd"]["daily"] == pytest.approx(spreads[0]["pnl_usd"]["daily"])
    assert pos["pnl_excluded"]["daily"] == 1 and "SPREAD-B1: a leg unpriced" in pos["pnl_reasons"]["daily"]
    assert pos["level_entry"] is None and pos["level_entry_reason"] == "SPREAD-B1: no fx"


def test_every_existing_key_is_kept():
    conn = _db()
    _two_entries(conn)
    out = book_spreads(conn, AS_OF)
    assert set(out) == {"as_of", "spreads", "outrights", "review", "positions", "reasons"}
    s = out["spreads"][0]
    for key in ("spread_id", "name", "kind", "template", "family", "unit", "size", "size_unit", "deviation",
                "also_matches", "trade_ids", "accounts", "trade_dates", "status", "legs", "pnl_usd",
                "pnl_reasons", "pnl_notes", "ref_dates", "leftover", "leftover_basis"):
        assert key in s


# ------------------------------------------------------------------ the synthetic Jason book
def test_the_sample_book_at_synthetic_marks():
    from tests.golden_book import build_book

    conn = _db()
    build_book(conn)
    as_of = "2026-09-18"
    out = book_spreads(conn, as_of)
    cl = _only(out["positions"], kind=CALENDAR)
    assert cl["spread_ids"] == ["SPREAD-910000001", "SPREAD-910000003"] and cl["size"] == 15.0
    assert cl["level_entry"] == pytest.approx((10 * (68.45 - 68.1) + 5 * (68.72 - 68.4)) / 15)
    assert cl["usd_per_unit"] == pytest.approx(15000.0)
    _tells_the_same_story(cl)
    _tells_the_same_story(_only(out["positions"], kind="bench.crude.brent_vs_wti"))
    _tells_the_same_story(_only(out["positions"], kind="proc.us.crack_321"), rel=1e-5)
    # the cross-currency ones: the level now is the value_book rows' marks and spots in the formula
    rows = {r["instrument_id"]: r for r in _value_rows(conn, as_of)}
    ore = _only(out["spreads"], kind="bench.ore.dce_vs_sgx62")
    dce, sgx = rows["IOEF27 Comdty"], rows["SCOF27 Comdty"]
    assert ore["level_now"] == pytest.approx(dce["mark"] * dce["spot"] - sgx["mark"])
    gold = _only(out["spreads"], kind="bench.au.ose_vs_comex")
    jau, gc = rows["JGZ26 Comdty"], rows["GCZ26 Comdty"]
    assert gold["level_now"] == pytest.approx(jau["mark"] * G_PER_OZ * jau["spot"] - gc["mark"])
    assert gold["level_unit"] == "USD/oz" and ore["level_unit"] == "USD/t"


def _value_rows(conn, day):
    from engine.pnl.valuation import value_book
    return value_book(conn, day).to_dict("records")
