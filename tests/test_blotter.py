"""Tests for data/ingest/blotter.py. The sample-file tests read data/sample/blotter_sample.csv, the
synthetic commodity and FX-hedge book (every value made up; 2026-09-24)."""
from __future__ import annotations

from pathlib import Path
import dataclasses
import datetime
import math

import pandas as pd
import pytest

from data.ingest import blotter, schema

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "data" / "sample" / "blotter_sample.csv"

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
    assert usd_leg.settles_cash == 1  # every FX leg settles (NDFs left the app, 2026-09-24)


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


def test_forward_in_a_former_ndf_currency_is_written_deliverable(tmp_csv):
    """NDFs left the app on 2026-09-24 (commodity conversion, Phase 2): a USDBRL forward is an
    ordinary deliverable forward now, is_ndf 0 and both legs settling cash."""
    row = _forward_row(
        Description="TD 08/20/2026 VD 09/16/2026 SELL BRL VS .BUY USD @ 5.30000000",
        Symbol="USDBRL091626-997",
        **{"Currency Pair": "USDBRL-XXAA", "Buy Currency": "DOL.C-USAA", "Sell Currency": "BRL.C-BRAA",
           "BuyCurrency Amount": "1,000,000.00", "SellCurrency Amount": "5,300,000.00"},
    )
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects
    assert all(l.settles_cash == 1 for l in res.legs)
    assert res.instruments["USDBRL"].is_ndf == 0


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
    # WTI Dec26 (NYMEX:CL, 1,000 bbl, USD); the ES row this was until 2026-09-24 is now skipped
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "FUTURE"},
               **{"Trade Id": "400"}, Symbol="CLZ6-USAA", Side="Sell", Quantity="1",
               Price="68.45", TradeDate="20/8/2026", ExtAccount="GSIL-FUT-NMMF",
               Counterparty="GSILUK", Trader="HA", Description="WTI CRUDE FUT Dec26")
    row.update(overrides)
    return row


def test_future_row_produces_trade_with_signed_contracts(tmp_csv):
    res = blotter.parse(tmp_csv([_future_row()]))
    assert res.n_future == 1
    assert not res.rejects
    t = res.trades[0]
    assert t.product == "FUTURE"
    assert t.instrument_id == "CLZ26 Comdty"
    assert t.quantity == pytest.approx(-1.0)  # Sell -> negative
    assert t.price == pytest.approx(68.45)
    leg = res.legs[0]
    assert leg.leg_type == "NOTIONAL"
    assert leg.ccy == "USD"
    assert leg.amount == pytest.approx(-1 * 1000 * 68.45)
    assert leg.settles_cash == 0


@pytest.mark.parametrize("overrides", [
    {"Symbol": "ESU6-USAA", "Price": "7,716.00", "Description": "S&P500 EMINI FUT  Sep26"},
    {"Symbol": "NQZ6-USAA", "Price": "21,000.00"},
    {"Symbol": "", "Underlying Symbol": "RTYU6-USAA", "Price": "2,300.00"},
    {"Symbol": "YMZ6-USAA", "Price": "45,000", "Quantity": "24-Jul"},    # skipped before any cell is read
])
def test_an_equity_index_future_is_counted_and_skipped_never_rejected(tmp_csv, overrides):
    """The equity index left the app on 2026-09-24 (commodity conversion, Phase 2): an ES / NQ /
    RTY / YM row is counted and named with a plain reason, never rejected, never coerced."""
    res = blotter.parse(tmp_csv([_future_row(**overrides), _future_row(**{"Trade Id": "401"})]))
    assert not res.rejects and [t.trade_id for t in res.trades] == ["401"]
    assert res.n_skipped_other == 1 and res.n_skipped_retired == 1 and res.n_future == 1
    (row_no, _symbol, reason), = res.skipped_other_rows
    assert row_no == 2 and reason.startswith("equity index future (") and "left the app on 2026-09-24" in reason
    assert not [i for i in res.instruments if i.endswith(" Index")]


def test_future_row_buy_side_gives_positive_quantity(tmp_csv):
    res = blotter.parse(tmp_csv([_future_row(**{"Trade Id": "401"}, Side="Buy", Quantity="6")]))
    assert res.trades[0].quantity == pytest.approx(6.0)


def test_future_row_rejects_unknown_root(tmp_csv):
    # CLU6 was the unknown root here until 2026-09-24; it is now a commodity future resolved
    # through the contract master (tests/test_commodity_ingest.py), so a made-up root stands in.
    res = blotter.parse(tmp_csv([_future_row(Symbol="QQU6-USAA")]))
    assert len(res.rejects) == 1
    assert "not in config/contracts.csv" in res.rejects[0].reason


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


# --------------------------------------------------------------------------- IRS (left the app 2026-09-24)
def _irs_row(**overrides) -> dict:
    row = dict(Status="Completed", Fund="NMMF", **{"Fin Type": "INTEREST_RATE_SWAP"},
               **{"Trade Id": "600"}, Symbol="IRSOIS-USD-22860996", Side="Buy",
               Description="IRS NA 11/11/2026 02/11/2027 3.98000000 USD",
               **{"FixedRate": "3.98", "Notional": "625,000,000", "Effective Date": "11/11/2026",
                  "Termination": "11/2/2027", "TradeDate": "7/8/2026",
                  "ExtAccount": "GSCO-DRV-NMMF", "Counterparty": "HSBCUK", "Trader": "HA"})
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides", [
    {},
    {"Notional": "(625,000,000)", "Notes": "Pay Fixed"},    # contradictory directions: a reject until 2026-09-24
    {"Notional": "0"},                                       # likewise
    {"Symbol": "IRS-1", "Description": "", "Currency": ""},  # likewise (no currency anywhere)
    {"Notional": "24-Jul", "FixedRate": "24-Jul"},           # mangled cells are never even read
])
def test_an_interest_rate_swap_is_counted_and_skipped_never_rejected(tmp_csv, overrides):
    """Rates left the app on 2026-09-24 (commodity conversion, Phase 2): a swap row is counted
    and named with a plain reason, never rejected and never booked as anything else."""
    res = blotter.parse(tmp_csv([_irs_row(**overrides), _forward_row()]))
    assert not res.rejects and not res.warnings
    assert [t.product for t in res.trades] == ["FX_FWD"]
    assert res.n_skipped_other == 1 and res.n_skipped_retired == 1
    assert res.skipped_other_rows == [(2, _irs_row(**overrides)["Symbol"], blotter.RETIRED_REASON_IRS)]
    assert not [i for i in res.instruments if i.startswith("IRS")]


