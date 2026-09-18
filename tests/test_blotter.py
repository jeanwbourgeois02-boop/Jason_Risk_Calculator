"""Tests for data/ingest/blotter.py. Real-file tests skip if the raw sample is absent."""
from __future__ import annotations

from pathlib import Path
import dataclasses

import pandas as pd
import pytest

from data.ingest import blotter, schema, swaps

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw" / "new_sample_trades.csv"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason=f"raw file absent: {RAW}")

# Minimal header covering every column blotter.py reads by name, so synthetic single-row
# CSVs below don't depend on column order (mirrors bnp.py test style: name lookups only).
HEADER = ["Status", "Fund", "Fin Type", "Trade Id", "Description", "Side", "Symbol",
          "Quantity", "Price", "Currency", "TradeDate", "Settle Date", "ExtAccount",
          "Counterparty", "Trader", "Currency Pair", "Buy Currency", "Sell Currency",
          "BuyCurrency Amount", "SellCurrency Amount"]


def _csv_text(rows: list[dict]) -> str:
    df = pd.DataFrame(rows)
    for col in HEADER:
        if col not in df.columns:
            df[col] = ""
    return df.to_csv(index=False)


@pytest.fixture
def tmp_csv(tmp_path):
    counter = {"n": 0}

    def _make(rows: list[dict]) -> Path:
        counter["n"] += 1
        p = tmp_path / f"blot_{counter['n']}.csv"
        p.write_text(_csv_text(rows))
        return p

    return _make


def _forward_row(**overrides) -> dict:
    row = dict(
        Status="Completed", Fund="NMMF", **{"Fin Type": "FORWARD"},
        **{"Trade Id": "111"},
        Description="TD 08/20/2026 VD 09/16/2026 SELL JPY VS .BUY USD @ 158.26755000",
        Side="Buy", Symbol="USDJPY091626-999",
        **{"Currency Pair": "USDJPY-XXAA", "Buy Currency": "DOL.C-USAA", "Sell Currency": "JPY.C-JPAA",
           "BuyCurrency Amount": "1,137,580.00", "SellCurrency Amount": "180,042,000.00"},
        ExtAccount="BNPP-IPBFX-NMMF", Counterparty="SCBANK", Trader="HA",
    )
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- FORWARD
def test_parse_forward_row_produces_trade_and_two_legs(tmp_csv):
    p = tmp_csv([_forward_row()])
    res = blotter.parse(p)
    assert res.n_forward == 1
    assert not res.rejects
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.trade_id == "111"
    assert t.product == "FX_FWD"
    assert t.instrument_id == "USDJPY"
    assert t.quantity == pytest.approx(1_137_580.00)
    assert t.price == pytest.approx(158.26755)
    legs = res.legs
    assert len(legs) == 2
    usd_leg = next(l for l in legs if l.ccy == "USD")
    jpy_leg = next(l for l in legs if l.ccy == "JPY")
    assert usd_leg.amount == pytest.approx(1_137_580.00)
    assert jpy_leg.amount == pytest.approx(-180_042_000.00)
    assert usd_leg.settle_date == "2026-09-16"
    assert usd_leg.settles_cash == 1  # neither ccy is in NDF_CCYS


def test_parse_forward_sell_base_ccy_gives_negative_quantity(tmp_csv):
    row = _forward_row(
        **{"Trade Id": "222"},
        Description="TD 08/20/2026 VD 09/16/2026 SELL USD VS .BUY JPY @ 158.30000000",
        Symbol="USDJPY091626-998",
        **{"Buy Currency": "JPY.C-JPAA", "Sell Currency": "DOL.C-USAA",
           "BuyCurrency Amount": "158,300,000.00", "SellCurrency Amount": "1,000,000.00"},
    )
    res = blotter.parse(tmp_csv([row]))
    t = res.trades[0]
    assert t.quantity == pytest.approx(-1_000_000.00)  # sold USD (the base ccy)


def test_forward_rejects_symbol_mismatch(tmp_csv):
    row = _forward_row(Symbol="EURJPY091626-999")  # pair disagrees with description
    res = blotter.parse(tmp_csv([row]))
    assert res.n_forward == 1
    assert len(res.rejects) == 1
    assert "disagree" in res.rejects[0].reason


