"""Shared display-only formatting for every screen.

Display only: nothing here computes P&L, delta or a sum -- per CLAUDE.md ("ui/ ... never
recomputes P&L or delta itself"), storage/precision stay in engine/ and data/.

- `format_cell` / `format_frame`: whole units with separators, negatives in parentheses.
- `short_money`: a money figure in k / m / bn for summary screens ("1.65m").
- `about`: a section title whose definitions sit on hover of the title (screens redesign,
  user 2026-09-25: "never as a paragraph above the table").
- `marker`: a short visible marker ("excl. 3", "n/a", "filled 2") with its sentence on hover.
- `issues_drawer`: the tab's one collapsed "Data issues (N)" drawer of reasons.
- `tab_link`: a tab's name as a quiet link that switches to that tab (user, 2026-09-25);
  the one callback that switches is `ui/app.py`'s, keyed on `TAB_LINK_TYPE`.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple, Union

import pandas as pd
from dash import html

MINUS = "\u2212"          # a real minus sign, not a hyphen
INFO_MARK = "\u24d8"      # the small circled "i" beside a title with definitions on hover


def format_cell(value) -> str:
    """Format a single numeric cell: round to whole units, thousands separators,
    negatives in parentheses, blank ("") for NaN/None."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    rounded = round(float(value))
    if rounded < 0:
        return f"({abs(rounded):,})"
    return f"{rounded:,}"


def format_frame(df: pd.DataFrame, label_col: str = "ccy") -> pd.DataFrame:
    """Apply `format_cell` to every column except `label_col` (the non-numeric row
    label, e.g. `ccy` or `settle_date`). Returns a new DataFrame of strings so it can be
    unit-tested without Dash."""
    out = df.copy()
    for col in out.columns:
        if col == label_col:
            continue
        out[col] = out[col].map(format_cell)
    return out


# ----------------------------------------------------------------------------- short money
_SHORT_SUFFIXES = {3: "k", 6: "m", 9: "bn"}


