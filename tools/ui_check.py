#!/usr/bin/env python
"""Display-text checker for the Dash screens: the basic mistakes on the screens caught mechanically.

    py 2_launcher.py ui-check               every tab; exit 1 on any visible-text finding
    py 2_launcher.py ui-check --tab book    one tab (top, header, book, pnl, risk, blotter, data)
    py 2_launcher.py ui-check --json        also write the findings to reports/ui_check.json

It builds the synthetic sample book (`tests.golden_book.build_book`, as of `AS_OF`) in a
throw-away database under a temp dir, renders the top bar, the header and every visible tab
(`ui.app.VISIBLE_TABS`) the way their callbacks do, with the first trade's row opened on Book,
P&L and Risk and every Risk fold open, and walks the Dash component trees. No server, no
browser, no Bloomberg, no network; nothing is written outside the temp dir and `reports/`.

Every piece of visible text is gathered per block element (the inline children of a cell, a
paragraph or a heading are read as one run, as the browser draws them), with the text that is
drawn from a static block (`ui.tabs.formatting.static_block`, a `dcc.Markdown` of html) parsed
back out of its markup. Hover text (`title=`) is its own, lower-severity category: it is
reported but never fails the run.

Rules (visible text unless said):
  LOWERCASE            the first letter is lowercase (units, numbers, dates, text opening with a
                       digit, sign or symbol, and the short `WHITELIST` below are exempt); hover too
  BANNED               n/a, nan, None, null, undefined, inf, TODO, NaT, Python reprs; hover too
  ENGINE_WORD          POSITION- / OUTRIGHT- / TRADE- ids, UNRECOGNISED, snake_case, ALL_CAPS
                       codes (FX_FWD, FUTURE_PX, BBG_, QL_), " Comdty" / " Curncy"; visible only,
                       since hover carries instrument ids by design
  LOOSE_TEXT           text outside any table, card, panel, section, header, drawer, tab title
                       or chart (`CONTAINER_TYPES`, `CONTAINER_CLASS_PARTS`); a heuristic
  DOUBLE_SPACE         two spaces inside a text; hover too
  TRAILING_PUNCT_SPACE a space before punctuation ("word ."), or leading / trailing whitespace
                       in a placeholder or a tab label, where the browser draws it
  RENDER_ERROR         a screen that raised or logged an error while rendering
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import re
import sqlite3
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AS_OF = "2026-09-18"                     # the golden book's last pinned date (tests/golden_book.py)
REPORT_PATH = ROOT / "reports" / "ui_check.json"
TAB_ORDER = ("top", "header", "book", "pnl", "risk", "blotter", "data")
TAB_ALIASES = {"market-data": "data", "p&l": "pnl", "top-bar": "top"}

# ----------------------------------------------------------------------------- rule tables
# A lowercase first word that is a unit or a unit-like suffix: drawn beside a figure, never a label.
UNIT_WORDS = {"lots", "lot", "t", "oz", "bbl", "bu", "lb", "lbs", "mt", "k", "m", "bn", "bp", "bps", "x",
              "vs", "of", "per", "est", "c", "p"}
# The whitelist: lowercase first words the rulebook itself prescribes. Keep it small.
WHITELIST = {
    "excl",      # CLAUDE.md "Tabs as views": a subtotal carries the marker `excl. N`
    "filled",    # CLAUDE.md "Header": the fill's marker `filled N`
    "ref",       # CLAUDE.md "Header": a stepped-back reference close's marker `ref <date>`
    "z",         # the z-score column, CLAUDE.md "Screens redesign plan" Phase G's column list: a symbol
}
# Leading glyphs that decorate a text (row chevrons, bullets, ticks) and are skipped before the
# first-letter test, so "▸ cattle" is still read as "cattle".
DECORATION = "▸▾▴▹►◂◃•·✓✗✔✘⚠ⓘ  \t\n"

BANNED_PATTERNS = [
    ("n/a", re.compile(r"(?<![\w/])n/a(?![\w/])", re.IGNORECASE)),
    ("nan", re.compile(r"(?<![\w])(nan|NaN|NAN)(?![\w])")),
    ("NaT", re.compile(r"\bNaT\b")),
    ("None", re.compile(r"(^\s*None\s*$)|([:=(\[,]\s*None\b)|(\bNone\s*[)\],])")),
    ("null", re.compile(r"\bnull\b")),
    ("undefined", re.compile(r"\bundefined\b")),
    ("inf", re.compile(r"(?<![\w.])[-+−]?inf(?![\w])")),
    ("TODO", re.compile(r"\bTODO\b")),
    ("repr", re.compile(r"\[\]|\['|\{'|<NA>|Timestamp\(|dtype")),
]

ENGINE_PATTERNS = [
    ("position id", re.compile(r"\b(POSITION|OUTRIGHT|TRADE)-")),
    ("UNRECOGNISED", re.compile(r"\bUNRECOGNISED\b")),
    ("snake_case", re.compile(r"(?<![\w./\\])[a-z][a-z0-9]*_[a-z0-9_]+(?![\w]|\.(csv|xlsx|xls|yaml|yml|json|db|txt)\b)")),
    ("code", re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")),
    ("code", re.compile(r"\b(BBG|QL)_")),
    ("code", re.compile(r"\b(FUTURE_PX|FWD_OUTRIGHT|CMDTY_OPTION|EQ_OPTION|FX_OPTION|LME_FWD|FX_FWD|FX_SPOT|"
                        r"FX_SWAP|NOTIONAL|INTERP:)")),
    ("Bloomberg suffix", re.compile(r" (Comdty|Curncy)\b")),
]

# ENGINE_WORD does not apply to these DataTable columns, which show Bloomberg's own names by design:
# the Data tab's Bloomberg library lists "ticker, field, what it is for" (CLAUDE.md "Bloomberg library").
ENGINE_EXEMPT_COLUMNS = {
    ("market-data-library-table", "ticker"),
    ("market-data-library-table", "field"),
}

# Loose text: a visible run is "held" when any ancestor is one of these component types or carries a
# class containing one of these parts (ui/tabs/formatting.py, ui/tabs/data_kit.py, ui/assets/style.css).
CONTAINER_TYPES = {"Table", "Thead", "Tbody", "Tfoot", "Tr", "Td", "Th", "DataTable", "Details", "Summary",
                   "Graph", "Tab", "Tabs"}
CONTAINER_CLASS_PARTS = (
    "card",            # book-card, tk-card, upload-card, card-pad, cards
    "panel",           # status-panel, curve-panel, fx-ccy-panel, tk-panel-body
    "section",         # section, book-section-*, risk-section-head
    "fold",            # risk-fold, book-fold, tk-fold-block
    "drawer",          # issues-drawer
    "details",         # details, details--diag
    "header",          # header-block, header-figure
    "top-bar",         # the top bar
    "source-",         # the top bar's upload strip
    "strip",           # tk-strip, book-strip
    "headline",        # tk-headline, risk-headline
    "chart", "table", "grid", "tile", "heatmap",
    "issues", "tk-foot", "book-foot", "book-empty", "risk-block", "data-block", "bbg-check",
    "tf-bar", "blotter-filter", "subtab", "static-block--held",
)
CONTROL_TYPES = {"Button", "A", "Label", "Option", "Upload", "Dropdown", "RadioItems", "Checklist", "Input",
                 "DatePickerSingle", "DatePickerRange", "Tab", "Tabs", "Select", "Textarea"}
INLINE_TYPES = {"Span", "A", "B", "Strong", "Em", "I", "Small", "Abbr", "Code", "Sup", "Sub", "Mark", "U", "S",
                "Font", "Time", "Kbd", "Q", "Cite", "Var", "Samp", "Data"}
HEADINGS = {"H1", "H2", "H3", "H4", "H5", "H6"}
HIDDEN_STYLE = re.compile(r"display\s*:\s*none")


# ----------------------------------------------------------------------------- findings
class Finding:
    __slots__ = ("tab", "rule", "text", "path", "hover", "detail")

    def __init__(self, tab: str, rule: str, text: str, path: str, hover: bool, detail: str = ""):
        self.tab, self.rule, self.text, self.path, self.hover, self.detail = tab, rule, text, path, hover, detail

    def key(self) -> tuple:
        return (self.tab, self.rule, self.hover, self.text, self.path)


# ----------------------------------------------------------------------------- a node view
class Node:
    """One element of a tree to check: a Dash component or an element parsed from a static
    block's markup, seen through the same few fields."""
    __slots__ = ("type", "id", "classes", "props", "children", "source")

    def __init__(self, type_: str, id_: Any, classes: List[str], props: dict, children: list, source: str):
        self.type, self.id, self.classes, self.props, self.children, self.source = (
            type_, id_, classes, props, children, source)

    def label(self) -> str:
        if self.id not in (None, ""):
            ident = self.id if isinstance(self.id, str) else json.dumps(self.id, sort_keys=True)
            return f"{self.type}#{ident}"
        if self.classes:
            return f"{self.type}.{self.classes[0]}"
        return self.type


