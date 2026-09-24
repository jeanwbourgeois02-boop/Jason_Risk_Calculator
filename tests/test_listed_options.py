"""Options on commodity futures (engine/options/equity_commodity.py, the listed-options-pricer
lane), pinned on options on the December 2026 WTI future and on SHFE copper.

The P&L of a listed option is contracts x multiplier x (Bloomberg's price - fill), with no model
in between (engine/pnl's listed path); the pricer only gives Greeks, Black-76 (European) or
Barone-Adesi-Whaley (American) at the vol Bloomberg's own price implies, and a missing input
blanks them with the reason (CLAUDE.md "P&L conventions -> Options on commodity futures").
"""
from __future__ import annotations

import datetime
import math
import sqlite3

import pytest

from data.ingest import schema

try:
    import QuantLib  # noqa: F401
    HAVE_QUANTLIB = True
except ImportError:  # pragma: no cover
    HAVE_QUANTLIB = False
needs_quantlib = pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")

ROOT = "NYMEX:CL"
FUTURE = "CLZ26 Comdty"
FUTURE_EXPIRY = "2026-11-19"
EXPIRY = "2026-11-16"
AS_OF = "2026-09-24"
F = 78.0        # the future, $/bbl
K = 80.0
MULT = 1000.0
USD_RATE = 0.045

CU_ROOT = "SHFE:CU"
CU_FUTURE = "CUZ26 Comdty"
CU_FUTURE_EXPIRY = "2026-12-15"
CU_EXPIRY = "2026-11-24"
CU_F, CU_K, CU_MULT = 79_500.0, 80_000.0, 5.0

_COLS = "(instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, expiry_date)"


def option_id(cp="C", strike=K, root="CL"):
    return f"{root}Z26{cp} {strike:g} Comdty"


def _instrument(conn, iid, asset_class, root, ccy, mult, expiry, ticker=""):
    conn.execute(f"INSERT OR IGNORE INTO instruments {_COLS} VALUES (?,?,?,?,?,?,?,?)",
                 (iid, asset_class, root, ccy, mult, 0, ticker, expiry))


def _option(conn, iid, option_type, strike, payoff, root=ROOT, ccy="USD", mult=MULT, expiry=EXPIRY):
    _instrument(conn, iid, "CMDTY_OPTION", root, ccy, mult, expiry)
    conn.execute("INSERT OR REPLACE INTO instrument_options (instrument_id, strike, option_type, payoff) "
                 "VALUES (?,?,?,?)", (iid, strike, option_type, payoff))
    conn.commit()


def _db(tmp_path, curve=True):
    conn = schema.connect(tmp_path / "risk.db")
    _instrument(conn, FUTURE, "FUTURE", ROOT, "USD", MULT, FUTURE_EXPIRY, "CLZ6 Comdty")
    if curve:
        _usd_curve(conn)
    conn.commit()
    return conn


def _usd_curve(conn):
    conn.executemany(
        'INSERT INTO curve_quotes (as_of_date, ccy, "index", tenor, ticker, value, quote_type, field, source) '
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(AS_OF, "USD", "SOFR", t, f"USD{t}", USD_RATE, "OIS", "PX_LAST", "BBG_BDP") for t in ("1M", "1Y", "2Y")])
    conn.commit()


def _trade(conn, trade_id, instrument_id, quantity, fill, mult=MULT, ccy="USD", expiry=EXPIRY):
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description, theme) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, "XLSX", instrument_id, "CMDTY_OPTION", trade_id, "2026-09-14", quantity, fill,
                  "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "NOTIONAL", ccy, quantity * mult * fill, "2026-09-14", expiry, fill, 0))
    conn.commit()


def _price(conn, instrument_id, settle_date, value, as_of=AS_OF):
    """Bloomberg's official FUTURE_PX (BBG_BDH) of a future or of the listed option."""
    conn.execute("INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                 "snapped_at) VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, settle_date, "FUTURE_PX", value, "BBG_BDH", f"{as_of}T17:00:00-04:00"))
    conn.commit()