# --------------------------------------------------------------------------- de-dupe
def test_repeated_trade_id_keeps_highest_version(tmp_csv):
    # Documented in CLAUDE.md ("a repeated Trade Id within a file keeps the highest
    # Version"), but unexercised by the reference sample (0/857 rows share a Trade Id) --
    # covered here directly instead.
    row_v1 = _future_row(Version="3", Price="68.40")
    row_v2 = _future_row(Version="11", Price="68.50")
    res = blotter.parse(tmp_csv([row_v1, row_v2]))
    assert res.n_superseded == 1
    assert len(res.trades) == 1
    assert res.trades[0].price == pytest.approx(68.50)


def test_repeated_trade_id_keeps_last_row_when_version_column_absent(tmp_csv):
    # HEADER (the fixture's column set) carries no Version column at all -- the
    # documented "else the last occurrence" half of the same rule.
    res = blotter.parse(tmp_csv([_future_row(Price="68.40"), _future_row(Price="68.50")]))
    assert len(res.trades) == 1
    assert res.trades[0].price == pytest.approx(68.50)


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


# --------------------------------------------------------------------------- "swap" labels
# Reviewer finding 2026-09-22: any label with "swap" and no forward word used to book as an
# interest rate swap, so an export saying "FX Swap" would have loaded its fills as IRS.
def test_reference_sample_labels_resolve_as_before():
    # data/raw/new_sample_trades.csv uses these five exact labels (docs/blotter-parser-
    # assumptions.md line 21); the rule change must not move any of them.
    assert {k: blotter._kind_of(k) for k in blotter.IN_SCOPE_TYPES} == {k: k for k in blotter.IN_SCOPE_TYPES}
    assert blotter._kind_of("Futures") == "FUTURE"
    assert blotter._kind_of("FX Forward") == "FORWARD"
    assert blotter._kind_of("Interest Rate Swap") == "INTEREST_RATE_SWAP"


@pytest.mark.parametrize("label", ["Interest Rate Swap", "INTEREST_RATE_SWAP", "IRS", "irs", "OIS Swap",
                                   "Rate Swap", "Rates Swap", "Interest rate swaps"])
def test_swap_label_with_a_rates_word_is_recognised_as_an_interest_rate_swap(label):
    # recognised so that it is skipped with its own reason, never read as an FX swap's fill
    assert blotter._kind_of(label) == "INTEREST_RATE_SWAP"


@pytest.mark.parametrize("label", ["FX Swap", "fx swap", "FX_SWAP", "Currency Swap", "Foreign Exchange Swap",
                                   "Forward Swap", "FX Swaps"])
def test_swap_label_with_an_fx_word_is_a_forward(label):
    assert blotter._kind_of(label) == "FORWARD"


@pytest.mark.parametrize("label", ["Swap", "SWAP", "swaps", "Equity Swap", "Total Return Swap"])
def test_swap_label_with_neither_word_is_not_loaded(label):
    assert blotter._kind_of(label) is None


def test_fx_swap_fin_type_books_the_row_as_a_forward_fill(tmp_csv):
    row = _forward_row(**{"Fin Type": "FX Swap"})
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and res.n_forward == 1 and res.n_skipped_other == 0
    t = res.trades[0]
    assert t.product == "FX_FWD" and t.instrument_id == "USDJPY"
    assert {(l.leg_type, l.ccy) for l in res.legs} == {("FX_NEAR", "USD"), ("FX_NEAR", "JPY")}


def test_fx_swap_fin_type_pair_loads_as_two_outrights(tmp_csv):
    """The FX-swap package rule left the app on 2026-09-24: an FX swap's two fills load as two
    FX_FWD outrights, each its own package, each at its own value date."""
    conn = schema.connect()
    near = _forward_row(**{"Fin Type": "FX Swap", "Trade Id": "801"}, Symbol="USDJPY091626-1")
    far = _forward_row(**{"Fin Type": "FX Swap", "Trade Id": "802"}, Symbol="USDJPY101626-2", Side="Sell",
                       Description="TD 08/20/2026 VD 10/16/2026 BUY JPY VS .SELL USD @ 159.00000000",
                       **{"Buy Currency": "JPY.C-JPAA", "Sell Currency": "DOL.C-USAA",
                          "BuyCurrency Amount": "180,875,000.00", "SellCurrency Amount": "1,137,580.00"})
    blotter.load(tmp_csv([near, far]), conn)
    rows = conn.execute("SELECT trade_id, product, package_id FROM trades ORDER BY trade_id").fetchall()
    assert rows == [("801", "FX_FWD", "801"), ("802", "FX_FWD", "802")]
    assert {r[0] for r in conn.execute("SELECT DISTINCT settle_date FROM trade_legs")} == {"2026-09-16", "2026-10-16"}


