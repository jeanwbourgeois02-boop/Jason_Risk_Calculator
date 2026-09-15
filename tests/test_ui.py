"""Tests for ui/app.py (Dash skeleton, six placeholder tabs).

All tests skip if dash is not importable, per environment constraints.
"""
from __future__ import annotations

import pandas as pd
import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import cash_ladder, pnl  # noqa: E402


def _seed(conn):
    conn.execute(
        "INSERT INTO instruments VALUES ('USDJPY','FX','USD','JPY',1,0,'USDJPY Curncy','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO trades VALUES "
        "('t1','BNP','USDJPY','FX_SPOT','t1','2026-08-17',1000000,147.10,"
        "'BNPP-IPBFX-NMMF','BNP','HAHY7','trader','desc')"
    )
    conn.executemany(
        "INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("t1", 1, "FX_NEAR", "USD", 1000000, "2026-08-17", "2026-08-17", 147.10, 1),
            ("t1", 2, "FX_NEAR", "JPY", -147100000, "2026-08-17", "2026-08-17", 147.10, 1),
        ],
    )
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-17','USDJPY','2026-08-17','SPOT',147.12,'BBG_BFXFORWARD','2026-08-17T15:00:00-04:00')"
    )
    conn.execute(
        "INSERT INTO positions VALUES "
        "('2026-08-17','BNP','BNPP-IPBFX-NMMF','USDJPY','2026-08-17',1000000,147100000,147.12,"
        "0.0068,147120000,1000000,1000,1000,1000)"
    )
    conn.commit()


# --------------------------------------------------------------------------- app / layout
def test_create_app_returns_dash_app():
    app = uiapp.create_app(db_path="does-not-exist.db")
    assert isinstance(app, dash.Dash)


def test_layout_has_six_tabs_in_order():
    data = uiapp.empty_summary()
    layout = uiapp.build_layout(data)
    assert layout.children[1].className == "source-strip"  # upload strip sits above the tabs
    tabs_component = layout.children[2]  # [1] is the data-source strip above the tabs
    assert isinstance(tabs_component, dash.dcc.Tabs)
    labels = [child.label for child in tabs_component.children]
    assert labels == [
        "Cash ladder",
        "FX",
        "Rates",
        "Options",
        "Delta",
        "Overall book",
    ]
    for child in tabs_component.children:
        assert isinstance(child, dash.dcc.Tab)


# --------------------------------------------------------------------------- summary()
def test_summary_with_seeded_data():
    conn = schema.connect()
    _seed(conn)
    result = uiapp.summary(conn)
    assert result == {
        "as_of_date": "2026-08-17",
        "trades": 1,
        "trade_legs": 2,
        "marks": 1,
        "positions": 1,
    }


def test_summary_empty_db():
    conn = schema.connect()
    result = uiapp.summary(conn)
    assert result["as_of_date"] == "none"
    assert result["trades"] == 0
    assert result["trade_legs"] == 0
    assert result["marks"] == 0
    assert result["positions"] == 0


def test_load_summary_missing_file(tmp_path):
    missing = tmp_path / "no_such.db"
    result = uiapp.load_summary(missing)
    assert result["as_of_date"] == "none"
    assert result["trades"] == 0
    assert result["trade_legs"] == 0
    assert result["marks"] == 0
    assert result["positions"] == 0
    assert "message" in result


def test_load_summary_existing_file(tmp_path):
    db_path = tmp_path / "risk.db"
    conn = schema.connect(db_path)
    _seed(conn)
    conn.close()
    result = uiapp.load_summary(db_path)
    assert result["as_of_date"] == "2026-08-17"
    assert result["trades"] == 1


def test_get_db_path_default(monkeypatch):
    monkeypatch.delenv("RISK_DB", raising=False)
    assert uiapp.get_db_path() == uiapp.DEFAULT_DB_PATH


