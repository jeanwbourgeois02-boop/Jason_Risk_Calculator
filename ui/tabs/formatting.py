"""Shared display-only formatting for every screen.

Display only: nothing here computes P&L, delta or a sum -- per CLAUDE.md ("ui/ ... never
recomputes P&L or delta itself"), storage/precision stay in engine/ and data/.

- `format_cell` / `format_frame`: whole units with separators, a negative with a real minus.
- `short_money`: a money figure in k / m / bn for summary screens ("1.65m").
- `about`: a section title whose definitions sit on hover of the title (screens redesign,
  user 2026-09-25: "never as a paragraph above the table").
- `marker`: a short visible marker ("excl. 3", "n/a", "filled 2") with its sentence on hover.
- `issues_drawer`: the tab's one collapsed "Data issues (N)" drawer of reasons.
- `tab_link`: a tab's name as a quiet link that switches to that tab (user, 2026-09-25);
  the one callback that switches is `ui/app.py`'s, keyed on `TAB_LINK_TYPE`.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from typing import Iterable, Optional, Tuple, Union

import pandas as pd
from dash import html

MINUS = "\u2212"          # a real minus sign, not a hyphen
INFO_MARK = "\u24d8"      # the small circled "i" beside a title with definitions on hover


def format_cell(value) -> str:
    """Format a single numeric cell: round to whole units, thousands separators, a negative
    with a real minus sign (U+2212, never parentheses: the display rule of 2026-09-28), blank
    ("") for NaN/None."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    rounded = round(float(value))
    if rounded < 0:
        return f"{MINUS}{abs(rounded):,}"
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


# ----------------------------------------------------------------------------- plain words
# The engine's own vocabulary (mark types, product codes, module and lane names, file paths)
# in a reason, a note or a hover, put into a trader's words on the screen (user, 2026-09-28:
# no engine words, file paths or identifiers on any screen). Applied in every chokepoint of
# the kit (`about`, `marker`, `missing_cell`, `issues_drawer`) and by the tabs on the hovers
# they build themselves. A phrase not listed passes through unchanged; the order matters (a
# longer phrase before the word inside it).
_PLAIN_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("no FUTURE_PX for ", "no price for "),
    ("no FWD_OUTRIGHT for ", "no forward price for "), ("no FWD_OUTRIGHT", "no forward price"),
    ("no SPOT for ", "no spot for "), ("no FUTURE_PX", "no price"),
    ("no PREMIUM mark", "no premium mark"), ("no PREMIUM", "no premium mark"),
    ("no DELTA mark", "no delta mark"), ("no GAMMA mark", "no gamma mark"), ("no THETA mark", "no theta mark"),
    ("no VEGA mark", "no vega mark"), ("no RHO mark", "no rho mark"),
    ("INTERP: FUTURE_PX of ", "estimated from the close of "),
    ("INTERP: FWD_OUTRIGHT from the ", "estimated from the "),
    ("INTERP: FWD_OUTRIGHT of ", "estimated from the forward of "),
    ("INTERP: SPOT of ", "estimated from the spot of "), ("INTERP: PREMIUM of ", "estimated from the premium of "),
    ("INTERP: ", "estimated: "),
    ("(QL_OPTIONS_PRICER)", "(the app's option pricer)"), ("QL_OPTIONS_PRICER", "the app's option pricer"),
    ("BBG_BFXFORWARD", "Bloomberg"), ("BBG_BDH", "Bloomberg history"), ("BBG_BDP", "Bloomberg live"),
    ("BBG_INTERP", "Bloomberg curve, interpolated"), ("CLOSE_OUT_FILL", "the closing fill"),
    ("spreads-engine's usd_per_unit", "the $ per unit"), ("spreads-engine gave no", "no"),
    ("spreads-engine could not group", "could not be grouped into a spread"),
    ("(spreads-engine)", ""), ("spreads-engine", "the spread rule"),
    ("(curve-positions' delta lots)", ""), ("curve-positions' delta lots", "the delta lots"),
    ("(curve-positions)", ""), ("curve-positions'", "the Exposure tab's"), ("curve-positions", "the Exposure tab"),
    ("(book-positions)", ""), ("as book-positions gives it", "as the book gives it"),
    ("book-positions gave nothing", "no currency figures"), ("book-positions", "the currency positions"),
    ("frozen by the ledger (realised_pnl)", "settled and frozen"), ("realised_pnl", "the settled ledger"),
    ("the ledger has frozen", "settled"), ("frozen by the ledger", "settled and frozen"),
    ("settled by the ledger", "settled"), ("the ledger froze", "settlement froze"),
    ("value_book's", "the valuation's"), ("(value_book)", ""), ("value_book", "the book's valuation"),
    ("data/sample/blotter_sample.csv", "the synthetic sample blotter"),
    ("FUT_LAST_TRADE_DT and FUT_NOTICE_FIRST", "Bloomberg's last-trade and first-notice dates"),
    ("CMDTY_OPTION", "option on future"), ("FX_OPTION", "FX option"), ("FX_FWD", "FX forward"),
    ("FX_SPOT", "FX spot"), ("LME_FWD", "LME forward"), ("EQ_OPTION", "listed option"),
    ("LME_CURVE", "LME curve"), ("FUTURE_PX", "futures price"), ("FWD_OUTRIGHT", "forward"),
    ("PREMIUM mark", "premium mark"), ("DELTA mark", "delta mark"), ("GAMMA mark", "gamma mark"),
    ("THETA mark", "theta mark"), ("VEGA mark", "vega mark"), ("RHO mark", "rho mark"),
    ("official DELTA", "official delta"), ("official GAMMA", "official gamma"), ("official THETA", "official theta"),
    ("official VEGA", "official vega"),
)