class _Markup(HTMLParser):
    """A static block's markup as a small tree of `Node`s (html void tags have no children)."""
    VOID = {"br", "hr", "img", "input", "col", "meta", "link", "wbr", "area", "base", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("Div", None, [], {}, [], "markup")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        classes = (a.get("class") or a.get("classname") or "").split()
        props = {}
        if a.get("title"):
            props["title"] = a["title"]
        if "data-hidden" in a or "hidden" in a:
            props["hidden"] = True
        node = Node(tag.capitalize(), a.get("id"), classes, props, [], "markup")
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID and self.stack[-1].type == tag.capitalize():
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].type == tag.capitalize():
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


_JSX_STYLE = re.compile(r"\sstyle=\{(\{[^{}]*\})\}")
_JSX_ATTR = re.compile(r"\s[\w-]+=\{[^{}]*\}")


def parse_markup(source: str) -> Node:
    """The markup `formatting.static_block` writes (html with JSX-style `style={...}` and `{false}`
    attributes) as a `Node` tree; an element styled `display: none` is marked hidden."""
    src = _JSX_STYLE.sub(lambda m: ' data-hidden="1"' if HIDDEN_STYLE.search(m.group(1).replace('"', ""))
                         else "", str(source))
    src = _JSX_ATTR.sub("", src)
    parser = _Markup()
    parser.feed(src)
    parser.close()
    return parser.root


