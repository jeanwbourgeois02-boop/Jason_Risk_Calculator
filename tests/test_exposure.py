"""Tests for engine/ladder/exposure.py against docs/CASH_LADDER_SPEC.md.

engine.ladder.exposure is a pure DELTA table (no P&L): records are one per trade LEG
(`trade_id` may repeat across a trade's own legs; natural key is
(trade_id, currency, settlement_date)). A USD leg is a leg like any other (spot = 1.0
identity), so a cross such as EURSEK -- which has no USD leg at all -- is priced the
same way as any other currency.
"""
import math

import pytest

from engine.ladder.exposure import ROUNDING_TOLERANCE_USD, build_exposure, usd_per_local


def rec(tid, date, ccy, local, book="HA"):
    return {"trade_id": tid, "settlement_date": date, "book": book, "currency": ccy, "local_amount": local}


def rate(value, inverted=False, stale=False, source="MOCK", ts="2026-09-14T15:00:00-04:00"):
    return {"rate": value, "inverted": inverted, "source": source, "timestamp": ts, "stale": stale}


# No USD legs in this fixture (matches the old trade-level AUD-only screenshot shape).
AUD_FIXTURE = [
    rec("A1", "2026-09-11", "AUD", -20_763_351),
    rec("A2", "2026-09-16", "AUD", 9_764_499),
    rec("A3", "2026-09-24", "AUD", -21_109_276),
    rec("A4", "2026-09-28", "AUD", 11_146_505),
]


def _row(res, ccy):
    return res.summary.set_index("currency").loc[ccy]


def test_aud_screenshot_fixture():
    res = build_exposure(AUD_FIXTURE, {"AUD": rate(0.600)})
    s = _row(res, "AUD")
    assert s["local_delta"] == -20_961_623
    assert abs(s["usd_delta"] - (-12_576_974)) <= ROUNDING_TOLERANCE_USD
    assert s["status"] == "OK"
    assert list(res.ladder.index) == ["2026-09-11", "2026-09-16", "2026-09-24", "2026-09-28"]
    assert list(res.ladder["AUD"]) == [-20_763_351, 9_764_499, -21_109_276, 11_146_505]
    print("\nAUD fixture:", {k: s[k] for k in ["fx_rate", "local_delta", "usd_delta"]})


def test_multiple_settlement_dates_and_currencies():
    recs = [rec("T1", "2026-10-01", "AUD", 100), rec("T2", "2026-10-05", "AUD", -40),
            rec("T3", "2026-10-05", "CAD", 500)]
    res = build_exposure(recs, {"AUD": rate(0.7), "CAD": rate(0.74)})
    assert res.ladder.shape == (2, 2)
    assert res.ladder.loc["2026-10-01", "CAD"] == 0
    assert res.ladder.loc["2026-10-05", "AUD"] == -40
    assert _row(res, "AUD")["local_delta"] == 60
    assert _row(res, "CAD")["usd_delta"] == pytest.approx(370)


def test_usdjpy_inverted_conversion():
    assert usd_per_local(rate(150.0, inverted=True)) == pytest.approx(1 / 150)
    assert usd_per_local(rate(0.70)) == 0.70
    res = build_exposure([rec("J1", "2026-10-01", "JPY", 150_000_000)],
                         {"JPY": rate(150.0, inverted=True)})
    s = _row(res, "JPY")
    assert s["fx_rate"] == pytest.approx(1 / 150)
    assert s["usd_delta"] == pytest.approx(1_000_000)


def test_missing_rate_is_flagged_not_zero():
    res = build_exposure([rec("B1", "2026-10-01", "BRL", 1_000_000)], {})
    s = _row(res, "BRL")
    assert s["status"] == "MISSING_RATE"
    assert math.isnan(s["usd_delta"])
    assert s["local_delta"] == 1_000_000
    assert res.status.iloc[0]["status"] == "MISSING_RATE"
    assert res.ladder.loc["2026-10-01", "BRL"] == 1_000_000


