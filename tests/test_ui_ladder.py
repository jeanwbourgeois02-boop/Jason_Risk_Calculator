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
    assert labels == ["Delta (FX + futures)"]


def test_headline_numbers_unavailable_without_rate():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, {})
    headline = exposure.headline_numbers(result)
    card = headline.children[0]
    assert card.children[1].children == "Unavailable"


def test_headline_futures_unavailable_with_reason():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["ESU6 Index"],
               "reason": "no FUTURE_PX on 2026-08-17 for ESU6 Index"}
    headline = exposure.headline_numbers(result, futures)
    card = headline.children[0]
    assert card.children[1].children == "Unavailable"
    assert "no FUTURE_PX" in card.children[2].children


def test_exposure_section_has_headline_and_three_tables_only():
    futures = {"value": 1.0, "by_instrument": {"ESU6 Index": 1.0}, "missing": [], "reason": ""}
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
    futures = {"value": 1.0, "by_instrument": {"ESU6 Index": 1.0}, "missing": [], "reason": ""}
    section = exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES, futures=futures)
    ids = _all_ids(section)
    assert ids.index(exposure.HEADLINE_ID) < ids.index(exposure.COMBINED_TABLE_ID)
    assert ids.index(exposure.COMBINED_TABLE_ID) < ids.index(exposure.FUTURES_TABLE_ID)
    assert ids.index(exposure.FUTURES_TABLE_ID) < ids.index(exposure.RISK_TABLE_ID)


def test_combined_risk_frame_futures_row_present_with_mark():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": 100_000.0, "by_instrument": {"ESU6 Index": 100_000.0}, "missing": [], "reason": ""}
    frame = exposure.combined_risk_frame(result, futures, scenarios={})
    row = frame[frame[exposure.RISK_LABEL_COL] == "ESU6 Index"].iloc[0]
    assert row["usd_delta"] == 100_000.0
    assert row["_unavailable"] == ""


def test_combined_risk_frame_futures_row_unavailable_with_reason():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["ESU6 Index"],
               "reason": "no FUTURE_PX on 2026-08-17 for ESU6 Index"}
    frame = exposure.combined_risk_frame(result, futures, scenarios={})
    row = frame[frame[exposure.RISK_LABEL_COL] == "ESU6 Index"].iloc[0]
    assert pd.isna(row["usd_delta"])
    assert "no FUTURE_PX" in row["_unavailable"]
    table = exposure.combined_risk_table(result, futures, scenarios={})
    inner = table.children[1]
    row = next(r for r in inner.data if r[exposure.RISK_LABEL_COL] == "ESU6 Index")
    assert "Unavailable" in row["usd_delta"]
    assert "no FUTURE_PX" in row["usd_delta"]


def test_combined_risk_table_net_excludes_futures_gross_includes():
    from engine.ladder.exposure import build_exposure, portfolio_totals
    result = build_exposure(RECORDS, RATES)
    totals = portfolio_totals(result)
    futures = {"value": 100_000.0, "by_instrument": {"ESU6 Index": 100_000.0}, "missing": [], "reason": ""}
    table = exposure.combined_risk_table(result, futures, scenarios={})
    inner = table.children[1]
    rows = {r[exposure.RISK_LABEL_COL]: r for r in inner.data}
    net_formatted = exposure.format_amount(-totals["net_usd"])  # USD position: + = long USD, as the header
    gross_formatted = exposure.format_amount(totals["gross_usd"] + 100_000.0)
    assert rows["Net USD delta, FX only (+ = long USD)"]["usd_delta"] == net_formatted
    assert rows["Gross delta (incl. |futures|)"]["usd_delta"] == gross_formatted


def test_combined_risk_table_marks_fallback_currency():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    table = exposure.combined_risk_table(result, fallback_ccys={"JPY"})
    inner = table.children[1]
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
    grid = exposure.combined_table(result, RECORDS)
    assert "Rate source" not in {r[exposure.ROW_LABEL_COL] for r in grid.data}


def test_futures_table_present_with_mark():
    futures = {"value": 100_000.0, "by_instrument": {"ESU6 Index": 100_000.0}, "missing": [], "reason": ""}
    table = exposure.futures_table(futures)
    assert table.id == exposure.FUTURES_TABLE_ID
    assert table.data[0]["instrument"] == "ESU6 Index"


