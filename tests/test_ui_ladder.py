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


# --------------------------------------------------------------------------- bnp_bval_rates
def test_bnp_bval_rates_used_only_as_fallback(conn):
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-17','USDJPY','2026-08-17','SPOT',148.00,'BNP_BVAL','2026-08-17T00:00:00-04:00')"
    )
    conn.commit()
    out = cash_ladder.bnp_bval_rates(conn, "2026-08-17")
    assert out["JPY"]["rate"] == 148.0
    assert out["JPY"]["inverted"] is True
    assert out["JPY"]["source"] == cash_ladder.BNP_BVAL_SOURCE_LABEL


def test_bnp_bval_rates_falls_back_to_latest_on_or_before(conn):
    conn.execute(
        "INSERT INTO marks VALUES "
        "('2026-08-10','USDJPY','2026-08-10','SPOT',146.00,'BNP_BVAL','2026-08-10T00:00:00-04:00')"
    )
    conn.commit()
    out = cash_ladder.bnp_bval_rates(conn, "2026-08-17")
    assert out["JPY"]["rate"] == 146.0


def test_bnp_bval_rates_empty_when_no_bnp_bval_mark(conn):
    assert cash_ladder.bnp_bval_rates(conn, "2026-08-17") == {}


# --------------------------------------------------------------------------- layout / callbacks
def test_build_layout_has_only_date_picker_no_dropdowns():
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


def test_combined_table_has_rate_source_row():
    from engine.ladder.exposure import build_exposure
    result = build_exposure(RECORDS, RATES)
    table = exposure.combined_table(result, RECORDS, fallback_ccys={"JPY"})
    rows = {r[exposure.ROW_LABEL_COL]: r for r in table.data}
    assert rows["Rate source"]["JPY"] == "BNP file"


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
    assert "Cash ladder: spot, forwards, swaps and option deltas" in text


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


def test_reconciliation_panel_shows_no_snapshot_message_when_bnp_never_reported():
    # A totally empty DB: BNP has no positions and no trades at all, so
    # bnp_snapshot_date resolves to None -- the panel must say so plainly, not claim
    # "no trades on either side" (which would misleadingly imply BNP was checked and
    # found to have nothing, rather than never having reported at all).
    conn = schema.connect(":memory:")
    panel = cash_ladder.reconciliation_panel(conn, "2026-08-17")
    assert isinstance(panel, dash.html.Details)
    assert "no BNP snapshot loaded yet" in panel.children[0].children


def test_reconciliation_panel_shows_no_trades_message_when_bnp_reported_but_empty():
    # BNP has reported (a positions row exists), but neither side has any trades for
    # this instrument/date -- this is the genuine "nothing to reconcile" case, distinct
    # from "BNP never reported" above.
    conn = schema.connect(":memory:")
    conn.execute(
        "INSERT INTO instruments VALUES ('CASH-USD','CASH','USD','USD',1,0,'','9999-12-31')"
    )
    conn.execute(
        "INSERT INTO positions VALUES ('2026-08-17','BNP','ACC','CASH-USD','2026-08-17',"
        "1000,1000,1,1,1000,1000,0,0,0)"
    )
    panel = cash_ladder.reconciliation_panel(conn, "2026-08-17")
    assert isinstance(panel, dash.html.Details)
    assert "no trades on either side" in panel.children[0].children


def test_reconciliation_panel_reports_clean_agreement(conn):
    # conn fixture already has one BNP trade (t1, USDJPY, USD leg +1,000,000). Add a
    # matching blotter trade with the same USD notional -- should agree.
    _add_blotter_trade(conn, "b1", "USDJPY", 1000000, 1000000)
    panel = cash_ladder.reconciliation_panel(conn, "2026-08-17")
    assert isinstance(panel, dash.html.Details)
    assert "agree" in panel.children[0].children
    assert len(panel.children) == 1  # no mismatch table appended


def test_reconciliation_panel_flags_mismatch_with_table(conn):
    # A blotter trade with a clearly different USD notional than the BNP side (t1's
    # USD leg is +1,000,000) must show up as a mismatch, open by default, with a table.
    _add_blotter_trade(conn, "b1", "USDJPY", 5000000, 5000000)
    panel = cash_ladder.reconciliation_panel(conn, "2026-08-17")
    assert isinstance(panel, dash.html.Details)
    assert panel.open is True
    assert "outside tolerance" in panel.children[0].children
    table = panel.children[1]
    assert isinstance(table, dash.dash_table.DataTable)
    ids = {row["instrument_id"] for row in table.data}
    assert ids == {"USDJPY"}
