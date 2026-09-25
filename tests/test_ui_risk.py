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


def _walk_open(node):
    """What is on screen with every collapsed block shut: `_walk` that does not enter a Details."""
    yield node
    if type(node).__name__ == "Details":
        return
    children = getattr(node, "children", None)
    if children is None:
        return
    for child in (children if isinstance(children, (list, tuple)) else [children]):
        yield from _walk_open(child)


def _texts(node) -> list:
    return [n for n in _walk(node) if isinstance(n, str)]


def _text(node) -> str:
    return " ".join(_texts(node))


def _titles(node) -> list:
    return [t for t in (getattr(n, "title", None) for n in _walk(node) if not isinstance(n, str)) if isinstance(t, str) and t]


def _ids(node) -> list:
    return [i for i in (getattr(n, "id", None) for n in _walk(node)) if isinstance(i, str)]


def _table(node, table_id: str) -> dash_table.DataTable:
    found = [n for n in _walk(node) if isinstance(n, dash_table.DataTable) and getattr(n, "id", None) == table_id]
    assert len(found) == 1, f"{table_id}: {len(found)} tables"
    return found[0]


def _cards(body) -> dict:
    """label -> (value text, note text or '', card title, value span, figure line). Every
    card is three lines: the label, the figure line (the value, its markers, a flag) and one
    clipped note line."""
    block = next(n for n in _walk(body) if getattr(n, "id", None) == risk.CARDS_ID)
    out = {}
    for card in block.children:
        assert len(card.children) == 3, card
        label, figure, note = card.children
        assert note.style["whiteSpace"] == "nowrap" and note.style["textOverflow"] == "ellipsis"
        value = figure.children[0]
        out[label.children] = (value.children, note.children.replace("\u00a0", ""), card.title, value, figure)
    return out


def _markers(figure) -> dict:
    """The short markers on a card's figure line: text -> hover."""
    return {m.children: getattr(m, "title", None) for m in figure.children[1:] if m.className == "marker"}


def _drawer(body):
    """The tab's one Data issues drawer."""
    found = [n for n in _walk(body) if getattr(n, "id", None) == risk.ISSUES_ID]
    assert len(found) == 1
    return found[0]


def _issues(body) -> list:
    """The drawer's items as (label, sentence)."""
    items = []
    for li in _drawer(body).children[1].children:
        label, _, sentence = li.children
        items.append((label.children, sentence))
    return items


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
    """A `book_risk`-shaped result: CHF (full metrics), SEK (no history), XAU (a metal),
    and the two retired macro rows the engine may still return until risk-metrics drops
    them (SPX, USD rates), with their `missing` entries and the scenarios' equity line:
    the tab must render none of those."""
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
                                    "columns": [], "first_date": None, "last_date": None, "reason": "/hist/y not found"},
                         "swap_rates": {"file": "bbg_raw_rates.parquet", "path": "/hist/r", "loaded": True, "rows": 5000,
                                        "columns": ["USD_SWAP10Y"], "first_date": "2007-01-01", "last_date": AS_OF,
                                        "reason": ""}}}
    rows = [_row("CHF", "FX", 1_250_000.0),
            _row("SPX", "EQUITY_INDEX", 700_000.0, carry=True, note="ES futures + SPX options as one line"),
            _row("SEK", "FX", 300_000.0, carry=False, reason="no history for SEK", **_nan_metrics("no history for SEK")),
            _row("XAU", "METAL", 200_000.0, carry=False, note="metal, not in the FX net"),
            _row("USD rates", "RATES", 4_200.0, carry=False, note="the 10Y par swap rate stands in")]
    book = {"net_usd": 1_750_000.0, "gross_usd": 1_750_000.0, "dv01_usd": 4_200.0, "fx_net_usd": -1_550_000.0,
            "fx_gross_usd": 1_550_000.0, "rows_in_series": ["CHF", "XAU"], "reason": "",
            "vol_vs_target_pct": 27.4, "worst_day_ex_vs_cap_pct": 1.7, "over_cap": False, "over_vol_target": False,
            "var_window": {"dates": [AS_OF], "pnl_usd": [1.0]}}
    book.update(_metrics())
    scenarios = {
        "USD +2% all": {"total": -68_000.0, "fx_pnl": {"CHF": -25_000.0, "SEK": -6_000.0, "JPY": 0.0}, "fx_total": -31_000.0,
                        "futures_pnl": -28_000.0, "equity_pct": -0.04},
        "Europe -3%": {"total": -46_500.0, "fx_pnl": {"CHF": -37_500.0, "SEK": -9_000.0}, "fx_total": -46_500.0,
                       "futures_pnl": 0.0, "equity_pct": None},
        "Metals -10%": {"total": NAN, "fx_pnl": {}, "fx_total": 0.0, "futures_pnl": NAN, "equity_pct": None,
                        "reason": "no USD delta for XAU (no XAUUSD spot on 2026-09-22)"},
    }
    missing = ["NOK: not in the scenarios (no rate for NOK)", "rates: IRSOIS-USD-1: no DV01_USD on 2026-09-22",
               "equity index: ESZ6 Index: no FUTURE_PX on 2026-09-22", "SPX: not in the book series (no ES price)"]
    result = {"as_of": AS_OF, "history": history, "config": config, "book": book, "underlyers": rows,
              "scenarios": scenarios, "missing": missing}
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


# --------------------------------------------------------------------------- caption and drawer
def test_caption_is_one_short_line_and_the_sources_and_missing_list_are_in_the_drawer():
    r = _result()
    body = risk.body(r)
    parts = risk.caption_parts(r)
    assert [t for t, _ in parts] == ["As of Tuesday 22 September 2026 (2026-09-22)", "FX history to 2026-09-22",
                                     "vol target 4.50m"]
    # each part's full sentence is its hover
    assert parts[1][1].startswith("History: /hist/bbg_data, last close 2026-09-22; used to 2026-09-22, lag-2 date 2026-09-18.")
    assert parts[2][1].startswith("Parameters: vol target 4,500,000;")
    line = body.children[0].children[0]
    assert line.style["whiteSpace"] == "nowrap" and line.style["textOverflow"] == "ellipsis"
    assert [getattr(s, "title", None) for s in line.children if hasattr(s, "children")] == [p[1] for p in parts]
    # everything else in one collapsed drawer: the history used, the parameters, the missing list
    drawer = _drawer(body)
    assert type(drawer).__name__ == "Details" and drawer.open is False
    assert drawer.children[0].children == "Data issues (3)"
    issues = _issues(body)
    assert [label for label, _ in issues] == ["Market history", "Parameters", "Not included"]
    assert issues[0][1] == ("History: /hist/bbg_data, last close 2026-09-22; used to 2026-09-22, lag-2 date 2026-09-18. "
                            "Files: spot: bbg_raw_fx_marks.parquet (2007-01-01 to 2026-09-22, 5000 rows); "
                            "yields: bbg_raw_fx_yields.parquet (not loaded (/hist/y not found)).")
    assert issues[1][1] == ("vol target 4,500,000; stress cap 2,250,000 (50% of the target); blended vol 2/3 trailing "
                            "(500 bd) + 1/3 crisis (2008-01-01 to 2010-12-31), cutover 2011-01-01; VaR window 252 bd at "
                            "95%; worst day from 2008-01-01; shock dates: 2015-01-15 SNB floor removal; 2016-06-24 Brexit "
                            "referendum result. Read from /repo/config/risk.yaml (loaded).")
    assert issues[2] == ("Not included", "NOK: not in the scenarios (no rate for NOK)")
    # the drawer comes before the first figure: nothing but the caption line above it
    assert body.children[0].children[1] is drawer and body.children[1].id == risk.CARDS_ID
    # the config and history notes, when there are any, and a config that did not load
    r["config"].update(note="/repo/config/risk.yaml not found: defaults in use", loaded=False)
    r["history"]["note"] = "carry not included: /hist/y not found"
    issues = _issues(risk.body(r))
    assert ("Config", "/repo/config/risk.yaml not found: defaults in use.") in issues
    assert ("History", "carry not included: /hist/y not found.") in issues
    assert "(not loaded: /repo/config/risk.yaml not found: defaults in use)" in dict(issues)["Parameters"]