def test_get_db_path_from_env(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("RISK_DB", str(custom))
    assert uiapp.get_db_path() == custom


# --------------------------------------------------------------------------- cash ladder tab
def _hand_built_ladder_frame():
    import pandas as pd

    return pd.DataFrame(
        {
            "ccy": ["EUR", "USD"],
            "2026-08-18": [1000000.0, -1234567.8],
            "2026-08-19": [-500000.4, float("nan")],
            "2026-08-20": [0.0, 250000.0],
            "total": [499999.6, -984567.8],
            "usd": [float("nan"), -984567.8],
        }
    )


def test_cash_ladder_table_from_hand_built_frame():
    df = _hand_built_ladder_frame()
    table = cash_ladder.table_from_ladder(df)
    assert isinstance(table, dash.dash_table.DataTable)
    assert [c["id"] for c in table.columns] == list(df.columns)
    assert len(table.data) == 2
    row0 = table.data[0]
    assert row0["ccy"] == "EUR"
    assert row0["2026-08-18"] == "1,000,000"
    assert row0["2026-08-19"] == "(500,000)"
    assert row0["usd"] == ""  # NaN -> blank
    row1 = table.data[1]
    assert row1["2026-08-18"] == "(1,234,568)"  # rounds -1234567.8 -> (1,234,568)


def test_cash_ladder_source_dropdown_defaults_to_official():
    layout = cash_ladder.build_layout()
    dropdown = _find(layout, cash_ladder.SOURCE_DROPDOWN_ID)
    assert isinstance(dropdown, dash.dcc.Dropdown)
    assert dropdown.value == "WORKBOOK_REFERENCE"
    assert dropdown.id == cash_ladder.SOURCE_DROPDOWN_ID
    labels = [opt["label"] for opt in dropdown.options]
    values = [opt["value"] for opt in dropdown.options]
    assert "Official" in labels
    assert cash_ladder.SOURCE_OFFICIAL in values


def test_cash_ladder_source_value_to_param():
    assert cash_ladder.source_value_to_param(cash_ladder.SOURCE_OFFICIAL) is None
    assert cash_ladder.source_value_to_param(None) is None
    assert cash_ladder.source_value_to_param("BNP_BVAL") == "BNP_BVAL"


def test_format_cell_parentheses_and_thousands():
    assert cash_ladder.format_cell(-1234567.8) == "(1,234,568)"
    assert cash_ladder.format_cell(1234567.8) == "1,234,568"
    assert cash_ladder.format_cell(0) == "0"
    assert cash_ladder.format_cell(None) == ""
    assert cash_ladder.format_cell(float("nan")) == ""


def test_format_ladder_frame_blank_for_nan_and_ccy_passthrough():
    df = _hand_built_ladder_frame()
    formatted = cash_ladder.format_ladder_frame(df)
    assert formatted.loc[0, "ccy"] == "EUR"
    assert formatted.loc[1, "ccy"] == "USD"
    assert formatted.loc[0, "usd"] == ""
    assert formatted.loc[1, "2026-08-19"] == ""
    assert formatted.loc[0, "2026-08-18"] == "1,000,000"
    assert formatted.loc[1, "2026-08-18"] == "(1,234,568)"


def test_cash_ladder_wired_into_cash_ladder_tab():
    data = uiapp.empty_summary()
    layout = uiapp.build_layout(data)
    tabs_component = layout.children[2]  # [1] is the data-source strip above the tabs
    cash_ladder_tab = tabs_component.children[0]
    assert cash_ladder_tab.label == "Cash ladder"
    inner = cash_ladder_tab.children[0]
    assert isinstance(inner.children[0], dash.html.H3)
    assert any(
        getattr(child, "id", None) == cash_ladder.TABLE_CONTAINER_ID
        for child in inner.children
    )


def _cash_ladder_callback(app):
    """The cash-ladder callback has two outputs, so Dash keys it '..a.children...b.children..'."""
    keys = [k for k in app.callback_map if f"{cash_ladder.TABLE_CONTAINER_ID}.children" in k]
    assert len(keys) == 1
    return app.callback_map[keys[0]]["callback"]


def test_create_app_registers_cash_ladder_callback():
    app = uiapp.create_app(db_path="does-not-exist.db")
    assert _cash_ladder_callback(app) is not None


# --------------------------------------------------------------------------- transpose_ladder
def _hand_built_wide_ladder_frame():
    """Unsorted row order on purpose (EUR, GBP, USD) -- transpose_ladder must recompute
    the currency order itself, not rely on the input already being sorted.

    Covers all three usd_equivalent blank-rule branches (see cash_ladder module
    docstring): EUR has usd=NaN (spot unknown) so any date where EUR is non-zero blanks
    that date; 2026-08-19 has a NaN amount cell (unknown flow) which blanks that date
    regardless of spot; 2026-08-20 has only USD non-zero with a recoverable spot so it is
    the one date with a defined usd_equivalent. GBP's spot (1.25) is recoverable from
    usd/total (250.0/200.0) even though it never appears alone with a non-zero amount.
    """
    import pandas as pd

    return pd.DataFrame(
        {
            "ccy": ["EUR", "GBP", "USD"],
            "2026-08-18": [1000000.0, 0.0, -1234567.8],
            "2026-08-19": [-500000.4, 200.0, float("nan")],
            "2026-08-20": [0.0, 0.0, 250000.0],
            "total": [499999.6, 200.0, -984567.8],
            "usd": [float("nan"), 250.0, -984567.8],
        }
    )


def test_transpose_ladder_column_order_and_rows():
    df = _hand_built_wide_ladder_frame()
    out = cash_ladder.transpose_ladder(df)
    assert list(out.columns) == ["settle_date", "USD", "GBP", "EUR", "usd_equivalent"]
    assert list(out["settle_date"]) == ["2026-08-18", "2026-08-19", "2026-08-20", "Total"]


def test_transpose_ladder_usd_equivalent_blank_rules():
    import pandas as pd

    df = _hand_built_wide_ladder_frame()
    out = cash_ladder.transpose_ladder(df).set_index("settle_date")

    # 2026-08-18: EUR is non-zero and has no defined spot (usd=NaN) -> blank.
    assert pd.isna(out.loc["2026-08-18", "usd_equivalent"])
    # 2026-08-19: USD amount itself is NaN -> blank regardless of spot.
    assert pd.isna(out.loc["2026-08-19", "usd_equivalent"])
    # 2026-08-20: only USD is non-zero, spot = usd/total = 1.0 -> defined.
    assert out.loc["2026-08-20", "usd_equivalent"] == 250000.0
    # Total row: EUR total is non-zero with no defined spot -> blank.
    assert pd.isna(out.loc["Total", "usd_equivalent"])


def test_transpose_ladder_cell_values_pass_through():
    df = _hand_built_wide_ladder_frame()
    out = cash_ladder.transpose_ladder(df).set_index("settle_date")
    assert out.loc["2026-08-18", "USD"] == -1234567.8
    assert out.loc["Total", "GBP"] == 200.0


def test_transpose_ladder_empty_frame():
    import pandas as pd

    out = cash_ladder.transpose_ladder(pd.DataFrame({"ccy": []}))
    assert list(out.columns) == ["settle_date", "usd_equivalent"]
    assert out.empty


def test_table_from_transposed_ladder_via_settle_date_label():
    df = _hand_built_wide_ladder_frame()
    transposed = cash_ladder.transpose_ladder(df)
    table = cash_ladder.table_from_ladder(transposed, label_col="settle_date")
    assert isinstance(table, dash.dash_table.DataTable)
    assert [c["id"] for c in table.columns] == list(transposed.columns)
    total_row = table.data[-1]
    assert total_row["settle_date"] == "Total"
    assert total_row["usd_equivalent"] == ""  # NaN -> blank
    assert total_row["USD"] == "(984,568)"


def test_cash_ladder_callback_pipeline_transposes(monkeypatch):
    """register_callbacks' inner function should call transpose_ladder on the fetched
    ladder frame before rendering -- checked by monkeypatching transpose_ladder and
    verifying it was called with the raw ladder_table output."""
    import types
    import sys

    df = _hand_built_wide_ladder_frame()
    calls = []

    fake_views = types.ModuleType("engine.ladder.views")
    fake_views.ladder_table = lambda conn, as_of_date, source=None: df
    fake_engine = types.ModuleType("engine")
    fake_engine_ladder = types.ModuleType("engine.ladder")
    fake_engine.ladder = fake_engine_ladder
    fake_engine_ladder.views = fake_views
    # The callback also imports engine.ladder.valuation; stub it so this test does not
    # depend on another test file having imported the real module first.
    fake_valuation = types.ModuleType("engine.ladder.valuation")
    fake_valuation.ladder_trade_valuation = lambda conn, as_of_date, source=None: pd.DataFrame({"pnl_usd": []})
    fake_valuation.ladder_valuation_summary = lambda detail: pd.DataFrame()
    fake_engine_ladder.valuation = fake_valuation
    monkeypatch.setitem(sys.modules, "engine", fake_engine)
    monkeypatch.setitem(sys.modules, "engine.ladder", fake_engine_ladder)
    monkeypatch.setitem(sys.modules, "engine.ladder.views", fake_views)
    monkeypatch.setitem(sys.modules, "engine.ladder.valuation", fake_valuation)

    real_transpose = cash_ladder.transpose_ladder

    def spy_transpose(frame):
        calls.append(frame)
        return real_transpose(frame)

    monkeypatch.setattr(cash_ladder, "transpose_ladder", spy_transpose)
    monkeypatch.setattr(
        "ui.app.connect_readonly", lambda path: schema.connect()
    )

    app = uiapp.create_app(db_path="does-not-exist.db")
    callback = _cash_ladder_callback(app)
    callback.__wrapped__(cash_ladder.SOURCE_OFFICIAL, "2026-08-18")
    assert len(calls) == 1
    assert calls[0] is df


# --------------------------------------------------------------------------- P&L tab
def _hand_built_by_pair():
    import pandas as pd

    return pd.DataFrame(
        {
            "instrument_id": ["USDJPY", "EURUSD"],
            "usd_notional": [1000000.0, -500000.0],
            "ltd_usd": [1234.5, float("nan")],
            "n_trades": [3, 1],
        }
    )


def test_pnl_pairs_table_from_by_pair():
    by_pair = _hand_built_by_pair()
    table = pnl.pairs_table_from_by_pair(by_pair)
    assert isinstance(table, dash.dash_table.DataTable)
    assert [c["id"] for c in table.columns] == pnl.PAIR_COLUMNS
    row0 = table.data[0]
    assert row0["instrument_id"] == "USDJPY"
    assert row0["usd_notional"] == "1,000,000"
    assert row0["n_trades"] == "3"
    row1 = table.data[1]
    assert row1["usd_notional"] == "(500,000)"
    assert row1["ltd_usd"] == ""  # NaN -> blank


def test_pnl_pairs_table_empty():
    import pandas as pd

    table = pnl.pairs_table_from_by_pair(pd.DataFrame(columns=pnl.PAIR_COLUMNS))
    assert table.data == []


def test_pnl_totals_table_from_book_totals():
    totals = {"net_usd": 100000.0, "gross_usd": -250000.5, "gold_usd": float("nan"), "futures_usd": 0.0}
    table = pnl.totals_table_from_book_totals(totals)
    rows = {row["metric"]: row["usd"] for row in table.data}
    assert rows["net_usd"] == "100,000"
    assert rows["gross_usd"] == "(250,000)"  # round(-250000.5) -> -250000 (banker's rounding)
    assert rows["gold_usd"] == ""
    assert rows["futures_usd"] == "0"


def test_pnl_period_table_from_period_pnl():
    period = {
        "as_of_date": "2026-08-17",
        "ltd": 5000.0,
        "daily": 100.0, "daily_ref_date": "2026-08-14",
        "d5": float("nan"), "d5_ref_date": "2026-08-10",
        "mtd": -200.0, "mtd_ref_date": "2026-07-31",
        "ytd": 900.0, "ytd_ref_date": "2025-12-31",
    }
    table = pnl.period_table_from_period_pnl(period)
    rows = {row["period"]: row for row in table.data}
    assert rows["LTD"]["usd"] == "5,000"
    assert rows["LTD"]["ref_date"] == ""
    assert rows["Daily"]["usd"] == "100"
    assert rows["Daily"]["ref_date"] == "2026-08-14"
    assert rows["5d"]["usd"] == ""  # NaN -> blank
    assert rows["5d"]["ref_date"] == "2026-08-10"
    assert rows["MTD"]["usd"] == "(200)"
    assert rows["YTD"]["usd"] == "900"


def test_pnl_source_dropdown_and_date_picker():
    layout = pnl.build_layout(default_date="2026-08-17")
    assert isinstance(layout.children[0], dash.html.H3)
    source_div = layout.children[1]
    dropdown = source_div.children[1]
    assert isinstance(dropdown, dash.dcc.Dropdown)
    assert dropdown.id == pnl.SOURCE_DROPDOWN_ID
    assert dropdown.value == "WORKBOOK_REFERENCE"
    date_div = layout.children[2]
    picker = date_div.children[1]
    assert isinstance(picker, dash.dcc.DatePickerSingle)
    assert picker.id == pnl.DATE_PICKER_ID
    assert picker.date == "2026-08-17"
    assert any(
        getattr(child, "id", None) == pnl.CONTENT_CONTAINER_ID
        for child in layout.children
    )


def test_pnl_wired_into_overall_book_tab():
    data = uiapp.empty_summary()
    layout = uiapp.build_layout(data)
    tabs_component = layout.children[2]  # [1] is the data-source strip above the tabs
    overall_book_tab = tabs_component.children[5]
    assert overall_book_tab.label == "Overall book"
    inner = overall_book_tab.children[0]
    assert isinstance(inner.children[0], dash.html.H3)
    assert any(
        getattr(child, "id", None) == pnl.CONTENT_CONTAINER_ID
        for child in inner.children
    )


def test_create_app_registers_pnl_callback():
    app = uiapp.create_app(db_path="does-not-exist.db")
    assert f"{pnl.CONTENT_CONTAINER_ID}.children" in app.callback_map


def test_pnl_component_ids_distinct_from_cash_ladder():
    assert pnl.SOURCE_DROPDOWN_ID != cash_ladder.SOURCE_DROPDOWN_ID
    assert pnl.DATE_PICKER_ID != cash_ladder.DATE_PICKER_ID
    assert pnl.CONTENT_CONTAINER_ID != cash_ladder.TABLE_CONTAINER_ID


# --------------------------------------------------------------------------- exposure ladder section
def _find(component, comp_id):
    """Depth-first search of a Dash component tree by id."""
    if getattr(component, "id", None) == comp_id:
        return component
    children = getattr(component, "children", None)
    if children is None:
        return None
    for child in children if isinstance(children, list) else [children]:
        found = _find(child, comp_id) if hasattr(child, "children") or hasattr(child, "id") else None
        if found is not None:
            return found
    return None


def _texts(component) -> str:
    out = []

    def walk(c):
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for x in c:
                walk(x)
        elif hasattr(c, "children"):
            walk(c.children)
    walk(component)
    return " | ".join(out)


def _exposure_records():
    from tests.test_exposure import AUD_FIXTURE
    recs = [dict(r, book_source="HAHY7", settles_cash=1, is_ndf=0) for r in AUD_FIXTURE]
    for r in recs:
        r["book"] = "HAHY7"
    recs.append({"trade_id": "B1", "settlement_date": "2026-09-16", "book": "HAHY7", "book_source": "HAHY7",
                 "currency": "BRL", "local_amount": 30841044.0, "usd_entry_amount": 6000000.0,
                 "settles_cash": 0, "is_ndf": 1})
    return recs


def _aud_section():
    from ui.tabs import exposure
    rates = exposure.mock_rates(["AUD", "BRL"])
    rates["AUD"]["rate"] = 0.600  # screenshot rate; still labelled MOCK
    return exposure.exposure_section(_exposure_records(), [], "2026-09-14", rates=rates)


def _cards(section):
    from ui.tabs import exposure
    out = {}
    for card in _find(section, exposure.SNAPSHOT_ID).children:
        label, value, tag = (c.children for c in card.children[:3])
        out[label] = (value, tag)
    return out


def test_dashboard_order_cards_meta_summary_then_collapsed_ladder():
    from ui.tabs import exposure
    section = _aud_section()
    ids = [getattr(c, "id", None) for c in section.children]
    order = [ids.index(exposure.SNAPSHOT_ID), ids.index(exposure.META_ID), ids.index(exposure.MARKET_DATA_ID),
             ids.index(exposure.WORKBOOK_STATUS_ID), ids.index(exposure.COMBINED_TABLE_ID),
             ids.index(exposure.LADDER_DETAILS_ID), ids.index(exposure.LEGEND_ID)]
    assert order == sorted(order)                       # cards < meta < mock line < wb status < combined < details < legend
    details = _find(section, exposure.LADDER_DETAILS_ID)
    assert isinstance(details, dash.html.Details) and details.open is False
    assert details.children[0].children.startswith("Alternative views")
    assert _find(details, exposure.SUMMARY_TABLE_ID) is not None
    assert _find(details, exposure.LADDER_TABLE_ID) is not None


def test_four_snapshot_cards_use_engine_totals():
    from engine.ladder.exposure import build_exposure, portfolio_totals
    from ui.tabs import exposure
    from ui.tabs.exposure import format_amount
    cards = _cards(_aud_section())
    assert list(cards) == ["Net USD exposure", "Gross USD exposure", "Exposure P&L", "Open FX trades"]
    rates = exposure.mock_rates(["AUD", "BRL"]); rates["AUD"]["rate"] = 0.600
    totals = portfolio_totals(build_exposure(_exposure_records(), rates))
    assert cards["Net USD exposure"] == (format_amount(totals["net_usd"]), "Exposure")
    assert cards["Gross USD exposure"] == (format_amount(totals["gross_usd"]), "Exposure")
    assert cards["Exposure P&L"] == (format_amount(totals["exposure_pnl"]), "Exposure")
    assert cards["Open FX trades"] == ("5", "Exposure")


def test_metadata_line_and_compact_workbook_status():
    from ui.tabs import exposure
    section = _aud_section()
    meta = _texts(_find(section, exposure.META_ID))
    for item in ("As-of 2026-09-14", "Book HAHY7", "NDF trades 1", "Unresolved trades 0",
                 "Sign convention broker_reference", exposure.MOCK_SOURCE, exposure.MOCK_TIMESTAMP):
        assert item in meta
    wb = _find(section, exposure.WORKBOOK_STATUS_ID)
    assert "Workbook MTM unavailable" in _texts(wb) and "wb-status--unavailable" in wb.className
    assert _texts(wb.children[0]) == "Workbook MTM"
    period = {"daily": -1234.6, "ltd": 98765.4, "trading": float("nan"), "daily_ref_date": "2026-09-11"}
    with_wb = exposure.exposure_section(_exposure_records(), [], "2026-09-14",
                                        rates=exposure.mock_rates(["AUD", "BRL"]), period=period)
    text = _texts(_find(with_wb, exposure.WORKBOOK_STATUS_ID))
    assert "Daily P&L (1,235)" in text and "LTD P&L 98,765" in text and "Trading P&L unavailable" in text
    assert _cards(with_wb)["Exposure P&L"][1] == "Exposure"                        # distinct from Workbook MTM


def test_currency_summary_primary_columns_sort_scope_aud_values_and_ndf_badge():
    from ui.tabs import exposure
    section = _aud_section()
    table = _find(section, exposure.SUMMARY_TABLE_ID)
    assert [c["id"] for c in table.columns] == ["currency", "local_delta", "usd_delta", "usd_delta_entry",
                                                "exposure_pnl", "settlement"]
    assert [row["currency"] for row in table.data] == ["AUD", "BRL"]              # |USD delta| desc: 12.6m > 5.9m
    summary = {row["currency"]: row for row in table.data}
    aud = summary["AUD"]
    assert aud["local_delta"] == "(20,961,623)" and aud["usd_delta"] == "(12,576,974)"
    assert aud["usd_delta_entry"] == "(15,000,000)" and aud["exposure_pnl"] == "2,423,026"
    assert aud["settlement"] == "Deliverable" and summary["BRL"]["settlement"] == exposure.NDF_BADGE == "NDF"
    assert "fx_rate" not in aud and "status" not in aud and "rate_source" not in aud
    rates = exposure.mock_rates(["AUD", "BRL"]); rates["AUD"]["rate"] = 0.600
    # default sort puts the larger |USD delta| first
    recs = _exposure_records(); recs[-1]["local_amount"] = 308410440.0
    big = exposure.exposure_section(recs, [], "2026-09-14", rates=rates)
    assert [row["currency"] for row in _find(big, exposure.SUMMARY_TABLE_ID).data] == ["BRL", "AUD"]
    # all currencies always shown; the list view lives in the collapsed details
    many = _exposure_records()
    for i, ccy in enumerate(["JPY", "EUR", "GBP", "CAD", "CHF", "SEK", "NOK"]):
        many.append({"trade_id": f"X{i}", "settlement_date": "2026-09-16", "book": "HAHY7", "book_source": "HAHY7",
                     "currency": ccy, "local_amount": 1000.0 * (i + 1), "usd_entry_amount": 0.0,
                     "settles_cash": 1, "is_ndf": 0})
    everything = exposure.exposure_section(many, [], "2026-09-14")
    assert len(_find(everything, exposure.SUMMARY_TABLE_ID).data) == 9


def test_combined_ladder_is_primary_shows_all_currencies_dates_then_summary_rows():
    from ui.tabs import exposure
    many = _exposure_records()
    for i, ccy in enumerate(["JPY", "EUR", "GBP", "CAD", "CHF", "SEK", "NOK"]):
        many.append({"trade_id": f"X{i}", "settlement_date": "2026-09-16", "book": "HAHY7", "book_source": "HAHY7",
                     "currency": ccy, "local_amount": 1000.0 * (i + 1), "usd_entry_amount": 0.0,
                     "settles_cash": 1, "is_ndf": 0})
    rates = exposure.mock_rates({r["currency"] for r in many}); rates["AUD"]["rate"] = 0.600
    section = exposure.exposure_section(many, [], "2026-09-14", rates=rates)
    table = _find(section, exposure.COMBINED_TABLE_ID)
    ids = [c["id"] for c in table.columns]
    assert ids[0] == exposure.ROW_LABEL_COL and ids[-1] == exposure.USD_EQUIVALENT_COL
    assert len(ids) - 2 == 9 and ids[1] == "AUD"                       # every currency, largest |USD delta| first
    labels = [r[exposure.ROW_LABEL_COL] for r in table.data]
    assert labels[:4] == ["11 Sep 2026", "16 Sep 2026", "24 Sep 2026", "28 Sep 2026"]
    assert labels[4:] == ["FX rate (USD per local)", "Local delta", "USD delta", "USD delta entry",
                          "Exposure P&L", "Settlement type"]
    rows = {r[exposure.ROW_LABEL_COL]: r for r in table.data}
    assert rows["11 Sep 2026"]["AUD"] == "(20,763,351)" and rows["11 Sep 2026"]["settlement_date"] == "2026-09-11"
    assert rows["FX rate (USD per local)"]["AUD"] == "0.600000"
    assert rows["Local delta"]["AUD"] == "(20,961,623)" and rows["USD delta"]["AUD"] == "(12,576,974)"
    assert rows["USD delta entry"]["AUD"] == "(15,000,000)" and rows["Exposure P&L"]["AUD"] == "2,423,026"
    assert rows["Settlement type"]["BRL"] == "NDF" and rows["Settlement type"]["AUD"] == "Deliverable"
    brl_col = next(c for c in table.columns if c["id"] == "BRL")
    assert brl_col["name"] == ["NDF", "BRL"] and table.merge_duplicate_headers is True
    assert table.fixed_rows == {"headers": True} and table.fixed_columns == {"headers": True, "data": 1}
    # USD equivalent column: per-date engine value on date rows, portfolio totals on summary rows
    assert rows["11 Sep 2026"][exposure.USD_EQUIVALENT_COL] == "(12,458,011)"
    assert rows["Exposure P&L"][exposure.USD_EQUIVALENT_COL] != "" and rows["FX rate (USD per local)"][exposure.USD_EQUIVALENT_COL] == ""
    alpha = exposure.exposure_section(many, [], "2026-09-14", rates=rates, sort=exposure.SORT_ALPHA)
    assert [c["id"] for c in _find(alpha, exposure.COMBINED_TABLE_ID).columns][1:3] == ["AUD", "BRL"]


def test_ladder_orientation_dates_usd_equivalent_and_sticky_layout():
    from engine.ladder.exposure import build_exposure, ladder_usd_equivalent
    from ui.tabs import exposure
    ladder = _find(_aud_section(), exposure.LADDER_TABLE_ID)
    assert [c["id"] for c in ladder.columns] == [exposure.DATE_LABEL_COL, "AUD", "BRL", exposure.USD_EQUIVALENT_COL]
    assert ladder.columns[0]["name"] == "Settlement date" and ladder.columns[-1]["name"] == "USD equivalent"
    assert [row["settlement_date"] for row in ladder.data] == ["2026-09-11", "2026-09-16", "2026-09-24", "2026-09-28"]
    assert [row[exposure.DATE_LABEL_COL] for row in ladder.data] == ["11 Sep 2026", "16 Sep 2026", "24 Sep 2026", "28 Sep 2026"]
    assert ladder.data[0]["AUD"] == "(20,763,351)" and ladder.data[1]["BRL"] == "30,841,044"
    assert ladder.data[0]["BRL"] == exposure.EM_DASH
    rates = exposure.mock_rates(["AUD", "BRL"]); rates["AUD"]["rate"] = 0.600
    usd = ladder_usd_equivalent(build_exposure(_exposure_records(), rates))
    assert ladder.data[0][exposure.USD_EQUIVALENT_COL] == exposure.format_amount(usd["2026-09-11"]) == "(12,458,011)"
    assert ladder.fixed_rows == {"headers": True} and ladder.fixed_columns == {"headers": True, "data": 1}
    assert ladder.style_table["overflowX"] == "auto"
    styles = ladder.style_data_conditional
    assert any(s["if"]["column_id"] == "AUD" and "contains '('" in s["if"]["filter_query"] and s["color"] == "#b42318" for s in styles)
    assert any(s["if"]["column_id"] == "AUD" and s["color"] == "#1a7f4b" for s in styles)


def test_market_data_panel_and_legend_mock_labels():
    from ui.tabs import exposure
    section = _aud_section()
    panel = _find(section, exposure.MARKET_DATA_ID)
    text = _texts(panel)
    assert "NO BLOOMBERG FEED" in text and "no pull recorded yet" in text and "nothing is substituted" in text.lower()
    assert "status-panel--down" in panel.className and _find(panel, exposure.WARNINGS_ID).hidden is True
    live_panel = exposure.market_data_panel(
        __import__("engine.ladder.exposure", fromlist=["build_exposure"]).build_exposure([], {}),
        {"connected": True, "time": "2026-09-14T15:00:00", "written": 36, "failed": 0})
    live_text = _texts(live_panel)
    assert "BLOOMBERG LIVE" in live_text and "36 marks written, 0 failed" in live_text and "every 2 min" in live_text
    legend = _texts(_find(section, exposure.LEGEND_ID))
    for term in ("Local delta", "USD delta", "USD delta entry", "Exposure P&L", "NDF", "settles_cash=0", "Bloomberg rates"):
        assert term in legend
    assert "Mock" not in legend


def test_exposure_section_warns_on_missing_and_stale_rates():
    from ui.tabs import exposure
    recs = _exposure_records()
    rates = {"AUD": {"rate": 0.6, "inverted": False, "source": "MOCK", "timestamp": "old", "stale": True}}
    section = exposure.exposure_section(recs, [], "2026-09-14", rates=rates)
    warnings = _texts(_find(section, exposure.WARNINGS_ID))
    assert "BRL: no market-data rate" in warnings and "AUD: stale rate" in warnings
    summary = {row["currency"]: row for row in _find(section, exposure.SUMMARY_TABLE_ID).data}
    assert summary["BRL"]["usd_delta"] == "" and summary["BRL"]["exposure_pnl"] == ""
    assert getattr(_find(section, exposure.WARNINGS_ID), "hidden", None) is not True   # warnings visible
    cards = _cards(section)
    assert cards["Net USD exposure"] == ("Unavailable", "Unavailable")          # never a partial or zero total
    from engine.ladder.exposure import build_exposure
    assert exposure.rate_status_text(build_exposure(recs, rates)) == "Rates: 2 missing/stale"


def test_exposure_book_display_mapping_is_explicit():
    from ui.tabs import exposure
    recs = _exposure_records()
    for r in recs:
        r["book"] = "HA"  # what records_from_db produces when BOOK_DISPLAY = {"HAHY7": "HA"}
    assert "Book HA (source HAHY7)" in _texts(_find(exposure.exposure_section(recs, [], "2026-09-14"), exposure.HEADER_ID))


def test_format_amount_em_dash_only_for_zero():
    from ui.tabs.exposure import EM_DASH, format_amount
    assert format_amount(0) == EM_DASH and format_amount(0.4) == EM_DASH
    assert format_amount(-1234.6) == "(1,235)" and format_amount(1234.4) == "1,234"
    assert format_amount(float("nan")) == "" and format_amount(None) == ""


def test_cash_ladder_layout_title_toolbar_and_tab_styling():
    layout = uiapp.build_layout(uiapp.empty_summary())
    tabs_component = layout.children[2]  # [1] is the data-source strip above the tabs
    assert tabs_component.parent_className == "tabs-bar"
    assert all(t.className == "tab" and t.selected_className == "tab--selected" for t in tabs_component.children)
    inner = tabs_component.children[0].children[0]
    assert inner.children[0].children == "FX Risk and Settlement Ladder"
    toolbar = _find(inner, cash_ladder.TOOLBAR_ID)
    assert toolbar.className == "toolbar"
    assert _find(toolbar, cash_ladder.SOURCE_DROPDOWN_ID) is not None
    assert _find(toolbar, cash_ladder.DATE_PICKER_ID) is not None
    order = _find(toolbar, cash_ladder.SORT_ID)
    assert order.value == "usd" and [o["value"] for o in order.options] == ["usd", "alpha"]
    assert [o["label"] for o in order.options] == ["|USD delta|", "A-Z"]
    text = _texts(toolbar)
    assert "Book / Bloomberg feed" in text and "Bloomberg: waiting for first refresh" in text and "MOCK" not in text
    assert _find(toolbar, cash_ladder.STATUS_ID) is not None
    assert "Workbook MTM valuation: Workbook rates" in text
    # the workbook-source dropdown sits under the workbook label, not under a generic 'Source' for both
    workbook_group = [c for c in toolbar.children if getattr(c, "className", "") and "toolbar-group--workbook" in c.className][0]
    assert _find(workbook_group, cash_ladder.SOURCE_DROPDOWN_ID) is not None


def test_stylesheet_asset_exists_with_key_classes():
    css = (uiapp.REPO_ROOT / "ui" / "assets" / "style.css").read_text(encoding="utf-8")
    for cls in (".tab--selected", ".toolbar", ".meta", ".metric", ".badge--ndf", ".section--secondary", ".legend"):
        assert cls in css


def test_cash_ladder_callback_renders_exposure_from_db(monkeypatch):
    """End to end through the real callback: seeded DB -> records_from_db -> exposure section."""
    from ui.tabs import exposure
    conn = schema.connect()
    _seed(conn)
    monkeypatch.setattr("ui.app.connect_readonly", lambda path: conn)
    app = uiapp.create_app(db_path="does-not-exist.db")
    callback = _cash_ladder_callback(app)
    out, toolbar_status = callback.__wrapped__(cash_ladder.SOURCE_OFFICIAL, "2026-08-17")
    # seeded DB has one official SPOT mark for USDJPY (147.12, snapped 2026-08-17 -> stale); no mock anywhere
    assert toolbar_status.startswith("Book HAHY7 · Rates: 1 missing/stale · Bloomberg:")
    assert "MOCK" not in toolbar_status and "mock" not in _texts(out).lower()
    ladder = _find(out, exposure.LADDER_TABLE_ID)
    assert [c["id"] for c in ladder.columns] == [exposure.DATE_LABEL_COL, "JPY", exposure.USD_EQUIVALENT_COL]
    row = ladder.data[0]
    assert row["settlement_date"] == "2026-08-17" and row[exposure.DATE_LABEL_COL] == "17 Aug 2026"
    assert row["JPY"] == "(147,100,000)"
    summary = _find(out, exposure.SUMMARY_TABLE_ID).data[0]
    assert summary["currency"] == "JPY" and summary["usd_delta_entry"] == "(1,000,000)"
    combined = {r[exposure.ROW_LABEL_COL]: r for r in _find(out, exposure.COMBINED_TABLE_ID).data}
    assert combined["FX rate (USD per local)"]["JPY"] == f"{1 / 147.12:.6f}"      # from marks, inverted USDJPY
    assert "Rate source BBG_BFXFORWARD" in _texts(_find(out, exposure.META_ID))
    assert "HAHY7" in _texts(_find(out, exposure.HEADER_ID))
    from ui.tabs import market_data
    diag = _find(out, market_data.DIAG_ID)
    assert isinstance(diag, dash.html.Details)
    rates_rows = _find(diag, market_data.RATES_TABLE_ID).data
    assert rates_rows == [{"currency": "JPY", "pair": "USDJPY", "rate": "147.12000000", "inverted": "1/rate",
                           "source": "BBG_BFXFORWARD", "timestamp": "2026-08-17T15:00:00-04:00", "stale": "STALE"}]
    # P&L ledger block sits between the exposure section and the diagnostics panel
    from ui.tabs import ledger as ledger_ui
    block = _find(out, ledger_ui.LEDGER_ID)
    assert block is not None
    ids = [getattr(c, "id", None) for c in out.children[0].children]
    assert ids.index(ledger_ui.LEDGER_ID) < ids.index(market_data.DIAG_ID)
    labels = [c.children[0].children for c in _find(block, ledger_ui.LEDGER_CARDS_ID).children]
    assert labels == ["Realised LTD", "Unrealised (open)", "Total LTD", "Trading today",
                      "Daily P&L", "5-day P&L", "MTD P&L", "YTD P&L"]
    cards = _cards(out)
    assert cards["Open FX trades"] == ("1", "Exposure")
    assert "Workbook MTM" in _texts(_find(out, exposure.WORKBOOK_STATUS_ID))   # from period_pnl, never mock
    # dashboard section precedes the collapsed secondary workbook section, which stays intact
    assert [getattr(c, "id", None) for c in out.children][1] == cash_ladder.WORKBOOK_SECTION_ID
    workbook = _find(out, cash_ladder.WORKBOOK_SECTION_ID)
    assert isinstance(workbook, dash.html.Details) and workbook.open is False
    assert "section--secondary" in workbook.className
    text = _texts(workbook)
    assert "Workbook mark-to-market — separate calculation" in text
    assert "not the screenshot-style Exposure P&L" in text
    assert _find(workbook, "cash-ladder-valuation-summary") is not None
    assert _find(workbook, "cash-ladder-valuation-detail") is not None
    assert _find(workbook, "cash-ladder-datatable") is not None


# --------------------------------------------------------------------------- P&L ledger block
def test_ledger_block_renders_values_and_unavailable_reasons():
    from ui.tabs import ledger as ledger_ui
    summary = {
        "realised_ltd_usd": 30000.0, "unrealised_usd": 20000.0, "total_ltd_usd": 50000.0, "trading_usd": 0.0,
        "open_trades": 2, "realised_trades": 1, "missing": [], "unrealisable": [{"trade_id": "j1", "reason": "no spot"}],
        "periods": {"daily": {"value": 40000.0, "ref_date": "2026-09-11", "snapshot_date": "2026-09-11", "available": True, "reason": ""},
                    "d5": {"value": float("nan"), "ref_date": "2026-09-07", "snapshot_date": "", "available": False, "reason": "no snapshot on or before 2026-09-07"},
                    "mtd": {"value": 55000.0, "ref_date": "2026-08-31", "snapshot_date": "2026-08-28", "available": True,
                            "reason": "reference snapshot dated 2026-08-28 (last on or before 2026-08-31)"},
                    "ytd": {"value": float("nan"), "ref_date": "2025-12-31", "snapshot_date": "", "available": False, "reason": "no snapshot on or before 2025-12-31"}},
        "last_snapshot": {"as_of_date": "2026-09-14", "snapped_at": "2026-09-14T15:00:00", "complete": False},
        "snapshot_count": 3, "realised_table": pd.DataFrame(), "snapshot_table": pd.DataFrame(),
    }
    block = ledger_ui.ledger_block(summary, "2026-09-14")
    cards = {c.children[0].children: (c.children[1].children, c.children[2].children)
             for c in _find(block, ledger_ui.LEDGER_CARDS_ID).children}
    assert cards["Realised LTD"] == ("30,000", "1 settled trades frozen")
    assert cards["Unrealised (open)"][0] == "20,000" and cards["Total LTD"][0] == "50,000"
    assert cards["Trading today"] == (ledger_ui.EM_DASH if hasattr(ledger_ui, "EM_DASH") else "—", "trades dated 2026-09-14")
    assert cards["Daily P&L"] == ("40,000", "vs 11 Sep 2026")
    assert cards["5-day P&L"] == ("Unavailable", "no snapshot on or before 2026-09-07")
    assert cards["MTD P&L"][0] == "55,000" and "28 Aug 2026" in cards["MTD P&L"][1]
    assert cards["YTD P&L"][0] == "Unavailable"
    note = _texts(_find(block, ledger_ui.LEDGER_NOTE_ID))
    assert "last snapshot 2026-09-14" in note and "(incomplete)" in note and "j1 (no spot)" in note
    # missing rate -> unrealised / total / trading unavailable with the reason, realised still shown
    summary["missing"] = ["BRL"]; summary["unrealised_usd"] = float("nan"); summary["total_ltd_usd"] = float("nan")
    cards = {c.children[0].children: (c.children[1].children, c.children[2].children)
             for c in _find(ledger_ui.ledger_block(summary, "2026-09-14"), ledger_ui.LEDGER_CARDS_ID).children}
    assert cards["Total LTD"] == ("Unavailable", "missing rate: BRL") and cards["Realised LTD"][0] == "30,000"