def test_futures_table_unavailable_with_reason_when_no_mark():
    futures = {"value": float("nan"), "by_instrument": {}, "missing": ["ESU6 Index"],
               "reason": "no FUTURE_PX on 2026-08-17 for ESU6 Index"}
    table = exposure.futures_table(futures)
    assert "no FUTURE_PX" in table.data[0]["usd_delta"]


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
    assert by_label.loc["JPY", SETTLED] == "500,000" and by_label.loc["USD", SETTLED] == "(3,000)"
    assert by_label.loc[exposure.USD_EQUIVALENT_ROW_LABEL, SETTLED] == "333"  # 500,000 / 150 - 3,000
    table = exposure.combined_table(result, records)
    assert [c["name"] for c in table.columns] == ["Currency", exposure.SETTLED_ROW_LABEL, "25 Sep",
                                                  exposure.TOTAL_COLUMN_LABEL]
    block, _ = exposure.summary_block_frame(result, records, rates=_jpy_rate())
    assert block.set_index(exposure.ROW_LABEL_COL).loc["Local delta", "JPY"] == "(146,500,000)"


def test_summary_block_rate_row_shows_rate_as_quoted():
    """The rate is shown the way Bloomberg quotes it ('USDJPY 150'), not as a
    USD-per-local fraction ('0.006667'): a KRW mark stored at the wrong scale must be
    visible at a glance (2026-09-18, 'krw is wrong by a factor of 1000'). The row is
    'FX rate (as quoted)' since 2026-09-21: for an NDF currency it is not a spot."""
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
    records = [_rec("k1", "KRW", 1_413_138_000.0, "2026-09-21", pair="USDKRW", fill=1413.138),
               _rec("k1", "USD", -1_000_000.0, "2026-09-21", pair="USDKRW", fill=1413.138)]
    bad = {"KRW": {"rate": 1.3945, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "",
                   "stale": False, "pair": "USDKRW"}}
    result = build_exposure(records, bad)
    frame, _ = exposure.summary_block_frame(result, records, rates=bad)
    by_label = frame.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["USD delta", "KRW"] == ""
    assert by_label.loc["Rate source", "KRW"].startswith("SUSPECT: official SPOT USDKRW 1.3945 is 1,013x away")
    assert by_label.loc["FX rate (as quoted)", "KRW"] == "USDKRW 1.3945"
    # the full sentence is repeated under the block, where a table cell cannot cut it short
    assert "KRW: official SPOT USDKRW 1.3945 is 1,013x away" in exposure.rate_reasons_caption(result).children


def test_settled_unknown_caption_lists_only_settlement_reasons():
    from engine.ladder.exposure_adapter import Unresolved
    none = exposure.settled_unknown_caption([Unresolved("f1", "ESU6 Index", "non-FX product FUTURE excluded")])
    assert none is None
    cap = exposure.settled_unknown_caption([
        Unresolved("n1", "USDKRW", "settled 2026-09-16 (FX_FWD), USD settlement unknown: not realised yet"),
        Unresolved("f1", "ESU6 Index", "non-FX product FUTURE excluded"),
    ])
    text = _render_text(cap)
    assert "1 settled non-deliverable ticket not yet in Settled cash" in text
    assert "USDKRW (n1)" in text and "ESU6" not in text
    section = exposure.exposure_section(RECORDS, [
        Unresolved("n1", "USDKRW", "settled 2026-09-16 (FX_FWD), USD settlement unknown: not realised yet")],
        "2026-08-17", rates=RATES)
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
    assert by_label["JPY"][SETTLED] == "(147,100,000)" and by_label["USD"][SETTLED] == "1,000,000"
    assert _find_id(body, exposure.SUMMARY_BLOCK_TABLE_ID) is None     # one table, no block above it
    assert by_label["JPY"]["fx_rate"] == "USDJPY 147.12"
    assert by_label["JPY"]["local_delta"] == "(147,100,000)"


# --------------------------------------------------------------------------- 2026-09-21: one table, NDF display
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
    assert jpy["fx_rate"] == "147" and jpy["local_delta"] == "(147,100,000)"   # RATES names no pair
    totals = portfolio_totals(build_exposure(RECORDS, RATES))
    usd_row = next(r for r in grid.data if r["kind"] == exposure.USD_EQUIVALENT_COL)
    assert usd_row["usd_delta"] == exposure.format_amount(totals["net_usd"])
    assert usd_row["fx_rate"] == "" and usd_row["local_delta"] == ""


