"""Tests for ui/tabs/cash_ladder.py and ui/tabs/exposure.py (docs/BUILD_PLAN.md
Task C split, agent C1 Ladder; tightened by the 2026-09-15 same-day follow-up: no
dropdowns, no collapsed 'Details' on the Ladder tab).

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


def _all_ids(node, out=None):
    out = out if out is not None else []
    ident = getattr(node, "id", None)
    if ident is not None:
        out.append(ident)
    for child in getattr(node, "children", None) or []:
        if hasattr(child, "children") or hasattr(child, "id"):
            _all_ids(child, out)
    return out


# --------------------------------------------------------------------------- transpose_ladder
# transpose_ladder / table_from_ladder are no longer rendered on the Ladder tab (removed
# outright, user decision 2026-09-15) but stay in the module with their own tests.
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


def test_build_layout_keeps_retired_controls_out():
    """The 2026-09-15 retirements stay retired; the spec's own view controls (2026-09-18)
    are covered in tests/test_ui_ladder_view.py."""
    layout = cash_ladder.build_layout(default_date="2026-08-17")
    ids = _all_ids(layout)
    assert cash_ladder.DATE_PICKER_ID in ids
    assert "cash-ladder-summary-sort" not in ids  # sort dropdown removed outright
    assert "cash-ladder-status" not in ids
    assert "cash-ladder-source" not in ids  # workbook rates dropdown removed
    assert "cash-ladder-pull-now" not in ids  # moved to Market data tab


def test_render_with_seeded_db(tmp_path, conn, monkeypatch):
    # ui.app itself is C5's file; stub it here so this test only exercises
    # cash_ladder.py's own logic, per `connect_readonly`'s documented signature.
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
    matches = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k]
    assert matches
    callback = matches[0]["callback"].__wrapped__
    body = callback("2026-08-17", 0)
    assert body is not None


def test_render_no_as_of_date_message():
    app = dash.Dash(__name__)
    cash_ladder.register_callbacks(app, get_db_path=lambda: ":memory:")
    matches = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k]
    callback = matches[0]["callback"].__wrapped__
    body = callback(None, 0)
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


def test_render_no_longer_discards_exposure_only_unresolved_trades(tmp_path, conn, monkeypatch):
    """2026-09-16 fix: `_render` used to discard `exposure_records_from_db`'s own
    `unresolved` list entirely (assigned to `_exposure_unresolved` and never used), so
    a trade unresolved only under the exposure calc's settle_date > as_of rule (not the
    grid's >= rule) never reached `exposure_section`'s `unresolved` argument at all.

    Note: the "Unresolved trades N" caption itself (`ui/tabs/exposure.py::
    metadata_line`) was separately retired from the rendered UI by an earlier,
    documented 2026-09-15 decision (`exposure_section`'s own docstring: "metadata
    line... retired, not moved") -- it is dead code, not wired into exposure_section's
    output, regardless of this fix. So this test checks the actual data path (what
    `exposure_section` receives) rather than rendered text, which would never show it
    either way. Simulated by monkeypatching exposure_records_from_db to report one
    unresolved trade the grid's own records_from_db does not."""
    import sys
    import types
    from engine.ladder.exposure_adapter import Unresolved

    stub = types.ModuleType("ui.app")
    stub.connect_readonly = lambda path: schema.connect(str(path))
    monkeypatch.setitem(sys.modules, "ui.app", stub)

    db_path = tmp_path / "risk.db"
    seeded = schema.connect(str(db_path))
    _seed(seeded)
    seeded.close()

    import engine.ladder.exposure_adapter as exposure_adapter
    import ui.tabs.exposure as exposure_module

    real_exposure_records_from_db = exposure_adapter.exposure_records_from_db

    def _fake_exposure_records_from_db(conn, as_of_date, **kwargs):
        records, unresolved = real_exposure_records_from_db(conn, as_of_date, **kwargs)
        return records, list(unresolved) + [Unresolved("only-in-exposure", "AUDUSD", "test reason")]

    monkeypatch.setattr(exposure_adapter, "exposure_records_from_db", _fake_exposure_records_from_db)

    captured = {}
    real_exposure_section = exposure_module.exposure_section

    def _capturing_exposure_section(records, unresolved, *args, **kwargs):
        captured["unresolved"] = unresolved
        return real_exposure_section(records, unresolved, *args, **kwargs)

    monkeypatch.setattr(exposure_module, "exposure_section", _capturing_exposure_section)

    app = dash.Dash(__name__)
    cash_ladder.register_callbacks(app, get_db_path=lambda: str(db_path))
    matches = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k]
    callback = matches[0]["callback"].__wrapped__
    callback("2026-08-17", 0)

    assert "unresolved" in captured
    assert any(u.trade_id == "only-in-exposure" for u in captured["unresolved"])


def test_headline_numbers_present_in_order():
    """Merged 2026-09-15 (user decision -- Henry doesn't need the forward/non-forward
    split, one Delta card): was three cards, now one."""
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    headline = exposure.headline_numbers(result)
    labels = [c.children[0].children for c in headline.children]
    assert labels == [exposure.HEADLINE_TITLE] == ["USD delta, FX only"]


def test_headline_numbers_unavailable_without_rate():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, {})
    headline = exposure.headline_numbers(result)
    card = headline.children[0]
    assert card.children[1].children == "Unavailable"


