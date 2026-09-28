"""Tests for ui/tabs/expiries.py (the Timing & cash tab): the tab renders
`engine.expiry.expiry_schedule`'s output and computes no date, count or level of its own. The
roll table was rebuilt on 2026-09-28 (the screens tidy, wave 2: seven columns, an html.Table):
the tests pinned to the former DataTable's records, columns and level chips were deleted, not
re-pinned; `tests/test_ui_smoke.py` is the screens' standing cover. What is left here is the
settled section's absence, the Data issues drawer and the tab's failure wording.

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import itertools
import sys
import types

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.contracts import store_static_dates  # noqa: E402
from data.ingest import schema  # noqa: E402
from ui.tabs import expiries  # noqa: E402

AS_OF = "2026-09-24"
_trade_ids = itertools.count(1)


# --------------------------------------------------------------------------- helpers
def _walk(node):
    yield node
    children = getattr(node, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    else:
        yield from _walk(children)


def _text(node) -> str:
    return " ".join(n for n in _walk(node) if isinstance(n, str))


def _trade(conn, contract_id, root_id, lots, trade_date="2026-09-01"):
    conn.execute(
        "INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
        "is_ndf, bbg_ticker, expiry_date) VALUES (?, 'FUTURE', ?, 'USD', 1000, 0, '', '2026-12-31')",
        (contract_id, root_id))
    tid = f"T{next(_trade_ids)}"
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, "
        "price, account, counterparty, strategy, trader, description) "
        "VALUES (?, 'XLSX', ?, 'FUTURE', ?, ?, ?, 100, 'ACC', 'CP', '', 'JB', '')",
        (tid, contract_id, tid, trade_date, lots))
    conn.commit()
    return tid


def _book(path, *, empty=False):
    conn = schema.connect(str(path))
    if not empty:
        _trade(conn, "CLX26 Comdty", "NYMEX:CL", 1)                          # estimated, physical: AMBER
        _trade(conn, "HGZ26 Comdty", "COMEX:HG", -4)                         # Bloomberg's dates: GREEN
        store_static_dates(conn, [{"contract_id": "HGZ26 Comdty", "last_trade_date": "2026-12-29",
                                   "first_notice_date": "2026-11-20", "source": "BBG_BDP"}])
        _trade(conn, "CLQ26 Comdty", "NYMEX:CL", -2, trade_date="2026-06-01")  # past its last trade: EXPIRED
        store_static_dates(conn, [{"contract_id": "CLQ26 Comdty", "last_trade_date": "2026-07-21",
                                   "first_notice_date": "2026-07-22", "source": "BBG_BDP"}])
    conn.close()
    return path


@pytest.fixture
def stub_app(monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    return stub


def _row(**over) -> dict:
    base = {"root_id": "NYMEX:CL", "name": "WTI Crude", "sector": "Energy", "exchange": "NYMEX",
            "calendar": "US", "delivery": "physical", "delivery_assumed": "physical",
            "contract_id": "CLX26 Comdty", "lots": 1.0, "last_trade_date": "2026-10-20",
            "first_notice_date": "2026-10-21", "dates_source": "BLOOMBERG", "estimated": False,
            "next_event": "last trade", "next_event_date": "2026-10-20", "alert_date": "2026-10-20",
            "alert_basis": "last trade", "business_days": 18, "level": "GREEN",
            "reason": "Last trade in 18 business days on the US calendar, +1 lot held.",
            "beyond_calendar_coverage": False}
    base.update(over)
    return base


def _result(rows, note="", settled=None):
    counts = {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in ("EXPIRED", "RED", "AMBER", "GREEN")}
    return {"as_of": AS_OF, "rows": rows, "counts": counts, "thresholds": {"RED": 3, "AMBER": 10}, "note": note,
            "settled_expired": settled or []}


def _issues(body):
    return next((n for n in _walk(body) if getattr(n, "id", None) == expiries.ISSUES_ID), None)


def _settled(body):
    return next((n for n in _walk(body) if getattr(n, "id", None) == expiries.SETTLED_ID), None)


# --------------------------------------------------------------------------- tests
def test_the_settled_section_is_absent_when_the_list_is_empty(tmp_path, stub_app):
    assert _settled(expiries.body(_result([_row()]))) is None
    assert _settled(expiries.render(AS_OF, _book(tmp_path / "risk.db"))) is None
    assert "Expired and settled" not in _text(expiries.body(_result([_row()])))


def test_the_issues_drawer_gathers_the_reasons_and_is_absent_when_clean():
    assert _issues(expiries.body(_result([_row()]))) is None
    rows = [_row(level="RED", business_days=None, contract_id="XXZ26 Comdty",
                 reason="+1 lot held, but its dates are unknown."),
            _row(estimated=True, dates_source="ESTIMATED", contract_id="CLF27 Comdty"),
            _row(contract_id="CLG27 Comdty")]
    drawer = _issues(expiries.body(_result(rows)))
    text = _text(drawer)
    assert "Data issues (2)" in text
    assert "+1 lot held, but its dates are unknown." in text
    assert "1 of 3 open positions are dated by contract-master's estimate" in text and "CLF27 Comdty" in text


def test_render_says_why_when_there_is_no_date_no_database_or_an_engine_failure(tmp_path, stub_app, monkeypatch):
    import sqlite3
    assert "No as-of date" in expiries.render(None, tmp_path / "risk.db").children

    def boom(conn, as_of):
        raise ValueError("bad contract row")
    monkeypatch.setattr(expiries, "expiry_schedule", boom)
    out = expiries.render(AS_OF, tmp_path / "risk.db")
    assert "could not be built for 2026-09-24 (ValueError: bad contract row)" in _text(out)

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    stub_app.connect_readonly = refuse
    assert "Database not available" in expiries.render(AS_OF, tmp_path / "missing.db").children
