"""Smoke tests for the five-tab app of Phase G (2026-09-29: Book, P&L, Risk, Blotter, Data;
Exposure merged into Risk, its key "curve" hidden), on the golden sample book
(`tests/golden_book.py::build_book`, as of 2026-09-18) written to a file in tmp and read as the
app reads it.

What is pinned here is the shell and the identities that must hold whatever the screens look
like: the app builds and wires without a duplicate id or output, every tab renders on the sample
without raising, each tab gathers its reasons in one "Data issues" drawer, the P&L tab's per-trade
figures add up to the header's figure per period, the Book line (the whole book, one row per
trade) equals the header's Daily, MTD, YTD and LTD to the cent, and no page or layout response is
ever cacheable. Nothing about a column list, a label or a card list is pinned: that is the
screens' own business.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from dash import html  # noqa: E402
from dash.development.base_component import Component  # noqa: E402

from data.ingest import schema  # noqa: E402
from ui import app as uiapp  # noqa: E402
from ui.tabs import blotter, blotter_fills, book, header, market_data, pnl, risk  # noqa: E402
from ui.tabs.formatting import issues_drawer  # noqa: E402

AS_OF = "2026-09-18"
TABS = ["Book", "P&L", "Risk", "Blotter", "Data"]
KEYS = ["book", "pnl", "risk", "blotter", "market-data"]


# --------------------------------------------------------------------------- helpers
def _walk(node):
    """Every node of a Dash tree, components and strings alike (nested lists flattened)."""
    if isinstance(node, (list, tuple)):
        for child in node:
            yield from _walk(child)
        return
    yield node
    children = getattr(node, "children", None)
    if children is not None:
        yield from _walk(children)


def _components(node):
    return [n for n in _walk(node) if isinstance(n, Component)]


def _text(node) -> str:
    return " ".join(n for n in _walk(node) if isinstance(n, str))


DRAWER_TITLES = ("Data issues (", "Not included (", "Notes (")   # the Risk tab's drawer is split in two (2026-09-28)


def _drawers(node):
    """The `formatting.issues_drawer` Details of a tree: a Summary reading "Data issues (N)" (the
    Risk tab: "Not included (N)" and "Notes (N)")."""
    found = []
    for n in _components(node):
        if isinstance(n, html.Details) and isinstance(n.children, list) and n.children:
            first = n.children[0]
            if isinstance(first, html.Summary) and str(first.children).startswith(DRAWER_TITLES):
                found.append(n)
    return found


def _drawer_items(drawer) -> int:
    """The items a drawer lists: the rows of its table's body (a table since the layout wave of
    2026-09-29), each run of plain rows drawn as one static Markdown block
    (`formatting.static_runs`) counted by its `<tr>` elements."""
    body = next(c for c in drawer.children[1].children if isinstance(c, html.Tbody))
    n = 0
    for child in body.children:
        if isinstance(child, dash.dcc.Markdown):
            n += len(re.findall(r"<tr[\s>]", str(child.children)))
        else:
            n += 1
    return n


def _ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _callback(app, output_prefix: str):
    """The raw function of the callback whose (first) output starts with `output_prefix`."""
    key = next(k for k in app.callback_map if k.startswith(output_prefix))
    wrapped = app.callback_map[key]["callback"]
    return getattr(wrapped, "__wrapped__", wrapped)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def golden_path(tmp_path_factory):
    from tests.golden_book import build_book
    path = tmp_path_factory.mktemp("smoke") / "golden.db"
    build_book(schema.connect(str(path))).close()
    return path


@pytest.fixture(scope="module")
def app(golden_path):
    return uiapp.create_app(db_path=golden_path, start_feed=False)


@pytest.fixture(scope="module")
def bodies(app, golden_path):
    """Every tab's rendered body on the as-of, keyed by tab key. Blotter and Data render through
    their own body callbacks (their blocks are that callback's outputs, not one module-level
    `render`); only the outputs that are components are kept (the others are stores and styles)."""
    out = {}
    out["book"] = book.render(AS_OF, golden_path)
    out["pnl"] = pnl.render(AS_OF, golden_path)
    out["risk"] = risk.render(AS_OF, golden_path)
    # Blotter: (last upload, trade-set signature, sub-tab style, fills card style, upload history, drawer)
    blotter_outputs = _callback(app, f"..{blotter.CONTENT_ID}.children")(AS_OF, blotter.SCOPE_ORDER[0])
    out["blotter"] = html.Div([o for o in blotter_outputs if isinstance(o, Component)])
    data_outputs = _callback(app, f"..{market_data.BODY_ID}.children")(AS_OF)
    out["market-data"] = html.Div([o for o in data_outputs if isinstance(o, Component)])
    return out


# --------------------------------------------------------------------------- (a) the shell
def test_the_app_builds_with_five_tabs_no_duplicate_ids_and_no_duplicate_outputs(app):
    assert uiapp.VISIBLE_TABS == TABS
    assert [uiapp.TAB_KEYS[label] for label in TABS] == KEYS
    assert callable(app.layout)                       # built on every page load, so today is fresh
    layout = app.layout()
    tabs = layout.children[0].children[0]
    assert isinstance(tabs, dash.dcc.Tabs)
    assert [t.label for t in tabs.children] == TABS
    assert [t.value for t in tabs.children] == KEYS
    assert tabs.value == "book"                       # the app opens on Book
    body_ids = [getattr(b, "id", None) for b in next(c for c in layout.children if getattr(c, "id", None) == "tab-bodies").children]
    assert body_ids == [uiapp.tab_body_id(label) for label in TABS]
    # no id appears twice in the assembled layout (a pattern-matching id compares by its JSON)
    ids = [getattr(n, "id", None) for n in _components(layout)]
    keys = [json.dumps(i, sort_keys=True) if isinstance(i, dict) else i for i in ids if i is not None]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, dupes
    # every output is claimed by one callback only (allow_duplicate outputs carry an @hash suffix)
    assert app.callback_map, "create_app registered no callback"
    outputs = [o for k in app.callback_map for o in k.strip(".").split("...") if "@" not in o]
    assert len(outputs) == len(set(outputs)), sorted({o for o in outputs if outputs.count(o) > 1})
    # every tab body is built and shown / hidden by the one callback (`build_tab_bodies`: one tab mounted)
    body_key = next(k for k in app.callback_map if k.startswith("..tab-body-book.children"))
    for label in TABS:
        assert f"{uiapp.tab_body_id(label)}.children" in body_key
        assert f"{uiapp.tab_body_id(label)}.style" in body_key


# --------------------------------------------------------------------------- (b) every tab renders
@pytest.mark.parametrize("key", KEYS)
def test_every_tab_renders_a_layout_on_the_as_of(bodies, key):
    body = bodies[key]
    assert isinstance(body, Component), f"{key}: {type(body).__name__}"
    assert _components(body), f"{key}: an empty body"
    text = _text(body)
    assert "could not be built" not in text and "Traceback" not in text, f"{key}: {text[:300]}"


@pytest.mark.parametrize("key", KEYS)
def test_every_tab_gathers_its_reasons_in_one_data_issues_drawer(bodies, golden_path, key):
    """One drawer per tab, present whenever the tab has something to say. The Blotter may have
    nothing to say on the sample (every fill landed in a trade): its drawer is then absent by
    design (`formatting.issues_drawer` returns None for no items), which is checked against the
    tab's own issue list (`blotter_fills.fills_frame`) rather than assumed."""
    drawers = _drawers(bodies[key])
    assert len(drawers) <= (2 if key == "risk" else 1), f"{key}: {len(drawers)} drawers"
    if key == "blotter":
        conn = _ro(golden_path)
        try:
            _df, issues, _with = blotter_fills.fills_frame(conn, AS_OF)
        finally:
            conn.close()
        assert bool(drawers) == (issues_drawer(issues) is not None)
        return
    assert drawers, f"{key}: no Data issues drawer"
    for drawer in drawers:
        summary = str(drawer.children[0].children)
        n = int(summary[summary.index("(") + 1:-1])
        assert n == _drawer_items(drawer) > 0


