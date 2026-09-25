"""Spreads tab: "Where is my relative-value P&L, and where do my spreads stand?" (Commodity
conversion plan, Phase 3; rebuilt around the spread's level in Phase B of the Screens redesign
plan, 2026-09-25). Rendered from `engine.spreads.book_spreads` and, beside it, the research app's
statistics read by `engine.risk.research_spreads`, and nothing else: every grouping, level, P&L,
reference close, statistic and leftover on the tab is theirs; nothing here regroups a leg,
re-prices a trade, re-reads a mark or recomputes a level or a statistic (CLAUDE.md "Tabs as
views").

The engine is called with the screens' shared filled reader
(`ui.tabs.blotter_pricing.priced_value_book`) as its `value_fn`, so a trade with no price on a
date takes the value of its last earlier close exactly as the header does ("The fill").

Layout, top to bottom (`body`; definitions on hover of the titles, reasons gathered in one
collapsed "Data issues (N)" drawer):
  1. `caption_block`: one line, the as-of, the counts and the research run, then the Data
     issues drawer (`issues_items`).
  2. `positions_section`: one row per open position (`book_spreads(...)["positions"]`: the same
     spread put on over several trade dates is one row), grouped by family (calendars,
     benchmarks / arbs, cracks and processing, substitution, then the spreads grouped by hand),
     largest |LTD| first within a family. Columns in trader order: family, spread, legs, size
     with its direction and unit; the level's unit, entry, now and today's change (text in the
     unit's own decimals, `level_text`; the sources on hover); the research columns (today's
     move in sigma from `sigma_move`, the z-score, the 5-year percentile, the half-life), under
     a "Research" header naming the run, "context only, not a mark" on hover; USD per 1.0 move
     of the level; Daily, MTD and LTD P&L in k / m (5d on Daily's hover, YTD on LTD's), a
     "P&L note" cell with "excl. N" when the engine left entries out; the leftover outright. A
     |sigma| or |z| of 2 or more is shaded quietly. A Total line is pinned under it. Click a row
     and `detail_panel` opens under the table: the research history of the spread with the
     position's entry as a line and today's level as a marker, the member entries and the legs
     with their prices as quoted.
  3. `closed_section`: the closed spreads, their table and legs, collapsed.
  4. `outrights_section`: the futures trades in no spread (trade rows: full figures).
  5. `review_section`: a collapsed "For review (N)" drawer.

A figure the engine or the research reader gives as None reads "n/a" (it ranks last) with the
reason on hover and in the drawer, never 0 and never blank without a reason. Every table ranks
(`ui.tabs.ranking`); a Total line never takes part in a ranking. The only arithmetic here is the
display sums of the Total lines and of a leftover's USD over its roots, and display rounding.

The tab has no date picker: it follows the header's as-of store and re-renders in place on the
data revision and on its safety interval; the position opened in the drill-down is kept in
`SELECTED_STORE_ID` across those re-renders. `layout(default_date)` (alias `build_layout`) and
`register_callbacks(app, get_db_path)` are the shell's interface.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dash import Input, Output, State, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Sign
from dash.exceptions import PreventUpdate

from engine.risk.research_spreads import research_spread_history, research_spread_stats, sigma_move
from engine.spreads import book_spreads
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import about, issues_drawer, marker, short_money
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "spreads-body"
REFRESH_ID = "spreads-refresh"
TABLE_ID = "spreads-table"                   # the open positions (the open spreads until 2026-09-25)
SELECTED_STORE_ID = "spreads-selected"       # the position open in the drill-down (static, in the layout)
DETAIL_STORE_ID = "spreads-detail-store"     # each open position's drill-down data (in the body)
DETAIL_ID = "spreads-detail"                 # the drill-down panel under the positions table
DETAIL_GRAPH_ID = "spreads-detail-graph"
DETAIL_MEMBERS_ID = "spreads-detail-members"
DETAIL_LEGS_ID = "spreads-detail-legs"
CLOSED_ID = "spreads-closed"
CLOSED_TABLE_ID = "spreads-closed-table"
CLOSED_LEGS_ID = "spreads-closed-legs"
OUTRIGHTS_ID = "spreads-outrights"
OUTRIGHTS_TABLE_ID = "spreads-outrights-table"
REVIEW_ID = "spreads-review"
REVIEW_TABLE_ID = "spreads-review-table"     # the review drawer's list of groups (a table until 2026-09-25)
ISSUES_ID = "spreads-issues"

NA = "n/a"
MINUS = "−"
PERIODS = ("ltd", "daily", "d5", "mtd", "ytd")          # the engine's period keys
PERIOD_TITLES = {"ltd": "LTD", "daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD"}
SHOWN_PERIODS = ("daily", "mtd", "ltd")                  # the positions table's P&L columns
HOVER_PERIOD = {"daily": "d5", "ltd": "ytd"}             # the other two, on hover of these
TOTAL_LABEL = "Total"
KIND_LABELS = {"calendar": "Calendar", "bundle": "Bundle (by hand)", "pinned": "Pinned (by hand)"}
REVIEW_LABELS = {"ambiguous": "Two ways to group", "ratio_off": "Ratio off", "accounts": "Two accounts"}
BUNDLE_HINT = ("To group any of them, put their trades in one bundle on the Blotter's Bundles sub-tab: "
               "a bundle is always taken as one spread.")
# The positions table's groups, in this order; a family not listed comes after them, before the
# spreads grouped by hand.
FAMILY_ORDER = ("calendar", "benchmark", "processing", "substitution")
FAMILY_LABELS = {"calendar": "Calendar", "benchmark": "Benchmark / arb", "processing": "Crack / processing",
                 "substitution": "Substitution", "bundle": "Bundle", "pinned": "Pinned"}
HIGHLIGHT = 2.0                               # |sigma| and |z| at or above it are shaded
RESEARCH_TAG = "research, context only, not a mark"

TITLE_ABOUT = ("The book's spreads as spreads-engine finds them, at the header's as-of date, with the research "
               "app's statistics beside them. P&L in USD is the legs' own valuation added up (each leg converted "
               "at its day's spot), over the header's periods and reference closes; a level is the spread's price "
               "in its own unit from the fills and the official marks, by the research app's formula. n/a is a "
               "figure that could not be given, its reason on hover. A Total line adds up the known figures only "
               "and says what it leaves out.")
POSITIONS_ABOUT = ("One row per open spread position: the same spread put on over several trade dates is one "
                   "row, its entry the entries weighted by size. Grouped by family, largest |LTD| first. "
                   "Levels in the spread's own unit; USD figures in k / m, the full figure on hover. The research "
                   "columns are the research app's statistics of the same spread: context, never a mark. Click a "
                   "row for its research history, its entries and its legs.")
DETAIL_ABOUT = ("The research app's daily history of this spread in its unit (the research app's prices, not "
                "the book's marks), with the position's entry level as a dashed line and today's level (the "
                "official marks) as a marker; then its entries and its legs with their prices as quoted.")
LEGS_ABOUT = ("Each spread's contracts with their lots and the P&L spreads-engine added up, in the table's "
              "order: click a spread to open it.")
CLOSED_ABOUT = ("Spreads whose every leg has settled or been closed: their P&L is frozen, listed so none "
                "disappears unseen.")
OUTRIGHTS_ABOUT = "The futures trades in no spread, each with its period P&L and why spreads-engine left it outright."
REVIEW_ABOUT = ("Groups spreads-engine would not decide, never guessed: two ways to group the same trades, "
                "a ratio outside 5 %, or legs on two accounts. Their trades stay under Outrights. "
                "Hover a line for the full reason.")
NO_RESEARCH = "the research statistics were not read"

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_TOTAL_STYLE = {"fontWeight": "700", "borderTop": "2px solid #1f2933"}
_RESEARCH_BG = {"backgroundColor": "rgba(100, 116, 139, 0.06)"}
_HIGHLIGHT_STYLE = {"backgroundColor": "var(--warn-bg)", "color": "var(--warn)", "fontWeight": "600"}
_GROUP_START = {"borderTop": "2px solid var(--line)"}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%A} {d.day} {d:%B %Y} ({iso})"


def signed(v: Optional[float]) -> str:
    """+10 / -10 (a true minus sign) / 0, as few decimals as the number needs."""
    if v is None:
        return NA
    if v == 0:
        return "0"
    return f"{v:+,g}".replace("-", MINUS)


def plain(v: float) -> str:
    """5,000 / 96.45 / -2.5 (a true minus sign): thousands separators, at most 2 decimals, trimmed."""
    text = f"{abs(v):,.2f}".rstrip("0").rstrip(".")
    return (MINUS + text) if v < 0 and text != "0" else text


def full_usd(v: float) -> str:
    """The full figure behind a k / m cell: 'USD -6,928' (a true minus sign)."""
    return f"USD {v:,.0f}".replace("-", MINUS)


def size_text(size: float, unit: str) -> str:
    """'10 lots', '5,000 bbl', '96.45 oz': the engine's size with its unit in one cell."""
    return f"{plain(size)} {unit}".strip()


def contract_label(instrument_id: Optional[str]) -> str:
    """'CLZ26 Comdty' -> 'CLZ26': the instrument id without its Bloomberg yellow key."""
    s = str(instrument_id or "")
    for key in (" Comdty", " Curncy", " Index"):
        if s.endswith(key):
            return s[: -len(key)]
    return s