def test_irs_fin_type_spelled_out_is_skipped_as_a_rate_swap(tmp_csv):
    for label in ("Interest Rate Swap", "IRS"):
        res = blotter.parse(tmp_csv([_irs_row(**{"Fin Type": label})]))
        assert not res.rejects and not res.trades and res.n_forward == 0, label
        assert res.skipped_other_rows[0][2] == blotter.RETIRED_REASON_IRS


def test_bare_swap_fin_type_is_counted_and_skipped_not_coerced(tmp_csv):
    row = _irs_row(**{"Fin Type": "Swap"}, Product="")
    res = blotter.parse(tmp_csv([row]))
    assert not res.trades and not res.rejects
    assert res.n_forward == 0 and res.n_skipped_other == 1 and res.n_skipped_retired == 0
    assert res.skipped_other_rows[0][2] == "type not loaded by the app: Fin Type 'Swap', Product ''"


def test_bare_swap_fin_type_still_falls_back_to_product(tmp_csv):
    row = _irs_row(**{"Fin Type": "Swap"}, Product="Interest Rate Swap")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and not res.trades and res.n_skipped_retired == 1
    assert res.skipped_other_rows[0][2] == blotter.RETIRED_REASON_IRS


# --------------------------------------------------------------------------- load()
def test_parse_and_load_take_no_swap_direction_arguments_any_more():
    import inspect

    assert "direction_overrides" not in inspect.signature(blotter.parse).parameters
    assert "turn_swap_marks" not in inspect.signature(blotter.load).parameters
    for gone in ("irs_directions", "direction_overrides", "n_direction_overrides", "n_irs", "n_skipped_irs"):
        assert not hasattr(blotter.ParseResult(), gone), gone


def test_load_needs_no_swap_review_table(tmp_csv):
    """The swap_review table left with the package rule: a load on a database without one
    writes the trades (it used to delete from it and dissolve FX_SWAP packages)."""
    conn = schema.connect()
    conn.execute("DROP TABLE IF EXISTS swap_review")
    res = blotter.load(tmp_csv([_forward_row()]), conn)
    assert len(res.trades) == 1
    assert blotter.load(tmp_csv([_forward_row()]), conn).n_updated == 1


def test_load_writes_to_db(tmp_csv):
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
    products = {r[0] for r in conn.execute("SELECT product FROM trades")}
    assert products == {"FX_FWD"}


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
def test_sample_file_parses_with_only_its_two_deliberate_rejects():
    res = blotter.parse(SAMPLE)
    assert res.n_forward == 8
    assert res.n_currency == 1
    assert res.n_future == 31
    assert res.n_option == 5
    assert res.n_cmdty_option == 4 and res.n_lme_forward == 3        # Phase 5 (2026-09-24)
    assert res.n_skipped_other == 0 and res.n_skipped_retired == 0   # no macro product in the commodity book
    assert res.n_skipped_status_or_fund == 0
    # the two futures the contract master cannot resolve: an ambiguous bare code, an unknown root
    assert [r.symbol for r in res.rejects] == ["ZCZ6-USAA", "QQZ6-USAA"]
    # 29 futures + 8 forwards + 1 spot (the CURRENCY row names two currencies) + 5 FX options
    # + 4 options on futures + 3 LME forwards (two legs each)
    assert res.n_spot == 1
    assert len(res.trades) == 29 + 8 + 1 + 5 + 4 + 3
    assert len(res.legs) == 29 + (8 + 1) * 2 + 5 + 4 + 3 * 2
    spot = [t for t in res.trades if t.product == "FX_SPOT"]
    assert [(t.trade_id, t.instrument_id) for t in spot] == [("910000040", "EURUSD")]
    assert not res.warnings


def test_sample_file_loads_into_db(tmp_csv):
    conn = schema.connect()
    res = blotter.load(SAMPLE, conn, strict=False)
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


# =========================================================================== TASK B
# No text may ever reach a numeric column. Linked incident (2026-09-18): the app showed
# "could not convert string to float" and the offending text looked like a date, "24 Jul"
# -- Excel turns a number such as 7.24 into the date 24-Jul, and the user uploads real
# .xlsx files whose date cells read_table accepts.
DATE_CELL = datetime.datetime(2026, 7, 24)          # what Excel makes of 7.24
UNPARSEABLE_FWD_DESC = "free text the export changed"


@pytest.fixture
def tmp_xlsx(tmp_path):
    """Rows -> a real .xlsx built with openpyxl, so a datetime value is a genuine Excel
    date cell (not text) by the time blotter.read_table sees it."""
    import openpyxl

    counter = {"n": 0}

    def _make(rows: list[dict]) -> Path:
        counter["n"] += 1
        columns = list(HEADER) + sorted({k for r in rows for k in r} - set(HEADER))
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(columns)
        for r in rows:
            ws.append([r.get(c, "") for c in columns])
        p = tmp_path / f"blot_{counter['n']}.xlsx"
        wb.save(p)
        return p

    return _make


def _assert_db_numeric(conn):
    for table, column in [("trades", "quantity"), ("trades", "price"), ("trade_legs", "amount"),
                          ("trade_legs", "rate"), ("instruments", "multiplier"), ("instrument_options", "strike")]:
        kinds = {r[0] for r in conn.execute(f"SELECT DISTINCT typeof({column}) FROM {table}")}
        assert kinds <= {"real", "integer"}, f"{table}.{column} holds {kinds}"
    assert blotter.non_numeric_cells(conn) == []


