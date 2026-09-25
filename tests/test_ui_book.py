"""Tests for ui/tabs/book.py: the Book tab renders other lanes' output and computes no figure of
its own. Most tests run on the golden sample book (`tests/golden_book.py::build_book`, as of
2026-09-18) written to a file in tmp, read through a read-only connection as the app does; the
rest feed the body builders a synthetic `gather` result.

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from dash import html  # noqa: E402

from data.ingest import schema  # noqa: E402
from engine.risk.research_spreads import sigma_move  # noqa: E402
from ui.tabs import book  # noqa: E402
from ui.tabs.formatting import MINUS  # noqa: E402

AS_OF = "2026-09-18"


# --------------------------------------------------------------------------- tree helpers
def _walk(node):
    yield node
    children = getattr(node, "children", None)
    if children is None:
        return
    for child in (children if isinstance(children, (list, tuple)) else [children]):
        yield from _walk(child)


def _text(node) -> str:
    return " ".join(n for n in _walk(node) if isinstance(n, str))


def _by_id(node, node_id):
    hits = [n for n in _walk(node) if getattr(n, "id", None) == node_id]
    assert len(hits) == 1, f"{len(hits)} x {node_id}"
    return hits[0]


def _has(node, node_id) -> bool:
    return any(getattr(n, "id", None) == node_id for n in _walk(node))


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def golden_path(tmp_path_factory):
    from tests.golden_book import build_book
    path = tmp_path_factory.mktemp("book") / "golden.db"
    conn = build_book(schema.connect(str(path)))
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    book._MEMO.clear()
    monkeypatch.setattr(book, "_open", _ro)
    yield
    book._MEMO.clear()


@pytest.fixture
def golden(golden_path):
    conn = _ro(golden_path)
    try:
        yield book.gather(conn, AS_OF)
    finally:
        conn.close()


# --------------------------------------------------------------------------- synthetic data
def _periods(daily, mtd, ltd):
    return {"daily": daily, "mtd": mtd, "ltd": ltd, "d5": None, "ytd": None}


def _position(pid, name, daily, mtd=1.0, ltd=2.0, *, move=1.5, unit="USD/bbl", research=("r.x", ""),
              reasons=None, excluded=None, direction="long", status="open"):
    return {"position_id": pid, "name": name, "direction": direction, "status": status, "spread_ids": [pid],
            "legs": [{"instrument_id": "CLZ26 Comdty"}, {"instrument_id": "CLF27 Comdty"}],
            "pnl_usd": _periods(daily, mtd, ltd),
            "pnl_excluded": excluded or {p: 0 for p in ("daily", "mtd", "ltd", "d5", "ytd")},
            "pnl_reasons": reasons or {p: "" for p in ("daily", "mtd", "ltd", "d5", "ytd")},
            "pnl_notes": {p: "" for p in ("daily", "mtd", "ltd", "d5", "ytd")},
            "level_change": move, "level_change_reason": "" if move is not None else "no mark for CLF27",
            "level_unit": unit, "level_prev": 1.0, "level_now": 2.5, "level_prev_date": "2026-09-17",
            "research_id": research[0], "research_instance": research[1],
            "research_reason": "" if research[0] else "no research id"}


def _outright(tid, root, daily, reason=""):
    return {"trade_id": tid, "instrument_id": "C Z26 Comdty", "root_id": root,
            "pnl_usd": _periods(daily, daily, daily),
            "pnl_reasons": {p: reason if daily is None else "" for p in ("daily", "mtd", "ltd")},
            "pnl_notes": {}}


def _data(**over):
    data = {"as_of": AS_OF,
            "spreads": {"positions": [_position("P1", "CL Z26/F27 calendar", -1234.5678),
                                      _position("P2", "Brent vs WTI", 987.654321)],
                        "outrights": [_outright("C1", "CBOT:ZC", 100.25), _outright("C2", "CBOT:ZC", None, "no FUTURE_PX"),
                                      _outright("G1", "COMEX:GC", 40.0)],
                        "review": [], "reasons": []},
            "spreads_error": "",
            "research": {"available": True, "reason": "", "stats": {}},
            "curve": {"by_sector": {"energy": {"net_usd": 1111.5, "gross_usd": 5000.0, "net_delta_usd": 999.25,
                                               "reason": "", "delta_reason": ""},
                                    "metals": {"net_usd": None, "gross_usd": None, "net_delta_usd": -222.0,
                                               "reason": "LME:CA: no FWD_OUTRIGHT", "delta_reason": ""}},
                      "reasons": [], "note": ""},
            "curve_error": "",
            "schedule": {"rows": [], "counts": {}}, "schedule_error": "",
            "needs": (0, []), "needs_error": "",
            "limits": [{"limit": "gross_lots", "level": "NOT_SET", "reason": "no limit set"}], "limits_error": "",
            "risk": None}
    data.update(over)
    return data


def _bars(fig) -> dict:
    """{row label: bar value} of the P&L chart (the label is the bar's hover title)."""
    trace = fig.data[0]
    return {cd[0]: x for cd, x in zip(trace.customdata, trace.x)}


# --------------------------------------------------------------------------- 1. P&L by spread
def test_chart_bars_equal_the_positions_daily_figures_on_the_sample(golden):
    positions = golden["spreads"]["positions"]
    rows = book.pnl_rows(golden["spreads"], golden["research"])
    bars = _bars(book.pnl_figure(rows))
    assert positions
    for p in positions:
        label = p["name"] + (" (short)" if p["direction"] == "short" else "")
        assert bars[label] == p["pnl_usd"]["daily"]          # the engine's float, as it is
    # the outright lines: each root's trades' Daily added up (the header's display rule)
    by_root: dict = {}
    for o in golden["spreads"]["outrights"]:
        by_root.setdefault(o["root_id"], []).append(o["pnl_usd"]["daily"])
    outright_bars = {k: v for k, v in bars.items() if k.endswith("(outright)")}
    assert len(outright_bars) == len(by_root)
    assert sorted(outright_bars.values()) == pytest.approx(sorted(sum(v) for v in by_root.values()))


def test_chart_and_table_put_winners_on_top(golden):
    rows = book.pnl_rows(golden["spreads"], golden["research"])
    dailies = [r["values"]["daily"] for r in rows if r["values"]["daily"] is not None]
    assert dailies == sorted(dailies, reverse=True)
    fig = book.pnl_figure(rows)
    assert list(fig.data[0].x) == list(reversed(dailies))     # plotly draws the first bar at the bottom
    colours = fig.data[0].marker.color
    assert all(c == (book.POS_COLOUR if x >= 0 else book.NEG_COLOUR) for c, x in zip(colours, fig.data[0].x))


def test_table_holds_the_engines_figures_and_moves(golden):
    body = book.body(golden)
    table = _by_id(body, book.PNL_TABLE_ID)
    by_label = {r["label"]: r for r in table.data}
    for p in golden["spreads"]["positions"]:
        rec = by_label[p["name"] + (" (short)" if p["direction"] == "short" else "")]
        for period in ("daily", "mtd", "ltd"):
            assert rec[period] == float(round(p["pnl_usd"][period]))     # display rounding only
        assert rec["move"] == p["level_change"] and rec["unit"] == p["level_unit"]
    # money in k / m, the full figure on hover
    assert table.columns[1]["format"]["specifier"].endswith("s")
    tips = table.tooltip_data[0]
    assert "USD " in tips["daily"]["value"]


def test_research_sigma_is_sigma_move_labelled_research():
    entry = {"spread_id": "r.x", "instance": "", "found": True, "reason": "", "dvol_20d": 0.5, "unit": "USD/bbl",
             "asof": "2026-09-18", "note": ""}
    data = _data(research={"available": True, "reason": "", "stats": {("r.x", ""): entry}})
    rows = book.pnl_rows(data["spreads"], data["research"])
    spread = next(r for r in rows if r["label"] == "Brent vs WTI")
    assert spread["sigma"] == sigma_move(1.5, entry, unit="USD/bbl")[0]
    body = book.body(data)
    table = _by_id(body, book.PNL_TABLE_ID)
    assert any("research" in c["name"] for c in table.columns)
    rec = next(r for r, t in zip(table.data, table.tooltip_data) if r["label"] == "Brent vs WTI")
    assert rec["sigma"] == spread["sigma"]
    movers = _by_id(body, book.MOVERS_ID)
    assert f"+{spread['sigma']:.1f}σ" in _text(movers)


def test_sigma_na_carries_the_research_reason(golden):
    rows = book.pnl_rows(golden["spreads"], golden["research"])
    spreads = [r for r in rows if r["kind"] == book.SPREAD]
    assert spreads and all(r["sigma"] is None and r["sigma_reason"] for r in spreads)
    table = _by_id(book.body(golden), book.PNL_TABLE_ID)
    for rec, tip in zip(table.data, table.tooltip_data):
        assert rec["sigma"] == book.NA and tip["sigma"]["value"].startswith("research:")


def test_a_missing_daily_is_na_with_its_reason_never_zero_and_not_charted():
    reasons = {p: "SPREAD-9: leg CLF27 has no FUTURE_PX mark" for p in ("daily", "mtd", "ltd", "d5", "ytd")}
    data = _data(spreads={"positions": [_position("P9", "Unpriced calendar", None, None, None, reasons=reasons)],
                          "outrights": [_outright("W1", "NYMEX:CL", 50.0)], "review": [], "reasons": []})
    body = book.body(data)
    rows = book.pnl_rows(data["spreads"], data["research"])
    assert "Unpriced calendar" not in _bars(book.pnl_figure(rows))
    chart_block = _by_id(body, book.PNL_ID)
    assert "n/a 1" in _text(chart_block)
    table = _by_id(body, book.PNL_TABLE_ID)
    rec, tip = next((r, t) for r, t in zip(table.data, table.tooltip_data) if r["label"] == "Unpriced calendar")
    assert rec["daily"] == book.NA and "no FUTURE_PX mark" in tip["daily"]["value"]
    assert "Unpriced calendar" in _text(_by_id(body, book.ISSUES_ID))


def test_an_outright_commodity_sums_its_known_trades_and_says_what_it_leaves_out():
    data = _data()
    corn = next(r for r in book.pnl_rows(data["spreads"], data["research"]) if r["key"] == "OUTRIGHT-CBOT:ZC")
    assert corn["values"]["daily"] == 100.25 and corn["excluded"]["daily"] == 1
    assert "C2: no FUTURE_PX" in corn["reasons"]["daily"]
    assert "excludes 1" in book._period_hover(corn, "daily")


def test_total_line_adds_the_known_figures_and_marks_the_exclusions():
    data = _data()
    rows = book.pnl_rows(data["spreads"], data["research"])
    rec, tip = book.total_record(rows)
    assert rec["daily"] == pytest.approx(-1234.5678 + 987.654321 + 100.25 + 40.0)
    assert rec["label"].startswith("Total (excl.") and "left out" in tip["daily"]["value"]
    assert "Blotter" in tip["label"]["value"]


def test_no_figure_is_recomputed_the_engines_numbers_are_shown_as_given():
    """Figures that do not add up (Daily far from LTD minus anything, a net above its gross)
    are shown exactly as the engines give them: nothing here re-derives one from another."""
    data = _data()
    data["spreads"]["positions"][0]["pnl_usd"] = _periods(-7.0, 123456789.0, 3.0)
    data["curve"]["by_sector"]["energy"].update(net_usd=9e9, gross_usd=1.0, net_delta_usd=-5.5)
    rows = book.pnl_rows(data["spreads"], data["research"])
    assert _bars(book.pnl_figure(rows))["CL Z26/F27 calendar"] == -7.0
    fig = book.sector_figure(book.sector_rows(data["curve"]))
    notional, delta = fig.data
    assert dict(zip(notional.y, notional.x))["Energy"] == 9e9
    assert dict(zip(delta.y, delta.x))["Energy"] == -5.5
    table = _by_id(book.body(data), book.PNL_TABLE_ID)
    rec = next(r for r in table.data if r["label"] == "CL Z26/F27 calendar")
    assert (rec["daily"], rec["mtd"], rec["ltd"]) == (-7.0, 123456789.0, 3.0)


# --------------------------------------------------------------------------- 2. sectors
def test_sector_bars_equal_curve_positions_on_the_sample(golden):
    by_sector = golden["curve"]["by_sector"]
    fig = book.sector_figure(book.sector_rows(golden["curve"]))
    notional, delta = fig.data
    got_n, got_d = dict(zip(notional.y, notional.x)), dict(zip(delta.y, delta.x))
    assert set(got_n) == {book._sector_label(s) for s in by_sector}
    for sector, s in by_sector.items():
        label = book._sector_label(sector)
        assert got_n[label] == s["net_usd"] and got_d[label] == s["net_delta_usd"]


def test_sector_totals_mark_the_sector_left_out_and_name_the_curve_tab():
    section = book.sector_section(_data())
    text = _text(section)
    assert "→ Curve" in text and "excl. 1" in text and "n/a 1" in text      # metals notional
    assert "+1.11k" in text                                                        # energy's 1,111.5 alone


# --------------------------------------------------------------------------- 3. alerts
def test_every_alert_names_its_source_on_the_sample(golden):
    items = book.alerts(golden)
    assert items
    for a in items:
        assert a["tab"] in {"Expiries", "Data", "Spreads", "Risk"} and a["source"]
    texts = " | ".join(a["text"] for a in items)
    counts = golden["schedule"]["counts"]
    assert sum(1 for a in items if a["chip"] in ("EXPIRED", "RED")) == counts["EXPIRED"] + counts["RED"]
    assert f"{counts['AMBER']} contracts near expiry" in texts
    needed, missing = golden["needs"]
    assert f"{len(missing)} of {needed} marks missing" in texts
    assert f"{len(golden['spreads']['review'])} spread groups for review" in texts
    assert "VaR …" in texts and "limits not set" in texts
    rendered = _text(book.alerts_list(golden))
    for tab in ("Expiries", "Data", "Spreads", "Risk"):
        assert f"→ {tab}" in rendered


def test_alerts_are_most_urgent_first(golden):
    severities = [a["severity"] for a in book.alerts(golden)]
    assert severities == sorted(severities)


def test_var_alert_pending_ready_over_target_and_na():
    pending = book.var_alert(_data(risk=None))
    assert pending["text"] == "VaR …" and pending["severity"] == book.INFO
    ready = book.var_alert(_data(risk={"book": {"var95_1d_usd": 33600.0, "vol_vs_target_pct": 6.7,
                                                "over_vol_target": False},
                                       "config": {"vol_target_usd": 4.5e6, "vol_target_placeholder": True}}))
    assert ready["text"].startswith("VaR $33.6k") and "6.7% of target (placeholder)" in ready["text"]
    assert ready["severity"] == book.INFO and ready["tab"] == "Risk"
    over = book.var_alert(_data(risk={"book": {"var95_1d_usd": 9e6, "vol_vs_target_pct": 140.0,
                                               "over_vol_target": True}, "config": {}}))
    assert over["severity"] == book.ACT and over["chip"] == "OVER"
    na = book.var_alert(_data(risk={"book": {"var95_1d_usd": None, "reasons": {"var95_1d_usd": "under 252 days"}},
                                     "config": {}}))
    assert na["text"] == "VaR n/a" and "under 252 days" in na["hover"]
    err = book.var_alert(_data(risk={"error": "the risk history could not be read"}))
    assert err["text"] == "VaR n/a" and "could not be read" in err["hover"]


def test_limits_quiet_while_unset_and_loud_when_breached():
    quiet = book.limit_alerts(_data())
    assert [a["text"] for a in quiet] == ["limits not set"] and quiet[0]["severity"] == book.INFO
    checks = [{"limit": "gross_lots", "scope": "book", "source": "desk", "unit": "lots", "value": 120.0,
               "limit_value": 100.0, "level": "BREACH", "reason": "120% of the limit", "basis": "sum of |lots|"},
              {"limit": "net_usd_sector", "scope": "energy", "source": "desk", "unit": "USD", "value": -850000.0,
               "limit_value": 1e6, "level": "WARN", "reason": "85% of the limit", "basis": ""},
              {"limit": "exchange_all_months", "scope": "NYMEX:CL", "source": "exchange", "unit": "lots",
               "value": 3.0, "limit_value": 1000.0, "level": "OK", "reason": "", "basis": ""}]
    loud = book.limit_alerts(_data(limits=checks))
    assert [a["chip"] for a in loud] == ["BREACH", "WARN"]
    assert loud[0]["severity"] == book.ACT and "120 of 100 lots" in loud[0]["text"]
    refused = book.limit_alerts(_data(limits=[{"limit": "config", "level": "N/A", "reason": "yaml refused"}]))
    assert refused[0]["text"] == "limits n/a" and "yaml refused" in refused[0]["hover"]


def test_expiry_alerts_one_line_per_red_and_one_for_amber():
    rows = [{"contract_id": "HGZ26 Comdty", "level": "RED", "next_event": "first notice",
             "next_event_date": "2026-09-22", "business_days": 2, "estimated": False, "reason": "physical"},
            *[{"contract_id": f"X{n} Comdty", "level": "AMBER", "next_event": "last trade",
               "next_event_date": "2026-09-30", "business_days": 8, "estimated": True} for n in range(5)],
            {"contract_id": "G Comdty", "level": "GREEN", "business_days": 40}]
    items = book.expiry_alerts(_data(schedule={"rows": rows}))
    assert [a["chip"] for a in items] == ["RED", "AMBER"]
    assert items[0]["text"] == "HGZ26 first notice 2026-09-22, 2 bd"
    assert items[1]["text"].startswith("5 contracts near expiry: X0 8 bd, X1 8 bd, X2 8 bd +2")


# --------------------------------------------------------------------------- 4. movers
def test_top_movers_by_absolute_daily(golden):
    rows = book.pnl_rows(golden["spreads"], golden["research"])
    _by_sigma, by_daily = book.movers(rows)
    dailies = [abs(r["values"]["daily"]) for r in by_daily]
    assert len(by_daily) == min(book.TOP_MOVERS, len(rows)) and dailies == sorted(dailies, reverse=True)
    text = _text(_by_id(book.body(golden), book.MOVERS_ID))
    assert by_daily[0]["label"][:20] in text and "no σ today" in text


# --------------------------------------------------------------------------- states
def test_empty_book_reads_in_plain_words(tmp_path):
    path = tmp_path / "empty.db"
    schema.connect(str(path)).close()
    body, pending = book.render(AS_OF, path)
    assert f"No commodity futures in the book on {AS_OF}" in _text(body)
    assert _has(body, book.ALERTS_ID) and pending == AS_OF


def test_no_date_no_database_and_a_failing_engine_read_in_plain_words(golden_path, monkeypatch):
    assert "No as-of date" in _text(book.render(None, golden_path)[0])

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(book, "_open", refuse)
    assert "Database not available" in _text(book.render(AS_OF, golden_path)[0])
    monkeypatch.setattr(book, "_open", _ro)

    def broken(conn, as_of):
        raise RuntimeError("template file unreadable")
    monkeypatch.setattr(book, "_spreads", broken)
    body, _ = book.render(AS_OF, golden_path)
    pnl = _text(_by_id(body, book.PNL_ID))
    assert "the spreads could not be built (RuntimeError: template file unreadable)" in pnl
    assert _has(body, book.SECTOR_CHART_ID) and _has(body, book.ALERTS_ID)       # the rest still shows
    monkeypatch.setattr(book, "body", lambda data: 1 / 0)
    failed, _ = book.render(AS_OF, golden_path)
    assert "The book could not be built for 2026-09-18 (ZeroDivisionError" in _text(failed)


def test_render_time_and_the_var_never_blocks(golden_path, monkeypatch):
    from ui.tabs import header

    def slow(*_a, **_k):
        raise AssertionError("the body must never compute the VaR")
    monkeypatch.setattr(header, "risk_summary", slow)
    start = time.perf_counter()
    body, pending = book.render(AS_OF, golden_path)
    cold = time.perf_counter() - start
    start = time.perf_counter()
    book.render(AS_OF, golden_path)
    warm = time.perf_counter() - start
    assert _has(body, book.PNL_CHART_ID) and pending == AS_OF
    assert cold < 10.0, f"cold render {cold:.2f}s"
    assert warm < 1.0, f"warm render {warm:.2f}s (book_spreads should be memoised)"


def test_render_alerts_fills_the_var_from_the_headers_reading(golden_path, monkeypatch):
    from ui.tabs import header
    monkeypatch.setattr(header, "risk_summary", lambda conn, as_of: {
        "book": {"var95_1d_usd": 33600.0, "vol_vs_target_pct": 6.7, "over_vol_target": False},
        "config": {"vol_target_usd": 4.5e6, "vol_target_placeholder": True}})
    out = book.render_alerts(AS_OF, golden_path)
    assert isinstance(out, html.Ul) and "VaR $33.6k" in _text(out)
    assert book.render_alerts(None, golden_path) is dash.no_update


# --------------------------------------------------------------------------- shell
def test_layout_follows_the_header_and_all_ids_are_prefixed(golden):
    layout = book.layout(AS_OF)
    ids = [getattr(n, "id", None) for n in _walk(layout) if getattr(n, "id", None)]
    assert {book.BODY_ID, book.REFRESH_ID, book.VAR_PENDING_ID} <= set(ids)
    assert not any(type(n).__name__ == "DatePickerSingle" for n in _walk(layout))
    assert book.build_layout is book.layout
    body_ids = [getattr(n, "id", None) for n in _walk(book.body(golden)) if getattr(n, "id", None)]
    assert all(i.startswith("book-") for i in ids + body_ids), ids + body_ids
    assert MINUS in book.signed_money(-1500.0)


def test_register_callbacks_registers_the_body_and_the_chained_var(golden_path):
    from ui.revision import DATA_REVISION_ID
    from ui.tabs.header import AS_OF_STORE_ID
    app = dash.Dash(__name__)
    book.register_callbacks(app, get_db_path=lambda: str(golden_path))
    body_key = next(k for k in app.callback_map if book.BODY_ID in k)
    inputs = {(i["id"], i["property"]) for i in app.callback_map[body_key]["inputs"]}
    assert inputs == {(AS_OF_STORE_ID, "data"), (DATA_REVISION_ID, "data"), (book.REFRESH_ID, "n_intervals")}
    assert book.VAR_PENDING_ID in body_key
    var_key = next(k for k in app.callback_map if book.ALERTS_ID in k)
    assert {(i["id"], i["property"]) for i in app.callback_map[var_key]["inputs"]} == {(book.VAR_PENDING_ID, "data")}
    children, pending = app.callback_map[body_key]["callback"].__wrapped__(AS_OF, "rev", 0)
    assert _has(children, book.PNL_TABLE_ID) and pending in (AS_OF, None)
