"""The trade tabs' shared filters, column funnels, Group switch and headline (Phase G, 2026-09-29).

User, 2026-09-29: "a way to split the rows in the table any which way - with the headline updating
to only being calculated numbers for rows showing", then, after five rounds: "the table columns -
and above theres filters with the same names". The filters live in the column headings,
spreadsheet-style (CLAUDE.md "Screens redesign plan", Phase G): each navy heading's title sorts
and its funnel opens a panel under it, a tick list of the values present on a text column (with a
search box that narrows the list) and one comparison (`> 0`, `< -10k`) on a number column; the
funnel is gold while it filters and one panel is open at a time (`ui/assets/book_filters.js`).
Above a table, in its own title strip, only the free-text search, the Group switch (a view, not a
filter; the P&L tab calls it Slice) and a "Clear filters" link while a filter is set (`bar`).

The funnels and their panels (`funnel`, `pop_list`, `pop_number`, `pop_text`, `head_th`) are the
one component every tab's table uses, Blotter and Data included (each with its own store there).
On Book, P&L and Risk, Type, Trade and Commodity (the commodity family: the Book's "What it is"
funnel, the Trade funnel on P&L and Risk) carry across the three tabs; a number column's
comparison is the tab's own (`tab_filters`).

A filter keeps or drops WHOLE trades (`keeps`): a leg is never filtered out of its trade. The state
lives in one session `dcc.Store` (`STORE_ID`, in the app's layout, outside every tab); each tab's
strip is rendered from it when the tab mounts (`register_bar`), its table (heads and funnels
included) renders from the store, and a change to a funnel, the search or the switch writes it
back (`register`: one callback for every tab's controls, by pattern). The top bar is never filtered.

The trades are `engine.spreads.trade_book`'s (`ui.tabs.blotter_pricing.shared_trade_book`), plus
the Book's pseudo-trade of the fills that carry no trade name. Nothing here prices or sums:
`keeps`, the option lists and the group labels only read the trade's own fields.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import dash
from dash import ALL, Input, Output, State, dcc, html

from ui.revision import DATA_REVISION_ID
from ui.tabs.formatting import plain_words
from ui.tabs.header import AS_OF_STORE_ID

STORE_ID = "trade-filter-store"             # the shared state, session: {search, type, commodity, trade, group, cols}
FILLS_STORE_ID = "trade-filter-see-fills"   # "See fills" on a Book trade: {trade, n} for the Blotter (round 2 reads it)
SEARCH_TYPE = "tf-search"                   # the strip's controls' pattern ids: {"type": ..., "tab": <tab key>}
GROUP_TYPE = "tf-group"
CLEAR_TYPE = "tf-clear"
COL_TYPE = "tf-col"                         # a funnel's control: {"type", "tab", "part": type|commodity|trade|<column>}
LINK_TYPE = "tf-link"                       # a link that filters to one trade and opens a tab: {"type", "to", "trade"}
SHARED_PARTS = ("type", "commodity", "trade")   # the funnel parts carried across Book, P&L and Risk

GROUP_NONE = "none"
GROUP_BY_TYPE = "type"
GROUP_BY_COMMODITY = "commodity"
GROUP_BY_CONTRACT = "contract"              # the P&L tab's Contract slice only (2026-09-30); Book and Risk read it as none
GROUPS = (GROUP_NONE, GROUP_BY_TYPE, GROUP_BY_COMMODITY, GROUP_BY_CONTRACT)
TRADE_GROUPS = (GROUP_NONE, GROUP_BY_TYPE, GROUP_BY_COMMODITY)   # the groupings of whole trades (Book, Risk)
DEFAULT_STATE: Dict[str, Any] = {"search": "", "type": [], "commodity": [], "trade": [], "group": GROUP_NONE,
                                 "cols": {}}
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

FILTER_ABOUT = ("Free text over the trade name, what it is, every leg's contract and broker symbol. Each column's "
                "own filter is the funnel in its heading. Filters keep or drop whole trades, never a leg of one; "
                "Type, Trade and Commodity carry across Book, P&L and Risk; the top bar always shows the whole book.")
GROUP_ABOUT = ("Group the rows by the trade's type or its commodity family, each group with its subtotals. A trade "
               "is never split across groups: a trade mixing families is under Cross-product.")
SLICE_ABOUT = ("Slice the table by spread (one row per trade), by strategy (calendar, cross-exchange, cross-product), "
               "by commodity family, or by contract: one row per contract, with the part of each trade that holds it. "
               "Only the Contract slice splits a trade; the others keep each trade whole.")


# --------------------------------------------------------------------------- the state
def normal(state: Optional[dict]) -> Dict[str, Any]:
    """The store's value, every key present and of its type. `cols` holds each tab's own column
    filters, keyed '<tab>:<column>': a comparison's text, or a list of ticked values."""
    s = dict(DEFAULT_STATE)
    s["cols"] = {}
    for k, v in (state or {}).items():
        if k in SHARED_PARTS:
            s[k] = [str(x) for x in (v or []) if x is not None]
        elif k == "search":
            s[k] = str(v or "")
        elif k == "group":
            s[k] = v if v in GROUPS else GROUP_NONE
        elif k == "cols" and isinstance(v, dict):
            for ck, cv in v.items():
                if isinstance(cv, (list, tuple)):
                    vals = [str(x) for x in cv if x is not None]
                    if vals:
                        s["cols"][str(ck)] = vals
                elif str(cv or "").strip():
                    s["cols"][str(ck)] = str(cv).strip()
    return s