def root_label(root_id: Optional[str]) -> str:
    """'NYMEX:CL' -> 'CL'."""
    return str(root_id or "").split(":")[-1]


def kind_label(spread: Dict[str, Any]) -> str:
    """Calendar, Bundle (by hand), Pinned (by hand), or a template's family and id."""
    kind = spread.get("kind") or ""
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    family = spread.get("family") or ""
    template = spread.get("template") or kind
    return f"{family.capitalize()}: {template}" if family else (template or NA)


def legs_summary(legs: Sequence[Dict[str, Any]]) -> str:
    """'CLZ26 +10 / CLF27 -10', the engine's legs in its order."""
    return " / ".join(f"{contract_label(leg.get('instrument_id'))} {signed(_num(leg.get('lots')))}" for leg in legs)


def ltd_order(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Largest |LTD| first, an n/a LTD last, the engine's order within a tie."""
    def key(pair):
        n, item = pair
        v = _num((item.get("pnl_usd") or {}).get("ltd"))
        return (v is None, -abs(v) if v is not None else 0.0, n)
    return [item for _n, item in sorted(enumerate(items), key=key)]


# ---- levels, in the spread's own unit
def level_decimals(unit: Optional[str]) -> int:
    """How many decimals a level in `unit` is shown with: the exchange's tick, roughly.
    Bushels, pounds and gallons 4 (a quarter cent), MMBtu and hundredweight 3, yen and won 0,
    yuan 1 (a half-yuan tick on iron ore; 2 per gram), anything else 2 (a cent)."""
    ccy, _sep, qty = str(unit or "").partition("/")
    ccy, qty = ccy.strip().upper(), qty.strip().lower()
    if qty in ("bu", "lb", "gal"):
        return 4
    if qty in ("mmbtu", "cwt"):
        return 3
    if ccy in ("JPY", "KRW"):
        return 0
    if ccy == "CNY":
        return 2 if qty == "g" else 1
    return 2


def level_text(v: Optional[float], unit: Optional[str], sign: bool = False) -> str:
    """A level (or, with `sign`, a change) in its unit's decimals, a true minus sign: '0.34',
    '−2.63', '+3.06'; None is 'n/a'."""
    if v is None:
        return NA
    d = level_decimals(unit)
    text = f"{abs(v):,.{d}f}"
    if float(text.replace(",", "")) == 0:
        return text
    if v < 0:
        return MINUS + text
    return ("+" + text) if sign else text


def family_key(item: Dict[str, Any]) -> str:
    """The group a position sits in: its family, or 'bundle' / 'pinned' for the ones by hand."""
    kind = item.get("kind") or ""
    if kind in ("bundle", "pinned"):
        return kind
    return str(item.get("family") or kind or "")


def family_label(key: str) -> str:
    return FAMILY_LABELS.get(key, key.capitalize() if key else NA)


def family_order(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Grouped by family (`FAMILY_ORDER`, other families, then bundles and pins), largest |LTD|
    first within each."""
    def rank(key: str) -> Tuple[int, str]:
        if key in FAMILY_ORDER:
            return FAMILY_ORDER.index(key), ""
        if key == "bundle":
            return len(FAMILY_ORDER) + 1, ""
        if key == "pinned":
            return len(FAMILY_ORDER) + 2, ""
        return len(FAMILY_ORDER), key
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(family_key(item), []).append(item)
    return [item for key in sorted(groups, key=rank) for item in ltd_order(groups[key])]


# --------------------------------------------------------------------------- period cells
def period_cells(item: Dict[str, Any], rec: Dict[str, Any], tip: Dict[str, dict], full: bool = False,
                 periods: Sequence[str] = PERIODS) -> None:
    """The period cells of a spread, a position or an outright, as the engine gives them: a
    None is "n/a" with its reason; a figure carries the close it is measured from (and the
    engine's step-back or fill note, and for a position the entries it leaves out) on hover,
    and with `full` (a k / m column) the full figure first."""
    values = item.get("pnl_usd") or {}
    reasons = item.get("pnl_reasons") or {}
    notes = item.get("pnl_notes") or {}
    refs = item.get("ref_dates") or {}
    excluded = item.get("pnl_excluded") or {}
    for p in periods:
        v = _num(values.get(p))
        note = notes.get(p) or ""
        if v is None:
            rec[p] = NA
            why = reasons.get(p) or "spreads-engine gave no figure and no reason"
            tip[p] = _tip(f"{why}. {note}".strip() if note else why)
            continue
        rec[p] = v
        words = [full_usd(v)] if full else []
        if p != "ltd":
            words.append(f"measured from the {refs.get(p) or NA} close")
        n_out = int(_num(excluded.get(p)) or 0)
        if n_out:
            words.append(f"excludes {n_out} entr{'y' if n_out == 1 else 'ies'} with no figure: "
                         f"{reasons.get(p) or 'no reason given'}")
        if note:
            words.append(note)
        if words:
            tip[p] = _tip("; ".join(words))


def _period_columns(short: bool = False) -> List[dict]:
    """The five USD period columns: k / m on the spreads tables (`short`, fed through
    `ranking.whole_units`), full figures on the trade rows of the outrights."""
    usd = rk.amount_short(nully="") if short else rk.amount(nully="")
    return [rk.numeric(f"{PERIOD_TITLES[p]} USD", p, usd) for p in PERIODS]


def total_record(items: Sequence[Dict[str, Any]], records: Sequence[Dict[str, Any]], columns: Sequence[str],
                 label_col: str, note_col: str, noun: str) -> Tuple[dict, dict]:
    """(footer record, its tooltips): each column in `columns` is the sum of the records' known
    figures; one that leaves any out says so on hover and in the note column ("excludes N"); one
    with no known figure at all is n/a. The header's display rule."""
    rec: Dict[str, Any] = {label_col: TOTAL_LABEL}
    tip: Dict[str, dict] = {}
    notes: List[str] = []
    names = [str(i.get("name") or i.get("trade_id") or "") for i in items]
    total = len(records)
    for col in columns:
        known = [(n, r[col]) for n, r in enumerate(records) if isinstance(r.get(col), float)]
        missing = [names[n] for n, r in enumerate(records) if not isinstance(r.get(col), float)]
        title = PERIOD_TITLES.get(col, "Leftover USD" if col == "leftover_usd" else col)
        if not known:
            rec[col] = NA
            tip[col] = _tip(f"no {noun} has a {title} figure" if total else f"no {noun}")
            continue
        rec[col] = float(sum(v for _n, v in known))
        if missing:
            notes.append(f"{title} {len(missing)}")
            tip[col] = _tip(f"excludes {len(missing)} of {total} {noun}s with no figure (n/a): "
                            + ", ".join(missing))
    rec[note_col] = f"excludes n/a: {', '.join(notes)}" if notes else f"{total} {noun}{'s' if total != 1 else ''}"
    return rec, tip


# --------------------------------------------------------------------------- research
def empty_research(reason: str = NO_RESEARCH) -> Dict[str, Any]:
    """What the tab shows when the research statistics were not read: every research cell n/a
    with `reason`."""
    return {"available": False, "reason": reason, "run_asof": None, "source": "", "stats": {}, "label": "research"}


def research_key_of(item: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(research_id, instance) as spreads-engine gives them, or None when it gives no research id."""
    rid = item.get("research_id") or ""
    return (rid, item.get("research_instance") or "") if rid else None


def research_for(result: Dict[str, Any], as_of: str, stats_fn: Callable = research_spread_stats) -> Dict[str, Any]:
    """One `research_spread_stats` call for every open position's research key (about 0.1 s,
    so not memoised: the research database is the research app's and changes on its own
    runs, which no revision of ours would see). Never raises: a failure is the reason."""
    keys = sorted({k for k in (research_key_of(p) for p in result.get("positions") or []) if k})
    try:
        return stats_fn(keys, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a broken tab
        return empty_research(f"the research statistics could not be read ({type(exc).__name__}: {exc})")


def research_entry(item: Dict[str, Any], research: Dict[str, Any]) -> Tuple[Optional[dict], str]:
    """(the research statistics of a position, '') or (None, why there are none)."""
    key = research_key_of(item)
    if key is None:
        return None, item.get("research_reason") or "spreads-engine gave no research id and no reason"
    entry = (research.get("stats") or {}).get(key)
    if entry is None:
        return None, research.get("reason") or NO_RESEARCH
    if not entry.get("found"):
        return entry, entry.get("reason") or "no research statistics"
    return entry, ""


def _run_words(entry: Optional[dict], research: Dict[str, Any]) -> str:
    run = (entry or {}).get("asof") or research.get("run_asof")
    words = f"research run of {run}" if run else "no research run"
    note = (entry or {}).get("note") or ""
    return f"{words}{f' ({note})' if note else ''}; {RESEARCH_TAG}"


def research_cells(item: Dict[str, Any], research: Dict[str, Any], rec: Dict[str, Any],
                   tip: Dict[str, dict]) -> List[str]:
    """The four research cells of a position: today's move in sigma (`sigma_move` of the
    engine's level change), z_primary, pctile_5y and half_life_days, as the research reader
    gives them; each n/a with its reason. Returns the distinct reasons for the drawer."""
    entry, why = research_entry(item, research)
    unit = item.get("level_unit") or None
    reasons: List[str] = []
    run = _run_words(entry, research)
    # sigma: our level move over the research app's daily vol
    change = _num(item.get("level_change"))
    if change is None:
        sigma, s_why = None, item.get("level_change_reason") or "spreads-engine gave no level change and no reason"
    elif why:
        sigma, s_why = None, why
    else:
        sigma, s_why = sigma_move(change, entry, unit=unit)
    if sigma is None:
        rec["sigma"] = NA
        tip["sigma"] = _tip(f"{s_why}. {run}")
        reasons.append(f"sigma move n/a: {s_why}")
    else:
        rec["sigma"] = float(sigma)
        tip["sigma"] = _tip(f"today's level change {level_text(change, unit, sign=True)} {unit or ''} over the "
                            f"research app's 20-day daily vol {entry.get('dvol_20d'):.4g} {entry.get('unit') or ''}"
                            f" = {sigma:+.2f} sigma; {run}")
    stat_cols = (("z", "z_primary", "z-score"), ("pctile", "pctile_5y", "5-year percentile"),
                 ("half_life", "half_life_days", "half-life in days"))
    for col, field, words in stat_cols:
        v = None if why else _num((entry or {}).get(field))
        if v is None:
            c_why = why or f"the research app left the {words} blank (not enough history)"
            rec[col] = NA
            tip[col] = _tip(f"{c_why}. {run}")
            if not why:
                reasons.append(f"{words} n/a: {c_why}")
            continue
        rec[col] = v
        extra = ""
        if col == "z":
            kind = entry.get("z_primary_kind") or "not named"
            z1 = _num(entry.get("z_1y"))
            extra = f"kind {kind}" + (f"; 1-year z {z1:+.2f}" if z1 is not None and kind != "z_1y" else "")
        elif col == "pctile":
            extra = "where today's research level sits in its last 5 years (0 = lowest, 100 = highest)"
        elif col == "half_life":
            extra = "how fast the spread has mean-reverted: days for half a deviation to close"
        tip[col] = _tip(f"{words} {v:.4g}; {extra}; {run}")
    if why:
        reasons.insert(0, f"research n/a: {why}")
    return list(dict.fromkeys(reasons))


# --------------------------------------------------------------------------- 1. caption
def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _na_reasons(item: Dict[str, Any], periods: Sequence[str] = PERIODS) -> List[str]:
    """The engine's distinct reasons for the periods it gave as None (and, for a position, the
    entries a period leaves out), in period order."""
    values = item.get("pnl_usd") or {}
    reasons = item.get("pnl_reasons") or {}
    excluded = item.get("pnl_excluded") or {}
    by_reason: Dict[str, List[str]] = {}
    for p in periods:
        why = reasons.get(p) or "spreads-engine gave no figure and no reason"
        if _num(values.get(p)) is None:
            by_reason.setdefault(f"n/a: {why}", []).append(PERIOD_TITLES[p])
        elif _num(excluded.get(p)):
            by_reason.setdefault(f"excludes {int(_num(excluded.get(p)))}: {why}", []).append(PERIOD_TITLES[p])
    return [f"{', '.join(titles)} {why}" for why, titles in by_reason.items()]


def _level_reasons(item: Dict[str, Any]) -> List[str]:
    """The distinct reasons of the level figures spreads-engine gave as None."""
    out: Dict[str, None] = {}
    for key, words in (("level_entry", "entry"), ("level_now", "level now"), ("level_change", "today's change"),
                       ("usd_per_unit", "USD per 1.0")):
        if _num(item.get(key)) is None:
            why = item.get(f"{key}_reason") or "spreads-engine gave no figure and no reason"
            out.setdefault(f"{words} n/a: {why}", None)
    return list(out)


def issues_items(result: Dict[str, Any], research: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """The Data issues drawer's lines: the engine's book-level `reasons`; the research reader's
    own reason when it could not read at all; each open position's n/a levels, research
    statistics and P&L; each closed spread's and outright's n/a periods."""
    research = research if research is not None else empty_research()
    items: List[Tuple[str, str]] = [("Grouping", r) for r in (result.get("reasons") or []) if r]
    book_level = "" if research.get("available") else (research.get("reason") or NO_RESEARCH)
    if book_level:
        items.append(("Research", book_level))
    for p in open_positions(result):
        lines = _level_reasons(p)
        lines += [r for r in research_cells(p, research, {}, {}) if not (book_level and book_level in r)]
        lines += _na_reasons(p)
        if lines:
            items.append((str(p.get("name") or p.get("position_id") or ""), "; ".join(lines)))
    for s in ltd_order([s for s in (result.get("spreads") or []) if s.get("status") == "closed"]):
        lines = _na_reasons(s)
        if lines:
            items.append((f"Closed {s.get('name') or s.get('spread_id') or ''}", "; ".join(lines)))
    for o in result.get("outrights") or []:
        lines = _na_reasons(o)
        if lines:
            items.append((f"Outright {o.get('trade_id') or ''} ({contract_label(o.get('instrument_id'))})",
                          "; ".join(lines)))
    return items


def caption_block(result: Dict[str, Any], research: Optional[Dict[str, Any]] = None) -> html.Div:
    """One line (the as-of, the counts and the research run) and the Data issues drawer; the
    definitions are on the title's hover (`TITLE_ABOUT`)."""
    research = research if research is not None else empty_research()
    spreads = result.get("spreads") or []
    n_open = sum(1 for s in spreads if s.get("status") != "closed")
    n_pos = len(open_positions(result))
    counts = (f"{_plural(n_pos, 'open position')} ({_plural(n_open, 'spread')}), {len(spreads) - n_open} closed, "
              f"{_plural(len(result.get('outrights') or []), 'outright')}, "
              f"{_plural(len(result.get('review') or []), 'group')} for review")
    run = research.get("run_asof")
    run_text = (f"Research: run of {run}" if run else
                "Research: no run on or before this date" if research.get("available") else "Research: n/a")
    run_hover = research.get("source") or research.get("reason") or NO_RESEARCH
    children: List[Any] = [html.Div(className="meta-line", children=[
        html.Span(f"As of {_date_words(result.get('as_of'))}"), html.Span(counts),
        html.Span(run_text, title=f"{run_hover}; {RESEARCH_TAG}")])]
    drawer = issues_drawer(issues_items(result, research), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
    return html.Div(children, className="spreads-caption")


# --------------------------------------------------------------------------- 2. positions
def open_positions(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The open positions, grouped by family, largest |LTD| first within each."""
    return family_order([p for p in (result.get("positions") or []) if p.get("status") != "closed"])


def leftover_cells(spread: Dict[str, Any]) -> Tuple[str, Any, str]:
    """(lots text per root, USD notional or "n/a", hover). The USD is the roots' engine figures
    added up; any root without one makes it n/a with the engine's reason."""
    entries = spread.get("leftover") or []
    basis = spread.get("leftover_basis") or ""
    if not entries:
        return "none", NA, "no futures leg in this spread, so no leftover outright is sized"
    lots = [(e, _num(e.get("lots"))) for e in entries]
    if all(v == 0 for _e, v in lots):
        text = "none (clean)"
    else:
        text = "; ".join(f"{root_label(e.get('root_id'))} {signed(v)}" for e, v in lots if v != 0)
    usd = [_num(e.get("usd_notional")) for e in entries]
    parts = [f"{e.get('root_id') or ''}: {signed(v)} lot(s), "
             + (f"USD {u:,.0f}" if u is not None else f"{NA} ({e.get('reason') or 'no USD notional'})")
             for (e, v), u in zip(lots, usd)]
    hover = "; ".join(parts) + (f". Leftover {basis}." if basis else "")
    if any(u is None for u in usd):
        return text, NA, hover
    return text, float(sum(usd)), hover


def position_size_text(p: Dict[str, Any]) -> Tuple[str, str]:
    """('long 15 lots', hover) or ('n/a', why): the summed size with its direction and unit."""
    size = _num(p.get("size"))
    if size is None:
        why = ("no calendar or template fits all of its futures legs, so it has no size"
               if p.get("kind") in ("bundle", "pinned") else "spreads-engine gave no size")
        return NA, why
    direction = p.get("direction") or ("long" if size >= 0 else "short")
    unit = p.get("size_unit") or ""
    hover = (f"{direction} {size_text(abs(size), unit)}: a calendar in lots of the near month (long = long the "
             "near month), a template in its quantity unit (long = long the spread as the template writes it)")
    return f"{direction} {size_text(abs(size), unit)}", hover


def position_record(p: Dict[str, Any], rank: int, research: Dict[str, Any]) -> Tuple[dict, dict]:
    """(record, tooltips) of one position row, the engine's and the research reader's figures
    as they are; `id` is the position id the drill-down opens."""
    tip: Dict[str, dict] = {}
    legs = p.get("legs") or []
    members = p.get("spread_ids") or []
    unit = p.get("level_unit") or ""
    name = str(p.get("name") or p.get("position_id") or "")
    rec: Dict[str, Any] = {
        "id": p.get("position_id") or name, "rank": rank,
        "name": name + (f" · {len(members)} entries" if len(members) > 1 else ""),
        "legs": legs_summary(legs), "unit": unit or NA,
    }
    rkey = research_key_of(p)
    facts = [name, f"family {family_label(family_key(p))}", kind_label(p), f"id {p.get('position_id') or ''}",
             f"{len(members)} entr{'y' if len(members) == 1 else 'ies'}: {', '.join(members)}",
             f"trades {', '.join(p.get('trade_ids') or [])}",
             f"account(s) {', '.join(p.get('accounts') or [])}",
             f"traded {', '.join(p.get('trade_dates') or [])}",
             (f"research id {rkey[0]}{f' {rkey[1]}' if rkey[1] else ''}" if rkey
              else f"no research id: {p.get('research_reason') or 'no reason given'}"),
             "click for its research history, entries and legs"]
    tip["name"] = _tip("; ".join(facts))
    tip["legs"] = _tip("; ".join(f"{leg.get('instrument_id') or ''}: {signed(_num(leg.get('lots')))} lot(s), "
                                 f"{signed(_num(leg.get('open_lots')))} open" for leg in legs) or "no legs")
    rec["size"], size_hover = position_size_text(p)
    tip["size"] = _tip(size_hover)
    if not unit:
        tip["unit"] = _tip("no calendar or template fits these legs, so the spread has no level")
    # levels: the engine's, in the unit's decimals, sources on hover
    sources = p.get("level_sources") or {}
    for col, key, src in (("entry", "level_entry", "entry"), ("now", "level_now", "now")):
        v = _num(p.get(key))
        rec[col] = level_text(v, unit)
        if v is None:
            tip[col] = _tip(p.get(f"{key}_reason") or "spreads-engine gave no level and no reason")
        else:
            tip[col] = _tip(f"{level_text(v, unit)} {unit}; read from {sources.get(src) or 'no source named'}")
    change, prev = _num(p.get("level_change")), _num(p.get("level_prev"))
    rec["change"] = level_text(change, unit, sign=True)
    if change is None:
        tip["change"] = _tip(p.get("level_change_reason") or p.get("level_prev_reason")
                             or "spreads-engine gave no change and no reason")
    else:
        tip["change"] = _tip(f"now {level_text(_num(p.get('level_now')), unit)} against "
                             f"{level_text(prev, unit)} at the {p.get('level_prev_date') or NA} close (the Daily's "
                             f"reference), read from {sources.get('prev') or 'no source named'}")
    research_cells(p, research, rec, tip)
    upu = _num(p.get("usd_per_unit"))
    rec["usd_per_unit"] = NA if upu is None else upu
    tip["usd_per_unit"] = _tip(
        (p.get("usd_per_unit_reason") or "spreads-engine gave no figure and no reason") if upu is None else
        f"{full_usd(upu)} for a 1.0 rise of the level ({unit}) on the open lots (+ = a rise is a gain); "
        + (sources.get("usd_per_unit") or "USD contracts, no conversion"))
    # P&L: Daily, MTD, LTD shown; 5d on Daily's hover and YTD on LTD's
    period_cells(p, rec, tip, full=True, periods=SHOWN_PERIODS)
    hidden: Dict[str, Any] = {}
    hidden_tip: Dict[str, dict] = {}
    period_cells(p, hidden, hidden_tip, full=True, periods=tuple(HOVER_PERIOD.values()))
    for shown, other in HOVER_PERIOD.items():
        words = hidden_tip.get(other, {}).get("value") or ""
        line = f"{PERIOD_TITLES[other]}: {words}" if words else f"{PERIOD_TITLES[other]}: {hidden.get(other)}"
        base = tip.get(shown, {}).get("value")
        tip[shown] = _tip(f"{base}. {line}" if base else line)
    excluded = p.get("pnl_excluded") or {}
    left_out = {q: int(_num(excluded.get(q)) or 0) for q in PERIODS
                if _num((p.get("pnl_usd") or {}).get(q)) is not None and _num(excluded.get(q))}
    if left_out:
        rec["pnl_note"] = f"excl. {max(left_out.values())}"
        tip["pnl_note"] = _tip("; ".join(f"{PERIOD_TITLES[q]} excludes {n}: "
                                         f"{(p.get('pnl_reasons') or {}).get(q) or 'no reason given'}"
                                         for q, n in left_out.items()))
    else:
        filled = [n for n in (p.get("pnl_notes") or {}).values() if n]
        rec["pnl_note"] = "filled" if filled else ""
        if filled:
            tip["pnl_note"] = _tip("; ".join(dict.fromkeys(filled)))
    text, usd, hover = leftover_cells(p)
    rec["leftover"], rec["leftover_usd"] = text, usd
    tip["leftover"] = _tip(hover)
    tip["leftover_usd"] = _tip(f"{full_usd(usd)}. {hover}" if isinstance(usd, float) else hover)
    return rec, tip


def _sigma_format() -> dict:
    return Format(precision=1, scheme=Scheme.fixed, sign=Sign.positive, nully="").to_plotly_json()


def position_columns(research: Dict[str, Any]) -> List[dict]:
    """Two header rows: the group (Spread, Level, Research with its run, USD, P&L, Leftover)
    over each column's name."""
    run = research.get("run_asof")
    rgroup = f"Research (run {run})" if run else "Research"
    return [
        rk.numeric(["", "#"], "rank", rk.count()),
        rk.text(["Spread", "Spread"], "name"), rk.text(["Spread", "Legs (lots)"], "legs"),
        rk.text(["Spread", "Size"], "size"),
        rk.text(["Level", "Unit"], "unit"), rk.text(["Level", "Entry"], "entry"),
        rk.text(["Level", "Now"], "now"), rk.text(["Level", "Chg"], "change"),
        rk.numeric(["USD", "per 1.0"], "usd_per_unit", rk.amount_short(nully="")),
        *[rk.numeric(["P&L USD", PERIOD_TITLES[p]], p, rk.amount_short(nully="")) for p in SHOWN_PERIODS],
        rk.numeric([rgroup, "σ move"], "sigma", _sigma_format()),
        rk.numeric([rgroup, "z"], "z", _sigma_format()),
        rk.numeric([rgroup, "5y %ile"], "pctile", rk.count()),
        rk.numeric([rgroup, "Half-life"], "half_life", rk.amount(0, nully="")),
        rk.text(["Leftover", "Lots"], "leftover"),
        rk.numeric(["Leftover", "USD"], "leftover_usd", rk.amount_short(nully="")),
        rk.text(["", "Note"], "pnl_note"),
    ]


# Every column's width in px, so the whole row is in sight at 1680 px with no horizontal scroll
# (the section's inner width there is about 1590 px): the widest usual content at 12.5 px Inter
# plus 16 px of padding, and about 2 px of cell border each on top (`BORDER_PX`). A longer name,
# legs or leftover is cut with an ellipsis, in full on hover.
COLUMN_WIDTHS_PX: Dict[str, int] = {
    "rank": 32, "name": 220, "legs": 190, "size": 104, "unit": 68, "entry": 72, "now": 72, "change": 68,
    "usd_per_unit": 64, "daily": 66, "mtd": 66, "ltd": 66, "sigma": 60, "z": 52, "pctile": 56, "half_life": 60,
    "leftover": 104, "leftover_usd": 62, "pnl_note": 62,
}
WIDTH_BUDGET_PX = 1590
BORDER_PX = 2                                # what each cell renders wider than its width (Chrome, 2026-09-25)


def _width_rules(columns: Sequence[dict]) -> List[dict]:
    rules = []
    for col in columns:
        px = f"{COLUMN_WIDTHS_PX[col['id']]}px"
        rules.append({"if": {"column_id": col["id"]}, "width": px, "minWidth": px, "maxWidth": px})
    return rules


# The footer table of `ranking.with_footer` hides its header cells, but each header row keeps its
# height, which left an empty band above the Total line (seen 2026-09-25 in Chrome at 1680 px):
# hide those rows on the footer table only.
_FOOTER_CSS = [{"selector": "tr:has(> th)", "rule": "display: none;"}]


def pinned(block: html.Div) -> html.Div:
    """`ranking.with_footer`'s block with its footer table's header rows hidden, so the Total
    line sits straight under the rows."""
    footer = block.children[1]
    footer.css = list(getattr(footer, "css", None) or []) + _FOOTER_CSS
    return block


RESEARCH_COLS = ("sigma", "z", "pctile", "half_life")
_POSITION_SUMMED = SHOWN_PERIODS + ("leftover_usd",)
_POSITION_ROUNDED = _POSITION_SUMMED + ("usd_per_unit",)


def _position_header_tips(research: Dict[str, Any]) -> Dict[str, str]:
    run = research.get("run_asof")
    src = research.get("source") or research.get("reason") or NO_RESEARCH
    tag = f"The research app's statistics ({f'run of {run}' if run else 'no run'}; {src}): {RESEARCH_TAG}."
    return {
        "rank": "Grouped by family (calendar, benchmark / arb, crack / processing, substitution, then the "
                "spreads grouped by hand; a line between groups), largest |LTD| first within each. Click to "
                "restore that order.",
        "name": "The spread (its family on hover); one row however many trade dates it was put on over. Click a "
                "row to open it.",
        "size": "The position's size, summed over its entries, with its direction and unit.",
        "unit": "The unit every level of the row is in.",
        "entry": "The level at the fills (each leg at its lots-weighted average fill; several entries weighted "
                 "by size).",
        "now": "The level at the as-of date's official marks: the prices the P&L reads.",
        "change": "Now less the level at the close the Daily P&L is measured from.",
        "sigma": f"Today's level change over the research app's 20-day daily vol, in sigma. {tag}",
        "z": f"The research app's primary z-score (its kind on hover of the cell). {tag}",
        "pctile": f"Where the research level sits in its last 5 years, 0 to 100. {tag}",
        "half_life": f"The research app's mean-reversion half-life, in days. {tag}",
        "usd_per_unit": "USD P&L of a 1.0 rise of the level on the open lots (+ = long: a rise is a gain).",
        "daily": "Daily P&L in USD (5d on hover).", "mtd": "Month-to-date P&L in USD.",
        "ltd": "Life-to-date P&L in USD (YTD on hover).",
        "pnl_note": "excl. N: a period sums the priced entries only and leaves N out (the reasons on hover); "
                    "filled: a figure is the value of an earlier close.",
        "leftover": "What the open legs hold beyond the largest whole spread they make, per commodity.",
        "leftover_usd": "The leftover's USD notional as spreads-engine gives it, added over the commodities.",
    }


def positions_table(positions: Sequence[Dict[str, Any]], research: Dict[str, Any]) -> html.Div:
    """The open positions, one row each, a Total line pinned under them; a row's id is its
    position id, which the drill-down reads from the clicked cell."""
    recs = [position_record(p, i, research) for i, p in enumerate(positions, start=1)]
    records = [r for r, _ in recs]
    columns = position_columns(research)
    numeric = [c["id"] for c in columns if c["type"] == "numeric" and c["id"] != "rank"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    signed_cols = [c for c in numeric if c not in RESEARCH_COLS]
    styles = _table_styles(signed_cols, text, na_cols=("size", "entry", "now", "change", "unit"),
                           extra_na=RESEARCH_COLS)
    group_starts = [i for i in range(1, len(positions))
                    if family_key(positions[i]) != family_key(positions[i - 1])]
    styles["style_data_conditional"] = (
        [{"if": {"column_id": c}, **_RESEARCH_BG} for c in RESEARCH_COLS]
        + styles["style_data_conditional"]
        + [{"if": {"column_id": c, "filter_query": f"{{{c}}} >= {HIGHLIGHT}"}, **_HIGHLIGHT_STYLE}
           for c in ("sigma", "z")]
        + [{"if": {"column_id": c, "filter_query": f"{{{c}}} <= -{HIGHLIGHT}"}, **_HIGHLIGHT_STYLE}
           for c in ("sigma", "z")]
        + [{"if": {"row_index": i}, **_GROUP_START} for i in group_starts])
    styles["style_data"] = {"cursor": "pointer"}
    styles["style_cell"] = {**styles["style_cell"], **_ONE_LINE}
    styles["style_cell_conditional"] = styles["style_cell_conditional"] + _width_rules(columns)
    table = dash_table.DataTable(
        id=TABLE_ID, columns=columns, data=rk.whole_units(records, _POSITION_ROUNDED),
        tooltip_data=[t for _, t in recs], tooltip_header=_position_header_tips(research),
        merge_duplicate_headers=True, **rk.sortable(TABLE_ID), **styles, page_action="none")
    footer, footer_tip = total_record(positions, records, _POSITION_SUMMED, "name", "legs", "position")
    for col in _POSITION_SUMMED:
        partial = [str(p.get("name") or "") for p in positions
                   if col in SHOWN_PERIODS and _num((p.get("pnl_excluded") or {}).get(col))
                   and _num((p.get("pnl_usd") or {}).get(col)) is not None]
        rest = (footer_tip.get(col) or {}).get("value")
        words = [full_usd(footer[col])] if isinstance(footer.get(col), float) else []
        if rest:
            words.append(rest)
        if partial:
            words.append(f"includes {len(partial)} position(s) that leave out entries: {', '.join(partial)}")
        if words:
            footer_tip[col] = _tip("; ".join(words))
    footer = rk.whole_units([footer], _POSITION_SUMMED)[0]
    total_style = [{"if": {"filter_query": f"{{name}} = '{TOTAL_LABEL}'"}, **_TOTAL_STYLE}]
    return pinned(rk.with_footer(table, [footer], widths=False, footer_style=total_style,
                                 footer_tooltips=[footer_tip]))


def positions_section(result: Dict[str, Any], research: Dict[str, Any], selected: Optional[str] = None,
                      history_fn: Callable = research_spread_history) -> html.Div:
    """The positions table, the drill-down under it (opened on `selected` when it is still an
    open position) and the store the drill-down reads."""
    positions = open_positions(result)
    heading = about(f"Open spreads ({len(positions)})", POSITIONS_ABOUT)
    if not positions:
        return html.Div(className="section", children=[heading, message_box("No open spread in the book."),
                                                       dcc.Store(id=DETAIL_STORE_ID, data={}),
                                                       html.Div(id=DETAIL_ID)])
    payloads = detail_payloads(result, research)
    panel = (detail_panel(payloads[selected], result.get("as_of"), history_fn) if selected in payloads
             else html.Div("Click a spread to see its research history, its entries and its legs.",
                           className="section-kicker", style={"margin": "6px 0 0"}))
    return html.Div(className="section", children=[
        heading, positions_table(positions, research),
        dcc.Store(id=DETAIL_STORE_ID, data=payloads),
        html.Div(id=DETAIL_ID, className="spreads-detail", children=panel)])


# --------------------------------------------------------------------------- the drill-down
def detail_payloads(result: Dict[str, Any], research: Dict[str, Any]) -> Dict[str, dict]:
    """{position_id: what its drill-down shows}, JSON-safe, kept in `DETAIL_STORE_ID` so a click
    opens it without building the book again: the position's levels, its member spreads' entries
    (from `result["spreads"]`), its legs (`level_legs` beside the summed `legs`) and its
    research key."""
    by_id = {s.get("spread_id"): s for s in result.get("spreads") or []}
    out: Dict[str, dict] = {}
    for p in open_positions(result):
        entry, why = research_entry(p, research)
        lots = {leg.get("instrument_id"): leg for leg in p.get("legs") or []}
        members = []
        for sid in p.get("spread_ids") or []:
            s = by_id.get(sid) or {}
            members.append({k: s.get(k) for k in ("spread_id", "trade_dates", "accounts", "trade_ids", "size",
                                                   "size_unit", "status", "level_entry", "level_entry_reason")}
                           | {"spread_id": sid})
        legs = []
        for leg in p.get("level_legs") or []:
            held = lots.get(leg.get("instrument_id")) or {}
            legs.append({k: leg.get(k) for k in ("instrument_id", "root_id", "weight", "qty_factor", "conversion",
                                                 "currency", "price_scale", "entry_price", "prev_price",
                                                 "now_price")}
                        | {k: held.get(k) for k in ("lots", "open_lots", "pnl_usd", "status", "reason")})
        key = research_key_of(p)
        out[p.get("position_id") or str(p.get("name"))] = {
            "position_id": p.get("position_id"), "name": p.get("name"), "unit": p.get("level_unit") or "",
            "size_text": position_size_text(p)[0],
            **{k: p.get(k) for k in ("level_entry", "level_entry_reason", "level_now", "level_now_reason",
                                     "level_prev", "level_prev_reason", "level_prev_date")},
            "sources": dict(p.get("level_sources") or {}),
            "research_id": key[0] if key else "", "research_instance": key[1] if key else "",
            "research_reason": why, "research_unit": (entry or {}).get("unit") or "",
            "research_run": (entry or {}).get("asof") or research.get("run_asof"),
            "research_name": (entry or {}).get("name") or "",
            "members": members, "legs": legs,
        }
    return out


def _unit_same(a: Optional[str], b: Optional[str]) -> bool:
    """Two unit strings the same once spaces and case are ignored and '$' read as 'USD'."""
    def norm(u):
        return "".join(str(u or "").split()).replace("$", "USD").casefold()
    return bool(a) and norm(a) == norm(b)


def history_figure(series, payload: Dict[str, Any], as_of: Optional[str]) -> Tuple[Optional[dict], str]:
    """(figure, note) for the drill-down chart: the research history as a line in its unit; the
    position's entry level as a dashed line and today's level as a marker, drawn only when our
    unit and the research unit agree (else the note says why). (None, reason) when the research
    app has no history."""
    reason = getattr(series, "attrs", {}).get("reason") if series is not None else "no research history read"
    if series is None or len(series) == 0:
        return None, reason or "the research app has no history for this spread"
    xs = [d.strftime("%Y-%m-%d") for d in series.index]
    r_unit = series.attrs.get("unit") or payload.get("research_unit") or ""
    data = [{"x": xs, "y": [float(v) for v in series.values], "type": "scatter", "mode": "lines",
             "name": "research history", "line": {"color": "#16294f", "width": 1.5},
             "hovertemplate": "%{x}<br>%{y:.4g} " + r_unit + " (research)<extra></extra>"}]
    unit = payload.get("unit") or ""
    notes: List[str] = []
    if _unit_same(unit, r_unit):
        entry, now = _num(payload.get("level_entry")), _num(payload.get("level_now"))
        if entry is not None:
            data.append({"x": [xs[0], max(xs[-1], as_of or xs[-1])], "y": [entry, entry], "type": "scatter",
                         "mode": "lines", "name": f"entry {level_text(entry, unit)} (the fills)",
                         "line": {"color": "#c9a227", "dash": "dash", "width": 1.5}, "hoverinfo": "skip"})
        else:
            notes.append(f"no entry line: {payload.get('level_entry_reason') or 'no entry level'}")
        if now is not None and as_of:
            data.append({"x": [as_of], "y": [now], "type": "scatter", "mode": "markers",
                         "name": f"now {level_text(now, unit)} (official marks)",
                         "marker": {"color": "#c0392b", "size": 9, "symbol": "diamond"},
                         "hovertemplate": "%{x}<br>now %{y:.4g} " + unit + "<extra></extra>"})
        elif now is None:
            notes.append(f"no marker for today: {payload.get('level_now_reason') or 'no level now'}")
    else:
        notes.append(f"entry and today's level not drawn: the book's level is in {unit or 'no unit'}, "
                     f"the research history in {r_unit or 'no unit'}")
    figure = {"data": data,
              "layout": {"margin": {"l": 55, "r": 20, "t": 10, "b": 30}, "height": 260,
                         "xaxis": {"type": "date"}, "yaxis": {"title": r_unit},
                         "legend": {"orientation": "h", "y": -0.2}, "showlegend": True}}
    return figure, "; ".join(notes)


def members_table(payload: Dict[str, Any]) -> dash_table.DataTable:
    """The position's entries: one row per member spread, its trade dates, accounts, size and
    entry level (n/a with its reason)."""
    unit = payload.get("unit") or ""
    rows, tips = [], []
    for m in payload.get("members") or []:
        size = _num(m.get("size"))
        entry = _num(m.get("level_entry"))
        rows.append({"spread_id": m.get("spread_id") or "", "trade_dates": ", ".join(m.get("trade_dates") or []),
                     "accounts": ", ".join(m.get("accounts") or []),
                     "size": NA if size is None else size_text(size, m.get("size_unit") or ""),
                     "entry": level_text(entry, unit), "trades": ", ".join(m.get("trade_ids") or []),
                     "status": m.get("status") or ""})
        tip: Dict[str, dict] = {}
        if entry is None:
            tip["entry"] = _tip(m.get("level_entry_reason") or "spreads-engine gave no entry and no reason")
        tips.append(tip)
    columns = [rk.text("Entry", "spread_id"), rk.text("Trade date(s)", "trade_dates"),
               rk.text("Account(s)", "accounts"), rk.text("Size", "size"),
               rk.text(f"Entry level ({unit or 'no unit'})", "entry"), rk.text("Trades", "trades"),
               rk.text("Status", "status")]
    return dash_table.DataTable(
        id=DETAIL_MEMBERS_ID, columns=columns, data=rows, tooltip_data=tips, **rk.sortable(),
        **_table_styles([], [c["id"] for c in columns if c["id"] != "entry"], na_cols=("entry", "size"),
                        compact=True), page_action="none")


def detail_legs_table(payload: Dict[str, Any]) -> dash_table.DataTable:
    """The legs in the shape's order: weight, lots, currency, the prices as quoted at entry, at
    the previous close and now, and the conversion to the spread's unit."""
    rows, tips = [], []
    reasons = {"entry_price": payload.get("level_entry_reason"), "prev_price": payload.get("level_prev_reason"),
               "now_price": payload.get("level_now_reason")}
    for leg in payload.get("legs") or []:
        rec: Dict[str, Any] = {"contract": contract_label(leg.get("instrument_id")),
                               "weight": _num(leg.get("weight")), "lots": _num(leg.get("lots")),
                               "open_lots": _num(leg.get("open_lots")), "currency": leg.get("currency") or ""}
        tip: Dict[str, dict] = {}
        for col in ("entry_price", "prev_price", "now_price"):
            v = _num(leg.get(col))
            rec[col] = NA if v is None else v
            if v is None:
                tip[col] = _tip(reasons[col] or "spreads-engine gave no price and no reason")
        conv, scale = _num(leg.get("conversion")), _num(leg.get("price_scale"))
        rec["conversion"] = NA if conv is None else f"× {conv:g}"
        qf = _num(leg.get("qty_factor"))
        tip["conversion"] = _tip(
            (f"price per quote quantity to per spread quantity: × {conv:g}" if conv is not None
             else "no conversion given")
            + (f"; price scale {scale:g}" if scale is not None else "")
            + (f"; the template's quantity factor {qf:g}" if qf is not None else ""))
        for col in ("weight", "lots", "open_lots"):
            if rec[col] is None:
                rec[col] = NA
                tip[col] = _tip(leg.get("reason") or "not given")
        if leg.get("reason"):
            tip["contract"] = _tip(leg["reason"])
        rows.append(rec)
        tips.append(tip)
    px = rk.rate(4, nully="", trim=True)
    prev_date = payload.get("level_prev_date") or "previous"
    columns = [rk.text("Contract", "contract"), rk.numeric("Weight", "weight", rk.rate(6, trim=True)),
               rk.numeric("Lots", "lots", rk.amount(2, nully="", trim=True)),
               rk.numeric("Open lots", "open_lots", rk.amount(2, nully="", trim=True)),
               rk.text("Ccy", "currency"), rk.numeric("Entry px", "entry_price", px),
               rk.numeric(f"{prev_date} close px", "prev_price", px), rk.numeric("Now px", "now_price", px),
               rk.text("To spread unit", "conversion")]
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    return dash_table.DataTable(
        id=DETAIL_LEGS_ID, columns=columns, data=rows, tooltip_data=tips, **rk.sortable(),
        **_table_styles([], [c["id"] for c in columns if c["type"] == "text"], extra_na=numeric, compact=True),
        page_action="none")


def detail_panel(payload: Dict[str, Any], as_of: Optional[str],
                 history_fn: Callable = research_spread_history) -> html.Details:
    """The drill-down of one position: the research history chart (or its reason), then the
    entries and the legs. The history reaches back 5 years from the as-of (the percentile's
    window) and stops at the as-of."""
    name = payload.get("name") or payload.get("position_id") or ""
    unit = payload.get("unit") or ""
    rid, inst = payload.get("research_id") or "", payload.get("research_instance") or ""
    label = "research history (the research app's prices)"
    if not rid:
        chart: Any = html.P(f"No {label}: {payload.get('research_reason') or 'no research id'}",
                            className="section-kicker")
    else:
        start = None
        if as_of:
            try:
                d = dt.date.fromisoformat(as_of)
                start = d.replace(year=d.year - 5, day=min(d.day, 28)).isoformat()
            except ValueError:
                start = None
        try:
            series = history_fn((rid, inst), start=start, end=as_of)
        except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a broken panel
            series, fail = None, f"the research history could not be read ({type(exc).__name__}: {exc})"
        else:
            fail = ""
        figure, note = history_figure(series, payload, as_of)
        if figure is None:
            chart = html.P(f"No {label}: {fail or note}", className="section-kicker")
        else:
            key_words = f"{rid}{f' {inst}' if inst else ''}"
            run = payload.get("research_run")
            caption = (f"{label}: {payload.get('research_name') or key_words} ({key_words}) in "
                       f"{series.attrs.get('unit') or 'its unit'}{f', statistics of the {run} run' if run else ''}"
                       f"; {RESEARCH_TAG}")
            chart = html.Div([
                html.Div(caption, className="section-kicker", title=series.attrs.get("path") or ""),
                dcc.Graph(id=DETAIL_GRAPH_ID, figure=figure, config={"displayModeBar": False}),
                *([html.Div(note, className="section-kicker")] if note else [])])
    summary = (f"{name}: {payload.get('size_text') or ''}, entry {level_text(_num(payload.get('level_entry')), unit)}"
               f", now {level_text(_num(payload.get('level_now')), unit)} {unit}").strip()
    return html.Details(className="details spreads-detail-panel", open=True, children=[
        html.Summary(summary, title=DETAIL_ABOUT, className="about-title"),
        chart,
        about("Entries", "The spreads found on each trade date that make this position, with their own entry "
                         "level.", level="div", className="section-kicker"),
        members_table(payload),
        about("Legs", "Each leg's prices as quoted (entry = its lots-weighted average fill), and the factor that "
                      "turns its price into the spread's unit.", level="div", className="section-kicker"),
        detail_legs_table(payload)])


# --------------------------------------------------------------------------- 3. closed spreads
def spread_record(spread: Dict[str, Any], position: int) -> Tuple[dict, dict]:
    """(record, tooltips) of one spread row, the engine's figures as they are."""
    tip: Dict[str, dict] = {}
    legs = spread.get("legs") or []
    rec: Dict[str, Any] = {
        "rank": position,
        "name": spread.get("name") or spread.get("spread_id") or "",
        "kind": kind_label(spread),
        "legs": legs_summary(legs),
    }
    size = _num(spread.get("size"))
    if size is None:
        rec["size"] = NA
        tip["size"] = _tip("no calendar or template fits all of its futures legs, so it has no size"
                           if spread.get("kind") in ("bundle", "pinned") else "spreads-engine gave no size")
    else:
        rec["size"] = size_text(size, spread.get("size_unit") or "")
    facts = [f"id {spread.get('spread_id') or ''}",
             f"trades {', '.join(spread.get('trade_ids') or [])}",
             f"account(s) {', '.join(spread.get('accounts') or [])}",
             f"traded {', '.join(spread.get('trade_dates') or [])}"]
    if spread.get("unit"):
        facts.append(f"quoted in {spread['unit']}")
    dev = _num(spread.get("deviation"))
    if dev is not None:
        facts.append(f"lots {dev:.1%} off the exact ratio")
    if spread.get("also_matches"):
        facts.append(f"also matches {', '.join(spread['also_matches'])}")
    tip["name"] = _tip("; ".join(facts))
    tip["legs"] = _tip("; ".join(f"{leg.get('instrument_id') or ''}: {signed(_num(leg.get('lots')))} lot(s), "
                                 f"{signed(_num(leg.get('open_lots')))} open" for leg in legs) or "no legs")
    period_cells(spread, rec, tip, full=True)
    text, usd, hover = leftover_cells(spread)
    rec["leftover"], rec["leftover_usd"] = text, usd
    tip["leftover"] = _tip(hover)
    tip["leftover_usd"] = _tip(f"{full_usd(usd)}. {hover}" if isinstance(usd, float) else hover)
    return rec, tip


def spread_columns() -> List[dict]:
    return ([rk.numeric("#", "rank", rk.count()), rk.text("Spread", "name"), rk.text("Kind", "kind"),
             rk.text("Size", "size"), rk.text("Legs (lots)", "legs")]
            + _period_columns(short=True)
            + [rk.text("Leftover outright (lots)", "leftover"),
               rk.numeric("Leftover USD", "leftover_usd", rk.amount_short(nully=""))])


_SUMMED = PERIODS + ("leftover_usd",)

_SPREAD_HEADER_TIPS = {
    "rank": "Largest |LTD| first, n/a last. Click to restore that order.",
    "size": "The spread's size with its unit: a calendar in lots of the near month; a template in its "
            "quantity unit (+ = long the spread as the template writes it).",
    "legs": "Each leg's net lots, in the engine's leg order.",
    "leftover": "What the open legs hold beyond the largest whole spread they make, per commodity.",
    "leftover_usd": "The leftover's USD notional: lots x multiplier x the day's price x spot, as spreads-engine "
                    "gives it, added over the commodities.",
    **{p: f"{PERIOD_TITLES[p]} P&L in USD, the legs' valuations added up by spreads-engine" for p in PERIODS},
}


def _table_styles(numeric_cols: Sequence[str], text_cols: Sequence[str], wide: Sequence[str] = (),
                  na_cols: Sequence[str] = (), one_line: Sequence[str] = (), compact: bool = False,
                  extra_na: Sequence[str] = ()) -> dict:
    """Shared table styles. `wide` columns wrap; `one_line` columns are cut with an ellipsis
    (their full text on hover); `compact` tightens the rows; `na_cols` are text columns and
    `extra_na` unsigned numeric ones whose "n/a" is greyed like the signed numeric ones'."""
    cell = dict(_MONO, padding="2px 8px") if compact else _MONO
    return dict(
        style_table={"overflowX": "auto"},
        style_cell=cell,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in text_cols]
                               + [{"if": {"column_id": c}, "whiteSpace": "normal", "minWidth": "220px",
                                   "maxWidth": "480px"} for c in wide]
                               + [{"if": {"column_id": c}, **_ONE_LINE, "maxWidth": "320px"} for c in one_line],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=rk.sign_styles(numeric_cols)
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in (*numeric_cols, *na_cols, *extra_na)],
        tooltip_delay=0, tooltip_duration=None,
    )


def spreads_table(spreads: Sequence[Dict[str, Any]], table_id: str) -> html.Div:
    """The ranked spreads table with its Total line pinned under it; the USD columns in k / m
    (rounded to whole units for display only), the full figure on hover."""
    recs = [spread_record(s, i) for i, s in enumerate(spreads, start=1)]
    records = [r for r, _ in recs]
    columns = spread_columns()
    numeric = [c["id"] for c in columns if c["type"] == "numeric" and c["id"] != "rank"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=table_id, columns=columns, data=rk.whole_units(records, _SUMMED), tooltip_data=[t for _, t in recs],
        tooltip_header=_SPREAD_HEADER_TIPS,
        **rk.sortable(table_id),
        **_table_styles(numeric, text, na_cols=("size",)),
        page_action="none",
    )
    footer, footer_tip = total_record(spreads, records, _SUMMED, "name", "legs", "spread")
    for col in _SUMMED:
        if isinstance(footer.get(col), float):
            rest = (footer_tip.get(col) or {}).get("value")
            footer_tip[col] = _tip(f"{full_usd(footer[col])}; {rest}" if rest else full_usd(footer[col]))
    footer = rk.whole_units([footer], _SUMMED)[0]
    total_style = [{"if": {"filter_query": f"{{name}} = '{TOTAL_LABEL}'"}, **_TOTAL_STYLE}]
    return pinned(rk.with_footer(table, [footer], footer_style=total_style, footer_tooltips=[footer_tip]))


# ---- the legs under each closed spread
def leg_record(leg: Dict[str, Any]) -> Tuple[dict, dict]:
    reason = leg.get("reason") or ""
    tip: Dict[str, dict] = {}
    product = leg.get("product") or ""
    month = leg.get("contract_month") or ""
    rec: Dict[str, Any] = {
        "contract": contract_label(leg.get("instrument_id")),
        "month": month or (NA if product == "FUTURE" else f"not a future ({product})" if product else NA),
        "product": product,
        "weight": _num(leg.get("weight")) if _num(leg.get("weight")) is not None else "",
        "lots": _num(leg.get("lots")), "open_lots": _num(leg.get("open_lots")),
        "currency": leg.get("currency") or "", "status": leg.get("status") or "",
        "trades": ", ".join(str(t) for t in (leg.get("trade_ids") or [])), "reason": reason,
    }
    if rec["month"] == NA:
        tip["month"] = _tip("contract-master gave no contract month for this contract")
    if rec["weight"] == "":
        rec["weight"] = NA
        tip["weight"] = _tip("no calendar or template fits these legs, so no weight")
    for col in ("lots", "open_lots"):
        if rec[col] is None:
            rec[col] = NA
            tip[col] = _tip(reason or "not a number on file")
    for col, what in (("pnl_local", "local P&L"), ("pnl_usd", "USD P&L")):
        v = _num(leg.get(col))
        rec[col] = NA if v is None else v
        if v is None:
            tip[col] = _tip(reason or f"spreads-engine gave no {what} and no reason")
    if reason:
        tip["contract"] = _tip(reason)
    return rec, tip


def legs_table(legs: Sequence[Dict[str, Any]]) -> dash_table.DataTable:
    recs = [leg_record(leg) for leg in legs]
    lots = rk.amount(2, nully="", trim=True)
    columns = [rk.text("Contract", "contract"), rk.text("Month", "month"), rk.text("Product", "product"),
               rk.numeric("Weight", "weight", rk.rate(4, trim=True)), rk.numeric("Lots", "lots", lots),
               rk.numeric("Open lots", "open_lots", lots), rk.text("Ccy", "currency"),
               rk.numeric("P&L (local)", "pnl_local", rk.amount(nully="")),
               rk.numeric("LTD USD", "pnl_usd", rk.amount(nully="")),
               rk.text("Status", "status"), rk.text("Trades", "trades"), rk.text("Reason", "reason")]
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    return dash_table.DataTable(
        columns=columns, data=[r for r, _ in recs], tooltip_data=[t for _, t in recs],
        **rk.sortable(), **_table_styles(numeric, text, wide=("reason",)), page_action="none")


def legs_block(spreads: Sequence[Dict[str, Any]], block_id: str) -> html.Div:
    """One collapsed block per spread, in the table's order, holding its legs."""
    blocks = []
    for n, s in enumerate(spreads, start=1):
        legs = s.get("legs") or []
        ltd = _num((s.get("pnl_usd") or {}).get("ltd"))
        figure = f"LTD USD {short_money(ltd)}" if ltd is not None else f"LTD {NA}"
        blocks.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"#{n} {s.get('name') or s.get('spread_id') or ''}: {legs_summary(legs)} "
                         f"({len(legs)} leg{'s' if len(legs) != 1 else ''}, {figure})"),
            legs_table(legs)]))
    return html.Div(id=block_id, className="spreads-legs", children=[
        about("Legs", LEGS_ABOUT, level="div", className="section-kicker"), *blocks])


