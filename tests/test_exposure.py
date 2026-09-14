"""Tests for engine/ladder/exposure.py against docs/CASH_LADDER_SPEC.md."""
import math

import pytest

from engine.ladder.exposure import ROUNDING_TOLERANCE_USD, build_exposure, usd_per_local


def rec(tid, date, ccy, local, usd_entry, book="HA"):
    return {"trade_id": tid, "settlement_date": date, "book": book, "currency": ccy,
            "local_amount": local, "usd_entry_amount": usd_entry}


def rate(value, inverted=False, stale=False, source="MOCK", ts="2026-09-14T15:00:00-04:00"):
    return {"rate": value, "inverted": inverted, "source": source, "timestamp": ts, "stale": stale}


# Screenshot gives only the currency-level USD entry (-15,000,000); it is carried on one
# trade here because the fixture does not disclose per-trade entry amounts.
AUD_FIXTURE = [
    rec("A1", "2026-09-11", "AUD", -20_763_351, -15_000_000),
    rec("A2", "2026-09-16", "AUD", 9_764_499, 0),
    rec("A3", "2026-09-24", "AUD", -21_109_276, 0),
    rec("A4", "2026-09-28", "AUD", 11_146_505, 0),
]


def _row(res, ccy):
    return res.summary.set_index("currency").loc[ccy]


def test_aud_screenshot_fixture():
    res = build_exposure(AUD_FIXTURE, {"AUD": rate(0.600)})
    s = _row(res, "AUD")
    assert s["local_delta"] == -20_961_623
    assert abs(s["usd_delta"] - (-12_576_974)) <= ROUNDING_TOLERANCE_USD
    assert s["usd_delta_entry"] == -15_000_000
    assert abs(s["exposure_pnl"] - 2_423_026) <= ROUNDING_TOLERANCE_USD
    assert s["status"] == "OK"
    assert list(res.ladder.index) == ["2026-09-11", "2026-09-16", "2026-09-24", "2026-09-28"]
    assert list(res.ladder["AUD"]) == [-20_763_351, 9_764_499, -21_109_276, 11_146_505]
    print("\nAUD fixture:", {k: s[k] for k in ["fx_rate", "local_delta", "usd_delta", "usd_delta_entry", "exposure_pnl"]})


def test_same_date_different_entry_rates_no_averaging():
    recs = [rec("T1", "2026-10-01", "AUD", 100_000, 100_000 * 0.65),
            rec("T2", "2026-10-01", "AUD", 200_000, 200_000 * 0.67)]
    res = build_exposure(recs, {"AUD": rate(0.70)})
    s = _row(res, "AUD")
    assert s["local_delta"] == 300_000
    assert s["usd_delta_entry"] == pytest.approx(199_000)
    assert s["usd_delta"] == pytest.approx(210_000)
    assert s["exposure_pnl"] == pytest.approx(11_000)
    assert res.ladder.loc["2026-10-01", "AUD"] == 300_000


def test_multiple_settlement_dates_and_currencies():
    recs = [rec("T1", "2026-10-01", "AUD", 100, 65), rec("T2", "2026-10-05", "AUD", -40, -26),
            rec("T3", "2026-10-05", "CAD", 500, 370)]
    res = build_exposure(recs, {"AUD": rate(0.7), "CAD": rate(0.74)})
    assert res.ladder.shape == (2, 2)
    assert res.ladder.loc["2026-10-01", "CAD"] == 0
    assert res.ladder.loc["2026-10-05", "AUD"] == -40
    assert _row(res, "AUD")["local_delta"] == 60
    assert _row(res, "CAD")["usd_delta"] == pytest.approx(370)


def test_usdjpy_inverted_conversion():
    assert usd_per_local(rate(150.0, inverted=True)) == pytest.approx(1 / 150)
    assert usd_per_local(rate(0.70)) == 0.70
    res = build_exposure([rec("J1", "2026-10-01", "JPY", 150_000_000, 1_000_000)],
                         {"JPY": rate(150.0, inverted=True)})
    s = _row(res, "JPY")
    assert s["fx_rate"] == pytest.approx(1 / 150)
    assert s["usd_delta"] == pytest.approx(1_000_000)
    assert s["exposure_pnl"] == pytest.approx(0, abs=1e-6)


