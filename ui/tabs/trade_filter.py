"""The one filter bar, Group switch and headline of the trade tabs (Phase G, 2026-09-29).

User, 2026-09-29: "a way to split the rows in the table any which way - with the headline updating
to only being calculated numbers for rows showing". One bar, identical on Book, P&L and (round 2)
Risk, directly above each tab's headline: Search (free text over the trade name, what it is, every
leg's name and broker symbol), Type, Commodity and Trade (multi-selects of the values present, each
with its trade count, "All" by default), Flags only, the Group switch (None | Type | Commodity; the
P&L tab reads None as "by trade") and Clear (only when something is set; it keeps the group).

A filter keeps or drops WHOLE trades (`keeps`): a leg is never filtered out of its trade. What is
set on one tab is set on all three: the state lives in one session `dcc.Store` (`STORE_ID`, in the
app's layout, outside every tab), each tab's bar is rendered from it when the tab mounts
(`register_bar`), and a change to a control writes it back (`register`: one callback for every
tab's controls, by pattern). Every tab's table and headline render from the store, never from the
controls, so a tab opened later shows the same slice. The top bar is never filtered.

The trades are `engine.spreads.trade_book`'s (`ui.tabs.blotter_pricing.shared_trade_book`), plus
the Book's pseudo-trade of the fills that carry no trade name. Nothing here prices or sums:
`keeps`, the option lists and the group labels only read the trade's own fields.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import dash
from dash import ALL, Input, Output, State, dcc, html

from ui.revision import DATA_REVISION_ID
from ui.tabs.formatting import about, plain_words
from ui.tabs.header import AS_OF_STORE_ID

STORE_ID = "trade-filter-store"             # the shared state, session: {search, type, commodity, trade, flags, group}
FILLS_STORE_ID = "trade-filter-see-fills"   # "See fills" on a Book trade: {trade, n} for the Blotter (round 2 reads it)
SEARCH_TYPE = "tf-search"                   # the controls' pattern ids: {"type": ..., "tab": <tab key>}
TYPE_TYPE = "tf-type"
COMMODITY_TYPE = "tf-commodity"
TRADE_TYPE = "tf-trade"
FLAGS_TYPE = "tf-flags"
GROUP_TYPE = "tf-group"
CLEAR_TYPE = "tf-clear"
LINK_TYPE = "tf-link"                       # a link that filters to one trade and opens a tab: {"type", "to", "trade"}

GROUP_NONE = "none"
GROUP_BY_TYPE = "type"
GROUP_BY_COMMODITY = "commodity"
GROUPS = (GROUP_NONE, GROUP_BY_TYPE, GROUP_BY_COMMODITY)
DEFAULT_STATE: Dict[str, Any] = {"search": "", "type": [], "commodity": [], "trade": [], "flags": False,
                                 "group": GROUP_NONE}
FLAGS_ON = "on"

# The trade's type as engine.spreads.trades gives it, in words (long, short) and in the fixed order
# the groups and the Type list follow.
TYPE_WORDS = {"CALENDAR": "Calendar", "CROSS_EXCHANGE": "Cross-exchange", "CROSS_PRODUCT": "Cross-product",
              "MIXED": "Mixed", "OUTRIGHT": "Outright", "": "Hedges only"}
TYPE_SHORT = {"CALENDAR": "Calendar", "CROSS_EXCHANGE": "Cross-exch", "CROSS_PRODUCT": "Cross-prod",
              "MIXED": "Mixed", "OUTRIGHT": "Outright", "": "Hedges"}
TYPE_ORDER = ("CALENDAR", "CROSS_EXCHANGE", "CROSS_PRODUCT", "MIXED", "OUTRIGHT", "")
UNASSIGNED = "No trade name"                # the Book's pseudo-trade of the fills with no PBRoot name
_LAST_FAMILIES = ("Cross-product", "FX", "Other")

FILTER_ABOUT = ("Filters keep or drop whole trades, never a leg of one. What is set here is set on Book, P&L and "
                "Risk alike; the top bar always shows the whole book.")
GROUP_ABOUT = ("Group the rows by the trade's type or its commodity family, each group with its subtotals. A trade "
               "is never split across groups: a trade mixing families is under Cross-product.")


# --------------------------------------------------------------------------- the state
def normal(state: Optional[dict]) -> Dict[str, Any]:
    """The store's value, every key present and of its type."""
    s = dict(DEFAULT_STATE)
    for k, v in (state or {}).items():
        if k in ("type", "commodity", "trade"):
            s[k] = [str(x) for x in (v or []) if x is not None]
        elif k == "search":
            s[k] = str(v or "")
        elif k == "flags":
            s[k] = bool(v)
        elif k == "group":
            s[k] = v if v in GROUPS else GROUP_NONE
    return s