def plain_words(text) -> str:
    """`text` with the engine's vocabulary in a trader's words (`_PLAIN_PHRASES`, in order);
    None or '' -> ''; anything unlisted passes through unchanged. The Risk tab's own words
    (its limits-file mentions) are left to its own pass (`ui.tabs.risk.plain_reason`)."""
    s = "" if text is None else str(text)
    if not s:
        return s
    for engine, plain in _PLAIN_PHRASES:
        if engine in s:
            s = s.replace(engine, plain)
    return s.replace("  ", " ").replace(" .", ".").replace(" ,", ",")


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
    text = plain_words(text)
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
        return html.Span(short, className=classes, title=plain_words(reason))
    return html.Span(short, className=classes)


IssueItem = Union[str, Tuple[str, str]]


# ----------------------------------------------------------------------------- static blocks
# 2026-09-29 (performance): in Dash 4.4 every component a callback renders dispatches one redux
# action that runs every mounted component's selector, so a read-only table of 1,500 cells cost
# seconds in the browser. `static_block` sends such a block as ONE component: the same html
# tree written out as markup and drawn by `dcc.Markdown` (its html is parsed into plain React
# elements, never redux components). The tags, classes, hovers and styles are the kit's own, so
# the look is unchanged. Only plain `html.*` trees convert: anything with an id, a callback or a
# non-html component inside (a tab link, a chart, a dropdown) is returned as it was.
_STATIC_SKIP = {"children", "n_clicks", "n_clicks_timestamp", "disable_n_clicks", "key", "loading_state"}


def _jsx_text(value: str) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("{", "&#123;").replace("}", "&#125;").replace("\r", "").replace("\n", "&#10;"))


def _to_jsx(node, out: list) -> bool:
    if node is None or isinstance(node, bool):
        return True
    if isinstance(node, (str, int, float)):
        out.append(_jsx_text(node))
        return True
    if isinstance(node, (list, tuple)):
        return all(_to_jsx(x, out) for x in node)
    if getattr(node, "_namespace", None) != "dash_html_components":
        return False
    props = node.to_plotly_json().get("props") or {}
    if "id" in props:
        return False
    tag = str(node._type).lower()
    out.append("<" + tag)
    for k, v in props.items():
        if k in _STATIC_SKIP or v is None:
            continue
        if k == "style":
            if not isinstance(v, dict):
                return False
            out.append(" style={" + json.dumps(v) + "}")
        elif isinstance(v, bool):
            out.append(f" {k}" if v else f" {k}={{false}}")
        elif isinstance(v, (int, float)):
            out.append(f" {k}={{{json.dumps(v)}}}")
        elif isinstance(v, str):
            out.append(f' {k}="{_jsx_text(v)}"')
        else:
            return False
    children = props.get("children")
    if children is None or children == [] or children == "":
        out.append(" />")
        return True
    out.append(">")
    if not _to_jsx(children, out):
        return False
    out.append(f"</{tag}>")
    return True


def static_block(component, className: Optional[str] = None):
    """`component` (a plain `html.*` tree: a table, a list) as one `dcc.Markdown` drawing the same
    markup, or `component` unchanged when it holds an id or a non-html component."""
    out: list = []
    if component is None or not _to_jsx(component, out) or not out:
        return component
    from dash import dcc
    return dcc.Markdown("".join(out), dangerously_allow_html=True,
                        className=" ".join(c for c in ("static-block", className) if c))


_ROW_PARENTS = {"Tr", "Tbody", "Thead", "Tfoot", "Ul", "Ol"}
_KEEP_ROW_ORDER = {"Tbody", "Thead", "Tfoot"}   # zebra rows (nth-child): all rows in one block, or none


