"""Ladder tab view controls, USD-at-outright cells, heatmap, downloads and the spec's
worked example (user's cash-ladder spec, 2026-09-18) -- ui/tabs/exposure.py and
ui/tabs/cash_ladder.py. Shares fixtures with tests/test_ui_ladder.py."""
from __future__ import annotations

from pathlib import Path

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from tests.test_ui_ladder import _all_ids, _find_id, _rec, _render_text  # noqa: E402
from ui.tabs import cash_ladder, exposure  # noqa: E402

RAW_SAMPLE = Path(__file__).resolve().parents[1] / "data" / "raw" / "new_sample_trades.csv"


def _spec_records():
    """Two currencies, a settled row and three value dates: the spec's worked example
    in miniature (AUD settled +23.77m, -21.11m on 24 Sep, +11.15m on 28 Sep)."""
    from engine.ladder.exposure_adapter import SETTLED
    return [
        {**_rec("s1", "AUD", 23_771_313.0, SETTLED, pair="AUDUSD", fill=0.70), "settled_on": "2026-09-16"},
        {**_rec("s1", "USD", -16_500_000.0, SETTLED, pair="AUDUSD", fill=0.70), "settled_on": "2026-09-16"},
        _rec("o1", "AUD", -21_109_276.0, "2026-09-24", pair="AUDUSD", fill=0.71),
        _rec("o1", "USD", 15_000_000.0, "2026-09-24", pair="AUDUSD", fill=0.71),
        _rec("o2", "AUD", 11_146_505.0, "2026-09-28", pair="AUDUSD", fill=0.72),
        _rec("o2", "USD", -8_000_000.0, "2026-09-28", pair="AUDUSD", fill=0.72),
        _rec("j1", "JPY", -147_000_000.0, "2026-10-21"),
        _rec("j1", "USD", 1_000_000.0, "2026-10-21"),
    ]


