"""Tests for ui/tabs/risk.py: the Risk tab renders `engine.risk.book_risk`'s output and
recomputes nothing. Most tests feed the body builders a synthetic result; one goes
end to end through the real engine on a tiny book and synthetic parquet history in
tmp_path (never the sibling nm-dashboard folder).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import math
import sys
import types

import numpy as np
import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from dash import dash_table  # noqa: E402

from data.ingest import schema  # noqa: E402
from engine.risk import history as history_mod  # noqa: E402
from ui.tabs import risk  # noqa: E402

NAN = float("nan")
AS_OF = "2026-09-22"
SNB, WORST_EX = "2015-01-15", "2020-03-16"
VOL_TARGET, CAP = 4_500_000.0, 2_250_000.0


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


def _texts(node) -> list:
    return [n for n in _walk(node) if isinstance(n, str)]


def _text(node) -> str:
    return " ".join(_texts(node))


def _titles(node) -> list:
    return [t for t in (getattr(n, "title", None) for n in _walk(node)) if t]


def _ids(node) -> list:
    return [i for i in (getattr(n, "id", None) for n in _walk(node)) if isinstance(i, str)]


def _table(node, table_id: str) -> dash_table.DataTable:
    found = [n for n in _walk(node) if isinstance(n, dash_table.DataTable) and getattr(n, "id", None) == table_id]
    assert len(found) == 1, f"{table_id}: {len(found)} tables"
    return found[0]


def _cards(body) -> dict:
    """label -> (value text, note text or '', card title, value span)."""
    block = next(n for n in _walk(body) if getattr(n, "id", None) == risk.CARDS_ID)
    out = {}
    for card in block.children:
        label, value = card.children[0].children, card.children[1]
        note = card.children[2].children if len(card.children) > 2 else ""
        out[label] = (value.children, note, card.title, value)
    return out


def _metrics(**over) -> dict:
    base = {"vol_blended_ann_usd": 1_234_567.8, "vol_trailing_ann_usd": 1_100_000.0, "vol_crisis_ann_usd": 1_503_703.4,
            "var95_1d_usd": 25_000.0, "worst_1d_raw_usd": -187_500.0, "worst_1d_raw_date": SNB,
            "worst_1d_ex_shocks_usd": -37_500.0, "worst_1d_ex_shocks_date": WORST_EX,
            "days": 5000, "first_date": "2007-01-02", "last_date": AS_OF, "reasons": {}}
    base.update(over)
    return base


def _nan_metrics(reason: str) -> dict:
    return _metrics(vol_blended_ann_usd=NAN, vol_trailing_ann_usd=NAN, vol_crisis_ann_usd=NAN, var95_1d_usd=NAN,
                    worst_1d_raw_usd=NAN, worst_1d_raw_date=None, worst_1d_ex_shocks_usd=NAN,
                    worst_1d_ex_shocks_date=None, worst_day_ex_vs_target_pct=NAN, days=0, first_date=None,
                    last_date=None, reasons={"all": reason})


def _row(underlyer, kind, net, **over) -> dict:
    is_rates = kind == "RATES"
    row = {"underlyer": underlyer, "kind": kind, "series": underlyer, "carry": kind == "FX",
           "net_usd": NAN if is_rates else net, "gross_usd": NAN if is_rates else abs(net),
           "dv01_usd": net if is_rates else NAN, "reason": "", "note": "", "worst_day_ex_vs_target_pct": 0.8333}
    row.update(_metrics())
    row.update(over)
    return row


def _result(**over) -> dict:
    """A `book_risk`-shaped result: CHF (full metrics), SEK (no history), SPX, USD rates."""
    config = {"vol_target_usd": VOL_TARGET, "stress_pct": 50.0, "stress_cap_usd": CAP,
              "blended": {"trail_window_bd": 500, "w_trail": 2 / 3, "w_stress": 1 / 3, "stress_start": "2008-01-01",
                          "stress_end": "2010-12-31", "cutover": "2011-01-01"},
              "var_window_bd": 252, "var_confidence": 0.95, "worst_day_start": "2008-01-01",
              "shock_dates": [{"date": SNB, "name": "SNB floor removal"}, {"date": "2016-06-24", "name": "Brexit referendum result"}],
              "file": "/repo/config/risk.yaml", "loaded": True, "note": ""}
    history = {"available": True, "path": "/hist/bbg_data", "last_date": AS_OF, "used_to": AS_OF,
               "lag2_date": "2026-09-18", "reason": "", "note": "", "carry": True, "candidates": [],
               "files": {"spot": {"file": "bbg_raw_fx_marks.parquet", "path": "/hist/bbg_data/bbg_raw_fx_marks.parquet",
                                  "loaded": True, "rows": 5000, "columns": ["CHF"], "first_date": "2007-01-01",
                                  "last_date": AS_OF, "reason": ""},
                         "yields": {"file": "bbg_raw_fx_yields.parquet", "path": "/hist/y", "loaded": False, "rows": 0,
                                    "columns": [], "first_date": None, "last_date": None, "reason": "/hist/y not found"}}}
    rows = [_row("CHF", "FX", 1_250_000.0),
            _row("SEK", "FX", 300_000.0, carry=False, reason="no history for SEK", **_nan_metrics("no history for SEK")),
            _row("SPX", "EQUITY_INDEX", 700_000.0, carry=True, note="ES futures + SPX options as one line"),
            _row("USD rates", "RATES", 4_200.0, carry=False, note="the 10Y par swap rate stands in")]
    book = {"net_usd": 2_250_000.0, "gross_usd": 2_250_000.0, "dv01_usd": 4_200.0, "fx_net_usd": -1_550_000.0,
            "fx_gross_usd": 1_550_000.0, "rows_in_series": ["CHF", "SPX", "USD rates"], "reason": "",
            "vol_vs_target_pct": 27.4, "worst_day_ex_vs_cap_pct": 1.7, "over_cap": False, "over_vol_target": False,
            "var_window": {"dates": [AS_OF], "pnl_usd": [1.0]}}
    book.update(_metrics())
    scenarios = {
        "USD +2% all": {"total": -68_000.0, "fx_pnl": {"CHF": -25_000.0, "SEK": -6_000.0, "JPY": 0.0}, "fx_total": -31_000.0,
                        "futures_pnl": -28_000.0, "equity_pct": -0.04},
        "Europe -3%": {"total": -46_500.0, "fx_pnl": {"CHF": -37_500.0, "SEK": -9_000.0}, "fx_total": -46_500.0,
                       "futures_pnl": 0.0, "equity_pct": None},
        "Equities -10%": {"total": NAN, "fx_pnl": {}, "fx_total": 0.0, "futures_pnl": NAN, "equity_pct": -0.10,
                          "reason": "no USD delta for SPX (no ES price on 2026-09-22)"},
    }
    result = {"as_of": AS_OF, "history": history, "config": config, "book": book, "underlyers": rows,
              "scenarios": scenarios, "missing": ["NOK: not in the scenarios (no rate for NOK)"]}
    result.update(over)
    return result


# --------------------------------------------------------------------------- shell
def test_layout_is_a_risk_prefixed_shell_with_no_date_picker():
    layout = risk.layout(AS_OF)
    ids = _ids(layout)
    assert risk.BODY_ID in ids and risk.REFRESH_ID in ids
    assert all(i.startswith("risk-") for i in ids), ids
    assert not any(type(n).__name__ == "DatePickerSingle" for n in _walk(layout))
    assert "Risk" in _text(layout) and AS_OF in _text(layout)
    assert risk.build_layout is risk.layout


def test_register_callbacks_listens_to_the_header_as_of_and_the_data_revision():
    from ui.revision import DATA_REVISION_ID
    from ui.tabs.header import AS_OF_STORE_ID
    app = dash.Dash(__name__)
    risk.register_callbacks(app, get_db_path=lambda: ":memory:")
    keys = [k for k in app.callback_map if risk.BODY_ID in k]
    assert len(keys) == 1
    inputs = {(i["id"], i["property"]) for i in app.callback_map[keys[0]]["inputs"]}
    assert (AS_OF_STORE_ID, "data") in inputs and (DATA_REVISION_ID, "data") in inputs and (risk.REFRESH_ID, "n_intervals") in inputs


def test_render_says_why_when_there_is_no_date_or_no_database(tmp_path, monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    assert "No as-of date" in risk.render(None, tmp_path / "risk.db").children
    import sqlite3

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    stub.connect_readonly = refuse
    assert "Database not available" in risk.render(AS_OF, tmp_path / "missing.db").children


def test_render_shows_an_engine_failure_instead_of_a_blank_tab(tmp_path, monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)

    def boom(conn, as_of):
        raise ValueError("bad history column")
    monkeypatch.setattr(risk, "book_risk", boom)
    out = risk.render(AS_OF, tmp_path / "risk.db")
    assert "Risk could not be computed for 2026-09-22 (ValueError: bad history column)" in _text(out)


# --------------------------------------------------------------------------- caption
def test_caption_names_the_history_the_lag2_date_the_parameters_and_the_missing_list():
    body = risk.body(_result())
    lines = risk.caption_lines(_result())
    assert lines[0] == "As of Tuesday 22 September 2026 (2026-09-22)."
    assert lines[1] == "History: /hist/bbg_data, last close 2026-09-22; used to 2026-09-22, lag-2 date 2026-09-18."
    assert lines[2] == ("Parameters: vol target 4,500,000; stress cap 2,250,000 (50% of the target); blended vol 2/3 trailing "
                        "(500 bd) + 1/3 crisis (2008-01-01 to 2010-12-31); VaR window 252 bd at 95%.")
    assert len(lines) == 3
    assert "NOK: not in the scenarios (no rate for NOK)" in _text(body) and "Not included:" in _text(body)
    # the config and history notes, when there are any
    r = _result()
    r["config"]["note"] = "/repo/config/risk.yaml not found: defaults in use"
    r["history"]["note"] = "carry not included: /hist/y not found"
    lines = risk.caption_lines(r)
    assert lines[3] == "Config: /repo/config/risk.yaml not found: defaults in use."
    assert lines[4] == "History: carry not included: /hist/y not found."


# --------------------------------------------------------------------------- cards
def test_every_card_shows_its_formatted_figure_with_the_definition_on_hover():
    cards = _cards(risk.body(_result()))
    assert list(cards) == ["Net USD delta", "Gross USD delta", "DV01 (USD/bp)", "Blended vol (annual)", "1y 95% VaR (1-day)",
                           "Worst day ex shocks", "Worst day raw"]
    assert cards["Net USD delta"][0] == "2,250,000"
    assert "header's FX net USD (+ = long USD): (1,550,000)" in cards["Net USD delta"][1]
    assert cards["Gross USD delta"][0] == "2,250,000" and "header's FX gross: 1,550,000" in cards["Gross USD delta"][1]
    assert cards["DV01 (USD/bp)"][0] == "4,200"
    assert cards["Blended vol (annual)"][0] == "1,234,568" and cards["Blended vol (annual)"][1] == "27.4% of the 4,500,000 target"
    assert cards["1y 95% VaR (1-day)"][0] == "25,000" and "positive = loss" in cards["1y 95% VaR (1-day)"][1]
    assert cards["Worst day ex shocks"][0] == "(37,500)"
    assert cards["Worst day ex shocks"][1] == f"{WORST_EX}; 1.7% of the 2,250,000 cap"
    assert cards["Worst day raw"][0] == "(187,500)" and cards["Worst day raw"][1] == f"{SNB}; nothing excluded"
    # the definitions, with the config's numbers in them, are the cards' hovers
    assert "Blended annual vol = 2/3 x trailing 500-day vol + 1/3 x crisis vol (2008-01-01 to 2010-12-31)" in cards["Blended vol (annual)"][2]
    assert "lag-2 date (2026-09-18)" in cards["Blended vol (annual)"][2]
    assert "minus the 5th percentile of the last 252 daily" in cards["1y 95% VaR (1-day)"][2]
    assert "SNB floor removal 2015-01-15, Brexit referendum result 2016-06-24" in cards["Worst day ex shocks"][2]
    assert "Cap = 50% of the vol target = 2,250,000" in cards["Worst day ex shocks"][2]
    assert "nothing excluded" in cards["Worst day raw"][2]
    # a loss is in the negative colour, a plain figure is not, and no flag tag is shown
    assert cards["Worst day raw"][3].style == {"color": "var(--neg)"}
    assert cards["Blended vol (annual)"][3].style == {}
    assert "over cap" not in _texts(risk.body(_result())) and "over vol target" not in _texts(risk.body(_result()))


def test_over_cap_and_over_vol_target_are_flagged_in_red():
    r = _result()
    r["book"].update(over_cap=True, over_vol_target=True, worst_day_ex_vs_cap_pct=131.6, vol_vs_target_pct=142.0,
                     worst_1d_ex_shocks_usd=-2_960_000.0, vol_blended_ann_usd=6_390_000.0)
    body = risk.body(r)
    cards = _cards(body)
    assert "over cap" in _texts(body) and "over vol target" in _texts(body)
    assert cards["Worst day ex shocks"][0] == "(2,960,000)" and "131.6% of the 2,250,000 cap" in cards["Worst day ex shocks"][1]
    assert cards["Worst day ex shocks"][3].style["color"] == "var(--neg)" and cards["Worst day ex shocks"][3].style["fontWeight"] == "700"
    assert cards["Blended vol (annual)"][0] == "6,390,000" and "142.0% of the 4,500,000 target" in cards["Blended vol (annual)"][1]
    assert cards["Blended vol (annual)"][3].style["color"] == "var(--neg)"


def test_a_nan_figure_reads_na_with_its_reason_never_zero():
    r = _result()
    why = "needs 500 daily observations to 2026-09-18: 297 on file"
    r["book"].update(vol_blended_ann_usd=NAN, vol_trailing_ann_usd=NAN, vol_crisis_ann_usd=NAN, vol_vs_target_pct=NAN,
                     reasons={"vol_blended_ann_usd": why}, dv01_usd=NAN)
    r["missing"] = ["rates: IRSOIS-USD-1: no DV01_USD on 2026-09-22"]
    cards = _cards(risk.body(r))
    value, note, _, span = cards["Blended vol (annual)"]
    assert value == "n/a" and note == why and span.title == why and "card-value--muted" in span.className
    assert cards["DV01 (USD/bp)"][0] == "n/a" and cards["DV01 (USD/bp)"][1] == "rates: IRSOIS-USD-1: no DV01_USD on 2026-09-22"
    assert "0" not in (cards["Blended vol (annual)"][0], cards["DV01 (USD/bp)"][0])
    # the table: the SEK row has no history, so every metric cell says n/a with the reason on hover
    table = _table(risk.body(r), risk.TABLE_ID)
    sek = next(i for i, rec in enumerate(table.data) if rec["underlyer"] == "SEK")
    rec, tip = table.data[sek], table.tooltip_data[sek]
    assert rec["net_usd"] == 300_000.0 and rec["gross_usd"] == 300_000.0
    for col in ("vol_blended_ann_usd", "var95_1d_usd", "worst_1d_ex_shocks_usd", "worst_1d_ex_shocks_date",
                "worst_1d_raw_usd", "worst_1d_raw_date", "worst_day_ex_vs_target_pct"):
        assert rec[col] == "n/a", col
        assert tip[col] == {"value": "no history for SEK", "type": "text"}, col
    assert rec["note"].startswith("no history for SEK")
    # a position with no rate: n/a in the delta cells too, with the positions' reason
    r = _result()
    r["underlyers"][0].update(net_usd=NAN, gross_usd=NAN, reason="no rate for CHF", **_nan_metrics("no rate for CHF"))
    table = _table(risk.body(r), risk.TABLE_ID)
    assert table.data[0]["net_usd"] == "n/a" and table.tooltip_data[0]["net_usd"]["value"] == "no rate for CHF"
    # a row the engine flags `carry` but could not size (the SPX line without a price) does not claim carry
    r = _result()
    r["underlyers"][2].update(net_usd=NAN, gross_usd=NAN, reason="ESZ6 Index: no FUTURE_PX on 2026-09-22", carry=True,
                              **_nan_metrics("ESZ6 Index: no FUTURE_PX on 2026-09-22"))
    spx = _table(risk.body(r), risk.TABLE_ID).data[2]
    assert spx["net_usd"] == "n/a" and spx["note"] == "ESZ6 Index: no FUTURE_PX on 2026-09-22; ES futures + SPX options as one line"
    # many reasons behind one card: the first with a count as the note, the whole list on hover
    r = _result()
    r["book"]["dv01_usd"] = NAN
    r["missing"] = [f"rates: IRSOIS-USD-{i}: no DV01_USD on 2026-09-22" for i in range(1, 19)]
    body = risk.body(r)
    value, note, _, span = _cards(body)["DV01 (USD/bp)"]
    assert value == "n/a" and note == "rates: IRSOIS-USD-1: no DV01_USD on 2026-09-22 (+17 more on hover)"
    assert span.title.count("no DV01_USD") == 18
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["dv01_usd"] == "n/a" and footer.tooltip_data[0]["dv01_usd"]["value"].count("no DV01_USD") == 18
    # no swaps at all: the Book's DV01 says so
    r = _result(missing=[])
    r["book"]["dv01_usd"] = NAN
    body = risk.body(r)
    assert _cards(body)["DV01 (USD/bp)"][1] == "no open swap with a DV01 mark"
    assert _table(body, f"{risk.TABLE_ID}-footer").tooltip_data[0]["dv01_usd"]["value"] == "no open swap with a DV01 mark"


def test_history_unavailable_shows_the_reason_and_every_metric_na():
    r = _result()
    reason = "no market history folder: tried /a/nm-dashboard/bbg_data, /a/bbg_data/bbg_data"
    r["history"] = {"available": False, "path": "/a/nm-dashboard/bbg_data", "last_date": None, "used_to": None,
                    "lag2_date": None, "reason": reason, "note": "", "carry": False, "candidates": [], "files": {}}
    r["book"].update(_nan_metrics("no market history: " + reason), reason="no market history: " + reason,
                     vol_vs_target_pct=NAN, worst_day_ex_vs_cap_pct=NAN, over_cap=False, over_vol_target=False,
                     rows_in_series=[])
    for row in r["underlyers"]:
        row.update(_nan_metrics("no market history: " + reason), reason="no market history: " + reason, carry=False)
    body = risk.body(r)
    text = _text(body)
    assert f"Market history unavailable: {reason}. Every risk figure below is n/a" in text
    cards = _cards(body)
    for label in ("Blended vol (annual)", "1y 95% VaR (1-day)", "Worst day ex shocks", "Worst day raw"):
        assert cards[label][0] == "n/a" and reason in cards[label][1], label
    assert cards["Net USD delta"][0] == "2,250,000"          # the positions stand
    table = _table(body, risk.TABLE_ID)
    assert all(rec["vol_blended_ann_usd"] == "n/a" for rec in table.data)
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["worst_1d_raw_usd"] == "n/a" and footer.data[0]["net_usd"] == 2_250_000.0
    # the definitions still read, without a lag-2 date to name
    assert "the history's last date less 2 business days" in _text(body)


# --------------------------------------------------------------------------- the key table
def test_table_rows_follow_the_engines_order_and_the_book_is_pinned_under_them():
    body = risk.body(_result())
    table = _table(body, risk.TABLE_ID)
    assert [rec["underlyer"] for rec in table.data] == ["CHF", "SEK", "SPX", "USD rates"]
    assert [rec["kind"] for rec in table.data] == ["FX", "FX", "Equity index", "Rates"]
    assert [c["id"] for c in table.columns] == [
        "underlyer", "kind", "net_usd", "gross_usd", "dv01_usd", "vol_blended_ann_usd", "vol_trailing_ann_usd",
        "vol_crisis_ann_usd", "var95_1d_usd", "worst_1d_ex_shocks_usd", "worst_1d_ex_shocks_date", "worst_1d_raw_usd",
        "worst_1d_raw_date", "worst_day_ex_vs_target_pct", "note"]
    assert [c["name"] for c in table.columns][8:11] == ["VaR95 1d", "Worst ex shocks", "Date"]
    chf = table.data[0]
    assert chf["net_usd"] == 1_250_000.0 and isinstance(chf["net_usd"], float)      # a number, so it ranks
    assert chf["dv01_usd"] is None                                                   # does not apply: blank
    assert chf["vol_blended_ann_usd"] == 1_234_567.8 and chf["worst_1d_raw_usd"] == -187_500.0
    assert chf["worst_1d_raw_date"] == SNB and chf["worst_1d_ex_shocks_date"] == WORST_EX
    assert chf["worst_day_ex_vs_target_pct"] == 0.8333 and chf["note"] == "carry included"
    rates = table.data[3]
    assert rates["net_usd"] is None and rates["gross_usd"] is None and rates["dv01_usd"] == 4_200.0
    assert rates["note"] == "the 10Y par swap rate stands in"
    assert table.sort_action == "native" and table.persistence is True
    # the Book: a header-less second table under the first, same columns, never ranked with the rows
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.sort_action == "none" and footer.style_header == {"display": "none"}
    assert [c["id"] for c in footer.columns] == [c["id"] for c in table.columns]
    book = footer.data[0]
    assert (book["underlyer"], book["kind"]) == ("Book", "Book")
    assert book["net_usd"] == 2_250_000.0 and book["gross_usd"] == 2_250_000.0 and book["dv01_usd"] == 4_200.0
    assert book["worst_1d_raw_usd"] == -187_500.0 and book["worst_1d_raw_date"] == SNB
    assert book["worst_day_ex_vs_target_pct"] is None
    assert footer.tooltip_data[0]["worst_day_ex_vs_target_pct"]["value"] == \
        "the Book is measured against the stress cap: 1.7% of the cap (the card above)"
    assert book["note"] == "3 row(s)' daily P&L summed date by date, correlation embedded: CHF, SPX, USD rates"
    # losses colour red, deltas by sign; the definitions are the header hovers
    styles = table.style_data_conditional
    assert {"if": {"column_id": "worst_1d_raw_usd", "filter_query": "{worst_1d_raw_usd} < 0"}, "color": "var(--neg)"} in styles
    assert {"if": {"column_id": "net_usd", "filter_query": "{net_usd} > 0"}, "color": "var(--pos)", "fontWeight": "700"} in styles
    assert "minus the 5th percentile" in table.tooltip_header["var95_1d_usd"]
    assert "Blended annual vol" in table.tooltip_header["vol_blended_ann_usd"]


# --------------------------------------------------------------------------- scenarios
def test_scenario_table_equity_cell_is_blank_or_na_or_a_number_as_the_engine_says():
    body = risk.body(_result())
    table = _table(body, risk.SCENARIO_TABLE_ID)
    assert [rec["scenario"] for rec in table.data] == ["USD +2% all", "Europe -3%", "Equities -10%"]
    usd2, europe, equities = table.data
    assert usd2["total"] == -68_000.0 and usd2["fx_total"] == -31_000.0
    assert usd2["equity_pct"] == -0.04 and usd2["equity_pnl"] == -28_000.0           # a number
    assert europe["equity_pct"] is None and europe["equity_pnl"] is None              # blank: no EQUITY move
    assert europe["total"] == -46_500.0
    assert equities["equity_pnl"] == "n/a" and equities["total"] == "n/a"             # n/a with the reason
    assert equities["equity_pct"] == -0.10
    tip = table.tooltip_data[2]
    assert tip["equity_pnl"]["value"] == "no USD delta for SPX (no ES price on 2026-09-22)"
    assert tip["total"]["value"] == "no USD delta for SPX (no ES price on 2026-09-22)"
    assert table.tooltip_data[1] == {}
    pct = next(c for c in table.columns if c["id"] == "equity_pct")["format"]
    assert pct["specifier"].endswith("%")                                             # -0.04 prints as -4.0%
    assert "config/stress.yaml" in _text(body) and "the same scenarios as the Ladder's" in _text(body)
    # the matrix: underlyer order first, then the rest alphabetically; a currency a scenario does not move is blank
    matrix = _table(body, risk.MATRIX_TABLE_ID)
    assert [c["id"] for c in matrix.columns] == ["ccy", "USD +2% all", "Europe -3%", "Equities -10%"]
    assert [rec["ccy"] for rec in matrix.data] == ["CHF", "SEK", "JPY"]
    chf, sek, jpy = matrix.data
    assert chf["USD +2% all"] == -25_000.0 and chf["Europe -3%"] == -37_500.0 and chf["Equities -10%"] is None
    assert jpy["USD +2% all"] == 0.0 and jpy["Europe -3%"] is None
    footer = _table(body, f"{risk.MATRIX_TABLE_ID}-footer")
    assert footer.data == [{"ccy": "FX total", "USD +2% all": -31_000.0, "Europe -3%": -46_500.0, "Equities -10%": 0.0}]
    # the matrix is collapsed by default
    details = next(n for n in _walk(body) if type(n).__name__ == "Details" and "matrix" in _text(n).lower())
    assert details.open is False
    # no scenarios on file: said, not a crash
    r = _result(scenarios={})
    assert "No scenarios on file" in _text(risk.scenario_section(r))


# --------------------------------------------------------------------------- definitions
def test_definitions_block_states_the_formulas_and_the_config_values():
    body = risk.body(_result())
    block = next(n for n in _walk(body) if getattr(n, "id", None) == risk.DEFINITIONS_ID)
    assert block.open is False and type(block).__name__ == "Details"
    text = _text(block)
    for needle in ("Definitions", "USD delta x its daily move", "DV01 x the change of the currency's 10Y par swap rate",
                   "Blended annual vol = 2/3 x trailing 500-day vol + 1/3 x crisis vol (2008-01-01 to 2010-12-31)",
                   "minus the 5th percentile of the last 252 daily", "positive = loss",
                   "SNB floor removal 2015-01-15, Brexit referendum result 2016-06-24", "set to 0",
                   "Cap = 50% of the vol target = 2,250,000", "nothing excluded",
                   "vol target 4,500,000; stress cap 2,250,000 (50%); trailing window 500 bd, weights 2/3 / 1/3",
                   "crisis window 2008-01-01 to 2010-12-31, cutover 2011-01-01; VaR window 252 bd at 95%",
                   "worst day from 2008-01-01", "Read from /repo/config/risk.yaml (loaded)",
                   "spot: bbg_raw_fx_marks.parquet (2007-01-01 to 2026-09-22, 5000 rows)",
                   "yields: bbg_raw_fx_yields.parquet (not loaded (/hist/y not found))",
                   "config/stress.yaml"):
        assert needle in text, needle
    r = _result()
    r["config"].update(loaded=False, note="/repo/config/risk.yaml not found: defaults in use")
    assert "(not loaded: /repo/config/risk.yaml not found: defaults in use)" in _text(risk.definitions_block(r))


# --------------------------------------------------------------------------- end to end
DATES = pd.bdate_range("2007-01-01", AS_OF)
SNAP = f"{AS_OF}T15:00:00-04:00"


def _write_history(folder):
    """CHF with the SNB day and a 2020 bad day, JPY flat, yields flat, USD 10Y with one step."""
    folder.mkdir(parents=True, exist_ok=True)
    r = pd.Series(0.0005, index=DATES)
    r.loc[pd.Timestamp(SNB)] = -0.15
    r.loc[pd.Timestamp(WORST_EX)] = -0.03
    spot = pd.DataFrame(index=DATES)
    spot.index.name = "date"
    spot["CHF"] = np.exp(np.cumsum(r.to_numpy()))
    spot["JPY"] = 0.0067
    spot.to_parquet(folder / history_mod.SPOT_FILE)
    y = pd.DataFrame({"USD": 3.0, "CHF": 3.0, "JPY": 3.0}, index=DATES)
    y.index.name = "date"
    y.to_parquet(folder / history_mod.YIELDS_FILE)
    level = pd.Series(4.0, index=DATES)
    level.loc[pd.Timestamp("2008-10-15"):] -= 0.30
    rt = pd.DataFrame({"USD_SWAP10Y": level}, index=DATES)
    rt.index.name = "date"
    rt.to_parquet(folder / history_mod.RATES_FILE)
    return folder


def _write_book(db_path):
    conn = schema.connect(str(db_path))
    conn.executemany("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)", [
        ("USDCHF", "FX", "USD", "CHF", 1, 0, "USDCHF Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31"),
        ("IRSOIS-USD-1", "IRS", "USD", "USD", 1, 0, "", "2031-09-01")])
    for tid, pair, (bccy, bamt), (qccy, qamt), px in [
            ("c1", "USDCHF", ("USD", -1_000_000.0), ("CHF", 800_000.0), 0.80),       # long 0.8m CHF at USDCHF 0.64 = $1.25m
            ("j1", "USDJPY", ("USD", 1_000_000.0), ("JPY", -150_000_000.0), 150.0)]:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", pair, "FX_FWD", tid, "2026-09-01", bamt, px, "acc", "cp", "", "t", "d", ""))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
            (tid, 1, "FX_NEAR", bccy, bamt, "2026-09-01", "2026-10-20", px, 1),
            (tid, 2, "FX_NEAR", qccy, qamt, "2026-09-01", "2026-10-20", px, 1)])
    conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("r1", "XLSX", "IRSOIS-USD-1", "IRS", "r1", "2026-09-01", 1e7, 0.035, "acc", "cp", "", "t", "d", ""))
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("r1", 1, "FIXED", "USD", -1e7, "2026-09-01", "2031-09-01", 0.035, 1),
        ("r1", 2, "FLOAT", "USD", 1e7, "2026-09-01", "2031-09-01", 0.0, 1)])
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (AS_OF, "USDCHF", AS_OF, "SPOT", 0.64, "BBG_BFXFORWARD", SNAP),
        (AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD", SNAP),
        (AS_OF, "IRSOIS-USD-1", "2031-09-01", "DV01_USD", 4200.0, "QL_PRICER", SNAP)])
    conn.commit()
    conn.close()


def test_the_rendered_tab_shows_the_engines_numbers_end_to_end(tmp_path, monkeypatch):
    from engine.risk import book_risk
    from ui.tabs.formatting import format_cell
    monkeypatch.setenv(history_mod.ENV_VAR, str(_write_history(tmp_path / "hist")))
    db_path = tmp_path / "risk.db"
    _write_book(db_path)
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)

    app = dash.Dash(__name__)
    risk.register_callbacks(app, get_db_path=lambda: str(db_path))
    callback = next(v for k, v in app.callback_map.items() if risk.BODY_ID in k)["callback"].__wrapped__
    body = callback(AS_OF, "rev", 0)

    conn = schema.connect(str(db_path))
    engine = book_risk(conn, AS_OF)
    conn.close()
    rows = {r["underlyer"]: r for r in engine["underlyers"]}
    assert engine["history"]["available"] and engine["history"]["lag2_date"] == "2026-09-18"
    assert rows["CHF"]["net_usd"] == pytest.approx(1_250_000.0) and rows["CHF"]["worst_1d_raw_date"] == SNB
    assert not math.isnan(engine["book"]["vol_blended_ann_usd"])

    text = _text(body)
    assert f"History: {tmp_path / 'hist'}, last close {AS_OF}; used to {AS_OF}, lag-2 date 2026-09-18." in text
    cards = _cards(body)
    assert cards["Net USD delta"][0] == format_cell(engine["book"]["net_usd"]) == "250,000"
    assert cards["DV01 (USD/bp)"][0] == "4,200"
    assert cards["Blended vol (annual)"][0] == format_cell(engine["book"]["vol_blended_ann_usd"])
    assert cards["Worst day raw"][0] == format_cell(engine["book"]["worst_1d_raw_usd"]) and SNB in cards["Worst day raw"][1]
    # the book's worst day ex shocks is the rates step (DV01 x -30bp = -126,000), not CHF's 2020 day: the engine's call
    assert engine["book"]["worst_1d_ex_shocks_date"] == "2008-10-15"
    assert cards["Worst day ex shocks"][0] == format_cell(engine["book"]["worst_1d_ex_shocks_usd"])
    assert engine["book"]["worst_1d_ex_shocks_date"] in cards["Worst day ex shocks"][1]
    table = _table(body, risk.TABLE_ID)
    assert [rec["underlyer"] for rec in table.data] == [r["underlyer"] for r in engine["underlyers"]] == ["CHF", "JPY", "USD rates"]
    chf = table.data[0]
    assert chf["net_usd"] == pytest.approx(1_250_000.0) and chf["worst_1d_raw_usd"] == pytest.approx(rows["CHF"]["worst_1d_raw_usd"])
    assert chf["worst_1d_raw_date"] == SNB and chf["worst_1d_ex_shocks_date"] == WORST_EX
    assert chf["vol_blended_ann_usd"] == pytest.approx(rows["CHF"]["vol_blended_ann_usd"]) and chf["note"] == "carry included"
    rates = table.data[2]
    assert rates["dv01_usd"] == 4200.0 and rates["worst_1d_raw_usd"] == pytest.approx(rows["USD rates"]["worst_1d_raw_usd"])
    assert rates["worst_1d_raw_date"] == "2008-10-15" and rates["net_usd"] is None
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["worst_1d_raw_usd"] == pytest.approx(engine["book"]["worst_1d_raw_usd"])
    scen = _table(body, risk.SCENARIO_TABLE_ID)
    assert [rec["scenario"] for rec in scen.data] == list(engine["scenarios"])
    europe = next(rec for rec in scen.data if rec["scenario"].startswith("Europe"))
    assert europe["fx_total"] == pytest.approx(engine["scenarios"][europe["scenario"]]["fx_total"]) == pytest.approx(-37_500.0)
    assert europe["equity_pnl"] is None
    # a JPY that never moves: its vol is a real zero from the engine, shown as 0, not n/a
    assert table.data[1]["vol_blended_ann_usd"] == 0.0