def _ndf_records():
    return [_rec("k1", "KRW", -1_394_500_000.0, "2026-09-17", pair="USDKRW", fill=1394.5),
            _rec("k1", "USD", 1_000_000.0, "2026-09-17", pair="USDKRW", fill=1394.5),
            _rec("o1", "JPY", -147_000_000.0, "2026-09-25"), _rec("o1", "USD", 1_000_000.0, "2026-09-25")]


def _ndf_rates():
    """The shape engine.ladder.ndf.apply_ndf_1m_rates gives an NDF currency."""
    return {**_jpy_rate(),
            "KRW": {"rate": 1394.5, "inverted": True, "source": "BBG_BFXFORWARD", "timestamp": "", "stale": False,
                    "pair": "USDKRW", "mark_type": "NDF_1M", "ticker": "KWN+1M Curncy", "label": "KWN+1M"}}


def test_ndf_currency_row_label_rate_cell_and_fixing_caption():
    from engine.ladder.exposure import build_exposure
    from engine.ladder.ndf import FIXING_CAPTION
    from engine.ladder.usd_marks import BASIS_NDF_1M
    records, rates = _ndf_records(), _ndf_rates()
    result = build_exposure(records, rates)
    frame, ccys = exposure.combined_frame(result, records)
    labels = dict(zip(frame[exposure.CURRENCY_COL], frame[exposure.ROW_LABEL_COL]))
    assert labels["KRW"] == "KRW (NDF)" and labels["JPY"] == "JPY" and labels["USD"] == "USD"
    # the label never reaches an id or the filter: block columns and the currency filter use the plain code
    block, block_ccys = exposure.summary_block_frame(result, records, rates=rates)
    assert "KRW" in block.columns and block_ccys == ccys
    by_label = block.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["FX rate (as quoted)", "KRW"] == "KWN+1M 1,394.5"
    assert by_label.loc["FX rate (as quoted)", "JPY"] == "USDJPY 150"
    assert by_label.loc["USD delta", "KRW"] == "(1,000,000)"          # / 1,394.5, the 1M NDF price
    assert by_label.loc["Rate source", "KRW"] == "Bloomberg 1M NDF"
    only_krw = exposure.grid_records(records, exposure.LadderView(currencies=frozenset({"KRW"})))
    assert {r["currency"] for r in only_krw} == {"KRW"}
    filtered, _ = exposure.combined_frame(build_exposure(only_krw, rates), only_krw)
    assert list(filtered[exposure.CURRENCY_COL]) == ["KRW", ""]
    # the one-line caption under the grid, only when the book holds an NDF currency
    section = exposure.exposure_section(records, [], "2026-08-17", rates=rates)
    ids = _all_ids(section)
    assert ids.index(exposure.COMBINED_TABLE_ID) < ids.index(exposure.NDF_CAPTION_ID)
    assert _find_id(section, exposure.NDF_CAPTION_ID).children == FIXING_CAPTION
    assert exposure.NDF_CAPTION_ID not in _all_ids(exposure.exposure_section(RECORDS, [], "2026-08-17", rates=RATES))
    # the USD-basis line names the currencies valued on the 1M NDF price
    fwd = {("KRW", "2026-09-17"): {"rate": 1 / 1394.5, "basis": BASIS_NDF_1M},
           ("JPY", "2026-09-25"): {"rate": 1 / 149.0, "basis": "outright"}}
    text = exposure.usd_basis_caption(fwd).children
    assert "valued at Bloomberg's 1M NDF price on every date" in text and text.rstrip(".").endswith("never at spot: KRW")
    assert "1M NDF" not in exposure.usd_basis_caption({("JPY", "2026-09-25"): {"rate": 1 / 149.0, "basis": "outright"}}).children
    legend = _render_text(exposure.legend())
    assert "an NDF currency uses Bloomberg's 1M NDF price instead, never spot" in legend and "KRW KWN+1M" in legend