def _stored(conn, instrument_id):
    return {mt: (v, src, settle) for mt, v, src, settle in conn.execute(
        "SELECT mark_type, value, source, settle_date FROM marks_official WHERE as_of_date = ? "
        "AND instrument_id = ? AND mark_type IN ('PREMIUM','DELTA','GAMMA','THETA','VEGA','RHO')",
        (AS_OF, instrument_id))}


def _T(expiry=EXPIRY):
    return (datetime.date.fromisoformat(expiry) - datetime.date.fromisoformat(AS_OF)).days / 365.0


def _usd_r(conn, expiry=EXPIRY):
    from engine.options.rates import resolve_ccy_rate

    r, reason = resolve_ccy_rate(conn, AS_OF, "USD", expiry, {})
    assert r is not None, reason
    return r


def _model_price(payoff, option_type, vol, r, f=F, k=K, T=None):
    """What the vendored model gives at `vol` (carry = r): Black-76 or Barone-Adesi-Whaley."""
    T = _T() if T is None else T
    if payoff == "AMERICAN":
        from engine.options.vendor.options_calc import _baw_engine

        return _baw_engine.price(f, k, T, r, vol, option_type.lower(), r)
    from engine.options.vendor.options_calc import commodity

    return commodity.price_european(f, k, T, r, vol, option_type.lower())["price"]


def _setup_option(conn, cp, payoff, vol, strike=K):
    iid = option_id(cp, strike)
    _option(conn, iid, "CALL" if cp == "C" else "PUT", strike, payoff)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    bbg = _model_price(payoff, "CALL" if cp == "C" else "PUT", vol, _usd_r(conn), k=strike)
    _price(conn, iid, EXPIRY, bbg)
    return iid, bbg


# --------------------------------------------------------------------------- implied vol and Greeks