def tab_filters(state: Optional[dict], tab: str) -> Dict[str, Any]:
    """The column filters `tab` itself set: {column: comparison text | [ticked values]}."""
    pre = f"{tab}:"
    return {k[len(pre):]: v for k, v in normal(state)["cols"].items() if k.startswith(pre)}


def is_filtered(state: Optional[dict], tab: Optional[str] = None) -> bool:
    """True when anything but the group is set: the rows showing may be fewer than the book. With
    `tab`, that tab's own column filters count too."""
    s = normal(state)
    if s["search"].strip() or s["type"] or s["commodity"] or s["trade"]:
        return True
    return bool(tab_filters(s, tab)) if tab else False


def one_trade(name: str, group: str = GROUP_NONE) -> Dict[str, Any]:
    """The state of a link to one trade ("P&L history", "Risk" on a Book trade): that trade alone."""
    return normal({"trade": [name], "group": group})


# --------------------------------------------------------------------------- a column's comparison
_CMP = re.compile(r"^\s*(>=|<=|!=|==|=|>|<)?\s*([-+−]?\s*\d[\d,]*(?:\.\d*)?|[-+−]?\s*\.\d+)\s*([kmb%])?\s*$", re.I)
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9, "%": 1.0}
NUMBER_HINT = "For example > 0, < -10k, >= 1.5m or = 3; Enter to apply, empty for all"


def parse_compare(text: Optional[str]) -> Optional[Tuple[str, float]]:
    """'> 0', '< -10000', '>= 1.5m', '= 3', '10k' (equal) -> (op, value); None for an empty or
    unreadable box (then it filters nothing)."""
    m = _CMP.match(str(text or "")) if text else None
    if not m:
        return None
    op = {"==": "=", None: "="}.get(m.group(1), m.group(1))
    num = float(m.group(2).replace(",", "").replace("−", "-").replace(" ", ""))
    return op, num * _SCALE.get((m.group(3) or "").lower(), 1.0)


def passes(value: Optional[float], test: Optional[Tuple[str, float]]) -> bool:
    """Whether `value` meets the comparison; a row with no figure never does."""
    if test is None:
        return True
    if value is None or value != value:
        return False
    op, x = test
    return {"=": value == x, "!=": value != x, ">": value > x, "<": value < x, ">=": value >= x,
            "<=": value <= x}[op]


def keeps_cols(item: Any, filters: Dict[str, Any], value_of: Callable[[Any, str], Optional[float]],
               lists_of: Optional[Callable[[Any, str], Iterable[str]]] = None) -> bool:
    """Whether a row passes every column filter of its tab: a comparison on `value_of(item, col)`,
    a tick list on `lists_of(item, col)` (any value ticked)."""
    for col, want in filters.items():
        if isinstance(want, list):
            if lists_of is None:
                continue
            if not {str(x) for x in lists_of(item, col) or []} & set(want):
                return False
        elif not passes(value_of(item, col), parse_compare(want)):
            return False
    return True


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
    parts: List[str] = [str(trade.get("trade") or ""), str(trade.get("what_it_is") or ""),
                        str(trade.get("what_words") or "")]     # the Book's sentence ("short 30 SHFE copper ...")
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
    return True