@pytest.mark.parametrize("cell", [
    "24-Jul", "24 Jul", "Jul-24", "24.Jul", "7/24/2026", "24/07/2026", "2026-07-24", "2026-07-24 00:00:00",
    "05:45:36", "1 Dec", "abc", "1,2,3", "24,07,2026", "1e999", "inf", "nan", "", " ", "-", "N/A",
    DATE_CELL, DATE_CELL.date(), datetime.time(5, 45, 36), pd.Timestamp("2026-07-24"), True, None, float("nan"), float("inf"),
])
def test_num_refuses_anything_that_is_not_a_whole_number_cell(cell):
    """The hole that existed: every non-digit was deleted before float(), so '24 Jul'
    read as 24.0, 'Jul-24' as -24.0, '7/24/2026' as 7242026.0, '05:45:36' as 54536.0
    and '1e999' as inf. All of them are NaN now, so the caller recovers or rejects."""
    assert math.isnan(blotter._num(cell))


@pytest.mark.parametrize("cell,value", [
    ("1,137,580.00", 1137580.0), ("35,000,000", 35e6), ("0.00579", 0.00579), ("(625,000,000)", -625e6),
    ("-142,500.00", -142500.0), ("142,500.00-", -142500.0), ("−5", -5.0), ("$1,000", 1000.0), ("USD 5", 5.0),
    ("5 USD", 5.0), ("(USD 5)", -5.0), (" 7.24 ", 7.24), ("1e5", 1e5), ("1.5E+06", 1.5e6),
    ("0,00579", 0.00579), ("1.234.567,89", 1234567.89), ("1 000 000", 1e6), ("1'000'000.5", 1000000.5),
    (".5", 0.5), ("+5", 5.0), ("0", 0.0), (7, 7.0), (7.24, 7.24),
])
def test_num_still_reads_every_number_layout(cell, value):
    assert blotter._num(cell) == pytest.approx(value)


# ---- FORWARD
@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
def test_forward_price_that_arrives_as_a_date_is_rebuilt_from_the_amounts(request, make, cell):
    """Forward Price from the BuyCurrency Amount / SellCurrency Amount ratio oriented by
    the pair (quote / base), for '24-Jul' text and for a real Excel datetime cell."""
    bad = _forward_row(Description=UNPARSEABLE_FWD_DESC, TradeDate="20/8/2026", **{"Settle Date": "16/9/2026"}, Price=cell)
    good = _forward_row(**{"Trade Id": "112"})
    res = blotter.parse(request.getfixturevalue(make)([bad, good]))
    assert not res.rejects and len(res.trades) == 2
    t = next(t for t in res.trades if t.trade_id == "111")
    assert t.price == pytest.approx(180_042_000.00 / 1_137_580.00) and t.quantity == pytest.approx(1_137_580.00)
    assert all(l.rate == t.price for l in res.legs if l.trade_id == "111")
    (w,) = res.warnings
    assert w.row_no == 2 and "Price" in w.message and "is not a number" in w.message and "reads as a date" in w.message
    assert ("24-Jul" in w.message) or ("2026-07-24" in w.message)


def test_forward_price_as_a_date_with_a_parseable_description_takes_the_description_rate(tmp_csv):
    res = blotter.parse(tmp_csv([_forward_row(Price="24-Jul")]))
    assert not res.rejects
    assert res.trades[0].price == pytest.approx(158.26755)
    assert "'24-Jul'" in res.warnings[0].message and "the Description" in res.warnings[0].message


@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
def test_forward_quantity_that_arrives_as_a_date_does_not_matter_when_the_amounts_are_there(request, make, cell):
    res = blotter.parse(request.getfixturevalue(make)([_forward_row(Quantity=cell)]))
    assert not res.rejects
    assert res.trades[0].quantity == pytest.approx(1_137_580.00)


@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
def test_forward_amount_that_arrives_as_a_date_is_rebuilt_from_quantity_or_rate(request, make, cell):
    make_file = request.getfixturevalue(make)
    # base amount (USD, the bought currency) from Quantity
    res = blotter.parse(make_file([_forward_row(**{"BuyCurrency Amount": cell}, Quantity="1,137,580.00")]))
    assert not res.rejects and res.trades[0].quantity == pytest.approx(1_137_580.00)
    assert "BuyCurrency Amount" in res.warnings[0].message and "Quantity" in res.warnings[0].message
    # quote amount (JPY) from base x rate
    res = blotter.parse(make_file([_forward_row(**{"SellCurrency Amount": cell})]))
    assert not res.rejects
    jpy = next(l for l in res.legs if l.ccy == "JPY")
    assert jpy.amount == pytest.approx(-1_137_580.00 * 158.26755)


@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
def test_forward_with_an_unrecoverable_date_cell_rejects_that_one_row_and_the_rest_loads(request, make, cell):
    """Nothing left to rebuild the rate from: that ONE row is rejected, naming the column
    and the offending value; the other row still loads; nothing is stored as text or 0."""
    bad = _forward_row(Description=UNPARSEABLE_FWD_DESC, TradeDate="20/8/2026", **{"Settle Date": "16/9/2026"},
                       Price=cell, Quantity=cell, **{"BuyCurrency Amount": "", "SellCurrency Amount": ""})
    good = _forward_row(**{"Trade Id": "112"})
    conn = schema.connect()
    res = blotter.load(request.getfixturevalue(make)([bad, good]), conn)
    (rj,) = res.rejects
    assert rj.row_no == 2 and "Price" in rj.reason and "Quantity" in rj.reason and "is not a number" in rj.reason
    assert ("'24-Jul'" in rj.reason) or ("2026-07-24" in rj.reason)
    assert [r[0] for r in conn.execute("SELECT trade_id FROM trades")] == ["112"]
    _assert_db_numeric(conn)