def closed_section(result: Dict[str, Any]) -> Optional[html.Details]:
    """The closed spreads, collapsed below the open ones; None when there are none."""
    closed = ltd_order([s for s in (result.get("spreads") or []) if s.get("status") == "closed"])
    if not closed:
        return None
    return html.Details(id=CLOSED_ID, className="section section--secondary details", open=False, children=[
        html.Summary(f"Closed spreads ({len(closed)})", title=CLOSED_ABOUT, className="about-title"),
        spreads_table(closed, CLOSED_TABLE_ID), legs_block(closed, CLOSED_LEGS_ID)])


# --------------------------------------------------------------------------- 4. outrights
def outright_record(o: Dict[str, Any]) -> Tuple[dict, dict]:
    tip: Dict[str, dict] = {}
    review_ids = o.get("review_ids") or []
    rec: Dict[str, Any] = {
        "trade_id": o.get("trade_id") or "", "contract": contract_label(o.get("instrument_id")),
        "month": o.get("contract_month") or NA, "account": o.get("account") or "",
        "trade_date": o.get("trade_date") or "", "lots": _num(o.get("lots")),
        "currency": o.get("currency") or "", "status": o.get("status") or "",
        "why": o.get("why_outright") or "no spread fits",
        "review": f"yes ({len(review_ids)})" if review_ids else "",
    }
    tip["why"] = _tip(rec["why"])
    if rec["month"] == NA:
        tip["month"] = _tip("contract-master gave no contract month for this contract")
    if rec["lots"] is None:
        rec["lots"] = NA
        tip["lots"] = _tip("trades.quantity is not a number on file")
    local = _num(o.get("pnl_local"))
    rec["pnl_local"] = NA if local is None else local
    if local is None:
        tip["pnl_local"] = _tip((o.get("pnl_reasons") or {}).get("ltd") or "spreads-engine gave no local P&L")
    if review_ids:
        tip["review"] = _tip("listed under For review: " + "; ".join(review_ids))
    period_cells(o, rec, tip)
    return rec, tip