def is_filtered(state: Optional[dict]) -> bool:
    """True when anything but the group is set: the rows showing may be fewer than the book."""
    s = normal(state)
    return bool(s["search"].strip() or s["type"] or s["commodity"] or s["trade"] or s["flags"])


def state_from_controls(search, types, commodities, trades, flags, group) -> Dict[str, Any]:
    return normal({"search": search, "type": types, "commodity": commodities, "trade": trades,
                   "flags": bool(flags) and FLAGS_ON in (flags or []), "group": group})


def one_trade(name: str, group: str = GROUP_NONE) -> Dict[str, Any]:
    """The state of a link to one trade ("P&L history", "Risk" on a Book trade): that trade alone."""
    return normal({"trade": [name], "group": group})


# --------------------------------------------------------------------------- reading a trade
def type_code(trade: dict) -> str:
    return str(trade.get("type") or "")


def type_label(code: str, short: bool = False) -> str:
    return (TYPE_SHORT if short else TYPE_WORDS).get(str(code or ""), str(code or "").replace("_", " ").capitalize())


NOT_TYPED = "Not typed"


def trade_type_label(trade: dict, short: bool = False) -> str:
    """The trade's type in words; 'Not typed' for a trade whose only legs are contracts the app
    does not recognise (the engine's type '' there means "not typed until they are mapped", not
    "hedges only"; every row loads, 2026-09-29)."""
    code = type_code(trade)
    legs = trade.get("legs") or []
    if not code and any(leg.get("unrecognised") for leg in legs) and not any(leg.get("hedge") for leg in legs):
        return NOT_TYPED
    return type_label(code, short=short)


NOT_TYPED_CODE = "NOT_TYPED"   # the Type filter's value for a trade `trade_type_label` calls "Not typed"


def type_key(trade: dict) -> str:
    """The Type filter's value of a trade: its engine type, or `NOT_TYPED_CODE` for a trade of only
    unrecognised contracts, so the filter's label is the row's own ("Not typed", never "Hedges only")."""
    return NOT_TYPED_CODE if trade_type_label(trade) == NOT_TYPED else type_code(trade)


def key_label(key: str, short: bool = False) -> str:
    """A Type filter value in words (`type_key`)."""
    return NOT_TYPED if key == NOT_TYPED_CODE else type_label(key, short=short)


def family_label(trade: dict) -> str:
    """The trade's commodity family in words ('Copper', 'Ferrous', 'Cross-product', 'FX'); 'Other'
    when the engine gives none."""
    fam = str(trade.get("commodity_family") or "").strip()
    if not fam:
        return "Other"
    if fam.lower() == "cross-product":
        return "Cross-product"
    if fam.lower() == "fx":
        return "FX"
    return fam[0].upper() + fam[1:]


def _search_blob(trade: dict) -> str:
    parts: List[str] = [str(trade.get("trade") or ""), str(trade.get("what_it_is") or "")]
    for leg in trade.get("legs") or []:
        parts.append(str(leg.get("name") or ""))
        parts.append(str(leg.get("contract_id") or ""))
        parts.extend(str(s) for s in leg.get("broker_symbols") or [])
    return " ".join(parts).lower()


def has_flags(trade: dict) -> bool:
    return bool(trade.get("flags"))


def keeps(trade: dict, state: Optional[dict]) -> bool:
    """Whether the whole trade passes the filters (never a leg of it)."""
    s = normal(state)
    text = s["search"].strip().lower()
    if text and not all(word in _search_blob(trade) for word in text.split()):
        return False
    if s["type"] and type_key(trade) not in s["type"]:
        return False
    if s["commodity"] and family_label(trade) not in s["commodity"]:
        return False
    if s["trade"] and str(trade.get("trade") or "") not in s["trade"]:
        return False
    if s["flags"] and not has_flags(trade):
        return False
    return True