def test_forward_with_unparseable_description_falls_back_to_structured_columns(tmp_csv):
    via_desc = blotter.parse(tmp_csv([_forward_row()]))
    via_cols = blotter.parse(tmp_csv([_forward_row(
        Description="free text the export changed", TradeDate="20/8/2026",
        **{"Settle Date": "16/9/2026"}, Price="158.26755")]))
    assert not via_cols.rejects
    assert via_cols.trades[0] == dataclasses.replace(via_desc.trades[0], description="free text the export changed")
    assert [(l.ccy, l.amount, l.settle_date, l.rate) for l in via_cols.legs] == \
           [(l.ccy, l.amount, l.settle_date, l.rate) for l in via_desc.legs]
    assert via_cols.trades[0].trade_date == "2026-08-20"


def test_forward_with_no_description_and_no_dates_is_rejected_not_guessed(tmp_csv):
    res = blotter.parse(tmp_csv([_forward_row(Description="")]))
    assert len(res.rejects) == 1
    assert "trade date" in res.rejects[0].reason


def test_forward_rejects_blank_trade_id(tmp_csv):
    row = _forward_row(**{"Trade Id": ""})
    res = blotter.parse(tmp_csv([row]))
    assert len(res.rejects) == 1
    assert "blank Trade Id" in res.rejects[0].reason


def test_forward_rejects_symbol_currency_pair_disagreement_with_buy_sell_columns(tmp_csv):
    row = _forward_row(**{"Buy Currency": "EUR.C-EUAA"})  # now disagrees with pair USDJPY
    res = blotter.parse(tmp_csv([row]))
    assert len(res.rejects) == 1
    assert "Buy/Sell Currency columns" in res.rejects[0].reason


def test_forward_ndf_currency_sets_settles_cash_zero(tmp_csv):
    row = _forward_row(
        Description="TD 08/20/2026 VD 09/16/2026 SELL BRL VS .BUY USD @ 5.30000000",
        Symbol="USDBRL091626-997",
        **{"Currency Pair": "USDBRL-XXAA", "Buy Currency": "DOL.C-USAA", "Sell Currency": "BRL.C-BRAA",
           "BuyCurrency Amount": "1,000,000.00", "SellCurrency Amount": "5,300,000.00"},
    )
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    assert all(l.settles_cash == 0 for l in res.legs)
    assert res.instruments["USDBRL"].is_ndf == 1


# --------------------------------------------------------------------------- CURRENCY
def test_currency_row_writes_instrument_but_no_trade(tmp_csv):
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "CURRENCY"},
               **{"Trade Id": "300"}, Symbol="EUR.C-EUAA", Side="Buy")
    res = blotter.parse(tmp_csv([row]))
    assert res.n_currency == 1
    assert not res.rejects
    assert "CASH-EUR" in res.instruments
    assert not res.trades


def _currency_spot_row(**overrides) -> dict:
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "CURRENCY"}, **{"Trade Id": "938038060"},
               Symbol="DOL.C-USAA", Side="Buy", Description="UNITED STATES DOLLARS", Currency="CAD.C-CNAA",
               **{"Buy Currency": "DOL.C-USAA", "Sell Currency": "CAD.C-CNAA",
                  "BuyCurrency Amount": "273,204.00", "SellCurrency Amount": "376,879.45",
                  "Price": "1.37948", "TradeDate": "24/8/2026", "Settle Date": "25/8/2026", "Quantity": "273,204"})
    row.update(overrides)
    return row


def test_currency_row_with_two_currencies_is_a_spot_trade_with_two_legs(tmp_csv):
    """User's cash-ladder spec (2026-09-18): a CURRENCY row naming both currencies is a
    spot FX fill and enters the book like a forward -- product FX_SPOT, two FX_NEAR
    legs on the settle date, our side kept as the file gives it (Buy USD / Sell CAD)."""
    res = blotter.parse(tmp_csv([_currency_spot_row()]))
    assert not res.rejects and res.n_currency == 1 and res.n_spot == 1
    assert "CASH-USD" in res.instruments and "USDCAD" in res.instruments
    (t,) = res.trades
    assert (t.product, t.instrument_id, t.trade_id, t.trade_date) == ("FX_SPOT", "USDCAD", "938038060", "2026-08-24")
    assert t.quantity == pytest.approx(273204.0) and t.price == pytest.approx(1.37948)
    legs = {l.ccy: l for l in res.legs}
    assert legs["USD"].amount == pytest.approx(273204.0) and legs["CAD"].amount == pytest.approx(-376879.45)
    assert all(l.settle_date == "2026-08-25" and l.settles_cash == 1 and l.leg_type == "FX_NEAR" for l in res.legs)