def _plain_markdown(source: str) -> Node:
    """A plain `dcc.Markdown` (no html): one paragraph per line, the markup characters dropped."""
    kids = []
    for line in str(source).splitlines():
        line = re.sub(r"^\s*(#+|[-*>]|\d+\.)\s+", "", line)
        line = re.sub(r"[*_`]{1,3}", "", line).strip()
        if line:
            kids.append(Node("P", None, [], {}, [line], "markdown"))
    return Node("Div", None, [], {}, kids, "markdown")


def _figure_texts(fig: Any) -> List[str]:
    """The words a chart draws: its title, axis titles, annotations and legend names."""
    try:
        fig = fig.to_plotly_json() if hasattr(fig, "to_plotly_json") else dict(fig or {})
    except (TypeError, ValueError):
        return []
    out: List[str] = []

    def title_of(obj) -> Optional[str]:
        t = (obj or {}).get("title") if isinstance(obj, dict) else None
        if isinstance(t, dict):
            t = t.get("text")
        return t if isinstance(t, str) else None

    layout = fig.get("layout") or {}
    if isinstance(layout, dict):
        for key, value in layout.items():
            if key == "title" or (isinstance(key, str) and key.startswith(("xaxis", "yaxis"))):
                t = title_of({"title": value}) if key == "title" else title_of(value)
                if t:
                    out.append(t)
        for ann in layout.get("annotations") or []:
            if isinstance(ann, dict) and isinstance(ann.get("text"), str):
                out.append(ann["text"])
    legend = not (isinstance(layout, dict) and layout.get("showlegend") is False)
    for trace in fig.get("data") or []:
        if legend and isinstance(trace, dict) and isinstance(trace.get("name"), str) and trace.get("showlegend", True):
            out.append(trace["name"])
    return [re.sub(r"<[^>]+>", " ", t) for t in out]


def as_node(obj: Any) -> Optional[Node]:
    """A Dash component as a `Node` (None for anything else)."""
    from dash.development.base_component import Component
    if not isinstance(obj, Component):
        return None
    type_ = type(obj).__name__
    props = {}
    for name in getattr(obj, "_prop_names", ()):
        if name == "children":
            continue
        value = getattr(obj, name, None)
        if value is not None:
            props[name] = value
    classes = str(props.get("className") or "").split()
    children = getattr(obj, "children", None)
    kids: list
    if type_ == "Markdown":
        source = children if isinstance(children, str) else "".join(c for c in (children or []) if isinstance(c, str))
        kids = [parse_markup(source) if props.get("dangerously_allow_html") else _plain_markdown(source)]
    elif children is None:
        kids = []
    elif isinstance(children, (list, tuple)):
        kids = list(children)
    else:
        kids = [children]
    return Node(type_, props.get("id"), classes, props, kids, "dash")


def _is_hidden(node: Node) -> bool:
    if node.props.get("hidden") is True:
        return True
    style = node.props.get("style")
    if isinstance(style, dict):
        return str(style.get("display", "")).replace(" ", "") == "none"
    return False


def _flatten(children: Iterable) -> Iterable:
    for c in children:
        if isinstance(c, (list, tuple)):
            yield from _flatten(c)
        else:
            yield c


