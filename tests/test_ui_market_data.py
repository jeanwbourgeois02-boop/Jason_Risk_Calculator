"""Tests for ui/tabs/market_data.py (C3 Market data tab)."""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from ui.tabs import market_data as md


def test_build_layout_has_expected_ids():
    layout = md.build_layout(default_date="2026-08-18")
    rendered = str(layout)
    for expected_id in (md.DATE_PICKER_ID, md.STATUS_ID, md.BODY_ID, md.PULL_NOW_ID,
                        md.PULL_NOW_STATUS_ID, md.PULL_REVISION_ID, md.REFRESH_ID):
        assert expected_id in rendered


def test_feed_headline_variants():
    assert md.feed_headline(None) == "Bloomberg: no pull recorded yet"
    assert "not connected" in md.feed_headline({"connected": False, "reason": "no port"})
    text = md.feed_headline({"connected": True, "time": "t", "written": 3, "failed": 1})
    assert "3 marks written, 1 failed" in text


def test_inventory_table_formats_missing_blank():
    df = pd.DataFrame([
        {"instrument_id": "EURUSD", "settle_date": "2026-08-18", "mark_type": "SPOT",
         "value": 1.1, "source": "BBG_BFXFORWARD", "snapped_at": "2026-08-18T15:00:00-04:00",
         "status": "OFFICIAL"},
        {"instrument_id": "GBPUSD", "settle_date": "2026-08-18", "mark_type": "SPOT",
         "value": None, "source": None, "snapped_at": None, "status": "MISSING"},
    ])
    table = md.inventory_table(df)
    values = [row["value"] for row in table.data]
    assert values[0] == "1.10000000"
    assert values[1] == ""
    statuses = {row["status"] for row in table.data}
    assert statuses == {"OFFICIAL", "MISSING"}


def test_completeness_table_marks_incomplete_days():
    df = pd.DataFrame([
        {"as_of_date": "2026-08-17", "needed": 5, "present": 5, "complete": True},
        {"as_of_date": "2026-08-18", "needed": 5, "present": 3, "complete": False},
    ])
    table = md.completeness_table(df)
    completes = [row["complete"] for row in table.data]
    assert completes == ["yes", "no"]


def test_manual_entry_form_has_fields():
    form = market_form = md.manual_entry_form()
    rendered = str(form)
    for expected_id in (md.MANUAL_INSTRUMENT_ID, md.MANUAL_SETTLE_ID, md.MANUAL_MARK_TYPE_ID,
                        md.MANUAL_VALUE_ID, md.MANUAL_SUBMIT_ID, md.MANUAL_STATUS_ID):
        assert expected_id in rendered


def test_message_box_is_grey_paragraph():
    box = md.message_box("nothing here")
    assert box.children == "nothing here"


class _StubApp:
    """Minimal stand-in for a Dash app: records callbacks without needing a real
    Dash server, mirroring the pattern already used for the cash-ladder tab."""

    def __init__(self):
        self.callbacks = []

    def callback(self, *args, **kwargs):
        def _decorator(fn):
            self.callbacks.append(fn)
            return fn
        return _decorator


def test_register_callbacks_registers_three_callbacks():
    app = _StubApp()
    md.register_callbacks(app, get_db_path=lambda: ":memory:")
    assert len(app.callbacks) == 3


def test_render_reports_database_not_available(monkeypatch, tmp_path):
    app = _StubApp()
    missing_db = tmp_path / "does-not-exist.db"
    md.register_callbacks(app, get_db_path=lambda: str(missing_db))
    update_body = app.callbacks[0]
    body, status = update_body("2026-08-18")
    assert "not available" in str(body).lower() or "Database not available" in str(body)


def test_render_no_as_of_date():
    app = _StubApp()
    md.register_callbacks(app, get_db_path=lambda: ":memory:")
    update_body = app.callbacks[0]
    body, status = update_body(None)
    assert "No as-of date available." in str(body)


def test_pull_now_reports_not_connected(monkeypatch):
    app = _StubApp()
    md.register_callbacks(app, get_db_path=lambda: ":memory:")
    pull_now = app.callbacks[1]

    def _fake_pull_once(db_path):
        return {"connected": False, "reason": "blpapi is not installed on this computer"}

    monkeypatch.setattr("data.bloomberg.live.pull_once", _fake_pull_once)
    text, revision = pull_now(1)
    assert "Not pulled" in text
    assert revision


def test_manual_submit_requires_all_fields():
    app = _StubApp()
    md.register_callbacks(app, get_db_path=lambda: ":memory:")
    submit = app.callbacks[2]
    result = submit(1, None, "EURUSD", "2026-08-18", "SPOT", 1.1)
    assert "Fill in every field" in result


def test_manual_submit_writes_manual_mark(tmp_path):
    from data.ingest.schema import connect

    db_path = tmp_path / "risk.db"
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO instruments VALUES ('EURUSD','FX','USD','EUR',1,0,'EURUSD Curncy','9999-12-31')"
    )
    conn.commit()
    conn.close()

    app = _StubApp()
    md.register_callbacks(app, get_db_path=lambda: str(db_path))
    submit = app.callbacks[2]
    result = submit(1, "2026-08-18", "EURUSD", "2026-08-19", "FWD_OUTRIGHT", 1.2345)
    assert "Saved MANUAL" in result

    check = sqlite3.connect(str(db_path))
    row = check.execute(
        "SELECT value, source FROM marks WHERE instrument_id='EURUSD' AND mark_type='FWD_OUTRIGHT'"
    ).fetchone()
    check.close()
    assert row == (1.2345, "MANUAL")


def test_render_with_populated_db(tmp_path):
    from data.ingest.schema import connect

    db_path = tmp_path / "risk.db"
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO instruments VALUES ('EURUSD','FX','USD','EUR',1,0,'EURUSD Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES ('T1','MANUAL','EURUSD','FX_FWD','T1','2026-08-10',1000000,1.1,"
        "'ACC','CPTY','HAHY7','trader','desc','')"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',1,'FX_NEAR','EUR',1000000,'2026-08-10','2026-08-20',1.1,0)"
    )
    conn.execute(
        "INSERT INTO trade_legs VALUES ('T1',2,'FX_NEAR','USD',-1100000,'2026-08-10','2026-08-20',1.1,1)"
    )
    conn.commit()
    conn.close()

    app = _StubApp()
    md.register_callbacks(app, get_db_path=lambda: str(db_path))
    update_body = app.callbacks[0]
    body, status = update_body("2026-08-18")
    rendered = str(body)
    assert "Marks needed today" in rendered
    assert "Close completeness" in rendered
    assert "Manual mark entry" in rendered