def test_currency_spot_row_tolerates_blank_amounts_pair_and_dates(tmp_csv):
    """Tolerance rule: amounts from Quantity x Price, pair by market convention when the
    column is '0', settle date from the trade date when blank -- never a reject."""
    row = _currency_spot_row(**{"BuyCurrency Amount": "", "SellCurrency Amount": "", "Currency Pair": "0",
                                "Settle Date": ""})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and res.n_spot == 1
    legs = {l.ccy: l for l in res.legs}
    assert legs["USD"].amount == pytest.approx(273204.0)
    assert legs["CAD"].amount == pytest.approx(-273204.0 * 1.37948)
    assert legs["CAD"].settle_date == "2026-08-24"
    # a sell of the base currency flips both legs
    res = blotter.parse(tmp_csv([_currency_spot_row(Side="Sell", **{"Buy Currency": "CAD.C-CNAA", "Sell Currency": "DOL.C-USAA",
                                                                       "BuyCurrency Amount": "376,879.45",
                                                                       "SellCurrency Amount": "273,204.00"})]))
    assert res.trades[0].quantity == pytest.approx(-273204.0)


def test_currency_row_with_one_currency_stays_instrument_only(tmp_csv):
    row = _currency_spot_row(**{"Sell Currency": "", "SellCurrency Amount": ""})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and res.n_spot == 0 and not res.trades
    assert "CASH-USD" in res.instruments


def test_currency_row_rejects_bad_symbol(tmp_csv):
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "CURRENCY"},
               **{"Trade Id": "300"}, Symbol="NOT-A-CASH-SYMBOL")
    res = blotter.parse(tmp_csv([row]))
    assert len(res.rejects) == 1


def test_currency_row_blank_symbol_falls_back_on_side_not_on_currency_column(tmp_csv):
    # Shape observed on the reference sample: 'Currency' is the OTHER leg's ccy (SEK on
    # a EUR cash row), never this row's own -- a blank Symbol must fall back to
    # Buy/Sell Currency (Side-aware), not to the misleading Currency column.
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "CURRENCY"},
               **{"Trade Id": "300"}, Symbol="", Side="Sell",
               **{"Currency": "SEK.C-SSAA", "Buy Currency": "SEK.C-SSAA", "Sell Currency": "EUR.C-EUAA"})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    assert "CASH-EUR" in res.instruments
    assert "CASH-SEK" not in res.instruments


def test_currency_row_blank_symbol_buy_side_uses_buy_currency(tmp_csv):
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "CURRENCY"},
               **{"Trade Id": "301"}, Symbol="", Side="Buy",
               **{"Currency": "CAD.C-CNAA", "Buy Currency": "DOL.C-USAA", "Sell Currency": "CAD.C-CNAA"})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    assert "CASH-USD" in res.instruments


def test_currency_row_blank_symbol_and_side_is_rejected_not_guessed(tmp_csv):
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "CURRENCY"},
               **{"Trade Id": "302"}, Symbol="", Side="",
               **{"Currency": "SEK.C-SSAA", "Buy Currency": "SEK.C-SSAA", "Sell Currency": "EUR.C-EUAA"})
    res = blotter.parse(tmp_csv([row]))
    assert len(res.rejects) == 1


# --------------------------------------------------------------------------- FUTURE
def _future_row(**overrides) -> dict:
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "FUTURE"},
               **{"Trade Id": "400"}, Symbol="ESU6-USAA", Side="Sell", Quantity="1",
               Price="7,716.00", TradeDate="20/8/2026", ExtAccount="GSIL-FUT-NMMF",
               Counterparty="GSILUK", Trader="HA", Description="S&P500 EMINI FUT  Sep26")
    row.update(overrides)
    return row


