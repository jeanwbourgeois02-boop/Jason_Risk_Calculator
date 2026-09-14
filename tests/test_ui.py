"""Tests for ui/app.py (Dash skeleton, six placeholder tabs).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import cash_ladder, pnl  # noqa: E402


def _seed(conn):
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t1','BNP','USDJPY','FX_SPOT','t1','2026-08-17',1000000,147.10,"
        "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc')"
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("t1", 1, "FX_NEAR", "USD", 1000000, "2026-08-17", "2026-08-17", 147.10, 1),
            ("t1", 2, "FX_NEAR", "JPY", -147100000, "2026-08-17", "2026-08-17", 147.10, 1),
        ],
    )
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-17','USDJPY','2026-08-17','SPOT',147.12,'BBG_BFXFORWARD','2026-08-17T15:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO positions VALUES "
        "('2026-08-17','BNP','BNPP-IPBFX-NMMF','USDJPY','2026-08-17',1000000,147100000,147.12,"
        "0.0068,147120000,1000000,1000,1000,1000)"
    )
    conn.commit()


# --------------------------------------------------------------------------- app / layout
def test_create_app_returns_dash_app():
    app = uiapp.create_app(db_path="does-not-exist.db")
    assert isinstance(app, dash.Dash)


def test_layout_has_six_tabs_in_order():
    data = uiapp.empty_summary()
    layout = uiapp.build_layout(data)
    tabs_component = layout.children[1]
    assert isinstance(tabs_component, dash.dcc.Tabs)
    labels = [child.label for child in tabs_component.children]
    assert labels == [
        "Cash ladder",
        "FX",
        "Rates",
        "Options",
        "Delta",
        "Overall book",
    ]
    for child in tabs_component.children:
        assert isinstance(child, dash.dcc.Tab)


# --------------------------------------------------------------------------- summary()
def test_summary_with_seeded_data():
    conn = schema.connect()
    _seed(conn)
    result = uiapp.summary(conn)
    assert result == {
        "as_of_date": "2026-08-17",
        "trades": 1,
        "trade_legs": 2,
        "marks": 1,
        "positions": 1,
    }


def test_summary_empty_db():
    conn = schema.connect()
    result = uiapp.summary(conn)
    assert result["as_of_date"] == "none"
    assert result["trades"] == 0
    assert result["trade_legs"] == 0
    assert result["marks"] == 0
    assert result["positions"] == 0


def test_load_summary_missing_file(tmp_path):
    missing = tmp_path / "no_such.db"
    result = uiapp.load_summary(missing)
    assert result["as_of_date"] == "none"
    assert result["trades"] == 0
    assert result["trade_legs"] == 0
    assert result["marks"] == 0
    assert result["positions"] == 0
    assert "message" in result


def test_load_summary_existing_file(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = schema.connect(db_path)
    _seed(conn)
    conn.close()
    result = uiapp.load_summary(db_path)
    assert result["as_of_date"] == "2026-08-17"
    assert result["trades"] == 1


def test_get_db_path_default(monkeypatch):
    monkeypatch.delenv("RISK_DB", raising=False)
    assert uiapp.get_db_path() == uiapp.DEFAULT_DB_PATH


def test_get_db_path_from_env(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("RISK_DB", str(custom))
    assert uiapp.get_db_path() == custom


# --------------------------------------------------------------------------- cash ladder tab
def _hand_built_ladder_frame():
    import pandas as pd

    return pd.DataFrame(
        {
            "ccy": ["EUR", "USD"],
            "2026-08-18": [1000000.0, -1234567.8],
            "2026-08-19": [-500000.4, float("nan")],
            "2026-08-20": [0.0, 250000.0],
            "total": [499999.6, -984567.8],
            "usd": [float("nan"), -984567.8],
        }
    )


def test_cash_ladder_table_from_hand_built_frame():
    df = _hand_built_ladder_frame()
    table = cash_ladder.table_from_ladder(df)
    assert isinstance(table, dash.dash_table.DataTable)
    assert [c["id"] for c in table.columns] == list(df.columns)
    assert len(table.data) == 2
    row0 = table.data[0]
    assert row0["ccy"] == "EUR"
    assert row0["2026-08-18"] == "1,000,000"
    assert row0["2026-08-19"] == "(500,000)"
    assert row0["usd"] == ""  # NaN -> blank
    row1 = table.data[1]
    assert row1["2026-08-18"] == "(1,234,568)"  # rounds -1234567.8 -> (1,234,568)


def test_cash_ladder_source_dropdown_defaults_to_official():
    layout = cash_ladder.build_layout()
    controls = layout.children[1]  # source div
    dropdown = controls.children[1]
    assert isinstance(dropdown, dash.dcc.Dropdown)
    assert dropdown.value == cash_ladder.SOURCE_OFFICIAL
    assert dropdown.id == cash_ladder.SOURCE_DROPDOWN_ID
    labels = [opt["label"] for opt in dropdown.options]
    values = [opt["value"] for opt in dropdown.options]
    assert "Official" in labels
    assert cash_ladder.SOURCE_OFFICIAL in values


def test_cash_ladder_source_value_to_param():
    assert cash_ladder.source_value_to_param(cash_ladder.SOURCE_OFFICIAL) is None
    assert cash_ladder.source_value_to_param(None) is None
    assert cash_ladder.source_value_to_param("BNP_BVAL") == "BNP_BVAL"


def test_format_cell_parentheses_and_thousands():
    assert cash_ladder.format_cell(-1234567.8) == "(1,234,568)"
    assert cash_ladder.format_cell(1234567.8) == "1,234,568"
    assert cash_ladder.format_cell(0) == "0"
    assert cash_ladder.format_cell(None) == ""
    assert cash_ladder.format_cell(float("nan")) == ""


def test_format_ladder_frame_blank_for_nan_and_ccy_passthrough():
    df = _hand_built_ladder_frame()
    formatted = cash_ladder.format_ladder_frame(df)
    assert formatted.loc[0, "ccy"] == "EUR"
    assert formatted.loc[1, "ccy"] == "USD"
    assert formatted.loc[0, "usd"] == ""
    assert formatted.loc[1, "2026-08-19"] == ""
    assert formatted.loc[0, "2026-08-18"] == "1,000,000"
    assert formatted.loc[1, "2026-08-18"] == "(1,234,568)"


def test_cash_ladder_wired_into_cash_ladder_tab():
    data = uiapp.empty_summary()
    layout = uiapp.build_layout(data)
    tabs_component = layout.children[1]
    cash_ladder_tab = tabs_component.children[0]
    assert cash_ladder_tab.label == "Cash ladder"
    inner = cash_ladder_tab.children[0]
    assert isinstance(inner.children[0], dash.html.H3)
    assert any(
        getattr(child, "id", None) == cash_ladder.TABLE_CONTAINER_ID
        for child in inner.children
    )


def test_create_app_registers_cash_ladder_callback():
    app = uiapp.create_app(db_path="does-not-exist.db")
    assert f"{cash_ladder.TABLE_CONTAINER_ID}.children" in app.callback_map


# --------------------------------------------------------------------------- transpose_ladder
def _hand_built_wide_ladder_frame():
    """Unsorted row order on purpose (EUR, GBP, USD) -- transpose_ladder must recompute
    the currency order itself, not rely on the input already being sorted.

    Covers all three usd_equivalent blank-rule branches (see cash_ladder module
    docstring): EUR has usd=NaN (spot unknown) so any date where EUR is non-zero blanks
    that date; 2026-08-19 has a NaN amount cell (unknown flow) which blanks that date
    regardless of spot; 2026-08-20 has only USD non-zero with a recoverable spot so it is
    the one date with a defined usd_equivalent. GBP's spot (1.25) is recoverable from
    usd/total (250.0/200.0) even though it never appears alone with a non-zero amount.
    """
    import pandas as pd

    return pd.DataFrame(
        {
            "ccy": ["EUR", "GBP", "USD"],
            "2026-08-18": [1000000.0, 0.0, -1234567.8],
            "2026-08-19": [-500000.4, 200.0, float("nan")],
            "2026-08-20": [0.0, 0.0, 250000.0],
            "total": [499999.6, 200.0, -984567.8],
            "usd": [float("nan"), 250.0, -984567.8],
        }
    )


def test_transpose_ladder_column_order_and_rows():
    df = _hand_built_wide_ladder_frame()
    out = cash_ladder.transpose_ladder(df)
    assert list(out.columns) == ["settle_date", "USD", "GBP", "EUR", "usd_equivalent"]
    assert list(out["settle_date"]) == ["2026-08-18", "2026-08-19", "2026-08-20", "Total"]


def test_transpose_ladder_usd_equivalent_blank_rules():
    import pandas as pd

    df = _hand_built_wide_ladder_frame()
    out = cash_ladder.transpose_ladder(df).set_index("settle_date")

    # 2026-08-18: EUR is non-zero and has no defined spot (usd=NaN) -> blank.
    assert pd.isna(out.loc["2026-08-18", "usd_equivalent"])
    # 2026-08-19: USD amount itself is NaN -> blank regardless of spot.
    assert pd.isna(out.loc["2026-08-19", "usd_equivalent"])
    # 2026-08-20: only USD is non-zero, spot = usd/total = 1.0 -> defined.
    assert out.loc["2026-08-20", "usd_equivalent"] == 250000.0
    # Total row: EUR total is non-zero with no defined spot -> blank.
    assert pd.isna(out.loc["Total", "usd_equivalent"])


def test_transpose_ladder_cell_values_pass_through():
    df = _hand_built_wide_ladder_frame()
    out = cash_ladder.transpose_ladder(df).set_index("settle_date")
    assert out.loc["2026-08-18", "USD"] == -1234567.8
    assert out.loc["Total", "GBP"] == 200.0


def test_transpose_ladder_empty_frame():
    import pandas as pd

    out = cash_ladder.transpose_ladder(pd.DataFrame({"ccy": []}))
    assert list(out.columns) == ["settle_date", "usd_equivalent"]
    assert out.empty


def test_table_from_transposed_ladder_via_settle_date_label():
    df = _hand_built_wide_ladder_frame()
    transposed = cash_ladder.transpose_ladder(df)
    table = cash_ladder.table_from_ladder(transposed, label_col="settle_date")
    assert isinstance(table, dash.dash_table.DataTable)
    assert [c["id"] for c in table.columns] == list(transposed.columns)
    total_row = table.data[-1]
    assert total_row["settle_date"] == "Total"
    assert total_row["usd_equivalent"] == ""  # NaN -> blank
    assert total_row["USD"] == "(984,568)"


def test_cash_ladder_callback_pipeline_transposes(monkeypatch):
    """register_callbacks' inner function should call transpose_ladder on the fetched
    ladder frame before rendering -- checked by monkeypatching transpose_ladder and
    verifying it was called with the raw ladder_table output."""
    import types
    import sys

    df = _hand_built_wide_ladder_frame()
    calls = []

    fake_views = types.ModuleType("engine.ladder.views")
    fake_views.ladder_table = lambda conn, as_of_date, source=None: df
    fake_engine = types.ModuleType("engine")
    fake_engine_ladder = types.ModuleType("engine.ladder")
    fake_engine.ladder = fake_engine_ladder
    fake_engine_ladder.views = fake_views
    monkeypatch.setitem(sys.modules, "engine", fake_engine)
    monkeypatch.setitem(sys.modules, "engine.ladder", fake_engine_ladder)
    monkeypatch.setitem(sys.modules, "engine.ladder.views", fake_views)

    real_transpose = cash_ladder.transpose_ladder

    def spy_transpose(frame):
        calls.append(frame)
        return real_transpose(frame)

    monkeypatch.setattr(cash_ladder, "transpose_ladder", spy_transpose)
    monkeypatch.setattr(
        "ui.app.connect_readonly", lambda path: schema.connect()
    )

    app = uiapp.create_app(db_path="does-not-exist.db")
    callback = app.callback_map[f"{cash_ladder.TABLE_CONTAINER_ID}.children"]["callback"]
    callback.__wrapped__(cash_ladder.SOURCE_OFFICIAL, "2026-08-18")
    assert len(calls) == 1
    assert calls[0] is df


# --------------------------------------------------------------------------- P&L tab
def _hand_built_by_pair():
    import pandas as pd

    return pd.DataFrame(
        {
            "instrument_id": ["USDJPY", "EURUSD"],
            "usd_notional": [1000000.0, -500000.0],
            "ltd_usd": [1234.5, float("nan")],
            "n_trades": [3, 1],
        }
    )


def test_pnl_pairs_table_from_by_pair():
    by_pair = _hand_built_by_pair()
    table = pnl.pairs_table_from_by_pair(by_pair)
    assert isinstance(table, dash.dash_table.DataTable)
    assert [c["id"] for c in table.columns] == pnl.PAIR_COLUMNS
    row0 = table.data[0]
    assert row0["instrument_id"] == "USDJPY"
    assert row0["usd_notional"] == "1,000,000"
    assert row0["n_trades"] == "3"
    row1 = table.data[1]
    assert row1["usd_notional"] == "(500,000)"
    assert row1["ltd_usd"] == ""  # NaN -> blank


def test_pnl_pairs_table_empty():
    import pandas as pd

    table = pnl.pairs_table_from_by_pair(pd.DataFrame(columns=pnl.PAIR_COLUMNS))
    assert table.data == []


def test_pnl_totals_table_from_book_totals():
    totals = {"net_usd": 100000.0, "gross_usd": -250000.5, "gold_usd": float("nan"), "futures_usd": 0.0}
    table = pnl.totals_table_from_book_totals(totals)
    rows = {row["metric"]: row["usd"] for row in table.data}
    assert rows["net_usd"] == "100,000"
    assert rows["gross_usd"] == "(250,000)"  # round(-250000.5) -> -250000 (banker's rounding)
    assert rows["gold_usd"] == ""
    assert rows["futures_usd"] == "0"


def test_pnl_period_table_from_period_pnl():
    period = {
        "as_of_date": "2026-08-17",
        "ltd": 5000.0,
        "daily": 100.0, "daily_ref_date": "2026-08-14",
        "d5": float("nan"), "d5_ref_date": "2026-08-10",
        "mtd": -200.0, "mtd_ref_date": "2026-07-31",
        "ytd": 900.0, "ytd_ref_date": "2025-12-31",
    }
    table = pnl.period_table_from_period_pnl(period)
    rows = {row["period"]: row for row in table.data}
    assert rows["LTD"]["usd"] == "5,000"
    assert rows["LTD"]["ref_date"] == ""
    assert rows["Daily"]["usd"] == "100"
    assert rows["Daily"]["ref_date"] == "2026-08-14"
    assert rows["5d"]["usd"] == ""  # NaN -> blank
    assert rows["5d"]["ref_date"] == "2026-08-10"
    assert rows["MTD"]["usd"] == "(200)"
    assert rows["YTD"]["usd"] == "900"


def test_pnl_source_dropdown_and_date_picker():
    layout = pnl.build_layout(default_date="2026-08-17")
    assert isinstance(layout.children[0], dash.html.H3)
    source_div = layout.children[1]
    dropdown = source_div.children[1]
    assert isinstance(dropdown, dash.dcc.Dropdown)
    assert dropdown.id == pnl.SOURCE_DROPDOWN_ID
    assert dropdown.value == "OFFICIAL"
    date_div = layout.children[2]
    picker = date_div.children[1]
    assert isinstance(picker, dash.dcc.DatePickerSingle)
    assert picker.id == pnl.DATE_PICKER_ID
    assert picker.date == "2026-08-17"
    assert any(
        getattr(child, "id", None) == pnl.CONTENT_CONTAINER_ID
        for child in layout.children
    )


def test_pnl_wired_into_overall_book_tab():
    data = uiapp.empty_summary()
    layout = uiapp.build_layout(data)
    tabs_component = layout.children[1]
    overall_book_tab = tabs_component.children[5]
    assert overall_book_tab.label == "Overall book"
    inner = overall_book_tab.children[0]
    assert isinstance(inner.children[0], dash.html.H3)
    assert any(
        getattr(child, "id", None) == pnl.CONTENT_CONTAINER_ID
        for child in inner.children
    )


def test_create_app_registers_pnl_callback():
    app = uiapp.create_app(db_path="does-not-exist.db")
    assert f"{pnl.CONTENT_CONTAINER_ID}.children" in app.callback_map


def test_pnl_component_ids_distinct_from_cash_ladder():
    assert pnl.SOURCE_DROPDOWN_ID != cash_ladder.SOURCE_DROPDOWN_ID
    assert pnl.DATE_PICKER_ID != cash_ladder.DATE_PICKER_ID
    assert pnl.CONTENT_CONTAINER_ID != cash_ladder.TABLE_CONTAINER_ID
