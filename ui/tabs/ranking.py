"""Every table ranks (user, 2026-09-22: "for all tables, make sure we can rank").

One rule for the app's tables: a click on a column header sorts the rows by that column,
a second click reverses it, shift-click adds a second key (Dash's native sort), and the
order survives the tab's own rebuilds because `sort_by` is kept in the browser session.
For the ranking to be right a number is stored as a number and formatted by the table
(`amount`, `rate`, `percent`), never as a pre-formatted string, which would rank "9,000"
above "10,000,000". A cell with nothing to show is None and `nully` says what the table
prints for it ("" or "n/a"); a sign colour keys on `{col} < 0`, never on the text.

A total or net row never takes part in a ranking: `with_footer` renders it as a
header-less second table under the first with the same columns, styles and widths, so it
reads as the table's last row and stays put whatever the sort. `column_widths` gives both
tables the same widths, from the content, so their columns line up.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, List, Optional, Sequence

from dash import dash_table, html
from dash.dash_table.Format import Format, Group, Scheme, Sign, Symbol, Trim

NULL_TEXTS = ("", "n/a", "\u2014", "Unavailable")   # strings a numeric column may still carry; they rank last
_SPEC_RE = re.compile(r"^(?P<sign>[(+\- ])?(?P<symbol>\$)?(?P<group>,)?(?:\.(?P<precision>\d+))?(?P<trim>~)?(?P<type>[f%])?$")


# ----------------------------------------------------------------------------- formats
def amount(decimals: int = 0, nully: str = "", trim: bool = False) -> dict:
    """Whole units with thousands separators and a negative in parentheses: 1,234 / (1,234).
    `decimals` > 0 keeps that many; `trim` drops trailing zeros (12.5, not 12.50)."""
    fmt = Format(precision=decimals, scheme=Scheme.fixed, group=Group.yes, sign=Sign.parantheses, nully=nully)
    if trim:
        fmt = fmt.trim(Trim.yes)
    return fmt.to_plotly_json()


def rate(decimals: int = 6, nully: str = "", trim: bool = False) -> dict:
    """A quote or a fill at fixed decimals, no parentheses (a rate is never negative)."""
    fmt = Format(precision=decimals, scheme=Scheme.fixed, group=Group.yes, nully=nully)
    if trim:
        fmt = fmt.trim(Trim.yes)
    return fmt.to_plotly_json()


def percent(decimals: int = 1, nully: str = "n/a") -> dict:
    """A signed per cent: +12.5% / -3.0%."""
    fmt = Format(precision=decimals, scheme=Scheme.fixed, sign=Sign.positive, nully=nully)
    return fmt.symbol(Symbol.yes).symbol_suffix("%").to_plotly_json()


def count(nully: str = "") -> dict:
    return Format(precision=0, scheme=Scheme.fixed, group=Group.yes, nully=nully).to_plotly_json()


# ----------------------------------------------------------------------------- columns
def numeric(name: str, col_id: str, fmt: Optional[dict] = None, **extra) -> dict:
    return {"name": name, "id": col_id, "type": "numeric", "format": fmt or amount(), **extra}


def text(name: str, col_id: str, **extra) -> dict:
    return {"name": name, "id": col_id, "type": "text", **extra}


def value(v) -> Optional[float]:
    """The number a record carries for a cell: None for None / NaN / '' (nothing to show),
    else a float. Keeps a string that is not a number as it is (an illustrative
    "(sample)" cell, say), which the table shows raw and ranks last (`NULL_TEXTS`)."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) else f


# ----------------------------------------------------------------------------- table props
def sortable(table_id: Optional[str] = None, persisted: Sequence[str] = ()) -> dict:
    """The DataTable props that make a table rank: native multi-column sort, strings a
    numeric column may still carry ranked last, and, when the table has an id, the sort
    kept in the browser session so a rebuilt tab comes back in the same order."""
    props = {"sort_action": "native", "sort_mode": "multi", "sort_as_null": list(NULL_TEXTS)}
    if table_id:
        props.update(persistence=True, persistence_type="session",
                     persisted_props=sorted({"sort_by", *persisted}))
    return props