def test_future_row_produces_trade_with_signed_contracts(tmp_csv):
    res = blotter.parse(tmp_csv([_future_row()]))
    assert res.n_future == 1
    assert not res.rejects
    t = res.trades[0]
    assert t.product == "FUTURE"
    assert t.instrument_id == "ESU6 Index"
    assert t.quantity == pytest.approx(-1.0)  # Sell -> negative
    assert t.price == pytest.approx(7716.00)
    leg = res.legs[0]
    assert leg.leg_type == "NOTIONAL"
    assert leg.ccy == "USD"
    assert leg.amount == pytest.approx(-1 * 50 * 7716.00)
    assert leg.settles_cash == 0


def test_future_row_buy_side_gives_positive_quantity(tmp_csv):
    res = blotter.parse(tmp_csv([_future_row(**{"Trade Id": "401"}, Side="Buy", Quantity="6")]))
    assert res.trades[0].quantity == pytest.approx(6.0)


def test_future_row_rejects_unknown_root(tmp_csv):
    res = blotter.parse(tmp_csv([_future_row(Symbol="CLU6-USAA")]))
    assert len(res.rejects) == 1
    assert "unknown futures root" in res.rejects[0].reason


# --------------------------------------------------------------------------- OPTION
def _option_row(**overrides) -> dict:
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "OPTION"},
               **{"Trade Id": "500"}, Symbol="EURSEK092326C-197727826",
               **{"Currency Pair": "EURSEK-XXAA"}, Side="Buy", Quantity="35,000,000",
               Price="0.00579", TradeDate="21/8/2026", ExtAccount="BNPP-IPBFX-NMMF",
               Counterparty="UBSLUK", Trader="HA",
               Description="EURSEK-XXAA 11.058400 STRIKE EUR Call 09/23/2026 UBSLUK")
    row.update(overrides)
    return row


def test_option_row_produces_fx_option_trade(tmp_csv):
    res = blotter.parse(tmp_csv([_option_row()]))
    assert res.n_option == 1
    assert not res.rejects
    t = res.trades[0]
    assert t.product == "FX_OPTION"
    assert t.instrument_id == "EURSEK092326C-197727826"
    assert t.quantity == pytest.approx(35_000_000.0)
    assert t.price == pytest.approx(0.00579)
    leg = res.legs[0]
    assert leg.leg_type == "NOTIONAL"
    assert leg.ccy == "EUR"
    assert leg.settle_date == "2026-09-23"
    assert leg.settles_cash == 0


def test_option_row_sell_side_gives_negative_quantity(tmp_csv):
    res = blotter.parse(tmp_csv([_option_row(**{"Trade Id": "501"}, Side="Sell")]))
    assert res.trades[0].quantity == pytest.approx(-35_000_000.0)


def test_option_row_rejects_currency_pair_mismatch(tmp_csv):
    res = blotter.parse(tmp_csv([_option_row(**{"Currency Pair": "USDJPY-XXAA"})]))
    assert len(res.rejects) == 1
    assert "Currency Pair" in res.rejects[0].reason


def test_option_row_rejects_symbol_description_expiry_disagreement(tmp_csv):
    # Symbol says 09/23/2026 (092326); Description now says a different date.
    row = _option_row(Description="EURSEK-XXAA 11.058400 STRIKE EUR Call 09/24/2026 UBSLUK")
    res = blotter.parse(tmp_csv([row]))
    assert len(res.rejects) == 1
    assert "expiry" in res.rejects[0].reason


def test_option_row_rejects_symbol_description_call_put_disagreement(tmp_csv):
    row = _option_row(Description="EURSEK-XXAA 11.058400 STRIKE EUR Put 09/23/2026 UBSLUK")
    res = blotter.parse(tmp_csv([row]))
    assert len(res.rejects) == 1
    assert "call/put" in res.rejects[0].reason


def test_option_row_blank_description_skips_cross_check(tmp_csv):
    # No Description text to disagree with: Symbol alone is still enough (tolerance
    # rule -- a blank field never rejects).
    res = blotter.parse(tmp_csv([_option_row(Description="")]))
    assert not res.rejects


