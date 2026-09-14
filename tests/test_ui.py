"""Tests for ui/app.py (Dash skeleton, six placeholder tabs).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import cash_ladder  # noqa: E402


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