def test_headline_is_fx_only_whatever_the_futures():
    """Phase 3 (CLAUDE.md "Net USD"): the card no longer takes the futures at all, so a
    future with no price cannot make it Unavailable, and a priced one is not summed in."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    result = build_exposure(RECORDS, RATES)
    totals = portfolio_totals(result)
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["CLZ26 Comdty"],
               "reason": "no FUTURE_PX on 2026-08-17 for CLZ26 Comdty"}
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, futures=futures)
    card = _find_id(section, exposure.HEADLINE_ID).children[0]
    assert card.children[1].children == exposure.format_amount(totals["gross_usd"])
    assert card.children[2].children[1].children == exposure.format_amount(-totals["net_usd"])


def test_exposure_section_has_headline_and_three_tables_only():
    futures = {"value": 1.0, "by_instrument": {"CLZ26 Comdty": 1.0}, "missing": [], "reason": ""}
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, futures=futures)
    ids = _all_ids(section)
    assert exposure.HEADLINE_ID in ids
    assert exposure.RISK_TABLE_ID in ids
    assert exposure.COMBINED_TABLE_ID in ids
    assert exposure.FUTURES_TABLE_ID in ids
    # Retired components must not appear.
    assert exposure.SNAPSHOT_ID not in ids
    assert exposure.META_ID not in ids
    assert exposure.LEGEND_ID not in ids
    assert exposure.LADDER_DETAILS_ID not in ids


def test_table_order_ladder_then_futures_then_risk():
    """User decision 2026-09-15 ("Reorder the Ladder tab", item 2): headline cards, then
    the currency ladder grid, then open futures, then the risk/scenario table."""
    futures = {"value": 1.0, "by_instrument": {"CLZ26 Comdty": 1.0}, "missing": [], "reason": ""}
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, futures=futures)
    ids = _all_ids(section)
    assert ids.index(exposure.HEADLINE_ID) < ids.index(exposure.COMBINED_TABLE_ID)
    assert ids.index(exposure.COMBINED_TABLE_ID) < ids.index(exposure.FUTURES_TABLE_ID)
    assert ids.index(exposure.FUTURES_TABLE_ID) < ids.index(exposure.RISK_TABLE_ID)


def test_combined_risk_frame_futures_row_present_with_mark():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": 100_000.0, "by_instrument": {"CLZ26 Comdty": 100_000.0}, "missing": [], "reason": ""}
    frame = exposure.combined_risk_frame(result, futures, scenarios={})
    row = frame[frame[exposure.RISK_LABEL_COL] == "CLZ26 Comdty"].iloc[0]
    assert row["usd_delta"] == 100_000.0
    assert row["_unavailable"] == ""


def test_combined_risk_frame_futures_row_unavailable_with_reason():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["CLZ26 Comdty"],
               "reason": "no FUTURE_PX on 2026-08-17 for CLZ26 Comdty"}
    frame = exposure.combined_risk_frame(result, futures, scenarios={})
    row = frame[frame[exposure.RISK_LABEL_COL] == "CLZ26 Comdty"].iloc[0]
    assert pd.isna(row["usd_delta"])
    assert "no FUTURE_PX" in row["_unavailable"]
    table = exposure.combined_risk_table(result, futures, scenarios={})
    inner = table.children[1].children[0]          # the ranked table; its pinned footer is children[1]
    i, row = next((i, r) for i, r in enumerate(inner.data) if r[exposure.RISK_LABEL_COL] == "CLZ26 Comdty")
    assert row["usd_delta"] == exposure.UNAVAILABLE   # the cell; the reason is its tooltip
    assert "no FUTURE_PX" in inner.tooltip_data[i]["usd_delta"]["value"]


def test_combined_risk_table_net_and_gross_exclude_futures():
    from engine.ladder.exposure import build_exposure, portfolio_totals
    result = build_exposure(RECORDS, RATES)
    totals = portfolio_totals(result)
    futures = {"value": 100_000.0, "by_instrument": {"CLZ26 Comdty": 100_000.0}, "missing": [], "reason": ""}
    table = exposure.combined_risk_table(result, futures, scenarios={})
    inner, footer = table.children[1].children       # the totals are the pinned footer, never ranked
    assert all(r["kind"] != "total" for r in inner.data)
    rows = {r[exposure.RISK_LABEL_COL]: r for r in footer.data}
    # USD position: + = long USD, as the header
    assert rows["Net USD delta, FX only (+ = long USD)"]["usd_delta"] == pytest.approx(-totals["net_usd"])
    assert rows["Gross USD delta, FX only"]["usd_delta"] == pytest.approx(totals["gross_usd"])
    # the future is still a row of its own, with its own USD delta
    assert next(r for r in inner.data if r[exposure.RISK_LABEL_COL] == "CLZ26 Comdty")["usd_delta"] == 100_000.0


def test_combined_risk_table_marks_fallback_currency():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    table = exposure.combined_risk_table(result, fallback_ccys={"JPY"})
    inner = table.children[1].children[0]
    names = [r[exposure.RISK_LABEL_COL] for r in inner.data]
    assert "JPY *" in names


def test_summary_block_table_has_rate_source_row():
    """The 'Rate source' row lives in the rate / delta block above the grid since
    2026-09-21 (it was the combined table's last row)."""
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    table = exposure.summary_block_table(result, RECORDS, fallback_ccys={"JPY"})
    assert table.id == exposure.SUMMARY_BLOCK_TABLE_ID
    rows = {r[exposure.ROW_LABEL_COL]: r for r in table.data}
    assert rows["Rate source"]["JPY"] == "BNP file"
    # the grid itself carries no rate / delta rows any more
    grid = exposure.combined_table(result, RECORDS).children[0]
    assert "Rate source" not in {r[exposure.ROW_LABEL_COL] for r in grid.data}


def test_futures_table_present_with_mark():
    futures = {"value": 100_000.0, "by_instrument": {"CLZ26 Comdty": 100_000.0}, "missing": [], "reason": ""}
    table = exposure.futures_table(futures)
    assert table.id == exposure.FUTURES_TABLE_ID
    assert table.data[0]["instrument"] == "CLZ26 Comdty"


def test_futures_table_unavailable_with_reason_when_no_mark():
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["CLZ26 Comdty"],
               "reason": "no FUTURE_PX on 2026-08-17 for CLZ26 Comdty"}
    table = exposure.futures_table(futures)
    assert table.data[0]["usd_delta"] == exposure.UNAVAILABLE
    assert "no FUTURE_PX" in table.tooltip_data[0]["usd_delta"]["value"]