def test_option_row_no_strike_in_description_keeps_zero_sentinel(tmp_csv):
    # Reproduces the 3 reference-sample rows with a real Description but no STRIKE
    # number in it (EURSEK112526C-197906813, USDJPY111926P-197571137/197957397):
    # genuinely absent from the file, not a parser gap -- 0.0 sentinel, never invented.
    row = _option_row(Symbol="EURSEK112526C-197906813", Description="EURSEK-XXAA EUR Call 11/25/2026 SBILUK")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    assert res.instrument_options["EURSEK112526C-197906813"].strike == 0.0


def test_option_row_fallback_reads_expiry_and_call_put_from_description(tmp_csv):
    # Symbol doesn't match <PAIR><mmddyy>[CP]-<id>: falls back to Currency Pair for the
    # pair and to the Description's own mm/dd/yyyy date + CALL/PUT word for the rest.
    row = _option_row(Symbol="EURSEK-OTC-1", Description="EURSEK-XXAA 11.058400 STRIKE EUR Put 09/05/2026 UBSLUK")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    t = res.trades[0]
    assert res.instruments[t.instrument_id].expiry_date == "2026-09-05"
    assert res.instrument_options[t.instrument_id].option_type == "PUT"


# --------------------------------------------------------------------------- IRS
def _irs_row(**overrides) -> dict:
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "INTEREST_RATE_SWAP"},
               **{"Trade Id": "600"}, Symbol="IRSOIS-USD-22860996", Side="Buy",
               Description="IRS NA 11/11/2026 02/11/2027 3.98000000 USD",
               **{"FixedRate": "3.98", "Notional": "625,000,000", "Effective Date": "11/11/2026",
                  "Termination": "11/2/2027", "TradeDate": "7/8/2026",
                  "ExtAccount": "GSCO-DRV-NMMF", "Counterparty": "HSBCUK", "Trader": "HA"})
    row.update(overrides)
    return row


def test_irs_row_positive_notional_is_payer(tmp_csv):
    res = blotter.parse(tmp_csv([_irs_row()]))
    assert not res.rejects
    assert res.n_skipped_irs == 0
    t = res.trades[0]
    assert t.product == "IRS"
    assert t.instrument_id == "IRSOIS-USD-22860996"
    assert t.quantity == pytest.approx(625_000_000.0)  # signed Notional, full units (matches irs.py)
    assert t.price == pytest.approx(0.0398)
    fixed = next(l for l in res.legs if l.leg_type == "FIXED")
    floating = next(l for l in res.legs if l.leg_type == "FLOAT")
    assert fixed.amount == pytest.approx(-625_000_000.0)  # payer: negative FIXED leg
    assert floating.amount == pytest.approx(625_000_000.0)  # positive FLOAT leg
    assert fixed.rate == pytest.approx(0.0398)
    assert floating.rate == 0.0
    assert fixed.settle_date == "2027-02-11"
    assert fixed.settles_cash == 0 and floating.settles_cash == 0


def test_irs_row_negative_notional_is_receiver(tmp_csv):
    res = blotter.parse(tmp_csv([_irs_row(**{"Trade Id": "601", "Notional": "-625,000,000"})]))
    assert not res.rejects
    t = res.trades[0]
    assert t.quantity == pytest.approx(-625_000_000.0)
    fixed = next(l for l in res.legs if l.leg_type == "FIXED")
    floating = next(l for l in res.legs if l.leg_type == "FLOAT")
    assert fixed.amount == pytest.approx(625_000_000.0)  # receiver: positive FIXED leg
    assert floating.amount == pytest.approx(-625_000_000.0)


@pytest.mark.parametrize("overrides", [
    {"Notional": "(625,000,000)"},                     # brackets on Notional
    {"Notional": "625,000,000", "Quantity": "(625)"},  # Notional unsigned, brackets on Quantity
    {"Notional": "625,000,000", "Quantity": "-625"},   # Notional unsigned, minus on Quantity
    {"Notional": "", "Quantity": "(625)"},             # Quantity only (millions), in brackets
    {"Notional": "-625,000,000", "Quantity": "-625"},  # both signed: still one short, not a double flip
])
def test_irs_row_brackets_or_minus_on_either_column_is_short(tmp_csv, overrides):
    res = blotter.parse(tmp_csv([_irs_row(**{"Trade Id": "603", **overrides})]))
    assert not res.rejects
    assert res.trades[0].quantity == pytest.approx(-625_000_000.0)
    fixed = next(l for l in res.legs if l.leg_type == "FIXED")
    assert fixed.amount == pytest.approx(625_000_000.0)  # receiver: positive FIXED leg