def test_missing_rate_is_flagged_not_zero():
    res = build_exposure([rec("B1", "2026-10-01", "BRL", 1_000_000, 180_000)], {})
    s = _row(res, "BRL")
    assert s["status"] == "MISSING_RATE"
    assert math.isnan(s["usd_delta"]) and math.isnan(s["exposure_pnl"])
    assert s["local_delta"] == 1_000_000 and s["usd_delta_entry"] == 180_000
    assert res.status.iloc[0]["status"] == "MISSING_RATE"
    assert res.ladder.loc["2026-10-01", "BRL"] == 1_000_000


def test_stale_rate_used_but_flagged():
    res = build_exposure([rec("C1", "2026-10-01", "CHF", 1_000, 1_200)],
                         {"CHF": rate(1.25, stale=True, ts="2026-09-10T15:00:00-04:00")})
    s = _row(res, "CHF")
    assert s["status"] == "STALE"
    assert s["usd_delta"] == pytest.approx(1_250)
    assert "2026-09-10" in res.status.iloc[0]["message"]
    assert s["rate_source"] == "MOCK"


def test_cell_traceability_and_book_filter():
    recs = [rec("T1", "2026-10-01", "AUD", 100, 65), rec("T2", "2026-10-01", "AUD", 200, 134),
            rec("T3", "2026-10-02", "AUD", 50, 33), rec("T4", "2026-10-01", "AUD", 999, 600, book="OTHER")]
    res = build_exposure(recs, {"AUD": rate(0.7)})
    cell = res.cell_trades("2026-10-01", "AUD")
    assert sorted(cell["trade_id"]) == ["T1", "T2", "T4"]
    assert cell["local_amount"].sum() == res.ladder.loc["2026-10-01", "AUD"] == 1299
    ha = build_exposure(recs, {"AUD": rate(0.7)}, books=["HA"])
    assert sorted(ha.cell_trades("2026-10-01", "AUD")["trade_id"]) == ["T1", "T2"]
    assert ha.ladder.loc["2026-10-01", "AUD"] == 300


def test_portfolio_totals_and_usd_equivalent():
    from engine.ladder.exposure import ladder_usd_equivalent, portfolio_totals
    recs = AUD_FIXTURE + [rec("J1", "2026-09-16", "JPY", 150_000_000, 900_000)]
    res = build_exposure(recs, {"AUD": rate(0.600), "JPY": rate(150.0, inverted=True)})
    t = portfolio_totals(res)
    assert t["net_usd"] == pytest.approx(-12_576_973.8 + 1_000_000)
    assert t["gross_usd"] == pytest.approx(12_576_973.8 + 1_000_000)
    assert t["exposure_pnl"] == pytest.approx(2_423_026.2 + 100_000)
    assert t["currencies"] == 2 and t["missing"] == []
    usd = ladder_usd_equivalent(res)
    assert usd["2026-09-11"] == pytest.approx(-20_763_351 * 0.6)
    assert usd["2026-09-16"] == pytest.approx(9_764_499 * 0.6 + 1_000_000)
    # missing rate: totals NaN, missing named, date with the unpriced ccy NaN, others intact
    res2 = build_exposure(recs, {"AUD": rate(0.600)})
    t2 = portfolio_totals(res2)
    assert math.isnan(t2["net_usd"]) and math.isnan(t2["gross_usd"]) and t2["missing"] == ["JPY"]
    usd2 = ladder_usd_equivalent(res2)
    assert math.isnan(usd2["2026-09-16"]) and usd2["2026-09-11"] == pytest.approx(-20_763_351 * 0.6)
    empty = portfolio_totals(build_exposure([], {}))
    assert math.isnan(empty["net_usd"]) and empty["currencies"] == 0


def test_empty_records_give_empty_result():
    res = build_exposure([], {"AUD": rate(0.6)})
    assert res.ladder.empty and res.summary.empty and res.contributions.empty and res.status.empty


def test_duplicate_trade_id_rejected():
    with pytest.raises(ValueError, match="duplicate trade_id"):
        build_exposure([rec("X", "2026-10-01", "AUD", 1, 1), rec("X", "2026-10-01", "AUD", 1, 1)], {})