def compact(node):
    """A tab's rendered tree with its rows drawn as static blocks (2026-09-29, performance; see
    `static_block`): every table body, table head, list and table row whose children are all
    plain becomes that element holding ONE `dcc.Markdown` of those children; a row that holds a
    click target (the Book's names and chevrons, a tab link) keeps that cell as a component and
    the plain cells around it become static runs. The elements the kit's CSS targets (the table,
    its body, a row) stay where they were; a static block is `display: contents`, so the layout
    and the look are unchanged. Returns `node` (edited in place)."""
    if isinstance(node, (list, tuple)):
        return [compact(x) for x in node]
    if not hasattr(node, "_prop_names") or "children" not in getattr(node, "_prop_names", ()):
        return node
    ch = getattr(node, "children", None)
    if ch is None or isinstance(ch, (str, int, float)):
        return node
    kids = list(ch) if isinstance(ch, (list, tuple)) else [ch]
    html_node = getattr(node, "_namespace", None) == "dash_html_components"
    if html_node and node._type in _ROW_PARENTS and kids:
        parts: list = []
        if all(_to_jsx(k, parts) for k in kids):
            if len(kids) > 1 or not isinstance(kids[0], (str, int, float)):
                node.children = [_markdown("".join(parts))]
            return node
        if node._type not in _KEEP_ROW_ORDER:
            node.children = [compact(x) if not isinstance(x, _StaticMarker) else x.block
                             for x in _runs(kids)]
            return node
    node.children = compact(ch) if isinstance(ch, (list, tuple)) else compact(ch)
    return node


class _StaticMarker:
    def __init__(self, block):
        self.block = block


def _runs(kids: list) -> list:
    """`kids` with each run of plain ones folded into a `_StaticMarker` of one static block."""
    out: list = []
    run: list = []

    def flush():
        if run:
            parts: list = []
            for r in run:
                _to_jsx(r, parts)
            out.append(_StaticMarker(_markdown("".join(parts))))
            run.clear()
    for k in kids:
        if _to_jsx(k, []):
            run.append(k)
        else:
            flush()
            out.append(k)
    flush()
    return out


def _markdown(source: str):
    from dash import dcc
    return dcc.Markdown(source, dangerously_allow_html=True, className="static-block")


def static_runs(children: Iterable) -> list:
    """`children` (the rows of a list or a table body) with each run of plain rows drawn as one
    `static_block` and every row holding an id or a callback (a tab link) kept as it is, in order."""
    out: list = []
    run: list = []

    def flush() -> None:
        if run:
            parts: list = []
            out.append(static_block_of(run) if all(_to_jsx(r, parts) for r in run) else None)
            if out[-1] is None:
                out.pop()
                out.extend(run)
            run.clear()
    for child in children:
        if _to_jsx(child, []):
            run.append(child)
        else:
            flush()
            out.append(child)
    flush()
    return out