# ----------------------------------------------------------------------------- the walk
class Run:
    """One piece of text as drawn: its words, the chain of elements above it, what kind it is."""
    __slots__ = ("text", "chain", "kind")

    def __init__(self, text: str, chain: List[Node], kind: str):
        self.text, self.chain, self.kind = text, chain, kind   # kind: visible | hover | placeholder | label


def _label_texts(value: Any) -> List[Any]:
    """The labels of a control's options (strings, or components drawn as they are)."""
    out: List[Any] = []
    if isinstance(value, dict):
        return [str(v) for v in value.values()]
    for o in value or []:
        if isinstance(o, dict):
            out.append(o.get("label"))
        elif isinstance(o, (str, int, float)):
            out.append(str(o))
    return out


def walk(root: Any) -> List[Run]:
    """Every run of text in the tree under `root` (a component, a list of them, or strings)."""
    runs: List[Run] = []

    def emit(text: str, chain: List[Node], kind: str) -> None:
        if text and text.strip():
            runs.append(Run(text, list(chain), kind))

    def props_of(node: Node, chain: List[Node]) -> None:
        title = node.props.get("title")
        if isinstance(title, str):
            emit(title, chain, "hover")
        placeholder = node.props.get("placeholder")
        if isinstance(placeholder, str):
            emit(placeholder, chain, "placeholder")
        if node.type == "Tab" and isinstance(node.props.get("label"), str):
            emit(node.props["label"], chain, "label")
        if node.type in ("Dropdown", "RadioItems", "Checklist") and "options" in node.props:
            for lab in _label_texts(node.props.get("options")):
                if isinstance(lab, str):
                    emit(lab, chain, "control")
                elif lab is not None:
                    block(lab, chain)
        if node.type == "DataTable":
            table_texts(node, chain)
        if node.type == "Graph":
            for t in _figure_texts(node.props.get("figure")):
                emit(t, chain, "visible")

    def table_texts(node: Node, chain: List[Node]) -> None:
        for col in node.props.get("columns") or []:
            name = col.get("name") if isinstance(col, dict) else None
            for n in (name if isinstance(name, (list, tuple)) else [name]):
                if isinstance(n, str):
                    emit(n, chain + [Node("Th", None, [], {}, [], "dash")], "visible")
        hidden = set(node.props.get("hidden_columns") or [])
        cells: Dict[str, Node] = {}

        def cell(column: str) -> Node:
            if column not in cells:
                cells[column] = Node("Td", None, [], {"column": column}, [], "dash")
            return cells[column]
        for row in node.props.get("data") or []:
            if isinstance(row, dict):
                for k, v in row.items():
                    if k not in hidden and isinstance(v, str):
                        emit(v, chain + [cell(str(k))], "visible")
        for row in node.props.get("tooltip_data") or []:
            for k, v in (row or {}).items():
                v = v.get("value") if isinstance(v, dict) else v
                if isinstance(v, str):
                    emit(v, chain + [cell(str(k))], "hover")

    def block(obj: Any, chain: List[Node]) -> None:
        """`obj` as a block: its inline children drawn as one run, each block child its own."""
        node = obj if isinstance(obj, Node) else as_node(obj)
        if node is None:
            if isinstance(obj, (str, int, float)) and not isinstance(obj, bool):
                emit(str(obj), chain, "visible")
            elif isinstance(obj, (list, tuple)):
                for c in _flatten(obj):
                    block(c, chain)
            return
        if _is_hidden(node):
            return
        here = chain + [node]
        props_of(node, chain + [node])
        buf: List[str] = []

        def flush():
            if buf:
                emit("".join(buf), here, "visible")
                buf.clear()

        def inline(child: Any) -> None:
            if child is None or isinstance(child, bool):
                return
            if isinstance(child, (str, int, float)):
                buf.append(str(child))
                return
            if isinstance(child, (list, tuple)):
                for c in _flatten(child):
                    inline(c)
                return
            n = child if isinstance(child, Node) else as_node(child)
            if n is None:
                return
            if _is_hidden(n):
                return
            if n.type == "Br":
                flush()
                return
            if n.type in INLINE_TYPES and n.type not in ("A",) and n.source != "markdown":
                props_of(n, here + [n])
                for c in _flatten(n.children):
                    inline(c)
                return
            flush()
            block(n, here)

        for child in _flatten(node.children):
            inline(child)
        flush()

    block(root, [])
    return runs