def test_stale_rate_used_but_flagged():
    res = build_exposure([rec("C1", "2026-10-01", "CHF", 1_000)],
                         {"CHF": rate(1.25, stale=True, ts="2026-09-10T15:00:00-04:00")})
    s = _row(res, "CHF")
    assert s["status"] == "STALE"
    assert s["usd_delta"] == pytest.approx(1_250)
    assert "2026-09-10" in res.status.iloc[0]["message"]
    assert s["rate_source"] == "MOCK"


def test_cell_traceability_and_book_filter():
    recs = [rec("T1", "2026-10-01", "AUD", 100), rec("T2", "2026-10-01", "AUD", 200),
            rec("T3", "2026-10-02", "AUD", 50), rec("T4", "2026-10-01", "AUD", 999, book="OTHER")]
    res = build_exposure(recs, {"AUD": rate(0.7)})
    cell = res.cell_trades("2026-10-01", "AUD")
    assert sorted(cell["trade_id"]) == ["T1", "T2", "T4"]
    assert cell["local_amount"].sum() == res.ladder.loc["2026-10-01", "AUD"] == 1299
    ha = build_exposure(recs, {"AUD": rate(0.7)}, books=["HA"])
    assert sorted(ha.cell_trades("2026-10-01", "AUD")["trade_id"]) == ["T1", "T2"]
    assert ha.ladder.loc["2026-10-01", "AUD"] == 300


def test_portfolio_totals_and_usd_equivalent():
    from engine.ladder.exposure import ladder_usd_equivalent, portfolio_totals
    recs = AUD_FIXTURE + [rec("J1", "2026-09-16", "JPY", 150_000_000)]
    res = build_exposure(recs, {"AUD": rate(0.600), "JPY": rate(150.0, inverted=True)})
    t = portfolio_totals(res)
    assert t["net_usd"] == pytest.approx(-12_576_973.8 + 1_000_000)
    assert t["gross_usd"] == pytest.approx(12_576_973.8 + 1_000_000)
    assert t["currencies"] == 2 and t["missing"] == []
    assert "exposure_pnl" not in t
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
    assert empty["net_usd"] == 0.0 and empty["gross_usd"] == 0.0
    assert empty["currencies"] == 0 and empty["missing"] == []


def test_usd_leg_priced_at_identity_and_excluded_from_net_gross():
    """A USD leg is a currency row like any other (spot = 1.0), contributing to the
    ladder and summary, but NOT to Net/Gross USD (which are FX exposure against USD,
    so a USD row would be self-referential)."""
    from engine.ladder.exposure import portfolio_totals
    recs = [rec("T1", "2026-10-01", "EUR", 1_000_000), rec("T1", "2026-10-01", "USD", -1_100_000)]
    res = build_exposure(recs, {"EUR": rate(1.10)})
    usd_row = _row(res, "USD")
    assert usd_row["fx_rate"] == 1.0 and usd_row["usd_delta"] == -1_100_000
    eur_row = _row(res, "EUR")
    assert eur_row["usd_delta"] == pytest.approx(1_100_000)
    t = portfolio_totals(res)
    assert t["net_usd"] == pytest.approx(1_100_000)          # EUR only, USD excluded
    assert t["gross_usd"] == pytest.approx(1_100_000)
    assert res.ladder.loc["2026-10-01", "USD"] == -1_100_000


def test_cross_currency_trade_no_usd_leg_priced_at_spot():
    """EURSEK: buy 1,000,000 EUR / sell 11,000,000 SEK, no USD leg at all. Both legs
    are priced at their own spot; the cross is no longer excluded for lack of a USD
    entry amount."""
    recs = [rec("X1", "2026-10-01", "EUR", 1_000_000), rec("X1", "2026-10-01", "SEK", -11_000_000)]
    res = build_exposure(recs, {"EUR": rate(1.10), "SEK": rate(10.5, inverted=True)})
    eur = _row(res, "EUR")
    sek = _row(res, "SEK")
    assert eur["usd_delta"] == pytest.approx(1_100_000)
    assert sek["usd_delta"] == pytest.approx(-11_000_000 / 10.5)
    assert sek["usd_delta"] == pytest.approx(-1_047_619.047619)
    assert "USD" not in res.ladder.columns  # no USD leg on this trade