def static_block_of(rows: list, className: Optional[str] = None):
    """Several plain rows (list items, table rows) drawn as one `dcc.Markdown`, no element of its own
    around them."""
    parts: list = []
    for r in rows:
        _to_jsx(r, parts)
    from dash import dcc
    return dcc.Markdown("".join(parts), dangerously_allow_html=True,
                        className=" ".join(c for c in ("static-block", className) if c))


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
            rows.append(html.Li([html.Span(str(label), className="issue-label"), " ", plain_words(sentence)]
                                if label else plain_words(sentence)))
        elif isinstance(item, str):
            rows.append(html.Li(plain_words(item)))
        else:
            rows.append(html.Li(item))
    if not rows:
        return None
    extra = {"id": id} if id else {}
    return html.Details([html.Summary(f"{title} ({len(rows)})"), html.Ul(static_runs(rows), className="issues-list")],
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
    "curve", "pnl", "risk", "blotter", "market-data"; a deleted tab's key is ignored).

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


# ----------------------------------------------------------------------------- the display rules (2026-09-28)
# The rules every screen follows since the screens tidy (user, 2026-09-28; brief "Rules the whole
# UI follows"): a missing value is an em dash with its reason on hover (`missing_cell`), never
# "n/a"; a total sums the known figures and says "excl. N" (`sum_known`); a negative takes a real
# minus and a P&L or move a leading "+" (`signed_money`, `signed_number`); prices at tick
# precision (`price_text`); plain names (`contract_name`, `spread_name`, `fx_name`); sizes in
# words (`size_words`); an estimated date grey with a leading "≈" (`date_cell`).
MISSING = "—"        # em dash: a cell the engine could not give
# The groups the user made (a strategy label, a bundle, a pin): spreads-engine's `HAND_KINDS`, read
# here so the display kit needs no engine import; a strategy is named by its own name, never by
# its legs (2026-09-28).
HAND_KINDS = ("strategy", "bundle", "pinned")
ESTIMATED = "≈"      # before an estimated date
EN_DASH = "–"
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_CODES = "FGHJKMNQUVXZ"   # data.contracts.tickers.MONTH_CODES, index 0 = January


def missing_cell(reason: Optional[str] = None, className: str = ""):
    """The em dash of a value the engine could not give, grey, the reason on hover. Never the
    text "n/a", never a zero."""
    classes = " ".join(c for c in ("cell-missing", className) if c)
    return html.Span(MISSING, className=classes, title=plain_words(reason) or "not available")


def sum_known(values_with_reasons: Iterable) -> Tuple[Optional[float], int, list]:
    """(total, excluded_count, reasons) over (value, reason) pairs: the known figures summed
    (display only, the header's rule), how many were missing, and their reasons. `total` is
    None only when nothing is known."""
    total, excluded, reasons = None, 0, []
    for value, reason in values_with_reasons:
        try:
            f = float(value) if value is not None else float("nan")
        except (TypeError, ValueError):
            f = float("nan")
        if math.isnan(f):
            excluded += 1
            if reason:
                reasons.append(str(reason))
            continue
        total = f if total is None else total + f
    return total, excluded, reasons


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def signed_money(value, symbol: str = "") -> str:
    """'+15.3k' / '−39.4k' / '0' (short_money, a leading "+" on a gain): a P&L on a summary
    screen. None / NaN -> the em dash text."""
    if _is_missing(value):
        return MISSING
    text = short_money(value, symbol)
    return text if text.startswith(MINUS) or text in ("0", f"{symbol}0") else "+" + text


def signed_number(value, decimals: int = 2) -> str:
    """A move with its sign: '+4.39', '−1.41', '0.00' (a real minus sign)."""
    if _is_missing(value):
        return MISSING
    text = f"{abs(float(value)):,.{decimals}f}"
    if float(text.replace(",", "")) == 0:
        return text
    return (MINUS if value < 0 else "+") + text


def sign_class(value) -> str:
    """'cell-pos' / 'cell-neg' / '' by the sign of a P&L or move (green / red, never a
    parenthesis)."""
    if _is_missing(value):
        return ""
    f = float(value)
    if f == 0:
        return ""
    return "cell-neg" if f < 0 else "cell-pos"


def full_money(value, ccy: str = "USD") -> str:
    """'USD −6,928': the full figure behind a k / m cell."""
    return f"{ccy} {format_cell(value)}"


def money_cell(value, reason: Optional[str] = None, hover: Optional[str] = None, ccy: str = "USD",
               className: str = ""):
    """A summary money cell: k / m with its sign and colour, the full figure (and `hover`) on
    hover; the em dash with `reason` when missing."""
    if _is_missing(value):
        return missing_cell(reason, className)
    classes = " ".join(c for c in (sign_class(value), className) if c)
    title = "\n".join(t for t in (full_money(value, ccy), hover) if t)
    return html.Span(signed_money(value), className=classes or None, title=title)


# --- prices at tick precision
def decimals_of(value, cap: int = 6) -> int:
    """How many decimals a stored number carries (79637 -> 0, 432.25 -> 2, 3380.5 -> 1), at
    most `cap`; 0 for a non-number."""
    try:
        text = f"{float(value):.{cap}f}".rstrip("0")
    except (TypeError, ValueError):
        return 0
    return len(text.split(".")[1]) if "." in text else 0


def quoted_unit(root) -> str:
    """The unit a contract's price is quoted in, as the trader reads it: a cents sign per bushel
    for a USD contract quoted in cents (price_scale 0.01), 'p/therm' for pence, else the root's
    own quote unit ('USD/bbl', 'CNY/t', 'EUR/MWh'). '' without a root."""
    if root is None:
        return ""
    unit = str(getattr(root, "quote_unit", "") or "")
    scale = getattr(root, "price_scale", 1) or 1
    ccy, _sep, qty = unit.partition("/")
    qty = qty.replace("mwh", "MWh").replace("mmbtu", "MMBtu")
    if float(scale) == 0.01 and ccy.upper() == "USD":
        return f"¢/{qty}"
    if float(scale) == 0.01 and ccy.upper() == "GBP":
        return f"p/{qty}"
    return f"{ccy}/{qty}" if qty else unit


def is_fx_pair(text: str) -> bool:
    """'USDJPY', 'XAUUSD': six letters and no slash."""
    t = str(text or "")
    return len(t) == 6 and t.isalpha() and t.isupper()


def fx_pair_decimals(pair: str) -> int:
    """The decimals an FX pair's price is shown with: a JPY cross 3 (145.620), gold and silver
    2 (3,365.40), every other pair 4 (7.2737)."""
    p = str(pair or "").upper()
    if p.startswith(("XAU", "XAG")):
        return 2
    if "JPY" in p:
        return 3
    return 4


def price_decimals(unit: str = "", fill=None, floor: Optional[int] = None) -> int:
    """The decimals a price in `unit` is shown with: the tick's, roughly (lb 4, bu / gal 2 in
    cents, oz 1, bbl / MWh / therm 2, a tonne in CNY or USD 0; an FX pair passed as the unit
    by `fx_pair_decimals`, no unit 4), never fewer than the
    fill's own decimals (`decimals_of(fill)`, so a 2,588.6 fill keeps its .6), never more
    than 6."""
    ccy, _sep, qty = str(unit or "").partition("/")
    ccy, qty = ccy.strip().upper(), qty.strip().lower()
    cents = ccy in ("¢", "P")          # quoted in cents or pence: two decimals is the tick
    if floor is None:
        if not unit:
            floor = 4                        # a price with no unit known (an FX option premium)
        elif not qty and is_fx_pair(ccy):
            floor = fx_pair_decimals(ccy)    # 'USDJPY' 3, 'XAUUSD' 2, 'USDCNH' 4
        elif cents:
            floor = 2
        elif qty in ("lb",):
            floor = 4
        elif qty in ("bu", "gal", "bbl", "mwh", "therm", "st", "cwt", "kg"):
            floor = 2
        elif qty in ("oz", "g"):
            floor = 1
        elif qty in ("t",) or ccy in ("JPY", "KRW"):
            floor = 0
        else:
            floor = 2
    # The fill's own decimals are the fallback, never more than half a tick finer than the unit's
    # floor (a 2,588.6 fill on a USD/t contract keeps its .6; a lots-weighted average never
    # drags a price to six decimals).
    own = decimals_of(fill) if fill is not None else 0
    return min(6, max(floor, min(own, floor + 1)))


def price_text(value, unit: str = "", fill=None, decimals: Optional[int] = None) -> str:
    """A price at tick precision: 79,637 (CNY/t), 437.06 (cents per bushel), 3,353.7 (USD/oz),
    67.13 (USD/bbl), 7.2737 (FX, no unit). `unit` is the quoted unit (`quoted_unit`), `fill`
    the trade's own fill whose decimals are the floor. None / NaN -> the em dash text."""
    if _is_missing(value):
        return MISSING
    d = decimals if decimals is not None else price_decimals(unit, fill)
    text = f"{abs(float(value)):,.{d}f}"
    return (MINUS + text) if float(value) < 0 and float(text.replace(",", "")) != 0 else text


# --- plain names
# Short names for the contract roots the screens show most (the CSV names are Bloomberg-long:
# "NYMEX WTI light sweet crude"); anything else is `_short_from_name`. Display only.
SHORT_ROOT_NAMES = {
    "NYMEX:CL": "WTI", "ICE:B": "Brent", "NYMEX:RB": "RBOB", "NYMEX:HO": "Heating oil", "NYMEX:NG": "Henry Hub gas",
    "ICE:G": "Gasoil", "ICE:TFM": "TTF gas", "ICE:M": "NBP gas",
    "COMEX:HG": "COMEX copper", "COMEX:GC": "Gold", "COMEX:SI": "COMEX silver", "COMEX:ALI": "COMEX aluminium",
    "SHFE:CU": "SHFE copper", "OSE:JAU": "OSE gold", "OSE:JPL": "OSE platinum",
    "LME:CA": "LME copper", "LME:AH": "LME aluminium", "LME:ZS": "LME zinc", "LME:PB": "LME lead",
    "LME:NI": "LME nickel", "LME:SN": "LME tin",
    "CBOT:ZC": "Corn", "CBOT:ZS": "Soybeans", "CBOT:ZM": "Soybean meal", "CBOT:ZL": "Soybean oil",
    "CBOT:ZW": "Wheat", "CBOT:ZO": "Oats", "CBOT:ZR": "Rough rice", "MGEX:MWE": "Spring wheat",
    "DCE:I": "DCE iron ore", "SGX:FEF": "SGX iron ore", "SGX:M65F": "SGX 65% iron ore", "DCE:J": "DCE coke",
    "ICE:RC": "Robusta", "SGX:TF": "Rubber",
    "CME:GF": "Feeder cattle", "CME:LE": "Live cattle", "CME:HRC": "HRC", "SHFE:ZN": "SHFE zinc",
    "SHFE:AG": "SHFE silver", "SGX:XUC": "USD/CNH",
}
_KEEP_EXCHANGE = {"SHFE", "DCE", "ZCE", "INE", "GFEX", "LME", "SGX", "OSE", "COMEX"}


def _short_from_name(root) -> str:
    """A short name from a root's CSV name: cut at the first parenthesis, the exchange word
    dropped unless it tells venues apart (Chinese exchanges, LME, SGX, OSE, COMEX), at most
    three words."""
    name = str(getattr(root, "name", "") or getattr(root, "root_id", "") or "").split(" (")[0].strip()
    exchange = str(getattr(root, "exchange", "") or "")
    words = name.split()
    if words and exchange and words[0].upper() == exchange.upper() and exchange.upper() not in _KEEP_EXCHANGE:
        words = words[1:]
    text = " ".join(words[:3])
    return text[:1].upper() + text[1:] if text else str(getattr(root, "root_id", "") or "")


def short_root_name(root, root_id: str = "") -> str:
    """'WTI', 'SHFE copper', 'Corn': the plain short name of a contract root (a `ContractRoot`,
    or None with its `root_id`)."""
    rid = str(getattr(root, "root_id", "") or root_id or "")
    if rid in SHORT_ROOT_NAMES:
        return SHORT_ROOT_NAMES[rid]
    if root is None:
        return rid.split(":", 1)[-1] or rid
    return _short_from_name(root)


def month_label(month: int, year: int) -> str:
    """'Dec26' from (12, 2026) or (12, 26)."""
    return f"{_MONTH_ABBR[int(month) - 1]}{int(year) % 100:02d}"


def parse_contract_id(instrument_id: str) -> Optional[dict]:
    """{code, month, year, option_type, strike} from a canonical contract or option id
    ('CLZ26 Comdty', 'C Z26 Comdty', 'CLZ26C 75 Comdty', 'CUZ26C 80000 Comdty'); None when the
    id is not one (an FX pair, an LME root)."""
    text = str(instrument_id or "").strip()
    for key in (" Comdty", " Index", " Curncy"):
        if text.endswith(key):
            text = text[: -len(key)]
    m = re.match(r"^([A-Z][A-Z0-9 ]*?)\s?([FGHJKMNQUVXZ])(\d{2})\s*([CP])?\s*([\d.]+)?$", text)
    if not m or (m.group(4) is None) != (m.group(5) is None):
        return None
    code, mcode, yy, opt, strike = m.groups()
    return {"code": code.strip(), "month": _MONTH_CODES.index(mcode) + 1, "year": 2000 + int(yy),
            "option_type": {"C": "call", "P": "put"}.get(opt or "", ""), "strike": strike or ""}


def strike_text(strike) -> str:
    text = str(strike or "")
    try:
        f = float(text)
    except ValueError:
        return text
    return f"{f:,.0f}" if f == int(f) else f"{f:,g}"


def contract_name(instrument_id: str, root=None, root_id: str = "") -> str:
    """'WTI Dec26' for a future, 'WTI Dec26 75c' for an option on it (the strike and c / p);
    the id without its yellow key when it does not parse."""
    parsed = parse_contract_id(instrument_id)
    base = short_root_name(root, root_id or (parsed or {}).get("code", ""))
    if parsed is None:
        text = str(instrument_id or "")
        return text[:-len(" Comdty")] if text.endswith(" Comdty") else text
    name = f"{base} {month_label(parsed['month'], parsed['year'])}"
    if parsed["option_type"]:
        name += f" {strike_text(parsed['strike'])}{parsed['option_type'][0]}"
    return name


# Short names for the processing templates (config/spreads/, family "processing"): a crack, a
# crush or a margin is named by what it is, not by its legs joined. Anything else in that family
# takes its template name up to the first parenthesis. Display only.
SHORT_TEMPLATE_NAMES = {
    "proc.us.crack_321": "3-2-1 crack", "proc.us.crack_211": "2-1-1 crack",
    "proc.us.rbob_crack_wti": "RBOB crack", "proc.us.ho_crack_wti": "Heating oil crack",
    "proc.atl.rbob_crack_brent": "RBOB–Brent crack", "proc.atl.ho_crack_brent": "Heating oil–Brent crack",
    "proc.eu.gasoil_crack_brent": "Gasoil crack", "proc.eu.eurobob_crack_brent": "Eurobob crack",
    "proc.eu.naphtha_crack_brent": "Naphtha crack", "proc.eu.hsfo_crack_brent": "HSFO crack",
    "proc.eu.vlsfo_crack_brent": "VLSFO crack", "proc.sg.gasoil_crack_dubai": "Sing gasoil crack",
    "proc.sg.jet_crack_dubai": "Jet crack", "proc.sg.mogas92_crack_brent": "Mogas 92 crack",
    "proc.sg.naphtha_crack_brent": "MOPJ crack", "proc.sg.hsfo380_crack_dubai": "Sing 380 crack",
    "proc.sg.vlsfo_crack_dubai": "Sing VLSFO crack", "proc.cn.fu_crack_sc": "SHFE fuel oil crack",
    "proc.cn.lu_crack_sc": "INE LSFO crack", "proc.cn.bu_crack_sc": "Bitumen crack",
    "proc.us.board_crush": "Board crush", "proc.cn.dalian_crush": "Dalian crush",
    "proc.cn.import_crush_cbot": "Import crush", "proc.cn.canola_import_crush": "Canola import crush",
    "proc.us.cattle_crush": "Cattle crush", "proc.cn.coking_margin": "Coking margin",
}


def short_template_name(template_id: str, name: str = "") -> str:
    """'3-2-1 crack' for proc.us.crack_321; else the template's name up to its first parenthesis."""
    if template_id in SHORT_TEMPLATE_NAMES:
        return SHORT_TEMPLATE_NAMES[template_id]
    text = str(name or template_id or "").split(" (")[0].strip()
    return text or str(template_id or "spread")


def spread_name(position: dict, roots: Optional[dict] = None) -> str:
    """A spread position's plain name: a calendar 'WTI Dec26/Jan27' (one root, the two months),
    a processing template by its own short name and month ('3-2-1 crack Nov26', 'Board crush
    Dec26'; `short_template_name`), any other template 'Brent–WTI Dec26' (the legs' short names
    joined by an en dash, the first leg's month), a bundle by its own name."""
    roots = roots or {}
    legs = [leg for leg in (position.get("legs") or position.get("level_legs") or []) if leg.get("instrument_id")]
    parsed = [(leg, parse_contract_id(leg.get("instrument_id"))) for leg in legs]
    kind = str(position.get("kind") or "")
    if kind == "calendar" and len(parsed) >= 2 and all(p for _l, p in parsed):
        root_id = str(legs[0].get("root_id") or "")
        months = sorted({(p["year"], p["month"]) for _l, p in parsed})
        return f"{short_root_name(roots.get(root_id), root_id)} " + "/".join(month_label(m, y) for y, m in months)
    template = str(position.get("template") or "")
    if str(position.get("family") or "") == "processing" or template.startswith("proc."):
        first = parsed[0][1] if parsed else None
        month = f" {month_label(first['month'], first['year'])}" if first else ""
        return short_template_name(template or kind, str(position.get("name") or "")) + month
    if legs and kind not in HAND_KINDS:
        seen = []
        for leg in legs:
            rid = str(leg.get("root_id") or "")
            name = short_root_name(roots.get(rid), rid)
            if name not in seen:
                seen.append(name)
        first = parsed[0][1]
        month = f" {month_label(first['month'], first['year'])}" if first else ""
        return EN_DASH.join(seen) + month
    return str(position.get("name") or position.get("position_id") or "spread")


def short_date(iso: Optional[str]) -> str:
    """'18 Nov' from '2026-11-18'; the text as it is when it is not a date."""
    try:
        d = dt.date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso or "")
    return f"{d.day} {_MONTH_ABBR[d.month - 1]}"