@needs_quantlib
def test_european_call_and_put_black76_round_trip_and_delta_bounds(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    call, call_px = _setup_option(conn, "C", "VANILLA", 0.35)
    put, put_px = _setup_option(conn, "P", "VANILLA", 0.35)
    c = price_listed_commodity_option(conn, call, AS_OF)
    p = price_listed_commodity_option(conn, put, AS_OF)
    for got, px in ((c, call_px), (p, put_px)):
        assert got["reason"] == "", got["reason"]
        assert got["model"] == "BLACK76" and got["underlying_id"] == FUTURE and got["underlying_price"] == F
        assert got["implied_vol"] == pytest.approx(0.35, abs=1e-6)
        assert got["marks"]["PREMIUM"] == px                           # the price as quoted, not x multiplier
        assert got["marks"]["GAMMA"] > 0 and got["marks"]["VEGA"] > 0 and got["marks"]["THETA"] < 0
        assert got["settle_date"] == EXPIRY and got["rate_source"].startswith("USD SOFR curve")
    assert 0.0 < c["marks"]["DELTA"] < 1.0 and -1.0 < p["marks"]["DELTA"] < 0.0
    # Black-76, premium-paid: call delta - put delta = the discount factor to expiry
    r = _usd_r(conn)
    assert c["marks"]["DELTA"] - p["marks"]["DELTA"] == pytest.approx(math.exp(-r * _T()), abs=1e-9)
    assert c["marks"]["GAMMA"] == pytest.approx(p["marks"]["GAMMA"], rel=1e-9)


@needs_quantlib
def test_american_call_and_put_barone_adesi_whaley_round_trip_and_delta_bounds(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    call, _ = _setup_option(conn, "C", "AMERICAN", 0.30)
    put, _ = _setup_option(conn, "P", "AMERICAN", 0.30, strike=90.0)      # in the money: early exercise matters
    r = _usd_r(conn)
    for iid, option_type, strike, lo, hi in ((call, "CALL", K, 0.0, 1.0), (put, "PUT", 90.0, -1.0, 0.0)):
        got = price_listed_commodity_option(conn, iid, AS_OF)
        assert got["reason"] == "", got["reason"]
        assert got["model"] == "BAW"
        assert got["implied_vol"] == pytest.approx(0.30, abs=1e-8)
        # the Greeks are taken under the model the vol was implied with: it reprices Bloomberg's price
        repriced = _model_price("AMERICAN", option_type, got["implied_vol"], r, k=strike)
        assert repriced == pytest.approx(got["marks"]["PREMIUM"], rel=1e-9)
        assert lo < got["marks"]["DELTA"] < hi
        assert got["marks"]["GAMMA"] > 0 and got["marks"]["VEGA"] > 0
    # an in-the-money American put is worth at least its European twin
    euro = _model_price("VANILLA", "PUT", 0.30, r, k=90.0)
    assert price_listed_commodity_option(conn, put, AS_OF)["option_price"] > euro


@needs_quantlib
def test_the_marks_are_written_per_option_and_a_trade_id_reaches_the_same_option(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity, price_listed_commodity_option

    conn = _db(tmp_path)
    iid, bbg = _setup_option(conn, "C", "AMERICAN", 0.35)
    _trade(conn, "cl1", iid, 10.0, 2.50)
    by_trade = price_listed_commodity_option(conn, "cl1", AS_OF)
    assert by_trade["instrument_id"] == iid and by_trade["reason"] == ""
    assert _stored(conn, iid) == {}                                      # the one call writes nothing

    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert outcome.priced and outcome.vol_source == "IMPLIED" and outcome.implied_vol == pytest.approx(0.35, abs=1e-3)
    marks = _stored(conn, iid)
    assert set(marks) == {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"}
    assert all(src == "QL_OPTIONS_PRICER" and settle == EXPIRY for _v, src, settle in marks.values())
    assert marks["PREMIUM"][0] == bbg
    assert marks["DELTA"][0] == pytest.approx(by_trade["marks"]["DELTA"])


# --------------------------------------------------------------------------- the discount curve

@needs_quantlib
def test_a_cny_option_discounts_on_its_own_rate_else_on_usd_and_says_so(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option
    from engine.options.rates import set_manual_rate

    conn = _db(tmp_path)
    _instrument(conn, CU_FUTURE, "FUTURE", CU_ROOT, "CNY", CU_MULT, CU_FUTURE_EXPIRY)
    cu = option_id("C", CU_K, root="CU")
    _option(conn, cu, "CALL", CU_K, "AMERICAN", root=CU_ROOT, ccy="CNY", mult=CU_MULT, expiry=CU_EXPIRY)
    _price(conn, CU_FUTURE, CU_FUTURE_EXPIRY, CU_F)
    T = _T(CU_EXPIRY)

    # CNY has no OIS convention: with no CNY rate on file it discounts on the USD SOFR curve
    r_usd = _usd_r(conn, CU_EXPIRY)
    _price(conn, cu, CU_EXPIRY, _model_price("AMERICAN", "CALL", 0.18, r_usd, f=CU_F, k=CU_K, T=T))
    got = price_listed_commodity_option(conn, cu, AS_OF)
    assert got["reason"] == "", got["reason"]
    assert got["rate"] == pytest.approx(r_usd)
    assert got["rate_source"].startswith("USD SOFR curve") and "(no CNY curve or manual rate on file)" in got["rate_source"]
    assert got["implied_vol"] == pytest.approx(0.18, abs=1e-3) and 0 < got["marks"]["DELTA"] < 1

    # a CNY rate on file is its own curve and wins
    set_manual_rate(conn, AS_OF, "CNY", 0.015)
    _price(conn, cu, CU_EXPIRY, _model_price("AMERICAN", "CALL", 0.18, 0.015, f=CU_F, k=CU_K, T=T))
    got = price_listed_commodity_option(conn, cu, AS_OF)
    assert got["reason"] == "" and got["rate"] == pytest.approx(0.015)
    assert got["rate_source"] == "CNY manual_rates flat '*'"
    assert got["implied_vol"] == pytest.approx(0.18, abs=1e-3)


@needs_quantlib
def test_no_curve_at_all_blanks_the_greeks_never_a_guessed_rate(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path, curve=False)
    iid = option_id()
    _option(conn, iid, "CALL", K, "VANILLA")
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, iid, EXPIRY, 2.0)
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {} and got["reason"] == "no curve/rate USD"

    _instrument(conn, CU_FUTURE, "FUTURE", CU_ROOT, "CNY", CU_MULT, CU_FUTURE_EXPIRY)
    cu = option_id("C", CU_K, root="CU")
    _option(conn, cu, "CALL", CU_K, "VANILLA", root=CU_ROOT, ccy="CNY", mult=CU_MULT, expiry=CU_EXPIRY)
    _price(conn, CU_FUTURE, CU_FUTURE_EXPIRY, CU_F)
    _price(conn, cu, CU_EXPIRY, 2000.0)
    got = price_listed_commodity_option(conn, cu, AS_OF)
    assert got["marks"] == {}
    assert got["reason"] == "no curve/rate CNY, and no curve/rate USD to discount on instead"


# --------------------------------------------------------------------------- missing inputs

@needs_quantlib
@pytest.mark.parametrize("payoff", ["VANILLA", "AMERICAN"])
def test_a_price_below_intrinsic_implies_no_vol_and_nothing_is_substituted(tmp_path, payoff):
    from engine.options.equity_commodity import price_listed_commodity_option
    from engine.options.inputs import set_manual_vol

    conn = _db(tmp_path)
    iid = option_id("C", 70.0)
    _option(conn, iid, "CALL", 70.0, payoff)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, iid, EXPIRY, 5.0)                            # worth at least ~8 in the money
    set_manual_vol(conn, AS_OF, FUTURE, EXPIRY, 0.30)         # never taken in its place
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {} and got["implied_vol"] is None
    assert got["reason"].startswith("no vol is implied by Bloomberg's price 5 with the future at 78: "
                                    "it is below the option's intrinsic value")


@needs_quantlib
def test_a_call_worth_more_than_the_future_implies_no_vol(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, "CALL", K, "AMERICAN")
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, iid, EXPIRY, 90.0)
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {}
    assert "it is at or above the most the option can be worth (78)" in got["reason"]


@needs_quantlib
def test_a_missing_underlying_price_blanks_the_greeks_never_the_pnl(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity, price_listed_commodity_option
    from engine.pnl.valuation import value_book

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, "CALL", K, "VANILLA")
    _trade(conn, "cl1", iid, 10.0, 2.50)
    _price(conn, iid, EXPIRY, 3.25)
    _price(conn, FUTURE, FUTURE_EXPIRY, F, as_of="2026-09-23")     # yesterday's is never used
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {}
    assert got["reason"] == f"no Bloomberg price of the underlying future {FUTURE} on {AS_OF}"
    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert not outcome.priced and outcome.reason == got["reason"] and _stored(conn, iid) == {}

    row = value_book(conn, AS_OF).set_index("trade_id").loc["cl1"]
    assert row["pnl_usd"] == pytest.approx(10 * 1000 * (3.25 - 2.50))     # 7,500 on Bloomberg's price


@needs_quantlib
def test_no_price_of_the_option_blanks_the_greeks_and_no_other_vol_is_used(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option
    from engine.options.inputs import set_manual_vol

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, "CALL", K, "VANILLA")
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    set_manual_vol(conn, AS_OF, FUTURE, EXPIRY, 0.35)
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {} and got["reason"] == f"no Bloomberg price of the option {iid} on {AS_OF}"


@needs_quantlib
@pytest.mark.parametrize("payoff, strike, option_type, expiry, expect", [
    ("ASIAN", K, "CALL", EXPIRY, "payoff 'ASIAN' not supported for CMDTY_OPTION (European or American only)"),
    ("VANILLA", 0.0, "CALL", EXPIRY, "no strike on file: enter the strike under Option terms"),
    ("VANILLA", K, "", EXPIRY, "unrecognized option_type ''"),
    ("VANILLA", K, "CALL", "9999-12-31", "no expiry on file for {iid} ('9999-12-31')"),
    ("VANILLA", K, "CALL", AS_OF, f"expiry {AS_OF} is not after as_of {AS_OF}"),
])
def test_what_cannot_be_priced_is_blank_with_its_reason(tmp_path, payoff, strike, option_type, expiry, expect):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, option_type, strike, payoff, expiry=expiry)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, iid, expiry, 2.0)
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {} and got["reason"] == expect.format(iid=iid)