def test_irs_row_positive_quantity_column_stays_payer(tmp_csv):
    res = blotter.parse(tmp_csv([_irs_row(**{"Trade Id": "604", "Quantity": "625"})]))
    assert not res.rejects
    assert res.trades[0].quantity == pytest.approx(625_000_000.0)


def test_irs_row_rejects_zero_notional(tmp_csv):
    res = blotter.parse(tmp_csv([_irs_row(**{"Trade Id": "602", "Notional": "0"})]))
    assert len(res.rejects) == 1
    assert res.n_skipped_irs == 1
    assert "Notional is zero" in res.rejects[0].reason


def test_irs_row_with_unrecognised_symbol_falls_back_to_description_and_columns(tmp_csv):
    res = blotter.parse(tmp_csv([_irs_row(Symbol="IRS-USD-1")]))
    assert not res.rejects
    t = res.trades[0]
    assert t.instrument_id == "IRS-USD-1"
    assert res.instruments["IRS-USD-1"].base_ccy == "USD"
    assert t.quantity == pytest.approx(625_000_000.0)


def test_irs_row_with_no_currency_anywhere_is_rejected(tmp_csv):
    res = blotter.parse(tmp_csv([_irs_row(Symbol="IRS-1", Description="", Currency="")]))
    assert len(res.rejects) == 1
    assert res.n_skipped_irs == 1
    assert "currency" in res.rejects[0].reason


# --------------------------------------------------------------------------- de-dupe
def test_repeated_trade_id_keeps_highest_version(tmp_csv):
    # Documented in CLAUDE.md ("a repeated Trade Id within a file keeps the highest
    # Version"), but unexercised by the reference sample (0/857 rows share a Trade Id) --
    # covered here directly instead.
    row_v1 = _future_row(Version="3", Price="7,700.00")
    row_v2 = _future_row(Version="11", Price="7,750.00")
    res = blotter.parse(tmp_csv([row_v1, row_v2]))
    assert res.n_superseded == 1
    assert len(res.trades) == 1
    assert res.trades[0].price == pytest.approx(7750.00)


def test_repeated_trade_id_keeps_last_row_when_version_column_absent(tmp_csv):
    # HEADER (the fixture's column set) carries no Version column at all -- the
    # documented "else the last occurrence" half of the same rule.
    res = blotter.parse(tmp_csv([_future_row(Price="7,700.00"), _future_row(Price="7,750.00")]))
    assert len(res.trades) == 1
    assert res.trades[0].price == pytest.approx(7750.00)


# --------------------------------------------------------------------------- filters
def test_non_completed_status_is_skipped(tmp_csv):
    row = _forward_row(Status="Cancelled")
    res = blotter.parse(tmp_csv([row]))
    assert res.n_skipped_status_or_fund == 1
    assert res.n_forward == 0
    assert not res.trades


def test_non_nmmf_fund_is_skipped(tmp_csv):
    row = _forward_row(Fund="OTHERFUND")
    res = blotter.parse(tmp_csv([row]))
    assert res.n_skipped_status_or_fund == 1


def test_unknown_fin_type_is_skipped(tmp_csv):
    row = _forward_row(**{"Fin Type": "CDS"})
    res = blotter.parse(tmp_csv([row]))
    assert res.n_skipped_other == 1
    assert not res.trades


# --------------------------------------------------------------------------- load()
def test_load_writes_to_db_and_swap_packaging_still_works(tmp_csv):
    conn = schema.connect()
    row1 = _forward_row(**{"Trade Id": "700"}, Symbol="USDJPY091626-1",
                        Description="TD 08/20/2026 VD 09/16/2026 SELL JPY VS .BUY USD @ 158.00000000",
                        **{"BuyCurrency Amount": "1,000,000.00", "SellCurrency Amount": "158,000,000.00"})
    row2 = _forward_row(**{"Trade Id": "701"}, Symbol="USDJPY092326-2",
                        Description="TD 08/20/2026 VD 09/23/2026 SELL USD VS .BUY JPY @ 158.10000000",
                        **{"Buy Currency": "JPY.C-JPAA", "Sell Currency": "DOL.C-USAA",
                           "BuyCurrency Amount": "158,100,000.00", "SellCurrency Amount": "1,000,000.00"})
    p = tmp_csv([row1, row2])
    res = blotter.load(p, conn)
    assert len(res.trades) == 2
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == 4
    packaged = swaps.package_swaps(conn)
    assert packaged == 2
    products = {r[0] for r in conn.execute("SELECT product FROM trades")}
    assert products == {"FX_SWAP"}