def apply(trades: Iterable[dict], state: Optional[dict]) -> List[dict]:
    return [t for t in trades if keeps(t, state)]


def group_of(trade: dict, group: str) -> str:
    """The group label a trade is shown under ('' when not grouped)."""
    if group == GROUP_BY_TYPE:
        return trade_type_label(trade)
    if group == GROUP_BY_COMMODITY:
        return family_label(trade)
    return ""


def group_order(labels: Iterable[str], group: str) -> List[str]:
    """The groups in their fixed order: types as `TYPE_ORDER`, families A to Z with Cross-product,
    FX and Other last."""
    labels = list(dict.fromkeys(labels))
    if group == GROUP_BY_TYPE:
        rank = {type_label(c): n for n, c in enumerate(TYPE_ORDER)}
        rank[NOT_TYPED] = len(TYPE_ORDER)
        return sorted(labels, key=lambda x: (rank.get(x, 99), x))
    return sorted(labels, key=lambda x: (x in _LAST_FAMILIES, _LAST_FAMILIES.index(x) if x in _LAST_FAMILIES else 0, x))


def default_order(trades: Sequence[dict]) -> List[dict]:
    """The stable default order: commodity family, then trade name A to Z."""
    rank = {f: n for n, f in enumerate(group_order([family_label(t) for t in trades], GROUP_BY_COMMODITY))}
    return sorted(trades, key=lambda t: (rank.get(family_label(t), 99), str(t.get("trade") or "")))