@needs_quantlib
def test_an_option_the_contract_master_does_not_know_is_blank_with_the_reason(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    _option(conn, "CLZ6C 80 Comdty", "CALL", K, "VANILLA")        # the one-digit live form is not canonical
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, "CLZ6C 80 Comdty", EXPIRY, 2.0)
    got = price_listed_commodity_option(conn, "CLZ6C 80 Comdty", AS_OF)
    assert got["marks"] == {}
    assert got["reason"].startswith("underlying future of CLZ6C 80 Comdty not known to the contract master")
    assert price_listed_commodity_option(conn, "nothing", AS_OF)["reason"] == "no option 'nothing' on file"


# --------------------------------------------------------------------------- delta units

@needs_quantlib
def test_delta_is_futures_equivalent_lots_per_option_lot_whatever_the_price_scale(tmp_path):
    """Delta = dV/dF with the option's multiplier its future's: lots x DELTA is the futures
    position that moves the same. The same option quoted in cents has the same vol and delta
    (Black-76 is homogeneous), its gamma 100x smaller per 1.0 of price."""
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    iid, _bbg = _setup_option(conn, "C", "VANILLA", 0.35)
    got = price_listed_commodity_option(conn, iid, AS_OF)
    r, h = _usd_r(conn), 0.01
    fd = (_model_price("VANILLA", "CALL", 0.35, r, f=F + h) - _model_price("VANILLA", "CALL", 0.35, r, f=F - h)) / (2 * h)
    assert got["marks"]["DELTA"] == pytest.approx(fd, rel=1e-6)
    # 10 option lots move like 10 x DELTA futures lots: same USD for a small move in the future
    lots = 10
    assert lots * MULT * got["marks"]["DELTA"] * h == pytest.approx(
        lots * MULT * (_model_price("VANILLA", "CALL", 0.35, r, f=F + h / 2)
                       - _model_price("VANILLA", "CALL", 0.35, r, f=F - h / 2)), rel=1e-4)

    # the same contract quoted in cents: 100x the prices and strike, a multiplier of 10 per cent
    cents_future = "CLF27 Comdty"
    _instrument(conn, cents_future, "FUTURE", ROOT, "USD", 10.0, "2026-12-18")
    cents = "CLF27C 8000 Comdty"
    _option(conn, cents, "CALL", 8000.0, "VANILLA", mult=10.0)
    _price(conn, cents_future, "2026-12-18", F * 100)
    _price(conn, cents, EXPIRY, _bbg * 100)
    in_cents = price_listed_commodity_option(conn, cents, AS_OF)
    assert in_cents["reason"] == "", in_cents["reason"]
    assert in_cents["implied_vol"] == pytest.approx(got["implied_vol"], abs=1e-6)
    assert in_cents["marks"]["DELTA"] == pytest.approx(got["marks"]["DELTA"], rel=1e-6)
    assert in_cents["marks"]["GAMMA"] == pytest.approx(got["marks"]["GAMMA"] / 100, rel=1e-6)