def test_empty_records_give_empty_result():
    res = build_exposure([], {"AUD": rate(0.6)})
    assert res.ladder.empty and res.summary.empty and res.contributions.empty and res.status.empty


def test_duplicate_leg_key_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build_exposure([rec("X", "2026-10-01", "AUD", 1), rec("X", "2026-10-01", "AUD", 1)], {})


def test_multiple_legs_same_trade_id_allowed():
    """Two legs of one trade (e.g. EUR and SEK, or EUR and USD) share trade_id; that
    is not a duplicate as long as (trade_id, currency, settlement_date) differs."""
    recs = [rec("T1", "2026-10-01", "EUR", 100), rec("T1", "2026-10-01", "USD", -110)]
    res = build_exposure(recs, {"EUR": rate(1.10)})
    assert sorted(res.contributions["currency"]) == ["EUR", "USD"]


def test_no_pnl_fields_present():
    """Scope: this module is a delta table, never P&L. Confirms the removed fields
    stay removed from the public surface."""
    res = build_exposure(AUD_FIXTURE, {"AUD": rate(0.6)})
    assert "exposure_pnl" not in res.summary.columns
    assert "usd_delta_entry" not in res.summary.columns
    assert "usd_entry_amount" not in res.contributions.columns


# --------------------------------------------------------------------------- rate plausibility guard (2026-09-18)
def _krw_records(fill=1413.138):
    return [
        {"trade_id": "k1", "settlement_date": "2026-09-21", "book": "HA", "currency": "KRW",
         "local_amount": 1_413_138_000.0, "currency_pair": "USDKRW", "entry_rate": fill, "product_type": "FX_FWD"},
        {"trade_id": "k1", "settlement_date": "2026-09-21", "book": "HA", "currency": "USD",
         "local_amount": -1_000_000.0, "currency_pair": "USDKRW", "entry_rate": fill, "product_type": "FX_FWD"},
    ]


def _krw_rate(rate):
    return {"KRW": {"rate": rate, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "t",
                    "stale": False, "pair": "USDKRW"}}


def test_spot_at_wrong_scale_is_reported_suspect_and_not_used():
    """"krw is wrong by a factor of 1000": a USDKRW spot of 1.3945 instead of 1,394.5
    would multiply the KRW USD delta by 1,000. The guard compares the mark with the
    book's own fills and reports it, with both numbers, as SUSPECT_RATE; the USD delta
    stays NaN exactly like a missing rate and the portfolio totals name the currency."""
    from engine.ladder.exposure import portfolio_totals
    result = build_exposure(_krw_records(), _krw_rate(1.3945))
    krw = result.summary.set_index("currency").loc["KRW"]
    assert krw["status"] == "SUSPECT_RATE"
    assert math.isnan(krw["usd_delta"])
    assert krw["rate_source"] == "BBG_BFXFORWARD"  # provenance kept: it is the mark that is wrong
    msg = result.status.set_index("currency").loc["KRW", "message"]
    assert msg.startswith("official SPOT USDKRW 1.3945 is 1,013x away from the book's own KRW fills (~1,413.14)")
    assert "a 1,000x SCALE error" in msg
    assert portfolio_totals(result)["missing"] == ["KRW"]
    # the same scale error the other way round (1,394,500) is caught too
    assert build_exposure(_krw_records(), _krw_rate(1_394_500.0)).summary.set_index("currency").loc["KRW", "status"] == "SUSPECT_RATE"