def apply(trades: Iterable[dict], state: Optional[dict]) -> List[dict]:
    return [t for t in trades if keeps(t, state)]


def trade_group(state: Optional[dict]) -> str:
    """The grouping of whole trades the Book and Risk apply: the shared group, or none when it is the
    P&L tab's Contract slice (a grouping those tabs do not have)."""
    g = normal(state)["group"]
    return g if g in TRADE_GROUPS else GROUP_NONE


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
    """The strip's switch: Group None | Type | Commodity, and on P&L the Slice Spread (the trade) |
    Strategy (its type, renamed 2026-09-30) | Commodity family | Contract."""
    if tab == "pnl":
        return [{"label": "Spread", "value": GROUP_NONE}, {"label": "Strategy", "value": GROUP_BY_TYPE},
                {"label": "Commodity family", "value": GROUP_BY_COMMODITY},
                {"label": "Contract", "value": GROUP_BY_CONTRACT}]
    return [{"label": "None", "value": GROUP_NONE}, {"label": "Type", "value": GROUP_BY_TYPE},
            {"label": "Commodity", "value": GROUP_BY_COMMODITY}]


HIDDEN_STYLE = {"display": "none"}
CLEAR_WORDS = "Clear filters"
CLEAR_TIP = "Every column's filter and the search back to All (the grouping stays)"


def bar(tab: str, state: Optional[dict], options: Optional[Dict[str, List[dict]]] = None) -> html.Div:
    """The table strip's own controls on `tab` ('book', 'pnl', 'risk'), set from the shared state:
    the free-text search, the Group switch (Slice on P&L) and "Clear filters" (only while a filter
    is set). Every other filter is a column's funnel (`funnel`). `options` is not read any more
    (kept for the callers of the filter bar this replaced)."""
    s = normal(state)
    return html.Div(className="tf-tools", children=[
        html.Div(className="tf-search-wrap", title=plain_words(FILTER_ABOUT), children=[
            dcc.Input(id=_cid(SEARCH_TYPE, tab), type="text", value=s["search"], debounce=True,
                      placeholder="Search trade, contract or symbol", className="blotter-filter-search tf-search",
                      autoComplete="off")]),
        html.Span(className="tf-group", children=[
            html.Label("Group" if tab != "pnl" else "Slice", className="tk-k",
                       title=plain_words(GROUP_ABOUT if tab != "pnl" else SLICE_ABOUT)),
            dcc.RadioItems(id=_cid(GROUP_TYPE, tab), className="book-switch", options=_group_options(tab),
                           value=s["group"] if tab == "pnl" else trade_group(s), inline=True)]),
        html.Button(CLEAR_WORDS, id=_cid(CLEAR_TYPE, tab), n_clicks=0, className="book-link-button tf-clear",
                    title=CLEAR_TIP, style={} if is_filtered(s, tab) else HIDDEN_STYLE),
    ])


def bar_slot(tab: str) -> html.Div:
    """Where the tab's strip controls are rendered (`register_bar`), inside the table's title strip."""
    return html.Div(id=bar_id(tab), className="tf-bar-slot")


# --------------------------------------------------------------------------- the column funnels
# Spreadsheet-style (user, 2026-09-29): each heading's title sorts, its funnel opens a panel under
# it. The panel is a <details> the browser opens and closes itself (`ui/assets/book_filters.js`
# keeps one open at a time, closes it on a click outside or Escape, narrows a long tick list by
# its search box, and re-opens it when the table re-renders under it), so opening one asks the
# server nothing. The controls inside carry pattern ids and write their tab's store.
LIST_HINT = "Nothing ticked = All"
FUNNEL_TIP = "Filter this column"
NUMBER_TITLE = "Show rows where the figure is"


def col_id(tab: str, part: str) -> dict:
    """The id of a trade tab's funnel control (`register` writes the shared store from it)."""
    return {"type": COL_TYPE, "tab": tab, "part": part}


