"""Commodity futures through the blotter parser (ingest-parser, 2026-09-24): contract-master
resolution, the leg in the contract's own currency, the config/book.yaml row filter, and the
synthetic sample book (data/sample/blotter_sample.csv: Jason's commodity futures and FX hedges,
every value made up; it replaced the macro trader's blotter on 2026-09-24)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from data.contracts import store_static_dates
from data.ingest import blotter

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "data" / "sample" / "blotter_sample.csv"

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
SAMPLE_FUTURES = {
    "CLZ26 Comdty", "CLF27 Comdty", "COZ26 Comdty", "CLX26 Comdty", "XBX26 Comdty", "HOX26 Comdty",
    "S X26 Comdty", "SMZ26 Comdty", "BOZ26 Comdty", "C Z26 Comdty", "GCZ26 Comdty", "SIZ26 Comdty",
    "HGZ26 Comdty", "CUX26 Comdty", "IOEF27 Comdty", "SCOF27 Comdty", "TZTX26 Comdty", "FNX26 Comdty",
    "JGZ26 Comdty", "CLQ26 Comdty", "CLV26 Comdty"}
SAMPLE_OPTIONS = {"EURUSD111826C-500041", "USDJPY121626P-500042", "USDJPY111926P-500043",
                  "EURUSD102126P-500044", "EURUSD102126P-500045"}
SAMPLE_CMDTY_OPTIONS = {"CLZ26C 75 Comdty", "CLZ26P 62 Comdty", "GCQ26C 3300 Comdty", "CUZ26C 80000 Comdty"}
# the options' underlying futures the sample does not trade itself: instrument rows only
SAMPLE_UNDERLYING_ONLY = {"GCQ26 Comdty", "CUZ26 Comdty"}
SAMPLE_LME = {"LME:CA", "LME:AH", "LME:NI"}


def test_sample_parses_with_the_expected_trades_and_rejects(sample):
    # 52 rows: 31 futures (29 trades + 2 deliberate rejects), 8 FX forwards, 1 spot, 5 FX options,
    # and since Phase 5 4 options on futures and 3 LME forwards
    assert (sample.n_future, sample.n_forward, sample.n_currency, sample.n_spot, sample.n_option,
            sample.n_cmdty_option, sample.n_lme_forward, sample.n_skipped_retired) == (31, 8, 1, 1, 5, 4, 3, 0)
    assert len(sample.trades) == 50 and len(sample.legs) == 29 + 8 * 2 + 2 + 5 + 4 + 3 * 2
    assert {p: sum(t.product == p for t in sample.trades)
            for p in ("FUTURE", "FX_FWD", "FX_SPOT", "FX_OPTION", "CMDTY_OPTION", "LME_FWD")} == \
        {"FUTURE": 29, "FX_FWD": 8, "FX_SPOT": 1, "FX_OPTION": 5, "CMDTY_OPTION": 4, "LME_FWD": 3}
    assert not sample.warnings                     # every NetInvoice agrees with its fill, every prompt is valid
    assert sample.n_skipped_status_or_fund == 0 and sample.n_skipped_other == 0 and sample.kept_by_trader == {"JB": 52}
    ambiguous, unknown = sample.rejects
    assert ambiguous.symbol == "ZCZ6-USAA" and "CBOT:ZC" in ambiguous.reason and "ZCE:ZC" in ambiguous.reason
    assert unknown.symbol == "QQZ6-USAA" and "'QQ' is not in config/contracts.csv" in unknown.reason
    assert set(sample.instruments) == SAMPLE_FUTURES | SAMPLE_UNDERLYING_ONLY | SAMPLE_OPTIONS | SAMPLE_CMDTY_OPTIONS | \
        SAMPLE_LME | {"USDCNH", "EURUSD", "USDJPY", "GBPUSD", "EURGBP", "XAUUSD", "CASH-EUR"}
    assert {k for k, i in sample.instruments.items() if i.asset_class == "FUTURE"} == \
        SAMPLE_FUTURES | SAMPLE_UNDERLYING_ONLY
    assert sample.underlying_only == SAMPLE_UNDERLYING_ONLY
    assert {t.instrument_id for t in sample.trades if t.product == "FUTURE"} == SAMPLE_FUTURES
    assert all(t.trader == "JB" and t.trade_id.startswith("9100000") for t in sample.trades)
    assert all(t.counterparty in ("CPTY-A", "CPTY-B", "CPTY-C") for t in sample.trades)
    assert all("2026-07-01" <= t.trade_date <= "2026-09-18" for t in sample.trades)


def test_the_sample_carries_no_macro_product(sample):
    """The macro trader's products leave in Phase 2: no rate swap, no NDF currency, no equity
    index future or listed index option (all left the app on 2026-09-24; tests/test_blotter.py
    shows such rows are skipped with their reason)."""
    assert {t.product for t in sample.trades} == {"FUTURE", "FX_FWD", "FX_SPOT", "FX_OPTION", "CMDTY_OPTION", "LME_FWD"}
    assert not any(i.is_ndf for i in sample.instruments.values())
    assert not {l.ccy for l in sample.legs} & {"BRL", "TWD", "KRW", "IDR", "INR"}
    assert not [k for k in sample.instruments if k.endswith(" Index") or "/" in k]


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


def _fx_legs(res, trade_id):
    return [(l.leg_type, l.ccy, l.amount, l.settle_date, l.settles_cash) for l in res.legs if l.trade_id == trade_id]


def test_the_usdcnh_hedges_are_three_forwards_one_already_settled(sample):
    cnh = {t.trade_id: t for t in sample.trades if t.instrument_id == "USDCNH"}
    assert {k: (t.product, t.quantity, t.price) for k, t in cnh.items()} == {
        "910000030": ("FX_FWD", -1_500_000.0, 7.1425), "910000031": ("FX_FWD", 1_000_000.0, 7.108),
        "910000034": ("FX_FWD", 2_000_000.0, 7.165)}
    assert sample.instruments["USDCNH"].is_ndf == 0
    assert _fx_legs(sample, "910000034") == [("FX_NEAR", "USD", 2_000_000.0, "2026-08-19", 1),
                                             ("FX_NEAR", "CNH", -14_330_000.0, "2026-08-19", 1)]   # settled
    assert {l.settle_date for l in sample.legs if l.trade_id in ("910000030", "910000031")} == \
        {"2026-11-18", "2027-01-20"}


@pytest.mark.parametrize("trade_id, pair, quantity, price, base_leg, quote_leg", [
    ("910000035", "EURUSD", -400_000.0, 1.1745, ("EUR", -400_000.0), ("USD", 469_800.0)),
    ("910000036", "USDJPY", 300_000.0, 145.62, ("USD", 300_000.0), ("JPY", -43_686_000.0)),
    ("910000037", "GBPUSD", 200_000.0, 1.3462, ("GBP", 200_000.0), ("USD", -269_240.0)),
    ("910000038", "EURGBP", 250_000.0, 0.8655, ("EUR", 250_000.0), ("GBP", -216_375.0)),     # the cross
    ("910000039", "XAUUSD", -100.0, 3365.4, ("XAU", -100.0), ("USD", 336_540.0)),           # the metal
])
def test_the_g10_cross_and_metal_forwards(sample, trade_id, pair, quantity, price, base_leg, quote_leg):
    t = next(t for t in sample.trades if t.trade_id == trade_id)
    assert (t.product, t.instrument_id, t.quantity, t.price) == ("FX_FWD", pair, quantity, price)
    legs = _fx_legs(sample, trade_id)
    assert [(ccy, amount) for _, ccy, amount, _, _ in legs] == [base_leg, quote_leg]
    assert all(leg_type == "FX_NEAR" and cash == 1 for leg_type, _, _, _, cash in legs)
    inst = sample.instruments[pair]
    assert (inst.asset_class, inst.base_ccy, inst.quote_ccy, inst.bbg_ticker, inst.is_ndf) == \
        ("FX", pair[:3], pair[3:], f"{pair} Curncy", 0)


def test_the_spot_trade_is_a_currency_row_naming_both_currencies(sample):
    (spot,) = [t for t in sample.trades if t.product == "FX_SPOT"]
    assert (spot.trade_id, spot.instrument_id, spot.quantity, spot.price, spot.trade_date) == \
        ("910000040", "EURUSD", -150_000.0, 1.169, "2026-09-09")
    assert _fx_legs(sample, "910000040") == [("FX_NEAR", "EUR", -150_000.0, "2026-09-11", 1),
                                             ("FX_NEAR", "USD", 175_350.0, "2026-09-11", 1)]
    assert sample.instruments["CASH-EUR"].asset_class == "CASH"


def test_the_fx_options_vanillas_digital_and_closed_out_pair(sample):
    terms = {k: (o.strike, o.option_type, o.payoff, sample.instruments[k].expiry_date)
             for k, o in sample.instrument_options.items() if sample.instruments[k].asset_class == "FX_OPTION"}
    assert terms == {
        "EURUSD111826C-500041": (1.18, "CALL", "VANILLA", "2026-11-18"),
        "USDJPY121626P-500042": (142.5, "PUT", "VANILLA", "2026-12-16"),
        # the digital: the export names neither its strike nor its payoff; both are typed in the app
        "USDJPY111926P-500043": (0.0, "PUT", "VANILLA", "2026-11-19"),
        "EURUSD102126P-500044": (1.15, "PUT", "VANILLA", "2026-10-21"),
        "EURUSD102126P-500045": (1.15, "PUT", "VANILLA", "2026-10-21"),
    }
    assert sample.options_missing_strike == ["USDJPY111926P-500043"]
    opts = {t.trade_id: (t.instrument_id, t.quantity, t.price, t.trade_date)
            for t in sample.trades if t.product == "FX_OPTION"}
    assert opts["910000041"] == ("EURUSD111826C-500041", 10_000_000.0, 0.0098, "2026-08-19")
    # the closed-out pair: the same put bought, then sold back, under two instrument ids
    assert opts["910000044"] == ("EURUSD102126P-500044", 5_000_000.0, 0.0042, "2026-08-10")
    assert opts["910000045"] == ("EURUSD102126P-500045", -5_000_000.0, 0.0031, "2026-09-08")
    legs = {l.trade_id: (l.leg_type, l.ccy, l.amount, l.settles_cash) for l in sample.legs if l.trade_id in opts}
    assert legs["910000042"] == ("NOTIONAL", "USD", 5_000_000.0, 0) and legs["910000045"][1:3] == ("EUR", -5_000_000.0)


def test_the_sample_options_on_futures(sample):
    """Phase 5: a WTI call bought and a put sold on the same expiry, a COMEX gold call already
    expired on 2026-09-18 (the freeze path), a SHFE copper call in CNY on the onshore account."""
    got = {t.trade_id: (t.instrument_id, t.quantity, t.price, sample.instruments[t.instrument_id].expiry_date,
                        sample.instruments[t.instrument_id].quote_ccy)
           for t in sample.trades if t.product == "CMDTY_OPTION"}
    assert got == {
        "910000046": ("CLZ26C 75 Comdty", 10.0, 2.15, "2026-11-17", "USD"),
        "910000047": ("CLZ26P 62 Comdty", -10.0, 1.4, "2026-11-17", "USD"),
        "910000048": ("GCQ26C 3300 Comdty", 2.0, 45.2, "2026-07-28", "USD"),       # expired
        "910000049": ("CUZ26C 80000 Comdty", 4.0, 1850.0, "2026-12-31", "CNY"),   # the estimate
    }
    assert {k: (o.strike, o.option_type, o.payoff) for k, o in sample.instrument_options.items()
            if k in SAMPLE_CMDTY_OPTIONS} == {
        "CLZ26C 75 Comdty": (75.0, "CALL", "AMERICAN"), "CLZ26P 62 Comdty": (62.0, "PUT", "AMERICAN"),
        "GCQ26C 3300 Comdty": (3300.0, "CALL", "AMERICAN"), "CUZ26C 80000 Comdty": (80000.0, "CALL", "AMERICAN")}
    (cu_leg,) = [l for l in sample.legs if l.trade_id == "910000049"]
    assert (cu_leg.ccy, cu_leg.amount, cu_leg.settle_date) == ("CNY", 4 * 5 * 1850.0, "2026-12-31")
    assert next(t for t in sample.trades if t.trade_id == "910000049").account == "PB-CN-NMMF"


def test_the_sample_lme_forwards(sample):
    """Phase 5: a copper 3M bought (no prompt in the file, so the 3-month date, said in the notes),
    an aluminium third-Wednesday prompt sold, a nickel prompt already past on 2026-09-18."""
    got = {t.trade_id: (t.instrument_id, t.quantity, t.price) for t in sample.trades if t.product == "LME_FWD"}
    assert got == {"910000050": ("LME:CA", 100.0, 9850.0), "910000051": ("LME:AH", -75.0, 2640.0),
                   "910000052": ("LME:NI", 12.0, 15420.0)}
    assert _legs(sample, "910000051") == [
        (1, "FX_NEAR", "LME:AH", -75.0, "2026-09-03", "2026-12-16", 2640.0, 0),
        (2, "FX_NEAR", "USD", 198_000.0, "2026-09-03", "2026-12-16", 2640.0, 1)]
    assert {l.settle_date for l in sample.legs if l.trade_id == "910000050"} == {"2026-12-10"}
    assert {l.settle_date for l in sample.legs if l.trade_id == "910000052"} == {"2026-09-16"}   # past
    assert any("3-month" in n and "910000050" in n for n in sample.information_notes())
    assert {sample.instruments[k].asset_class for k in SAMPLE_LME} == {"LME_FWD"}


def test_sample_loads_into_a_database():
    conn = sqlite3.connect(":memory:")
    res = blotter.load(SAMPLE, conn)
    assert len(res.rejects) == 2
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 50
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == 62
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 42     # 2 of them underlying-only
    assert conn.execute("SELECT COUNT(*) FROM instrument_options").fetchone()[0] == 9
    row = conn.execute("SELECT base_ccy, quote_ccy, multiplier, bbg_ticker FROM instruments "
                       "WHERE instrument_id = 'CUX26 Comdty'").fetchone()
    assert row == ("SHFE:CU", "CNY", 5.0, "CUX6 Comdty")


# --------------------------------------------------------------------------- single rows
def test_placeholder_root_gets_no_bloomberg_ticker(tmp_csv, tmp_path, monkeypatch):
    """A root whose Bloomberg root is still the research app's 'ZZ' placeholder is never given a
    ticker. Every root of config/contracts.csv carries a best guess since 2026-09-24, so the rule
    is shown on a copy of the contract list in which SHFE:SS is put back to a placeholder."""
    import csv

    from data.contracts import universe

    with open(universe.CONTRACTS_CSV, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert any(r["root_id"] == "SHFE:SS" for r in rows)
    for r in rows:
        if r["root_id"] == "SHFE:SS":
            r["bbg_root"] = "ZZSS"
    placeholder_csv = tmp_path / "contracts.csv"
    with open(placeholder_csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(universe, "CONTRACTS_CSV", placeholder_csv)
    assert universe.get_root("SHFE:SS").bbg_placeholder
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


# --------------------------------------------------------------------------- options on commodity futures (Phase 5)
def _opt(**overrides) -> dict:
    row = _row(**{"Fin Type": "OPTION", "Trade Id": "900000101", "Symbol": "CL/A261117C75-USAA", "Quantity": "10",
                  "Price": "2.15", "TradeDate": "24/8/2026", "Description": "WTI CRUDE OPT Dec26 75 STRIKE CALL"})
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides, contract_id, expiry", [
    ({}, "CLZ26C 75 Comdty", "2026-11-17"),                                           # prime-broker dated form
    ({"Symbol": "CLZ6C 75 Comdty"}, "CLZ26C 75 Comdty", "2026-12-31"),               # Bloomberg, estimated date
    ({"Symbol": "CLZ6C 75"}, "CLZ26C 75 Comdty", "2026-12-31"),                      # yellow key left off
    ({"Symbol": "NYMEX:CLZ6C 75 Comdty"}, "CLZ26C 75 Comdty", "2026-12-31"),          # exchange prefix
    ({"Symbol": "CLZ6-USAA"}, "CLZ26C 75 Comdty", "2026-12-31"),                     # futures form + Description
    ({"Symbol": "CLZ6-USAA", "Description": "WTI OPT", "FxOption Type": "Call", "Strike": "75"},
     "CLZ26C 75 Comdty", "2026-12-31"),                                              # type and strike in columns
    ({"Symbol": "LO/A261117C75-USAA", "Underlying Symbol": "CLZ6-USAA"}, "CLZ26C 75 Comdty", "2026-11-17"),
    ({"Symbol": "", "Underlying Symbol": "CLZ6 Comdty"}, "CLZ26C 75 Comdty", "2026-12-31"),
    ({"Symbol": "CU2612C80000", "Currency": "CNY.C-CNAA", "Execution Venue": "SHFE", "Price": "1,850",
      "Description": "SHFE COPPER OPT CU2612 CALL"}, "CUZ26C 80000 Comdty", "2026-12-31"),   # the Chinese code
    ({"Symbol": "SHFE:CU2612-C-80000", "Currency": "", "Execution Venue": "", "Price": "1,850", "Description": ""},
     "CUZ26C 80000 Comdty", "2026-12-31"),
])
def test_each_option_symbol_form_resolves(tmp_csv, overrides, contract_id, expiry):
    res = blotter.parse(tmp_csv([_opt(**overrides)]))
    assert not res.rejects, res.rejects
    (t,) = res.trades
    assert (t.product, t.instrument_id) == ("CMDTY_OPTION", contract_id)
    assert res.instruments[contract_id].expiry_date == expiry
    assert res.n_cmdty_option == 1 and res.n_option == 0


def test_the_option_instrument_terms_trade_and_leg(tmp_csv):
    res = blotter.parse(tmp_csv([_opt(), _opt(**{"Trade Id": "900000102", "Symbol": "CL/A261117P62-USAA",
                                                  "Side": "Sell", "Price": "1.40",
                                                  "Description": "WTI CRUDE OPT Dec26 62 STRIKE PUT"})]))
    assert not res.rejects and not res.warnings
    call, put = res.trades
    inst = res.instruments["CLZ26C 75 Comdty"]
    assert (inst.asset_class, inst.base_ccy, inst.quote_ccy, inst.multiplier, inst.is_ndf, inst.bbg_ticker,
            inst.expiry_date) == ("CMDTY_OPTION", "NYMEX:CL", "USD", 1000.0, 0, "CLZ6C 75 Comdty", "2026-11-17")
    o = res.instrument_options["CLZ26C 75 Comdty"]
    assert (o.strike, o.option_type, o.payoff, o.barrier_level) == (75.0, "CALL", "AMERICAN", 0.0)
    assert res.instrument_options["CLZ26P 62 Comdty"].option_type == "PUT"
    assert (call.product, call.quantity, call.price, call.trade_date, call.package_id) == \
        ("CMDTY_OPTION", 10.0, 2.15, "2026-08-24", "900000101")
    assert (put.instrument_id, put.quantity, put.price) == ("CLZ26P 62 Comdty", -10.0, 1.40)
    legs = {l.trade_id: l for l in res.legs}
    leg = legs["900000101"]
    assert (leg.leg_no, leg.leg_type, leg.ccy, leg.start_date, leg.settle_date, leg.rate, leg.settles_cash) == \
        (1, "NOTIONAL", "USD", "2026-08-24", "2026-11-17", 2.15, 0)
    assert leg.amount == pytest.approx(10 * 1000 * 2.15)
    assert legs["900000102"].amount == pytest.approx(-10 * 1000 * 1.40)
    assert not res.options_missing_strike


def test_a_european_option_is_vanilla(tmp_csv):
    res = blotter.parse(tmp_csv([_opt(Symbol="CL/E261117C75-USAA")]))
    assert res.instrument_options["CLZ26C 75 Comdty"].payoff == "VANILLA"


def test_a_cny_option_is_in_its_own_currency(tmp_csv):
    row = _opt(Symbol="CU2612C80000", Currency="CNY.C-CNAA", ExtAccount="PB-CN-NMMF", Price="1,850", Quantity="4",
               Description="SHFE COPPER OPT CU2612 80000 STRIKE CALL", **{"Execution Venue": "SHFE"},
               NetInvoice="37,000.00")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and not res.warnings
    inst = res.instruments["CUZ26C 80000 Comdty"]
    assert (inst.base_ccy, inst.quote_ccy, inst.multiplier, inst.bbg_ticker) == \
        ("SHFE:CU", "CNY", 5.0, "CUZ6C 80000 Comdty")
    (leg,) = res.legs
    assert (leg.ccy, leg.amount) == ("CNY", pytest.approx(4 * 5 * 1850.0))


def test_the_option_fill_is_cross_checked_like_a_future(tmp_csv):
    res = blotter.parse(tmp_csv([_opt(Quantity="", NetInvoice="21,500.00")]))   # lots rebuilt from the invoice
    assert res.trades[0].quantity == 10.0 and "NetInvoice / (1000 x Price)" in res.warnings[0].message
    res = blotter.parse(tmp_csv([_opt(NetInvoice="99,999.00")]))
    assert res.trades and "NetInvoice" in res.warnings[0].message


@pytest.mark.parametrize("overrides", [
    {"Symbol": "ES/A261218C6000-USAA", "Description": "S&P EMINI OPT 6000 STRIKE CALL"},
    {"Symbol": "ESZ6C 6000 Index", "Description": ""},
    {"Symbol": "SPX/E261016P7615-USAA", "Description": "SPX 7615 STRIKE PUT"},
    {"Symbol": "", "Underlying Symbol": "ESZ6-USAA"},
])
def test_an_equity_index_option_is_still_skipped(tmp_csv, overrides):
    res = blotter.parse(tmp_csv([_opt(**overrides)]))
    assert not res.rejects and not res.trades and res.n_cmdty_option == 0
    assert res.n_skipped_other == 1 and res.n_skipped_retired == 1
    assert res.skipped_other_rows[0][2].startswith("listed index option on ")


def test_an_unknown_commodity_option_root_rejects_naming_it(tmp_csv):
    res = blotter.parse(tmp_csv([_opt(Symbol="QQ/A261117C75-USAA")]))
    (rj,) = res.rejects
    assert "'QQ'" in rj.reason and "config/contracts.csv" in rj.reason and not res.trades


@pytest.mark.parametrize("overrides, words", [
    ({"Description": "WTI CRUDE OPT Dec26 75 STRIKE PUT"}, "option type"),        # the symbol says call
    ({"Strike": "80"}, "strike"),                                                 # column 80, Description 75
    ({"Symbol": "CU2612C80000", "Currency": "CNY.C-CNAA", "Description": "80000 STRIKE PUT"}, "PUT"),
])
def test_a_contradiction_in_the_option_terms_rejects(tmp_csv, overrides, words):
    res = blotter.parse(tmp_csv([_opt(**overrides)]))
    (rj,) = res.rejects
    assert words in rj.reason and not res.trades


def test_a_cp_letter_in_the_description_is_not_read_as_a_type(tmp_csv):
    """'CPTY-C' at the end of a put's Description must not make it a call (the FX path's hazard)."""
    res = blotter.parse(tmp_csv([_opt(Symbol="CL/A261117P62-USAA", Description="WTI OPT 62 STRIKE PUT CPTY-C")]))
    assert not res.rejects and res.instrument_options["CLZ26P 62 Comdty"].option_type == "PUT"


def test_fx_options_keep_their_own_path(tmp_csv):
    row = _opt(Symbol="EURUSD111826C-500041", **{"Currency Pair": "EURUSD-XXAA"}, Quantity="10,000,000",
               Price="0.0098", Description="EURUSD-XXAA 1.180000 STRIKE EUR Call 11/18/2026")
    res = blotter.parse(tmp_csv([row]))
    assert (res.n_option, res.n_cmdty_option) == (1, 0) and res.trades[0].product == "FX_OPTION"


def test_an_option_writes_its_underlying_futures_instrument_with_no_trade(tmp_csv):
    """The option's Greeks need the underlying's official FUTURE_PX, and a mark hangs off an
    instruments row: the underlying future is written as an instrument only (housekeeper, Phase 5)."""
    res = blotter.parse(tmp_csv([_opt()]))
    assert [t.product for t in res.trades] == ["CMDTY_OPTION"] and len(res.legs) == 1
    und = res.instruments["CLZ26 Comdty"]
    assert (und.asset_class, und.base_ccy, und.quote_ccy, und.multiplier, und.is_ndf, und.bbg_ticker,
            und.expiry_date) == ("FUTURE", "NYMEX:CL", "USD", 1000.0, 0, "CLZ6 Comdty", "2026-12-31")
    assert set(res.instruments) == {"CLZ26C 75 Comdty", "CLZ26 Comdty"} and res.underlying_only == {"CLZ26 Comdty"}
    conn = sqlite3.connect(":memory:")
    blotter.load(tmp_csv([_opt()]), conn)
    assert conn.execute("SELECT instrument_id, asset_class FROM instruments ORDER BY 1").fetchall() == \
        [("CLZ26 Comdty", "FUTURE"), ("CLZ26C 75 Comdty", "CMDTY_OPTION")]
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_the_underlying_never_overwrites_an_instrument_on_file(tmp_csv):
    conn = sqlite3.connect(":memory:")
    blotter.load(tmp_csv([_row()]), conn)                                    # the future, traded
    conn.execute("UPDATE instruments SET expiry_date = '2026-11-19' WHERE instrument_id = 'CLZ26 Comdty'")
    blotter.load(tmp_csv([_opt()]), conn)                                    # later, an option on it alone
    assert conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = 'CLZ26 Comdty'").fetchone() == \
        ("2026-11-19",)


def test_a_future_traded_after_its_option_in_the_file_is_its_own_row(tmp_csv):
    res = blotter.parse(tmp_csv([_opt(), _row(**{"Trade Id": "900000002"})]))
    assert not res.underlying_only and res.instruments["CLZ26 Comdty"].asset_class == "FUTURE"
    assert sorted(t.product for t in res.trades) == ["CMDTY_OPTION", "FUTURE"]


def test_an_option_loads_into_a_database(tmp_csv):
    conn = sqlite3.connect(":memory:")
    blotter.load(tmp_csv([_opt()]), conn)
    assert conn.execute("SELECT asset_class, base_ccy, multiplier, expiry_date FROM instruments "
                        "WHERE instrument_id = 'CLZ26C 75 Comdty'").fetchall() == \
        [("CMDTY_OPTION", "NYMEX:CL", 1000.0, "2026-11-17")]
    assert conn.execute("SELECT strike, option_type, payoff FROM instrument_options").fetchall() == \
        [(75.0, "CALL", "AMERICAN")]
    assert conn.execute("SELECT product, quantity FROM trades").fetchall() == [("CMDTY_OPTION", 10.0)]


# --------------------------------------------------------------------------- LME forwards (Phase 5)
def _lme(**overrides) -> dict:
    row = _row(**{"Fin Type": "FORWARD", "Trade Id": "900000201", "Symbol": "LMCADS03 Comdty", "Quantity": "4",
                  "Price": "9,850", "Execution Venue": "LME", "TradeDate": "10/9/2026", "Settle Date": "",
                  "Description": "LME COPPER 3M"})
    row.update(overrides)
    return row


def _legs(res, trade_id):
    return [(l.leg_no, l.leg_type, l.ccy, l.amount, l.start_date, l.settle_date, l.rate, l.settles_cash)
            for l in res.legs if l.trade_id == trade_id]


def test_a_3m_ticket_takes_the_three_month_date_and_says_so(tmp_csv):
    res = blotter.parse(tmp_csv([_lme()]))
    assert not res.rejects and not res.warnings and res.n_lme_forward == 1 and res.n_forward == 0
    (t,) = res.trades
    assert (t.product, t.instrument_id, t.quantity, t.price, t.trade_date) == \
        ("LME_FWD", "LME:CA", 100.0, 9850.0, "2026-09-10")                           # 4 lots x 25 t
    assert _legs(res, "900000201") == [
        (1, "FX_NEAR", "LME:CA", 100.0, "2026-09-10", "2026-12-10", 9850.0, 0),
        (2, "FX_NEAR", "USD", -985_000.0, "2026-09-10", "2026-12-10", 9850.0, 1)]
    inst = res.instruments["LME:CA"]
    assert (inst.asset_class, inst.base_ccy, inst.quote_ccy, inst.multiplier, inst.is_ndf, inst.bbg_ticker,
            inst.expiry_date) == ("LME_FWD", "LME:CA", "USD", 1.0, 0, "LMCADY Comdty", "9999-12-31")
    (note,) = res.information_notes()
    assert "3-month" in note and "2026-12-10" in note


def test_a_dated_prompt_sold_on_a_future_row(tmp_csv):
    row = _lme(**{"Fin Type": "FUTURE", "Symbol": "AHZ6-USAA", "Side": "Sell", "Quantity": "3", "Price": "2,640",
                  "Settle Date": "16/12/2026", "TradeDate": "3/9/2026", "Description": "LME ALUMINIUM",
                  "NetInvoice": "197,979.00", "Total Fees": "21.00"})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and not res.warnings and not res.information_notes()
    assert (res.n_lme_forward, res.n_future) == (1, 0)
    assert _legs(res, "900000201") == [
        (1, "FX_NEAR", "LME:AH", -75.0, "2026-09-03", "2026-12-16", 2640.0, 0),
        (2, "FX_NEAR", "USD", 198_000.0, "2026-09-03", "2026-12-16", 2640.0, 1)]
    assert res.instruments["LME:AH"].bbg_ticker == "LMAHDY Comdty"


@pytest.mark.parametrize("overrides, root, tonnes, prompt", [
    ({"Symbol": "LPZ6 Comdty", "Execution Venue": "", "Description": ""}, "LME:CA", 100.0, "2026-12-16"),   # the month
    ({"Symbol": "NI", "Quantity": "2", "Settle Date": "16/9/2026", "TradeDate": "2/7/2026",
      "Description": "LME NICKEL"}, "LME:NI", 12.0, "2026-09-16"),                                          # 6 t lots
    ({"Symbol": "", "Description": "LME TIN 2026-12-16"}, "LME:SN", 20.0, "2026-12-16"),                  # 5 t lots
    ({"Prompt Date": "2026-11-18", "Settle Date": ""}, "LME:CA", 100.0, "2026-11-18"),
])
def test_where_the_metal_and_the_prompt_come_from(tmp_csv, overrides, root, tonnes, prompt):
    res = blotter.parse(tmp_csv([_lme(**overrides)]))
    assert not res.rejects and not res.warnings
    (t,) = res.trades
    assert (t.instrument_id, t.quantity) == (root, tonnes)
    assert {l.settle_date for l in res.legs} == {prompt}


def test_an_invalid_prompt_is_a_warning_never_a_reject(tmp_csv):
    res = blotter.parse(tmp_csv([_lme(**{"Settle Date": "17/12/2026"})]))   # a Thursday in the weekly zone
    assert not res.rejects and res.trades[0].product == "LME_FWD"
    (w,) = res.warnings
    assert "2026-12-17" in w.message and "not an LME prompt date" in w.message


def test_an_lme_ferrous_row_stays_a_future(tmp_csv):
    row = _lme(**{"Fin Type": "FUTURE", "Symbol": "SCZ6-USAA", "Price": "365", "Settle Date": "10/9/2026",
                  "Description": "LME STEEL SCRAP FUT Dec26"})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and (res.n_future, res.n_lme_forward) == (1, 0)
    (t,) = res.trades
    assert (t.product, t.instrument_id) == ("FUTURE", "LSCZ26 Comdty")


@pytest.mark.parametrize("overrides, words", [
    ({"Currency": "GBP.C-GBAA"}, "contradicts LME:CA"),
    ({"Symbol": "CA", "Description": "LME CA"}, "no prompt date"),
])
def test_an_lme_row_that_cannot_be_booked_rejects(tmp_csv, overrides, words):
    res = blotter.parse(tmp_csv([_lme(**overrides)]))
    (rj,) = res.rejects
    assert words in rj.reason and not res.trades


def test_an_fx_forward_is_never_an_lme_forward(tmp_csv):
    row = _lme(Symbol="USDCNH111826-500030", **{"Execution Venue": ""},
               Description="TD 08/05/2026 VD 11/18/2026 SELL USD VS .BUY CNH @ 7.14250000",
               **{"Buy Currency": "CNH.C-CNAA", "Sell Currency": "DOL.C-USAA", "BuyCurrency Amount": "10,713,750.00",
                  "SellCurrency Amount": "1,500,000.00"}, Quantity="1,500,000.00", Price="7.1425")
    res = blotter.parse(tmp_csv([row]))
    assert (res.n_forward, res.n_lme_forward) == (1, 0) and res.trades[0].product == "FX_FWD"