# --------------------------------------------------------------------------- cards
def test_every_card_shows_its_formatted_figure_with_the_definition_on_hover():
    cards = _cards(risk.body(_result()))
    assert list(cards) == ["Net USD delta", "Gross USD delta", "Commodity net USD", "Commodity gross USD",
                           "Blended vol (annual)", "1y 95% VaR (1-day)",
                           "Worst day ex shocks", "Worst day raw"]
    # the figure in k / m, the full figure on its hover
    assert cards["Net USD delta"][0] == "1.75m" and cards["Net USD delta"][3].title == "1,750,000"
    assert cards["Net USD delta"][1] == "+ = long the underlyer"
    assert "FX & cash tab's FX net USD (+ = long USD): (1,550,000)" in cards["Net USD delta"][2]
    assert cards["Gross USD delta"][0] == "1.75m" and "FX & cash tab's FX gross: 1,550,000" in cards["Gross USD delta"][2]
    assert "header" not in cards["Net USD delta"][2] + cards["Gross USD delta"][2]
    assert "currency and metal rows" in cards["Net USD delta"][2] and "equity" not in cards["Net USD delta"][2]
    assert cards["Blended vol (annual)"][0] == "1.23m" and cards["Blended vol (annual)"][1] == "27.4% of the 4.50m target"
    assert cards["1y 95% VaR (1-day)"][0] == "25.0k" and "positive = loss" in cards["1y 95% VaR (1-day)"][1]
    assert cards["Worst day ex shocks"][0] == "(37.5k)" and cards["Worst day ex shocks"][3].title == "(37,500)"
    assert cards["Worst day ex shocks"][1] == f"{WORST_EX}; 1.7% of the 2.25m cap"
    assert cards["Worst day raw"][0] == "(188k)" and cards["Worst day raw"][1] == f"{SNB}; nothing excluded"
    # nothing left out, so no marker on any figure line
    assert all(not _markers(c[4]) for c in cards.values())
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
    assert cards["Worst day ex shocks"][0] == "(2.96m)" and "131.6% of the 2.25m cap" in cards["Worst day ex shocks"][1]
    assert cards["Worst day ex shocks"][3].style["color"] == "var(--neg)" and cards["Worst day ex shocks"][3].style["fontWeight"] == "700"
    assert cards["Blended vol (annual)"][0] == "6.39m" and "142.0% of the 4.50m target" in cards["Blended vol (annual)"][1]
    # the flag sits on the figure line, beside the figure, not under it
    assert cards["Worst day ex shocks"][4].children[-1].children == "over cap"
    assert cards["Blended vol (annual)"][3].style["color"] == "var(--neg)"


def test_a_nan_figure_reads_na_with_its_reason_never_zero():
    r = _result()
    why = "needs 500 daily observations to 2026-09-18: 297 on file"
    r["book"].update(vol_blended_ann_usd=NAN, vol_trailing_ann_usd=NAN, vol_crisis_ann_usd=NAN, vol_vs_target_pct=NAN,
                     reasons={"vol_blended_ann_usd": why}, net_usd=NAN)
    r["missing"] = ["FX positions: no SPOT for USDNOK on 2026-09-22"]
    cards = _cards(risk.body(r))
    value, note, _, span, _ = cards["Blended vol (annual)"]
    assert value == "n/a" and note == why and span.title == why and "card-value--muted" in span.className
    assert cards["Net USD delta"][0] == "n/a" and cards["Net USD delta"][1] == "FX positions: no SPOT for USDNOK on 2026-09-22"
    assert "0" not in (cards["Blended vol (annual)"][0], cards["Net USD delta"][0])
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
    # a row the engine flags `carry` but could not size (a metal without a spot) does not claim carry
    r = _result()
    r["underlyers"][3].update(net_usd=NAN, gross_usd=NAN, reason="no SPOT for XAUUSD on 2026-09-22", carry=True,
                              **_nan_metrics("no SPOT for XAUUSD on 2026-09-22"))
    xau = _table(risk.body(r), risk.TABLE_ID).data[2]
    assert xau["net_usd"] == "n/a" and xau["note"] == "no SPOT for XAUUSD on 2026-09-22; metal, not in the FX net"
    # many reasons behind one card: the first with a count as the note, the whole list on hover
    r = _result()
    r["book"]["net_usd"] = NAN
    r["missing"] = [f"FX option: USDJPY-{i}: no DELTA on 2026-09-22" for i in range(1, 19)]
    body = risk.body(r)
    value, note, _, span, _ = _cards(body)["Net USD delta"]
    assert value == "n/a" and note == "FX option: USDJPY-1: no DELTA on 2026-09-22 (+17 more on hover)"
    assert span.title.count("no DELTA") == 18
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["net_usd"] == "n/a" and footer.tooltip_data[0]["net_usd"]["value"].count("no DELTA") == 18
    # nothing on the missing list at all: the Book's delta still says why
    r = _result(missing=[])
    r["book"]["net_usd"] = NAN
    body = risk.body(r)
    assert _cards(body)["Net USD delta"][1] == "no currency or metal position with a USD delta"
    assert _table(body, f"{risk.TABLE_ID}-footer").tooltip_data[0]["net_usd"]["value"] == \
        "no currency or metal position with a USD delta"
    # the retired rows' reasons never stand in for a missing delta
    r = _result(missing=["rates: IRSOIS-USD-1: no DV01_USD on 2026-09-22", "SPX: not in the book series (no ES price)"])
    r["book"]["net_usd"] = NAN
    assert _cards(risk.body(r))["Net USD delta"][1] == "no currency or metal position with a USD delta"
    # a partial delta: the figure stands with a short "excl. N" marker, the reasons on its hover
    r = _result(missing=["FX positions: no SPOT for USDNOK on 2026-09-22", "FX option: EURUSD-1: no DELTA on 2026-09-22"])
    cards = _cards(risk.body(r))
    assert cards["Net USD delta"][0] == "1.75m"
    assert _markers(cards["Net USD delta"][4]) == {"excl. 2": "FX positions: no SPOT for USDNOK on 2026-09-22; "
                                                              "FX option: EURUSD-1: no DELTA on 2026-09-22"}
    assert "excl. 2" in _markers(cards["Gross USD delta"][4])


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
    # the caption says there is none, the folders tried are its hover and the drawer's first item
    assert [t for t, _ in risk.caption_parts(r)][1] == "FX history: none"
    assert reason in risk.caption_parts(r)[1][1]
    assert _issues(body)[0] == ("Market history", f"Market history unavailable: {reason}. Every currency and metal "
                                                  "risk figure is n/a; the positions and the scenarios stand.")
    cards = _cards(body)
    for label in ("Blended vol (annual)", "1y 95% VaR (1-day)", "Worst day ex shocks", "Worst day raw"):
        assert cards[label][0] == "n/a" and reason in cards[label][1] and reason in cards[label][3].title, label
    assert cards["Net USD delta"][0] == "1.75m"          # the positions stand
    table = _table(body, risk.TABLE_ID)
    assert all(rec["vol_blended_ann_usd"] == "n/a" for rec in table.data)
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["worst_1d_raw_usd"] == "n/a" and footer.data[0]["net_usd"] == 1_750_000.0
    # the definitions still read on hover, without a lag-2 date to name
    assert any("the history's last date less 2 business days" in t for t in _titles(body))


