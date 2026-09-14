"""BNP parser -> exposure adapter. Fixture rows are sanitised copies of real
HA_PNL_20260818.csv rows (AUDUSD id 196606908, USDJPY id 196243174)."""
from pathlib import Path

import pytest

from data.ingest import bnp
from engine.ladder.exposure import build_exposure
from engine.ladder.exposure_adapter import records_from_parse
from tests.test_ingest import _cash_row, _fut_row, _fwd_row, _write_csv


def _aud_sell_row():  # SELL AUD VS .BUY USD: real row 54 shape
    return _fwd_row(**{
        "Symbol": "AUDUSD091626-196606908", "Currency": "DOL.C-USAA",
        "Symbol Description": "TD 07/30/2026 VD 09/16/2026 SELL AUD VS .BUY USD @ 0.69469100",
        "Quantity": -2878977.848, "Position": -2878977.848, "Local Cost": -2000000.0, "Cost": -2000000.0,
        "Price": 0.70988, "Fx": 1.0, "Market Value Local": -43733.0, "Market Value Base": -43733.0,
        "Start Date Dirty MV": -43000.0, "Previous Month End Market Value Base": 0.0,
        "DTD Total P&L": -733.0, "DTD Trading P&L": -733.0, "MTD Total P&L": -43733.0,
    })


def _jpy_buy_row():  # SELL USD VS .BUY JPY: real row 93 shape
    return _fwd_row(**{
        "Symbol": "USDJPY091626-196243174",
        "Symbol Description": "TD 07/22/2026 VD 09/16/2026 SELL USD VS .BUY JPY @ 162.27029909",
        "Quantity": -3500000.02, "Position": -3500000.02, "Local Cost": -567946050.0, "Cost": -3558849.092,
        "Price": 159.24108, "Fx": 0.006266, "Market Value Local": 10601703.74, "Market Value Base": 66430.28,
        "Start Date Dirty MV": 66000.0, "Previous Month End Market Value Base": 0.0,
        "DTD Total P&L": 430.28, "DTD Trading P&L": 430.28, "MTD Total P&L": 66430.28,
    })


def _brl_ndf_row():  # SELL USD VS .BUY BRL: real row (id 196354929), non-deliverable
    return _fwd_row(**{
        "Symbol": "USDBRL091626-196354929", "Currency": "BRL.C-BSAA",
        "Symbol Description": "TD 07/23/2026 VD 09/16/2026 SELL USD VS .BUY BRL @ 5.14017400",
        "Quantity": -6000000.0, "Position": -6000000.0, "Local Cost": -30841044.0, "Cost": -5931643.013,
        "Price": 5.23129, "Fx": 0.19233, "Market Value Local": -546696.0, "Market Value Base": -105145.98,
        "Start Date Dirty MV": -105000.0, "Previous Month End Market Value Base": 0.0,
        "DTD Total P&L": -145.98, "DTD Trading P&L": -145.98, "MTD Total P&L": -105145.98,
    })


def test_ndf_included_in_ladder_and_flagged_non_cash(tmp_path):
    res = bnp.parse(_write_csv(tmp_path / "HA_PNL_20260818.csv", [_brl_ndf_row(), _aud_sell_row()]))
    records, unresolved = records_from_parse(res)
    assert unresolved == []
    brl = next(r for r in records if r["currency"] == "BRL")
    assert brl["is_ndf"] == 1 and brl["settles_cash"] == 0
    assert next(r for r in records if r["currency"] == "AUD")["settles_cash"] == 1
    assert brl["local_amount"] == pytest.approx(30841044.0) and brl["usd_entry_amount"] == pytest.approx(6000000.0)
    exp = build_exposure(records, {"BRL": {"rate": 5.23129, "inverted": True, "source": "FIXTURE", "timestamp": "t", "stale": False},
                                   "AUD": {"rate": 0.70988, "inverted": False, "source": "FIXTURE", "timestamp": "t", "stale": False}})
    assert "BRL" in exp.ladder.columns and exp.ladder.loc["2026-09-16", "BRL"] == pytest.approx(30841044.0)
    assert exp.summary.set_index("currency").loc["BRL", "exposure_pnl"] == pytest.approx(30841044.0 / 5.23129 - 6000000.0, abs=0.01)