# --------------------------------------------------------------------------- (c) P&L tab = header
def _header_cards(conn):
    cards = header._build_figures(conn, AS_OF)
    return {c.children[0].children: c for c in cards if getattr(c, "className", "") == "header-figure"}


def _known(values):
    return [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]


def test_the_pnl_tabs_per_trade_figures_add_up_to_the_headers_figure_per_period(golden_path):
    """The identity the P&L tab is built on: `pnl.period_rows` gives every trade its figure for
    the period by the header's own split (`header._priced_single` / `_priced_diff`), so the
    known figures summed are the header's figure to the dollar, for Daily, 5d, MTD, YTD and LTD.
    The header shows Daily, MTD, YTD and LTD as cards (the full figure the first line of the
    hover) and 5d as a sentence on the Daily's hover."""
    from ui.tabs.blotter_pricing import priced_value_book
    conn = _ro(golden_path)
    try:
        df_today = priced_value_book(conn, AS_OF)[0]
        assert not df_today.empty
        cards = _header_cards(conn)
        views = {key: pnl.period_rows(conn, AS_OF, key, df_today) for key in pnl.PERIODS}
    finally:
        conn.close()
    assert set(pnl.PERIODS) == {"daily", "d5", "mtd", "ytd", "ltd"}
    for key, view in views.items():
        assert len(view.rows) == len(df_today)
        entry = view.entry
        total = sum(_known(view.rows["value"].tolist()))
        if entry.get("available"):
            assert total == pytest.approx(entry["value"], abs=1e-6), key
        else:
            assert not _known(view.rows["value"].tolist()), key      # n/a as a whole: every row n/a too
            assert entry.get("reason"), key
    for key, title in (("daily", "Daily"), ("mtd", "MTD"), ("ytd", "YTD"), ("ltd", "LTD")):
        value_div = cards[title].children[1]
        entry = views[key].entry
        if entry.get("available"):
            assert value_div.title.splitlines()[0] == header._fmt_usd(sum(_known(views[key].rows["value"].tolist()))), key
        else:
            assert value_div.children == header.MISSING and value_div.title == entry["reason"], key
    d5 = views["d5"].entry
    daily_hover = cards["Daily"].children[1].title
    if d5.get("available"):
        assert f"5d {header._fmt_usd(sum(_known(views['d5'].rows['value'].tolist())))}" in daily_hover
    else:
        assert f"5d n/a: {d5['reason']}" in daily_hover
    # at least the LTD of the sample is a real figure, so the identity above is not vacuous
    assert views["ltd"].entry["available"] and _known(views["ltd"].rows["value"].tolist())