# --------------------------------------------------------------------------- the key table
def test_table_rows_follow_the_engines_order_and_the_book_is_pinned_under_them():
    body = risk.body(_result())
    table = _table(body, risk.TABLE_ID)
    # the engine's order, less the retired SPX and USD rates rows
    assert [rec["underlyer"] for rec in table.data] == ["CHF", "SEK", "XAU"]
    assert [rec["kind"] for rec in table.data] == ["FX", "FX", "Metal"]
    assert [c["id"] for c in table.columns] == [
        "underlyer", "kind", "sector", "net_usd", "gross_usd", "vol_blended_ann_usd", "vol_trailing_ann_usd",
        "vol_crisis_ann_usd", "var95_1d_usd", "worst_1d_ex_shocks_usd", "worst_1d_ex_shocks_date", "worst_1d_raw_usd",
        "worst_1d_raw_date", "worst_day_ex_vs_target_pct", "note"]
    assert [c["name"] for c in table.columns][8:11] == ["VaR95 1d", "Worst ex shocks", "Date"]
    chf = table.data[0]
    assert chf["net_usd"] == 1_250_000.0 and isinstance(chf["net_usd"], float)      # a number, so it ranks
    assert "dv01_usd" not in chf
    # money in k / M: whole units under the table's SI format (a stray 0.4 would print "400m")
    assert chf["vol_blended_ann_usd"] == 1_234_568.0 and chf["worst_1d_raw_usd"] == -187_500.0
    from ui.tabs import ranking as rk
    formats = {c["id"]: c.get("format") for c in table.columns}
    for col in ("net_usd", "gross_usd", "vol_blended_ann_usd", "var95_1d_usd", "worst_1d_raw_usd"):
        assert formats[col] == rk.amount_short(nully=""), col
    assert chf["worst_1d_raw_date"] == SNB and chf["worst_1d_ex_shocks_date"] == WORST_EX
    assert chf["worst_day_ex_vs_target_pct"] == 0.8333 and chf["note"] == "carry included"
    # single-line rows: the name and the note clipped with an ellipsis, the full note on hover
    clipped = {r["if"]["column_id"]: r for r in table.style_cell_conditional if r.get("textOverflow") == "ellipsis"}
    assert set(clipped) == {"underlyer", "note"} and all(r["whiteSpace"] == "nowrap" for r in clipped.values())
    assert not any(r.get("whiteSpace") == "normal" for r in table.style_cell_conditional)
    assert table.tooltip_data[0]["note"] == {"value": "carry included", "type": "text"}
    xau = table.data[2]
    assert xau["net_usd"] == 200_000.0 and xau["gross_usd"] == 200_000.0 and xau["note"] == "metal, not in the FX net"
    # a kind the tab has no label for is shown as the engine names it, not dropped
    r = _result()
    r["underlyers"].insert(1, _row("XX", "NEW_KIND", 900_000.0, carry=False))
    assert [(rec["underlyer"], rec["kind"]) for rec in _table(risk.body(r), risk.TABLE_ID).data][:2] == \
        [("CHF", "FX"), ("XX", "NEW_KIND")]
    assert table.sort_action == "native" and table.persistence is True
    # the Book: a header-less second table under the first, same columns, never ranked with the rows
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.sort_action == "none" and footer.style_header == {"display": "none"}
    assert [c["id"] for c in footer.columns] == [c["id"] for c in table.columns]
    book = footer.data[0]
    assert (book["underlyer"], book["kind"]) == ("Book", "Book")
    assert book["net_usd"] == 1_750_000.0 and book["gross_usd"] == 1_750_000.0 and "dv01_usd" not in book
    assert book["worst_1d_raw_usd"] == -187_500.0 and book["worst_1d_raw_date"] == SNB
    assert book["worst_day_ex_vs_target_pct"] is None
    assert footer.tooltip_data[0]["worst_day_ex_vs_target_pct"]["value"] == \
        "the Book is measured against the stress cap: 1.7% of the cap (the card above)"
    assert book["note"] == "2 row(s)' daily P&L summed date by date, correlation embedded: CHF, XAU"
    # losses colour red, deltas by sign; the definitions are the header hovers
    styles = table.style_data_conditional
    assert {"if": {"column_id": "worst_1d_raw_usd", "filter_query": "{worst_1d_raw_usd} < 0"}, "color": "var(--neg)"} in styles
    assert {"if": {"column_id": "net_usd", "filter_query": "{net_usd} > 0"}, "color": "var(--pos)", "fontWeight": "700"} in styles
    assert "minus the 5th percentile" in table.tooltip_header["var95_1d_usd"]
    assert "Blended annual vol" in table.tooltip_header["vol_blended_ann_usd"]


def test_no_retired_macro_output_reaches_the_screen():
    """The rates and equity-index rows, their DV01, their `missing` entries and the
    scenarios' equity line (removed 2026-09-24) are rendered nowhere: not in a table, a
    card, a hover or the definitions, even while the engine still returns them."""
    from dash._utils import to_json
    r = _result()
    body = risk.body(r)
    rendered = to_json(body).lower()
    for needle in ("dv01", "swap", "spx", "es futures", "equity", "usd rates", "rates:", "irsois", "futures_pnl"):
        assert needle not in rendered, needle
    # the currency and metal rows' own missing entry stays
    assert "NOK: not in the scenarios (no rate for NOK)" in _text(body)
    assert risk.shown_missing(r) == ["NOK: not in the scenarios (no rate for NOK)"]
    assert [x["underlyer"] for x in risk.shown_underlyers(r)] == ["CHF", "SEK", "XAU"]
    # a book with only currency and metal rows (the shape once risk-metrics drops the rest) reads the same
    r2 = _result()
    r2["underlyers"] = risk.shown_underlyers(r2)
    r2["missing"] = risk.shown_missing(r2)
    assert _table(risk.body(r2), risk.TABLE_ID).data == _table(body, risk.TABLE_ID).data


# --------------------------------------------------------------------------- scenarios
def test_scenario_table_shows_the_engines_totals_and_no_equity_line():
    body = risk.body(_result())
    table = _table(body, risk.SCENARIO_TABLE_ID)
    assert [c["id"] for c in table.columns] == ["scenario", "total", "fx_total"]     # the equity line is retired
    assert [rec["scenario"] for rec in table.data] == ["USD +2% all", "Europe -3%", "Metals -10%"]
    usd2, europe, metals = table.data
    assert usd2 == {"scenario": "USD +2% all", "total": -68_000.0, "fx_total": -31_000.0}
    assert europe["total"] == -46_500.0 and europe["fx_total"] == -46_500.0
    assert metals["total"] == "n/a" and metals["fx_total"] == 0.0                     # n/a with the reason, a real 0 stays 0
    assert table.tooltip_data[2] == {"total": {"value": "no USD delta for XAU (no XAUUSD spot on 2026-09-22)", "type": "text"}}
    assert table.tooltip_data[1] == {}
    # the definition is the title's hover, naming the FX & cash tab (the Ladder's new name)
    title = next(n for n in _walk(body) if getattr(n, "className", "") == "about-title"
                 and "FX scenario stress" in _text(n))
    assert "config/stress.yaml" in title.title and "the same scenarios as the FX & cash tab's" in title.title
    assert "Ladder" not in " ".join(_titles(body)) + _text(body)
    from ui.tabs import ranking as rk
    assert all(c.get("format") == rk.amount_short(nully="") for c in table.columns[1:])
    # the matrix: underlyer order first, then the rest alphabetically; a currency a scenario does not move is blank
    matrix = _table(body, risk.MATRIX_TABLE_ID)
    assert [c["id"] for c in matrix.columns] == ["ccy", "USD +2% all", "Europe -3%", "Metals -10%"]
    assert [rec["ccy"] for rec in matrix.data] == ["CHF", "SEK", "JPY"]
    chf, sek, jpy = matrix.data
    assert chf["USD +2% all"] == -25_000.0 and chf["Europe -3%"] == -37_500.0 and chf["Metals -10%"] is None
    assert jpy["USD +2% all"] == 0.0 and jpy["Europe -3%"] is None
    footer = _table(body, f"{risk.MATRIX_TABLE_ID}-footer")
    assert footer.data == [{"ccy": "FX total", "USD +2% all": -31_000.0, "Europe -3%": -46_500.0, "Metals -10%": 0.0}]
    # the matrix is collapsed by default
    details = next(n for n in _walk(body) if type(n).__name__ == "Details" and "matrix" in _text(n).lower())
    assert details.open is False
    # no scenarios on file: said, not a crash
    r = _result(scenarios={})
    assert "No scenarios on file" in _text(risk.scenario_section(r))


# --------------------------------------------------------------------------- definitions
def test_definitions_sit_on_hover_of_the_titles_never_as_paragraphs():
    """Screens redesign (2026-09-25): a section's definitions are its title's hover (the
    info mark), the cards' and the columns' their own; no Definitions block, no paragraph of
    explanation above a table."""
    body = risk.body(_commodity_result(), _margin(), _checks())
    assert not hasattr(risk, "DEFINITIONS_ID")
    assert not [n for n in _walk(body) if type(n).__name__ in ("Dl", "Dt")]
    abouts = {_text(n).replace(" \u24d8", "").strip(): n.title for n in _walk(body)
              if getattr(n, "className", "") == "about-title"}
    assert list(abouts) == ["Risk by underlyer", "Views (not added to the Book)", "Commodity scenario stress",
                            "FX scenario stress", "Margin (estimate, not exchange SPAN)", "Limits"]
    table = abouts["Risk by underlyer"]
    for needle in ("the commodities grouped by sector, then the currencies and metals",
                   "each currency or metal row's USD delta x its daily move", "never added to the Book",
                   "counted twice", "crisis window not in history: trailing vol only",
                   "for every row alike (currencies, metals and commodities)",
                   "SNB floor removal 2015-01-15, Brexit referendum result 2016-06-24"):
        assert needle in table, needle
    assert "must never be summed" in abouts["Views (not added to the Book)"]
    assert "first order on delta" in abouts["Commodity scenario stress"]
    assert "/repo/config/commodity_stress.yaml" in abouts["Commodity scenario stress"]
    # the metrics' formulas: the cards' hovers and the column headers' tooltips
    titles = " ".join(_titles(body))
    for needle in ("Blended annual vol = 2/3 x trailing 500-day vol + 1/3 x crisis vol (2008-01-01 to 2010-12-31)",
                   "minus the 5th percentile of the last 252 daily", "positive = loss", "set to 0",
                   "Cap = 50% of the vol target = 2,250,000", "nothing excluded"):
        assert needle in titles, needle
    header_tips = _table(body, risk.TABLE_ID).tooltip_header
    assert "Blended annual vol" in header_tips["vol_blended_ann_usd"] and "clipped" in header_tips["note"]
    # no paragraph of explanation left on screen (outside the collapsed blocks): the only
    # kicker is the limits' one-line count
    kickers = [_text(n) for n in _walk_open(body) if getattr(n, "className", "") == "section-kicker"]
    assert kickers == ["From config/limits.yaml: 1 BREACH, 1 WARN, 1 OK, 1 n/a."]


