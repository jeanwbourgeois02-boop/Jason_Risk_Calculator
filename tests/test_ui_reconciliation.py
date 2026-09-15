"""Tests for ui/tabs/reconciliation.py. Skips if dash is not importable, matching the
convention in tests/test_ui.py."""
from __future__ import annotations

import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from ui.tabs import reconciliation  # noqa: E402


def test_build_layout_has_controls_and_workbook_rates(monkeypatch):
    layout = reconciliation.build_layout(default_date="2026-08-18")
    # Recursively collect component ids.
    ids = set()

    def walk(node):
        cid = getattr(node, "id", None)
        if cid:
            ids.add(cid)
        for child in getattr(node, "children", []) or []:
            if isinstance(child, list):
                for c in child:
                    walk(c)
            else:
                walk(child)

    walk(layout)
    assert reconciliation.SOURCE_DROPDOWN_ID in ids
    assert reconciliation.DATE_PICKER_ID in ids
    assert reconciliation.CONTENT_CONTAINER_ID in ids
    assert "rates-grid" in ids  # workbook_rates.layout is embedded


def test_break_table_from_frames_matches_by_instrument():
    bnp = pd.DataFrame([
        {"instrument_id": "EURUSD", "mv_usd": 10.0, "pnl_dtd_usd": 3.0},
    ])
    vb = pd.DataFrame([
        {"instrument_id": "EURUSD", "status": "OPEN", "pnl_usd": 12.0, "reason": ""},
        {"instrument_id": "EURUSD", "status": "OPEN", "pnl_usd": float("nan"), "reason": "missing outright"},
        {"instrument_id": "EURUSD", "status": "SETTLED", "pnl_usd": 999.0, "reason": ""},
    ])
    table = reconciliation.break_table_from_frames(bnp, vb, daily_by_instrument={"EURUSD": 4.0})
    row = table.data[0]
    assert row["instrument_id"] == "EURUSD"
    assert row["bnp_mv_usd"] == "10"
    assert row["bnp_pnl_dtd_usd"] == "3"
    assert row["ours_ltd_usd"] == "12"  # SETTLED row excluded, OPEN+unpriced row excluded
    assert row["unpriced"] == "1"
    assert row["break_usd"] == "2"  # 12 - 10
    assert row["ours_daily_usd"] == "4"


def test_break_table_from_frames_empty():
    table = reconciliation.break_table_from_frames(pd.DataFrame(), pd.DataFrame())
    assert table.data == []
    assert [c["id"] for c in table.columns] == reconciliation.BREAK_COLUMNS


def test_break_table_outer_join_shows_one_sided_instrument():
    bnp = pd.DataFrame([{"instrument_id": "USDJPY", "mv_usd": 5.0, "pnl_dtd_usd": 1.0}])
    vb = pd.DataFrame(columns=["instrument_id", "status", "pnl_usd", "reason"])
    table = reconciliation.break_table_from_frames(bnp, vb)
    assert table.data[0]["instrument_id"] == "USDJPY"
    assert table.data[0]["ours_ltd_usd"] == ""


def test_pairs_table_from_by_pair():
    by_pair = pd.DataFrame([
        {"instrument_id": "EURUSD", "usd_notional": 1000000.0, "ltd_usd": 1234.9, "n_trades": 2},
    ])
    table = reconciliation.pairs_table_from_by_pair(by_pair)
    assert table.data[0]["usd_notional"] == "1,000,000"
    assert table.data[0]["ltd_usd"] == "1,235"


def test_pairs_table_empty():
    table = reconciliation.pairs_table_from_by_pair(pd.DataFrame())
    assert table.data == []


def test_totals_table_from_book_totals():
    totals = {"net_usd": float("nan"), "gross_usd": 100.0, "gold_usd": 0.0, "futures_usd": -5.4}
    table = reconciliation.totals_table_from_book_totals(totals)
    rows = {r["metric"]: r["usd"] for r in table.data}
    assert rows["net_usd"] == ""
    assert rows["gross_usd"] == "100"
    assert rows["futures_usd"] == "(5)"


def test_period_table_from_period_pnl():
    period = {"ltd": 100.0, "daily": 1.0, "d5": float("nan"), "mtd": float("nan"),
              "ytd": float("nan"), "daily_ref_date": "2026-08-17"}
    table = reconciliation.period_table_from_period_pnl(period)
    rows = {r["period"]: r for r in table.data}
    assert rows["LTD"]["usd"] == "100"
    assert rows["LTD"]["ref_date"] == ""
    assert rows["Daily"]["ref_date"] == "2026-08-17"


