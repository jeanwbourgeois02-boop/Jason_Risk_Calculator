"""The table helpers of the Blotter and Data tabs (Phase G, round 2, 2026-09-29).

Every table these two tabs draw goes through here, so the look pass (round 3) can switch them in
one place: the trade kit's classes (`ui/assets/style.css`, "Phase G: the trade kit": a
`.book-table.book-grid.tk-table` inside a `.book-card.tk-card`, its controls in a `.tk-strip`),
a sortable header (click a title: descending, again ascending, a third time back to the default
order; the arrow only on the column sorted), the total row first, a check cell (a green tick or
a red cross with its sentence on hover). Display only: nothing here computes a figure.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from dash import html

from ui.tabs.formatting import MISSING, plain_words

TABLE_CLASS = "book-table book-grid tk-table"
CARD_CLASS = "book-card book-main tk-card"
STRIP_CLASS = "tk-strip"
SLOT_CLASS = "tk-table-slot"
TICK = "✓"      # ✓
CROSS = "✗"     # ✗

# A column: (key, title, css class ('l' for text, '' for figures), hover of the title, sortable).
Column = Tuple[str, str, str, str, bool]


def card(children: list, id: Optional[str] = None, className: str = "", style: Optional[dict] = None) -> html.Div:
    """The one card around a table (its strip of controls first, then the table)."""
    classes = " ".join(c for c in (CARD_CLASS, className) if c)
    props: Dict[str, Any] = {"className": classes}
    if id:
        props["id"] = id
    if style is not None:
        props["style"] = style
    return html.Div(children, **props)


def strip(children: list, className: str = "") -> html.Div:
    """The card's top strip: the title and the controls on one line."""
    return html.Div(children, className=" ".join(c for c in (STRIP_CLASS, className) if c))


def strip_title(title, hover: Optional[str] = None) -> html.Span:
    """The strip's title, its definition on hover (never a paragraph)."""
    text = plain_words(hover) if hover else None
    return html.Span(title, className="tk-title", title=text or None)


def next_sort(current: Optional[dict], key: str) -> Optional[dict]:
    """A click on the title `key`: descending, then ascending, then back to the default (None)."""
    cur = current or {}
    if cur.get("key") != key:
        return {"key": key, "dir": "desc"}
    if cur.get("dir") == "desc":
        return {"key": key, "dir": "asc"}
    return None


def head(columns: Sequence[Column], sort: Optional[dict], sort_type: str) -> html.Thead:
    """The header row; a sortable title is a clickable span with the pattern id
    {"type": sort_type, "idx": key}; the arrow shows on the column sorted only."""
    cells = []
    for key, title, cls, tip, sortable in columns:
        if sortable:
            arrow = ""
            if (sort or {}).get("key") == key:
                arrow = " ▼" if (sort or {}).get("dir") != "asc" else " ▲"
            inner = html.Span([title, html.Span(arrow, className="book-sort-arrow")],
                              id={"type": sort_type, "idx": key}, n_clicks=0, className="tk-sort")
        else:
            inner = title
        classes = " ".join(c for c in (cls, "tk-sortable" if sortable else "") if c)
        cells.append(html.Th(inner, className=classes or None, title=plain_words(tip) or None))
    return html.Thead(html.Tr(cells))


def sort_records(records: List[dict], sort: Optional[dict], keys: Dict[str, Callable[[dict], Any]]) -> List[dict]:
    """`records` in the order `sort` asks ({"key", "dir"}); the given order when `sort` is None or
    names no known column. A record whose value is None goes last either way (stable)."""
    if not sort or sort.get("key") not in keys:
        return list(records)
    fn = keys[sort["key"]]
    desc = sort.get("dir") != "asc"
    known = [r for r in records if fn(r) is not None]
    unknown = [r for r in records if fn(r) is None]
    try:
        known = sorted(known, key=fn, reverse=desc)
    except TypeError:
        known = sorted(known, key=lambda r: str(fn(r)), reverse=desc)
    return known + unknown


def table(thead: html.Thead, rows: Iterable, className: str = "") -> html.Table:
    """The kit's table: the header, then the body rows."""
    classes = " ".join(c for c in (TABLE_CLASS, className) if c)
    return html.Table([thead, html.Tbody(list(rows))], className=classes)


def td(content, left: bool = False, title: Optional[str] = None, className: str = "", **props) -> html.Td:
    """One cell: left-aligned text or a right-aligned figure, its hover in plain words."""
    classes = " ".join(c for c in ("l" if left else "", className) if c)
    if title:
        props["title"] = plain_words(title)
    return html.Td(content if content not in (None, "") else MISSING, className=classes or None, **props)


def total_row(cells: list) -> html.Tr:
    """The total row: first under the header, bold and shaded (sticky in the kit)."""
    return html.Tr(cells, className="book-total")


def note_row(text, n_cols: int, className: str = "cell-amber", title: Optional[str] = None) -> html.Tr:
    """A one-cell line across the table (the amber line above the fills in no trade)."""
    props: Dict[str, Any] = {"colSpan": n_cols, "className": f"l {className}".strip()}
    if title:
        props["title"] = plain_words(title)
    return html.Tr(html.Td(text, **props))


def check_cell(ok: Optional[bool], reason: str = "", ok_hover: str = "") -> html.Span:
    """A check: a green tick, or a red cross with the reason on hover; a grey dash when there
    was nothing to check (`ok` None)."""
    if ok is None:
        return html.Span(MISSING, className="cell-missing", title=plain_words(reason) or "nothing to check")
    if ok:
        return html.Span(TICK, className="cell-pos", title=plain_words(ok_hover or reason) or None)
    return html.Span(CROSS, className="cell-red", title=plain_words(reason) or "check failed")


def chip(text: str, level: str = "", title: Optional[str] = None) -> html.Span:
    """A small level chip ('Missing' red, 'Check' amber, 'OK' green) from the kit's level chips."""
    cls = {"red": "level-chip level-chip--red", "amber": "level-chip level-chip--amber",
           "green": "level-chip level-chip--green", "grey": "level-chip level-chip--estimated"}.get(level, "level-chip")
    return html.Span(text, className=cls, title=plain_words(title) or None)


def clicked(ctx_triggered) -> bool:
    """True when a pattern-matching click really happened (a value, not the insertion of a new
    title with n_clicks 0 / None)."""
    return bool(ctx_triggered) and bool((ctx_triggered[0] or {}).get("value"))