# ---- OPTION
@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
def test_option_price_that_arrives_as_a_date_is_rebuilt_from_netinvoice_over_quantity(request, make, cell):
    row = _option_row(Price=cell, NetInvoice="202,650.00")
    res = blotter.parse(request.getfixturevalue(make)([row]))
    assert not res.rejects
    assert res.trades[0].price == pytest.approx(0.00579) and res.legs[0].rate == pytest.approx(0.00579)
    assert "Price" in res.warnings[0].message and "|NetInvoice| / |Quantity|" in res.warnings[0].message


@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
def test_option_quantity_that_arrives_as_a_date_is_rebuilt_from_netinvoice_over_price(request, make, cell):
    make_file = request.getfixturevalue(make)
    res = blotter.parse(make_file([_option_row(Quantity=cell, NetInvoice="202,650.00")]))
    assert not res.rejects
    assert res.trades[0].quantity == pytest.approx(35_000_000.0)
    assert "Quantity" in res.warnings[0].message
    # the sign still comes from Side, never from NetInvoice
    res = blotter.parse(make_file([_option_row(Quantity=cell, NetInvoice="202,650.00", Side="Sell")]))
    assert res.trades[0].quantity == pytest.approx(-35_000_000.0)


@pytest.mark.parametrize("make,cell", [("tmp_csv", "24-Jul"), ("tmp_xlsx", DATE_CELL)])
@pytest.mark.parametrize("column", ["Price", "Quantity"])
def test_option_with_an_unrecoverable_date_cell_rejects_that_one_row_and_the_rest_loads(request, make, cell, column):
    bad = _option_row(**{column: cell})                       # no NetInvoice to rebuild from
    good = _option_row(**{"Trade Id": "501"})
    conn = schema.connect()
    res = blotter.load(request.getfixturevalue(make)([bad, good]), conn)
    (rj,) = res.rejects
    assert rj.row_no == 2 and column in rj.reason and "is not a number" in rj.reason
    assert ("'24-Jul'" in rj.reason) or ("2026-07-24" in rj.reason)
    assert [r[0] for r in conn.execute("SELECT trade_id FROM trades")] == ["501"]
    _assert_db_numeric(conn)


# ---- FUTURE, spot
def test_future_price_and_quantity_as_dates_are_rebuilt_from_the_invoice(tmp_csv):
    # Sell 10 WTI @ 68.45 with 21.00 of fees: NetInvoice = 10 x 1,000 x 68.45 - 21 = 684,479.00
    price_bad = _future_row(Quantity="10", Price="24-Jul", NetInvoice="684,479.00", **{"Total Fees": "21"})
    res = blotter.parse(tmp_csv([price_bad]))
    assert not res.rejects
    assert res.trades[0].price == pytest.approx(68.45) and res.trades[0].quantity == -10.0
    qty_bad = _future_row(Quantity="24-Jul", Price="68.45", NetInvoice="684,479.00")
    res = blotter.parse(tmp_csv([qty_bad]))
    assert not res.rejects and res.trades[0].quantity == -10.0
    assert "NetInvoice / (1000 x Price)" in res.warnings[0].message
    # Notional is never a source of contracts (the ES-only rebuild left with the equity index)
    res = blotter.parse(tmp_csv([_future_row(Quantity="24-Jul", Price="68.45", Notional="10,000")]))
    assert "Quantity '24-Jul' is not a number" in res.rejects[0].reason and not res.trades
    # a buy carries the fees the other way round: 6 x 1,000 x 68.72 + 12.60
    buy = _future_row(Side="Buy", Quantity="6", Price="24-Jul", NetInvoice="412,332.60", **{"Total Fees": "12.6"})
    assert blotter.parse(tmp_csv([buy])).trades[0].price == pytest.approx(68.72)


def test_future_with_an_unrecoverable_date_cell_is_rejected_by_name(tmp_csv):
    res = blotter.parse(tmp_csv([_future_row(Price="24-Jul"), _future_row(**{"Trade Id": "401"})]))
    (rj,) = res.rejects
    assert "Price '24-Jul' is not a number" in rj.reason
    assert [t.trade_id for t in res.trades] == ["401"]


def test_future_price_rebuilt_from_the_invoice_matches_every_sample_fill():
    df = blotter.read_table(SAMPLE)
    futures = df[df["Fin Type"] == "FUTURE"].copy()
    fills = {r["Trade Id"]: blotter._num(r["Price"]) for _, r in futures.iterrows()}
    futures["Price"] = "24-Jul"
    res = blotter.parse(futures)
    # the sample's two deliberate rejects stay rejects (contract not resolved, before any price);
    # 29 futures and the LME aluminium row on a FUTURE Fin Type (an LME forward, same rebuild)
    assert [r.symbol for r in res.rejects] == ["ZCZ6-USAA", "QQZ6-USAA"] and len(res.trades) == 30
    for t in res.trades:
        assert t.price == pytest.approx(fills[t.trade_id], rel=1e-9)   # NetInvoice = fill x size +/- fees


def test_spot_row_price_as_a_date_is_rebuilt_and_an_unrecoverable_amount_is_rejected(tmp_csv):
    res = blotter.parse(tmp_csv([_currency_spot_row(Price="24-Jul")]))
    assert not res.rejects and res.n_spot == 1
    assert res.trades[0].price == pytest.approx(376_879.45 / 273_204.00)
    assert "Price '24-Jul'" in res.warnings[0].message
    bad = _currency_spot_row(**{"BuyCurrency Amount": "24-Jul", "SellCurrency Amount": "24-Jul"}, Quantity="", Price="")
    res = blotter.parse(tmp_csv([bad, _currency_spot_row(**{"Trade Id": "938038061"})]))
    (rj,) = res.rejects
    assert "BuyCurrency Amount '24-Jul' is not a number" in rj.reason
    assert [t.trade_id for t in res.trades] == ["938038061"]