def outrights_section(result: Dict[str, Any]) -> html.Div:
    """The outright futures trades, one single-line row each (trade rows: full figures), a
    Total line pinned under them."""
    outrights = result.get("outrights") or []
    heading = about(f"Outrights ({len(outrights)})", OUTRIGHTS_ABOUT)
    if not outrights:
        return html.Div(id=OUTRIGHTS_ID, className="section", children=[
            heading, message_box("No outright futures trade.")])
    recs = [outright_record(o) for o in outrights]
    records = [r for r, _ in recs]
    lots = rk.amount(2, nully="", trim=True)
    columns = ([rk.text("Trade", "trade_id"), rk.text("Contract", "contract"), rk.text("Month", "month"),
                rk.text("Account", "account"), rk.text("Trade date", "trade_date"), rk.numeric("Lots", "lots", lots),
                rk.text("Ccy", "currency"), rk.text("Status", "status"),
                rk.numeric("P&L (local)", "pnl_local", rk.amount(nully=""))]
               + _period_columns()
               + [rk.text("Why outright", "why"), rk.text("For review", "review")])
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    text = [c["id"] for c in columns if c["type"] == "text"]
    table = dash_table.DataTable(
        id=OUTRIGHTS_TABLE_ID, columns=columns, data=records, tooltip_data=[t for _, t in recs],
        **rk.sortable(OUTRIGHTS_TABLE_ID), **_table_styles(numeric, text, one_line=("why",), compact=True),
        page_action="none")
    footer, footer_tip = total_record(outrights, records, PERIODS, "trade_id", "why", "trade")
    return html.Div(id=OUTRIGHTS_ID, className="section", children=[
        heading,
        pinned(rk.with_footer(table, [footer], footer_tooltips=[footer_tip], skip_widths=("why",),
                              footer_style=[{"if": {"filter_query": f"{{trade_id}} = '{TOTAL_LABEL}'"},
                                             **_TOTAL_STYLE}]))])