def with_missing(options: Optional[Sequence[dict]], chosen: Sequence[str], note: str = "none today") -> List[dict]:
    """The options, plus a value ticked earlier that is absent now (so a filter is never lost)."""
    known = {str(o["value"]) for o in options or []}
    return list(options or []) + [{"label": f"{v} ({note})", "value": v} for v in chosen if str(v) not in known]


def pop_list(cid: dict, title: str, options: Optional[Sequence[dict]], value: Optional[Sequence[str]],
             search: Optional[bool] = None) -> List[Any]:
    """A tick list in a funnel's panel: its title, a box that narrows the list (browser-side) when
    the list is long, the values present with their counts; nothing ticked = All."""
    chosen = [str(v) for v in value or []]
    opts = with_missing(options, chosen)
    kids: List[Any] = [html.Div(title, className="book-pop-label")]
    if search if search is not None else len(opts) > 6:
        kids.append(dcc.Input(type="text", placeholder="Search the list", className="book-pop-search",
                              autoComplete="off"))
    kids.append(dcc.Checklist(id=cid, options=opts, value=chosen, className="book-pop-list"))
    kids.append(html.Div(LIST_HINT, className="book-pop-hint"))
    return kids


def pop_number(cid: dict, value: Optional[str], title: str = NUMBER_TITLE, hint: str = NUMBER_HINT,
               placeholder: str = "> 0") -> List[Any]:
    """One comparison in a funnel's panel ('> 0', '< -10k'), applied on Enter."""
    return pop_text(cid, value, title, placeholder, hint)


def pop_text(cid: dict, value: Optional[str], title: str, placeholder: str, hint: str = "") -> List[Any]:
    """A text box in a funnel's panel (a comparison, a date), applied on Enter or when it loses focus."""
    kids: List[Any] = [html.Div(title, className="book-pop-label"),
                       dcc.Input(id=cid, type="text", value=str(value or ""), debounce=True, placeholder=placeholder,
                                 autoComplete="off", className="book-pop-box")]
    if hint:
        kids.append(html.Div(hint, className="book-pop-hint"))
    return kids


def option_words(options: Optional[Sequence[dict]], values: Sequence[str]) -> str:
    """The ticked values in words, as their labels without the counts: 'Calendar, Cross-exch'."""
    labels = {str(o["value"]): re.sub(r"\s*\(\d[\d,]*\)$", "", str(o.get("label") or o["value"]))
              for o in options or []}
    return ", ".join(labels.get(str(v), str(v)) for v in values)


def funnel(key: str, body: Sequence[Any], active: bool, summary: str = "") -> html.Details:
    """A column's funnel and its panel. `key` names it for the browser ('book:type', unique on the
    page); gold with what it filters on hover while `active`."""
    return html.Details(className="book-pop", **{"data-tf-key": key}, children=[
        html.Summary(html.Span(className="book-funnel-icon"),
                     className="book-funnel" + (" book-funnel--on" if active else ""),
                     title=(f"Filtered: {summary}" if active and summary else ("Filtered" if active else FUNNEL_TIP))),
        html.Div(list(body), className="book-pop-panel")])


def head_th(title: Any, cls: str = "", tip: str = "", sort_id: Optional[dict] = None, arrow: str = "",
            pop: Optional[html.Details] = None, right: bool = False, note: Any = None) -> html.Th:
    """A column heading: its title (a click sorts when `sort_id` is given; the arrow only on the
    column sorted), a note (the research "i"), then its funnel. `right`: the panel opens leftwards
    (a column in the right half of the table)."""
    hover = plain_words(tip) or None
    if sort_id is not None:
        label: Any = html.Span([title, html.Span(arrow, className="book-sort-arrow")], id=sort_id, n_clicks=0,
                               className="tk-sort", title=hover)
    else:
        label = html.Span(title, title=hover)
    kids = [x for x in (label, note, pop) if x is not None]
    classes = [cls, "tk-sortable" if sort_id is not None else "", "book-th-pop-right" if right else ""]
    return html.Th(html.Div(kids, className="book-th"), className=" ".join(c for c in classes if c) or None)