# ---- the gate and the diagnostics
def test_enforce_numeric_drops_a_trade_whose_numbers_are_not_finite_numbers():
    """The last gate before a write: whatever a parse path did, only finite numbers can
    reach a REAL column. Exercised directly, since no parse path produces these."""
    from data.ingest.common import Instrument, Trade, TradeLeg

    res = blotter.ParseResult()
    res.instruments["USDJPY"] = Instrument("USDJPY", "FX", "USD", "JPY", 1.0, 0, "USDJPY Curncy", "9999-12-31")
    common = dict(source="XLSX", instrument_id="USDJPY", product="FX_FWD", trade_date="2026-08-20",
                  account="", counterparty="", strategy="", trader="", description="")
    res.trades += [Trade(trade_id="ok", package_id="ok", quantity=1e6, price=158.0, **common),
                   Trade(trade_id="text", package_id="text", quantity=1e6, price="24-Jul", **common),
                   Trade(trade_id="nan", package_id="nan", quantity=float("nan"), price=158.0, **common),
                   Trade(trade_id="leg", package_id="leg", quantity=1e6, price=158.0, **common)]
    res.legs += [TradeLeg("ok", 1, "FX_NEAR", "USD", 1e6, "2026-08-20", "2026-09-16", 158.0, 1),
                 TradeLeg("leg", 1, "FX_NEAR", "USD", float("inf"), "2026-08-20", "2026-09-16", 158.0, 1)]
    res.trade_rows.update({"text": 7, "nan": 8, "leg": 9})
    blotter._enforce_numeric(res)
    assert [t.trade_id for t in res.trades] == ["ok"] and [l.trade_id for l in res.legs] == ["ok"]
    assert {(r.row_no, r.reason.split(";")[0]) for r in res.rejects} == {
        (7, "not a finite number: trades.price = '24-Jul'"), (8, "not a finite number: trades.quantity = nan"),
        (9, "not a finite number: trade_legs.amount (leg 1) = inf")}


def test_a_file_full_of_mangled_cells_never_puts_text_in_a_numeric_column(tmp_xlsx):
    rows = [_forward_row(Description=UNPARSEABLE_FWD_DESC, TradeDate="20/8/2026", **{"Settle Date": "16/9/2026"}, Price=DATE_CELL),
            _forward_row(**{"Trade Id": "113"}, Quantity="24 Jul", Price="Jul-24"),
            _option_row(Price=DATE_CELL, NetInvoice="202,650.00"),
            _option_row(**{"Trade Id": "502"}, Quantity="7/24/2026"),                     # unrecoverable
            _option_row(**{"Trade Id": "503"}, Symbol="EURSEK112526C-197906813", Strike="24-Jul",
                        Description="EURSEK-XXAA EUR Call 11/25/2026 SBILUK"),             # strike cell is a date
            _future_row(Price=datetime.time(5, 45, 36)),                                   # unrecoverable
            _future_row(**{"Trade Id": "402"}),
            _currency_spot_row(Price="24/07/2026")]
    conn = schema.connect()
    res = blotter.load(tmp_xlsx(rows), conn)
    assert sorted(r.row_no for r in res.rejects) == [5, 7]
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 6
    assert conn.execute("SELECT strike FROM instrument_options WHERE instrument_id='EURSEK112526C-197906813'").fetchone()[0] == 0
    assert res.options_missing_strike == ["EURSEK112526C-197906813"]
    _assert_db_numeric(conn)


def test_non_numeric_cells_names_text_already_sitting_in_a_database(tmp_csv):
    conn = schema.connect()
    blotter.load(tmp_csv([_forward_row(), _option_row()]), conn)
    assert blotter.non_numeric_cells(conn) == []
    conn.execute("UPDATE trades SET price = '24-Jul' WHERE trade_id = '111'")
    conn.execute("UPDATE trade_legs SET rate = '24 Jul' WHERE trade_id = '111' AND leg_no = 2")
    conn.execute("UPDATE instrument_options SET strike = 'eleven'")
    conn.execute("UPDATE trades SET quantity = '35000000' WHERE trade_id = '500'")     # numeric text: SQLite stores REAL
    found = blotter.non_numeric_cells(conn)
    assert [(f["table"], f["key"], f["column"], f["value"], f["stored_as"]) for f in found] == [
        ("trades", "trade_id=111", "price", "24-Jul", "text"),
        ("trade_legs", "trade_id=111, leg_no=2", "rate", "24 Jul", "text"),
        ("instrument_options", "instrument_id=EURSEK092326C-197727826", "strike", "eleven", "text")]
    assert conn.execute("SELECT price FROM trades WHERE trade_id='111'").fetchone()[0] == "24-Jul"   # report only


# =========================================================================== TASK C
def test_option_netinvoice_sign_is_never_used_for_direction(tmp_csv):
    """Reference Trade Id 934168029: Side Buy, NetInvoice -142,500.00. Side carries the
    direction; the magnitude agrees with Quantity x Price, so there is no warning."""
    row = _option_row(Symbol="USDJPY111926P-197571137", **{"Currency Pair": "USDJPY-XXAA"}, Quantity="1,000,000",
                      Price="0.1425", NetInvoice="-142,500.00", Description="USDJPY-XXAA EUR Put 11/19/2026 MLILUK")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and not res.warnings
    assert res.trades[0].quantity == pytest.approx(1_000_000.0)
    sell = blotter.parse(tmp_csv([_option_row(Side="Sell", NetInvoice="202,650.00")]))
    assert sell.trades[0].quantity == pytest.approx(-35_000_000.0) and not sell.warnings


