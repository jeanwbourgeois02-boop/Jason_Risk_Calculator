"""Tests for ui/tabs/expiries.py: the Expiries tab renders `engine.expiry.expiry_schedule`'s
output and computes no date, count or level of its own. The end-to-end tests go through the
real engine on a small database in tmp_path; the rest feed the body builders a synthetic
result.

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import itertools
import sys
import types

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from dash import dash_table  # noqa: E402

from data.contracts import store_static_dates  # noqa: E402
from data.ingest import schema  # noqa: E402
from engine.expiry import expiry_schedule  # noqa: E402
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


def _ids(node) -> list:
    return [i for i in (getattr(n, "id", None) for n in _walk(node)) if isinstance(i, str)]


def _table(node) -> dash_table.DataTable:
    found = [n for n in _walk(node) if isinstance(n, dash_table.DataTable) and getattr(n, "id", None) == expiries.TABLE_ID]
    assert len(found) == 1, f"{len(found)} tables"
    return found[0]


def _counts(body) -> dict:
    """level -> the count shown on its card."""
    strip = next(n for n in _walk(body) if getattr(n, "id", None) == expiries.COUNTS_ID)
    return {card.children[0].children: card.children[1].children for card in strip.children}


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


def _result(rows, note=""):
    counts = {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in ("EXPIRED", "RED", "AMBER", "GREEN")}
    return {"as_of": AS_OF, "rows": rows, "counts": counts, "thresholds": {"RED": 3, "AMBER": 10}, "note": note}


# --------------------------------------------------------------------------- end to end
def test_render_shows_the_engines_rows_worst_first(tmp_path, stub_app):
    db = _book(tmp_path / "risk.db")
    body = expiries.render(AS_OF, db)
    conn = schema.connect(str(db))
    engine = expiry_schedule(conn, AS_OF)
    conn.close()
    table = _table(body)
    assert [r["contract"] for r in table.data] == [r["contract_id"] for r in engine["rows"]] \
        == ["CLQ26 Comdty", "CLX26 Comdty", "HGZ26 Comdty"]
    assert [r["level"] for r in table.data] == ["EXPIRED", "AMBER", "GREEN"]
    assert [r["rank"] for r in table.data] == [1, 2, 3]
    by = {r["contract"]: r for r in table.data}
    eng = {r["contract_id"]: r for r in engine["rows"]}
    # the engine's numbers, stored as numbers, never recounted
    for cid in by:
        assert by[cid]["business_days"] == eng[cid]["business_days"]
        assert by[cid]["lots"] == eng[cid]["lots"]
    assert by["CLX26 Comdty"]["business_days"] == 5 and by["CLQ26 Comdty"]["business_days"] < 0
    assert by["HGZ26 Comdty"]["lots"] == -4.0
    # Bloomberg's dates are shown as they are, not as estimates
    hg = by["HGZ26 Comdty"]
    assert (hg["next_event"], hg["next_event_date"], hg["alert_date"]) == ("first notice", "2026-11-20", "2026-11-20")
    assert hg["dates_source"] == "Bloomberg" and "(est.)" not in hg["last_trade_date"]
    assert hg["delivery"] == "physical"


def test_render_counts_strip_matches_the_engines_counts(tmp_path, stub_app):
    body = expiries.render(AS_OF, _book(tmp_path / "risk.db"))
    assert _counts(body) == {"EXPIRED": "1", "RED": "0", "AMBER": "1", "GREEN": "1"}
    text = _text(body)
    assert "RED: 3 business days or fewer to the alert date; AMBER: 10 or fewer" in text


def test_an_estimated_date_is_marked_est_and_never_as_bloombergs(tmp_path, stub_app):
    body = expiries.render(AS_OF, _book(tmp_path / "risk.db"))
    cl = next(r for r in _table(body).data if r["contract"] == "CLX26 Comdty")
    assert cl["last_trade_date"] == "2026-11-30 (est.)"
    assert cl["next_event_date"] == "2026-11-30 (est.)"
    assert cl["alert_date"] == "2026-10-01 (est.)"
    assert cl["alert_basis"] == "estimated: first business day of Oct 2026"
    assert cl["dates_source"] == "Estimated, not Bloomberg's"
    assert cl["first_notice_date"] == "none on file"


def test_business_days_column_is_labelled_as_days_to_the_alert_date():
    table = _table(expiries.body(_result([_row()])))
    col = next(c for c in table.columns if c["id"] == "business_days")
    assert col["name"] == "Business days to alert date" and col["type"] == "numeric"
    assert "alert date" in table.tooltip_header["business_days"]
    assert all("to event" not in c["name"].lower() for c in table.columns)


def test_the_reason_is_a_column_and_on_hover(tmp_path, stub_app):
    body = expiries.render(AS_OF, _book(tmp_path / "risk.db"))
    table = _table(body)
    for rec, tip in zip(table.data, table.tooltip_data):
        assert rec["reason"]
        assert tip["contract"]["value"] == rec["reason"] == tip["business_days"]["value"]
    expired = table.data[0]
    assert "still held" in expired["reason"]


def test_the_empty_book_shows_the_engines_note(tmp_path, stub_app):
    body = expiries.render(AS_OF, _book(tmp_path / "risk.db", empty=True))
    assert "No commodity futures position is open as of 2026-09-24." in _text(body)
    assert not [n for n in _walk(body) if isinstance(n, dash_table.DataTable)]
    assert _counts(body) == {"EXPIRED": "0", "RED": "0", "AMBER": "0", "GREEN": "0"}


# --------------------------------------------------------------------------- synthetic rows
def test_unknown_delivery_reads_assumed_physical_and_beyond_coverage_is_marked():
    rows = [_row(delivery="", delivery_assumed="physical", beyond_calendar_coverage=True)]
    rec = _table(expiries.body(_result(rows))).data[0]
    assert rec["delivery"] == "not on file (assumed physical)"
    assert rec["calendar"] == "US (beyond coverage)"


def test_a_count_the_engine_could_not_make_reads_na_with_its_reason():
    rows = [_row(level="RED", business_days=None, next_event=None, next_event_date=None, alert_date=None,
                 last_trade_date=None, first_notice_date=None, dates_source="", estimated=True, alert_basis="",
                 reason="+1 lot held, but its dates are unknown.")]
    table = _table(expiries.body(_result(rows)))
    rec, tip = table.data[0], table.tooltip_data[0]
    assert rec["business_days"] == "n/a" and tip["business_days"]["value"] == "+1 lot held, but its dates are unknown."
    assert rec["next_event_date"] == rec["alert_date"] == rec["last_trade_date"] == rec["first_notice_date"] == "n/a"
    assert rec["dates_source"] == "unknown" and rec["level"] == "RED"


def test_level_cells_are_coloured_by_level():
    table = _table(expiries.body(_result([_row()])))
    rules = {r["if"]["filter_query"]: r for r in table.style_data_conditional if r["if"].get("column_id") == "level"}
    for level in ("EXPIRED", "RED", "AMBER", "GREEN"):
        assert f"{{level}} = '{level}'" in rules
    assert rules["{level} = 'RED'"]["backgroundColor"] != rules["{level} = 'GREEN'"]["backgroundColor"]


# --------------------------------------------------------------------------- shell
def test_layout_is_an_expiries_prefixed_shell_with_no_date_picker():
    layout = expiries.layout(AS_OF)
    ids = _ids(layout)
    assert expiries.BODY_ID in ids and expiries.REFRESH_ID in ids
    assert all(i.startswith("expiries-") for i in ids), ids
    assert not any(type(n).__name__ == "DatePickerSingle" for n in _walk(layout))
    assert "Expiries" in _text(layout) and AS_OF in _text(layout)
    assert expiries.build_layout is expiries.layout


def test_register_callbacks_listens_to_the_shared_ids(tmp_path, stub_app):
    from ui.revision import DATA_REVISION_ID
    from ui.tabs.header import AS_OF_STORE_ID
    db = _book(tmp_path / "risk.db")
    app = dash.Dash(__name__)
    expiries.register_callbacks(app, get_db_path=lambda: str(db))
    keys = [k for k in app.callback_map if expiries.BODY_ID in k]
    assert len(keys) == 1
    inputs = {(i["id"], i["property"]) for i in app.callback_map[keys[0]]["inputs"]}
    assert inputs == {(AS_OF_STORE_ID, "data"), (DATA_REVISION_ID, "data"), (expiries.REFRESH_ID, "n_intervals")}
    callback = app.callback_map[keys[0]]["callback"].__wrapped__
    assert [r["contract"] for r in _table(callback(AS_OF, "rev", 0)).data][0] == "CLQ26 Comdty"


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