def test_exposure_section_shows_fallback_note():
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, fallback_ccys={"JPY"})
    text = _render_text(section)
    assert "BNP file rate" in text
    assert "JPY" in text


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
    assert "Risk and scenarios" in text
    assert "Open futures" in text
    assert "Cash ladder: settled cash, spot, forwards, swaps and option deltas" in text


# --------------------------------------------------------------------------- settled cash row (2026-09-18)
def _rec(trade_id, ccy, amount, settle, pair="USDJPY", fill=147.0):
    return {"trade_id": trade_id, "settlement_date": settle, "book": "HAHY7", "book_source": "HAHY7",
            "currency": ccy, "local_amount": amount, "currency_pair": pair, "entry_rate": fill,
            "product_type": "FX_FWD", "settles_cash": 1}


def _find_id(node, wanted):
    """First component in the tree whose id == wanted (DataTable cells live in `data`,
    not in rendered children text)."""
    if getattr(node, "id", None) == wanted:
        return node
    for child in getattr(node, "children", None) or []:
        if hasattr(child, "children") or hasattr(child, "id"):
            hit = _find_id(child, wanted)
            if hit is not None:
                return hit
    return None


def _jpy_rate(rate=150.0, pair="USDJPY"):
    return {"JPY": {"rate": rate, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False,
                    "pair": pair}}


def test_combined_frame_settled_column_first_then_dates_then_total():
    """Transposed 2026-09-21: the settled cash (engine.ladder.exposure_adapter.SETTLED)
    is the FIRST value column, headed 'Settled cash', ahead of the value dates even
    though the engine's pivot sorts the sentinel last; its amounts and USD equivalent are
    the settled legs at spot, and the Local delta row of the block above sums settled
    and open together."""
    from engine.ladder.exposure import build_exposure
    from engine.ladder.exposure_adapter import SETTLED
    records = [_rec("s1", "JPY", 500_000.0, SETTLED), _rec("s1", "USD", -3_000.0, SETTLED),
               _rec("o1", "JPY", -147_000_000.0, "2026-09-25"), _rec("o1", "USD", 1_000_000.0, "2026-09-25")]
    result = build_exposure(records, _jpy_rate())
    frame, ccys = exposure.combined_frame(result, records)
    assert exposure.grid_value_columns(frame) == [SETTLED, "2026-09-25", exposure.TOTAL_COL]
    assert list(frame["kind"]) == ["currency", "currency", exposure.USD_EQUIVALENT_COL]
    assert list(frame[exposure.CURRENCY_COL]) == ccys + [""]
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["JPY", SETTLED] == 500_000 and by_label.loc["USD", SETTLED] == -3_000
    assert round(by_label.loc[exposure.USD_EQUIVALENT_ROW_LABEL, SETTLED]) == 333  # 500,000 / 150 - 3,000
    table, footer = exposure.combined_table(result, records).children   # the USD row is the pinned footer
    assert [c["name"] for c in table.columns] == ["Currency", exposure.SETTLED_ROW_LABEL, "25 Sep",
                                                  exposure.TOTAL_COLUMN_LABEL]
    assert [r["kind"] for r in table.data] == ["currency", "currency"]
    assert footer.data[0]["kind"] == exposure.USD_EQUIVALENT_COL and footer.columns == table.columns
    block, _ = exposure.summary_block_frame(result, records, rates=_jpy_rate())
    assert block.set_index(exposure.ROW_LABEL_COL).loc["Local delta", "JPY"] == -146_500_000