def test_option_netinvoice_mismatch_above_half_a_percent_warns_and_never_rejects(tmp_csv):
    within = blotter.parse(tmp_csv([_option_row(NetInvoice="203,600.00")]))      # +0.47 %
    assert not within.rejects and not within.warnings
    beyond = blotter.parse(tmp_csv([_option_row(NetInvoice="204,000.00")]))      # +0.67 %
    assert not beyond.rejects and len(beyond.trades) == 1
    assert beyond.trades[0].price == pytest.approx(0.00579)                      # the Price fill is kept
    (w,) = beyond.warnings
    assert "NetInvoice" in w.message and "0.67%" in w.message


def test_sample_option_terms_and_the_digital_with_no_strike():
    res = blotter.parse(SAMPLE)
    assert not res.warnings                                   # NetInvoice = |Quantity x Price| on all 5
    terms = {k: (o.strike, o.option_type, res.instruments[k].expiry_date) for k, o in res.instrument_options.items()
             if res.instruments[k].asset_class == "FX_OPTION"}       # the options on futures: test_commodity_ingest
    assert terms == {
        "EURUSD111826C-500041": (pytest.approx(1.18), "CALL", "2026-11-18"),
        "USDJPY121626P-500042": (pytest.approx(142.5), "PUT", "2026-12-16"),
        # no "<n> STRIKE" in the Description and no strike column in the export: the
        # schema's 0 = "not known" sentinel (never a real strike), and named for the UI
        "USDJPY111926P-500043": (0.0, "PUT", "2026-11-19"),
        "EURUSD102126P-500044": (pytest.approx(1.15), "PUT", "2026-10-21"),
        "EURUSD102126P-500045": (pytest.approx(1.15), "PUT", "2026-10-21"),
    }
    assert res.options_missing_strike == ["USDJPY111926P-500043"]
    assert any("1 option(s) have no strike" in n for n in res.notes())
    signs = {t.trade_id: t.quantity for t in res.trades if t.product == "FX_OPTION"}
    assert signs["910000045"] == pytest.approx(-5_000_000.0)  # the one Sell: the closed-out pair's sell-back
    assert sum(1 for q in signs.values() if q > 0) == 4


def test_a_strike_entered_in_the_app_survives_a_re_upload_of_the_same_file(tmp_path):
    from data.ingest import upload

    digital = "USDJPY111926P-500043"
    # library path (merge)
    conn = schema.connect()
    blotter.load(SAMPLE, conn)
    conn.execute("UPDATE instrument_options SET strike = 152, payoff = 'DIGITAL' WHERE instrument_id = ?", (digital,))
    conn.commit()
    res = blotter.load(SAMPLE, conn)
    assert conn.execute("SELECT strike, payoff FROM instrument_options WHERE instrument_id = ?", (digital,)
                        ).fetchone() == (152.0, "DIGITAL")
    assert digital in res.options_missing_strike   # the FILE still has none
    # the app's upload path (full replace of the book)
    db = tmp_path / "risk.db"
    upload.import_blotter(SAMPLE.read_bytes(), SAMPLE.name, db)
    live = schema.connect(db)
    live.execute("UPDATE instrument_options SET strike = 150.5 WHERE instrument_id = ?", (digital,))
    live.commit()
    live.close()
    upload.import_blotter(SAMPLE.read_bytes(), SAMPLE.name, db)
    live = schema.connect(db)
    assert live.execute("SELECT strike FROM instrument_options WHERE instrument_id = ?", (digital,)).fetchone()[0] == 150.5
    live.close()


# =========================================================================== TASK A
def test_percent_sign_is_handled_per_column_never_silently(tmp_csv):
    """S-4: '%' used to be stripped everywhere. A percent-unit column keeps its meaning
    (3.98 = 3.98 %), an option Price is a fraction so '0.58%' = 0.0058 with a warning,
    anywhere else it is not a number."""
    assert math.isnan(blotter._num("3.98%"))
    assert blotter._num("3.98%", blotter.PERCENT_KEEP) == pytest.approx(3.98)
    assert blotter._num("0.58%", blotter.PERCENT_FRACTION) == pytest.approx(0.0058)
    res = blotter.parse(tmp_csv([_option_row(Price="0.579%")]))
    assert not res.rejects and res.trades[0].price == pytest.approx(0.00579)
    assert "percent sign" in res.warnings[0].message
    res = blotter.parse(tmp_csv([_future_row(Price="68.45%")]))
    assert "Price '68.45%' is not a number" in res.rejects[0].reason


def test_every_rebuild_warns_blank_or_not_and_a_date_serial_price_is_caught(tmp_csv):
    """W-3: a BLANK cell rebuilt from another column used to be silent. The reviewer's
    case: option Quantity blank, NetInvoice 204,150 (a fee inside) -> notional 35,259,067."""
    res = blotter.parse(tmp_csv([_option_row(Quantity="", NetInvoice="204,150.00")]))
    assert res.trades[0].quantity == pytest.approx(204_150 / 0.00579)
    assert "Quantity is blank" in res.warnings[0].message and "NetInvoice" in res.warnings[0].message
    res = blotter.parse(tmp_csv([_future_row(Quantity="", NetInvoice="68,450.00")]))
    assert "Quantity is blank" in res.warnings[0].message and "NetInvoice / (1000 x Price)" in res.warnings[0].message
    res = blotter.parse(tmp_csv([_forward_row(**{"BuyCurrency Amount": ""}, Quantity="1,137,580.00")]))
    assert "BuyCurrency Amount is blank" in res.warnings[0].message and "Quantity" in res.warnings[0].message
    assert not blotter.parse(tmp_csv([_forward_row()])).warnings          # the normal path stays quiet
    # an Excel date serial in Price is a fine float: only the consistency check sees it
    serial = _currency_spot_row(Price="46227", NetInvoice="376,879.45")
    res = blotter.parse(tmp_csv([serial]))
    assert not res.rejects and "odd one out" in res.warnings[0].message
    assert res.trades[0].price == pytest.approx(376_879.45 / 273_204.00)
    res = blotter.parse(tmp_csv([_currency_spot_row(Price="46227", Quantity="")]))   # not doubly confirmed: kept
    assert res.trades[0].price == 46227.0 and "kept as read" in res.warnings[0].message