def fx_name(pair: str, product: str, settle_date: Optional[str] = None, option_type: str = "",
            strike=None) -> str:
    """'USDCNH 18 Nov forward', 'EURUSD spot', 'USDJPY 16 Dec 155 put'."""
    pair = str(pair or "")
    if product == "FX_OPTION":
        what = " ".join(x for x in (strike_text(strike) if strike else "", (option_type or "option").lower()) if x)
        return f"{pair} {short_date(settle_date)} {what}".strip()
    if product == "FX_SPOT":
        return f"{pair} spot"
    return f"{pair} {short_date(settle_date)} forward".strip()


def lme_name(root, root_id: str, prompt: Optional[str]) -> str:
    """'LME copper 10 Dec'."""
    return f"{short_root_name(root, root_id)} {short_date(prompt)}".strip()


# --- trade types and strategies (2026-09-28: Jason's broker labels each trade with a strategy
# name, COPAR3, and a trade type, cross exchange / cross product / term structure; spreads-engine
# carries the codes, the words are the screens')
TRADE_TYPE_WORDS = {"CROSS_EXCHANGE": "cross exchange", "CROSS_PRODUCT": "cross product",
                    "TERM_STRUCTURE": "term structure"}
TRADE_TYPE_TITLES = {"CROSS_EXCHANGE": "Cross exchange", "CROSS_PRODUCT": "Cross product",
                     "TERM_STRUCTURE": "Term structure"}