@needs_quantlib
def test_a_multiplier_or_price_scale_that_disagrees_with_the_future_blanks_the_greeks(tmp_path):
    from engine.options.equity_commodity import price_listed_commodity_option

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, "CALL", K, "VANILLA", mult=100.0)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, iid, EXPIRY, 2.0)
    got = price_listed_commodity_option(conn, iid, AS_OF)
    assert got["marks"] == {} and got["reason"].startswith("the option's multiplier 100.0 is not its underlying")

    cents = option_id("C", 8000.0)                          # a strike in cents on a future in dollars
    _option(conn, cents, "CALL", 8000.0, "VANILLA")
    _price(conn, cents, EXPIRY, 2.0)
    got = price_listed_commodity_option(conn, cents, AS_OF)
    assert got["marks"] == {} and "their price scales look different" in got["reason"]


# --------------------------------------------------------------------------- bulk pass

@needs_quantlib
def test_the_bulk_pass_isolates_one_trades_failure_and_an_unreadable_trade(tmp_path, monkeypatch):
    from engine.options import equity_commodity as ec

    conn = _db(tmp_path)
    iid, _ = _setup_option(conn, "C", "VANILLA", 0.35)
    _trade(conn, "cl1", iid, 10.0, 2.50)
    _trade(conn, "cl2", iid, -5.0, 3.10)
    real = ec._price_row

    def exploding(conn_, as_of, row, curve_cache=None, done=None):
        if row["trade_id"] == "cl1":
            raise RuntimeError("convergence not reached after 99 iterations")
        return real(conn_, as_of, row, curve_cache, done)

    monkeypatch.setattr(ec, "_price_row", exploding)
    outcomes = {o.trade_id: o for o in ec.price_all_and_store_commodity(conn, AS_OF)}
    assert set(outcomes) == {"cl1", "cl2"}
    assert not outcomes["cl1"].priced and outcomes["cl1"].reason.startswith("pricer error: RuntimeError")
    assert outcomes["cl1"].instrument_id == iid
    assert outcomes["cl2"].priced, outcomes["cl2"].reason

    monkeypatch.setattr(ec, "_read_trade", lambda conn_, tid: None)
    assert all(not o.priced and "could not be read" in o.reason for o in ec.price_all_and_store_commodity(conn, AS_OF))

    def locked(conn_, tid):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ec, "_read_trade", locked)
    assert all(o.reason.startswith("pricer error: OperationalError")
               for o in ec.price_all_and_store_commodity(conn, AS_OF))