def arrow_of(sort: Optional[dict], key: str) -> str:
    """The sort arrow of column `key` ('' when another column, or none, is sorted)."""
    if (sort or {}).get("key") != key:
        return ""
    return " ▼" if (sort or {}).get("dir") != "asc" else " ▲"


def trade_funnel(tab: str, state: Optional[dict], options: Dict[str, List[dict]], parts: Sequence[str],
                 key: str) -> html.Details:
    """The funnel of a trade tab's text column holding the shared lists `parts` ('type', 'trade',
    'commodity': carried across Book, P&L and Risk), in that order."""
    s = normal(state)
    titles = {"type": "Type", "trade": "Trade", "commodity": "Commodity"}
    body: List[Any] = []
    bits = []
    for part in parts:
        body += pop_list(col_id(tab, part), titles[part], options.get(part), s[part],
                         search=True if part == "trade" else None)
        if s[part]:
            bits.append(f"{titles[part]}: {option_words(options.get(part), s[part])}")
    return funnel(f"{tab}:{key}", body, bool(bits), "; ".join(bits))


def number_funnel(tab: str, state: Optional[dict], col: str, hint: str = NUMBER_HINT) -> html.Details:
    """The funnel of a trade tab's number column: one comparison, the tab's own."""
    value = tab_filters(state, tab).get(col)
    text = value if isinstance(value, str) else ""
    return funnel(f"{tab}:{col}", pop_number(col_id(tab, col), text, hint=hint), bool(text), text)


# --------------------------------------------------------------------------- research figures
# Every figure the Book and Risk read from the research app's database (z, percentile, z at entry,
# carry, hedge %) goes through `research_mark`: a grey "mock history" marker with the source's
# sentence on hover whenever the research history is not real Bloomberg data
# (`engine.risk.commodity_history.research_source`, risk-history, 2026-09-29).
MOCK_WORDS = "mock history"


def research_source() -> Dict[str, Any]:
    """{kind, note, missing, tried}: 'real' | 'mock' | 'unknown' and its one sentence; `missing`
    True when no research database is on this PC (`tried`: the paths looked at, for a hover only);
    never raises."""
    try:
        from engine.risk.commodity_history import candidates
        from engine.risk.commodity_history import research_source as read
        src = read() or {}
        missing = not str(src.get("path") or "")
        tried = ", ".join(str(c.get("path")) for c in candidates()) if missing else ""
        return {"kind": str(src.get("source_kind") or "unknown"), "note": str(src.get("source_note") or ""),
                "missing": missing, "tried": tried}
    except Exception as exc:  # noqa: BLE001 -- unknown, said so
        return {"kind": "unknown", "note": f"the research history's source could not be read ({type(exc).__name__})",
                "missing": False, "tried": ""}


# Research data not on this PC (user, 2026-09-30: three drawer lines each dumping the file path were
# unclear): ONE drawer line per tab, the path on hover only, and every engine reason that only says
# the database is missing left out of the drawer (`is_research_missing_reason`).
RESEARCH_MISSING_WHERE = "z, carry, roll-down, hedge %"
RESEARCH_MISSING_TEXT = "Research data not on this PC: z, carry, roll-down and hedge % are blank"
_RESEARCH_MISSING = re.compile(r"research history not found|no (commodity|research) (price )?history( database)?\b|"
                               r"no research database|research database|provider is unknown|no research figures|"
                               r"research data not on this PC",
                               re.IGNORECASE)


def research_missing(source: Optional[Dict[str, Any]]) -> bool:
    """True when the research database is not on this PC."""
    return bool((source or {}).get("missing"))


def is_research_missing_reason(text: Any) -> bool:
    """True for a reason that only says the research data is missing (said once, by `research_issue`)."""
    return bool(_RESEARCH_MISSING.search(str(text or "")))