NO_TYPE_REASON = "no type: outright"
SOURCE_INFERRED, SOURCE_MIXED = "inferred", "mixed labels"


def trade_type_words(code) -> str:
    """'cross exchange' / 'cross product' / 'term structure' for spreads-engine's codes; '' for
    no type (an outright) or an unknown code."""
    return TRADE_TYPE_WORDS.get(str(code or "").upper(), "")


def type_disagrees(type_source: str = "", type_note: str = "") -> bool:
    """True when the labels disagree among themselves (`mixed labels`) or the note says the legs
    look like another type than the label ('labelled ...; the legs look like ...')."""
    note = str(type_note or "").lower()
    return str(type_source or "") == SOURCE_MIXED or ("labelled" in note and "look like" in note)


def type_cell(trade_type, type_source: str = "", type_note: str = "", className: str = ""):
    """A Type cell: the words ('cross exchange'), a small grey 'inferred' after them when the type
    was read from the legs rather than the broker's label, an amber 'check' marker when the labels
    disagree or the legs look like another type, the engine's note on hover. No type: the em dash
    with 'no type: outright' (and the note) on hover; mixed labels with no type: the amber marker
    alone."""
    words = trade_type_words(trade_type)
    note = str(type_note or "")
    source = str(type_source or "")
    if not words:
        if source == SOURCE_MIXED:
            return html.Span(html.Span("mixed", className="marker marker--amber",
                                       title=note or "the broker's labels on the legs disagree"),
                             className=className or None)
        return missing_cell("\n".join(t for t in (NO_TYPE_REASON, note) if t), className)
    children = [words]
    if source == SOURCE_INFERRED:
        children.append(html.Span("inferred", className="cell-unit",
                                  title="read from the legs (no broker label on these trades)"))
    if type_disagrees(source, note):
        children.append(html.Span("check", className="marker marker--amber",
                                  title=note or "the labels disagree"))
    return html.Span(children, className=className or None, title=note or f"{words}: the broker's label")