def test_summary_block_rate_row_shows_rate_as_quoted():
    """The rate is shown the way Bloomberg quotes it ('USDJPY 150'), not as a
    USD-per-local fraction ('0.006667'): a KRW mark stored at the wrong scale must be
    visible at a glance (2026-09-18, 'krw is wrong by a factor of 1000')."""
    from engine.ladder.exposure import build_exposure
    records = [_rec("o1", "JPY", -147_000_000.0, "2026-09-25"), _rec("o1", "USD", 1_000_000.0, "2026-09-25")]
    result = build_exposure(records, _jpy_rate(147.25))
    frame, _ = exposure.summary_block_frame(result, records, rates=_jpy_rate(147.25))
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert exposure.FX_RATE_ROW_LABEL == "FX rate (as quoted)"
    assert "Spot (as quoted)" not in by_label.index
    assert by_label.loc["FX rate (as quoted)", "JPY"] == "USDJPY 147.25"
    assert by_label.loc["FX rate (as quoted)", "USD"] == "1"
    assert by_label.loc["Rate source", "JPY"] == "Bloomberg"
    # without the quote dict the engine's USD-per-local rate is still shown, not blank
    frame, _ = exposure.summary_block_frame(result, records)
    assert frame.set_index(exposure.ROW_LABEL_COL).loc["FX rate (as quoted)", "JPY"] == "0.00679117"
    assert exposure.format_quoted_rate(1394.5) == "1,394.5"
    assert exposure.format_quoted_rate(0.66) == "0.66"
    assert exposure.format_quoted_rate(float("nan")) == ""


def test_summary_block_names_suspect_rate_in_rate_source_row():
    """A spot 1,000x away from the book's fills is not used: the USD delta cell is
    blank and the 'Rate source' row carries the engine's reason instead of 'Bloomberg'."""
    from engine.ladder.exposure import build_exposure
    records = [_rec("c1", "CNH", 7_142_500.0, "2026-11-18", pair="USDCNH", fill=7.1425),
               _rec("c1", "USD", -1_000_000.0, "2026-11-18", pair="USDCNH", fill=7.1425)]
    bad = {"CNH": {"rate": 7142.5, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "",
                   "stale": False, "pair": "USDCNH"}}
    result = build_exposure(records, bad)
    frame, _ = exposure.summary_block_frame(result, records, rates=bad)
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["USD delta", "CNH"] == ""
    assert by_label.loc["Rate source", "CNH"].startswith("SUSPECT: official SPOT USDCNH 7,142.5 is 1,000x away")
    assert by_label.loc["FX rate (as quoted)", "CNH"] == "USDCNH 7,142.5"
    # the full sentence is repeated under the block, where a table cell cannot cut it short
    assert "CNH: official SPOT USDCNH 7,142.5 is 1,000x away" in exposure.rate_reasons_caption(result).children


def test_settled_unknown_caption_lists_only_settlement_reasons():
    from engine.ladder.exposure_adapter import Unresolved
    none = exposure.settled_unknown_caption([Unresolved("f1", "CLZ26 Comdty", "non-FX product FUTURE excluded")])
    assert none is None
    settled = Unresolved("f2", "CLQ26 Comdty", "settled 2026-08-31 (FUTURE), USD settlement unknown: not "
                                               "realised yet -- no official mark on or before 2026-08-31")
    cap = exposure.settled_unknown_caption([settled, Unresolved("f1", "CLZ26 Comdty", "non-FX product FUTURE excluded")])
    assert cap.id == exposure.SETTLED_CAPTION_ID and len(cap.children) == 1
    text = _render_text(cap)
    assert "1 settled non-deliverable ticket not yet in Settled cash" in text and 'press "Pull Bloomberg now"' in text
    assert "CLQ26 Comdty (f2)" in text and "CLZ26" not in text
    section = exposure.exposure_section(RECORDS, [settled], "2026-08-17", rates=RATES)
    assert exposure.SETTLED_CAPTION_ID in _all_ids(section)


def test_render_shows_settled_cash_row_for_expired_ticket(tmp_path, monkeypatch):
    """End to end through the tab's own callback: the seeded USDJPY forward settled
    2026-08-20, so as of 2026-08-25 it must be on the ladder as Settled cash, not gone
    ("expired tickets must settle not disappear")."""
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
    matches = [v for k, v in app.callback_map.items() if "cash-ladder-table-container" in k]
    callback = matches[0]["callback"].__wrapped__
    from engine.ladder.exposure_adapter import SETTLED
    body = callback("2026-08-25", 0)
    assert "No open FX trades" not in _render_text(body)
    grid = _find_id(body, exposure.COMBINED_TABLE_ID)
    assert grid is not None
    grid_ids = [c["id"] for c in grid.columns]
    assert grid_ids[1:4] == ["fx_rate", "local_delta", "usd_delta"]   # the rate / delta figures lead each row
    assert grid_ids[4] == SETTLED                                     # first value column
    assert [c["name"] for c in grid.columns][4] == exposure.SETTLED_ROW_LABEL
    by_label = {r[exposure.ROW_LABEL_COL]: r for r in grid.data}
    assert by_label["JPY"][SETTLED] == -147_100_000 and by_label["USD"][SETTLED] == 1_000_000
    assert _find_id(body, exposure.SUMMARY_BLOCK_TABLE_ID) is None     # one table, no block above it
    assert by_label["JPY"]["fx_rate"] == "USDJPY 147.12"
    assert by_label["JPY"]["local_delta"] == -147_100_000


