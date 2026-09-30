#!/usr/bin/env python
"""Display-text checker for the Dash screens: the basic mistakes on the screens caught mechanically.

    py 2_launcher.py ui-check               every tab; exit 1 on any visible finding, a hover LOWERCASE / BANNED
                                            one or a LOOSE_BLOCK (text outside a table, title or control)
    py 2_launcher.py ui-check --lenient     the same, LOOSE_BLOCK report-only (the old default)
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
back out of its markup. Hover text (`title=`) is reported apart from the visible text; its
LOWERCASE and BANNED findings fail the run too (`FAILING_HOVER_RULES`), its other findings are
report-only, and ENGINE_WORD is not applied to it (instrument ids on hover are by design).

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
  DUPLICATE_FILTER     a filter control outside a table (Dropdown, Checklist, RadioItems, a date
                       picker, Input) whose label, or a Dropdown's placeholder, repeats a column
                       heading of a table in the same card or of the next table on the tab (case
                       and a trailing "only" ignored; "Trade date" matches "Date"): filters live in
                       the column headings (the user, five rounds running). Allowed above a table:
                       the free-text search, the Group-by / Slice and Period / Table switches,
                       buttons. Fails the default run; Book, P&L, Risk, Blotter and Data
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
# The markers read "Excl. 3", "Filled 2", "Ref 16 Sep" and sizes "Long 15 lots": none is exempt.
WHITELIST = {
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
# A lowercase part after " · " or ": " inside a cell: report-only, since most are the prose after a colon
# ("No strike on file: type it in ...") rather than a label.
PART_RULE = "LOWERCASE_PART"
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
    __slots__ = ("text", "chain", "kind", "inline_classes")

    def __init__(self, text: str, chain: List[Node], kind: str, inline_classes: Iterable[str] = ()):
        self.text, self.chain, self.kind = text, chain, kind   # kind: visible | hover | placeholder | label
        self.inline_classes = sorted(set(inline_classes))    # the classes of the spans the words came from


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

    def emit(text: str, chain: List[Node], kind: str, inline_classes: Iterable[str] = ()) -> None:
        if text and text.strip():
            runs.append(Run(text, list(chain), kind, inline_classes))

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
        buf_classes: set = set()

        def flush():
            if buf:
                emit("".join(buf), here, "visible", buf_classes)
                buf.clear()
            buf_classes.clear()

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
                buf_classes.update(n.classes)
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
    """(the text from its first letter or digit, its first word lowercased without a dot): leading
    glyphs, bullets, arrows, signs, currency symbols and spaces are skipped ("● not recognised")."""
    s = re.sub(r"^[\W_]+", "", text.lstrip(DECORATION))
    m = re.match(r"([A-Za-zÀ-ɏ]+)", s)
    return s, (m.group(1).lower() if m else "")


def lowercase_hit(text: str) -> bool:
    s, word = _first_word(text.splitlines()[0] if text.strip() else text)
    if not s or not s[0].isalpha():
        return False                     # nothing but glyphs, or a number
    if not s[0].islower():
        return False
    if word in UNIT_WORDS or word in WHITELIST:
        return False
    return True


_PART_SPLIT = re.compile(r" · |: ")


def lowercase_parts(text: str) -> List[str]:
    """The parts of a cell after " · " or ": " that start with a lowercase label (units exempt)."""
    return [part.strip() for part in _PART_SPLIT.split(text.splitlines()[0] if text.strip() else text)[1:]
            if lowercase_hit(part)]


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
    titles: Dict[int, str] = {}
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
        if run.kind == "visible" and any(n.type in ("Td", "Th") for n in run.chain):
            for part in lowercase_parts(text):
                add(PART_RULE, f"after the separator: {part[:40]}")
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
        if tab in LAYOUT_TABS and run.kind == "visible" and re.search(r"[A-Za-z0-9]", shown):
            # the spans the words came from count too: a line holding a title span is a title line
            lchain = run.chain + ([Node("Span", None, run.inline_classes, {}, [], "inline")]
                                  if run.inline_classes else [])
            role = designed_role(lchain)
            if role:
                out.append(Finding(tab, "DESIGNED_LINE", shown[:100], path, False, role))
            elif not _placed(lchain):
                out.append(Finding(tab, "LOOSE_BLOCK", shown[:100], path, False,
                                   f"under: {section_title(run.chain, titles)}"))
    return out


# ----------------------------------------------------------------------------- the strict layout
# LOOSE_BLOCK (the user: "things just wandering about not in tables"): inside a tab body, text is in
# place only in a table cell, a section's title, the one headline line above a table, a control, a
# drawer's title line, a chart or a tab's empty state. A paragraph, a meta line under a title, a
# list, a tile, a status sentence in a Div or a note under a table is flagged.
LAYOUT_TABS = {"book", "pnl", "risk", "blotter", "data"}
TITLE_CLASSES = {"about-title", "tk-title", "book-section-title", "section-title", "risk-subhead", "curve-subhead",
                 "fx-ccy-title", "book-empty-title", "upload-card-title", "card-head",
                 "book-h"}                          # book.panel_chart: the chart's own title
HEADLINE_CLASSES = {"tk-headline"}                  # trade_filter.headline: "3 of 6 trades · ..."
# DESIGNED_LINE: the one-line blocks the Phase G design places outside a table on purpose, told by their
# class (never by their words). Reported apart, for the user to judge; never a failure.
DESIGNED_CLASSES = {
    "tk-headline": "headline above the table",                  # trade_filter.headline (Book, P&L, Risk)
    "risk-reach": "Risk history-reach line",                    # risk.py, under the headline
    "book-prepull": "Book pre-pull line",                       # book.PREPULL_TEXT
    "tk-flag-lines": "Book panel flags line",                   # book.panel_tr
    "tk-hedge-line": "Book panel hedge line",                   # book: the hedge line under the legs
    "blotter-upload-line": "Blotter last upload line",          # blotter_fills.last_upload_block
    "data-status-line": "Data status line",                     # market_data.BODY_ID
    "risk-fold-head": "fold title",                             # risk_folds: "Net by commodity", ...
    "risk-fold-title": "fold title",
    "tk-fold": "fold title",                                    # book: "Closed this year (N)" row
    "risk-more": "fold title",                                  # risk_folds: the Stress fold's "N more"
}
FOLD_BLOCK_CLASS = "tk-fold-block"                  # a Details fold (P&L "Track record"): its Summary is a fold title
PANEL_CELL_CLASS = "tk-panel-cell"                  # book.panel_tr: one Td spanning the table, a panel, not a cell
CONTROL_STRIP_CLASS = "tk-strip--controls"          # a strip of switches: its words are the switches' labels
EMPTY_STATE_PREFIX = "book-empty"                   # book.empty_state, shared by every trade tab
PLACED_TYPES = {"Td", "Th", "DataTable", "Graph", "Summary", "Button", "Dropdown", "Input", "RadioItems",
                "Checklist", "Label", "Tabs", "Tab", "A", "DatePickerSingle", "DatePickerRange", "Upload", "Select",
                "Textarea", "Option"} | HEADINGS
SECTION_TOKENS = {"card", "book-card", "tk-card", "risk-fold", "data-block", "section", "status-panel", "upload-card",
                  "curve-panel", "fx-ccy-panel", "tk-foot", "details", "issues-drawer"}
LAYOUT_SKIP_TYPES = {"Store", "Interval", "Download", "Location"}


def designed_role(chain: List[Node]) -> str:
    """The designed one-line block the run belongs to ('' when none), by class."""
    for i, node in enumerate(chain):
        for c in node.classes:
            if c in DESIGNED_CLASSES:
                return DESIGNED_CLASSES[c]
        if node.type == "Summary" and i and FOLD_BLOCK_CLASS in chain[i - 1].classes:
            return "fold title"
    return ""


def _placed(chain: List[Node]) -> bool:
    for node in chain:
        if node.type == "Td" and PANEL_CELL_CLASS in node.classes:
            continue
        if node.type in PLACED_TYPES or CONTROL_STRIP_CLASS in node.classes:
            return True
        for c in node.classes:
            if c in TITLE_CLASSES or c in HEADLINE_CLASSES or c.startswith(EMPTY_STATE_PREFIX):
                return True
    return False


def _is_section(node: Node) -> bool:
    return node.type == "Details" or any(c in SECTION_TOKENS or c.endswith("-card") for c in node.classes)


def _is_title(node: Node) -> bool:
    return node.type in HEADINGS or node.type == "Summary" or any(c in TITLE_CLASSES for c in node.classes)


def text_of(obj: Any) -> str:
    """The visible words under `obj`, spaces collapsed, the info mark dropped."""
    parts: List[str] = []

    def visit(o: Any) -> None:
        if o is None or isinstance(o, bool):
            return
        if isinstance(o, (str, int, float)):
            parts.append(str(o))
            return
        if isinstance(o, (list, tuple)):
            for c in _flatten(o):
                visit(c)
            return
        n = o if isinstance(o, Node) else as_node(o)
        if n is not None and not _is_hidden(n) and n.type not in LAYOUT_SKIP_TYPES:
            block = n.type not in INLINE_TYPES
            parts.append(" " if block else "")        # a block's words never run into its neighbour's
            for c in _flatten(n.children):
                visit(c)
            parts.append(" " if block else "")
    visit(obj)
    return re.sub(r"\s+", " ", "".join(parts).replace("ⓘ", "")).strip()


def find_title(node: Node, limit: int = 400) -> str:
    """The first title (a heading, a Summary, a title class) under `node`, depth first; '' if none."""
    seen = [0]

    def visit(o: Any) -> Optional[str]:
        seen[0] += 1
        if seen[0] > limit or o is None or isinstance(o, (bool, str, int, float)):
            return None
        if isinstance(o, (list, tuple)):
            for c in _flatten(o):
                hit = visit(c)
                if hit:
                    return hit
            return None
        n = o if isinstance(o, Node) else as_node(o)
        if n is None or _is_hidden(n) or n.type in LAYOUT_SKIP_TYPES:
            return None
        if _is_title(n) or designed_role([n]) == "fold title":
            return text_of(n) or None
        for c in _flatten(n.children):
            hit = visit(c)
            if hit:
                return hit
        return None
    return visit(list(node.children)) or ""


def section_title(chain: List[Node], cache: Dict[int, str]) -> str:
    """The title of the nearest titled section around the run, else "(no titled section)"."""
    for node in reversed(chain[:-1]):
        if _is_section(node):
            if id(node) not in cache:
                cache[id(node)] = find_title(node)
            if cache[id(node)]:
                return cache[id(node)][:60]
    return "(no titled section)"


def _table_shape(node: Node) -> Tuple[int, int]:
    rows, cols = 0, 0

    def visit(o: Any, in_head: bool) -> None:
        nonlocal rows, cols
        n = o if isinstance(o, Node) else as_node(o)
        if n is None or _is_hidden(n):
            return
        if n.type == "Tr":
            cells = [c for c in (as_node(k) if not isinstance(k, Node) else k for k in _flatten(n.children))
                     if c is not None and c.type in ("Td", "Th")]
            cols = max(cols, len(cells))
            rows += 0 if in_head else 1
            return
        for c in _flatten(n.children):
            visit(c, in_head or n.type == "Thead")
    visit(node, False)
    return rows, cols


def _count_type(node: Node, type_: str) -> int:
    """How many elements of `type_` are under `node` (static blocks parsed)."""
    total = 0
    for c in _flatten(node.children):
        n = c if isinstance(c, Node) else as_node(c)
        if n is None or _is_hidden(n):
            continue
        total += (1 if n.type == type_ else 0) + _count_type(n, type_)
    return total


def _text_only(node: Node) -> bool:
    for c in _flatten(node.children):
        if c is None or isinstance(c, (bool, str, int, float)):
            continue
        n = c if isinstance(c, Node) else as_node(c)
        if n is None:
            continue
        if n.type not in INLINE_TYPES or n.type == "A" or not _text_only(n):
            return False
    return True


def _block_kind(node: Node) -> Optional[Tuple[str, str, bool]]:
    """(kind, title, descend) for a block the inventory names, None for a wrapper to look through."""
    classes = set(node.classes)
    role = next((DESIGNED_CLASSES[c] for c in node.classes if c in DESIGNED_CLASSES), "")
    if role:
        return f"designed line ({role})", text_of(node)[:80], False
    if node.type == "Table":
        r, c = _table_shape(node)
        return f"table {r} rows x {c} cols", "", False
    if node.type == "DataTable":
        return (f"table {len(node.props.get('data') or [])} rows x {len(node.props.get('columns') or [])} cols",
                "", False)
    if node.type == "Graph":
        texts = _figure_texts(node.props.get("figure"))
        return "chart", (texts[0] if texts else ""), False
    if node.type == "Details":
        summary = next((as_node(c) for c in _flatten(node.children) if getattr(as_node(c), "type", "") == "Summary"),
                       None) if node.source == "dash" else None
        kind = "fold" if FOLD_BLOCK_CLASS in classes else "drawer"
        return kind, (text_of(summary) if summary else find_title(node)), True
    if node.type in ("Ul", "Ol"):
        n = _count_type(node, "Li")
        return f"list {n} items", "", False
    if classes & HEADLINE_CLASSES:
        return "headline", text_of(node)[:80], False
    if classes & {"tk-strip", "tf-bar", "blotter-filter-bar", "toolbar", "tf-bar-slot"}:
        return "strip", text_of(node)[:80], False
    if any("tile" in c or c == "cards" or c.startswith("cards--") for c in classes):
        n = sum(1 for c in _flatten(node.children) if c is not None and not isinstance(c, str))
        return f"tiles ({n})", text_of(node)[:80], False
    if node.type in {"Button", "Dropdown", "RadioItems", "Checklist", "Input", "DatePickerSingle",
                     "DatePickerRange", "Tabs"}:
        return "controls", text_of(node)[:40] or str(node.props.get("placeholder") or ""), False
    if _is_title(node):
        return "title", text_of(node)[:80], False
    if node.type == "Markdown":
        return None
    return None


def layout_inventory(tree: Any, max_depth: int = 3) -> List[str]:
    """The page's blocks in order, one line each (kind: title), cards indented over their contents."""
    lines: List[str] = []

    def add(depth: int, kind: str, title: str = "") -> None:
        lines.append("  " * depth + kind + (f": {title}" if title else ""))

    def visit(obj: Any, depth: int) -> None:
        if obj is None or isinstance(obj, bool):
            return
        if isinstance(obj, (list, tuple)):
            for c in _flatten(obj):
                visit(c, depth)
            return
        if isinstance(obj, (str, int, float)):
            s = re.sub(r"\s+", " ", str(obj)).strip()
            if re.search(r"[A-Za-z0-9]", s):
                add(depth, "text", s[:80])
            return
        node = obj if isinstance(obj, Node) else as_node(obj)
        if node is None or _is_hidden(node) or node.type in LAYOUT_SKIP_TYPES:
            return
        kind = _block_kind(node)
        if kind is not None:
            label, title, descend = kind
            add(depth, label, title)
            if descend and depth < max_depth:
                for c in _flatten(node.children):
                    if getattr(as_node(c) if not isinstance(c, Node) else c, "type", "") != "Summary":
                        visit(c, depth + 1)
            return
        if _is_section(node):
            add(depth, "card", find_title(node) or "(untitled)")
            if depth < max_depth:
                for c in _flatten(node.children):
                    visit(c, depth + 1)
            return
        if _text_only(node) and node.children:
            s = text_of(node)
            if s:
                add(depth, "text", s[:80])
            return
        for c in _flatten(node.children):
            visit(c, depth)

    visit(tree, 0)
    out: List[str] = []
    for line in lines:                                   # a run of identical lines as one, "xN"
        if out and out[-1][0] == line:
            out[-1][1] += 1
        else:
            out.append([line, 1])
    return [f"{line}  x{n}" if n > 1 else line for line, n in out]