# --- sizes in words
def amount_words(value: float, ccy: str = "") -> str:
    """'2.0m USD', '250k EUR', '100 oz', '5,000 bbl': a size with its unit, k / m for money."""
    v = abs(float(value))
    if ccy and len(ccy) == 3 and ccy.isupper() and v >= 1000:
        if v >= 1e6:
            body = f"{v / 1e6:.1f}m"
        else:
            body = f"{v / 1e3:.0f}k" if v == round(v / 1e3) * 1e3 else f"{v / 1e3:.1f}k"
    else:
        body = f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{body} {ccy}".strip()


def size_words(quantity, unit: str = "lots", ccy: str = "") -> str:
    """'long 15 lots', 'short 30 lots', 'long 5,000 bbl', 'long 100 t', 'long 2.0m USD', 'short
    1 lot': the sign as a word, never a signed number."""
    if _is_missing(quantity):
        return MISSING
    q = float(quantity)
    if q == 0:
        return "flat"
    side = "long" if q >= 0 else "short"
    if ccy:
        return f"{side} {amount_words(q, ccy)}"
    n = abs(q)
    body = f"{n:,.2f}".rstrip("0").rstrip(".")
    u = "lot" if unit == "lots" and n == 1 else unit
    return f"{side} {body} {u}".strip()


