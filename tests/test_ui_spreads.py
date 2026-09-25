"""Tests for ui/tabs/spreads.py: the Spreads tab renders `engine.spreads.book_spreads`'s output and
the research app's statistics (`engine.risk.research_spreads`) and recomputes nothing. Most tests
feed the body builders a synthetic `book_spreads` result and a synthetic research result; the
end-to-end ones build a small book in tmp_path through the real schema and the real engine (a WTI
calendar with marks on two closes and a corn outright with no price at all), a small research
database shaped like the research app's, and the synthetic Jason sample (`tests/golden_book.py`).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import sqlite3
import sys
import types

import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from dash import dash_table, dcc, html  # noqa: E402
from dash.exceptions import PreventUpdate  # noqa: E402

from data.contracts import get_root  # noqa: E402
from data.ingest import schema  # noqa: E402
from ui.tabs import spreads  # noqa: E402
from ui.tabs.formatting import INFO_MARK  # noqa: E402

AS_OF = "2026-09-15"          # a Tuesday: Daily is measured from the 2026-09-14 close
PREV = "2026-09-14"
CLZ6, CLF7 = "CLZ26 Comdty", "CLF27 Comdty"
CORN = "C Z26 Comdty"
PERIODS = ("ltd", "daily", "d5", "mtd", "ytd")
M = spreads.MINUS
CAL_KEY = ("cal.nymex_cl.z_f", "2026")
BW_KEY = ("bench.crude.brent_vs_wti", "")


# --------------------------------------------------------------------------- tree helpers
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


def _by_id(node, node_id):
    hits = [n for n in _walk(node) if getattr(n, "id", None) == node_id]
    assert len(hits) == 1, f"{len(hits)} x {node_id}"
    return hits[0]


def _has(node, node_id) -> bool:
    return any(getattr(n, "id", None) == node_id for n in _walk(node))


def _row(table, name_start):
    i = next(i for i, r in enumerate(table.data) if r["name"].startswith(name_start))
    return table.data[i], table.tooltip_data[i]


# --------------------------------------------------------------------------- a fake result
def _periods(values, reasons=None, notes=None, refs=None):
    return {"pnl_usd": dict(zip(PERIODS, values)),
            "pnl_reasons": {p: (reasons or {}).get(p, "") for p in PERIODS},
            "pnl_notes": {p: (notes or {}).get(p, "") for p in PERIODS},
            "ref_dates": {p: (refs or {}).get(p, AS_OF if p == "ltd" else PREV) for p in PERIODS}}


def _leg(inst, lots, pnl_usd, month, weight=None, reason="", status="open", product="FUTURE"):
    return {"trade_ids": [f"T-{inst}"], "instrument_id": inst, "root_id": "NYMEX:CL", "product": product,
            "contract_month": month, "lots": lots, "open_lots": lots if status == "open" else 0.0,
            "currency": "USD", "pnl_local": pnl_usd, "pnl_usd": pnl_usd, "status": status, "reason": reason,
            "weight": weight}


def _level_leg(inst, weight, entry, prev, now, conversion=1.0):
    return {"instrument_id": inst, "root_id": "NYMEX:CL", "weight": weight, "qty_factor": None,
            "conversion": conversion, "currency": "USD", "price_scale": 1.0, "entry_price": entry,
            "prev_price": prev, "now_price": now}


def _levels(entry=0.34, prev=2.28, now=-0.34, unit="USD/bbl", upu=15000.0, rid="cal.nymex_cl.z_f", inst="2026",
            reasons=None, legs=None):
    reasons = reasons or {}
    change = None if now is None or prev is None else now - prev
    return {"level_unit": unit, "level_entry": entry, "level_entry_reason": reasons.get("entry", ""),
            "level_prev": prev, "level_prev_reason": reasons.get("prev", ""), "level_prev_date": PREV,
            "level_now": now, "level_now_reason": reasons.get("now", ""),
            "level_change": change, "level_change_reason": reasons.get("change", ""),
            "usd_per_unit": upu, "usd_per_unit_reason": reasons.get("upu", ""),
            "level_sources": {"entry": "the fills, each leg's lots-weighted average",
                              "prev": "CLZ26 Comdty BBG_BDH; CLF27 Comdty BBG_BDH",
                              "now": "CLZ26 Comdty BBG_BDH; CLF27 Comdty BBG_BDH", "usd_per_unit": ""},
            "level_legs": legs if legs is not None else [_level_leg(CLZ6, 1.0, 68.54, 69.70, 67.14),
                                                         _level_leg(CLF7, -1.0, 68.20, 67.41, 67.48)],
            "research_id": rid, "research_instance": inst,
            "research_reason": "" if rid else reasons.get("research", "no calendar or template fits")}


def _spread(sid, name, ltd, *, kind="calendar", status="open", legs=None, periods=None, leftover=None, **extra):
    s = {"spread_id": sid, "name": name, "kind": kind, "template": "", "family": "calendar", "unit": "USD/bbl",
         "size": 10.0, "size_unit": "lots", "deviation": 0.0, "also_matches": [], "trade_ids": [f"{sid}-1"],
         "accounts": ["ACC"], "trade_dates": ["2026-09-01"], "status": status,
         "legs": legs if legs is not None else [_leg(CLZ6, 10.0, 1000.0, "2026-12", 1.0),
                                                _leg(CLF7, -10.0, -400.0, "2027-01", -1.0)],
         "leftover": leftover if leftover is not None else [{"root_id": "NYMEX:CL", "lots": 0.0,
                                                             "usd_notional": 0.0, "reason": ""}],
         "leftover_basis": "net of the calendar"}
    s.update(periods or _periods([ltd, 100.0, 200.0, 300.0, 400.0]))
    s.update(_levels())
    s.update(extra)
    return s


def _position(pid, name, ltd, *, family="calendar", kind="calendar", members=("SPREAD-A",), periods=None,
              excluded=None, levels=None, size=10.0, direction="long", size_unit="lots", legs=None, leftover=None,
              status="open"):
    p = {"position_id": pid, "spread_ids": list(members), "name": name, "kind": kind, "template": "",
         "family": family, "unit": "USD/bbl", "direction": direction, "size": size, "size_unit": size_unit,
         "trade_ids": [f"{m}-1" for m in members], "accounts": ["ACC"], "trade_dates": ["2026-09-01"],
         "status": status,
         "legs": legs if legs is not None else [_leg(CLZ6, 10.0, 1000.0, "2026-12", 1.0),
                                                _leg(CLF7, -10.0, -400.0, "2027-01", -1.0)],
         "leftover": leftover if leftover is not None else [{"root_id": "NYMEX:CL", "lots": 0.0,
                                                             "usd_notional": 0.0, "reason": ""}]}
    p.update(periods or _periods([ltd, 100.0, 200.0, 300.0, 400.0]))
    p["pnl_excluded"] = {q: (excluded or {}).get(q, 0) for q in PERIODS}
    p.update(levels if levels is not None else _levels())
    return p


def _outright(tid, ltd, why="", review_ids=()):
    o = {"trade_id": tid, "instrument_id": CORN, "root_id": "CBOT:ZC", "contract_month": "2026-12",
         "account": "ACC", "trade_date": "2026-09-01", "lots": 2.0, "currency": "USD", "status": "open",
         "pnl_local": ltd, "why_outright": why, "review_ids": list(review_ids)}
    o.update(_periods([ltd, 1.0, 2.0, 3.0, 4.0]) if ltd is not None
             else _periods([None] * 5, reasons={p: "C1 (no FUTURE_PX mark)" for p in PERIODS}))
    return o


UNPRICED = "unpriced on 2026-09-15: W9 (no FUTURE_PX mark)"


def _result():
    unpriced = _periods([None] * 5, reasons={p: UNPRICED for p in PERIODS})
    return {
        "as_of": AS_OF,
        "spreads": [
            _spread("SPREAD-A", "CL Z26/F27 calendar", 600.0, trade_dates=["2026-09-01"]),
            _spread("SPREAD-A2", "CL Z26/F27 calendar", 50.0, size=5.0, trade_dates=["2026-09-03"],
                    level_entry=0.30),
            _spread("SPREAD-B", "CL X26/Z26 calendar", -5000.0),
            _spread("SPREAD-C", "Brent vs WTI", None, kind="bench.crude.brent_vs_wti", family="benchmark",
                    template="bench.crude.brent_vs_wti", periods=unpriced, level_entry=None,
                    level_entry_reason="the entry needs the CNY SPOT of 2026-08-20"),
            _spread("BUNDLE-old", "old", 50.0, kind="bundle", status="closed", size=None, size_unit="",
                    legs=[_leg(CLZ6, 1.0, 50.0, "2026-12", status="closed")], leftover=[]),
        ],
        "positions": [
            _position("POSITION-calendar|CLZ26 Comdty/CLF27 Comdty|long", "CL Z26/F27 calendar", 650.0,
                      members=("SPREAD-A", "SPREAD-A2"), size=15.0),
            _position("POSITION-calendar|CLX26 Comdty/CLZ26 Comdty|short", "CL X26/Z26 calendar", -5000.0,
                      members=("SPREAD-B",), direction="short", size=-10.0,
                      levels=_levels(entry=-0.5, prev=-0.6, now=-0.9, rid="cal.nymex_cl.x_z", upu=-10000.0)),
            _position("POSITION-bench|COZ26/CLZ26|long", "Brent vs WTI", None, family="benchmark",
                      kind="bench.crude.brent_vs_wti", members=("SPREAD-C",), size=5000.0, size_unit="bbl",
                      periods=unpriced, excluded={p: 1 for p in PERIODS},
                      levels=_levels(entry=None, prev=1.87, now=4.93, rid="bench.crude.brent_vs_wti", inst="",
                                     upu=5000.0, reasons={"entry": "the entry needs the CNY SPOT of 2026-08-20"}),
                      leftover=[{"root_id": "ICE:B", "lots": 1.0, "usd_notional": None,
                                 "reason": "COZ26 Comdty: no price or USD conversion on 2026-09-15"}]),
            _position("POSITION-BUNDLE-old", "old", 50.0, kind="bundle", family="", members=("BUNDLE-old",),
                      status="closed", size=None),
        ],
        "outrights": [_outright("C1", None, why="", review_ids=["ratio_off|C1+C2"]),
                      _outright("C2", 80.0, why="split by hand (spread_overrides)")],
        "review": [{"review_id": "ratio_off|C1+C2", "kind": "ratio_off", "trade_ids": ["C1", "C2"],
                    "instruments": [CORN], "accounts": ["ACC"], "trade_dates": ["2026-09-01"],
                    "candidates": [{"kind": "calendar", "name": "C Z26/H27 calendar", "trade_ids": ["C1", "C2"],
                                    "size": 2.0, "size_unit": "lots", "deviation": 0.1, "also_matches": []}],
                    "reason": "looks like C Z26/H27 calendar but the lots are off by 10.0%; left as outrights"}],
        "reasons": ["template crack_321 could not be sized"],
    }


def _entry(key, found=True, **stats):
    e = {"spread_id": key[0], "instance": key[1], "found": found, "reason": "" if found else stats.pop("reason"),
         "note": stats.pop("note", ""), "label": "research", "name": "x", "family": "calendar", "sector": "energy",
         "unit": stats.pop("unit", "USD/bbl"), "verified": True, "asof": "2026-09-15" if found else None,
         "z_primary": None, "z_primary_kind": None, "z_1y": None, "pctile_5y": None, "half_life_days": None,
         "dvol_20d": None}
    e.update(stats)
    return e


def _research():
    return {"available": True, "path": "rv.sqlite", "reason": "", "run_asof": "2026-09-15", "label": "research",
            "source": "research app database rv.sqlite, run of 2026-09-15", "as_of": AS_OF,
            "stats": {
                CAL_KEY: _entry(CAL_KEY, dvol_20d=1.2, z_primary=-3.2, z_primary_kind="seasonal_analogue",
                                z_1y=-1.7, pctile_5y=7.8, half_life_days=6.0),
                ("cal.nymex_cl.x_z", "2026"): _entry(("cal.nymex_cl.x_z", "2026"), dvol_20d=0.25, z_primary=0.4,
                                                     z_primary_kind="z_1y", pctile_5y=50.0, half_life_days=None),
                BW_KEY: _entry(BW_KEY, found=False,
                               reason="spread bench.crude.brent_vs_wti is not in the research universe (rv.sqlite)"),
            }}


# --------------------------------------------------------------------------- the positions table
def test_one_row_per_open_position_grouped_by_family_in_trader_order():
    table = _by_id(spreads.body(_result(), _research()), spreads.TABLE_ID)
    assert [r["name"] for r in table.data] == ["CL X26/Z26 calendar", "CL Z26/F27 calendar · 2 entries",
                                               "Brent vs WTI"]                  # closed "old" is not here
    assert "family" not in table.data[0]                             # the grouping says it; the name's hover too
    assert "family Benchmark / arb" in table.tooltip_data[2]["name"]["value"]
    assert [r["id"] for r in table.data][1] == "POSITION-calendar|CLZ26 Comdty/CLF27 Comdty|long"
    ids = [c["id"] for c in table.columns]
    assert ids == ["rank", "name", "legs", "size", "unit", "entry", "now", "change", "usd_per_unit", "daily", "mtd",
                   "ltd", "sigma", "z", "pctile", "half_life", "leftover", "leftover_usd", "pnl_note"]
    # two header rows, the research group names its run
    assert all(isinstance(c["name"], list) and len(c["name"]) == 2 for c in table.columns)
    assert {c["name"][0] for c in table.columns if c["id"] in spreads.RESEARCH_COLS} == {"Research (run 2026-09-15)"}
    assert table.merge_duplicate_headers is True
    assert "context only, not a mark" in table.tooltip_header["sigma"]
    # a family boundary is marked on the row where the group starts
    starts = [r["if"]["row_index"] for r in table.style_data_conditional if "row_index" in r.get("if", {})]
    assert starts == [2]


def test_every_column_fits_the_width_budget_at_1680_px_with_no_horizontal_scroll():
    table = _by_id(spreads.body(_result(), _research()), spreads.TABLE_ID)
    widths = spreads.COLUMN_WIDTHS_PX
    assert {c["id"] for c in table.columns} == set(widths)
    rendered = sum(widths.values()) + spreads.BORDER_PX * len(widths)
    assert rendered <= spreads.WIDTH_BUDGET_PX, rendered
    rules = {r["if"]["column_id"]: r for r in table.style_cell_conditional if "width" in r}
    for col, px in widths.items():
        assert rules[col]["width"] == rules[col]["minWidth"] == rules[col]["maxWidth"] == f"{px}px"
    # a long name, legs or leftover is cut on one line, never wrapped or widened
    assert table.style_cell["whiteSpace"] == "nowrap" and table.style_cell["textOverflow"] == "ellipsis"
    # the footer shares the widths, so the Total line lines up
    footer = _by_id(spreads.body(_result(), _research()), spreads.TABLE_ID + "-footer")
    assert [r for r in footer.style_cell_conditional if "width" in r] == list(rules.values())


def test_the_total_line_sits_under_the_rows_with_no_band():
    body = spreads.body(_result(), _research())
    for table_id in (spreads.TABLE_ID, spreads.CLOSED_TABLE_ID, spreads.OUTRIGHTS_TABLE_ID):
        footer = _by_id(body, table_id + "-footer")
        assert {"selector": "tr:has(> th)", "rule": "display: none;"} in footer.css, table_id
        assert not any("th" in (c.get("selector") or "") for c in (getattr(_by_id(body, table_id), "css", None) or []))


def test_family_order_puts_hand_groups_last():
    items = [{"kind": "bundle", "family": "", "pnl_usd": {"ltd": 1e6}},
             {"kind": "x", "family": "substitution", "pnl_usd": {"ltd": 1.0}},
             {"kind": "pinned", "family": "", "pnl_usd": {"ltd": 1.0}},
             {"kind": "y", "family": "processing", "pnl_usd": {"ltd": 1.0}},
             {"kind": "z", "family": "exotic", "pnl_usd": {"ltd": 1.0}},
             {"kind": "calendar", "family": "calendar", "pnl_usd": {"ltd": None}}]
    assert [spreads.family_key(i) for i in spreads.family_order(items)] == [
        "calendar", "processing", "substitution", "exotic", "bundle", "pinned"]


def test_size_with_direction_and_levels_in_the_units_decimals_sources_on_hover():
    table = _by_id(spreads.body(_result(), _research()), spreads.TABLE_ID)
    rec, tip = _row(table, "CL Z26/F27")
    assert rec["size"] == "long 15 lots" and rec["unit"] == "USD/bbl"
    assert (rec["entry"], rec["now"], rec["change"]) == ("0.34", f"{M}0.34", f"{M}2.62")
    assert "BBG_BDH" in tip["now"]["value"] and "lots-weighted" in tip["entry"]["value"]
    assert f"at the {PREV} close" in tip["change"]["value"] and "BBG_BDH" in tip["change"]["value"]
    assert "2 entries: SPREAD-A, SPREAD-A2" in tip["name"]["value"]
    assert "research id cal.nymex_cl.z_f 2026" in tip["name"]["value"]
    short, _tip = _row(table, "CL X26/Z26")
    assert short["size"] == "short 10 lots" and short["usd_per_unit"] == -10000.0
    assert rec["usd_per_unit"] == 15000.0 and "USD 15,000" in tip["usd_per_unit"]["value"]
    # a missing entry is n/a with the engine's reason, never 0
    bw, bw_tip = _row(table, "Brent vs WTI")
    assert bw["entry"] == "n/a" and "CNY SPOT of 2026-08-20" in bw_tip["entry"]["value"]


def test_level_decimals_follow_the_unit():
    assert spreads.level_text(0.0525, "USD/bu") == "0.0525"
    assert spreads.level_text(-0.1234, "USD/mmbtu", sign=True) == f"{M}0.123"
    assert spreads.level_text(3.0612, "USD/bbl", sign=True) == "+3.06"
    assert spreads.level_text(1234.56, "CNY/t") == "1,234.6"
    assert spreads.level_text(15382.3, "JPY/g") == "15,382"
    assert spreads.level_text(None, "USD/bbl") == "n/a"
    assert spreads.level_text(-0.001, "USD/bbl", sign=True) == "0.00"          # no negative zero


def test_research_columns_are_the_readers_figures_labelled_and_highlighted():
    table = _by_id(spreads.body(_result(), _research()), spreads.TABLE_ID)
    rec, tip = _row(table, "CL Z26/F27")
    # sigma is risk-history's sigma_move of the engine's level change over dvol_20d
    assert rec["sigma"] == pytest.approx((-0.34 - 2.28) / 1.2)
    assert rec["z"] == -3.2 and rec["pctile"] == 7.8 and rec["half_life"] == 6.0
    assert "seasonal_analogue" in tip["z"]["value"] and "1-year z" in tip["z"]["value"]
    for col in spreads.RESEARCH_COLS:
        assert "research run of 2026-09-15" in tip[col]["value"] and "not a mark" in tip[col]["value"]
    # a statistic the research app left blank is n/a with its reason
    other, other_tip = _row(table, "CL X26/Z26")
    assert other["half_life"] == "n/a" and "left the half-life" in other_tip["half_life"]["value"]
    # not in the research universe: every research cell n/a with that reason
    bw, bw_tip = _row(table, "Brent vs WTI")
    for col in spreads.RESEARCH_COLS:
        assert bw[col] == "n/a" and "not in the research universe" in bw_tip[col]["value"]
    # |sigma| and |z| of 2 or more are shaded
    rules = [r for r in table.style_data_conditional if r.get("if", {}).get("column_id") in ("sigma", "z")
             and "filter_query" in r["if"] and "backgroundColor" in r]
    assert {r["if"]["filter_query"] for r in rules} >= {"{sigma} >= 2.0", "{sigma} <= -2.0", "{z} >= 2.0",
                                                         "{z} <= -2.0"}
    # the research columns carry no P&L sign colour (a rise is not a gain for a short spread)
    assert not any(r.get("color") == "var(--pos)" and r["if"].get("column_id") in spreads.RESEARCH_COLS
                   for r in table.style_data_conditional)


def test_research_not_read_or_units_differ_are_na_with_the_reason():
    body = spreads.body(_result())                                  # no research result at all
    table = _by_id(body, spreads.TABLE_ID)
    rec, tip = _row(table, "CL Z26/F27")
    assert rec["sigma"] == "n/a" and spreads.NO_RESEARCH in tip["sigma"]["value"]
    assert "Research: n/a" in _text(body.children[0])
    research = _research()
    research["stats"][CAL_KEY]["unit"] = "USD/t"
    rec, tip = _row(_by_id(spreads.body(_result(), research), spreads.TABLE_ID), "CL Z26/F27")
    assert rec["sigma"] == "n/a" and "units differ" in tip["sigma"]["value"]
    assert rec["z"] == -3.2                                          # the other statistics are the reader's


def test_pnl_daily_mtd_ltd_k_m_with_5d_and_ytd_on_hover_and_excl_markers():
    result = _result()
    result["positions"][0].update(_periods([1234.6, -51018.4, 7.0, 1650590.0, 99.0]))
    result["positions"][0]["pnl_excluded"] = {"ltd": 0, "daily": 1, "d5": 0, "mtd": 0, "ytd": 0}
    result["positions"][0]["pnl_reasons"]["daily"] = "SPREAD-A2: no close on 2026-09-14"
    body = spreads.body(result, _research())
    table = _by_id(body, spreads.TABLE_ID)
    rec, tip = _row(table, "CL Z26/F27")
    short = spreads.rk.amount_short(nully="")
    assert all(c["format"] == short for c in table.columns if c["id"] in ("daily", "mtd", "ltd", "usd_per_unit",
                                                                          "leftover_usd"))
    assert (rec["ltd"], rec["daily"], rec["mtd"]) == (1235.0, -51018.0, 1650590.0)
    assert tip["daily"]["value"].startswith(f"USD {M}51,018") and "5d: USD 7" in tip["daily"]["value"]
    assert "YTD: USD 99" in tip["ltd"]["value"]
    assert rec["pnl_note"] == "excl. 1" and "Daily excludes 1: SPREAD-A2: no close" in tip["pnl_note"]["value"]
    assert "excludes 1 entry" in tip["daily"]["value"]
    # all members unpriced: n/a with the reasons, never 0
    bw, bw_tip = _row(table, "Brent vs WTI")
    assert bw["ltd"] == "n/a" and UNPRICED in bw_tip["ltd"]["value"]
    # the Total line sums the known figures and names what it leaves out or only partly holds
    footer = _by_id(body, spreads.TABLE_ID + "-footer")
    assert footer.columns == table.columns
    assert footer.data[0]["name"] == "Total" and footer.data[0]["ltd"] == round(1234.6 - 5000.0)
    ftip = footer.tooltip_data[0]
    assert "excludes 1 of 3 positions" in ftip["ltd"]["value"] and "Brent vs WTI" in ftip["ltd"]["value"]
    assert "leave out entries: CL Z26/F27 calendar" in ftip["daily"]["value"]


def test_leftover_stays_per_root_with_its_usd():
    rec, tip = _row(_by_id(spreads.body(_result(), _research()), spreads.TABLE_ID), "Brent vs WTI")
    assert rec["leftover"] == "B +1" and rec["leftover_usd"] == "n/a"
    assert "no price or USD conversion" in tip["leftover_usd"]["value"]


# --------------------------------------------------------------------------- the drill-down
def _history(key, start=None, end=None):
    s = pd.Series([0.1, -0.2, -1.0], index=pd.DatetimeIndex(pd.to_datetime(["2026-09-10", "2026-09-11",
                                                                             "2026-09-14"]), name="date"))
    s.attrs.update(reason="", label="research", path="rv.sqlite", spread_id=key[0], instance=key[1],
                   unit="USD/bbl", spread_name="WTI Z/F")
    _history.calls.append((key, start, end))
    return s


_history.calls = []


def test_the_body_carries_every_open_positions_drilldown_and_a_hint():
    body = spreads.body(_result(), _research())
    store = _by_id(body, spreads.DETAIL_STORE_ID)
    assert isinstance(store, dcc.Store)
    pid = "POSITION-calendar|CLZ26 Comdty/CLF27 Comdty|long"
    assert set(store.data) == {r["id"] for r in _by_id(body, spreads.TABLE_ID).data}
    payload = store.data[pid]
    assert [m["spread_id"] for m in payload["members"]] == ["SPREAD-A", "SPREAD-A2"]
    assert payload["members"][1]["trade_dates"] == ["2026-09-03"] and payload["members"][1]["level_entry"] == 0.30
    assert payload["legs"][0]["instrument_id"] == CLZ6 and payload["legs"][0]["lots"] == 10.0
    assert (payload["research_id"], payload["research_instance"]) == CAL_KEY
    assert "Click a spread" in _text(_by_id(body, spreads.DETAIL_ID))
    import json
    json.dumps(store.data)                                           # the store must be JSON


def test_detail_panel_chart_entry_line_today_marker_entries_and_legs():
    _history.calls.clear()
    pid = "POSITION-calendar|CLZ26 Comdty/CLF27 Comdty|long"
    body = spreads.body(_result(), _research(), selected=pid, history_fn=_history)
    panel = _by_id(body, spreads.DETAIL_ID)
    assert _history.calls == [(CAL_KEY, "2021-09-15", AS_OF)]         # 5 years back to the as-of
    graph = _by_id(panel, spreads.DETAIL_GRAPH_ID)
    names = [d["name"] for d in graph.figure["data"]]
    assert names[0] == "research history" and names[1].startswith("entry 0.34") and names[2].startswith(f"now {M}0.34")
    assert graph.figure["data"][1]["y"] == [0.34, 0.34] and graph.figure["data"][2]["x"] == [AS_OF]
    assert "research history (the research app's prices)" in _text(panel) and "not a mark" in _text(panel)
    members = _by_id(panel, spreads.DETAIL_MEMBERS_ID)
    assert [(m["spread_id"], m["entry"], m["trade_dates"]) for m in members.data] == [
        ("SPREAD-A", "0.34", "2026-09-01"), ("SPREAD-A2", "0.30", "2026-09-03")]
    legs = _by_id(panel, spreads.DETAIL_LEGS_ID)
    assert [(r["contract"], r["entry_price"], r["now_price"]) for r in legs.data] == [
        ("CLZ26", 68.54, 67.14), ("CLF27", 68.20, 67.48)]
    assert any(c["name"] == f"{PREV} close px" for c in legs.columns)


def test_detail_panel_says_why_when_there_is_no_history_or_units_differ():
    payloads = spreads.detail_payloads(_result(), _research())
    bw = payloads["POSITION-bench|COZ26/CLZ26|long"]
    empty = pd.Series([], dtype=float)
    empty.attrs.update(reason="spread x is not in the research universe (rv.sqlite)")
    panel = spreads.detail_panel(bw, AS_OF, history_fn=lambda *a, **k: empty)
    assert "No research history (the research app's prices): spread x is not in the research universe" in _text(panel)
    assert not _has(panel, spreads.DETAIL_GRAPH_ID)
    # no research id at all
    bw = dict(bw, research_id="", research_reason="a calendar wider than the research app's")
    assert "wider than the research app's" in _text(spreads.detail_panel(bw, AS_OF, history_fn=_history))
    # units differ: the history is drawn, our levels are not overlaid on it
    cal = dict(payloads["POSITION-calendar|CLZ26 Comdty/CLF27 Comdty|long"], unit="USD/t")
    panel = spreads.detail_panel(cal, AS_OF, history_fn=_history)
    assert [d["name"] for d in _by_id(panel, spreads.DETAIL_GRAPH_ID).figure["data"]] == ["research history"]
    assert "not drawn" in _text(panel)
    # a missing entry names why on the members table
    members = _by_id(spreads.detail_panel(payloads["POSITION-bench|COZ26/CLZ26|long"], AS_OF, history_fn=_history),
                     spreads.DETAIL_MEMBERS_ID)
    assert members.data[0]["entry"] == "n/a" and "CNY SPOT" in members.tooltip_data[0]["entry"]["value"]


def test_open_detail_opens_the_clicked_position_and_ignores_a_rebuilt_table():
    payloads = spreads.detail_payloads(_result(), _research())
    pid = "POSITION-calendar|CLX26 Comdty/CLZ26 Comdty|short"
    panel, kept = spreads.open_detail({"row": 0, "column_id": "name", "row_id": pid}, payloads, AS_OF,
                                      history_fn=_history)
    assert kept == pid and isinstance(panel, html.Details) and "CL X26/Z26 calendar" in panel.children[0].children
    for cell in (None, {"row": 0}, {"row_id": "POSITION-gone"}):
        with pytest.raises(PreventUpdate):
            spreads.open_detail(cell, payloads, AS_OF)


# --------------------------------------------------------------------------- drawer, closed, outrights, review
def test_reasons_are_gathered_in_one_collapsed_data_issues_drawer():
    body = spreads.body(_result(), _research())
    drawer = _by_id(body, spreads.ISSUES_ID)
    assert isinstance(drawer, html.Details) and drawer.open is False
    text = _text(drawer)
    assert "template crack_321 could not be sized" in text                      # book level
    assert "Brent vs WTI" in text and "entry n/a: the entry needs the CNY SPOT" in text
    assert "research n/a: spread bench.crude.brent_vs_wti is not in the research universe" in text
    assert f"LTD, Daily, 5d, MTD, YTD n/a: {UNPRICED}" in text
    assert "half-life in days n/a" in text                                      # a statistic left blank
    assert "Outright C1 (C Z26)" in text and "C1 (no FUTURE_PX mark)" in text
    # the research database not found at all: one line, not one per position
    research = spreads.empty_research("no research database: tried a.sqlite")
    text = _text(_by_id(spreads.body(_result(), research), spreads.ISSUES_ID))
    assert text.count("no research database: tried a.sqlite") == 1


def test_closed_spreads_are_collapsed_below_the_open_ones():
    body = spreads.body(_result(), _research())
    closed = _by_id(body, spreads.CLOSED_ID)
    assert isinstance(closed, html.Details) and closed.open is False
    assert closed.children[0].children == "Closed spreads (1)"
    table = _by_id(closed, spreads.CLOSED_TABLE_ID)
    assert [r["name"] for r in table.data] == ["old"]
    assert table.data[0]["size"] == "n/a" and "no calendar or template fits" in table.tooltip_data[0]["size"]["value"]
    assert table.data[0]["leftover_usd"] == "n/a" and "no futures leg" in table.tooltip_data[0]["leftover_usd"]["value"]
    legs = _by_id(closed, spreads.CLOSED_LEGS_ID)
    (block,) = [n for n in legs.children if isinstance(n, html.Details)]
    assert isinstance(block.children[1], dash_table.DataTable) and block.children[1].data[0]["contract"] == "CLZ26"
    order = [n.id for n in body.children if getattr(n, "id", None)]
    assert order.index(spreads.CLOSED_ID) < order.index(spreads.OUTRIGHTS_ID)
    result = _result()
    result["spreads"] = [s for s in result["spreads"] if s["status"] == "open"]
    assert not _has(spreads.body(result), spreads.CLOSED_ID)


def test_total_is_na_when_no_row_has_the_figure_and_plain_when_all_known():
    rec, tip = spreads.total_record([{"name": "x"}], [{"ltd": "n/a"}], ["ltd"], "name", "legs", "spread")
    assert rec["ltd"] == "n/a" and "no spread has a LTD figure" in tip["ltd"]["value"]
    rec, tip = spreads.total_record([{"name": "x"}, {"name": "y"}], [{"ltd": 1.0}, {"ltd": 2.5}],
                                    ["ltd"], "name", "legs", "spread")
    assert rec["ltd"] == 3.5 and rec["legs"] == "2 spreads" and not tip


def test_outrights_render_with_period_pnl_and_why_single_line_full_figures():
    body = spreads.body(_result(), _research())
    table = _by_id(body, spreads.OUTRIGHTS_TABLE_ID)
    c1, c2 = table.data
    assert c1["trade_id"] == "C1" and c1["ltd"] == "n/a" and c1["why"] == "no spread fits"
    assert "no FUTURE_PX mark" in table.tooltip_data[0]["ltd"]["value"]
    assert c1["review"] == "yes (1)" and "ratio_off|C1+C2" in table.tooltip_data[0]["review"]["value"]
    assert c2["ltd"] == 80.0 and c2["why"] == "split by hand (spread_overrides)"
    assert all(c["format"] == spreads.rk.amount(nully="") for c in table.columns if c["id"] in PERIODS)
    assert table.style_cell["padding"] == "2px 8px"
    footer = _by_id(body, spreads.OUTRIGHTS_TABLE_ID + "-footer").data[0]
    assert footer["ltd"] == 80.0 and "LTD 1" in footer["why"]


def test_review_is_a_collapsed_drawer_one_line_per_group_reason_on_hover():
    drawer = _by_id(spreads.body(_result(), _research()), spreads.REVIEW_ID)
    assert isinstance(drawer, html.Details) and drawer.open is False and drawer.className == "issues-drawer"
    assert drawer.children[0].children == "For review (1)" and "ratio outside 5 %" in drawer.children[0].title
    (line,) = _by_id(drawer, spreads.REVIEW_TABLE_ID).children
    text = _text(line)
    assert "Ratio off" in text and "C Z26/H27 calendar" in text and "trades C1, C2" in text and "off 10.0%" in text
    assert "off by 10.0%" in line.title and "off by 10.0%" not in text
    assert "Bundles sub-tab" in _text(drawer)
    result = _result()
    result["review"] = []
    body = spreads.body(result)
    assert not _has(body, spreads.REVIEW_ID) and "0 groups for review" in _text(body)


def _titles(node):
    return {_text(n).split(INFO_MARK)[0].strip(): n.title for n in _walk(node)
            if "about-title" in (getattr(n, "className", None) or "")}


def test_definitions_are_on_hover_of_titles_and_the_caption_is_one_line():
    body = spreads.body(_result(), _research())
    assert [n for n in _walk(body) if isinstance(n, html.P) and "section-kicker" in (n.className or "")] == []
    titles = _titles(body)
    assert "one row however" not in titles["Open spreads (3)"] and "Grouped by family" in titles["Open spreads (3)"]
    assert "why spreads-engine left it outright" in titles["Outrights (2)"]
    caption = _text(body.children[0])
    assert "3 open positions (4 spreads), 1 closed, 2 outrights, 1 group for review" in caption
    assert "Research: run of 2026-09-15" in caption
    (title,) = _titles(spreads.layout(AS_OF)).values()
    assert "Total line adds up" in title and "header's as-of" in title


def test_size_reads_with_its_unit_in_one_cell():
    assert spreads.size_text(10.0, "lots") == "10 lots"
    assert spreads.size_text(5000.0, "bbl") == "5,000 bbl"
    assert spreads.size_text(-2.5, "t") == f"{M}2.5 t"
    assert spreads.size_text(3.0, "") == "3"


def test_empty_book_is_a_plain_sentence():
    body = spreads.body({"as_of": AS_OF, "spreads": [], "positions": [], "outrights": [], "review": [],
                         "reasons": []})
    assert f"No commodity futures in the book on {AS_OF}" in _text(body)
    assert not _has(body, spreads.TABLE_ID)


def test_layout_follows_the_header_and_all_ids_are_prefixed():
    layout = spreads.layout(AS_OF)
    ids = [getattr(n, "id", None) for n in _walk(layout) if getattr(n, "id", None)]
    assert {spreads.BODY_ID, spreads.REFRESH_ID, spreads.SELECTED_STORE_ID} <= set(ids)
    assert all(i.startswith("spreads-") for i in ids), ids
    assert not any(type(n).__name__ == "DatePickerSingle" for n in _walk(layout))
    assert spreads.build_layout is spreads.layout
    body = spreads.body(_result(), _research(), selected="POSITION-calendar|CLZ26 Comdty/CLF27 Comdty|long",
                        history_fn=_history)
    body_ids = [getattr(n, "id", None) for n in _walk(body) if getattr(n, "id", None)]
    assert all(i.startswith("spreads-") for i in body_ids), body_ids


# --------------------------------------------------------------------------- the real engine
def _future(conn, tid, inst, root_id, expiry, lots, fill):
    root = get_root(root_id)
    conn.execute("INSERT OR IGNORE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
                 "is_ndf, bbg_ticker, expiry_date) VALUES (?,'FUTURE',?,?,?,0,?,?)",
                 (inst, root_id, root.currency, root.multiplier, inst, expiry))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    vals = {"trade_id": tid, "source": "XLSX", "instrument_id": inst, "product": "FUTURE", "package_id": tid,
            "trade_date": "2026-09-01", "quantity": lots, "price": fill, "account": "ACC", "counterparty": "C",
            "strategy": "", "trader": "JB", "description": "d", "theme": ""}
    conn.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [vals[c] for c in cols])
    conn.execute("INSERT INTO trade_legs VALUES (?,1,'NOTIONAL',?,?,?,?,?,0)",
                 (tid, root.currency, lots * root.multiplier * fill, "2026-09-01", expiry, fill))


def _px(conn, inst, expiry, value, day):
    conn.execute("INSERT INTO marks VALUES (?,?,?,'FUTURE_PX',?,'BBG_BDH','t')", (day, inst, expiry, value))


def _write_book(path, empty=False):
    conn = schema.connect(str(path))
    if not empty:
        _future(conn, "W1", CLZ6, "NYMEX:CL", "2026-12-31", 2, 70.0)
        _future(conn, "W2", CLF7, "NYMEX:CL", "2027-01-29", -2, 69.5)
        for day, z, f in ((PREV, 70.5, 69.8), (AS_OF, 71.0, 70.2)):
            _px(conn, CLZ6, "2026-12-31", z, day)
            _px(conn, CLF7, "2027-01-29", f, day)
        _future(conn, "C1", CORN, "CBOT:ZC", "2026-12-14", 2, 440.0)       # no price at all
    conn.commit()
    conn.close()
    return path


def _research_db(path):
    """A research database shaped like the research app's, with the WTI Z/F calendar only."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE spread_def (spread_id TEXT PRIMARY KEY, family TEXT, name TEXT, sector TEXT, unit TEXT,
            verified INTEGER, in_universe INTEGER);
        CREATE TABLE spread_daily (spread_id TEXT, instance TEXT, date TEXT, value REAL);
        CREATE TABLE spread_stats (spread_id TEXT, instance TEXT, asof TEXT, computed_at TEXT, heavy_asof TEXT,
            last_obs_date TEXT, stale_days INTEGER, stale_leg TEXT, contract_note TEXT, n_obs INTEGER, level REAL,
            chg_1d REAL, chg_1d_sd REAL, z_1y REAL, pctile_5y REAL, z_primary REAL, z_primary_kind TEXT,
            dvol_20d REAL, half_life_days REAL);
    """)
    conn.execute("INSERT INTO spread_def VALUES ('cal.nymex_cl.z_f','calendar','WTI Z/F','energy','USD/bbl',1,1)")
    conn.execute("INSERT INTO spread_stats VALUES ('cal.nymex_cl.z_f','2026',?,'t',?,?,0,NULL,'',300,0.8,0.1,0.5,"
                 "2.5,95.0,2.4,'z_1y',0.25,8.0)", (PREV, PREV, PREV))
    conn.executemany("INSERT INTO spread_daily VALUES ('cal.nymex_cl.z_f','2026',?,?)",
                     [("2026-09-10", 0.5), ("2026-09-11", 0.6), (PREV, 0.7), (AS_OF, 0.8), ("2026-09-16", 0.9)])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def stub_app(monkeypatch):
    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)
    return stub


def test_render_on_a_real_book_with_a_research_database(tmp_path, stub_app, monkeypatch):
    monkeypatch.setenv("COMMODITY_HISTORY_DB", str(_research_db(tmp_path / "rv.sqlite")))
    body = spreads.render(AS_OF, _write_book(tmp_path / "spreads.db"))
    table = _by_id(body, spreads.TABLE_ID)
    (row,) = table.data
    ltd = 2 * 1000 * (71.0 - 70.0) - 2 * 1000 * (70.2 - 69.5)
    prev = 2 * 1000 * (70.5 - 70.0) - 2 * 1000 * (69.8 - 69.5)
    assert row["name"] == "CL Z26/F27 calendar" and row["legs"] == f"CLZ26 +2 / CLF27 {M}2"
    assert row["ltd"] == pytest.approx(ltd) and row["daily"] == pytest.approx(ltd - prev)
    assert (row["entry"], row["now"], row["change"]) == ("0.50", "0.80", "+0.10")
    assert row["sigma"] == pytest.approx(0.1 / 0.25) and row["z"] == 2.4 and row["pctile"] == 95.0
    assert row["usd_per_unit"] == 2000.0
    # the drill-down on the real research history, clipped at the as-of
    payloads = _by_id(body, spreads.DETAIL_STORE_ID).data
    panel, _pid = spreads.open_detail({"row_id": row["id"]}, payloads, AS_OF)
    figure = _by_id(panel, spreads.DETAIL_GRAPH_ID).figure
    assert figure["data"][0]["x"][-1] == AS_OF and len(figure["data"][0]["x"]) == 4
    assert figure["data"][1]["y"] == [0.5, 0.5] and figure["data"][2]["y"] == [pytest.approx(0.8)]
    outs = _by_id(body, spreads.OUTRIGHTS_TABLE_ID)
    (corn,) = outs.data
    assert corn["trade_id"] == "C1" and corn["ltd"] == "n/a" and outs.tooltip_data[0]["ltd"]["value"]
    # the kept selection reopens after a re-render
    again = spreads.render(AS_OF, tmp_path / "spreads.db", selected=row["id"])
    assert _has(again, spreads.DETAIL_GRAPH_ID)


def test_render_on_the_sample_book_every_position_has_its_levels(tmp_path, stub_app, monkeypatch):
    from tests.golden_book import build_book
    monkeypatch.setenv("COMMODITY_HISTORY_DB", str(tmp_path / "none.sqlite"))       # no research database
    path = tmp_path / "sample.db"
    conn = schema.connect(str(path))
    build_book(conn)
    conn.commit()
    conn.close()
    body = spreads.render("2026-09-18", path)
    table = _by_id(body, spreads.TABLE_ID)
    assert len(table.data) >= 5 and "family Calendar" in table.tooltip_data[0]["name"]["value"]
    assert all(r["now"] != "n/a" and r["unit"] != "n/a" for r in table.data)
    assert all(r["sigma"] == "n/a" for r in table.data)
    assert "no research database" in _text(_by_id(body, spreads.ISSUES_ID))


def test_render_on_an_empty_book_and_without_a_date_or_database(tmp_path, stub_app):
    assert f"No commodity futures in the book on {AS_OF}" in _text(
        spreads.render(AS_OF, _write_book(tmp_path / "empty.db", empty=True)))
    assert "No as-of date" in spreads.render(None, tmp_path / "x.db").children

    def refuse(path):
        raise sqlite3.OperationalError("unable to open database file")
    stub_app.connect_readonly = refuse
    assert "Database not available" in spreads.render(AS_OF, tmp_path / "missing.db").children


def test_register_callbacks_the_body_and_the_drilldown(tmp_path, stub_app):
    from ui.revision import DATA_REVISION_ID
    from ui.tabs.header import AS_OF_STORE_ID
    path = _write_book(tmp_path / "cb.db")
    app = dash.Dash(__name__)
    spreads.register_callbacks(app, get_db_path=lambda: str(path))
    key = f"{spreads.BODY_ID}.children"
    assert key in app.callback_map                                   # one output, as the shell's test reads it
    inputs = {(i["id"], i["property"]) for i in app.callback_map[key]["inputs"]}
    assert inputs == {(AS_OF_STORE_ID, "data"), (DATA_REVISION_ID, "data"), (spreads.REFRESH_ID, "n_intervals")}
    assert {(s["id"], s["property"]) for s in app.callback_map[key]["state"]} == {(spreads.SELECTED_STORE_ID, "data")}
    assert _has(app.callback_map[key]["callback"].__wrapped__(AS_OF, "rev", 0, None), spreads.TABLE_ID)
    drill = [k for k in app.callback_map if spreads.DETAIL_ID + ".children" in k]
    assert len(drill) == 1 and spreads.SELECTED_STORE_ID + ".data" in drill[0]
    assert {(i["id"], i["property"]) for i in app.callback_map[drill[0]]["inputs"]} == {(spreads.TABLE_ID,
                                                                                          "active_cell")}
    outputs = [o for k in app.callback_map for o in k.strip(".").split("...")]
    assert len(outputs) == len(set(outputs))
