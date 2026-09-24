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
    return tid


def _freeze(conn, trade_id, contract_id, settle_date, frozen_at):
    """A realised_pnl row as the ledger writes it: the trade has left the book."""
    conn.execute(
        "INSERT INTO realised_pnl (trade_id, instrument_id, product, currency, settle_date, local_amount, "
        "usd_entry_amount, mark_type, spot_usd_per_local, spot_as_of_date, spot_source, pnl_usd, frozen_at, note) "
        "VALUES (?, ?, 'FUTURE', 'USD', ?, 0, 0, 'FUTURE_PX', 1, ?, 'BBG_BDH', 0, ?, '')",
        (trade_id, contract_id, settle_date, settle_date, frozen_at))
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


def _result(rows, note="", settled=None):
    counts = {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in ("EXPIRED", "RED", "AMBER", "GREEN")}
    return {"as_of": AS_OF, "rows": rows, "counts": counts, "thresholds": {"RED": 3, "AMBER": 10}, "note": note,
            "settled_expired": settled or []}


def _settled(body):
    return next((n for n in _walk(body) if getattr(n, "id", None) == expiries.SETTLED_ID), None)


def _settled_table(body) -> dash_table.DataTable:
    found = [n for n in _walk(body) if isinstance(n, dash_table.DataTable)
             and getattr(n, "id", None) == expiries.SETTLED_TABLE_ID]
    assert len(found) == 1, f"{len(found)} settled tables"
    return found[0]


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


def test_a_frozen_contract_leaves_the_alert_table_for_the_settled_section(tmp_path, stub_app):
    db = _book(tmp_path / "risk.db")
    conn = schema.connect(str(db))
    tid = _trade(conn, "CLN26 Comdty", "NYMEX:CL", 3, trade_date="2026-05-01")
    store_static_dates(conn, [{"contract_id": "CLN26 Comdty", "last_trade_date": "2026-06-22",
                               "first_notice_date": "2026-06-23", "source": "BBG_BDP"}])
    _freeze(conn, tid, "CLN26 Comdty", "2026-06-22", "2026-06-23T09:00:00+00:00")
    engine = expiry_schedule(conn, AS_OF)
    conn.close()
    assert [e["contract_id"] for e in engine["settled_expired"]] == ["CLN26 Comdty"]

    body = expiries.render(AS_OF, db)
    assert "CLN26 Comdty" not in [r["contract"] for r in _table(body).data]
    assert _counts(body) == {"EXPIRED": "1", "RED": "0", "AMBER": "1", "GREEN": "1"}   # never counted
    section = _settled(body)
    assert section is not None and section.open is False
    assert "Expired and settled (1)" in _text(section)
    table = _settled_table(body)
    assert [c["id"] for c in table.columns] == ["contract", "name", "lots", "last_trade_date", "frozen_at", "reason"]
    rec = table.data[0]
    assert rec["contract"] == "CLN26 Comdty" and rec["lots"] == 3.0
    assert rec["last_trade_date"] == "2026-06-22" and rec["frozen_at"] == "2026-06-23T09:00:00+00:00"
    assert rec["reason"] == engine["settled_expired"][0]["reason"]
    assert "level" not in rec
    assert not any(r["if"].get("column_id") == "level" for r in table.style_data_conditional)


def test_the_settled_section_is_absent_when_the_list_is_empty(tmp_path, stub_app):
    assert _settled(expiries.body(_result([_row()]))) is None
    assert _settled(expiries.render(AS_OF, _book(tmp_path / "risk.db"))) is None
    assert "Expired and settled" not in _text(expiries.body(_result([_row()])))


def test_an_estimated_settled_date_is_marked_est():
    entry = {"product": "FUTURE", "contract_id": "CLF26 Comdty", "root_id": "NYMEX:CL", "name": "WTI Crude",
             "lots": -1.0, "last_trade_date": "2026-01-30", "dates_source": "ESTIMATED", "estimated": True,
             "frozen_at": None, "reason": "Expired 2026-01-30 with -1 lot held; the ledger has frozen all 1 trade."}
    body = expiries.body(_result([], note="No commodity futures position is open as of 2026-09-24.", settled=[entry]))
    rec = _settled_table(body).data[0]
    assert rec["last_trade_date"] == "2026-01-30 (est.)" and rec["frozen_at"] == "n/a"
    assert "Expired and settled (1)" in _text(body)


def test_the_product_column_shows_only_when_more_than_one_product_is_present():
    one = _table(expiries.body(_result([_row(product="FUTURE"), _row(product="FUTURE", contract_id="CLZ26 Comdty")])))
    assert "product" not in [c["id"] for c in one.columns]
    two = _table(expiries.body(_result([_row(product="FUTURE"),
                                        _row(product="CMDTY_OPTION", contract_id="CLZ26C 70 Comdty"),
                                        _row(product="LME_FWD", contract_id="LMCADS 2026-12-16")])))
    ids = [c["id"] for c in two.columns]
    assert ids.index("product") == ids.index("level") + 1
    assert [r["product"] for r in two.data] == ["Future", "Option", "LME prompt"]