# ----------------------------------------------------------------------------- duplicate filters
# DUPLICATE_FILTER (the user, five rounds running: filters live in the column headings, never in a bar
# above the table repeating the column names). A filter control outside a table (Dropdown, Checklist,
# RadioItems, a date picker, Input, Select) is named by the text beside it (a Label or a short text
# sibling, in its parent or else its grandparent) and, for a Dropdown, its placeholder; a name equal to a
# column heading of a table in the same card, or of the next table on the tab, is a finding. Names are
# compared lowercased and trimmed, a count "(3)" and a trailing "only" dropped, and a name ending in
# "date" also matches a plain "Date" ("Trade date" ~ "Date"). A control inside a table (a heading's own
# filter, an in-row editor) is never looked at. Allowed above a table: the free-text search, the
# Group-by / Slice switch and the P&L Period / Table switch (`ALLOWED_FILTER_LABELS`), and buttons.
FILTER_CONTROL_TYPES = {"Dropdown", "Checklist", "RadioItems", "DatePickerRange", "DatePickerSingle", "Input",
                        "Select"}
ALLOWED_FILTER_LABELS = {"search", "group", "group by", "slice", "period", "table"}
IN_TABLE_TYPES = {"Table", "Thead", "Tbody", "Tfoot", "Tr", "Th", "Td", "DataTable"}
_HEAD_GLYPHS = re.compile(r"[▲▼△▽↑↓⇅⏷⌄▾▴▸▹ⓘ…]")


