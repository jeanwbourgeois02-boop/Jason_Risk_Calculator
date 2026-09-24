"""Listed options on commodity futures (engine/options/equity_commodity.py, the
listed-options-pricer lane), pinned on an option on the December 2026 crude future.

The P&L of a listed option is contracts x multiplier x (Bloomberg's price - fill), with no
model in between (engine/pnl's generic listed path, product EQ_OPTION); the pricer here
only writes Greeks, at the vol Bloomberg's own price implies, and a missing input blanks
them with the reason. The SPX / equity-index tests left with the equity index on
2026-09-24 (CLAUDE.md "Commodity conversion plan", Phase 2).
"""
from __future__ import annotations

import sqlite3

import pytest

from data.ingest import schema

try:
    import QuantLib  # noqa: F401
    HAVE_QUANTLIB = True
except ImportError:  # pragma: no cover
    HAVE_QUANTLIB = False
needs_quantlib = pytest.mark.skipif(not HAVE_QUANTLIB, reason="QuantLib not installed")

FUTURE = "CLZ26 Comdty"
FUTURE_EXPIRY = "2026-11-19"
OPTION = "CLZ6C 80 Comdty"
EXPIRY = "2026-11-16"
AS_OF = "2026-09-24"
F = 78.0        # the future, $/bbl
K = 80.0
USD_RATE = 0.045


def _db(tmp_path, product="CMDTY_OPTION", payoff="VANILLA", option_type="CALL", strike=K, curve=True):
    conn = schema.connect(tmp_path / "risk.db")
    cols = "(instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, expiry_date)"
    conn.execute(f"INSERT INTO instruments {cols} VALUES (?,?,?,?,?,?,?,?)",
                 (FUTURE, "FUTURE", "CL", "USD", 1000.0, 0, FUTURE, FUTURE_EXPIRY))
    # the option's own bbg_ticker holds the underlying future's instrument_id
    conn.execute(f"INSERT INTO instruments {cols} VALUES (?,?,?,?,?,?,?,?)",
                 (OPTION, product, "CL", "USD", 1000.0, 0, FUTURE, EXPIRY))
    conn.execute("INSERT INTO instrument_options (instrument_id, strike, option_type, payoff) VALUES (?,?,?,?)",
                 (OPTION, strike, option_type, payoff))
    _trade(conn, "cl1", product, 10.0, 2.50)
    if curve:
        conn.executemany(
            'INSERT INTO curve_quotes (as_of_date, ccy, "index", tenor, ticker, value, quote_type, field, source) '
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(AS_OF, "USD", "SOFR", t, f"USD{t}", USD_RATE, "OIS", "PX_LAST", "BBG_BDP") for t in ("1M", "1Y", "2Y")])
    conn.commit()
    return conn


def _trade(conn, trade_id, product, quantity, fill, instrument_id=OPTION):
    conn.execute("INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
                 "price, account, counterparty, strategy, trader, description, theme) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (trade_id, "XLSX", instrument_id, product, trade_id, "2026-09-14", quantity, fill,
                  "acc", "cp", "", "t", "d", ""))
    conn.execute("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
                 (trade_id, 1, "NOTIONAL", "USD", quantity * 1000 * K, "2026-09-14", EXPIRY, fill, 0))
    conn.commit()


def _price(conn, instrument_id, settle_date, value, as_of=AS_OF):
    """Bloomberg's official FUTURE_PX (BBG_BDH) of a future or of the listed option."""
    conn.execute("INSERT OR REPLACE INTO marks (as_of_date, instrument_id, settle_date, mark_type, value, source, "
                 "snapped_at) VALUES (?,?,?,?,?,?,?)",
                 (as_of, instrument_id, settle_date, "FUTURE_PX", value, "BBG_BDH", f"{as_of}T17:00:00-04:00"))
    conn.commit()


def _greeks(conn, instrument_id=OPTION):
    return {mt: (v, src, settle) for mt, v, src, settle in conn.execute(
        "SELECT mark_type, value, source, settle_date FROM marks_official WHERE as_of_date = ? "
        "AND instrument_id = ? AND mark_type IN ('PREMIUM','DELTA','GAMMA','THETA','VEGA','RHO')",
        (AS_OF, instrument_id))}


def _black76(conn, payoff, option_type, vol):
    """What the vendored Black-76 pricer gives at `vol` on the USD curve on file."""
    import datetime

    from engine.options import pricer
    from engine.options.rates import resolve_ccy_rate

    r, reason = resolve_ccy_rate(conn, AS_OF, "USD", EXPIRY, {})
    assert r is not None, reason
    return pricer.price_commodity_option(payoff, F, K, datetime.date.fromisoformat(EXPIRY),
                                         datetime.date.fromisoformat(AS_OF), r, vol, option_type).premium