def sign_styles(columns: Iterable[str], neg: str = "var(--neg)", pos: str = "var(--pos)",
                bold: bool = False, nil: Optional[dict] = None) -> List[dict]:
    """`style_data_conditional` rules colouring a numeric cell by its sign; `nil` is the
    style of an empty cell (None), e.g. {"color": "var(--muted)", "fontStyle": "italic"}."""
    weight = {"fontWeight": "700"} if bold else {}
    out: List[dict] = []
    for c in columns:
        out.append({"if": {"column_id": c, "filter_query": f"{{{c}}} < 0"}, "color": neg, **weight})
        out.append({"if": {"column_id": c, "filter_query": f"{{{c}}} > 0"}, "color": pos, **weight})
        if nil:
            out.append({"if": {"column_id": c, "filter_query": f"{{{c}}} is nil"}, **nil})
    return out


# ----------------------------------------------------------------------------- widths and footer
def display_length(v, fmt: Optional[dict] = None) -> int:
    """How many characters the table will print for `v`: a number through its d3
    specifier (grouping, precision, parentheses, trim, per cent), anything else as text."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return len((fmt or {}).get("nully", "") or "")
    if isinstance(v, str) or fmt is None:
        return len(str(v))
    spec = _SPEC_RE.match(fmt.get("specifier", "") or "")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return len(str(v))
    if not spec:
        return len(f"{f:,.0f}")
    precision = int(spec.group("precision") or 0)
    body = f"{abs(f):{',' if spec.group('group') else ''}.{precision}f}"
    if spec.group("trim") and "." in body:
        body = body.rstrip("0").rstrip(".")
    sign = spec.group("sign")
    extra = 2 if (sign == "(" and f < 0) else (1 if f < 0 or sign in ("+", " ") else 0)
    suffix = 1 if (fmt.get("locale") or {}).get("symbol", ["", ""])[1] else 0
    return len(body) + extra + suffix


def column_widths(columns: List[dict], *record_sets: Iterable[dict], pad: int = 2, min_ch: int = 4,
                  skip: Iterable[str] = ()) -> List[dict]:
    """`style_cell_conditional` width rules (in ch) from the content of every record set,
    so two tables sharing `columns` line up column for column; `skip` names columns whose
    width the caller sets itself."""
    skip = set(skip)
    rules = []
    for col in columns:
        cid = col["id"]
        if cid in skip:
            continue
        fmt = col.get("format") if col.get("type") == "numeric" else None
        name = col.get("name", cid)
        longest = max([len(name if isinstance(name, str) else " ".join(name))]
                      + [display_length(r.get(cid), fmt) for rs in record_sets for r in rs])
        width = f"{max(min_ch, longest + pad)}ch"
        rules.append({"if": {"column_id": cid}, "width": width, "minWidth": width, "maxWidth": width})
    return rules


_FOOTER_COPIED = ("columns", "hidden_columns", "css", "style_table", "style_cell", "style_cell_conditional",
                  "style_data_conditional", "tooltip_delay", "tooltip_duration")


def with_footer(table: dash_table.DataTable, footer: List[dict], *, widths: bool = True,
                footer_style: Optional[List[dict]] = None, footer_tooltips: Optional[List[dict]] = None,
                skip_widths: Iterable[str] = ()) -> html.Div:
    """`table` with `footer` rows (totals, nets) pinned under it as a header-less second
    table that shares its columns, styles and widths, so they read as its last rows and
    never move when the first is ranked. With `widths`, both tables get `column_widths`
    from their combined content; pass `widths=False` when the caller fixes the widths."""
    props = {k: getattr(table, k) for k in _FOOTER_COPIED if getattr(table, k, None) is not None}
    body = list(getattr(table, "data", None) or [])
    if widths:
        rules = column_widths(props["columns"], body, footer, skip=skip_widths)
        props["style_cell_conditional"] = list(props.get("style_cell_conditional") or []) + rules
        table.style_cell_conditional = props["style_cell_conditional"]
    props["style_data_conditional"] = list(props.get("style_data_conditional") or []) + list(footer_style or [])
    table_id = getattr(table, "id", None)
    footer_table = dash_table.DataTable(
        **({"id": f"{table_id}-footer"} if table_id else {}),
        data=footer, **props,
        **({"tooltip_data": footer_tooltips} if footer_tooltips else {}),
        style_header={"display": "none"}, sort_action="none", page_action="none",
    )
    return html.Div(className="ranked-table", children=[table, footer_table])
