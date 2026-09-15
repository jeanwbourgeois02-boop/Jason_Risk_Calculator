"""Tests for ui/tabs/cash_ladder.py and ui/tabs/exposure.py (docs/BUILD_PLAN.md
Task C split, agent C1 Ladder).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui.tabs import cash_ladder  # noqa: E402
from ui.tabs import exposure  # noqa: E402


def _seed(conn):
    conn.execute(
        "INSERT INTO instruments VALUES "
        "('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t1','BNP','USDJPY','FX_FWD','t1','2026-08-01',1000000,147.10,"
        "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc','')"
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("t1", 1, "FX_NEAR", "USD", 1000000, "2026-08-01", "2026-08-20", 147.10, 1),
            ("t1", 2, "FX_NEAR", "JPY", -147100000, "2026-08-01", "2026-08-20", 147.10, 1),
        ],
    )
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-17','USDJPY','2026-08-17','SPOT',147.12,'BBG_BFXFORWARD','2026-08-17T15:00:00-04:00')"
    )
    conn.commit()


@pytest.fixture
def conn():
    c = schema.connect(":memory:")
    _seed(c)
    return c


# --------------------------------------------------------------------------- transpose_ladder
def test_transpose_ladder_basic():
    df = pd.DataFrame({
        "ccy": ["USD", "JPY"],
        "2026-08-20": [1_000_000.0, -147_100_000.0],
        "total": [1_000_000.0, -147_100_000.0],
        "usd": [1_000_000.0, -1_000_000.0],
    })
    out = cash_ladder.transpose_ladder(df)
    assert list(out["settle_date"]) == ["2026-08-20", "Total"]
    assert out.loc[0, "usd_equivalent"] == pytest.approx(0.0)
    assert out.loc[1, "usd_equivalent"] == pytest.approx(0.0)


def test_transpose_ladder_blank_when_spot_missing():
    df = pd.DataFrame({
        "ccy": ["JPY"],
        "2026-08-20": [-147_100_000.0],
        "total": [-147_100_000.0],
        "usd": [float("nan")],
    })
    out = cash_ladder.transpose_ladder(df)
    assert math.isnan(out.loc[0, "usd_equivalent"])


def test_transpose_ladder_empty_frame():
    out = cash_ladder.transpose_ladder(pd.DataFrame())
    assert list(out.columns) == ["settle_date", "usd_equivalent"]
    assert out.empty


# --------------------------------------------------------------------------- layout / callbacks
def test_build_layout_has_no_source_dropdown_or_pull_now():
    layout = cash_ladder.build_layout(default_date="2026-08-17")
    ids = _all_ids(layout)
    assert cash_ladder.DATE_PICKER_ID in ids
    assert cash_ladder.SORT_ID in ids
    assert "cash-ladder-source" not in ids  # workbook rates dropdown removed
    assert "cash-ladder-pull-now" not in ids  # moved to Market data tab


def _all_ids(node, out=None):
    out = out if out is not None else []
    ident = getattr(node, "id", None)
    if ident is not None:
        out.append(ident)
    for child in getattr(node, "children", None) or []:
        if hasattr(child, "children") or hasattr(child, "id"):
            _all_ids(child, out)
    return out


def test_render_with_seeded_db(tmp_path, conn, monkeypatch):
    # ui.app itself is C5's file, mid-rewrite in this task split (it currently imports
    # a `ui.tabs.pnl` module another agent is retiring) -- stub it out here so this test
    # only exercises cash_ladder.py's own logic, per `connect_readonly`'s documented
    # signature, without depending on ui/app.py's current state.
    import sys
    import types
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)

    db_path = tmp_path / "risk.db"
    seeded = schema.connect(str(db_path))
    _seed(seeded)
    seeded.close()

    app = dash.Dash(__name__)
    cash_ladder.register_callbacks(app, get_db_path=lambda: str(db_path))
    # register_callbacks wires the inner _update_table closure into the Output callback;
    # `.__wrapped__` is the undecorated function, callable directly with positional args.
    matches = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k]
    assert matches
    callback = matches[0]["callback"].__wrapped__
    body, status = callback("2026-08-17", "usd", 0)
    assert body is not None
    assert isinstance(status, str)


def test_render_no_as_of_date_message():
    app = dash.Dash(__name__)
    cash_ladder.register_callbacks(app, get_db_path=lambda: ":memory:")
    matches = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k]
    callback = matches[0]["callback"].__wrapped__
    body, status = callback(None, "usd", 0)
    assert "No as-of date" in body.children


# --------------------------------------------------------------------------- exposure.py
RATES = {"JPY": {"rate": 147.0, "inverted": True, "source": "BBG_BFXFORWARD",
                 "timestamp": "2026-08-17T15:00:00-04:00", "stale": False}}
RECORDS = [
    {"trade_id": "t1", "settlement_date": "2026-08-20", "book": "HAHY7", "book_source": "HAHY7",
     "currency": "USD", "local_amount": 1_000_000.0, "settles_cash": 1},
    {"trade_id": "t1", "settlement_date": "2026-08-20", "book": "HAHY7", "book_source": "HAHY7",
     "currency": "JPY", "local_amount": -147_100_000.0, "settles_cash": 1},
]


def test_risk_snapshot_has_no_exposure_pnl_card():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    card = exposure.risk_snapshot(result, RECORDS)
    labels = [c.children[0].children for c in card.children]
    assert "Exposure P&L" not in labels
    assert "Net USD exposure" in labels
    assert "Gross USD exposure" in labels


def test_summary_frame_has_no_removed_columns():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    frame = exposure.summary_frame(result, RECORDS)
    assert "usd_delta_entry" not in frame.columns
    assert "exposure_pnl" not in frame.columns
    assert list(frame.columns) == exposure.SUMMARY_COLUMNS


def test_combined_risk_table_first_in_layout():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES)
    ids = _all_ids(section)
    assert exposure.RISK_TABLE_ID in ids
    assert ids.index(exposure.RISK_TABLE_ID) < ids.index(exposure.SNAPSHOT_ID)


def test_combined_risk_frame_futures_row_present_with_mark():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": 100_000.0, "by_instrument": {"ESU6 Index": 100_000.0}, "missing": [], "reason": ""}
    frame = exposure.combined_risk_frame(result, futures, scenarios={})
    row = frame[frame[exposure.RISK_LABEL_COL] == "ESU6 Index"].iloc[0]
    assert row["usd_delta"] == 100_000.0
    assert row["_unavailable"] == ""


def test_combined_risk_frame_futures_row_unavailable_with_reason():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["ESU6 Index"],
               "reason": "no FUTURE_PX on 2026-08-17 for ESU6 Index"}
    frame = exposure.combined_risk_frame(result, futures, scenarios={})
    row = frame[frame[exposure.RISK_LABEL_COL] == "ESU6 Index"].iloc[0]
    assert pd.isna(row["usd_delta"])
    assert "no FUTURE_PX" in row["_unavailable"]
    table = exposure.combined_risk_table(result, futures, scenarios={})
    inner = table.children[1]
    row = next(r for r in inner.data if r[exposure.RISK_LABEL_COL] == "ESU6 Index")
    assert "Unavailable" in row["usd_delta"]
    assert "no FUTURE_PX" in row["usd_delta"]


def test_combined_risk_table_net_excludes_futures_gross_includes():
    from engine.ladder.exposure import build_exposure, portfolio_totals
    result = build_exposure(RECORDS, RATES)
    totals = portfolio_totals(result)
    futures = {"value": 100_000.0, "by_instrument": {"ESU6 Index": 100_000.0}, "missing": [], "reason": ""}
    table = exposure.combined_risk_table(result, futures, scenarios={})
    inner = table.children[1]  # H4 title, then the DataTable
    rows = {r[exposure.RISK_LABEL_COL]: r for r in inner.data}
    # Net (currencies only) matches portfolio_totals; Gross (currencies + |futures|) adds 100,000
    net_formatted = exposure.format_amount(totals["net_usd"])
    gross_formatted = exposure.format_amount(totals["gross_usd"] + 100_000.0)
    assert rows["Net USD (currencies only)"]["usd_delta"] == net_formatted
    assert rows["Gross USD (currencies + |futures|)"]["usd_delta"] == gross_formatted


def test_futures_table_present_with_mark():
    futures = {"value": 100_000.0, "by_instrument": {"ESU6 Index": 100_000.0}, "missing": [], "reason": ""}
    table = exposure.futures_table(futures)
    assert table.id == exposure.FUTURES_TABLE_ID
    assert table.data[0]["instrument"] == "ESU6 Index"


def test_futures_table_unavailable_with_reason_when_no_mark():
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["ESU6 Index"],
               "reason": "no FUTURE_PX on 2026-08-17 for ESU6 Index"}
    table = exposure.futures_table(futures)
    assert "no FUTURE_PX" in table.data[0]["usd_delta"]


def _render_text(node):
    parts = []
    children = getattr(node, "children", None)
    if isinstance(children, str):
        parts.append(children)
    elif isinstance(children, list):
        for c in children:
            parts.append(_render_text(c))
    elif children is not None:
        parts.append(_render_text(children))
    return " ".join(p for p in parts if p)


def test_exposure_section_builds_without_market_data_panel():
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES)
    text = _render_text(section)
    assert "Net USD exposure" in text
    assert "Risk (currencies + open futures)" in text
    assert "Open futures" in text


def test_ledger_tab_module_removed():
    with pytest.raises(ModuleNotFoundError):
        import importlib
        importlib.import_module("ui.tabs.ledger")