def test_load_strict_raises_on_reject(tmp_csv):
    conn = schema.connect()
    p = tmp_csv([_forward_row(Description="garbage")])
    with pytest.raises(ValueError):
        blotter.load(p, conn, strict=True)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_load_non_strict_inserts_valid_rows_and_skips_rejects(tmp_csv):
    conn = schema.connect()
    good = _forward_row(**{"Trade Id": "800"})
    bad = _forward_row(**{"Trade Id": "801"}, Description="garbage")
    p = tmp_csv([good, bad])
    res = blotter.load(p, conn, strict=False)
    assert len(res.rejects) == 1
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


# --------------------------------------------------------------------------- real file
@needs_raw
def test_real_sample_file_parses_with_no_rejects():
    res = blotter.parse(RAW)
    assert res.n_forward == 743
    assert res.n_currency == 85
    assert res.n_future == 11
    assert res.n_option == 8
    assert res.n_skipped_irs == 0  # all 10 IRS rows have positive Notional -> payer, parsed
    assert res.n_skipped_other == 0
    assert res.n_skipped_status_or_fund == 0
    assert not res.rejects
    # 743 forward + 85 spot (every CURRENCY row names two currencies) + 11 future
    # + 8 option + 10 IRS trades (2026-09-18: CURRENCY rows are spot fills)
    assert res.n_spot == 85
    assert len(res.trades) == 743 + 85 + 11 + 8 + 10
    assert len(res.legs) == (743 + 85) * 2 + 11 + 8 + 10 * 2
    spot = [t for t in res.trades if t.product == "FX_SPOT"]
    assert len(spot) == 85 and all(t.trade_id for t in spot)
    assert {t.instrument_id for t in spot} == {"EURSEK", "EURUSD", "USDCAD", "USDHKD", "USDJPY", "XAUUSD"}
    irs_trades = [t for t in res.trades if t.product == "IRS"]
    assert len(irs_trades) == 10
    assert all(t.quantity > 0 for t in irs_trades)  # every reference-sample Notional is positive


@needs_raw
def test_real_sample_file_loads_into_db(tmp_csv):
    conn = schema.connect()
    res = blotter.load(RAW, conn, strict=False)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == len(res.trades)
    assert conn.execute("SELECT COUNT(*) FROM trade_legs").fetchone()[0] == len(res.legs)
    # instrument foreign keys all resolve (schema has FKs on and they're enforced above)
    dangling = conn.execute(
        "SELECT COUNT(*) FROM trades t LEFT JOIN instruments i ON t.instrument_id = i.instrument_id "
        "WHERE i.instrument_id IS NULL").fetchone()[0]
    assert dangling == 0


# --------------------------------------------------------------------------- strike column
def test_option_strike_read_from_structured_column_when_description_has_none(tmp_csv):
    row = _option_row(Symbol="EURSEK112526C-197906813",
                      Description="EURSEK-XXAA EUR Call 11/25/2026 SBILUK", Strike="11.25")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    assert res.instrument_options["EURSEK112526C-197906813"].strike == pytest.approx(11.25)


def test_option_strike_from_description_when_no_column(tmp_csv):
    res = blotter.parse(tmp_csv([_option_row()]))
    assert res.instrument_options["EURSEK092326C-197727826"].strike == pytest.approx(11.0584)


def test_option_strike_column_contradicting_description_rejects(tmp_csv):
    res = blotter.parse(tmp_csv([_option_row(Strike="11.30")]))
    assert len(res.rejects) == 1
    assert "strike column" in res.rejects[0].reason


def test_option_strike_column_agreeing_with_description_is_fine(tmp_csv):
    res = blotter.parse(tmp_csv([_option_row(**{"Strike Price": "11.0584"})]))
    assert not res.rejects
    assert res.instrument_options["EURSEK092326C-197727826"].strike == pytest.approx(11.0584)