def test_ndf_currency_with_no_1m_price_is_blank_with_its_reason_never_spot():
    """CLAUDE.md hard rule 2: engine.ladder.ndf.apply_ndf_1m_rates gives an NDF currency
    with no 1M NDF mark NO rate entry, even with an official SPOT on file. The tab shows
    blanks and the engine's reason, never a spot-valued number."""
    from engine.ladder.exposure import build_exposure
    records = _ndf_records()
    rates = _jpy_rate()                                     # no KRW entry: its 1M price is missing
    result = build_exposure(records, rates)
    block, _ = exposure.summary_block_frame(result, records, rates=rates)
    by_label = block.set_index(exposure.ROW_LABEL_COL)
    assert by_label.loc["FX rate (as quoted)", "KRW"] == ""
    assert by_label.loc["USD delta", "KRW"] == "" and by_label.loc["USD delta", exposure.TOTAL_COL] == ""
    assert by_label.loc["Local delta", "KRW"] == "(1,394,500,000)"      # the position itself is still shown
    assert by_label.loc["Rate source", "KRW"].startswith("MISSING: the 1M NDF price for KRW (KWN+1M) is missing")
    reasons = exposure.rate_reasons_caption(result).children
    assert 'KRW: the 1M NDF price for KRW (KWN+1M) is missing: press "Pull Bloomberg now"' in reasons
    assert "never valued at spot" in reasons and "JPY" not in reasons
    assert exposure.rate_reasons_caption(build_exposure(records, _ndf_rates())) is None
    frame, _ = exposure.combined_frame(result, records)
    usd_row = frame.set_index(exposure.ROW_LABEL_COL).loc[exposure.USD_EQUIVALENT_ROW_LABEL]
    assert usd_row["2026-09-17"] == "" and usd_row[exposure.TOTAL_COL] == ""   # blank, never a partial sum
    assert usd_row["2026-09-25"] == "20,000"                                     # -147m / 150 + 1m: JPY is priced
    card = exposure.headline_numbers(result).children[0]
    assert card.children[1].children == "Unavailable"
    assert "the 1M NDF price is missing for KRW (KWN+1M)" in card.children[2].children
    section = exposure.exposure_section(records, [], "2026-08-17", rates=rates)
    ids = _all_ids(section)
    assert ids.index(exposure.RATE_REASONS_ID) < ids.index(exposure.COMBINED_TABLE_ID)
    krw = next(r for r in _find_id(section, exposure.COMBINED_TABLE_ID).data if r[exposure.CURRENCY_COL] == "KRW")
    assert krw["fx_rate"] == "" and krw["usd_delta"] == ""                       # blank in the grid too, never spot


def _seed_ndf(conn, with_1m_price: bool):
    conn.execute(
        "INSERT INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, "
        "expiry_date) VALUES ('USDKRW','FX','USD','KRW',1,1,'USDKRW Curncy','9999-12-31')")
    conn.execute(
        "INSERT INTO trades VALUES ('k1','XLSX','USDKRW','FX_FWD','k1','2026-08-03',1000000,1394.5,"
        "'BNPP-IPBFX-NMMF','BNP','','trader','desc','')")
    conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", [
        ("k1", 1, "FX_NEAR", "USD", 1_000_000, "2026-08-03", "2026-09-21", 1394.5, 1),
        ("k1", 2, "FX_NEAR", "KRW", -1_394_500_000, "2026-08-03", "2026-09-21", 1394.5, 0)])
    stamp = "2026-08-17T17:00:00-04:00"
    conn.execute("INSERT INTO marks VALUES ('2026-08-17','USDKRW','2026-08-17','SPOT',1380.0,'BBG_BFXFORWARD',?)", (stamp,))
    if with_1m_price:
        conn.execute("INSERT INTO marks VALUES ('2026-08-17','USDKRW','2026-08-17','NDF_1M',1394.5,'BBG_BFXFORWARD',?)",
                     (stamp,))
    conn.commit()