# --------------------------------------------------------------------------- commodities (Phases 4 and 5)
CRISIS = "crisis window not in history: trailing vol only"
VOL_TARGET_NOTE = "placeholder: the macro fund's 4.5m, until Jason sets his own vol target (docs/open-questions.md C5)"


def _cm_row(root, name, sector, net, **over) -> dict:
    row = _row(root, "COMMODITY", net, carry=False, key=f"COMMODITY:{root}", role="part", parts=[], name=name,
               sector=sector, net_delta_lots=net / 100_000.0, vol_note=CRISIS, reason=CRISIS,
               note="research settlement history (risk input only)", contracts=[])
    row.update(over)
    return row


def _commodity_result(**over) -> dict:
    """A `book_risk` result with the commodity rows risk-metrics adds (Phase 4): three
    commodities in two sectors (the engine's order by gross: CL, ZC, B), two SECTOR views
    and a SPREAD view; the new book keys, the commodity history and commodity-stress's
    scenarios (an outright with a position it could not value, a replay with no history,
    an fx one)."""
    r = _result()
    r["config"].update(vol_target_placeholder=True, vol_target_note=VOL_TARGET_NOTE,
                       shock_dates=r["config"]["shock_dates"] + [
                           {"date": "2020-04-20", "name": "Negative WTI"}, {"date": "2022-03-07", "name": "LME nickel"}])
    cl_contracts = [
        {"contract_id": "CLZ26 Comdty", "product": "FUTURE", "history_contract": "CLZ26 Comdty", "delta_lots": 8.0,
         "multiplier": 1000.0, "currency": "USD", "days": 1200, "in_series": True, "reason": "", "note": ""},
        {"contract_id": "CLF27 Comdty", "product": "FUTURE", "history_contract": None, "delta_lots": 1.0,
         "multiplier": 1000.0, "currency": "USD", "days": 0, "in_series": False,
         "reason": "contract CLF27 Comdty: no history", "note": ""}]
    cl = _cm_row("NYMEX:CL", "WTI crude", "energy", 900_000.0, contracts=cl_contracts,
                 reason=f"excludes 1 of 2 contracts with no history: CLF27 Comdty: no history; {CRISIS}")
    zc = _cm_row("CBOT:ZC", "Corn", "agriculture", -500_000.0)
    brent = _cm_row("ICE:B", "Brent", "energy", 300_000.0, **_nan_metrics("no history: ICE:B not in the research database"),
                    reason="no history: ICE:B not in the research database", vol_note="")
    energy = _row("energy", "SECTOR", 1_200_000.0, carry=False, key="SECTOR:energy", role="view", name="energy",
                  sector="energy", parts=["COMMODITY:NYMEX:CL"], vol_note=CRISIS, reason=CRISIS,
                  note="view: the sum of its commodities' series (NYMEX:CL, ICE:B)")
    ags = _row("agriculture", "SECTOR", -500_000.0, carry=False, key="SECTOR:agriculture", role="view",
               name="agriculture", sector="agriculture", parts=["COMMODITY:CBOT:ZC"], vol_note=CRISIS, reason=CRISIS,
               note="view: the sum of its commodities' series (CBOT:ZC)")
    spread = _row("CL Z6/F7", "SPREAD", 100_000.0, carry=False, key="SPREAD:cal-1", role="view", name="CL Z6/F7",
                  sector="", spread_id="cal-1", spread_kind="calendar", family="", vol_note=CRISIS, reason=CRISIS,
                  parts=["CLZ26 Comdty", "CLF27 Comdty"],
                  legs=[{"contract_id": "CLZ26 Comdty", "root_id": "NYMEX:CL", "product": "FUTURE", "open_lots": 1.0,
                         "currency": "USD", "in_series": True, "days": 1200, "reason": ""},
                        {"contract_id": "CLF27 Comdty", "root_id": "NYMEX:CL", "product": "FUTURE", "open_lots": -1.0,
                         "currency": "USD", "in_series": False, "days": 0, "reason": "contract CLF27 Comdty: no history"}],
                  note="view: its legs' series at their open lots; net / gross USD = its leftover outright")
    for x in r["underlyers"]:
        x.update(key=x["underlyer"], role="part", parts=[], vol_note="")
    # the engine's order: the parts by gross (CL 0.9m, ZC 0.5m, B 0.3m among them), then sectors, then spreads
    r["underlyers"] = [r["underlyers"][0], cl, zc, r["underlyers"][1], brent, r["underlyers"][2], r["underlyers"][3],
                       r["underlyers"][4], energy, ags, spread]
    r["book"].update(commodity_net_usd=700_000.0, commodity_gross_usd=1_700_000.0, commodity_reason="",
                     views=["energy", "agriculture", "CL Z6/F7"], lag2_date="2026-09-18", vol_note=CRISIS,
                     rows_in_series=["CHF", "XAU", "NYMEX:CL", "CBOT:ZC"])
    r["commodity_history"] = {"available": True, "path": "/research/rvapp.db", "reason": "", "first_date": "2020-01-02",
                              "last_date": "2026-09-21", "note": "", "candidates": [], "roots": 180, "contracts": 9000,
                              "fx_pairs": ["USDCNH"], "used_to": "2026-09-21", "lag2_date": "2026-09-17",
                              "positions_note": ""}
    r["missing"] = r["missing"] + ["CLF27 Comdty: not in NYMEX:CL's series (contract CLF27 Comdty: no history)",
                                   "ICE:B: not in the book series (no history: ICE:B not in the research database)"]
    outright = {"name": "Energy -10%", "kind": "outright", "description": "crude and products down 10%", "basis": "delta",
                "total_usd": -90_000.0, "by_sector": {"energy": -90_000.0},
                "by_root": [{"root_id": "NYMEX:CL", "pnl_usd": -90_000.0}],
                "by_contract": [{"contract_id": "CLZ26 Comdty", "instrument_id": "CLZ26 Comdty", "product": "FUTURE",
                                 "root_id": "NYMEX:CL", "sector": "energy", "expiry": "2026-11-19", "lots": 10.0,
                                 "delta_lots": 10.0, "delta_usd": 900_000.0, "move": -0.10, "pnl_usd": -90_000.0}],
                "missing": [{"contract_id": "BZ1", "instrument_id": "COH7 Comdty", "product": "FUTURE", "root_id": "ICE:B",
                             "currency": "USD", "reason": "no USD delta on the day"}],
                "reason": "excludes 1 position(s) with no figure: BZ1: no USD delta on the day",
                "by_spread": [{"spread_id": "cal-1", "name": "CL Z6/F7", "family": "", "template": "", "pnl_usd": None,
                               "spread_pnl_usd": None, "leftover_pnl_usd": None, "legs": [], "leftover": [],
                               "reason": "COH7 Comdty: no USD delta on the day"}]}
    grains = {"name": "Grains +5%", "kind": "outright", "description": "", "basis": "delta", "total_usd": -25_000.0,
              "by_sector": {"agriculture": -25_000.0}, "by_root": [{"root_id": "CBOT:ZC", "pnl_usd": -25_000.0}],
              "by_contract": [], "missing": [], "reason": ""}
    replay = {"name": "2020 Covid", "kind": "replay", "description": "", "basis": "delta", "total_usd": None,
              "by_sector": {}, "by_root": [], "by_contract": [], "missing": [], "start": "2020-02-20", "end": "2020-04-21",
              "reason": "the commodity settlement history is not available"}
    cnh = {"name": "CNH -3%", "kind": "fx", "description": "", "basis": "delta", "total_usd": 1_500.0, "by_sector": {},
           "by_root": [], "by_contract": [], "missing": [], "reason": "", "delta_usd_change": -6_000.0,
           "by_currency": [{"currency": "CNY", "move": -0.03, "pnl_local": -350_000.0, "pnl_usd": -50_000.0,
                            "pnl_change_usd": 1_500.0, "delta_usd": 200_000.0, "delta_usd_change": -6_000.0}]}
    r["commodity_scenarios"] = {"as_of": AS_OF, "available": True, "basis": "delta", "config": "/repo/config/commodity_stress.yaml",
                                "scenarios": [outright, grains, replay, cnh],
                                "reasons": ["2020 Covid: n/a, the commodity settlement history is not available"]}
    r.update(over)
    return r