# ----------------------------------------------------------------------------- the rules
def path_of(chain: List[Node]) -> str:
    """The run's own element and the nearest element above it with an id or a class."""
    if not chain:
        return "(root)"
    own = chain[-1]
    column = own.props.get("column")
    own_type = f"{own.type}[{column}]" if column else own.type
    for node in reversed(chain):
        if node.id not in (None, "") or node.classes:
            anchor = node.label()
            return anchor if node is own else f"{own_type} in {anchor}"
    return own_type


def _first_word(text: str) -> Tuple[str, str]:
    """(the text from its first real character, its first word lowercased without a dot)."""
    s = text.lstrip(DECORATION)
    m = re.match(r"([A-Za-zÀ-ɏ]+)", s)
    return s, (m.group(1).lower() if m else "")


def lowercase_hit(text: str) -> bool:
    s, word = _first_word(text.splitlines()[0] if text.strip() else text)
    if not s or not s[0].isalpha():
        return False                     # a digit, a sign, a currency symbol, a glyph
    if not s[0].islower():
        return False
    if word in UNIT_WORDS or word in WHITELIST:
        return False
    return True


def _banned(text: str, control: bool = False) -> List[str]:
    """The banned tokens in `text`; "None" is a fair option label on a control (Group: None)."""
    return [name for name, rx in BANNED_PATTERNS if rx.search(text) and not (control and name == "None")]


def _engine_exempt(chain: List[Node]) -> bool:
    column = chain[-1].props.get("column") if chain else None
    return any((n.id, column) in ENGINE_EXEMPT_COLUMNS for n in chain if isinstance(n.id, str))


def _engine(text: str) -> List[str]:
    hits = []
    for name, rx in ENGINE_PATTERNS:
        m = rx.search(text)
        if m and name not in [h.split(":")[0] for h in hits]:
            hits.append(f"{name}: {m.group(0).strip()}")
    return hits


def _held(chain: List[Node]) -> bool:
    if chain and chain[-1].type in HEADINGS and "about-title" in chain[-1].classes:
        return True                      # a tab's or a section's title (formatting.about)
    for node in chain:
        if node.type in CONTAINER_TYPES:
            return True
        if node.id == "header-block" or any(part in c for c in node.classes for part in CONTAINER_CLASS_PARTS):
            return True
    return False


def _in_control(chain: List[Node]) -> bool:
    return any(n.type in CONTROL_TYPES for n in chain)


# a space before punctuation; a ratio's colon ("1 : 1.84", CLAUDE.md "Book") is not one
_PUNCT_SPACE = re.compile(r"[^\s.]\s+([.,;!?]|:(?!\s*[\d−+-]))(\s|$)")


def check_runs(tab: str, runs: List[Run]) -> List[Finding]:
    out: List[Finding] = []
    for run in runs:
        text = run.text
        shown = re.sub(r"\s+", " ", text).strip()
        hover = run.kind == "hover"
        path = path_of(run.chain)

        def add(rule: str, detail: str = "") -> None:
            out.append(Finding(tab, rule, shown[:120], path, hover, detail))

        banned = _banned(text, control=run.kind == "control")
        if lowercase_hit(text) and not (banned and _first_word(text)[1] in {"n", "nan", "none", "null", "undefined",
                                                                             "inf"}):
            add("LOWERCASE")
        for name in banned:
            add("BANNED", name)
        if not hover and not _engine_exempt(run.chain):
            for hit in _engine(text):
                add("ENGINE_WORD", hit)
        if "  " in text.strip(" \n\t") and not hover:
            add("DOUBLE_SPACE")
        elif hover and "  " in text.replace("\n", " ").strip():
            add("DOUBLE_SPACE")
        if not hover and _PUNCT_SPACE.search(shown):
            add("TRAILING_PUNCT_SPACE", "space before punctuation")
        if run.kind in ("placeholder", "label") and text != text.strip():
            add("TRAILING_PUNCT_SPACE", "leading or trailing whitespace")
        if (run.kind == "visible" and len(shown) > 2 and not _in_control(run.chain)
                and not _held(run.chain)):
            add("LOOSE_TEXT")
    return out


# ----------------------------------------------------------------------------- rendering
def _fill(tree: Any, slots: Dict[str, Any], styles: Optional[Dict[str, Any]] = None) -> Any:
    """`tree` with each component whose id is in `slots` given those children (and `styles`)."""
    from dash.development.base_component import Component
    styles = styles or {}

    def visit(obj: Any) -> None:
        if isinstance(obj, (list, tuple)):
            for c in obj:
                visit(c)
            return
        if not isinstance(obj, Component):
            return
        ident = getattr(obj, "id", None)
        if isinstance(ident, str):
            if ident in slots:
                obj.children = slots[ident]
            if ident in styles:
                obj.style = styles[ident]
        visit(getattr(obj, "children", None))
    visit(tree)
    return tree