def test_plausible_spot_passes_guard_even_after_a_large_move():
    """A real move, forward points, or a 30 % devaluation are nowhere near the 100x
    threshold: the rate is used and the status stays OK."""
    ok = build_exposure(_krw_records(), _krw_rate(1394.5)).summary.set_index("currency").loc["KRW"]
    assert ok["status"] == "OK"
    assert math.isclose(ok["usd_delta"], 1_413_138_000.0 / 1394.5)
    devalued = build_exposure(_krw_records(), _krw_rate(1900.0)).summary.set_index("currency").loc["KRW"]
    assert devalued["status"] == "OK"


def test_guard_is_silent_without_fill_fields_or_for_crosses():
    """Records that carry no fill (hand-built fixtures), option-delta records and
    cross pairs give the guard nothing to compare with, so it never fires on them."""
    from engine.ladder.exposure import fill_implied_rates
    import pandas as pd
    assert fill_implied_rates(pd.DataFrame(AUD_FIXTURE)) == {}
    cross = [{"trade_id": "c1", "settlement_date": "2026-09-25", "book": "HA", "currency": "SEK",
              "local_amount": 11_000_000.0, "currency_pair": "EURSEK", "entry_rate": 11.0, "product_type": "FX_FWD"},
             {"trade_id": "p1", "settlement_date": "2026-09-25", "book": "HA", "currency": "SEK",
              "local_amount": -1_000_000.0, "currency_pair": "USDSEK", "entry_rate": 0.0125, "product_type": "FX_OPTION"}]
    assert fill_implied_rates(pd.DataFrame(cross)) == {}
    sek = {"SEK": {"rate": 10.6, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False}}
    assert build_exposure(cross, sek).summary.set_index("currency").loc["SEK", "status"] == "OK"
    assert fill_implied_rates(pd.DataFrame(_krw_records())) == {"KRW": 1.0 / 1413.138}



# --------------------------------------------------------------------------- USD at outrights (spec 2026-09-18)
def test_ladder_usd_cells_use_each_dates_own_outright_and_fall_back_to_spot():
    """User's cash-ladder spec: a cell is amount x the USD-per-unit mark for its own value
    date (forward outright), the settled row stays at spot, and a date with no forward
    entry falls back to the summary's spot rate. The sum of all cells is the ladder's
    USD equivalent per row."""
    from engine.ladder.exposure import ladder_usd_cells, ladder_usd_equivalent
    recs = [rec("T1", "2026-10-21", "JPY", -149_000_000.0), rec("T1", "2026-10-21", "USD", 1_000_000.0),
            rec("T2", "settled", "JPY", 150_000_000.0), rec("T2", "settled", "USD", -1_000_000.0),
            rec("T3", "2026-12-15", "JPY", -1_500_000.0)]
    res = build_exposure(recs, {"JPY": rate(150.0, inverted=True)})
    fwd = {("JPY", "2026-10-21"): {"rate": 1 / 149.0, "basis": "outright"},
           ("JPY", "settled"): {"rate": 1 / 150.0, "basis": "spot"},
           ("USD", "2026-10-21"): {"rate": 1.0, "basis": "spot"}, ("USD", "settled"): {"rate": 1.0, "basis": "spot"}}
    cells = ladder_usd_cells(res, fwd)
    assert cells.loc["2026-10-21", "JPY"] == pytest.approx(-1_000_000.0)   # at the 21 Oct outright, not spot
    assert cells.loc["settled", "JPY"] == pytest.approx(1_000_000.0)
    assert cells.loc["2026-12-15", "JPY"] == pytest.approx(-10_000.0)      # no entry: spot 150
    eq = ladder_usd_equivalent(res, fwd)
    assert abs(eq["2026-10-21"]) < 1e-6 and abs(eq["settled"]) < 1e-6
    # without forward rates every cell is at spot (unchanged behaviour)
    assert ladder_usd_cells(res).loc["2026-10-21", "JPY"] == pytest.approx(-149_000_000.0 / 150.0)