# --------------------------------------------------------------------------- Phase 5: options and LME prompts
def _option_row(**over) -> dict:
    return _row(product="CMDTY_OPTION", contract_id="CLZ26C 70 Comdty", last_trade_date="2026-11-17",
                first_notice_date=None, next_event="option expiry", next_event_date="2026-11-17",
                alert_date="2026-11-17", alert_basis="option expiry", business_days=38,
                option_type="CALL", strike=70.0, style="AMERICAN", underlying_id="CLZ26 Comdty",
                underlying_event="first notice", underlying_event_date="2026-11-20", underlying_estimated=True,
                reason="Option expiry in 38 business days on the US calendar, +1 lot held.", **over)


def _lme_row(**over) -> dict:
    base = dict(product="LME_FWD", root_id="LME:CA", name="LME Copper", exchange="LME", calendar="LME",
                contract_id="LME:CA 2026-12-16", lots=2.0, tonnes=50.0, prompt_date="2026-12-16",
                instrument_id="LME:CA", last_trade_date=None, first_notice_date=None, dates_source="TICKET",
                estimated=False, next_event="LME prompt", next_event_date="2026-12-16", alert_date="2026-12-14",
                alert_basis="cash date: prompt less 2 LME business days", business_days=55,
                reason="The 2026-12-16 prompt becomes the cash date in 55 business days, +50 t (+2 lots) held.")
    base.update(over)
    return _row(**base)


def test_an_option_row_and_an_lme_row_render():
    table = _table(expiries.body(_result([_row(product="FUTURE"), _option_row(), _lme_row()])))
    by = {r["contract"]: (r, t) for r, t in zip(table.data, table.tooltip_data)}
    assert "product" in [c["id"] for c in table.columns]

    opt, opt_tip = by["CLZ26C 70 Comdty"]
    assert opt["product"] == "Option" and opt["next_event"] == "option expiry"
    assert opt["last_trade_date"] == "2026-11-17" and opt["dates_source"] == "Bloomberg"
    hover = opt_tip["next_event"]["value"]
    assert "Call" in hover and "strike 70" in hover and "American" in hover
    assert "CLZ26 Comdty" in hover and "first notice 2026-11-20 (est.)" in hover

    lme, lme_tip = by["LME:CA 2026-12-16"]
    assert lme["product"] == "LME prompt" and lme["next_event"] == "LME prompt"
    assert lme["lots"] == 2.0 and lme_tip["lots"]["value"].startswith("50 tonnes")
    assert lme["next_event_date"] == "2026-12-16" and lme["alert_date"] == "2026-12-14"
    assert lme["alert_basis"] == "cash date: prompt less 2 LME business days"


def test_a_first_notice_that_does_not_apply_reads_a_dash_never_missing():
    table = _table(expiries.body(_result([_option_row(), _lme_row()])))
    (opt, opt_tip), (lme, lme_tip) = zip(table.data, table.tooltip_data)
    assert opt["first_notice_date"] == lme["first_notice_date"] == "\u2014"
    assert opt_tip["first_notice_date"]["value"] == "First notice is not applicable to an option."
    assert lme_tip["first_notice_date"]["value"].startswith("First notice is not applicable to an LME prompt")
    assert lme["last_trade_date"] == "\u2014" and "not applicable to an LME prompt" in lme_tip["last_trade_date"]["value"]
    assert opt["last_trade_date"] == "2026-11-17"     # an option's expiry applies: shown as a date
    for rec in (opt, lme):
        assert "missing" not in str(rec).lower() and "none on file" not in rec.values()


def test_ticket_is_labelled_as_the_tickets_prompt():
    rec = _table(expiries.body(_result([_lme_row()]))).data[0]
    assert rec["dates_source"] == "Ticket's prompt"
    assert expiries.SOURCE_LABELS["TICKET"] == "Ticket's prompt"
    assert "(est.)" not in rec["next_event_date"]


def test_an_lme_row_whose_lots_could_not_be_counted_reads_na_with_its_reason():
    why = "+30 t held, but lots not counted: no lot size for LME:XX."
    rec, tip = (lambda t: (t.data[0], t.tooltip_data[0]))(
        _table(expiries.body(_result([_lme_row(lots=None, tonnes=30.0, reason=why)]))))
    assert rec["lots"] == "n/a"
    assert "30 tonnes" in tip["lots"]["value"] and why in tip["lots"]["value"]


def test_an_lme_entry_in_the_settled_section():
    entry = {"product": "LME_FWD", "contract_id": "LME:CA 2026-08-19", "root_id": "LME:CA", "name": "LME Copper",
             "lots": None, "tonnes": -25.0, "last_trade_date": "2026-08-19", "prompt_date": "2026-08-19",
             "dates_source": "TICKET", "estimated": False, "frozen_at": "2026-08-20T09:00:00+00:00",
             "reason": "Prompt 2026-08-19 passed with -25 t held; the ledger has frozen all 1 trade."}
    body = expiries.body(_result([_row()], settled=[entry]))
    table = _settled_table(body)
    assert next(c for c in table.columns if c["id"] == "last_trade_date")["name"] == "Last trade / prompt"
    rec, tip = table.data[0], table.tooltip_data[0]
    assert rec["contract"] == "LME:CA 2026-08-19" and rec["last_trade_date"] == "2026-08-19"
    assert rec["lots"] == "n/a" and tip["lots"]["value"] == "-25 tonnes"
    assert rec["frozen_at"] == "2026-08-20T09:00:00+00:00"
    assert "Expired and settled (1)" in _text(body)
    assert _counts(body) == {"EXPIRED": "0", "RED": "0", "AMBER": "0", "GREEN": "1"}


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