# --------------------------------------------------------------------------- P&L: no model

def test_pnl_is_contracts_x_multiplier_x_price_less_fill(tmp_path):
    from engine.pnl.valuation import value_book

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, "CALL", K, "VANILLA")
    _trade(conn, "cl1", iid, 10.0, 2.50)
    row = value_book(conn, AS_OF).set_index("trade_id").loc["cl1"]
    assert row["pnl_usd"] != row["pnl_usd"]                                  # no price on file: blank, with its reason
    assert row["reason"]

    _price(conn, iid, EXPIRY, 3.25)
    row = value_book(conn, AS_OF).set_index("trade_id").loc["cl1"]
    assert row["product"] == "CMDTY_OPTION" and row["status"] == "OPEN" and row["reason"] == ""
    assert row["pnl_usd"] == pytest.approx(10 * 1000 * (3.25 - 2.50))       # 7,500
    assert row["mark"] == 3.25 and row["mark_source"] == "BBG_BDH"


def test_an_expired_listed_option_freezes_at_its_last_price(tmp_path):
    from engine.pnl.ledger import realise_settled
    from engine.pnl.valuation import value_book

    conn = _db(tmp_path)
    iid = option_id()
    _option(conn, iid, "CALL", K, "VANILLA")
    _trade(conn, "cl1", iid, 10.0, 2.50)
    _price(conn, iid, EXPIRY, 0.40, as_of=EXPIRY)
    realise_settled(conn, "2026-11-18")
    row = value_book(conn, "2026-11-18").set_index("trade_id").loc["cl1"]
    assert row["status"] == "SETTLED"
    assert row["pnl_usd"] == pytest.approx(10 * 1000 * (0.40 - 2.50))