# --------------------------------------------------------------------------- 2026-09-21: one table
def test_exposure_section_folds_the_rate_delta_figures_into_the_grid_as_its_first_columns():
    """User, 2026-09-21: the rate / delta block as a table of its own above the grid was
    "super clunky with the other stuff above and scrolling odd". One table: FX rate, Local
    delta and USD delta lead each currency's row, the same strings the block frame holds,
    and the bottom USD row carries the net USD delta."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES)
    ids = _all_ids(section)
    assert exposure.SUMMARY_BLOCK_TABLE_ID not in ids
    assert ids.index(exposure.HEADLINE_ID) < ids.index(exposure.COMBINED_TABLE_ID)
    grid = _find_id(section, exposure.COMBINED_TABLE_ID)
    assert [(c["id"], c["name"]) for c in grid.columns][:4] == [
        (exposure.ROW_LABEL_COL, "Currency"), ("fx_rate", "FX rate"), ("local_delta", "Local delta"),
        ("usd_delta", "USD delta")]
    grid_currencies = [r[exposure.CURRENCY_COL] for r in grid.data if r["kind"] == "currency"]
    assert grid_currencies == ["JPY", "USD"]  # |USD delta| descending: 1,000,680 (147.1m / 147) then 1,000,000
    block, _ccys = exposure.summary_block_frame(build_exposure(RECORDS, RATES), RECORDS, rates=RATES)
    by_kind = block.set_index("kind")
    for row in grid.data:
        if row["kind"] == "currency":
            assert [row[k] for k in ("fx_rate", "local_delta", "usd_delta")] == [
                by_kind.at[k, row[exposure.CURRENCY_COL]] for k in ("fx_rate", "local_delta", "usd_delta")]
    jpy = next(r for r in grid.data if r[exposure.CURRENCY_COL] == "JPY")
    assert jpy["fx_rate"] == "147" and jpy["local_delta"] == -147_100_000   # RATES names no pair
    totals = portfolio_totals(build_exposure(RECORDS, RATES))
    footer = _find_id(section, exposure.COMBINED_TABLE_ID + "-footer")     # the USD row is pinned under the grid
    assert all(r["kind"] != exposure.USD_EQUIVALENT_COL for r in grid.data)
    usd_row = next(r for r in footer.data if r["kind"] == exposure.USD_EQUIVALENT_COL)
    assert usd_row["usd_delta"] == pytest.approx(totals["net_usd"])
    assert usd_row["fx_rate"] == "" and usd_row["local_delta"] == ""


def _cnh_records():
    return [_rec("c1", "CNH", 10_713_750.0, "2026-11-18", pair="USDCNH", fill=7.1425),
            _rec("c1", "USD", -1_500_000.0, "2026-11-18", pair="USDCNH", fill=7.1425),
            _rec("o1", "JPY", -147_000_000.0, "2026-09-25"), _rec("o1", "USD", 1_000_000.0, "2026-09-25")]


def _cnh_rate(rate=7.1425):
    return {"CNH": {"rate": rate, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False,
                    "pair": "USDCNH"}}


def test_every_currency_is_at_its_official_spot_under_its_plain_code():
    """2026-09-24 (the NDFs left the app): no row label, rate cell, caption or legend line
    speaks of NDFs or a 1M NDF price; every currency's rate is its official SPOT as quoted,
    under the plain currency code."""
    from engine.ladder.exposure import build_exposure
    records, rates = _cnh_records(), {**_jpy_rate(), **_cnh_rate()}
    result = build_exposure(records, rates)
    frame, ccys = exposure.combined_frame(result, records)
    assert list(frame[exposure.ROW_LABEL_COL])[:-1] == list(frame[exposure.CURRENCY_COL])[:-1] == ccys
    block, _ = exposure.summary_block_frame(result, records, rates=rates)
    by_label = block.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["FX rate (as quoted)", "CNH"] == "USDCNH 7.1425"
    assert by_label.loc["USD delta", "CNH"] == pytest.approx(1_500_000)
    assert by_label.loc["Rate source", "CNH"] == "Bloomberg"
    section = exposure.exposure_section(records, [], "2026-09-17", rates=rates)
    text = _render_text(section) + _render_text(exposure.legend())
    assert "NDF" not in text and "1M" not in text and "fixing" not in text
    assert not hasattr(exposure, "ndf_fixing_caption") and not hasattr(exposure, "NDF_BADGE")
    assert "1M NDF" not in exposure.usd_basis_caption({("CNH", "2026-11-18"): {"rate": 0.14, "basis": "outright"}}).children


def test_a_currency_with_no_spot_is_blank_with_its_reason():
    from engine.ladder.exposure import build_exposure
    records = _cnh_records()
    rates = _jpy_rate()                                     # no CNH spot on file
    result = build_exposure(records, rates)
    block, _ = exposure.summary_block_frame(result, records, rates=rates)
    by_label = block.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["FX rate (as quoted)", "CNH"] == ""
    assert by_label.loc["USD delta", "CNH"] == "" and by_label.loc["USD delta", exposure.TOTAL_COL] == ""
    assert by_label.loc["Local delta", "CNH"] == 10_713_750      # the position itself is still shown
    assert by_label.loc["Rate source", "CNH"].startswith("MISSING: ")
    assert "CNH: " in exposure.rate_reasons_caption(result).children
    card = exposure.headline_numbers(result).children[0]
    assert card.children[1].children == "Unavailable"
    assert card.children[2].children == "no official SPOT: CNH"
    assert exposure.missing_spot_reason([]) == ""
    assert exposure.missing_spot_reason(["JPY", "CNH", "JPY"], "2026-08-17") == "no official SPOT for 2026-08-17: CNH, JPY"


def _seed_cnh(conn, with_spot: bool):
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, "
        "expiry_date) VALUES ('USDCNH','FX','USD','CNH',1,0,'USDCNH Curncy','9999-12-31')")
    conn.execute(
        "INSERT INTO trades VALUES ('c1','XLSX','USDCNH','FX_FWD','c1','2026-08-03',1500000,7.1425,"
        "'PB-FX-NMMF','CPTY-B','','trader','desc','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("c1", 1, "FX_NEAR", "USD", -1_500_000, "2026-08-03", "2026-11-18", 7.1425, 1),
        ("c1", 2, "FX_NEAR", "CNH", 10_713_750, "2026-08-03", "2026-11-18", 7.1425, 1)])
    if with_spot:
        conn.execute("INSERT INTO marks VALUES ('2026-08-17','USDCNH','2026-08-17','SPOT',7.15,'BBG_BFXFORWARD',"
                     "'2026-08-17T15:00:00-04:00')")
    conn.commit()


def test_load_inputs_and_net_gross_usd_read_the_same_spot_rates(conn):
    """The Ladder tab's inputs and the header's Net / Gross USD delta read the SAME rates,
    rates_from_marks (official SPOT), so they agree; the grid dates each leg on its value date."""
    _seed_cnh(conn, with_spot=True)
    inputs = cash_ladder.load_inputs(conn, "2026-08-17")
    cnh = inputs["rates"]["CNH"]
    assert (cnh["rate"], cnh["pair"]) == (7.15, "USDCNH") and "mark_type" not in cnh
    assert {r["settlement_date"] for r in inputs["records"] if r["currency"] == "CNH"} == {"2026-11-18"}
    section = exposure.exposure_section(inputs["records"], inputs["unresolved"], "2026-08-17", rates=inputs["rates"],
                                        exposure_records=inputs["exposure_records"],
                                        forward_rates=inputs["forward_rates"])
    row = next(r for r in _find_id(section, exposure.COMBINED_TABLE_ID).data if r[exposure.CURRENCY_COL] == "CNH")
    assert row[exposure.ROW_LABEL_COL] == "CNH" and row["2026-11-18"] == 10_713_750
    assert row["fx_rate"] == "USDCNH 7.15"
    totals = cash_ladder.net_gross_usd(conn, "2026-08-17")
    assert totals["available"] is True
    # CNH +10,713,750 / 7.15 ; JPY -147,100,000 / 147.12 from the seed
    assert totals["net"] == pytest.approx(10_713_750 / 7.15 - 147_100_000.0 / 147.12)


def test_net_gross_usd_names_a_missing_spot(conn):
    _seed_cnh(conn, with_spot=False)
    assert "CNH" not in cash_ladder.load_inputs(conn, "2026-08-17")["rates"]
    totals = cash_ladder.net_gross_usd(conn, "2026-08-17")
    assert totals["available"] is False
    assert totals["reason"] == "no official SPOT for 2026-08-17: CNH"
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDJPY'")
    conn.commit()
    assert cash_ladder.net_gross_usd(conn, "2026-08-17")["reason"] == "no official SPOT for 2026-08-17: CNH, JPY"


def _futures_with_details():
    """engine.ladder.futures_delta.futures_usd_delta's shape (book-positions, 2026-09-24): a
    USD future, a CNY future at the USDCNY spot, and a JPY future with a price but no spot."""
    s_cny = 1 / 7.10
    details = {
        "CLZ26 Comdty": {"contracts": 10.0, "multiplier": 1000.0, "price": 68.5, "expiry": "2026-11-20",
                         "source": "BBG_BDH", "currency": "USD", "usd_per_unit": 1.0, "reason": ""},
        "CUX26 Comdty": {"contracts": -30.0, "multiplier": 5.0, "price": 78_450.0, "expiry": "2026-11-16",
                         "source": "BBG_BDH", "currency": "CNY", "usd_per_unit": s_cny, "reason": ""},
        "JGZ26 Comdty": {"contracts": 3.0, "multiplier": 1000.0, "price": 15_420.0, "expiry": "2026-12-24",
                         "source": "BBG_BDH", "currency": "JPY", "usd_per_unit": None,
                         "reason": "no SPOT for JPY on 2026-09-17"},
    }
    return {"value": float("nan"),
            "by_instrument": {"CLZ26 Comdty": 685_000.0, "CUX26 Comdty": -30 * 5 * 78_450.0 * s_cny},
            "missing": ["JGZ26 Comdty"], "reason": "no SPOT for JPY on 2026-09-17 (JGZ26 Comdty)", "details": details}


def test_futures_table_shows_each_futures_currency_beside_its_price_and_its_conversion():
    futures = _futures_with_details()
    table = exposure.futures_table(futures)       # details read from the dict itself
    names = [c["name"] for c in table.columns]
    assert names == ["Instrument", "Contracts", "Multiplier", "Settlement price", "Currency",
                     "USD per unit (spot)", "USD delta"]
    rows = {r["instrument"]: r for r in table.data}
    assert (rows["CLZ26 Comdty"]["currency"], rows["CLZ26 Comdty"]["usd_per_unit"]) == ("USD", 1.0)
    cu = rows["CUX26 Comdty"]
    assert cu["settlement_price"] == 78_450.0 and cu["currency"] == "CNY"
    assert cu["usd_per_unit"] == pytest.approx(1 / 7.10)
    assert cu["usd_delta"] == pytest.approx(-30 * 5 * 78_450.0 / 7.10)     # the engine's figure, as it stands
    # a price but no conversion spot: the price and currency shown, the conversion n/a with the reason
    i = next(i for i, r in enumerate(table.data) if r["instrument"] == "JGZ26 Comdty")
    jg = table.data[i]
    assert jg["settlement_price"] == 15_420.0 and jg["currency"] == "JPY"
    assert jg["usd_per_unit"] is None and jg["usd_delta"] == exposure.UNAVAILABLE
    assert table.tooltip_data[i]["usd_per_unit"]["value"] == "no SPOT for JPY on 2026-09-17"
    assert "no SPOT for JPY" in table.tooltip_data[i]["usd_delta"]["value"]
    assert "settlement_price" not in table.tooltip_data[i]              # the price is there: nothing to explain
    fmt = {c["id"]: c.get("format", {}) for c in table.columns}
    assert fmt["usd_per_unit"]["nully"] == "n/a"                        # a missing conversion is never shown as zero


def test_risk_table_names_each_futures_own_reason_and_applies_no_equity_move():
    """The scenarios are FX moves (the equity index's futures line left engine.pnl.stress,
    2026-09-24): a commodity future's scenario cells are blank with the reason on hover,
    whatever other key a scenario carries."""
    from engine.ladder.exposure import build_exposure
    futures = _futures_with_details()
    scenarios = {"Risk off": {"JPY": 0.05, "EQUITY": -0.10}}
    result = build_exposure(RECORDS, RATES)
    frame = exposure.combined_risk_frame(result, futures, scenarios=scenarios)
    jg = frame[frame[exposure.RISK_LABEL_COL] == "JGZ26 Comdty"].iloc[0]
    assert jg["_unavailable"] == "no SPOT for JPY on 2026-09-17"          # its own reason, not the total's
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, futures=futures)
    table = _find_id(section, exposure.RISK_TABLE_ID)
    i = next(i for i, r in enumerate(table.data) if r[exposure.RISK_LABEL_COL] == "CLZ26 Comdty")
    names = [c["id"] for c in table.columns][3:]
    assert names, "config/stress.yaml has scenarios"
    for name in names:
        assert table.data[i][name] == ""
        assert table.tooltip_data[i][name]["value"] == exposure.FUTURES_NO_SCENARIO
    assert _find_id(section, exposure.FUTURES_SCENARIO_CAPTION_ID) is not None
    # a currency row is still moved by the scenarios exactly as before
    jpy = next(r for r in table.data if r[exposure.RISK_LABEL_COL] == "JPY")
    assert any(jpy[n] not in ("", None) for n in names)


def test_scenario_columns_follow_config_order_not_alphabetical():
    """Scenario columns must render in config/stress.yaml's own order (user decision
    2026-09-15, item 3), not alphabetically."""
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    scenarios = {"Zeta scenario": {"JPY": 0.01}, "Alpha scenario": {"JPY": -0.01}}
    frame = exposure.combined_risk_frame(result, scenarios=scenarios)
    cols = list(frame.columns)
    assert cols.index("Zeta scenario") < cols.index("Alpha scenario")


def test_headline_card_shows_net_under_gross():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    headline = exposure.headline_numbers(result)
    card = headline.children[0]
    net_span = card.children[2]
    assert net_span.children[0].children == "net "


def test_headline_merges_currencies_and_futures_with_no_open_futures():
    """No open futures (0.0, per DEFAULT_FUTURES/no_open_futures) still merges cleanly
    into the one Delta card: gross/net equal the currency-only totals, no separate
    "no open futures" card is shown any more (merged 2026-09-15)."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    result = build_exposure(RECORDS, RATES)
    headline = exposure.headline_numbers(result)
    totals = portfolio_totals(result)
    card = headline.children[0]
    assert card.children[1].children == exposure.format_amount(totals["gross_usd"])