def _margin() -> dict:
    basis = "estimate (config/limits.yaml), not exchange SPAN"
    roll = {"gross_charge_usd": 90_000.0, "spread_credit_usd": 10_000.0, "margin_usd": 80_000.0, "positions": 2,
            "excluded": [], "excluded_count": 0, "caption": "", "reason": "", "basis": basis}
    ags = {"gross_charge_usd": 0.0, "spread_credit_usd": 0.0, "margin_usd": 0.0, "positions": 1,
           "excluded": ["CBOT:ZC 2026-12"], "excluded_count": 1,
           "caption": "excludes 1 of 1 positions with no margin figure",
           "reason": "CBOT:ZC 2026-12: no outright rate set for CBOT:ZC or agriculture in config/limits.yaml", "basis": basis}
    book = {"gross_charge_usd": 90_000.0, "spread_credit_usd": 10_000.0, "margin_usd": 80_000.0, "positions": 3,
            "excluded": ["CBOT:ZC 2026-12"], "excluded_count": 1,
            "caption": "excludes 1 of 3 positions with no margin figure", "reason": ags["reason"], "basis": basis}
    return {"as_of": AS_OF, "available": True, "basis": basis, "note": "Estimated initial margin: ...",
            "config_file": "/repo/config/limits.yaml", "config_note": "", "rows": [],
            "by_root": {"NYMEX:CL": {"name": "WTI crude", "sector": "energy", "rate_kind": "rate", "rate": 0.1,
                                     "rate_source": "sector energy rate", **roll},
                        "CBOT:ZC": {"name": "Corn", "sector": "agriculture", "rate_kind": "", "rate": None,
                                    "rate_source": "no outright rate set", **ags}},
            "by_sector": {"agriculture": ags, "energy": roll}, "book": book,
            "spreads": [{"spread_id": "cal-1", "name": "CL Z6/F7", "kind": "calendar", "family": "", "credit_key": "calendar",
                         "credit_pct": None, "legs": [], "charge_on_matched_usd": 20_000.0, "credit_usd": 0.0, "leftover": [],
                         "leftover_charge_usd": 0.0, "excluded": [], "excluded_count": 0,
                         "note": "no calendar credit set in config/limits.yaml: every lot at the outright rate", "basis": basis}],
            "reasons": ["CBOT:ZC 2026-12: no outright rate set for CBOT:ZC or agriculture in config/limits.yaml"]}


def _checks() -> list:
    def c(limit, scope, level, value, limit_value, used, reason, source="desk", unit="lots"):
        return {"limit": limit, "scope": scope, "source": source, "unit": unit, "value": value, "limit_value": limit_value,
                "used_pct": used, "level": level, "reason": reason, "basis": f"basis of {limit}"}
    return [c("gross_lots", "book", "BREACH", 120.0, 100.0, 120.0, "120% of the limit"),
            c("gross_usd", "book", "WARN", 900_000.0, 1_000_000.0, 90.0, "90% of the limit (warn from 80%)", unit="USD"),
            c("net_usd_commodity", "NYMEX:CL", "OK", 400_000.0, 1_000_000.0, 40.0, "", unit="USD"),
            c("net_usd_sector", "energy", "NOT_SET", 1_200_000.0, None, None,
              "no limit set in config/limits.yaml (desk_limits.net_usd_per_sector)", unit="USD"),
            c("exchange_spot_month", "ICE:B", "N/A", None, 500.0, None, "COH7 Comdty: no delta", source="exchange")]


def test_commodity_rows_come_first_grouped_by_sector_with_their_contracts_on_hover():
    r = _commodity_result()
    body = risk.body(r)
    table = _table(body, risk.TABLE_ID)
    # the parts only: the commodities by sector first, then currencies and metals in the engine's order
    assert [rec["underlyer"] for rec in table.data] == [
        "WTI crude (NYMEX:CL)", "Brent (ICE:B)", "Corn (CBOT:ZC)", "CHF", "SEK", "XAU"]
    assert [rec["kind"] for rec in table.data] == ["Commodity", "Commodity", "Commodity", "FX", "FX", "Metal"]
    assert [rec["sector"] for rec in table.data] == ["energy", "energy", "agriculture", None, None, None]
    assert risk.part_rows(r)[0]["key"] == "COMMODITY:NYMEX:CL"
    cl, brent = table.data[0], table.data[1]
    assert cl["net_usd"] == 900_000.0 and cl["vol_blended_ann_usd"] == 1_234_568.0
    # the contracts on the name's hover, the one left out with its reason
    hover = table.tooltip_data[0]["underlyer"]["value"]
    assert "WTI crude (NYMEX:CL), energy; net 9 delta lots" in hover
    assert "CLZ26 Comdty (FUTURE): 8 delta lots, in the series, 1200 days" in hover
    assert "CLF27 Comdty (FUTURE): 1 delta lots, not in the series: contract CLF27 Comdty: no history" in hover
    # the crisis-window fallback on hover of the vols, and in the note
    assert table.tooltip_data[0]["vol_blended_ann_usd"]["value"] == CRISIS
    assert table.tooltip_data[0]["vol_crisis_ann_usd"]["value"] == CRISIS
    assert cl["note"].startswith("excludes 1 of 2 contracts with no history") and CRISIS in cl["note"]
    assert table.tooltip_data[0]["note"]["value"] == cl["note"]            # clipped on its line, whole on hover
    # a commodity with no history: n/a with its reason, never zero; its delta stands
    assert brent["net_usd"] == 300_000.0 and brent["vol_blended_ann_usd"] == "n/a"
    assert table.tooltip_data[1]["vol_blended_ann_usd"]["value"] == "no history: ICE:B not in the research database"
    # the Book: the parts' series, its Net / Gross USD the currency and metal rows' with the commodities' on hover
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["note"] == ("4 row(s)' daily P&L summed date by date, correlation embedded: CHF, XAU, NYMEX:CL, "
                                      "CBOT:ZC; 3 view(s) below not added")
    assert "commodity rows' net USD is 700,000" in footer.tooltip_data[0]["net_usd"]["value"]
    assert "Commodity net USD card" in footer.tooltip_data[0]["net_usd"]["value"]
    assert footer.tooltip_data[0]["note"]["value"] == footer.data[0]["note"]
    # the footer shares the table's single-line widths
    assert footer.style_cell_conditional == table.style_cell_conditional


def test_sector_and_spread_views_are_marked_and_kept_out_of_the_book_table():
    r = _commodity_result()
    body = risk.body(r)
    main = _table(body, risk.TABLE_ID)
    assert not any(rec["kind"].endswith("(view)") for rec in main.data)
    views = _table(body, risk.VIEWS_TABLE_ID)
    assert [(rec["underlyer"], rec["kind"]) for rec in views.data] == [
        ("energy", "Sector (view)"), ("agriculture", "Sector (view)"), ("CL Z6/F7", "Spread (view)")]
    assert views.data[0]["net_usd"] == 1_200_000.0 and views.data[2]["net_usd"] == 100_000.0
    assert "Views (not added to the Book)" in _text(body) and any("must never be summed" in t for t in _titles(body))
    assert any(r.get("textOverflow") == "ellipsis" for r in views.style_cell_conditional)
    # a view's parts and legs on its name's hover
    assert "View, not added to the Book: the sum of COMMODITY:NYMEX:CL" in views.tooltip_data[0]["underlyer"]["value"]
    spread_hover = views.tooltip_data[2]["underlyer"]["value"]
    assert "spread cal-1 (calendar)" in spread_hover and "CLF27 Comdty (FUTURE): -1 open lots, not in the series" in spread_hover
    # the view rows are styled apart (the page background, italic) on their label columns
    assert {"if": {"column_id": "kind"}, "backgroundColor": "var(--page)", "fontStyle": "italic"} in views.style_data_conditional
    # no views: no second table
    r2 = _commodity_result()
    r2["underlyers"] = [x for x in r2["underlyers"] if x["role"] == "part"]
    assert not [n for n in _walk(risk.body(r2)) if getattr(n, "id", None) == risk.VIEWS_TABLE_ID]