def test_load_inputs_and_net_gross_usd_price_ndf_currencies_at_the_1m_ndf_mark(conn):
    """ui/tabs/cash_ladder.py: the Ladder tab's inputs and the header's Net / Gross USD
    delta read the SAME rates, apply_ndf_1m_rates(rates_from_marks), so they agree: KRW at
    KWN+1M 1,394.5, not at the 1,380 spot that is also on file."""
    from engine.ladder.ndf import fixing_date
    from engine.ladder.usd_marks import BASIS_NDF_1M
    _seed_ndf(conn, with_1m_price=True)
    inputs = cash_ladder.load_inputs(conn, "2026-08-17")
    krw = inputs["rates"]["KRW"]
    assert (krw["rate"], krw["mark_type"], krw["label"]) == (1394.5, "NDF_1M", "KWN+1M")
    fixing = fixing_date("2026-09-21")
    assert {r["settlement_date"] for r in inputs["records"] if r["currency"] == "KRW"} == {fixing}
    assert inputs["forward_rates"][("KRW", fixing)]["basis"] == BASIS_NDF_1M
    section = exposure.exposure_section(inputs["records"], inputs["unresolved"], "2026-08-17", rates=inputs["rates"],
                                        exposure_records=inputs["exposure_records"],
                                        forward_rates=inputs["forward_rates"])
    grid = _find_id(section, exposure.COMBINED_TABLE_ID)
    row = next(r for r in grid.data if r[exposure.CURRENCY_COL] == "KRW")
    assert row[exposure.ROW_LABEL_COL] == "KRW (NDF)" and row[fixing] == "(1,394,500,000)"
    assert row["fx_rate"] == "KWN+1M 1,394.5"
    totals = cash_ladder.net_gross_usd(conn, "2026-08-17")
    assert totals["available"] is True
    # KRW -1,394,500,000 / 1,394.5 = -1,000,000 (at the 1,380 spot it would be -1,010,507); JPY from the seed
    assert totals["net"] == pytest.approx(-1_000_000.0 - 147_100_000.0 / 147.12)


def test_net_gross_usd_names_a_missing_1m_ndf_price_and_keeps_the_spot_wording(conn):
    _seed_ndf(conn, with_1m_price=False)                     # an official USDKRW SPOT is on file; it must not be used
    assert "KRW" not in cash_ladder.load_inputs(conn, "2026-08-17")["rates"]
    totals = cash_ladder.net_gross_usd(conn, "2026-08-17")
    assert totals["available"] is False
    assert totals["reason"] == ('the 1M NDF price is missing for KRW (KWN+1M): press "Pull Bloomberg now" to fetch '
                                "it (NDF currencies are never valued at spot)")
    conn.execute("DELETE FROM marks WHERE instrument_id = 'USDJPY'")
    conn.commit()
    reason = cash_ladder.net_gross_usd(conn, "2026-08-17")["reason"]
    assert reason.startswith("no official SPOT for 2026-08-17: JPY; the 1M NDF price is missing for KRW")


def test_settled_unknown_caption_words_a_fixed_ndf_apart_from_an_unmarked_settlement():
    """An NDF that has fixed is waiting for its value date, not for a Bloomberg pull."""
    from engine.ladder.exposure_adapter import NDF_FIXED_REASON_PREFIX, Unresolved
    fixed = Unresolved("n1", "USDKRW", f"{NDF_FIXED_REASON_PREFIX} on 2026-09-17 (FX_FWD NDF, value date 2026-09-21), "
                                       "USD settlement unknown: not realised yet")
    text = _render_text(exposure.settled_unknown_caption([fixed]))
    assert "1 NDF ticket has fixed and is waiting for the value date" in text
    assert "no Bloomberg pull is needed" in text and "USDKRW (n1, value date 21 Sep 2026)" in text
    assert "not yet in Settled cash" not in text
    unmarked = Unresolved("n2", "USDTWD", "settled 2026-09-16 (FX_FWD), USD settlement unknown: not realised yet")
    cap = exposure.settled_unknown_caption([fixed, unmarked])
    assert cap.id == exposure.SETTLED_CAPTION_ID and len(cap.children) == 2
    text = _render_text(cap)
    assert "1 settled non-deliverable ticket not yet in Settled cash" in text and "USDTWD (n2)" in text
    assert 'press "Pull Bloomberg now"' in text and "1 NDF ticket has fixed" in text
    # a fixed NDF named under an empty grid still gets the fixing-dates line
    section = exposure.exposure_section([], [fixed], "2026-09-18", rates={})
    assert exposure.NDF_CAPTION_ID in _all_ids(section)


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


def test_stress_yaml_covers_every_book_currency():
    from engine.pnl.stress import load_scenarios
    scenarios = load_scenarios()
    covered = set()
    for moves in scenarios.values():
        covered.update(ccy for ccy in moves if ccy != "EQUITY")
    book_currencies = {"AUD", "CAD", "CHF", "EUR", "GBP", "HKD", "IDR", "INR", "JPY",
                       "KRW", "MXN", "NOK", "NZD", "SEK", "SGD", "TRY", "TWD", "XAU", "ZAR"}
    missing = book_currencies - covered
    assert not missing, f"currencies with no scenario coverage: {missing}"


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