# --------------------------------------------------------------------------- 5. review
def review_record(r: Dict[str, Any]) -> Tuple[dict, dict]:
    """(fields, hover) of one group for review, the engine's as they are."""
    cands = r.get("candidates") or []
    rec = {
        "kind": REVIEW_LABELS.get(r.get("kind") or "", r.get("kind") or NA),
        "trades": ", ".join(r.get("trade_ids") or []),
        "contracts": ", ".join(contract_label(i) for i in (r.get("instruments") or [])),
        "accounts": ", ".join(r.get("accounts") or []),
        "trade_dates": ", ".join(r.get("trade_dates") or []),
        "candidates": "; ".join(c.get("name") or c.get("kind") or "" for c in cands),
        "reason": r.get("reason") or "",
    }
    tip = {"kind": _tip(r.get("reason") or ""), "candidates": _tip(
        "; ".join(f"{c.get('name') or c.get('kind')}: {', '.join(c.get('trade_ids') or [])}" for c in cands)
        or "no candidate listed")}
    return rec, tip


def review_deviation(r: Dict[str, Any]) -> Optional[float]:
    """The largest deviation spreads-engine gives among a group's candidates, or None."""
    devs = [d for d in (_num(c.get("deviation")) for c in (r.get("candidates") or [])) if d is not None]
    return max(devs) if devs else None


