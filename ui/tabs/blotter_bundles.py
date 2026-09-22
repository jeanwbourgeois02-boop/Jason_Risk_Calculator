"""Blotter "Bundles" sub-tab (user decision 2026-09-15, item 4): pure render helpers.
Callback wiring lives in `ui.tabs.blotter` alongside the other sub-tabs; this module
only builds Dash components from already-fetched data so it is unit-testable without
Dash callbacks or a live DB.

A bundle is a named view (`data.ingest.themes.{create_bundle, list_bundles,
bundle_pairs, add_pair_to_bundle, remove_pair_from_bundle}`) whose membership is the
existing theme mechanism: a pair belongs to bundle X when its `instrument_theme.theme`
(and, for already-loaded trades, `trades.theme`) equals X. "Unassigned" is every trade
whose `theme` is empty -- computed the same way `period_pnl_by(..., 'theme')` would
already group it (that empty-string group), not a special case.
"""
from __future__ import annotations

from typing import Dict, List

from dash import dash_table, dcc, html

from ui.tabs import ranking as rk

BUNDLE_LIST_ID = "blotter-bundle-list"
BUNDLE_DETAIL_ID = "blotter-bundle-detail"
BUNDLE_SELECTED_ID = "blotter-bundle-selected"
BUNDLE_NAME_INPUT_ID = "blotter-bundle-name"
BUNDLE_DESC_INPUT_ID = "blotter-bundle-desc"
BUNDLE_PAIRS_INPUT_ID = "blotter-bundle-pairs"
BUNDLE_CREATE_BUTTON_ID = "blotter-bundle-create"
BUNDLE_STATUS_ID = "blotter-bundle-status"
BUNDLE_ADD_PAIR_INPUT_ID = "blotter-bundle-add-pair"
BUNDLE_ADD_PAIR_BUTTON_ID = "blotter-bundle-add-pair-button"
BUNDLE_REMOVE_PAIR_INPUT_ID = "blotter-bundle-remove-pair"
BUNDLE_REMOVE_PAIR_BUTTON_ID = "blotter-bundle-remove-pair-button"
BUNDLE_REVISION_ID = "blotter-bundle-revision"

UNASSIGNED_LABEL = "Unassigned"

_PERIOD_ORDER = ("daily", "mtd", "ytd")
_PERIOD_TITLES = {"daily": "Daily", "mtd": "MTD", "ytd": "YTD"}


def _period_cell(periods: dict, key: str) -> tuple:
    """`(value, tooltip)`: the number, or None (printed "Unavailable") with the reason as
    the cell's tooltip; never invented."""
    entry = periods.get(key, {})
    if entry.get("available"):
        return rk.value(entry["value"]), ""
    reason = entry.get("reason", "")
    return None, (f"Unavailable ({reason})" if reason else "Unavailable")


