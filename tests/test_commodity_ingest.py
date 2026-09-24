"""Commodity futures through the blotter parser (ingest-parser, 2026-09-24): contract-master
resolution, the leg in the contract's own currency, the config/book.yaml row filter, and the
synthetic commodity sample (data/sample/commodity_blotter_sample.csv)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.contracts import store_static_dates
from data.ingest import blotter

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "data" / "sample" / "commodity_blotter_sample.csv"
MACRO_SAMPLE = REPO / "data" / "sample" / "blotter_sample.csv"

HEADER = ["Status", "Fund", "Desk", "Fin Type", "Trade Id", "Description", "Side", "Symbol",
          "Underlying Symbol", "Quantity", "Price", "Total Fees", "Currency", "Execution Venue",
          "NetInvoice", "TradeDate", "Settle Date", "ExtAccount", "Counterparty", "Trader"]


@pytest.fixture
def tmp_csv(tmp_path):
    counter = {"n": 0}

    def _make(rows: list[dict]) -> Path:
        counter["n"] += 1
        df = pd.DataFrame(rows)
        for col in HEADER:
            if col not in df.columns:
                df[col] = ""
        p = tmp_path / f"cmdty_{counter['n']}.csv"
        p.write_text(df.to_csv(index=False))
        return p

    return _make


def _row(**overrides) -> dict:
    row = {"Status": "Completed", "Fund": "NMMF", "Desk": "JBRV", "Fin Type": "FUTURE", "Trade Id": "900000001",
           "Symbol": "CLZ6-USAA", "Side": "Buy", "Quantity": "10", "Price": "68.45", "Currency": "DOL.C-USAA",
           "Execution Venue": "NYMEX", "TradeDate": "3/8/2026", "ExtAccount": "GSIL-FUT-NMMF",
           "Counterparty": "GSILUK", "Trader": "JB", "Description": "WTI CRUDE FUT Dec26"}
    row.update(overrides)
    return row


@pytest.fixture(scope="module")
def sample():
    return blotter.parse(SAMPLE)


def _instrument_of(res, trade_id):
    t = next(t for t in res.trades if t.trade_id == trade_id)
    return t, res.instruments[t.instrument_id], [l for l in res.legs if l.trade_id == trade_id]


# --------------------------------------------------------------------------- the sample
def test_sample_parses_with_the_expected_trades_and_rejects(sample):
    assert sample.n_future == 31 and sample.n_forward == 2     # 33 rows: 29 futures, 2 FX, 2 bad
    assert len(sample.trades) == 31
    assert sum(t.product == "FUTURE" for t in sample.trades) == 29
    assert sum(t.product == "FX_FWD" for t in sample.trades) == 2
    assert not sample.warnings                     # every NetInvoice agrees with contracts x multiplier x Price
    assert sample.n_skipped_status_or_fund == 0 and sample.kept_by_trader == {"JB": 33}
    ambiguous, unknown = sample.rejects
    assert ambiguous.symbol == "ZCZ6-USAA" and "CBOT:ZC" in ambiguous.reason and "ZCE:ZC" in ambiguous.reason
    assert unknown.symbol == "QQZ6-USAA" and "'QQ' is not in config/contracts.csv" in unknown.reason
    assert set(sample.instruments) == {
        "CLZ26 Comdty", "CLF27 Comdty", "COZ26 Comdty", "CLX26 Comdty", "XBX26 Comdty", "HOX26 Comdty",
        "S X26 Comdty", "SMZ26 Comdty", "BOZ26 Comdty", "C Z26 Comdty", "GCZ26 Comdty", "SIZ26 Comdty",
        "HGZ26 Comdty", "CUX26 Comdty", "IOEF27 Comdty", "SCOF27 Comdty", "TZTX26 Comdty", "FNX26 Comdty",
        "JGZ26 Comdty", "CLQ26 Comdty", "CLV26 Comdty", "USDCNH"}
    assert all(t.trader == "JB" and t.trade_id.startswith("9") for t in sample.trades)
    assert all("2026-07-01" <= t.trade_date <= "2026-09-18" for t in sample.trades)


@pytest.mark.parametrize("contract_id, root_id, ccy, multiplier, ticker", [
    ("C Z26 Comdty", "CBOT:ZC", "USD", 50.0, "C Z6 Comdty"),       # cents / bu
    ("CUX26 Comdty", "SHFE:CU", "CNY", 5.0, "CUX6 Comdty"),        # Chinese form 'CU2611'
    ("FNX26 Comdty", "ICE:M", "GBP", 300.0, "FNX6 Comdty"),        # NBP, pence / therm
    ("COZ26 Comdty", "ICE:B", "USD", 1000.0, "COZ6 Comdty"),       # Brent: exchange code B, Bloomberg root CO
    ("TZTX26 Comdty", "ICE:TFM", "EUR", 720.0, "TZTX6 Comdty"),
    ("JGZ26 Comdty", "OSE:JAU", "JPY", 1000.0, "JGZ6 Comdty"),
    ("IOEF27 Comdty", "DCE:I", "CNY", 100.0, "IOEF7 Comdty"),
    ("XBX26 Comdty", "NYMEX:RB", "USD", 420.0, "XBX6 Comdty"),     # cents / gal
])
def test_contract_terms_come_from_the_contract_master(sample, contract_id, root_id, ccy, multiplier, ticker):
    inst = sample.instruments[contract_id]
    assert (inst.asset_class, inst.base_ccy, inst.quote_ccy, inst.multiplier, inst.is_ndf, inst.bbg_ticker) == \
        ("FUTURE", root_id, ccy, multiplier, 0, ticker)


def test_the_notional_leg_is_in_the_contracts_own_currency(sample):
    cu = [t for t in sample.trades if t.instrument_id == "CUX26 Comdty"]
    assert sorted(t.quantity for t in cu) == [-20.0, -10.0]
    t, inst, (leg,) = _instrument_of(sample, cu[0].trade_id)
    assert t.price == 78450.0 and t.quantity == -20.0
    assert (leg.leg_type, leg.ccy, leg.settles_cash, leg.rate) == ("NOTIONAL", "CNY", 0, 78450.0)
    assert leg.amount == pytest.approx(-20 * 5 * 78450.0)
    assert leg.start_date == t.trade_date and leg.settle_date == inst.expiry_date
    currencies = {sample.instruments[t.instrument_id].quote_ccy: l.ccy
                  for t in sample.trades if t.product == "FUTURE"
                  for l in sample.legs if l.trade_id == t.trade_id}
    assert currencies == {"USD": "USD", "CNY": "CNY", "EUR": "EUR", "GBP": "GBP", "JPY": "JPY"}


def test_expired_contract_and_round_trip_are_in_the_sample(sample):
    assert sample.instruments["CLQ26 Comdty"].expiry_date < "2026-09-18"
    rt = [t for t in sample.trades if t.instrument_id == "CLV26 Comdty"]
    assert len(rt) == 2 and sum(t.quantity for t in rt) == 0


def test_the_fx_hedges_load_as_usdcnh_forwards(sample):
    fx = [t for t in sample.trades if t.product == "FX_FWD"]
    assert {t.instrument_id for t in fx} == {"USDCNH"} and sorted(t.quantity for t in fx) == [-1_500_000.0, 1_000_000.0]
    assert sample.instruments["USDCNH"].is_ndf == 0


def test_sample_loads_into_a_database():
    conn = sqlite3.connect(":memory:")
    res = blotter.load(SAMPLE, conn)
    assert len(res.rejects) == 2
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 31
    row = conn.execute("SELECT base_ccy, quote_ccy, multiplier, bbg_ticker FROM instruments "
                       "WHERE instrument_id = 'CUX26 Comdty'").fetchone()
    assert row == ("SHFE:CU", "CNY", 5.0, "CUX6 Comdty")


# --------------------------------------------------------------------------- single rows
def test_placeholder_root_gets_no_bloomberg_ticker(tmp_csv):
    res = blotter.parse(tmp_csv([_row(Symbol="SS2611", Currency="CNY.C-CNAA", **{"Execution Venue": "SHFE"},
                                      Price="13,250", Description="SHFE STAINLESS SS2611")]))
    assert not res.rejects
    inst = next(iter(res.instruments.values()))
    assert inst.base_ccy == "SHFE:SS" and inst.bbg_ticker == ""


def test_a_currency_that_contradicts_the_contract_rejects(tmp_csv):
    res = blotter.parse(tmp_csv([_row(Symbol="CU2611", Currency="DOL.C-USAA", **{"Execution Venue": ""})]))
    (rj,) = res.rejects
    assert "SHFE:CU (CNY)" in rj.reason and not res.trades


def test_a_blank_currency_or_venue_never_rejects(tmp_csv):
    res = blotter.parse(tmp_csv([_row(Currency="", **{"Execution Venue": ""})]))
    assert not res.rejects and res.trades[0].instrument_id == "CLZ26 Comdty"


def test_the_underlying_symbol_stands_in_for_a_blank_symbol(tmp_csv):
    res = blotter.parse(tmp_csv([_row(Symbol="", **{"Underlying Symbol": "CLZ6 Comdty"})]))
    assert not res.rejects and res.trades[0].instrument_id == "CLZ26 Comdty"


def test_a_price_quoted_in_another_unit_warns(tmp_csv):
    # RBOB booked in USD / gal (2.054) where the contract list quotes cents: the invoice is 100 times more
    res = blotter.parse(tmp_csv([_row(Symbol="RBX6-USAA", Quantity="2", Price="2.054", NetInvoice="172,536.00")]))
    assert not res.rejects
    (w,) = res.warnings
    assert "another unit" in w.message and "USD/gal" in w.message


def test_commodity_quantity_is_not_rebuilt_from_notional(tmp_csv):
    # a Notional in gallons over a cents-scaled multiplier would read 100 times the contracts
    res = blotter.parse(tmp_csv([_row(Symbol="RBX6-USAA", Quantity="", Price="205.40", Notional="84,000")]))
    (rj,) = res.rejects
    assert "Quantity" in rj.reason
    res = blotter.parse(tmp_csv([_row(Symbol="RBX6-USAA", Quantity="", Price="205.40", NetInvoice="172,536.00")]))
    assert not res.rejects and res.trades[0].quantity == 2.0


def test_stored_bloomberg_dates_replace_the_estimate(tmp_csv):
    conn = sqlite3.connect(":memory:")
    store_static_dates(conn, [{"contract_id": "CLZ26 Comdty", "last_trade_date": "2026-11-19", "source": "BBG_BDP"}])
    path = tmp_csv([_row()])
    assert blotter.parse(path).instruments["CLZ26 Comdty"].expiry_date == "2026-12-31"      # the estimate
    res = blotter.parse(path, conn=conn)
    assert res.instruments["CLZ26 Comdty"].expiry_date == "2026-11-19"
    assert res.legs[0].settle_date == "2026-11-19"


# --------------------------------------------------------------------------- equity index path
def test_es_still_parses_exactly_as_before(tmp_csv):
    res = blotter.parse(tmp_csv([_row(Symbol="ESU6-USAA", Side="Sell", Quantity="1", Price="7,716.00",
                                      TradeDate="20/8/2026", Description="S&P500 EMINI FUT  Sep26",
                                      **{"Execution Venue": ""})]))
    assert not res.rejects
    inst = res.instruments["ESU6 Index"]
    assert (inst.base_ccy, inst.quote_ccy, inst.multiplier, inst.bbg_ticker, inst.expiry_date) == \
        ("ES", "USD", 50.0, "ESU6 Index", "2026-09-18")
    (leg,) = res.legs
    assert (leg.ccy, leg.amount, leg.settle_date) == ("USD", -1 * 50 * 7716.0, "2026-09-18")


def test_the_macro_sample_is_unchanged_by_the_book_filter():
    res = blotter.parse(MACRO_SAMPLE)
    assert len(res.trades) == 857 and not res.rejects and res.n_skipped_status_or_fund == 0
    es = [t for t in res.trades if t.product == "FUTURE"]
    assert len(es) == 11 and {t.instrument_id for t in es} == {"ESU6 Index"}


# --------------------------------------------------------------------------- config/book.yaml
def test_the_shipped_book_yaml_takes_fund_nmmf_only():
    book = blotter.load_book_filter()
    assert (book.funds, book.traders, book.desks) == (("NMMF",), (), ())
    assert book.source.endswith("book.yaml")


def test_fund_filter_counts_what_it_excludes(tmp_csv):
    rows = [_row(), _row(**{"Trade Id": "900000002", "Fund": "OTHER"}), _row(**{"Trade Id": "900000003", "Fund": ""}),
            _row(**{"Trade Id": "900000004", "Status": "Cancelled"}), _row(**{"Trade Id": "900000005", "Fund": "nmmf"})]
    res = blotter.parse(tmp_csv(rows))
    assert sorted(t.trade_id for t in res.trades) == ["900000001", "900000003", "900000005"]
    assert (res.n_excluded_status, res.n_excluded_fund, res.n_excluded_trader, res.n_excluded_desk) == (1, 1, 0, 0)
    assert res.n_skipped_status_or_fund == 2
    assert res.kept_by_trader == {"JB": 3}


def test_trader_and_desk_filters(tmp_csv, tmp_path):
    cfg = tmp_path / "book.yaml"
    cfg.write_text("funds: [NMMF]\ntraders: [jb, JB2]\ndesks: JBRV\n")
    rows = [_row(), _row(**{"Trade Id": "900000002", "Trader": "HA"}), _row(**{"Trade Id": "900000003", "Trader": ""}),
            _row(**{"Trade Id": "900000004", "Trader": "JB2", "Desk": "NMCL"}),
            _row(**{"Trade Id": "900000005", "Trader": "HA", "Fund": "OTHER"})]
    res = blotter.parse(tmp_csv(rows), book=cfg)
    assert sorted(t.trade_id for t in res.trades) == ["900000001", "900000003"]
    assert (res.n_excluded_fund, res.n_excluded_trader, res.n_excluded_desk) == (1, 1, 1)
    assert res.kept_by_trader == {"JB": 1, "": 1}
    summary = res.filter_summary()
    assert "traders jb, JB2" in summary and "1 fund, 1 trader, 1 desk" in summary and "(blank) 1" in summary


def test_empty_lists_take_every_row(tmp_csv):
    res = blotter.parse(tmp_csv([_row(), _row(**{"Trade Id": "900000002", "Fund": "OTHER", "Trader": "HA"})]),
                        book=blotter.BookFilter(funds=(), traders=(), desks=()))
    assert len(res.trades) == 2 and res.kept_by_trader == {"JB": 1, "HA": 1}


def test_a_broken_book_yaml_is_refused(tmp_path):
    bad = tmp_path / "book.yaml"
    bad.write_text("funds: {NMMF: 1}\n")
    with pytest.raises(ValueError, match="list of names"):
        blotter.load_book_filter(bad)
    with pytest.raises(ValueError, match="not found"):
        blotter.load_book_filter(tmp_path / "missing.yaml")


def test_load_passes_the_book_filter(tmp_csv):
    conn = sqlite3.connect(":memory:")
    res = blotter.load(tmp_csv([_row(), _row(**{"Trade Id": "900000002", "Trader": "HA"})]), conn,
                       book=blotter.BookFilter(traders=("JB",)))
    assert res.n_excluded_trader == 1
    assert conn.execute("SELECT trade_id FROM trades").fetchall() == [("900000001",)]