def review_line(r: Dict[str, Any]) -> html.Li:
    """One group for review on one line: kind, candidate, contracts, trades, account(s), trade
    date(s) and how far off the ratio is; the engine's full reason and candidates on hover."""
    rec, tip = review_record(r)
    words = " · ".join(w for w in (rec["candidates"] or "no candidate", rec["contracts"],
                                   f"trades {rec['trades']}" if rec["trades"] else "",
                                   rec["accounts"], rec["trade_dates"]) if w)
    dev = review_deviation(r)
    hover = rec["reason"] or "spreads-engine gave no reason"
    if tip["candidates"]["value"] != "no candidate listed":
        hover += f". Candidates: {tip['candidates']['value']}"
    return html.Li(title=hover, style={**_ONE_LINE, "cursor": "help"}, children=[
        html.Span(rec["kind"], className="issue-label"), " ", words,
        marker(f"off {dev:.1%}" if dev is not None and dev > 0 else "", hover)])


def review_section(result: Dict[str, Any]) -> Optional[html.Details]:
    """The groups for review as a collapsed drawer, "For review (N)", one line per group with
    its reason on hover, and the bundle hint; None when there is nothing for review (the
    caption's count says 0)."""
    review = result.get("review") or []
    if not review:
        return None
    return html.Details(id=REVIEW_ID, className="issues-drawer", open=False, children=[
        html.Summary(f"For review ({len(review)})", title=REVIEW_ABOUT),
        html.Ul(id=REVIEW_TABLE_ID, className="issues-list", children=[review_line(r) for r in review]),
        html.Div(BUNDLE_HINT, className="section-kicker", style={"margin": "4px 0 0"})])