# --- dates
def estimated_hover(business_days: Optional[int] = None, alert_date: Optional[str] = None, level: str = "",
                    hover: str = "") -> str:
    """The hover of an estimated Next / In / Level cell: 'alert counted from 1 Oct while the dates
    are estimated: 3 bd; level RED by the engine', then `hover`."""
    lvl = str(level or "").upper()
    parts = []
    if business_days is not None:
        count = "today" if business_days == 0 else (f"{business_days} bd" if business_days > 0 else f"{abs(business_days)} bd ago")
        frm = f"from {short_date(alert_date)} " if alert_date else ""
        parts.append(f"alert counted {frm}while the dates are estimated: {count}")
    else:
        parts.append("the dates are estimated (contract-master's, not Bloomberg's)")
    if lvl:
        parts.append(f"level {lvl} by the engine")
    return "\n".join(t for t in ("; ".join(parts), hover) if t)


def date_cell(iso: Optional[str], business_days: Optional[int] = None, estimated: bool = False,
              level: str = "", hover: str = "", prefix: str = "", alert_date: Optional[str] = None):
    """A Next / date cell: '18 Nov · 37 bd' plain for a real date, coloured by `level` only when
    it is RED or AMBER; grey '≈ 30 Nov' (the date alone) for an estimated one, with the count
    to the alert, the alert date and the engine's level on hover (`estimated_hover`), never a
    colour. None / '' -> the em dash with `hover` as its reason. Display only: the engine's
    level is unchanged."""
    if not iso:
        return missing_cell(hover or "no date")
    words = short_date(iso)
    lvl = str(level or "").upper()
    if estimated:
        text = f"{ESTIMATED} {f'{prefix} ' if prefix else ''}{words}"
        return html.Span(text, className="cell-estimated", title=estimated_hover(business_days, alert_date, lvl, hover))
    if business_days is not None:
        words += " · " + ("today" if business_days == 0 else f"{business_days} bd")
    if prefix:
        words = f"{prefix} {words}"
    cls = {"RED": "cell-red", "AMBER": "cell-amber", "EXPIRED": "cell-red"}.get(lvl, "")
    title = "\n".join(t for t in (lvl.lower() if lvl in ("RED", "AMBER", "EXPIRED") else "", hover) if t)
    return html.Span(words, className=cls or None, title=title or None)


def unit_suffix(unit: str):
    """The small grey unit after a Now cell ('6.03 USD/bbl'); None for no unit."""
    return html.Span(unit, className="cell-unit") if unit else None