def _stress_covered_currencies():
    from engine.pnl.stress import load_scenarios
    covered = set()
    for moves in load_scenarios().values():
        covered.update(moves)
    return covered


def test_stress_yaml_covers_every_book_currency():
    """The FX currencies of Jason's book (the sample blotter's FX hedges and FX options,
    2026-09-24) each have a move in config/stress.yaml; CNH is pinned on its own below."""
    book_currencies = {"EUR", "GBP", "JPY", "XAU"}
    missing = book_currencies - _stress_covered_currencies()
    assert not missing, f"currencies with no scenario coverage: {missing}"


def test_stress_yaml_covers_cnh():
    assert "CNH" in _stress_covered_currencies()


def test_ledger_tab_module_removed():
    with pytest.raises(ModuleNotFoundError):
        import importlib
        importlib.import_module("ui.tabs.ledger")


# --------------------------------------------------------------------------- reconciliation_panel

def _add_blotter_trade(conn, trade_id, instrument_id, quantity, usd_amount, trade_date="2026-08-01"):
    conn.execute(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, "XLSX", instrument_id, "FX_FWD", trade_id, trade_date, quantity, 1.0,
         "BNPP-IPBFX-NMMF", "BNP", "HAHY7", "trader", "desc", ""))
    conn.execute(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_id, 1, "FX_NEAR", "USD", usd_amount, trade_date, "2026-08-20", 1.0, 1))
    conn.commit()