def test_book_mapping_explicit_not_silent(parsed):
    records, _ = records_from_parse(parsed)
    assert {r["book_source"] for r in records} == {"HAHY7"} and {r["book"] for r in records} == {"HAHY7"}
    mapped, _ = records_from_parse(parsed, book_mapping={"HAHY7": "HA"})
    assert {r["book"] for r in mapped} == {"HA"} and {r["book_source"] for r in mapped} == {"HAHY7"}
    assert build_exposure(mapped, {}, books=["HA"]).contributions.shape[0] == 3
    assert build_exposure(records, {}, books=["HA"]).contributions.empty


@pytest.fixture
def parsed(tmp_path):
    p = _write_csv(tmp_path / "HA_PNL_20260818.csv", [_aud_sell_row(), _jpy_buy_row(), _fwd_row(), _cash_row(), _fut_row()])
    return bnp.parse(p)


def test_field_mapping_and_sign_convention(parsed):
    records, unresolved = records_from_parse(parsed)
    by_id = {r["trade_id"]: r for r in records}
    aud = by_id["196606908"]
    assert aud["settlement_date"] == "2026-09-16" and aud["trade_date"] == "2026-07-30"
    assert aud["currency"] == "AUD" and aud["local_amount"] == pytest.approx(-2878977.848)
    assert aud["usd_entry_amount"] == pytest.approx(-2000000.0)     # sold AUD: local and USD entry both negative
    assert aud["entry_rate"] == 0.694691 and aud["product_type"] == "FX_FWD"
    assert aud["book"] == "HAHY7" and aud["fund"] == "NMMF" and aud["account"] == "BNPP-IPBFX-NMMF"
    jpy = by_id["196243174"]
    assert jpy["currency"] == "JPY" and jpy["local_amount"] == pytest.approx(567946050.0)
    assert jpy["usd_entry_amount"] == pytest.approx(3500000.02)     # bought JPY: both positive
    usd_buy = by_id["111"]                                          # helper row: BUY USD / SELL JPY
    assert usd_buy["local_amount"] == pytest.approx(-147000000.0) and usd_buy["usd_entry_amount"] == pytest.approx(-1000000.0)


def test_currency_and_futures_rows_excluded_and_reported(parsed):
    records, unresolved = records_from_parse(parsed)
    assert len(records) == 3 and unresolved == []
    # The parser records CURRENCY balances and FUTURES as positions only (never trades),
    # so neither reaches the ladder; the counts prove both rows were seen.
    assert parsed.n_currency == 1 and parsed.n_futures == 1
    assert {t.product for t in parsed.trades} == {"FX_FWD"}
    assert {r["currency"] for r in records} == {"AUD", "JPY"}


def test_non_fx_trade_is_reported_not_dropped(parsed):
    from dataclasses import replace
    parsed.trades.append(replace(parsed.trades[0], trade_id="FUT-1", instrument_id="ESU6 Index", product="FUTURE"))
    records, unresolved = records_from_parse(parsed)
    assert len(records) == 3 and [u.reason for u in unresolved] == ["non-FX product FUTURE excluded"]


def test_parser_rejects_surface_as_unresolved(tmp_path):
    bad = _fwd_row(**{"Symbol Description": "TD 08/03/2026 VD 09/16/2026 BUY USD vs SELL JPY @ 147"})
    res = bnp.parse(_write_csv(tmp_path / "HA_PNL_20260818.csv", [bad]))
    records, unresolved = records_from_parse(res)
    assert records == [] and len(unresolved) == 1 and "parser reject" in unresolved[0].reason


def test_records_flow_into_build_exposure(parsed):
    records, _ = records_from_parse(parsed)
    rates = {"AUD": {"rate": 0.70988, "inverted": False, "source": "FIXTURE", "timestamp": "t", "stale": False},
             "JPY": {"rate": 159.24108, "inverted": True, "source": "FIXTURE", "timestamp": "t", "stale": False}}
    res = build_exposure(records, rates)
    assert list(res.ladder.index) == ["2026-09-16"]
    assert res.ladder.loc["2026-09-16", "JPY"] == pytest.approx(567946050.0 - 147000000.0)
    s = res.summary.set_index("currency")
    # AUD: -2,878,977.848 x 0.70988 - (-2,000,000) = -43,728.79 (= Quantity x (Price - rate) in USD)
    assert s.loc["AUD", "exposure_pnl"] == pytest.approx(-2878977.848 * 0.70988 + 2000000.0, abs=0.01)
    assert s.loc["AUD", "exposure_pnl"] == pytest.approx(-2878977.848 * (0.70988 - 0.694691), abs=0.01)
    assert sorted(res.cell_trades("2026-09-16", "JPY")["trade_id"]) == ["111", "196243174"]
    assert res.status["status"].eq("OK").all()