# --------------------------------------------------------------------------- body and shell
def is_empty(result: Dict[str, Any]) -> bool:
    return not (result.get("spreads") or result.get("outrights") or result.get("review"))


def body(result: Dict[str, Any], research: Optional[Dict[str, Any]] = None, selected: Optional[str] = None,
         history_fn: Callable = research_spread_history) -> html.Div:
    """The whole tab body from one `book_spreads` result and one `research_spread_stats` result
    (None: the research columns read n/a, "not read"); `selected` is the position whose
    drill-down is open."""
    research = research if research is not None else empty_research()
    children: List[Any] = [caption_block(result, research)]
    if is_empty(result):
        children.append(message_box(
            f"No commodity futures in the book on {result.get('as_of') or 'this date'}: "
            "no spread, outright or group for review to show."))
        return html.Div(className="spreads-body", children=children)
    children.append(positions_section(result, research, selected, history_fn))
    closed = closed_section(result)
    if closed is not None:
        children.append(closed)
    children.append(outrights_section(result))
    review = review_section(result)
    if review is not None:
        children.append(review)
    return html.Div(className="spreads-body", children=children)


def spreads_for(conn: sqlite3.Connection, as_of: str) -> Dict[str, Any]:
    """`book_spreads` through the screens' shared filled reader, one pricing snapshot."""
    from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
    with pricing_snapshot(conn, "Spreads tab"):
        return book_spreads(conn, as_of, value_fn=priced_value_book)