# --------------------------------------------------------------------------- Greeks

@needs_quantlib
def test_greeks_come_from_the_vol_bloombergs_price_implies(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity

    conn = _db(tmp_path)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    bbg = _black76(conn, "VANILLA", "CALL", 0.35)
    _price(conn, OPTION, EXPIRY, bbg)

    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert outcome.priced, outcome.reason
    assert outcome.vol_source == "IMPLIED"
    marks = _greeks(conn)
    assert set(marks) == {"PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA", "RHO"}
    assert all(src == "QL_OPTIONS_PRICER" and settle == EXPIRY for _v, src, settle in marks.values())
    # the premium of one contract is Bloomberg's price x 1,000 bbl, by construction
    assert marks["PREMIUM"][0] == pytest.approx(bbg * 1000.0, rel=1e-6)
    assert 0.0 < marks["DELTA"][0] < 1.0 and marks["GAMMA"][0] > 0 and marks["VEGA"][0] > 0


@needs_quantlib
def test_an_american_put_is_implied_under_black76_too(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity

    conn = _db(tmp_path, payoff="AMERICAN", option_type="PUT")
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    bbg = _black76(conn, "AMERICAN", "PUT", 0.30)
    _price(conn, OPTION, EXPIRY, bbg)

    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert outcome.priced, outcome.reason
    assert outcome.vol_source == "IMPLIED"
    # implied under Barone-Adesi-Whaley, repriced on the tree: within the BAW-vs-tree gap
    assert _greeks(conn)["PREMIUM"][0] == pytest.approx(bbg * 1000.0, rel=0.01)
    assert -1.0 < outcome.result.delta < 0.0


@needs_quantlib
def test_a_price_no_vol_can_explain_blanks_the_greeks_and_nothing_is_substituted(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity
    from engine.options.inputs import set_manual_vol

    conn = _db(tmp_path)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, OPTION, EXPIRY, 90.0)                        # a call worth more than the future
    set_manual_vol(conn, AS_OF, FUTURE, EXPIRY, 0.30)         # never taken in its place

    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert not outcome.priced
    assert outcome.reason.startswith("no vol is implied by Bloomberg's price 90 with the future at 78")
    assert _greeks(conn) == {}


@needs_quantlib
def test_with_no_bloomberg_price_of_the_option_a_manual_vol_is_used_else_no_vol(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity
    from engine.options.inputs import set_manual_vol

    conn = _db(tmp_path)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert not outcome.priced and outcome.reason == "no vol"
    assert _greeks(conn) == {}

    set_manual_vol(conn, AS_OF, FUTURE, EXPIRY, 0.35)
    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert outcome.priced and outcome.vol_source == "MANUAL"
    assert _greeks(conn)["PREMIUM"][0] == pytest.approx(_black76(conn, "VANILLA", "CALL", 0.35) * 1000.0)


@needs_quantlib
def test_no_price_of_the_underlying_future_blanks_the_greeks_with_the_reason(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity

    conn = _db(tmp_path)
    _price(conn, OPTION, EXPIRY, 2.0)
    _price(conn, FUTURE, FUTURE_EXPIRY, F, as_of="2026-09-23")     # yesterday's is never used
    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert not outcome.priced
    assert outcome.reason == f"no Bloomberg price of the underlying future {FUTURE} on {AS_OF}"
    assert _greeks(conn) == {}


@needs_quantlib
def test_no_discount_curve_on_file_blanks_the_greeks_never_a_guessed_rate(tmp_path):
    """The library no longer asks for a USD OIS curve on a listed option's behalf
    (2026-09-24): with none on file that day the Greeks are blank with the reason."""
    from engine.options.equity_commodity import price_and_store_commodity

    conn = _db(tmp_path, curve=False)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, OPTION, EXPIRY, 2.0)
    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert not outcome.priced
    assert outcome.reason == "no curve/rate USD"
    assert _greeks(conn) == {}


@needs_quantlib
@pytest.mark.parametrize("payoff, strike, option_type, expect", [
    ("BARRIER_KO", K, "CALL", "payoff 'BARRIER_KO' not supported for CMDTY_OPTION"),
    ("DIGITAL", K, "CALL", "payoff 'DIGITAL' not supported for CMDTY_OPTION"),
    ("VANILLA", 0.0, "CALL", "no strike on file: enter the strike under Option terms"),
    ("VANILLA", K, "", "unrecognized option_type ''"),
])
def test_what_cannot_be_priced_is_skipped_never_approximated(tmp_path, payoff, strike, option_type, expect):
    from engine.options.equity_commodity import price_and_store_commodity

    conn = _db(tmp_path, payoff=payoff, strike=strike, option_type=option_type)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, OPTION, EXPIRY, 2.0)
    outcome = price_and_store_commodity(conn, AS_OF, "cl1")
    assert not outcome.priced and outcome.reason == expect
    assert _greeks(conn) == {}


@needs_quantlib
def test_on_or_after_expiry_nothing_is_priced(tmp_path):
    from engine.options.equity_commodity import price_and_store_commodity

    conn = _db(tmp_path)
    outcome = price_and_store_commodity(conn, EXPIRY, "cl1")
    assert not outcome.priced and outcome.reason == f"expiry {EXPIRY} is not after as_of {EXPIRY}"


@needs_quantlib
def test_the_bulk_pass_isolates_one_trades_failure_and_an_unreadable_trade(tmp_path, monkeypatch):
    from engine.options import equity_commodity as ec

    conn = _db(tmp_path)
    _trade(conn, "cl2", "CMDTY_OPTION", -5.0, 3.10)
    _price(conn, FUTURE, FUTURE_EXPIRY, F)
    _price(conn, OPTION, EXPIRY, _black76(conn, "VANILLA", "CALL", 0.35))
    real = ec._price_row

    def exploding(conn_, as_of, row, curve_cache=None):
        if row["trade_id"] == "cl1":
            raise RuntimeError("convergence not reached after 99 iterations")
        return real(conn_, as_of, row, curve_cache)

    monkeypatch.setattr(ec, "_price_row", exploding)
    outcomes = {o.trade_id: o for o in ec.price_all_and_store_commodity(conn, AS_OF)}
    assert set(outcomes) == {"cl1", "cl2"}
    assert not outcomes["cl1"].priced and outcomes["cl1"].reason.startswith("pricer error: RuntimeError")
    assert outcomes["cl1"].instrument_id == OPTION
    assert outcomes["cl2"].priced, outcomes["cl2"].reason

    monkeypatch.setattr(ec, "_read_trade", lambda conn_, tid: None)
    assert all(not o.priced and "could not be read" in o.reason for o in ec.price_all_and_store_commodity(conn, AS_OF))

    def locked(conn_, tid):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ec, "_read_trade", locked)
    assert all(o.reason.startswith("pricer error: OperationalError")
               for o in ec.price_all_and_store_commodity(conn, AS_OF))


@needs_quantlib
def test_vol_surface_points_round_trip(tmp_path):
    from engine.options.equity_commodity import _build_vol_surface, write_vol_surface_points

    conn = _db(tmp_path)
    write_vol_surface_points(conn, AS_OF, FUTURE, [(30, 75, 0.36), (30, 85, 0.33), (90, 75, 0.34), (90, 85, 0.31)])
    surface = _build_vol_surface(conn, AS_OF, FUTURE)
    assert surface.get_vol(75, 30 / 365.0) == pytest.approx(0.36)
    assert surface.get_vol(85, 90 / 365.0) == pytest.approx(0.31)
    write_vol_surface_points(conn, AS_OF, "CLF27 Comdty", [(30, 75, 0.36), (90, 85, 0.31)])  # not a rectangle
    assert _build_vol_surface(conn, AS_OF, "CLF27 Comdty") is None


# --------------------------------------------------------------------------- P&L: no model

def test_pnl_is_contracts_x_multiplier_x_price_less_fill(tmp_path):
    from engine.pnl.valuation import value_book

    conn = _db(tmp_path, product="EQ_OPTION")        # the generic listed path's product code
    row = value_book(conn, AS_OF).set_index("trade_id").loc["cl1"]
    assert row["pnl_usd"] != row["pnl_usd"]                                  # no price on file: blank, with its reason
    assert row["reason"] == f"no Bloomberg price for the listed option {OPTION} on {AS_OF}"

    _price(conn, OPTION, EXPIRY, 3.25)
    row = value_book(conn, AS_OF).set_index("trade_id").loc["cl1"]
    assert row["product"] == "EQ_OPTION" and row["status"] == "OPEN" and row["reason"] == ""
    assert row["pnl_usd"] == pytest.approx(10 * 1000 * (3.25 - 2.50))       # 7,500
    assert row["mark"] == 3.25 and row["mark_source"] == "BBG_BDH"


def test_an_expired_listed_option_freezes_at_its_last_price(tmp_path):
    from engine.pnl.ledger import realise_settled
    from engine.pnl.valuation import value_book

    conn = _db(tmp_path, product="EQ_OPTION")
    _price(conn, OPTION, EXPIRY, 0.40, as_of=EXPIRY)
    realise_settled(conn, "2026-11-18")
    row = value_book(conn, "2026-11-18").set_index("trade_id").loc["cl1"]
    assert row["status"] == "SETTLED"
    assert row["pnl_usd"] == pytest.approx(10 * 1000 * (0.40 - 2.50))