def test_today_ny_is_the_books_one_day_boundary():
    """User, 2026-09-22: "all daily pnl is calculated from the NY 3pm the day before. I am
    based in HK, so basically all date rollover at hkt 5am". The screens' today is
    data.bloomberg.live.book_today, the same function every pull, the backfill and the
    ledger use, so the top bar and the marks can never disagree on the day again: at 16:59
    New York the 22nd, at 17:00 the 23rd, and 05:00 Hong Kong is that same 17:00 boundary."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from data.bloomberg import live

    ny, hk = ZoneInfo("America/New_York"), ZoneInfo("Asia/Hong_Kong")
    assert cash_ladder.ROLLOVER_HOUR_NY == live.ROLLOVER_HOUR_NY == 17
    for when in (
        datetime(2026, 9, 22, 16, 59, tzinfo=ny),
        datetime(2026, 9, 22, 17, 0, tzinfo=ny),
        datetime(2026, 9, 23, 4, 59, tzinfo=hk),     # 16:59 New York the 22nd: still the 22nd
        datetime(2026, 9, 23, 5, 0, tzinfo=hk),      # 17:00 New York the 22nd: the 23rd
    ):
        assert cash_ladder.today_ny(when) == live.book_today(when).isoformat()
    assert cash_ladder.today_ny(datetime(2026, 9, 22, 16, 59, tzinfo=ny)) == "2026-09-22"
    assert cash_ladder.today_ny(datetime(2026, 9, 22, 17, 0, tzinfo=ny)) == "2026-09-23"
    assert cash_ladder.today_ny(datetime(2026, 9, 23, 4, 59, tzinfo=hk)) == "2026-09-22"
    assert cash_ladder.today_ny(datetime(2026, 9, 23, 5, 0, tzinfo=hk)) == "2026-09-23"
    # no argument: the live clock, and still the same function's answer
    assert cash_ladder.today_ny() == live.book_today().isoformat()


# --------------------------------------------------------------------------- Phase 3: Net / Gross USD are FX only

def test_phase3_headline_and_risk_gross_leave_priced_futures_out_but_the_futures_table_lists_them():
    """CLAUDE.md "Net USD": commodity futures are positions on the Curve tab, not in Net /
    Gross USD. The headline card and the risk table's Gross are FX only; the open futures
    table still shows each future with its USD delta, and the line under it says where the
    commodity positions and scenarios are."""
    from engine.ladder.exposure import build_exposure, portfolio_totals
    futures = _futures_with_details()
    futures["value"] = sum(futures["by_instrument"].values())      # a priced total, to be left out
    totals = portfolio_totals(build_exposure(RECORDS, RATES))
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, futures=futures)

    card = _find_id(section, exposure.HEADLINE_ID).children[0]
    assert card.children[0].children == "USD delta, FX only"
    assert card.children[1].children == exposure.format_amount(totals["gross_usd"])
    assert card.children[2].children[1].children == exposure.format_amount(-totals["net_usd"])

    footer = _find_id(section, exposure.RISK_TABLE_ID + "-footer")
    rows = {r[exposure.RISK_LABEL_COL]: r for r in footer.data}
    assert rows[exposure.GROSS_FOOTER_LABEL]["usd_delta"] == pytest.approx(totals["gross_usd"])
    assert rows[exposure.NET_FOOTER_LABEL]["usd_delta"] == pytest.approx(-totals["net_usd"])

    table = _find_id(section, exposure.FUTURES_TABLE_ID)
    by_id = {r["instrument"]: r for r in table.data}
    assert by_id["CLZ26 Comdty"]["usd_delta"] == pytest.approx(685_000.0)
    assert by_id["CUX26 Comdty"]["usd_delta"] == pytest.approx(-30 * 5 * 78_450.0 / 7.10)

    text = _render_text(section)
    assert "Commodity positions by contract month: see the Curve tab." in text
    assert "Commodity scenarios: see the Risk tab." in text
    ids = _all_ids(section)
    assert ids.index(exposure.FUTURES_TABLE_ID) < ids.index(exposure.FUTURES_NOTE_ID) < ids.index(exposure.RISK_TABLE_ID)


def test_phase3_risk_gross_unavailable_names_only_the_missing_rate():
    """A future with no price no longer blanks the Gross: only a missing FX rate does."""
    from engine.ladder.exposure import build_exposure
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["CLZ26 Comdty"],
               "reason": "no FUTURE_PX on 2026-08-17 for CLZ26 Comdty"}
    table = exposure.combined_risk_table(build_exposure(RECORDS, RATES), futures, scenarios={})
    footer = table.children[1].children[1]
    gross = next(r for r in footer.data if r[exposure.RISK_LABEL_COL] == exposure.GROSS_FOOTER_LABEL)
    assert gross["usd_delta"] != exposure.UNAVAILABLE
    no_rate = exposure.combined_risk_table(build_exposure(RECORDS, {}), futures, scenarios={})
    footer, tips = no_rate.children[1].children[1], no_rate.children[1].children[1].tooltip_data
    i = next(i for i, r in enumerate(footer.data) if r[exposure.RISK_LABEL_COL] == exposure.GROSS_FOOTER_LABEL)
    assert footer.data[i]["usd_delta"] == exposure.UNAVAILABLE
    assert "no rate: " in tips[i]["usd_delta"]["value"] and "FUTURE_PX" not in tips[i]["usd_delta"]["value"]


def test_phase3_futures_scenario_hover_points_to_the_risk_tab():
    assert exposure.FUTURES_NO_SCENARIO.endswith("commodity scenarios: see the Risk tab")
    assert "not built yet" not in exposure.FUTURES_NO_SCENARIO