def test_aud_screenshot_result_preserved():
    from tests.test_exposure import AUD_FIXTURE, rate
    s = build_exposure(AUD_FIXTURE, {"AUD": rate(0.600)}).summary.iloc[0]
    assert round(s["usd_delta"]) == -12576974 and round(s["exposure_pnl"]) == 2423026


RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "HA_PNL_20260818.csv"
MOCK_RATES = {  # deterministic USD-per-local shape: non-USD-base pairs quoted direct, USD-base inverted
    "AUD": (0.65, False), "CAD": (0.73, False), "CHF": (1.2, False), "EUR": (1.1, False), "GBP": (1.3, False),
    "HKD": (7.8, True), "JPY": (150.0, True), "MXN": (18.0, True), "NOK": (10.5, True), "SEK": (10.0, True),
    "SGD": (1.3, True), "TRY": (40.0, True), "XAU": (3300.0, False), "ZAR": (18.0, True),
    "BRL": (5.2, True), "TWD": (30.0, True), "KRW": (1380.0, True), "IDR": (16000.0, True),
}


@pytest.mark.skipif(not RAW.exists(), reason="reference BNP file not present")
def test_end_to_end_real_file_diagnostic():
    res = bnp.parse(RAW)
    records, unresolved = records_from_parse(res)
    rates = {c: {"rate": v, "inverted": inv, "source": "MOCK", "timestamp": "2026-08-17T15:00:00-04:00", "stale": False}
             for c, (v, inv) in MOCK_RATES.items()}
    exp = build_exposure(records, rates)

    ccys = sorted({r["currency"] for r in records})
    dates = sorted({r["settlement_date"] for r in records})
    ndf = [r for r in records if r["is_ndf"]]
    report = {
        "parsed_fx_records": len(records), "unresolved": len(unresolved), "ndf_records": len(ndf),
        "currencies": ccys, "settlement_range": (dates[0], dates[-1]), "books": sorted({r["book"] for r in records}),
        "ladder_rows": exp.ladder.shape[0], "ladder_ccy_columns": exp.ladder.shape[1], "summary_rows": len(exp.summary),
        "rate_status": exp.status["status"].value_counts().to_dict(),
    }
    print("\nE2E:", report)

    assert len(records) == 229 and len(unresolved) == 0 and len(ndf) == 45
    assert set(ccys) == set(MOCK_RATES)
    brl = [r for r in ndf if r["currency"] == "BRL"]
    assert brl and all(r["settles_cash"] == 0 for r in brl) and "BRL" in exp.ladder.columns
    assert len(exp.contributions) == 229 and len(exp.summary) == len(ccys)
    assert exp.status["status"].eq("OK").all() and exp.summary["exposure_pnl"].notna().all()
    assert exp.ladder.values.sum() == pytest.approx(sum(r["local_amount"] for r in records))


def test_records_from_db_matches_records_from_parse(parsed):
    from data.ingest import schema
    from engine.ladder.exposure_adapter import records_from_db
    conn = schema.connect()
    conn.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                     [tuple(vars(i).values()) for i in parsed.instruments.values()])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [tuple(vars(t).values()) for t in parsed.trades])
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [tuple(vars(l).values()) for l in parsed.legs])
    from_parse, _ = records_from_parse(parsed)
    from_db, unresolved = records_from_db(conn, "2026-08-17")
    key = lambda r: r["trade_id"]
    assert unresolved == [] and sorted(from_db, key=key) == sorted(from_parse, key=key)
    assert records_from_db(conn, "2026-09-17")[0] == []  # settled trades drop out after the value date
    assert {r["book"] for r in records_from_db(conn, "2026-08-17", book_mapping={"HAHY7": "HA"})[0]} == {"HA"}