def test_cards_and_caption_carry_the_commodity_book_and_the_vol_target_note():
    r = _commodity_result()
    body = risk.body(r)
    cards = _cards(body)
    assert cards["Commodity net USD"][0] == "700k" and cards["Commodity gross USD"][0] == "1.70m"
    assert "Kept apart from the currency and metal net" in cards["Commodity net USD"][2]
    # the blended vol: the target (a placeholder) on the note line, the crisis fallback as a marker
    blended = cards["Blended vol (annual)"]
    assert blended[1] == "27.4% of the 4.50m target (placeholder)"
    assert _markers(blended[4]) == {"trailing only": CRISIS}
    # the caption: the commodity history's last close, the vol target flagged as a placeholder
    assert [t for t, _ in risk.caption_parts(r)] == ["As of Tuesday 22 September 2026 (2026-09-22)", "FX history to 2026-09-22",
                                                   "commodity history to 2026-09-21", "vol target 4.50m"]
    line = body.children[0].children[0]
    assert [(m.children, m.title) for m in line.children if getattr(m, "className", "") == "marker"] == \
        [("placeholder", VOL_TARGET_NOTE)]
    issues = _issues(body)
    assert ("Commodity history", "Commodity history: /research/rvapp.db, settlements 2020-01-02 to 2026-09-21 (180 roots, "
                                 "9000 contracts); used to 2026-09-21, lag-2 date 2026-09-17; USD conversion pairs USDCNH. "
                                 "Read-only, a risk input only: nothing from it is a mark or enters P&L.") in issues
    assert ("Vol target", f"{VOL_TARGET_NOTE}.") in issues
    assert ("Not included", "ICE:B: not in the book series (no history: ICE:B not in the research database)") in issues
    # a partial commodity delta: the figure with a short "excl. 1" marker, the engine's reason on hover
    why = "excludes 1 of 3 commodities with no USD delta: ICE:B: no spot for USDCNH"
    r["book"].update(commodity_reason=why)
    cards = _cards(risk.body(r))
    assert cards["Commodity net USD"][0] == "700k" and cards["Commodity net USD"][1] == "+ = long"
    assert _markers(cards["Commodity net USD"][4]) == {"excl. 1": why}
    assert _markers(cards["Commodity gross USD"][4]) == {"excl. 1": why}
    # no commodity delta at all: n/a with the engine's reason, never zero
    r["book"].update(commodity_net_usd=NAN, commodity_gross_usd=NAN, commodity_reason="no open commodity positions on 2026-09-22")
    cards = _cards(risk.body(r))
    assert cards["Commodity net USD"][0] == "n/a" and cards["Commodity net USD"][1] == "no open commodity positions on 2026-09-22"
    # the commodity rows' missing entries never stand in for the currency and metal delta's reason
    r["book"]["net_usd"] = NAN
    assert _cards(risk.body(r))["Net USD delta"][1] == "no currency or metal position with a USD delta"


def test_commodity_scenarios_show_totals_by_sector_with_na_and_missing_on_hover():
    body = risk.body(_commodity_result())
    table = _table(body, risk.COMMODITY_SCENARIO_TABLE_ID)
    assert [c["id"] for c in table.columns] == ["scenario", "kind", "dates", "total_usd", "sector_0", "sector_1", "note"]
    assert [c["name"] for c in table.columns][4:6] == ["energy", "agriculture"]
    energy, grains, replay, cnh = table.data
    assert energy["total_usd"] == -90_000.0 and energy["sector_0"] == -90_000.0 and energy["sector_1"] is None
    assert energy["kind"] == "Outright" and energy["dates"] is None
    assert energy["note"] == "excl. 1"
    assert table.tooltip_data[0]["total_usd"]["value"] == ("excludes 1 position(s) with no figure: "
                                                           "BZ1: no USD delta on the day")
    assert table.tooltip_data[0]["note"]["value"] == table.tooltip_data[0]["total_usd"]["value"]
    assert table.tooltip_data[0]["scenario"]["value"] == "crude and products down 10%"
    assert grains["sector_1"] == -25_000.0 and table.tooltip_data[1] == {}
    # n/a with its reason, never 0; a replay names its dates
    assert replay["total_usd"] == "n/a" and replay["kind"] == "Replay" and replay["dates"] == "2020-02-20 to 2020-04-21"
    assert table.tooltip_data[2]["total_usd"]["value"] == "the commodity settlement history is not available"
    assert cnh["kind"] == "FX" and cnh["total_usd"] == 1_500.0 and "no sector split" in cnh["note"]
    from ui.tabs import ranking as rk
    assert all(c.get("format") == rk.amount_short(nully="") for c in table.columns if c["type"] == "numeric")
    titles = " ".join(_titles(body))
    assert "first order on delta" in titles and "their gamma is not in it" in titles
    # what the scenarios could not value is in the drawer, not a list under the table
    assert "Not valued" not in _text(body)
    assert ("Commodity stress", "2020 Covid: n/a, the commodity settlement history is not available") in _issues(body)
    # expandable: one collapsed block of the scenarios, each scenario collapsed with its breakdowns
    detail = next(n for n in _walk(body) if getattr(n, "id", None) == risk.COMMODITY_SCENARIO_DETAIL_ID)
    outer = next(n for n in _walk(body) if type(n).__name__ == "Details" and detail in (n.children or []))
    assert outer.open is False and outer.children[0].children == "Each scenario by commodity, contract, spread and currency (4)"
    blocks = [n for n in detail.children if type(n).__name__ == "Details"]
    assert len(blocks) == 4 and all(b.open is False for b in blocks)
    assert blocks[0].children[0].children == "Energy -10%: (90.0k) (excl. 1)"
    assert blocks[0].children[0].title == "(90,000); excludes 1 position(s) with no figure"
    first = _text(blocks[0])
    assert "By commodity" in first and "By contract" in first and "By spread" in first
    assert "Not in the total (1):" in first and "BZ1: no USD delta on the day" in first
    tables = [n for n in _walk(blocks[0]) if isinstance(n, dash_table.DataTable)]
    by_contract = next(t for t in tables if any(c["id"] == "move" for c in t.columns))
    assert by_contract.data[0]["move"] == -0.10 and by_contract.data[0]["pnl_usd"] == -90_000.0
    by_spread = next(t for t in tables if any(c["id"] == "spread_pnl_usd" for c in t.columns))
    assert by_spread.data[0]["pnl_usd"] == "n/a"
    assert by_spread.tooltip_data[0]["pnl_usd"]["value"] == "COH7 Comdty: no USD delta on the day"
    assert blocks[2].children[0].children == "2020 Covid: n/a"
    assert blocks[2].children[0].title == "the commodity settlement history is not available"
    by_ccy = next(t for t in (n for n in _walk(blocks[3]) if isinstance(n, dash_table.DataTable)))
    assert by_ccy.data[0]["currency"] == "CNY" and by_ccy.data[0]["pnl_change_usd"] == 1_500.0
    # unavailable stress: the reason, never an empty table
    r = _commodity_result(commodity_scenarios={"as_of": AS_OF, "available": False, "config": "", "scenarios": [],
                                               "reasons": ["the stress scenarios could not be read: bad kind"]})
    assert "Commodity stress unavailable: the stress scenarios could not be read: bad kind." in _text(risk.body(r))