def sig_digits(magnitude: float, digits: int = 3) -> Tuple[str, int]:
    """`magnitude` (>= 1, already whole) to `digits` significant figures, as the printed
    mantissa for its thousands group and that group's power (0, 3, 6, 9): 51018 ->
    ("51.0", 3), 1650590 -> ("1.65", 6), 999600 -> ("1.00", 6). The rounding is done
    before the group is chosen, so 999,600 reads 1.00m, never 1000k. Groups stop at 9."""
    mantissa, exp = f"{magnitude:.{digits - 1}e}".split("e")
    exp = int(exp)
    group = min(9, max(0, (exp // 3) * 3))
    scaled = float(mantissa) * 10 ** (exp - group)
    decimals = max(0, digits - 1 - (exp - group))
    return f"{scaled:,.{decimals}f}", group


def short_money(value, symbol: str = "", parens: bool = False) -> str:
    """A money figure for a summary screen in k / m / bn to about 3 significant figures:
    51,018 -> "51.0k", 1,650,590 -> "1.65m", 395 -> "395", 0 -> "0", 2.4e9 -> "2.40bn".
    Rounded to whole units first, so float noise never prints. None / NaN -> "" (the caller
    shows the reason); a string that is not a number is returned as it is. A negative takes a
    real minus sign ("-$51.0k" with U+2212), or parentheses with `parens` ("($51.0k)"); the
    `symbol` sits after the sign. Trade rows keep full figures (`format_cell`)."""
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        try:
            value = float(stripped.replace(",", ""))
        except ValueError:
            return value
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(f):
        return ""
    if math.isinf(f):
        return (MINUS if f < 0 else "") + symbol + "inf"
    whole = abs(round(f))
    negative = round(f) < 0
    if whole < 1000:
        body = f"{whole:d}"
    else:
        mantissa, group = sig_digits(float(whole))
        body = mantissa + _SHORT_SUFFIXES[group]
    text = symbol + body
    if not negative:
        return text
    return f"({text})" if parens else MINUS + text


# ----------------------------------------------------------------------------- hover helpers
_HEADINGS = {"h1": html.H1, "h2": html.H2, "h3": html.H3, "h4": html.H4, "h5": html.H5, "h6": html.H6,
             "div": html.Div, "span": html.Span}


def about(title, text: Optional[str] = None, level: str = "h4", className: str = "", **props):
    """A section title with its definitions on hover: the heading `level` ("h3", "h4",
    "h5", "div", ...) holding `title` and a small info mark, both carrying `text` as their
    native `title` attribute. No paragraph under the heading. With no `text` it is the plain
    heading (nothing to hover). Extra `props` (an `id`, `style`) go on the heading."""
    tag = _HEADINGS[level.lower()]
    classes = " ".join(c for c in ("about-title", className) if c)
    if not text:
        return tag(title, className=classes, **props)
    return tag([title, html.Span(INFO_MARK, className="about-mark", title=text)],
               className=classes, title=text, **props)


def marker(short: str, reason: Optional[str] = None, className: str = ""):
    """A short visible marker with its full sentence on hover: `marker("excl. 3", "3 of 12
    trades unpriced: ...")` -> a small muted span. Returns None when `short` is empty, so a
    caller can place it unconditionally."""
    if not short:
        return None
    classes = " ".join(c for c in ("marker", className) if c)
    if reason:
        return html.Span(short, className=classes, title=reason)
    return html.Span(short, className=classes)


IssueItem = Union[str, Tuple[str, str]]


def issues_drawer(items: Optional[Iterable[IssueItem]], title: str = "Data issues", open: bool = False,
                  id: Optional[str] = None):
    """The tab's one collapsed drawer of reasons: an `html.Details` whose summary reads
    "Data issues (N)" and whose body lists the items compactly. An item is a sentence, a
    (label, sentence) pair (label in bold, e.g. the trade id or contract), or a Dash
    component shown as it is; empty items are dropped. Returns None when nothing is left,
    so a tab with no issues shows no drawer."""
    rows = []
    for item in items or ():
        if item is None or (isinstance(item, str) and not item.strip()):
            continue
        if isinstance(item, tuple) and len(item) == 2:
            label, sentence = item
            if not label and not sentence:
                continue
            rows.append(html.Li([html.Span(str(label), className="issue-label"), " ", str(sentence or "")]
                                if label else str(sentence)))
        elif isinstance(item, str):
            rows.append(html.Li(item))
        else:
            rows.append(html.Li(item))
    if not rows:
        return None
    extra = {"id": id} if id else {}
    return html.Details([html.Summary(f"{title} ({len(rows)})"), html.Ul(rows, className="issues-list")],
                        className="issues-drawer", open=open, **extra)


# ----------------------------------------------------------------------------- tab links
# A tab's name written on another screen ("-> Curve", "on the Risk tab") as a link that
# switches the tab bar to it (user, 2026-09-25: "make tab names in the screens clickable
# links"). The link is a Dash pattern-matching id, so it works inside a body a callback
# renders later; `ui/app.py` holds the one callback, on every id of type `TAB_LINK_TYPE`,
# that sets `main-tabs` to the link's `tab` (a stable key of `ui.app.TAB_KEYS`, e.g. "curve",
# "ladder", "market-data"; never the label). An unknown key does nothing when clicked.
TAB_LINK_TYPE = "tab-link"


def tab_link_id(tab_key: str, idx: str) -> dict:
    """The pattern-matching id of a tab link: {"type": "tab-link", "tab": <key>, "idx": <idx>}."""
    return {"type": TAB_LINK_TYPE, "tab": str(tab_key), "idx": str(idx)}


def tab_link(label, tab_key: str, idx: str, title: Optional[str] = None, className: str = ""):
    """A small, quiet link that switches to the tab `tab_key` when clicked:
    `tab_link("\u2192 Curve", "curve", "book-metals")`. `label` is shown as it is (text or
    components); `tab_key` is the tab's stable key (`ui.app.TAB_KEYS` values: "book",
    "spreads", "curve", "risk", "expiries", "blotter", "ladder", "market-data").

    The `idx` rule: the ids of all links on the page must differ, so `idx` names the spot
    the link sits in, as "<the tab it sits on>-<the spot>" ("book-metals", "risk-caption"),
    with a row's own key added when the link repeats per row ("book-spread-CLZ6-CLH7"). Two
    links to the same tab on one page never share an `idx`; links to different tabs may.

    Rendered as a `type="button"` element (keyboard-focusable, no URL change), styled as a
    link by `.tab-link` in `ui/assets/style.css`; its hover says which tab it opens unless
    `title` is given."""
    classes = " ".join(c for c in ("tab-link", className) if c)
    if title:
        hover = title
    else:
        # "-> Curve" hovers "Open the Curve tab": the leading arrow or symbol is not the name.
        name = label.lstrip(" \u2192\u2190>-:") if isinstance(label, str) else ""
        hover = f"Open the {name or tab_key} tab"
    return html.Button(label, id=tab_link_id(tab_key, idx), n_clicks=0, type="button",
                       className=classes, title=hover)