def bundle_list_table(bundles: List[dict], grouped_pnl: Dict[str, dict]) -> html.Div:
    """One row per bundle (name, description, pair count, LTD-period columns from
    `period_pnl_by(conn, as_of, 'theme')`) plus a trailing "Unassigned" row for the
    empty-theme group, pinned as the table's footer (ui.tabs.ranking: the bundles rank on
    a header click, Unassigned stays last). `grouped_pnl` is `{theme_value: {period:
    {...}}}`; a bundle or "Unassigned" with no trades this period shows Unavailable with
    the reason on hover, it is never invented."""
    rows, tips = [], []
    for b in bundles:
        periods = grouped_pnl.get(b["name"], {})
        row = {"name": b["name"], "description": b["description"], "pairs": len(b["pairs"])}
        tip = {}
        for p in _PERIOD_ORDER:
            row[_PERIOD_TITLES[p]], why = _period_cell(periods, p)
            if why:
                tip[_PERIOD_TITLES[p]] = {"value": why, "type": "text"}
        rows.append(row)
        tips.append(tip)
    unassigned_periods = grouped_pnl.get("", {})
    unassigned_row = {"name": UNASSIGNED_LABEL, "description": "", "pairs": None}
    unassigned_tip = {}
    for p in _PERIOD_ORDER:
        unassigned_row[_PERIOD_TITLES[p]], why = _period_cell(unassigned_periods, p)
        if why:
            unassigned_tip[_PERIOD_TITLES[p]] = {"value": why, "type": "text"}

    columns = [rk.text("Bundle", "name"), rk.text("Description", "description"),
               rk.numeric("Pairs", "pairs", rk.count())] + \
              [rk.numeric(_PERIOD_TITLES[p], _PERIOD_TITLES[p], rk.amount(nully="Unavailable")) for p in _PERIOD_ORDER]
    table = dash_table.DataTable(
        id=BUNDLE_LIST_ID,
        columns=columns,
        data=rows, tooltip_data=tips,
        row_selectable="single",
        **rk.sortable(BUNDLE_LIST_ID),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "left", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles([_PERIOD_TITLES[p] for p in _PERIOD_ORDER],
                                              nil={"color": "var(--muted)", "fontStyle": "italic"}),
    )
    return rk.with_footer(table, [unassigned_row], footer_tooltips=[unassigned_tip])


def bundle_detail_table(pairs: List[str]) -> dash_table.DataTable:
    """The member pairs of the currently-selected bundle."""
    return dash_table.DataTable(
        id=BUNDLE_DETAIL_ID,
        columns=[rk.text("Pair", "pair")],
        data=[{"pair": p} for p in pairs] or [],
        **rk.sortable(BUNDLE_DETAIL_ID),
        style_cell={"textAlign": "left", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold"},
    )


def create_form() -> html.Div:
    return html.Div(className="toolbar", children=[
        html.Div(className="toolbar-group", children=[
            html.Label("Name"),
            dcc.Input(id=BUNDLE_NAME_INPUT_ID, type="text", placeholder="bundle name"),
        ]),
        html.Div(className="toolbar-group", children=[
            html.Label("Description"),
            dcc.Input(id=BUNDLE_DESC_INPUT_ID, type="text", placeholder="description"),
        ]),
        html.Div(className="toolbar-group", children=[
            html.Label("Pairs (comma-separated)"),
            dcc.Input(id=BUNDLE_PAIRS_INPUT_ID, type="text", placeholder="EURUSD, USDJPY"),
        ]),
        html.Button("Create bundle", id=BUNDLE_CREATE_BUTTON_ID, n_clicks=0, className="btn"),
        html.Span(id=BUNDLE_STATUS_ID, className="status-line", style={"marginLeft": "8px"}),
    ])


def add_remove_form() -> html.Div:
    return html.Div(className="toolbar", children=[
        html.Div(className="toolbar-group", children=[
            html.Label("Add pair to selected bundle"),
            dcc.Input(id=BUNDLE_ADD_PAIR_INPUT_ID, type="text", placeholder="EURUSD"),
            html.Button("Add", id=BUNDLE_ADD_PAIR_BUTTON_ID, n_clicks=0, className="btn"),
        ]),
        html.Div(className="toolbar-group", children=[
            html.Label("Remove pair from selected bundle"),
            dcc.Input(id=BUNDLE_REMOVE_PAIR_INPUT_ID, type="text", placeholder="EURUSD"),
            html.Button("Remove", id=BUNDLE_REMOVE_PAIR_BUTTON_ID, n_clicks=0, className="btn"),
        ]),
    ])


def layout() -> html.Div:
    return html.Div(className="blotter-bundles", children=[
        html.Div(id="blotter-bundle-list-container"),
        create_form(),
        add_remove_form(),
        dcc.Store(id=BUNDLE_SELECTED_ID),
        dcc.Store(id=BUNDLE_REVISION_ID),
        html.Div(id="blotter-bundle-detail-container"),
    ])