RATES = {
    "AUD": {"rate": 0.66, "inverted": False, "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False, "pair": "AUDUSD"},
    "JPY": {"rate": 150.0, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False, "pair": "USDJPY"},
}
FWD = {("AUD", "2026-09-24"): {"rate": 0.659, "basis": "interpolated"},
       ("AUD", "2026-09-28"): {"rate": 0.658, "basis": "outright"},
       ("JPY", "2026-10-21"): {"rate": 1 / 149.0, "basis": "outright"},
       ("USD", "2026-09-24"): {"rate": 1.0, "basis": "spot"}, ("USD", "2026-09-28"): {"rate": 1.0, "basis": "spot"},
       ("USD", "2026-10-21"): {"rate": 1.0, "basis": "spot"}}


def test_build_layout_has_the_spec_view_controls():
    layout = cash_ladder.build_layout(default_date="2026-08-17")
    ids = _all_ids(layout)
    for wanted in (cash_ladder.DATE_PICKER_ID, cash_ladder.CCY_FILTER_ID, cash_ladder.DATE_RANGE_ID,
                   cash_ladder.SETTLED_TOGGLE_ID, cash_ladder.USD_TOGGLE_ID,
                   cash_ladder.DOWNLOAD_LADDER_BTN_ID, cash_ladder.DOWNLOAD_LEGS_BTN_ID,
                   cash_ladder.DOWNLOAD_LADDER_ID, cash_ladder.DOWNLOAD_LEGS_ID):
        assert wanted in ids


def test_view_from_controls_maps_dash_values():
    v = cash_ladder.view_from_controls(["AUD", "JPY"], "2026-09-24T00:00:00", "2026-09-28", ["on"], [])
    assert v.currencies == frozenset({"AUD", "JPY"}) and v.date_from == "2026-09-24" and v.date_to == "2026-09-28"
    assert v.settled_one_by_one is True and v.show_usd is False
    assert cash_ladder.view_from_controls(None, None, None, None, None) == exposure.DEFAULT_VIEW


def test_combined_frame_marks_usd_column_at_each_dates_outright_and_appends_total():
    """Spec section 5: a value-date row's USD equivalent is its legs at that date's
    outright, the settled row at spot, and a Total row follows the dated rows."""
    from engine.ladder.exposure import build_exposure
    records = _spec_records()
    result = build_exposure(records, RATES)
    frame, ccys = exposure.combined_frame(result, records, rates=RATES, forward_rates=FWD)
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert list(frame["kind"])[:5] == ["settled", "date", "date", "date", "total"]
    # settled: 23,771,313 x 0.66 - 16,500,000 ; 24 Sep: -21,109,276 x 0.659 + 15,000,000
    assert by_label.loc[exposure.SETTLED_ROW_LABEL, exposure.USD_EQUIVALENT_COL] == "(810,933)"
    assert by_label.loc["24 Sep 2026", exposure.USD_EQUIVALENT_COL] == "1,088,987"
    assert by_label.loc["28 Sep 2026", exposure.USD_EQUIVALENT_COL] == "(665,600)"
    assert by_label.loc["21 Oct 2026", exposure.USD_EQUIVALENT_COL] == "13,423"
    total = by_label.loc[exposure.TOTAL_ROW_LABEL]
    assert total["AUD"] == "13,808,542" and total[exposure.USD_EQUIVALENT_COL] == "(374,123)"
    assert by_label.loc["Local delta", "AUD"] == "13,808,542"


def test_view_filters_rows_and_hides_all_zero_columns_but_keeps_settled_and_deltas():
    from engine.ladder.exposure import build_exposure
    records = _spec_records()
    result = build_exposure(records, RATES)
    view = exposure.LadderView(date_from="2026-09-24", date_to="2026-09-28")
    frame, ccys = exposure.combined_frame(result, records, rates=RATES, forward_rates=FWD, view=view)
    labels = list(frame[exposure.ROW_LABEL_COL])
    assert labels[:4] == [exposure.SETTLED_ROW_LABEL, "24 Sep 2026", "28 Sep 2026", exposure.TOTAL_ROW_LABEL]
    assert "21 Oct 2026" not in labels
    assert "JPY" not in ccys  # zero on every shown row: hidden in this view
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc[exposure.TOTAL_ROW_LABEL, "AUD"] == "13,808,542"      # settled + 24 Sep + 28 Sep
    assert by_label.loc["Local delta", "AUD"] == "13,808,542"                   # whole grid, unchanged
    grid = exposure.grid_records(records, exposure.LadderView(currencies=frozenset({"AUD"})))
    assert {r["currency"] for r in grid} == {"AUD"}


def test_settled_one_by_one_puts_settled_legs_on_their_own_dates():
    from engine.ladder.exposure import build_exposure
    view = exposure.LadderView(settled_one_by_one=True)
    records = exposure.grid_records(_spec_records(), view)
    result = build_exposure(records, RATES)
    frame, _ = exposure.combined_frame(result, records, rates=RATES, view=view)
    labels = list(frame[exposure.ROW_LABEL_COL])
    assert exposure.SETTLED_ROW_LABEL not in labels and labels[0] == "16 Sep 2026"
    assert frame.iloc[0]["AUD"] == "23,771,313"


def test_show_usd_puts_usd_equivalents_in_the_cells():
    from engine.ladder.exposure import build_exposure
    records = _spec_records()
    result = build_exposure(records, RATES)
    view = exposure.LadderView(show_usd=True)
    frame, _ = exposure.combined_frame(result, records, rates=RATES, forward_rates=FWD, view=view)
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["24 Sep 2026", "AUD"] == "(13,911,013)"   # -21,109,276 x 0.659
    assert by_label.loc["24 Sep 2026", "USD"] == "15,000,000"
    table = exposure.combined_table(result, records, rates=RATES, forward_rates=FWD, view=view)
    assert [c["name"] for c in table.columns][1].endswith("(USD eq.)")
    assert "Cells are USD equivalents" in exposure.usd_basis_caption(FWD, view).children


def test_usd_basis_caption_names_currencies_with_no_forward_curve():
    from engine.ladder.usd_marks import BASIS_NO_CURVE
    fwd = {("NOK", "2026-12-15"): {"rate": 0.095, "basis": BASIS_NO_CURVE},
           ("JPY", "2026-12-15"): {"rate": 1 / 149.0, "basis": "outright"}}
    text = exposure.usd_basis_caption(fwd).children
    assert "No forward curve on file for NOK: shown at spot" in text
    assert "JPY" not in text.split("No forward curve")[1]
    assert "at spot for every date" in exposure.usd_basis_caption(None).children


def test_heatmap_colours_by_usd_and_hovers_local_and_usd():
    from engine.ladder.exposure import build_exposure
    records = _spec_records()
    result = build_exposure(records, RATES)
    graph = exposure.ladder_heatmap(result, ["AUD", "USD", "JPY"], FWD)
    assert graph.id == exposure.HEATMAP_ID
    trace = graph.figure.data[0]
    assert list(trace.x) == ["AUD", "USD", "JPY"] and trace.y[0] == exposure.SETTLED_ROW_LABEL
    assert trace.z[1][0] == pytest.approx(-21_109_276.0 * 0.659)         # colour = USD equivalent
    assert trace.text[1][0] == "(21,109,276)" and trace.customdata[1][0] == "(13,911,013)"
    assert trace.zmin == -trace.zmax and trace.zmid == 0                   # symmetric diverging scale
    assert exposure.ladder_heatmap(build_exposure([], {}), ["AUD"], None) is None


def test_local_vs_usd_details_and_export_frames():
    from engine.ladder.exposure import build_exposure
    records = _spec_records()
    details = exposure.local_vs_usd_details(records)
    assert details.id == exposure.LOCAL_VS_USD_DETAILS_ID
    table = _find_id(details, exposure.LOCAL_VS_USD_ID)
    rows = {(r["currency"], r["settlement_date"]): r for r in table.data}
    assert rows[("AUD", "24 Sep 2026")]["implied_rate_local_per_usd"] == "1.40729"   # 21,109,276 / 15,000,000
    assert rows[("AUD", exposure.SETTLED_ROW_LABEL)]["net_local"] == "23,771,313"
    result = build_exposure(records, RATES)
    ladder = exposure.ladder_export_frame(result, FWD, ccys=["AUD", "USD", "JPY"])
    assert list(ladder["settlement_date"]) == [exposure.SETTLED_ROW_LABEL, "2026-09-24", "2026-09-28",
                                               "2026-10-21", "Total"]
    assert ladder.iloc[-1]["AUD"] == pytest.approx(13_808_542.0)
    assert ladder.iloc[1][exposure.USD_EQUIVALENT_COL] == pytest.approx(-21_109_276.0 * 0.659 + 15_000_000.0)
    legs = exposure.legs_export_frame(records, RATES, FWD)
    assert len(legs) == len(records) and set(exposure.LEGS_EXPORT_COLUMNS) == set(legs.columns)
    aud24 = legs[(legs["currency"] == "AUD") & (legs["settlement_date"] == "2026-09-24")].iloc[0]
    assert aud24["usd_per_unit"] == pytest.approx(0.659) and aud24["mark_basis"] == "interpolated"
    assert set(legs[legs["settled_on"] == "2026-09-16"]["mark_basis"]) == {"spot", "identity"}  # AUD at spot, USD identity


def test_exposure_section_renders_heatmap_caption_and_details_after_the_grid():
    section = exposure.exposure_section(_spec_records(), [], "2026-09-17", rates=RATES, forward_rates=FWD)
    ids = _all_ids(section)
    for wanted in (exposure.USD_BASIS_CAPTION_ID, exposure.COMBINED_TABLE_ID, exposure.HEATMAP_ID,
                   exposure.LOCAL_VS_USD_DETAILS_ID, exposure.RISK_TABLE_ID):
        assert wanted in ids
    assert ids.index(exposure.COMBINED_TABLE_ID) < ids.index(exposure.HEATMAP_ID) < ids.index(exposure.RISK_TABLE_ID)
    # the headline card ignores the grid's view filters
    filtered = exposure.exposure_section(_spec_records(), [], "2026-09-17", rates=RATES,
                                         view=exposure.LadderView(currencies=frozenset({"JPY"})))
    unfiltered = exposure.exposure_section(_spec_records(), [], "2026-09-17", rates=RATES)
    assert _render_text(_find_id(filtered, exposure.HEADLINE_ID)) == _render_text(_find_id(unfiltered, exposure.HEADLINE_ID))


@pytest.mark.skipif(not RAW_SAMPLE.exists(), reason="reference blotter sample not on disk")
def test_spec_worked_example_aud_as_of_2026_09_17_through_the_tab(tmp_path, monkeypatch):
    """The spec's worked example (section 6): from the sample file as of 17 Sep 2026,
    AUD = +23.77m settled cash from the 16 Sep value date, -21.11m on 24 Sep, +11.15m
    on 28 Sep, net long 13.81m -- reproduced end to end through the Ladder tab's own
    callback, spot trades (CURRENCY rows) included, with the currency filter on."""
    import sys
    import types
    from data.ingest import blotter
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    db_path = tmp_path / "risk.db"
    conn = schema.connect(str(db_path))
    res = blotter.load(str(RAW_SAMPLE), conn, strict=False)
    conn.close()
    assert res.n_spot == 85
    app = dash.Dash(__name__)
    cash_ladder.register_callbacks(app, get_db_path=lambda: str(db_path))
    callback = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k][0]["callback"].__wrapped__
    body = callback("2026-09-17", 0, ["AUD", "USD", "CAD"], None, None, [], [])
    grid = _find_id(body, exposure.COMBINED_TABLE_ID)
    by_label = {r[exposure.ROW_LABEL_COL]: r for r in grid.data}
    assert by_label[exposure.SETTLED_ROW_LABEL]["AUD"] == "23,771,313"
    assert by_label["24 Sep 2026"]["AUD"] == "(21,109,276)"
    assert by_label["28 Sep 2026"]["AUD"] == "11,146,505"
    assert by_label["Local delta"]["AUD"] == "13,808,543"  # exact sample amounts round up here
    # the 51 USDCAD spot fills (all settled by 17 Sep) sit in the settled CAD balance
    assert by_label[exposure.SETTLED_ROW_LABEL]["CAD"] != exposure.EM_DASH
    assert "JPY" not in grid.data[0]  # filtered out of the grid by the currency control