def _raw_callback(app, output: str) -> Callable:
    key = next(k for k in app.callback_map if output in k.strip(".").split("...") and "@" not in k)
    wrapped = app.callback_map[key]["callback"]
    return getattr(wrapped, "__wrapped__", wrapped)


def build_sample(folder: Path) -> Path:
    """The synthetic sample book at `AS_OF`, written to `folder/ui_check.db` (the golden fixture,
    the ledger's freeze of what has settled, the Bloomberg library brought up to date)."""
    from data.ingest import schema
    from engine.pnl import ledger
    from tests.golden_book import build_book

    path = folder / "ui_check.db"
    conn = schema.connect(str(path))
    try:
        build_book(conn)
        ledger.realise_settled(conn, AS_OF)
        try:
            from data.bloomberg import library
            if library.is_out_of_date(conn):
                library.sync(conn)
        except Exception:  # noqa: BLE001 -- the readers work it out themselves, as in the app
            pass
        conn.commit()
    finally:
        conn.close()
    return path


def _ro(path: Path) -> sqlite3.Connection:
    from ui.app import connect_readonly
    return connect_readonly(path)


def render_top(app, db: Path) -> Any:
    """The top bar as a page load builds it, with the Bloomberg state line its refresh draws."""
    from ui import app as uiapp
    from ui import feed_controls
    page = uiapp.build_layout(uiapp.load_summary(db), db_path=db, build="ui-check")
    top = next(c for c in page.children if "top-bar" in str(getattr(c, "className", "") or ""))
    view, hover = _raw_callback(app, f"{feed_controls.PULL_STATUS_ID}.children")(0, None, None)
    _fill(top, {feed_controls.PULL_STATUS_ID: view})
    return _with_title(top, feed_controls.PULL_STATUS_ID, hover) if isinstance(hover, str) else top


def _with_title(tree: Any, ident: str, title: str) -> Any:
    from dash.development.base_component import Component

    def visit(obj):
        if isinstance(obj, (list, tuple)):
            for c in obj:
                visit(c)
        elif isinstance(obj, Component):
            if getattr(obj, "id", None) == ident:
                obj.title = title
            visit(getattr(obj, "children", None))
    visit(tree)
    return tree


def render_header(app, db: Path) -> Any:
    from ui.tabs import header
    conn = _ro(db)
    try:
        figures = header._build_figures(conn, AS_OF)
    finally:
        conn.close()
    return _fill(header.layout(), {f"{header.HEADER_ID}-figures": figures})


def _first_trade(db: Path) -> Optional[str]:
    from ui.tabs import book
    conn = _ro(db)
    try:
        trades = book.gather(conn, AS_OF).get("trades") or []
    finally:
        conn.close()
    named = [t for t in trades if not t.get("pseudo")] or trades
    return str(named[0].get("trade")) if named else None


def _trade_tab(module, db: Path, opened: List[str], **extra) -> Any:
    from ui.tabs import trade_filter as tf
    p = module.render_parts(AS_OF, db, None, opened=opened, **extra)
    shell = module.layout(AS_OF)
    if not p["shown"]:
        return _fill(shell, {module.BODY_ID: p["body"]})
    slots = {module.BODY_ID: None, module.HEADLINE_ID: p["headline"], module.TABLE_SLOT_ID: p["table"],
             module.FOOT_ID: p["foot"], tf.bar_id(module.TAB): tf.bar(module.TAB, None, module.options_of(AS_OF, db))}
    for key, ident in (("chart", "CHART_ID"), ("track", None)):
        if key in p and ident:
            slots[getattr(module, ident)] = p[key]
    if "track" in p:
        slots[f"{module.TRACK_ID}-slot"] = p["track"]
    return _fill(shell, slots, {module.CONTENT_ID: {}})


def render_book(app, db: Path) -> Any:
    from ui.tabs import book
    first = _first_trade(db)
    return _trade_tab(book, db, [first] if first else [], wait_risk=True)


def render_pnl(app, db: Path) -> Any:
    from ui.tabs import pnl
    first = _first_trade(db)
    return _trade_tab(pnl, db, [f"t:{first}"] if first else [])