def _node_of(obj: Any) -> Optional[Node]:
    return obj if isinstance(obj, Node) else as_node(obj)


def _norm_label(text: Any) -> str:
    """A label or a heading as compared: lowercased, glyphs and a count dropped, a trailing "only" dropped."""
    s = _HEAD_GLYPHS.sub(" ", str(text or "")).lower()
    s = re.sub(r"\(\d[\d,]*\)", " ", s)
    s = re.sub(r"[^\w&%/ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:-len(" only")].strip() if s.endswith(" only") else s


def _label_variants(text: Any) -> set:
    n = _norm_label(text)
    out = {n} if n else set()
    if n.endswith(" date"):
        out.add("date")
    return out


def _has_control(node: Node) -> bool:
    for c in _flatten(node.children):
        n = _node_of(c)
        if n is not None and not _is_hidden(n) and (n.type in FILTER_CONTROL_TYPES or _has_control(n)):
            return True
    return False


def _heading_text(th: Node) -> str:
    """The words a column heading draws: its title, never its own filter panel, control or button."""
    parts: List[str] = []

    def visit(o: Any) -> None:
        if o is None or isinstance(o, bool):
            return
        if isinstance(o, (str, int, float)):
            parts.append(str(o))
            return
        if isinstance(o, (list, tuple)):
            for c in _flatten(o):
                visit(c)
            return
        n = _node_of(o)
        if (n is None or _is_hidden(n) or n.type in FILTER_CONTROL_TYPES or n.type in LAYOUT_SKIP_TYPES
                or n.type == "Button" or any(p in c for c in n.classes for p in ("panel", "menu", "popover"))):
            return
        parts.append(" ")
        for c in _flatten(n.children):
            visit(c)
        parts.append(" ")
    for c in _flatten(th.children):
        visit(c)
    return re.sub(r"\s+", " ", _HEAD_GLYPHS.sub(" ", "".join(parts))).strip()


def _table_headings(node: Node) -> List[str]:
    if node.type == "DataTable":
        out = []
        for col in node.props.get("columns") or []:
            name = col.get("name") if isinstance(col, dict) else None
            name = name[-1] if isinstance(name, (list, tuple)) and name else name
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
        return out
    out: List[str] = []

    def visit(o: Any) -> None:
        n = _node_of(o)
        if n is None or _is_hidden(n):
            return
        if n.type == "Th":
            t = _heading_text(n)
            if t:
                out.append(t)
            return
        for c in _flatten(n.children):
            visit(c)
    visit(node)
    return out


def _control_labels(control: Node, parents: List[Node]) -> List[str]:
    """The names a filter control carries: the text beside it and a Dropdown's placeholder."""
    names: List[str] = []
    for depth in (1, 2):
        if len(parents) < depth:
            break
        parent, own = parents[-depth], (control if depth == 1 else parents[-1])
        for sib in _flatten(parent.children):
            if isinstance(sib, str):
                if sib.strip():
                    names.append(sib.strip())
                continue
            s = _node_of(sib)
            if s is None or s is own or _is_hidden(s) or s.type in FILTER_CONTROL_TYPES or s.type == "Button":
                continue
            if _has_control(s):
                continue
            t = text_of(s)
            if t and len(t) <= 40:
                names.append(t)
        if names:
            break
    placeholder = control.props.get("placeholder") if control.type == "Dropdown" else None
    if isinstance(placeholder, str) and placeholder.strip():
        names.append(placeholder.strip())
    return names


def duplicate_filters(tab: str, tree: Any) -> List[Finding]:
    """DUPLICATE_FILTER findings of one tab's tree (a list of trees: one per sub-tab)."""
    out: List[Finding] = []
    seen: set = set()
    for root in (tree if isinstance(tree, list) else [tree]):
        events: List[tuple] = []          # ("control", node, parents) | ("table", headings, parents), page order

        def visit(o: Any, parents: List[Node]) -> None:
            if o is None or isinstance(o, (bool, str, int, float)):
                return
            if isinstance(o, (list, tuple)):
                for c in _flatten(o):
                    visit(c, parents)
                return
            n = _node_of(o)
            if n is None or _is_hidden(n) or n.type in LAYOUT_SKIP_TYPES:
                return
            if n.type in ("Table", "DataTable"):
                events.append(("table", _table_headings(n), list(parents)))
                return
            if n.type in FILTER_CONTROL_TYPES:
                if not any(p.type in IN_TABLE_TYPES for p in parents):
                    events.append(("control", n, list(parents)))
                return
            for c in _flatten(n.children):
                visit(c, parents + [n])
        visit(root, [])

        for i, (kind, control, parents) in enumerate(events):
            if kind != "control":
                continue
            section = next((p for p in reversed(parents) if _is_section(p)), None)
            headings: List[str] = []
            for kind2, heads, tparents in events:
                if kind2 == "table" and section is not None and any(p is section for p in tparents):
                    headings.extend(heads)
            nxt = next((e for e in events[i + 1:] if e[0] == "table"), None)
            if nxt:
                headings.extend(nxt[1])
            if not headings:
                continue
            for name in _control_labels(control, parents):
                variants = _label_variants(name)
                if not variants or variants & ALLOWED_FILTER_LABELS or _norm_label(name).startswith("search"):
                    continue
                exact = [h for h in headings if _norm_label(h) == _norm_label(name)]
                loose = [h for h in headings if _label_variants(h) & variants]
                column = (exact or loose or [None])[0]
                if column is None or (name.lower(), column.lower()) in seen:
                    continue
                seen.add((name.lower(), column.lower()))
                out.append(Finding(tab, "DUPLICATE_FILTER", name, path_of(parents + [control]), False,
                                   f"repeats the column heading \"{column}\""))
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


def run_checks(tabs: List[str], layout: bool = False, shots: bool = False,
               shots_open: bool = False) -> Tuple[List[Finding], Dict[str, float], dict]:
    """(findings, seconds per step, extras): extras["layout"] = {tab: inventory lines} with `layout`,
    extras["shots"] = one sentence with `shots` or `shots_open` (which adds the panel and open shots)."""
    import warnings
    warnings.filterwarnings("ignore")
    from ui import app as uiapp

    findings: List[Finding] = []
    timings: Dict[str, float] = {}
    extras: dict = {"layout": {}}
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
                    if tab in LAYOUT_TABS:
                        findings.extend(duplicate_filters(tab, tree))
                    if layout:
                        trees = tree if isinstance(tree, list) else [tree]
                        lines: List[str] = []
                        for i, t in enumerate(trees):
                            if i:
                                lines.append(f"(sub-tab {i + 1})")
                            lines.extend(layout_inventory(t))
                        extras["layout"][tab] = lines
                except Exception as exc:  # noqa: BLE001 -- a screen that raises is itself the finding
                    findings.append(Finding(tab, "RENDER_ERROR", f"{type(exc).__name__}: {exc}"[:200],
                                            "(render)", False))
                for rec in errors.records:
                    msg = rec.getMessage()
                    if rec.exc_info and rec.exc_info[1] is not None:
                        msg += f" ({type(rec.exc_info[1]).__name__}: {rec.exc_info[1]})"
                    findings.append(Finding(tab, "RENDER_ERROR", msg[:200], f"(log {rec.name})", False))
                timings[tab] = time.perf_counter() - t0
            if shots or shots_open:
                t0 = time.perf_counter()
                extras["shots"] = take_shots(app, [t for t in tabs if t in SHOT_LABELS], opened=shots_open)
                timings["shots"] = time.perf_counter() - t0
            uiapp.set_active_db(None)
    finally:
        root_logger.removeHandler(errors)
        root_logger.setLevel(previous)
    return findings, timings, extras


# ----------------------------------------------------------------------------- screenshots
SHOT_DIR = ROOT / "reports" / "ui_shots"
SHOT_LABELS = {"book": "Book", "pnl": "P&L", "risk": "Risk", "blotter": "Blotter", "data": "Data"}
SHOT_WIDTH = 1680
AVOID_PORTS = {8050}                                 # the monitor's default port, and Henry's app's
SETTLE_SECONDS = 20.0                                # the most a shot waits for a tab's late figures
STABLE_SECONDS = 2.0                                 # the page's text unchanged this long = drawn
PANEL_ROW_TYPES = {"book": "ROW_TYPE", "pnl": "ROW_TYPE", "risk": "ROW_TYPE"}   # the tab module's row id type
FOLD_SKIP_TYPES = {"risk-commodity-row"}             # a single-select row (the curve on click), not a fold
MAX_FOLD_CLICKS = 40

# Dash is idle: no callback requested, queued or running, no loading flag, no `_dash-loading` element,
# and every late-figure poll of the tab (a dcc.Interval the tab enables while a background figure is
# still computing, and disables in the re-render that shows it) switched off again.
_IDLE_JS = """(pollIds) => {
  const st = window.store && window.store.getState();
  if (!st) return false;
  const cbs = Object.assign({}, st.callbacks || {});
  delete cbs.stored; delete cbs.completed;
  if ([].concat(...Object.values(cbs)).length || st.isLoading) return false;
  if (document.querySelector('._dash-loading')) return false;
  const strs = (st.paths && st.paths.strs) || {};
  for (const id of pollIds) {
    if (!strs[id]) continue;
    let node = st.layout;
    for (const k of strs[id]) { node = node == null ? node : node[k]; }
    if (node && node.props && node.props.disabled === false) return false;
  }
  return true;
}"""

# The clickable ancestor (an element with an id) of every visible closed chevron, skipping `skip` types,
# and every visible closed <details>.
_CLOSED_FOLDS_JS = """(skip) => {
  const out = [];
  for (const chev of document.querySelectorAll('.tk-chev')) {
    if (!chev.offsetParent || !chev.textContent.trim().startsWith('\\u25b8')) continue;
    const el = chev.closest('[id]');
    if (!el || skip.some(t => el.id.includes('"type":"' + t + '"'))) continue;
    out.push(el.id);
  }
  for (const s of document.querySelectorAll('details:not([open]) > summary')) {
    if (s.offsetParent) out.push('summary:' + (s.id || s.textContent.trim().slice(0, 40)));
  }
  return out;
}"""


def _free_server(app):
    """A werkzeug server on a port the OS picks for it (bound atomically, so two runs at once never
    share one), never one of AVOID_PORTS; nothing already listening is touched."""
    from werkzeug.serving import make_server
    while True:
        server = make_server("127.0.0.1", 0, app.server, threaded=True)
        if server.port not in AVOID_PORTS:
            return server
        server.server_close()


def _late_poll_ids() -> List[str]:
    """The ids of the tabs' late-figure polls (the Book's z and Move, Risk's VaR and daily risk)."""
    from ui.tabs import book, risk
    return [i for i in (getattr(book, "RISK_POLL_ID", None), getattr(risk, "RISK_POLL_ID", None)) if i]


def _settle(page, poll_ids: List[str], budget: float = SETTLE_SECONDS) -> bool:
    """Wait until Dash is idle with every late figure landed, then until the page's text has not changed
    for STABLE_SECONDS; False when `budget` ran out first (the shot is taken anyway and says so)."""
    deadline = time.monotonic() + budget
    try:
        page.wait_for_load_state("networkidle", timeout=budget * 1000)
    except Exception:  # noqa: BLE001 -- an app that polls may never go network-idle: the Dash check decides
        pass
    try:
        page.wait_for_function(_IDLE_JS, arg=poll_ids, timeout=max(0.1, deadline - time.monotonic()) * 1000)
    except Exception:  # noqa: BLE001 -- playwright's TimeoutError: report it, take the shot
        return False
    last, since = None, time.monotonic()
    while time.monotonic() < deadline:
        text = page.evaluate("document.body.innerText")
        if text != last:
            last, since = text, time.monotonic()
        elif time.monotonic() - since >= STABLE_SECONDS:
            return True
        page.wait_for_timeout(250)
    return False


# The store a prop lives in, read from the Dash layout by id (None when the id is not on the page).
_PROP_JS = """([id, prop]) => {
  const st = window.store && window.store.getState();
  const path = st && st.paths && st.paths.strs && st.paths.strs[id];
  if (!path) return null;
  let node = st.layout;
  for (const k of path) { node = node == null ? node : node[k]; }
  return node && node.props ? node.props[prop] : null;
}"""


def _pick_as_of(page, poll_ids: List[str]) -> bool:
    """Wait for the as-of store to hold AS_OF (the page is opened at `?as_of=AS_OF`, which
    `ui/app.py::_as_of_from_url` turns into the store and a pick the day roll keeps), so the shots
    show the figures the text check reads; True once the store holds it."""
    from ui.tabs import header
    try:
        page.wait_for_function(f"() => ({_PROP_JS})([{header.AS_OF_STORE_ID!r}, 'data']) === {AS_OF!r}",
                               timeout=SETTLE_SECONDS * 1000)
    except Exception:  # noqa: BLE001 -- playwright's TimeoutError: the run says so
        return False
    _settle(page, poll_ids)
    return True


def _save(page, name: str) -> str:
    """A full-page PNG written whole (a temp file renamed over the old one), so a run beside this one
    never reads half a file."""
    import os
    data = page.screenshot(full_page=True)
    target = SHOT_DIR / name
    tmp = SHOT_DIR / f".{name}.{os.getpid()}.tmp"
    tmp.write_bytes(data)
    for attempt in range(10):
        try:
            os.replace(tmp, target)
            return name
        except PermissionError:                       # Windows: another run is reading the old file
            time.sleep(0.2 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    return f"{name} (not replaced: file in use)"


def _row_locator(page, row_type: str):
    return page.locator(f"[id*='\"type\":\"{row_type}\"']:visible").first


def _panel_shot(page, tab: str, poll_ids: List[str]) -> Optional[str]:
    """`<tab>_panel.png`: the first row's panel opened, then closed again. None when the tab has no rows."""
    import importlib
    attr = PANEL_ROW_TYPES.get(tab)
    row_type = getattr(importlib.import_module(f"ui.tabs.{tab}"), attr, None) if attr else None
    if not row_type or not _row_locator(page, row_type).count():
        return None
    _row_locator(page, row_type).click()
    _settle(page, poll_ids)
    name = _save(page, f"{tab}_panel.png")
    if _row_locator(page, row_type).count():
        _row_locator(page, row_type).click()
        _settle(page, poll_ids)
    return name


def _open_shot(page, tab: str, poll_ids: List[str]) -> str:
    """`<tab>_open.png`: Expand all pressed and every closed fold opened, one click at a time."""
    expand = page.locator("button[id$='expand-all']:visible").first
    if expand.count():
        expand.click()
        _settle(page, poll_ids)
    clicked: set = set()
    for _ in range(MAX_FOLD_CLICKS):
        todo = [k for k in page.evaluate(_CLOSED_FOLDS_JS, sorted(FOLD_SKIP_TYPES)) if k not in clicked]
        if not todo:
            break
        key = todo[0]
        clicked.add(key)
        if key.startswith("summary:"):
            page.locator("details:not([open]) > summary:visible").first.click()
        else:
            page.locator(f"[id='{key}']" if "'" not in key else f'[id="{key}"]').first.click()
        _settle(page, poll_ids, budget=8)
    _settle(page, poll_ids)
    return _save(page, f"{tab}_open.png")


def take_shots(app, tabs: List[str], opened: bool = False) -> str:
    """Per tab under reports/ui_shots/, 1680 px wide, through playwright's own headless Chromium: the
    page as a trader sees it at the check's AS_OF, picked in the header (`<tab>.png`, taken once Dash is idle and the late figures have landed);
    with `opened` also the first row's panel opened (`<tab>_panel.png`) and every fold opened
    (`<tab>_open.png`), which adds minutes. The app
    is served in this process on a port of its own (never 8050) and stopped after. Nothing is
    installed: without playwright and its browser the step is skipped and says so."""
    import importlib.util
    if importlib.util.find_spec("playwright") is None:
        selenium = importlib.util.find_spec("selenium") is not None
        return ("screenshots skipped: playwright is not installed" + (" (selenium is, but is not wired here)"
                if selenium else " and neither is selenium") + "; nothing was installed")
    import threading
    from playwright.sync_api import sync_playwright

    server = _free_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    written: List[str] = []
    unsettled: List[str] = []
    poll_ids = _late_poll_ids()
    picked = False
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001 -- no browser downloaded for playwright: say so
                return (f"screenshots skipped: playwright has no browser to launch ({type(exc).__name__}); "
                        "nothing was installed")
            try:
                page = browser.new_page(viewport={"width": SHOT_WIDTH, "height": 1000})
                page.goto(f"http://127.0.0.1:{server.port}/?as_of={AS_OF}", wait_until="networkidle")
                picked = _pick_as_of(page, poll_ids)
                SHOT_DIR.mkdir(parents=True, exist_ok=True)
                for tab in tabs:
                    page.locator(f"#main-tabs >> text={SHOT_LABELS[tab]}").first.click()
                    if not _settle(page, poll_ids):
                        unsettled.append(tab)
                    written.append(_save(page, f"{tab}.png"))
                    if not opened:
                        continue
                    panel = _panel_shot(page, tab, poll_ids)
                    if panel:
                        written.append(panel)
                    written.append(_open_shot(page, tab, poll_ids))
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    note = "" if picked else f"; the as-of picker did not take {AS_OF}: shots are at today"
    note += (f"; still changing after {SETTLE_SECONDS:.0f} s, taken anyway: {', '.join(unsettled)}"
            if unsettled else "")
    return f"screenshots written to {SHOT_DIR.relative_to(ROOT)}: {', '.join(written)}{note}"


# ----------------------------------------------------------------------------- output
RULE_ORDER = ["RENDER_ERROR", "DUPLICATE_FILTER", "BANNED", "ENGINE_WORD", "LOWERCASE", "LOOSE_TEXT", "DOUBLE_SPACE",
              "TRAILING_PUNCT_SPACE", PART_RULE, "LOOSE_BLOCK", "DESIGNED_LINE"]
PAGE_ORDER_RULES = {"LOOSE_BLOCK", "DESIGNED_LINE"}  # listed in page order, not by path
# A tab whose LOOSE_BLOCK findings never fail the run, with the note printed beside it: none since the
# Data tab's rebuild landed (2026-09-29); kept for the next tab another session is still changing.
INFORMATIONAL_TABS: Dict[str, str] = {}


def grouped(findings: List[Finding]) -> List[Tuple[Finding, int]]:
    counts: Dict[tuple, int] = collections.Counter(f.key() for f in findings)
    first: Dict[tuple, Finding] = {}
    seq: Dict[tuple, int] = {}
    for i, f in enumerate(findings):
        first.setdefault(f.key(), f)
        seq.setdefault(f.key(), i)

    def order(item):
        f = item[0]
        where = (seq[f.key()], "", "") if f.rule in PAGE_ORDER_RULES else (0, f.path, f.text)
        return (TAB_ORDER.index(f.tab) if f.tab in TAB_ORDER else 99, f.hover,
                RULE_ORDER.index(f.rule) if f.rule in RULE_ORDER else 99, *where)
    return sorted(((first[k], n) for k, n in counts.items()), key=order)


FAILING_HOVER_RULES = {"LOWERCASE", "BANNED"}


def failing(f: Finding, strict: bool = False) -> bool:
    """Whether `f` fails the run: every visible finding and hover LOWERCASE / BANNED; LOOSE_BLOCK only
    with `strict` (and not on an informational tab); DESIGNED_LINE never."""
    if f.rule in ("DESIGNED_LINE", PART_RULE):
        return False
    if f.rule == "LOOSE_BLOCK":
        return strict and f.tab not in INFORMATIONAL_TABS
    return not f.hover or f.rule in FAILING_HOVER_RULES


def rule_name(f: Finding) -> str:
    return f"hover:{f.rule}" if f.hover else f.rule


def _note(tab: str) -> str:
    return f"  ({INFORMATIONAL_TABS[tab]})" if tab in INFORMATIONAL_TABS else ""


def print_layout(inventory: Dict[str, List[str]], out=sys.stdout) -> None:
    for tab in [t for t in TAB_ORDER if t in inventory]:
        out.write(f"\n== layout: {tab} =={_note(tab)}\n")
        for line in inventory[tab]:
            out.write(f"  {line}\n")


def print_report(findings: List[Finding], timings: Dict[str, float], out=sys.stdout, strict: bool = False) -> None:
    items = grouped(findings)
    tab = None
    for f, n in items:
        if f.tab != tab:
            tab = f.tab
            out.write(f"\n== {tab} =={_note(tab)}\n")
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
        out.write(f"  {t:<8} {parts}{_note(t)}\n")
    visible = sum(1 for f, _n in items if not f.hover and f.rule not in PAGE_ORDER_RULES and f.rule != PART_RULE)
    hover = sum(1 for f, _n in items if f.hover)
    hover_failing = sum(1 for f, _n in items if f.hover and failing(f, strict))
    loose = sum(1 for f, _n in items if f.rule == "LOOSE_BLOCK")
    designed = sum(1 for f, _n in items if f.rule == "DESIGNED_LINE")
    took = ", ".join(f"{k} {v:.1f}s" for k, v in timings.items())
    out.write(f"  {visible} visible, {hover} hover distinct findings ({hover_failing} of them failing); "
              f"{loose} LOOSE_BLOCK ({'failing' if strict else 'report-only under --lenient'}), "
              f"{designed} DESIGNED_LINE (report-only) ({took})\n")


def write_json(findings: List[Finding], timings: Dict[str, float], extras: Optional[dict] = None,
               path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"tab": f.tab, "rule": f.rule, "hover": f.hover, "text": f.text, "path": f.path, "detail": f.detail,
             "count": n, "informational": f.tab in INFORMATIONAL_TABS} for f, n in grouped(findings)]
    doc = {"as_of": AS_OF, "timings": timings, "findings": rows}
    if extras and extras.get("layout"):
        doc["layout"] = extras["layout"]
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="py 2_launcher.py ui-check", description=__doc__.split("\n")[0])
    parser.add_argument("--tab", help="one screen: " + ", ".join(TAB_ORDER))
    parser.add_argument("--json", action="store_true", help=f"also write the findings to {REPORT_PATH.relative_to(ROOT)}")
    parser.add_argument("--strict", action="store_true", help="the default since 2026-09-29, kept so older "
                                                               "commands still run")
    parser.add_argument("--lenient", action="store_true", help="LOOSE_BLOCK report-only (text outside a table, "
                                                                "title, headline, control, chart or empty state)")
    parser.add_argument("--layout", action="store_true", help="print each tab's blocks in page order")
    parser.add_argument("--shots", action="store_true",
                        help=f"one full-page PNG per tab under {SHOT_DIR.relative_to(ROOT)} once the late "
                             "figures land: <tab>.png (needs playwright)")
    parser.add_argument("--shots-open", action="store_true",
                        help="the --shots PNGs plus <tab>_panel.png (first row opened) and <tab>_open.png "
                             "(every fold opened); a few minutes more")
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
    findings, timings, extras = run_checks(tabs, layout=args.layout, shots=args.shots,
                                          shots_open=args.shots_open)
    if args.layout:
        print_layout(extras["layout"])
    strict = not args.lenient
    print_report(findings, timings, strict=strict)
    if args.shots or args.shots_open:
        print(extras.get("shots") or "screenshots: nothing to shoot")
    if args.json:
        print(f"written {write_json(findings, timings, extras)}")
    return 1 if any(failing(f, strict) for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