def render(as_of: Optional[str], db_path, selected: Optional[str] = None) -> Any:
    """The body for `as_of` from the database at `db_path`: one `book_spreads` call on a
    read-only connection, closed straight after, then one `research_spread_stats` call. A
    problem is a message where the body would be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        result = spreads_for(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The spreads could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result, research_for(result, as_of), selected)


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row (its definitions on hover), the body container the
    callback fills and the store of the position open in the drill-down. No date picker: the
    tab follows the header's as-of store, and the body's first line names the date."""
    follows = f"Follows the header's as-of date{f' ({default_date} when the page loaded)' if default_date else ''}."
    return html.Div(className="spreads-tab", children=[
        html.Div(className="ladder-title-row", children=[
            about("Spreads", f"{TITLE_ABOUT} {follows}", level="h3", className="ladder-title-row-heading")]),
        html.Div(id=BODY_ID, children=[message_box("Loading the spreads...")]),
        dcc.Store(id=SELECTED_STORE_ID, data=None),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def open_detail(active_cell: Optional[dict], payloads: Optional[dict], as_of: Optional[str],
                history_fn: Callable = research_spread_history) -> Tuple[Any, str]:
    """(the drill-down panel, the position id) for a clicked cell of the positions table;
    PreventUpdate when the click names no open position (a table just rebuilt has no active
    cell, and the body has already opened the kept position)."""
    pid = (active_cell or {}).get("row_id")
    if not pid or pid not in (payloads or {}):
        raise PreventUpdate
    return detail_panel(payloads[pid], as_of, history_fn), pid


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Two callbacks: the body re-renders on the header's as-of, on every data revision and on
    the safety interval (reopening the drill-down that was open); a click on a position row
    opens its drill-down and remembers it."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
        State(SELECTED_STORE_ID, "data"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0, selected=None):
        return render(as_of, get_db_path(), selected)

    @app.callback(
        Output(DETAIL_ID, "children"),
        Output(SELECTED_STORE_ID, "data"),
        Input(TABLE_ID, "active_cell"),
        State(DETAIL_STORE_ID, "data"),
        State(AS_OF_STORE_ID, "data"),
        prevent_initial_call=True,
    )
    def _drill(active_cell, payloads, as_of):
        return open_detail(active_cell, payloads, as_of)