def render_risk(app, db: Path) -> Any:
    from ui.tabs import risk
    from ui.tabs import risk_folds as rf
    first = _first_trade(db)
    tree = _trade_tab(risk, db, [first] if first else [], wait_risk=True)
    folds = risk.render_folds(AS_OF, db, None, [rf.FOLD_NET, rf.FOLD_CCY, rf.FOLD_GREEKS, rf.FOLD_STRESS,
                                                rf.FOLD_PRICE, rf.FOLD_STRESS_MORE], None, rf.DEFAULT_UNIT)
    return _fill(tree, {risk.FOLDS_ID: folds})


def render_blotter(app, db: Path) -> Any:
    from ui.tabs import blotter
    from ui.tabs import blotter_fills as fills
    update = _raw_callback(app, f"{blotter.CONTENT_ID}.children")
    conn = _ro(db)
    try:
        notices = blotter.blotter_notices(conn)
        df, _issues, _with = fills.fills_frame(conn, AS_OF)
    finally:
        conn.close()
    days = sorted(d for d in df["trade_date"].tolist() if d) if not df.empty else []
    fills_slots = {fills.BAR_SLOT_ID: fills.bar(None, fills.filter_options(df), (days[0], days[-1]) if days else
                                                (None, None)),
                   fills.FILLS_BODY_ID: fills.fills_table(df, len(df), None, None, AS_OF) if not df.empty else None,
                   fills.META_ID: f"{len(df):,} fills"}
    trees = []
    for scope in blotter.SCOPE_ORDER:
        content, _sig, subtabs, section, history, drawer = update(AS_OF, scope)
        if scope != blotter.SCOPE_ORDER[0] and subtabs == blotter.SUBTABS_HIDDEN:
            continue                                   # no open option: the Options sub-tab never shows
        slots = {blotter.CONTENT_ID: content, blotter.NOTICES_ID: notices}
        if scope == blotter.SCOPE_ORDER[0]:
            slots.update(fills_slots)
            slots[blotter.HISTORY_SLOT_ID] = history
            slots[blotter.BLOTTER_ISSUES_SLOT_ID] = drawer
        trees.append(_fill(blotter.build_layout(AS_OF), slots,
                           {blotter.SUBTABS_ID: subtabs, fills.SECTION_ID: section}))
    return trees


def render_data(app, db: Path) -> Any:
    from ui.tabs import data_checks, market_data as md
    out = md.render(AS_OF, db)
    problems, marks = out[1] or [], out[3] or []
    slots = {md.BODY_ID: out[0], md.MARKS_EMPTY_ID: out[5], md.PAST_CLOSES_PANEL_ID: out[7],
             md.CONTRACT_DATES_PANEL_ID: out[8], md.DIAG_BODY_ID: out[9], md.ISSUES_ID: out[10]}
    if problems:
        slots[md.PROBLEMS_SLOT_ID] = data_checks.problems_table(problems, None, md.PROBLEM_SORT_TYPE)
    if marks:
        slots[md.MARKS_TABLE_WRAP_ID] = data_checks.marks_table(marks, None, md.MARK_SORT_TYPE, len(marks))
    tree = _fill(md.build_layout(AS_OF), slots, {md.MISSING_PANEL_ID: out[2], md.MARKS_TOOLS_ID: out[6]})
    # the Commodity / sector choices the body fills in
    for node in _components(tree):
        if getattr(node, "id", None) == md.MARKS_FILTER_ID:
            node.options = out[4]
    return tree


def _components(tree: Any) -> Iterable:
    from dash.development.base_component import Component
    if isinstance(tree, (list, tuple)):
        for c in tree:
            yield from _components(c)
    elif isinstance(tree, Component):
        yield tree
        yield from _components(getattr(tree, "children", None))


RENDERERS = {"top": render_top, "header": render_header, "book": render_book, "pnl": render_pnl,
             "risk": render_risk, "blotter": render_blotter, "data": render_data}