def test_ladder_usd_cells_missing_rate_is_nan_never_zero():
    from engine.ladder.exposure import ladder_usd_cells, ladder_usd_equivalent
    res = build_exposure([rec("T1", "2026-10-21", "SEK", 100.0), rec("T1", "2026-10-21", "USD", -10.0)], {})
    assert math.isnan(ladder_usd_cells(res).loc["2026-10-21", "SEK"])
    assert math.isnan(ladder_usd_equivalent(res)["2026-10-21"])


def test_local_vs_usd_separates_cross_legs_from_the_implied_rate():
    """Spec's "Local vs USD by value date": the SEK side of a EURSEK cross counts in
    net_local and net_local_cross but never in the implied SEK-per-USD rate, which uses
    only SEK legs dealt against USD and the USD legs dealt against SEK."""
    from engine.ladder.exposure import local_vs_usd
    recs = [
        {**rec("A", "2026-10-21", "SEK", 9_500_000.0), "currency_pair": "USDSEK"},
        {**rec("A", "2026-10-21", "USD", -1_000_000.0), "currency_pair": "USDSEK"},
        {**rec("B", "2026-10-21", "SEK", 11_000_000.0), "currency_pair": "EURSEK"},
        {**rec("B", "2026-10-21", "EUR", -1_000_000.0), "currency_pair": "EURSEK"},
        {**rec("C", "2026-10-21", "JPY", -150_000.0), "currency_pair": "USDJPY"},
        {**rec("C", "2026-10-21", "USD", 1_000.0), "currency_pair": "USDJPY"},
    ]
    out = local_vs_usd(recs).set_index(["currency", "settlement_date"])
    sek = out.loc[("SEK", "2026-10-21")]
    assert sek["net_local"] == pytest.approx(20_500_000.0)
    assert sek["net_local_vs_usd"] == pytest.approx(9_500_000.0)
    assert sek["net_local_cross"] == pytest.approx(11_000_000.0)
    assert sek["net_usd"] == pytest.approx(-1_000_000.0)
    assert sek["implied_rate_local_per_usd"] == pytest.approx(9.5)
    eur = out.loc[("EUR", "2026-10-21")]
    assert eur["net_local_cross"] == pytest.approx(-1_000_000.0) and math.isnan(eur["implied_rate_local_per_usd"])
    assert "USD" not in out.index.get_level_values(0)
    assert local_vs_usd([]).empty


# --------------------------------------------------------------------------- rate guard: inverted vs scaled (review 2026-09-18)
def test_inverted_usdkrw_quote_is_refused_and_named_as_inverted():
    """A USDKRW SPOT stored as USD-per-KRW (0.000717, the reciprocal) is refused: USD
    delta NaN, status SUSPECT_RATE, and the reason says INVERTED, not scale."""
    res = build_exposure(_krw_records(), _krw_rate(0.000717))
    s = _row(res, "KRW")
    assert s["status"] == "SUSPECT_RATE" and math.isnan(s["usd_delta"])
    msg = res.status.set_index("currency").loc["KRW", "message"]
    assert "INVERTED" in msg and "SCALE" not in msg


def test_thousandfold_usdkrw_quote_is_refused_and_named_as_scale():
    res = build_exposure(_krw_records(), _krw_rate(1.3945))
    s = _row(res, "KRW")
    assert s["status"] == "SUSPECT_RATE" and math.isnan(s["usd_delta"])
    msg = res.status.set_index("currency").loc["KRW", "message"]
    assert "SCALE" in msg and "INVERTED" not in msg


def test_sane_usdkrw_quote_passes_the_guard():
    res = build_exposure(_krw_records(), _krw_rate(1394.5))
    s = _row(res, "KRW")
    assert s["status"] == "OK" and s["usd_delta"] == pytest.approx(1_413_138_000.0 / 1394.5)