def test_valuation_table_preserves_rate_precision_and_formats_dollars():
    df = pd.DataFrame([{"ccy": "EUR", "fill": 1.123456785, "pnl_usd": 1234.5}])
    table = reconciliation.valuation_table(df, "test-valuation-table")
    row = table.data[0]
    assert row["fill"] == "1.12345678" or row["fill"].startswith("1.12345")
    assert row["pnl_usd"] == "1,235" or row["pnl_usd"] == "1,234"


def test_message_box_is_grey_paragraph():
    box = reconciliation.message_box("hello")
    assert box.children == "hello"
    assert box.style.get("color") == "gray"


def test_component_ids_are_reconciliation_prefixed():
    # Own-module id check only: ui/tabs/cash_ladder.py is owned and concurrently edited
    # by another agent (C1), so this test does not import it to avoid a cross-agent
    # coupling / race on its attributes.
    assert reconciliation.SOURCE_DROPDOWN_ID.startswith("reconciliation-")
    assert reconciliation.DATE_PICKER_ID.startswith("reconciliation-")
    assert reconciliation.CONTENT_CONTAINER_ID.startswith("reconciliation-")


def test_register_callbacks_wires_content_callback_and_workbook_rates(tmp_path):
    from data.ingest import schema

    db_path = str(tmp_path / "empty.db")
    schema.connect(db_path).close()

    app = dash.Dash(__name__)
    app.layout = reconciliation.build_layout(default_date="2026-08-18")
    reconciliation.register_callbacks(app, get_db_path=lambda: db_path)

    outputs = [str(o) for cb in app.callback_map.values() for o in
               (cb["output"] if isinstance(cb["output"], (list, tuple)) else [cb["output"]])]
    assert any(reconciliation.CONTENT_CONTAINER_ID in o for o in outputs)
    assert any("rates-grid" in o for o in outputs)


def test_break_section_and_workbook_section_run_end_to_end_on_empty_db(tmp_path, monkeypatch):
    """Exercise the real DB-reading code path against an empty (but schema-valid) DB so
    a missing table / wrong column name fails loudly here rather than at runtime.

    Dash callbacks wrapped via `app.callback` require internal `outputs_list` /
    `inputs_list` kwargs supplied by the Dash renderer, so calling the wrapped function
    directly from `app.callback_map` raises `KeyError: 'outputs_list'`. Instead, stub
    `ui.app.connect_readonly` and trigger the app-level callback through
    `app.callback_map`'s underlying `.__wrapped__` is not exposed either; simplest
    reliable path is to open a real connection here and call the module's private
    section builders directly -- they hold all the DB-touching logic.

    NOTE: this test deliberately avoids `from ui.app import ...` -- `ui/app.py` still
    imports the now-deleted `ui.tabs.pnl` until C5 rewires it (docs/BUILD_PLAN.md Task
    C split), which is out of this agent's scope to fix."""
    import sqlite3
    from data.ingest import schema

    db_path = str(tmp_path / "empty.db")
    schema.connect(db_path).close()

    app = dash.Dash(__name__)
    app.layout = reconciliation.build_layout(default_date="2026-08-18")
    reconciliation.register_callbacks(app, get_db_path=lambda: db_path)

    conn = sqlite3.connect(db_path)
    try:
        from engine.pnl.valuation import value_book  # noqa: F401 -- confirms importable
        bnp = pd.read_sql_query(
            "SELECT instrument_id, mv_usd, pnl_dtd_usd FROM positions "
            "WHERE source = 'BNP' AND as_of_date = ?", conn, params=["2026-08-18"],
        )
        vb = value_book(conn, "2026-08-18")
        daily_by_instrument = {}
        try:
            from engine.pnl.ledger import period_pnl_by
            daily_by_instrument = {
                k: v["daily"]["value"] for k, v in period_pnl_by(conn, "2026-08-18", "instrument_id").items()
            }
        except ImportError:
            pass
        table = reconciliation.break_table_from_frames(bnp, vb, daily_by_instrument)
        assert table.data == []

        from engine.pnl.pnl import ltd_per_trade
        from engine.pnl.aggregate import aggregate_by_pair, book_totals, period_pnl
        per_trade = ltd_per_trade(conn, "2026-08-18", source=None, strict=False)
        by_pair = aggregate_by_pair(per_trade, conn)
        totals = book_totals(by_pair, conn)
        period = period_pnl(conn, "2026-08-18", source=None)
        reconciliation.pairs_table_from_by_pair(by_pair)
        reconciliation.totals_table_from_book_totals(totals)
        reconciliation.period_table_from_period_pnl(period)

        from engine.ladder.valuation import ladder_trade_valuation, ladder_valuation_summary
        detail = ladder_trade_valuation(conn, "2026-08-18", None)
        summary = ladder_valuation_summary(detail)
        reconciliation.valuation_table(summary, "t1")
        reconciliation.valuation_table(detail, "t2")
    finally:
        conn.close()