# --------------------------------------------------------------------------- (d) the Book table = header
def test_the_book_line_equals_the_headers_daily_mtd_ytd_and_ltd_to_the_cent(golden_path, bodies):
    """The identity the Book is built on (Phase G, 2026-09-29: one row per trade): every fill of
    the as-of book sits on exactly one trade row (a PBRoot name, or the row of fills on no trade),
    and each fill's period figure is `pnl.period_rows`' per-trade figure, the header's own split;
    so the Book line (`book.totals` over every trade) has the header's Daily, MTD, YTD and LTD,
    built through the same functions the app uses."""
    conn = _ro(golden_path)
    try:
        data = book.gather(conn, AS_OF)
        cards = _header_cards(conn)
    finally:
        conn.close()
    trades = data["trades"]
    assert trades, "the sample book has no trade"
    ids = [str(t) for tr in trades for t in tr.get("trade_ids") or []]
    assert len(ids) == len(set(ids)) == len(data["df"])          # every fill once
    tot = book.totals(data, trades)
    for key, title in (("daily", "Daily"), ("mtd", "MTD"), ("ytd", "YTD"), ("ltd", "LTD")):
        total, _excluded, _reasons = tot[key]
        entry = data["views"][key].entry
        value_div = cards[title].children[1]
        if entry.get("available"):
            assert total == pytest.approx(entry["value"], abs=0.005), key
            assert value_div.title.splitlines()[0] == header._fmt_usd(total), key
        else:
            assert total is None and value_div.children == header.MISSING, key
    # the rendered Book tab carries the table with its Book line first, unfiltered
    table = next(n for n in _components(bodies["book"]) if getattr(n, "id", None) == book.TABLE_ID)
    first = table.children[1].children[0]
    assert "book-total" in (getattr(first, "className", "") or "").split()
    assert _text(first).startswith("Book ·")


# --------------------------------------------------------------------------- (e) never a stale page
def test_the_index_and_the_layout_are_served_no_store(app):
    client = app.server.test_client()
    for path in ("/", "/_dash-layout", "/_dash-dependencies"):
        response = client.get(path)
        assert response.status_code == 200, path
        cache = response.headers.get("Cache-Control", "")
        assert "no-store" in cache and "max-age=0" in cache, (path, cache)
    assert uiapp.no_store_for("/") and uiapp.no_store_for("/_dash-layout")
    assert not uiapp.no_store_for("/_dash-component-suites/dash/dash-renderer/build/dash_renderer.v3.js")
    assert not uiapp.no_store_for("/assets/style.css?m=1")