def test_margin_is_labelled_an_estimate_with_credits_and_never_zero_for_a_missing_rate():
    body = risk.body(_commodity_result(), _margin(), _checks())
    section = next(n for n in _walk(body) if getattr(n, "id", None) == risk.MARGIN_LIMITS_ID)
    text = _text(section)
    assert "Margin (estimate, not exchange SPAN)" in text
    titles = " ".join(_titles(section))
    assert "Basis: estimate (config/limits.yaml), not exchange SPAN." in titles and "placeholders" in titles
    # the positions not in the margin are in the drawer, not a list under the table
    assert "Not in the margin" not in text
    assert ("Not in the margin", "CBOT:ZC 2026-12: no outright rate set for CBOT:ZC or agriculture in config/limits.yaml") \
        in _issues(body)
    table = _table(section, risk.MARGIN_TABLE_ID)
    assert [c["name"] for c in table.columns][1:4] == ["Gross charge USD", "Spread credit USD", "Margin USD (estimate)"]
    ags, energy = table.data
    assert energy["margin_usd"] == 80_000.0 and energy["spread_credit_usd"] == 10_000.0
    # every position of the sector without a rate: n/a with the reason, not the engine's 0 over nothing
    assert ags["gross_charge_usd"] == "n/a" and ags["margin_usd"] == "n/a"
    assert "no outright rate set" in table.tooltip_data[0]["margin_usd"]["value"]
    assert ags["note"] == "excludes 1 of 1 positions with no margin figure"
    footer = _table(section, f"{risk.MARGIN_TABLE_ID}-footer")
    assert footer.data[0]["label"] == "Book" and footer.data[0]["margin_usd"] == 80_000.0
    assert footer.data[0]["note"] == "excludes 1 of 3 positions with no margin figure"
    roots = _table(section, risk.MARGIN_ROOT_TABLE_ID)
    assert roots.data[0]["label"] == "WTI crude (NYMEX:CL)" and roots.data[0]["rate"] == "10% of |delta USD|"
    assert roots.data[1]["rate"] == "not set in config/limits.yaml"
    spreads = _table(section, risk.MARGIN_SPREAD_TABLE_ID)
    assert spreads.data[0]["credit_pct"] == "not set" and spreads.data[0]["charge_on_matched_usd"] == 20_000.0
    # unavailable: the reason
    out = _text(risk.margin_section({"available": False, "reasons": ["config/limits.yaml refused: bad rate"]}))
    assert "Margin estimate unavailable: config/limits.yaml refused: bad rate." in out


def test_limit_levels_are_coloured_and_not_set_says_so():
    section = risk.limits_section(_checks())
    table = _table(section, risk.LIMITS_TABLE_ID)
    # the table holds the limits that are set; the one not set is collapsed under it
    assert [rec["level"] for rec in table.data] == ["BREACH", "WARN", "OK", "n/a"]
    styles = table.style_data_conditional
    assert {"if": {"column_id": "level", "filter_query": "{level} = 'BREACH'"}, **risk.LEVEL_STYLES["BREACH"]} in styles
    assert risk.LEVEL_STYLES["BREACH"]["backgroundColor"] == "#c62828"                       # red
    assert {"if": {"column_id": "level", "filter_query": "{level} = 'WARN'"}, **risk.LEVEL_STYLES["WARN"]} in styles
    assert risk.LEVEL_STYLES["WARN"]["color"] == "#b26a00"                                   # amber
    assert {"if": {"column_id": "level", "filter_query": "{level} = 'OK'"}, **risk.LEVEL_STYLES["OK"]} in styles
    breach, _warn, _ok, na = table.data
    assert breach["value"] == 120.0 and breach["limit_value"] == 100.0 and breach["used_pct"] == 120.0
    assert na["value"] == "n/a" and table.tooltip_data[3]["value"]["value"] == "COH7 Comdty: no delta"
    assert table.tooltip_data[0]["limit"]["value"] == "basis of gross_lots"
    assert "From config/limits.yaml: 1 BREACH, 1 WARN, 1 OK, 1 n/a." in _text(section)
    # the not-set limit: one collapsed line, its position and config key one click away
    drawer = next(n for n in _walk(section) if getattr(n, "id", None) == risk.LIMITS_NOT_SET_ID)
    assert type(drawer).__name__ == "Details" and drawer.open is False
    assert _text(drawer.children[0]) == "Not set (1)"
    item = next(n for n in _walk(drawer) if getattr(n, "className", "") == "limit-not-set-item")
    assert item.children == "energy 1.20m USD"
    assert "Position: 1,200,000 USD." in item.title and "(desk_limits.net_usd_per_sector)" in item.title
    assert "basis of net_usd_sector" in item.title
    # single-line rows: the reason clipped, whole on hover; the explanation is the title's hover
    assert table.tooltip_data[1]["reason"]["value"] == "90% of the limit (warn from 80%)"
    assert any(r.get("textOverflow") == "ellipsis" and r["if"]["column_id"] == "reason" for r in table.style_cell_conditional)
    assert any("BREACH above the limit" in t for t in _titles(section))


def _not_set_checks() -> list:
    """The shape limit_checks returns on a fresh config/limits.yaml: every limit NOT_SET."""
    def c(limit, scope, value, source="desk", unit="lots", key="desk_limits.x"):
        return {"limit": limit, "scope": scope, "source": source, "unit": unit, "value": value, "limit_value": None,
                "used_pct": None, "level": "NOT_SET", "reason": f"no limit set in config/limits.yaml ({key})",
                "basis": f"basis of {limit}"}
    return [c("gross_lots", "book", 42.5), c("gross_usd", "book", 3_400_000.0, unit="USD"),
            c("net_usd_sector", "energy", -1_200_000.0, unit="USD"), c("net_usd_sector", "metals", None, unit="USD"),
            c("net_usd_commodity", "NYMEX:CL", -900_000.0, unit="USD"),
            c("net_usd_commodity", "LME:CA", 300_000.0, unit="USD"),
            c("exchange_spot_month", "NYMEX:CL 2026-12 (last trade 2026-11-19, estimated)", 5.0, source="exchange"),
            c("exchange_single_month", "NYMEX:CL 2027-01", -8.0, source="exchange"),
            c("exchange_all_months", "NYMEX:CL", -3.0, source="exchange"),
            c("exchange_spot_month", "LME:CA 2026-12 (last trade 2026-12-14)", 2.0, source="exchange"),
            c("lots_per_contract_month", "NYMEX:CL 2026-12", 5.0),
            c("lots_per_contract_month", "NYMEX:CL 2027-01", -8.0),
            c("lots_per_contract_month", "LME:CA 2026-12", 2.0)]


def test_nothing_set_reads_one_quiet_line_with_every_limit_collapsed_under_it():
    from dash._utils import to_json
    checks = _not_set_checks()
    section = risk.limits_section(checks)
    to_json(section)                                         # serialises
    # no table, one quiet line on screen and one collapsed line
    assert not [n for n in _walk(section) if isinstance(n, dash_table.DataTable)]
    shown = [_text(n) for n in _walk_open(section) if getattr(n, "className", "") == "section-kicker"]
    assert shown == ["No limit is set in config/limits.yaml yet: the positions are measured, not checked."]
    drawer = next(n for n in _walk(section) if getattr(n, "id", None) == risk.LIMITS_NOT_SET_ID)
    assert drawer.open is False and _text(drawer.children[0]) == f"Not set ({len(checks)})"
    assert "measured but not checked" in drawer.children[0].title
    # nothing dropped: one item per check, each with its config key on hover
    items = [n for n in _walk(drawer) if getattr(n, "className", "") == "limit-not-set-item"]
    assert len(items) == len(checks)
    assert all("no limit set in config/limits.yaml" in i.title for i in items)
    # grouped: the desk first (book, sectors, each root), then the exchange by root
    heads = [_text(n) for n in drawer.children[1:] if type(n).__name__ == "Div"]
    assert heads == ["Desk limits (9)", "Exchange limits (4)"]
    lines = [" ".join(_texts(li)) for li in _walk(drawer) if type(li).__name__ == "Li"]
    assert lines == [
        "Book   gross lots 42.5 lots  ·  gross USD 3.40m USD",
        "Net USD by sector   energy −1.20m USD  ·  metals n/a",
        "NYMEX:CL   net USD −900k USD  ·  2026-12 5 lots  ·  2027-01 -8 lots",
        "LME:CA   net USD 300k USD  ·  2026-12 2 lots",
        "NYMEX:CL   spot month 2026-12 5 lots  ·  single month 2027-01 -8 lots  ·  all months -3 lots",
        "LME:CA   spot month 2026-12 2 lots"]
    # a missing position is n/a with its reason, never zero; the spot month's last trade is on hover
    metals = next(i for i in items if i.children.startswith("metals"))
    assert "Position: n/a (the position has no figure)." in metals.title
    spot = next(i for i in items if i.children.startswith("spot month 2026-12 5"))
    assert "last trade 2026-11-19, estimated" in spot.title


def test_the_not_set_drawer_is_absent_when_every_limit_is_set():
    checks = [c for c in _checks() if c["level"] != "NOT_SET"]
    section = risk.limits_section(checks)
    assert risk.LIMITS_NOT_SET_ID not in _ids(section) and risk.not_set_drawer(checks) is None
    assert len(_table(section, risk.LIMITS_TABLE_ID).data) == len(checks)