def options_for(trades: Sequence[dict]) -> Dict[str, List[dict]]:
    """Each multi-select's choices: only the values present, with their trade count ('Copper (1)')."""
    def count(values: List[str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for v in values:
            out[v] = out.get(v, 0) + 1
        return out
    named = [t for t in trades if str(t.get("trade") or "") and t.get("trade") != UNASSIGNED]
    types = count([type_key(t) for t in named])
    fams = count([family_label(t) for t in named])
    return {
        "type": [{"label": f"{key_label(c)} ({types[c]})", "value": c} for c in TYPE_ORDER if c in types]
        + [{"label": f"{key_label(c)} ({n})", "value": c} for c, n in types.items() if c not in TYPE_ORDER],
        "commodity": [{"label": f"{f} ({fams[f]})", "value": f} for f in group_order(fams, GROUP_BY_COMMODITY)],
        "trade": [{"label": str(t["trade"]), "value": str(t["trade"])}
                  for t in sorted(named, key=lambda t: str(t["trade"]))],
    }


# --------------------------------------------------------------------------- the bar
def bar_id(tab: str) -> str:
    return f"tf-bar-{tab}"


def _cid(kind: str, tab: str) -> dict:
    return {"type": kind, "tab": tab}


def _group_options(tab: str) -> List[dict]:
    first = "Trade" if tab == "pnl" else "None"
    return [{"label": first, "value": GROUP_NONE}, {"label": "Type", "value": GROUP_BY_TYPE},
            {"label": "Commodity", "value": GROUP_BY_COMMODITY}]


def bar(tab: str, state: Optional[dict], options: Optional[Dict[str, List[dict]]] = None) -> html.Div:
    """The filter bar of `tab` ('book', 'pnl', 'risk'), its controls set from the shared state."""
    s = normal(state)
    opts = options or {}

    def pick(key: str, label: str, width: int) -> html.Div:
        chosen = [str(v) for v in s[key]]      # kept even when absent today, so a filter is never lost
        known = {str(o["value"]) for o in opts.get(key) or []}
        extra = [{"label": f"{v} (none today)", "value": v} for v in chosen if v not in known]
        return html.Div(className="blotter-filter tf-filter", style={"minWidth": f"{width}px"}, children=[
            html.Label(label),
            dcc.Dropdown(id=_cid({"type": TYPE_TYPE, "commodity": COMMODITY_TYPE, "trade": TRADE_TYPE}[key], tab),
                         options=list(opts.get(key) or []) + extra, value=chosen, multi=True, placeholder="All",
                         clearable=True, className="blotter-filter-dropdown")])

    return html.Div(className="blotter-filter-bar tf-bar", children=[
        html.Div(className="blotter-filter tf-filter", children=[
            html.Label(about("Search", FILTER_ABOUT, level="span")),
            dcc.Input(id=_cid(SEARCH_TYPE, tab), type="text", value=s["search"], debounce=True,
                      placeholder="Trade, contract or symbol", className="blotter-filter-search tf-search")]),
        pick("type", "Type", 170),
        pick("commodity", "Commodity", 170),
        pick("trade", "Trade", 180),
        html.Div(className="blotter-filter tf-filter tf-flags", children=[
            html.Label("Flags"),
            dcc.Checklist(id=_cid(FLAGS_TYPE, tab), options=[{"label": " Flags only", "value": FLAGS_ON}],
                          value=[FLAGS_ON] if s["flags"] else [], className="tf-check")]),
        html.Div(className="blotter-filter tf-filter", children=[
            html.Label(about("Group" if tab != "pnl" else "Slice", GROUP_ABOUT, level="span")),
            dcc.RadioItems(id=_cid(GROUP_TYPE, tab), className="book-switch", options=_group_options(tab),
                           value=s["group"], inline=True)]),
        html.Button("Clear", id=_cid(CLEAR_TYPE, tab), n_clicks=0, className="btn btn--ghost tf-clear",
                    title="Clear every filter (the grouping stays)",
                    style={} if is_filtered(s) else {"display": "none"}),
    ])


def bar_slot(tab: str) -> html.Div:
    """Where the tab's bar is rendered (`register_bar`)."""
    return html.Div(id=bar_id(tab), className="tf-bar-slot")


# --------------------------------------------------------------------------- research figures
# Every figure the Book and Risk read from the research app's database (z, percentile, z at entry,
# carry, hedge %) goes through `research_mark`: a grey "mock history" marker with the source's
# sentence on hover whenever the research history is not real Bloomberg data
# (`engine.risk.commodity_history.research_source`, risk-history, 2026-09-29).
MOCK_WORDS = "mock history"


def research_source() -> Dict[str, str]:
    """{kind, note}: 'real' | 'mock' | 'unknown' and its one sentence; never raises."""
    try:
        from engine.risk.commodity_history import research_source as read
        src = read() or {}
        return {"kind": str(src.get("source_kind") or "unknown"), "note": str(src.get("source_note") or "")}
    except Exception as exc:  # noqa: BLE001 -- unknown, said so
        return {"kind": "unknown", "note": f"the research history's source could not be read ({type(exc).__name__})"}


def research_mark(source: Optional[Dict[str, str]] = None):
    """The grey "mock history" marker (the sentence on hover), or None when the history is real."""
    from ui.tabs.formatting import marker
    src = source if source is not None else research_source()
    if src.get("kind") == "real":
        return None
    return marker(MOCK_WORDS if src.get("kind") == "mock" else "unverified history",
                  src.get("note") or "the research history's provider is not known", "marker--small")


def research_head(source: Optional[Dict[str, str]] = None):
    """The one small "i" for the heading of a column whose every value is research (z, percentile,
    carry, hedge %, roll-down) while the research history is not real, its sentence on hover, or
    None when it is real (layout wave, 2026-09-29: one mark per column, never a badge per cell)."""
    from ui.tabs.formatting import head_info
    src = source if source is not None else research_source()
    if src.get("kind") == "real":
        return None
    head = "Research history: mock data" if src.get("kind") == "mock" else "Research history: not verified as real"
    return head_info([head, src.get("note") or "the research history's provider is not known"])


def research_words(source: Optional[Dict[str, str]] = None) -> str:
    """The same as a hover line ('' when real)."""
    src = source if source is not None else research_source()
    return "" if src.get("kind") == "real" else f"({src.get('note') or 'research history not verified as real'})"


# --------------------------------------------------------------------------- the headline
def headline(items: Sequence[Any]) -> html.Div:
    """The headline between the bar and the table: ONE light line of (label, value, hover) items
    ("Net USD −34.4k · Flags 9 · 2 red"), a value being text or a component; None items are left
    out. It carries only what neither the header nor the table's own total row shows (one place per
    number, layout wave 2026-09-29); it is visibly lighter than the header."""
    kids = []
    for item in items:
        if item is None:
            continue
        label, value, hover = (tuple(item) + ("",))[:3]
        kids.append(html.Div(className="tk-head-item", title=plain_words(hover) or None, children=[
            html.Span(label, className="tk-head-k"), html.Span(value, className="tk-head-v")]))
    return html.Div(kids, className="tk-headline")


def link(label: str, to: str, trade: str, idx: str, title: str = "") -> html.Button:
    """A quiet link that filters every trade tab to `trade` and opens tab `to` ('pnl', 'risk',
    'blotter')."""
    return html.Button(label, id={"type": LINK_TYPE, "to": to, "trade": trade, "idx": idx}, n_clicks=0, type="button",
                       className="tab-link tk-link", title=title or None)


# --------------------------------------------------------------------------- callbacks
def register(app, main_tabs_id: str) -> None:
    """The controls -> the store (every tab's, by pattern); Clear -> the controls; the Clear
    button's visibility; the links that filter to one trade and open a tab."""
    ctl = [Input(_cid(k, ALL), p) for k, p in ((SEARCH_TYPE, "value"), (TYPE_TYPE, "value"),
                                                (COMMODITY_TYPE, "value"), (TRADE_TYPE, "value"),
                                                (FLAGS_TYPE, "value"), (GROUP_TYPE, "value"))]

    @app.callback(Output(STORE_ID, "data"), *ctl, State(STORE_ID, "data"), prevent_initial_call=True)
    def _sync(searches, types, commodities, trades, flags, groups, current):
        if not searches:                       # no bar mounted
            return dash.no_update
        new = state_from_controls(searches[0], types[0] if types else [], commodities[0] if commodities else [],
                                  trades[0] if trades else [], flags[0] if flags else [],
                                  groups[0] if groups else GROUP_NONE)
        return dash.no_update if new == normal(current) else new

    @app.callback(Output(_cid(SEARCH_TYPE, ALL), "value", allow_duplicate=True),
                  Output(_cid(TYPE_TYPE, ALL), "value", allow_duplicate=True),
                  Output(_cid(COMMODITY_TYPE, ALL), "value", allow_duplicate=True),
                  Output(_cid(TRADE_TYPE, ALL), "value", allow_duplicate=True),
                  Output(_cid(FLAGS_TYPE, ALL), "value", allow_duplicate=True),
                  Input(_cid(CLEAR_TYPE, ALL), "n_clicks"), prevent_initial_call=True)
    def _clear(clicks):
        n = len(clicks or [])
        if not any(clicks or []):
            return [dash.no_update] * 5
        return [""] * n, [[]] * n, [[]] * n, [[]] * n, [[]] * n

    @app.callback(Output(_cid(CLEAR_TYPE, ALL), "style"), Input(STORE_ID, "data"),
                  State(_cid(CLEAR_TYPE, ALL), "id"))
    def _clear_shown(state, ids):
        return [{} if is_filtered(state) else {"display": "none"} for _ in ids or []]

    @app.callback(Output(STORE_ID, "data", allow_duplicate=True),
                  Output(main_tabs_id, "value", allow_duplicate=True),
                  Output(FILLS_STORE_ID, "data"),
                  Input({"type": LINK_TYPE, "to": ALL, "trade": ALL, "idx": ALL}, "n_clicks"),
                  State(STORE_ID, "data"), prevent_initial_call=True)
    def _follow(_clicks, current):
        trig = dash.ctx.triggered_id
        hit = dash.ctx.triggered or []
        if not isinstance(trig, dict) or not (hit and hit[0].get("value")):
            return dash.no_update, dash.no_update, dash.no_update
        to, name = str(trig.get("to") or ""), str(trig.get("trade") or "")
        if to == "blotter":
            return dash.no_update, to, {"trade": name}
        return one_trade(name, normal(current)["group"]), to, dash.no_update


def register_bar(app, tab: str, get_db_path: Callable[[], object],
                 options_of: Callable[[str, object], Dict[str, List[dict]]]) -> None:
    """Render `tab`'s bar from the shared state when the tab mounts and on every as-of or data
    change (`options_of(as_of, db_path)`: the choices present in the book); never on a filter
    change, so the search box keeps its focus."""
    @app.callback(Output(bar_id(tab), "children"), Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"),
                  State(STORE_ID, "data"))
    def _bar(as_of, _rev, state):
        try:
            opts = options_of(as_of, get_db_path()) if as_of else {}
        except Exception:  # noqa: BLE001 -- the bar still filters on the values set
            opts = {}
        return bar(tab, state, opts)
