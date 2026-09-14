"""Tests for ui/app.py (Dash skeleton, six placeholder tabs).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402


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