def test_notes_are_counts_and_names_and_split_information_from_warnings(tmp_csv):
    assert blotter._some(["a", "b", "c"]) == "a, b, c"
    assert blotter._some([str(i) for i in range(8)]) == "0, 1, 2, 3, 4, 5, 6, 7"
    assert blotter._some([str(i) for i in range(9)]) == "0, 1, 2, 3, 4 and 4 more"
    no_strike = [_option_row(**{"Trade Id": str(510 + i)}, Symbol=f"EURSEK112526C-{i}",
                             Description="EURSEK-XXAA EUR Call 11/25/2026 SBILUK") for i in range(10)]
    res = blotter.parse(tmp_csv(no_strike + [_option_row(Price="24-Jul", NetInvoice="202,650.00")]))
    info, warn = res.information_notes(), res.warning_notes()
    assert res.notes() == info + warn
    (i,) = info                                              # the one kind of information left: missing strikes
    assert i.startswith("10 option(s) have no strike in the file:") and "and 5 more" in i
    assert not any("24-Jul" in n for n in info)             # a rebuilt cell is a warning, not information
    (w,) = warn
    assert w.startswith("1 cell(s) in 1 row(s) were doubtful") and "rows 12" in w and "Price '24-Jul'" in w


# --------------------------------------------------------------------------- the user's live options export, 2026-09-21
def test_option_symbol_carrying_the_delivery_date_loads_with_the_descriptions_expiry(tmp_csv):
    """'USDZAR101326C' with 'Call 10/09/2026': the Symbol date is the delivery date (expiry + 2
    business days over a weekend and a holiday). It was rejected as a contradiction, so the ZAR,
    CHF and one EURSEK option were missing from the book."""
    row = _option_row(Symbol="USDZAR101326C-198564638", **{"Currency Pair": "", "Underlying Symbol": "USDZAR-XXAA"},
                      Quantity="25,000,000", Price="0.005315",
                      Description="USDZAR-XXAA 16.350000 STRIKE EUR Call 10/09/2026 SBILUK")
    res = blotter.parse(tmp_csv([row]))
    assert res.rejects == [] and len(res.trades) == 1
    assert res.instruments["USDZAR101326C-198564638"].expiry_date == "2026-10-09"
    assert res.instrument_options["USDZAR101326C-198564638"].strike == 16.35
    assert any("delivery date" in str(w) for w in res.warnings)


def test_spx_index_option_is_counted_and_skipped_never_rejected(tmp_csv):
    """The listed index options left the app with the equity index on 2026-09-24."""
    row = _option_row(Symbol="SPX/E261016P7615-USAA", **{"Currency Pair": ""}, Quantity="15", Price="121.5",
                      Description="SPX 7615 STRIKE EUR PUT 10/16/2026")
    res = blotter.parse(tmp_csv([row, _option_row(**{"Trade Id": "501"})]))
    assert res.rejects == [] and [t.trade_id for t in res.trades] == ["501"]
    assert res.n_skipped_other == 1 and res.n_skipped_retired == 1
    (row_no, symbol, reason), = res.skipped_other_rows
    assert (row_no, symbol) == (2, "SPX/E261016P7615-USAA")
    assert reason.startswith("listed index option on SPX:") and "left the app on 2026-09-24" in reason
    assert "SPX/E261016P7615" not in res.instruments and "SPX/E261016P7615" not in res.instrument_options


def test_a_listed_option_on_a_commodity_future_loads_since_phase_5(tmp_csv):
    """A listed option that is not an index one is an option on a commodity future since Phase 5
    (2026-09-24; it was skipped as "not loaded yet" before): never read as an FX option.
    tests/test_commodity_ingest.py covers the path."""
    row = _option_row(Symbol="CL/A261116C75-USAA", **{"Currency Pair": ""}, Quantity="10", Price="1.25",
                      Description="WTI 75 STRIKE CALL 11/16/2026")
    res = blotter.parse(tmp_csv([row]))
    assert not res.rejects and res.n_skipped_other == 0 and res.n_option == 0 and res.n_cmdty_option == 1
    (t,) = res.trades
    assert (t.product, t.instrument_id, t.quantity, t.price) == ("CMDTY_OPTION", "CLZ26C 75 Comdty", 10.0, 1.25)


def test_gold_option_premium_in_usd_per_ounce_is_not_flagged_as_above_the_notional(tmp_csv):
    row = _option_row(Symbol="XAUUSD101526P-199095036", **{"Currency Pair": "", "Underlying Symbol": "XAUUSD-XXAA"},
                      Quantity="4635.37", Price="125.941005788",
                      Description="XAUUSD-XXAA 4314.650000 STRIKE EUR Put 10/15/2026 SCBANK")
    res = blotter.parse(tmp_csv([row]))
    assert res.rejects == [] and res.trades[0].quantity == 4635.37 and res.trades[0].price == 125.941005788
    assert not any("above 100" in str(w) for w in res.warnings)