def research_issue(source: Optional[Dict[str, Any]], where: str = RESEARCH_MISSING_WHERE,
                   text: str = RESEARCH_MISSING_TEXT) -> Optional[tuple]:
    """The drawer's one line for the missing research data: (kind, where, the sentence with the paths
    tried on hover), or None when the research data is there."""
    if not research_missing(source):
        return None
    tried = str((source or {}).get("tried") or "")
    return ("Research", where, html.Span(text, title=f"Looked for the research database at: {tried}" if tried else None))


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
def triggered_values() -> List[Tuple[Any, Any]]:
    """[(id, value)] of the inputs that really changed in this callback: never the whole set a
    table's re-render inserts (Dash fires then with "." as the trigger), so a store is written only
    from the control the user touched, never from a stale copy of another."""
    trig = dash.ctx.triggered_prop_ids or {}
    wanted = [(str(prop).rsplit(".", 1)[-1], ident) for prop, ident in trig.items() if str(prop) != "."]
    out: List[Tuple[Any, Any]] = []
    for group in dash.ctx.inputs_list or []:
        for item in (group if isinstance(group, list) else [group]):
            if not isinstance(item, dict):
                continue
            if any(item.get("id") == ident and item.get("property") == prop for prop, ident in wanted):
                out.append((item.get("id"), item.get("value")))
    return out


def apply_part(state: Dict[str, Any], tab: str, part: str, value: Any) -> None:
    """One funnel control's value into the state (in place): a shared list, a tick list of the
    tab's own, or a comparison kept only when it reads (an unreadable one is dropped, so its box
    empties and says so)."""
    if part in SHARED_PARTS:
        state[part] = [str(x) for x in (value or [])]
        return
    key = f"{tab}:{part}"
    cols = dict(state.get("cols") or {})
    if isinstance(value, (list, tuple)):
        if value:
            cols[key] = [str(x) for x in value]
        else:
            cols.pop(key, None)
    elif parse_compare(value) is not None:
        cols[key] = str(value).strip()
    else:
        cols.pop(key, None)
    state["cols"] = cols


def register(app, main_tabs_id: str) -> None:
    """The strip's search and switch and every funnel's control -> the store (every tab's, by
    pattern, only the control touched); Clear filters -> the store and the search box; its
    visibility; the links that filter to one trade and open a tab."""
    @app.callback(Output(STORE_ID, "data"),
                  Input(_cid(SEARCH_TYPE, ALL), "value"), Input(_cid(GROUP_TYPE, ALL), "value"),
                  Input({"type": COL_TYPE, "tab": ALL, "part": ALL}, "value"),
                  State(STORE_ID, "data"), prevent_initial_call=True)
    def _sync(_searches, _groups, _cols, current):
        changed = triggered_values()
        if not changed:
            return dash.no_update
        new = normal(current)
        for ident, value in changed:
            kind = ident.get("type") if isinstance(ident, dict) else None
            if kind == SEARCH_TYPE:
                new["search"] = str(value or "")
            elif kind == GROUP_TYPE:
                new["group"] = value if value in GROUPS else GROUP_NONE
            elif kind == COL_TYPE:
                apply_part(new, str(ident.get("tab") or ""), str(ident.get("part") or ""), value)
        new = normal(new)
        return dash.no_update if new == normal(current) else new

    @app.callback(Output(STORE_ID, "data", allow_duplicate=True),
                  Output(_cid(SEARCH_TYPE, ALL), "value", allow_duplicate=True),
                  Input(_cid(CLEAR_TYPE, ALL), "n_clicks"), State(STORE_ID, "data"),
                  State(_cid(SEARCH_TYPE, ALL), "id"), prevent_initial_call=True)
    def _clear(clicks, current, search_ids):
        if not any(clicks or []):
            return dash.no_update, [dash.no_update] * len(search_ids or [])
        return normal({"group": normal(current)["group"]}), [""] * len(search_ids or [])

    @app.callback(Output(_cid(CLEAR_TYPE, ALL), "style"), Input(STORE_ID, "data"),
                  State(_cid(CLEAR_TYPE, ALL), "id"))
    def _clear_shown(state, ids):
        return [{} if is_filtered(state, (i or {}).get("tab")) else HIDDEN_STYLE for i in ids or []]

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
    """Render `tab`'s strip controls (search, switch, Clear filters) from the shared state when the
    tab mounts and on every as-of or data change; never on a filter change, so the search box keeps
    its focus. The funnels' choices are drawn with the table (`options_of` is no longer called)."""
    @app.callback(Output(bar_id(tab), "children"), Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"),
                  State(STORE_ID, "data"))
    def _bar(_as_of, _rev, state):
        return bar(tab, state)