def test_sections_run_commodities_first_then_fx_then_margin_and_limits():
    """Commodity scenario stress before the FX scenario stress, margin and limits after
    them (screens redesign, 2026-09-25); every component id the shell and the tests read is kept."""
    body = risk.body(_commodity_result(), _margin(), _checks())
    ids = _ids(body)
    order = [risk.ISSUES_ID, risk.CARDS_ID, risk.TABLE_ID, risk.VIEWS_TABLE_ID, risk.COMMODITY_SCENARIO_TABLE_ID,
             risk.SCENARIO_TABLE_ID, risk.MATRIX_TABLE_ID, risk.MARGIN_LIMITS_ID, risk.MARGIN_TABLE_ID,
             risk.LIMITS_TABLE_ID]
    assert [ids.index(i) for i in order] == sorted(ids.index(i) for i in order)
    assert risk.COMMODITY_SCENARIO_DETAIL_ID in ids and risk.MARGIN_ROOT_TABLE_ID in ids and risk.MARGIN_SPREAD_TABLE_ID in ids
    assert (risk.BODY_ID, risk.REFRESH_ID) == ("risk-body", "risk-refresh")
    # every table row is one line: no text column left to wrap
    for table in (n for n in _walk(body) if isinstance(n, dash_table.DataTable)):
        assert not any(r.get("whiteSpace") == "normal" for r in (table.style_cell_conditional or [])), table.id


def test_the_tab_builds_when_the_commodity_history_is_unavailable():
    from dash._utils import to_json
    r = _commodity_result()
    why = "no research database: tried /a/Commodity Dashboard/rvapp/data/rvapp.db"
    r["commodity_history"] = {"available": False, "path": "", "reason": why, "first_date": None, "last_date": None,
                              "note": "", "candidates": [], "roots": 0, "contracts": 0, "fx_pairs": [], "used_to": None,
                              "lag2_date": None, "positions_note": ""}
    for row in r["underlyers"]:
        if row["kind"] in ("COMMODITY", "SECTOR", "SPREAD"):
            row.update(_nan_metrics(f"no history: {why}"), reason=f"no history: {why}", vol_note="")
    body = risk.body(r, None, None)
    to_json(body)                                            # serialises: no NaN or numpy scalar left
    text = _text(body)
    assert f"Commodity history unavailable: {why}. The commodity rows' risk figures are n/a" in text
    table = _table(body, risk.TABLE_ID)
    cl = next(rec for rec in table.data if "NYMEX:CL" in rec["underlyer"])
    assert cl["vol_blended_ann_usd"] == "n/a" and cl["net_usd"] == 900_000.0
    assert "The margin estimate was not computed." in text and "The limit checks were not computed." in text
    # an engine with no commodity blocks at all (an FX-only result) still builds
    to_json(risk.body(_result()))
    assert "The commodity scenarios were not computed" in _text(risk.body(_result()))


def test_render_passes_the_margin_and_limits_to_the_body(tmp_path, monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    monkeypatch.setattr(risk, "book_risk", lambda conn, as_of: _commodity_result())
    monkeypatch.setattr(risk, "margin_and_limits", lambda conn, as_of: (_margin(), _checks()))
    body = risk.render(AS_OF, tmp_path / "risk.db")
    assert _table(body, risk.MARGIN_TABLE_ID).data[1]["margin_usd"] == 80_000.0
    assert _table(body, risk.LIMITS_TABLE_ID).data[0]["level"] == "BREACH"


def test_margin_and_limits_on_an_empty_book_read_the_engine_once_each():
    conn = schema.connect(":memory:")
    margin, checks = risk.margin_and_limits(conn, AS_OF)
    conn.close()
    assert margin["available"] is True and margin["basis"] == "estimate (config/limits.yaml), not exchange SPAN"
    assert checks and all(c["level"] in ("NOT_SET", "OK", "WARN", "BREACH", "N/A") for c in checks)
    section = risk.margin_limits_section(margin, checks)
    footer = _table(section, f"{risk.MARGIN_TABLE_ID}-footer")
    assert footer.data[0]["note"] == "no open commodity position"


# --------------------------------------------------------------------------- end to end
DATES = pd.bdate_range("2007-01-01", AS_OF)
SNAP = f"{AS_OF}T15:00:00-04:00"


def _write_history(folder):
    """CHF with the SNB day and a 2020 bad day, JPY flat, yields flat (no rates file: the
    rates underlyer is retired)."""
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
    return folder


def _write_book(db_path):
    conn = schema.connect(str(db_path))
    conn.executemany("INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, "
                     "bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)", [
        ("USDCHF", "FX", "USD", "CHF", 1, 0, "USDCHF Curncy", "9999-12-31"),
        ("USDJPY", "FX", "USD", "JPY", 1, 0, "USDJPY Curncy", "9999-12-31")])
    for tid, pair, (bccy, bamt), (qccy, qamt), px in [
            ("c1", "USDCHF", ("USD", -1_000_000.0), ("CHF", 800_000.0), 0.80),       # long 0.8m CHF at USDCHF 0.64 = $1.25m
            ("j1", "USDJPY", ("USD", 1_000_000.0), ("JPY", -150_000_000.0), 150.0)]:
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, "XLSX", pair, "FX_FWD", tid, "2026-09-01", bamt, px, "acc", "cp", "", "t", "d", ""))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
            (tid, 1, "FX_NEAR", bccy, bamt, "2026-09-01", "2026-10-20", px, 1),
            (tid, 2, "FX_NEAR", qccy, qamt, "2026-09-01", "2026-10-20", px, 1)])
    conn.executemany("INSERT INTO marks VALUES (?,?,?,?,?,?,?)", [
        (AS_OF, "USDCHF", AS_OF, "SPOT", 0.64, "BBG_BFXFORWARD", SNAP),
        (AS_OF, "USDJPY", AS_OF, "SPOT", 150.0, "BBG_BFXFORWARD", SNAP)])
    conn.commit()
    conn.close()


def test_the_rendered_tab_shows_the_engines_numbers_end_to_end(tmp_path, monkeypatch):
    from engine.risk import book_risk
    from ui.tabs.formatting import format_cell, short_money
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
    assert cards["Net USD delta"][0] == short_money(engine["book"]["net_usd"], parens=True) == "250k"
    assert cards["Net USD delta"][3].title == format_cell(engine["book"]["net_usd"]) == "250,000"
    assert "DV01 (USD/bp)" not in cards
    assert cards["Blended vol (annual)"][3].title == format_cell(engine["book"]["vol_blended_ann_usd"])
    assert cards["Worst day raw"][3].title == format_cell(engine["book"]["worst_1d_raw_usd"]) and SNB in cards["Worst day raw"][1]
    # the book's worst day ex shocks is CHF's 2020 day (JPY never moves), the SNB day being a shock date
    assert engine["book"]["worst_1d_ex_shocks_date"] == WORST_EX
    assert cards["Worst day ex shocks"][0] == short_money(engine["book"]["worst_1d_ex_shocks_usd"], parens=True)
    assert engine["book"]["worst_1d_ex_shocks_date"] in cards["Worst day ex shocks"][1]
    table = _table(body, risk.TABLE_ID)
    assert [rec["underlyer"] for rec in table.data] == [r["underlyer"] for r in engine["underlyers"]] == ["CHF", "JPY"]
    chf = table.data[0]
    # the table shows whole units (k / M on screen): the engine's figure rounded, never recomputed
    assert chf["net_usd"] == pytest.approx(1_250_000.0) and chf["worst_1d_raw_usd"] == round(rows["CHF"]["worst_1d_raw_usd"])
    assert chf["worst_1d_raw_date"] == SNB and chf["worst_1d_ex_shocks_date"] == WORST_EX
    assert chf["vol_blended_ann_usd"] == round(rows["CHF"]["vol_blended_ann_usd"]) and chf["note"] == "carry included"
    assert "dv01_usd" not in chf
    footer = _table(body, f"{risk.TABLE_ID}-footer")
    assert footer.data[0]["worst_1d_raw_usd"] == round(engine["book"]["worst_1d_raw_usd"])
    scen = _table(body, risk.SCENARIO_TABLE_ID)
    assert [rec["scenario"] for rec in scen.data] == list(engine["scenarios"])
    europe = next(rec for rec in scen.data if rec["scenario"].startswith("Europe"))
    assert europe["fx_total"] == pytest.approx(engine["scenarios"][europe["scenario"]]["fx_total"]) == pytest.approx(-37_500.0)
    assert set(europe) == {"scenario", "total", "fx_total"}
    # a JPY that never moves: its vol is a real zero from the engine, shown as 0, not n/a
    assert table.data[1]["vol_blended_ann_usd"] == 0.0