class _Errors(logging.Handler):
    """ERROR records logged while a screen renders (a block that showed its reason instead)."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: List[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def run_checks(tabs: List[str]) -> Tuple[List[Finding], Dict[str, float]]:
    import warnings
    warnings.filterwarnings("ignore")
    from ui import app as uiapp

    findings: List[Finding] = []
    timings: Dict[str, float] = {}
    errors = _Errors()
    root_logger = logging.getLogger()
    root_logger.addHandler(errors)
    previous = root_logger.level
    root_logger.setLevel(logging.ERROR)
    for name in ("werkzeug", "dash"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        with tempfile.TemporaryDirectory(prefix="ui_check_", ignore_cleanup_errors=True) as tmp:
            t0 = time.perf_counter()
            db = build_sample(Path(tmp))
            app = uiapp.create_app(db_path=db, start_feed=False, build_fingerprint="ui-check")
            timings["sample"] = time.perf_counter() - t0
            for tab in tabs:
                t0 = time.perf_counter()
                errors.records.clear()
                try:
                    tree = RENDERERS[tab](app, db)
                    findings.extend(check_runs(tab, walk(tree)))
                except Exception as exc:  # noqa: BLE001 -- a screen that raises is itself the finding
                    findings.append(Finding(tab, "RENDER_ERROR", f"{type(exc).__name__}: {exc}"[:200],
                                            "(render)", False))
                for rec in errors.records:
                    msg = rec.getMessage()
                    if rec.exc_info and rec.exc_info[1] is not None:
                        msg += f" ({type(rec.exc_info[1]).__name__}: {rec.exc_info[1]})"
                    findings.append(Finding(tab, "RENDER_ERROR", msg[:200], f"(log {rec.name})", False))
                timings[tab] = time.perf_counter() - t0
            uiapp.set_active_db(None)
    finally:
        root_logger.removeHandler(errors)
        root_logger.setLevel(previous)
    return findings, timings


# ----------------------------------------------------------------------------- output
def grouped(findings: List[Finding]) -> List[Tuple[Finding, int]]:
    counts: Dict[tuple, int] = collections.Counter(f.key() for f in findings)
    first: Dict[tuple, Finding] = {}
    for f in findings:
        first.setdefault(f.key(), f)
    rule_order = ["RENDER_ERROR", "BANNED", "ENGINE_WORD", "LOWERCASE", "LOOSE_TEXT", "DOUBLE_SPACE",
                  "TRAILING_PUNCT_SPACE"]

    def order(item):
        f = item[0]
        return (TAB_ORDER.index(f.tab) if f.tab in TAB_ORDER else 99, f.hover,
                rule_order.index(f.rule) if f.rule in rule_order else 99, f.path, f.text)
    return sorted(((first[k], n) for k, n in counts.items()), key=order)


def rule_name(f: Finding) -> str:
    return f"hover:{f.rule}" if f.hover else f.rule


def print_report(findings: List[Finding], timings: Dict[str, float], out=sys.stdout) -> None:
    items = grouped(findings)
    tab = None
    for f, n in items:
        if f.tab != tab:
            tab = f.tab
            out.write(f"\n== {tab} ==\n")
        count = f"  x{n}" if n > 1 else ""
        detail = f"  [{f.detail}]" if f.detail else ""
        out.write(f"{rule_name(f):<24}\"{f.text}\"  at {f.path}{detail}{count}\n")
    out.write("\nSummary (distinct findings; occurrences in brackets)\n")
    per: Dict[str, Dict[str, List[int]]] = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for f, n in items:
        cell = per[f.tab][rule_name(f)]
        cell[0] += 1
        cell[1] += n
    for t in [t for t in TAB_ORDER if t in per]:
        parts = ", ".join(f"{r} {v[0]} ({v[1]})" for r, v in sorted(per[t].items()))
        out.write(f"  {t:<8} {parts}\n")
    visible = sum(1 for f, _n in items if not f.hover)
    hover = sum(1 for f, _n in items if f.hover)
    took = ", ".join(f"{k} {v:.1f}s" for k, v in timings.items())
    out.write(f"  {visible} visible, {hover} hover-only distinct findings ({took})\n")


def write_json(findings: List[Finding], timings: Dict[str, float], path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"tab": f.tab, "rule": f.rule, "hover": f.hover, "text": f.text, "path": f.path, "detail": f.detail,
             "count": n} for f, n in grouped(findings)]
    path.write_text(json.dumps({"as_of": AS_OF, "timings": timings, "findings": rows}, indent=1, ensure_ascii=False),
                    encoding="utf-8", newline="\n")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="py 2_launcher.py ui-check", description=__doc__.split("\n")[0])
    parser.add_argument("--tab", help="one screen: " + ", ".join(TAB_ORDER))
    parser.add_argument("--json", action="store_true", help=f"also write the findings to {REPORT_PATH.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    tabs = list(TAB_ORDER)
    if args.tab:
        key = TAB_ALIASES.get(args.tab.lower(), args.tab.lower())
        if key not in TAB_ORDER:
            parser.error(f"unknown tab {args.tab!r}: one of {', '.join(TAB_ORDER)}")
        tabs = [key]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    findings, timings = run_checks(tabs)
    print_report(findings, timings)
    if args.json:
        print(f"written {write_json(findings, timings)}")
    return 1 if any(not f.hover for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
