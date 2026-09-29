"""Risk tab: "How much can I lose, and which trades are using up my risk?" Four blocks, top
to bottom (user, 2026-09-29, after putting every measure through four tests: it answers a real
question, it changes a decision, it is not derivable from another figure on the screen, it can
be trusted), rendered from the engines and nothing else. Every figure is
`engine.risk.book_risk`'s dict (its `position_risk` block, `engine/risk/positions.py`, its
commodity and FX scenarios), `engine.limits.margin_estimate`'s or `engine.limits.limit_checks`';
nothing here recomputes a metric, a delta, a scenario, a margin or P&L (CLAUDE.md "Tabs as
views"): the screen sums known figures of one unit into a group line, ranks and formats.

  0. With no trade on file the tab is the Book's "No blotter loaded" card (`empty_state`,
     one pattern id per tab), nothing else (user, 2026-09-28).
  1. `headline_block`: the 1-day VaR (95 %, last 252 days) = `position_risk.headline_var` (the
     book VaR over every underlyer, `book.var95_1d_usd`) in k / m, the full figure and the
     definition on hover; beside it the share of the vol target the book's blended vol uses
     (`book.vol_vs_target_pct`, the target on hover, "placeholder" while config/risk.yaml says
     so); one line under them: the price history's reach and source in plain words.
  2. `positions_block`: risk by position, one row per position of `position_risk.positions`
     (the Book's own positions: a strategy, a spread, an outright contract, a trade), its
     contribution to the book's VaR (the standalone VaR on the cell's hover), its share and a
     small bar (negative = it hedges the book, drawn left). A position counted without a
     currency hedge that has no price history carries a grey "without hedge" marker. A switch
     Position | Spread type | Commodity (`GROUP_ID`): the three tables are rendered at once and
     a clientside callback shows one; the switch keeps its value across re-renders (Dash
     persistence). Under the table: the diversification line, the positions shown without
     their hedge and the positions not included, each one quiet line.
  (`liquidity_block`: the place kept for the liquidity block of a later wave; None today.)
  3. `correlation_block`: the positions' correlation heat map (-1 red, 0 white, +1 blue, the
     value in each cell), or one quiet line with the engine's reason.
  4. `stress_block`: ONE ranked list of scenarios, the commodity scenarios (designed and
     historical replays), the FX scenarios and the book's worst day in history, worst book
     P&L first: the five worst shown, the rest in a closed fold. Each row: the name, its kind
     (designed / historical replay / FX, "placeholder" in grey where the scenarios are
     placeholders), the book P&L and the position hit hardest where the engine attributes it.
  Margin and limits: hidden entirely while every limit is NOT_SET and the margin rates are
  placeholders (one quiet line says so); folded as before once either is real
  (`margin_limits_section`).
  Then the tab's one "Data issues (N)" drawer (`issue_items`): the histories used or the
  folders tried, the parameters, the positions not included, the scenarios not valued.

Removed on 2026-09-29 (the user's cull): the blended vol card, the worst day raw and ex shocks
cards and the stress cap, the per-underlyer "Risk by sector and commodity" table, vol and worst
day per position, the "Views" fold, the separate FX scenario fold and its currency matrix.

Every engine reason reaches the screen through `plain_reason`: shortest cause first, never a
file path in a cell or caption (the folders tried are one line of the drawer). The macro
trader's rates and equity-index underlyers never show (`RETIRED_KINDS`).

The tab has no date picker: it follows the header's as-of store
(`ui.tabs.header.AS_OF_STORE_ID`) and re-renders in place on the data revision
(`ui/revision.py`) and on its own safety interval (the histories live outside the database).
`layout(default_date)` and `register_callbacks(app, get_db_path)` are the shell's interface.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

import plotly.graph_objects as go
from dash import Input, Output, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Symbol

from engine.risk import book_risk
from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.book import empty_state, trades_on_file
from ui.tabs.formatting import (MINUS, MISSING, about, contract_name, format_cell, fx_name, issues_drawer, lme_name,
                                marker, missing_cell, short_money, signed_money, spread_name, sum_known,
                                trade_type_words)
from ui.tabs.header import AS_OF_STORE_ID

BODY_ID = "risk-body"
REFRESH_ID = "risk-refresh"
HEADLINE_ID = "risk-headline"
POSITIONS_ID = "risk-positions"
GROUP_ID = "risk-group-by"
GROUP_POSITION, GROUP_TYPE, GROUP_COMMODITY = "position", "type", "commodity"
GROUP_OPTIONS = ((GROUP_POSITION, "Position"), (GROUP_TYPE, "Spread type"), (GROUP_COMMODITY, "Commodity"))
GROUP_TABLE_IDS = {GROUP_POSITION: "risk-by-position", GROUP_TYPE: "risk-by-type", GROUP_COMMODITY: "risk-by-commodity"}
DIVERSIFICATION_ID = "risk-diversification"
EXCLUDED_ID = "risk-excluded"
PARTIAL_ID = "risk-partial"
CORRELATION_ID = "risk-correlation"
STRESS_ID = "risk-stress"
STRESS_MORE_ID = "risk-stress-more"
MARGIN_TABLE_ID = "risk-margin-table"
MARGIN_ROOT_TABLE_ID = "risk-margin-root-table"
MARGIN_SPREAD_TABLE_ID = "risk-margin-spread-table"
LIMITS_TABLE_ID = "risk-limits-table"
MARGIN_LIMITS_ID = "risk-margin-limits"
LIMITS_NOT_SET_ID = "risk-limits-not-set"
ISSUES_ID = "risk-issues"

NA = MISSING          # an em dash, never the text "n/a" (the display rule of 2026-09-28)
# underlyer kinds of the macro book, removed 2026-09-24: never rendered, nor their `missing` entries
RETIRED_KINDS = ("RATES", "EQUITY_INDEX")
RETIRED_MISSING_PREFIXES = ("rates:", "equity index:")
RETIRED_HISTORY_FILES = ("swap_rates",)      # the rates rows' par swap rate history
MARGIN_BASIS = "estimate, not exchange SPAN"
# limit levels (engine.limits.checks) as shown, and their colours: the Expiries tab's palette
LEVEL_TEXT = {"BREACH": "BREACH", "WARN": "WARN", "OK": "OK", "NOT_SET": "not set yet", "N/A": NA}
LEVEL_STYLES = {
    "BREACH": {"backgroundColor": "#c62828", "color": "#ffffff", "fontWeight": "700"},
    "WARN": {"backgroundColor": "#fff3e0", "color": "#b26a00", "fontWeight": "700"},
    "OK": {"backgroundColor": "#f1f5f1", "color": "#1a7f4b"},
    "NOT_SET": {"backgroundColor": "#eef0f3", "color": "#6b7280", "fontStyle": "italic"},
    "N/A": {"color": "var(--muted)", "fontStyle": "italic"},
}
_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
# one line, clipped with an ellipsis: a long name or note never makes a row taller
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
_SEP = " · "                              # a middle dot between the parts of a line
_HIDDEN = {"display": "none"}
STRESS_SHOWN = 5                               # the worst scenarios in sight; the rest folded
FX_KINDS = ("fx_spot", "fx_fwd", "fx_swap", "fx_option")
FX_GROUP, OTHER_GROUP, NO_TYPE_GROUP, MIXED_TYPE_GROUP = "FX hedges", "Other", "No spread type", "Mixed types"
FX_SECTOR = "fx"                               # the SGX USD/CNH future's sector in config/contracts.csv
HISTORY_SOURCE = "research app"                # where the commodity price history comes from, in words


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    """A finite float, or None for None / NaN / a non-number."""
    v = rk.value(value)
    return v if isinstance(v, float) and math.isfinite(v) else None


def _sector_words(sector: Any) -> str:
    """'Energy' for the engine's 'energy': every sector label in one case."""
    s = str(sector or "").replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else ""


def _usd(value: Any) -> str:
    v = _num(value)
    return NA if v is None else format_cell(v)


def _money(value: Any) -> str:
    """A money figure in k / m for the cards and captions ("1.65m", "(78.1k)"), n/a for none."""
    v = _num(value)
    return NA if v is None else short_money(v)


def _pct(value: Any, decimals: int = 1) -> str:
    v = _num(value)
    return NA if v is None else f"{v:,.{decimals}f}%"


def _lots(value: Any) -> str:
    v = _num(value)
    return NA if v is None else f"{v:,.2f}".rstrip("0").rstrip(".")


# The engine's reasons in plain words (user, 2026-09-28): a segment (the "; "-separated parts
# of a reason) that starts with a key is replaced whole by its words; None = pass through as
# it is (a more specific prefix listed before a shorter one it would otherwise match).
_PLAIN_REASONS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("no market history", "no FX price history on this PC"),
    ("no commodity history on or before", None),
    ("no commodity history", "no commodity price history"),
    ("no commodity row has a history series", "no commodity price history"),
    ("no open commodity futures", "no positions"),
    ("no currency or metal position", "no positions"),
    ("no commodity position", "no positions"),
)
_PATH_TAIL = re.compile(r"[:(]?\s*tried\s+[A-Za-z]:\\.*$|[:(]?\s*tried\s+/.*$")
# engine words in a reason or a note, and their plain words (applied after the causes)
_PLAIN_WORDS = (("not in the book series", "not in the book's figures"), ("' series", "' figures"),
                ("'s series", "'s figures"), ("COMMODITY:", ""))


def plain_reason(text: Any) -> str:
    """`text` (an engine reason) in plain words: in each "; " segment a known cause
    (`_PLAIN_REASONS`), at its start or inside it ("EUR: not in the book series (no market
    history: ...)"), becomes its short words to the end of the segment (a parenthesis it was
    in is closed again); a segment that names the folders tried loses that tail; the engine's
    words in `_PLAIN_WORDS` become plain ones; an unknown segment passes through unchanged;
    repeats are dropped."""
    out: List[str] = []
    for seg in str(text or "").split("; "):
        seg = seg.strip()
        if not seg:
            continue
        for prefix, words in _PLAIN_REASONS:
            i = seg.find(prefix)
            if i >= 0:
                if words is not None:
                    head = seg[:i]
                    seg = head + words + (")" if head.count("(") > head.count(")") else "")
                break
        else:
            seg = _PATH_TAIL.sub("", seg).rstrip(" :(")
        for engine_words, plain in _PLAIN_WORDS:
            seg = seg.replace(engine_words, plain)
        if seg and seg not in out:
            out.append(seg)
    return "; ".join(out)


def _why(entry: Dict[str, Any], metric: str) -> str:
    """Why `metric` is NaN on a row or the book, in plain words: the metric's own reason,
    else the row's."""
    reasons = entry.get("reasons") or {}
    return plain_reason(reasons.get(metric) or reasons.get("all") or entry.get("reason") or "")


def _month_words(iso: Optional[str]) -> str:
    """'Sep 2020' for '2020-09-28'; the text itself when it is not a date."""
    if not iso:
        return ""
    try:
        return f"{dt.date.fromisoformat(iso):%b %Y}"
    except ValueError:
        return str(iso)


def _day_words(iso: Optional[str]) -> str:
    """'6 May 2022' for '2022-05-06'; the text itself when it is not a date."""
    if not iso:
        return ""
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return str(iso)
    return f"{d.day} {d:%b %Y}"


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%A} {d.day} {d:%B %Y} ({iso})"


def _pct_format(decimals: int = 1, nully: str = "") -> dict:
    """An unsigned per cent at fixed decimals: 27.8% (a share of a target is never signed)."""
    return Format(precision=decimals, scheme=Scheme.fixed, nully=nully).symbol(Symbol.yes).symbol_suffix("%").to_plotly_json()


def _na_styles(columns) -> List[dict]:
    return [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE} for c in columns]


def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


# The margin and limits hovers' own pass (2026-09-28): the path of the limits file, which the
# engine's reasons, basis and notes name, becomes "the limits file" (the user fills it in).
# Kept out of `plain_reason` and `formatting.plain_words` deliberately, so nothing else changes.
_LIMITS_FILE_WORDS = (("in config/limits.yaml", "in the limits file"), ("config/limits.yaml", "the limits file"))


def _limits_words(text: Any) -> str:
    out = str(text or "")
    for engine_words, plain in _LIMITS_FILE_WORDS:
        out = out.replace(engine_words, plain)
    return out


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def shown_missing(result: Dict[str, Any]) -> List[str]:
    """The engine's `missing` list less the retired rows' entries: those with a retired
    prefix, and those naming a retired underlyer ("SPX: not in the scenarios (...)")."""
    retired = tuple(f"{r.get('underlyer')}:" for r in (result.get("underlyers") or [])
                    if r.get("kind") in RETIRED_KINDS and r.get("underlyer"))
    return [m for m in (result.get("missing") or [])
            if m and not m.startswith(RETIRED_MISSING_PREFIXES) and not (retired and m.startswith(retired))]



def _weight(w: Any) -> str:
    v = _num(w)
    if v is None:
        return NA
    for num, den in ((1, 2), (1, 3), (2, 3), (1, 4), (3, 4)):
        if abs(v - num / den) < 1e-6:
            return f"{num}/{den}"
    return f"{v:.2f}"


# --------------------------------------------------------------------------- the histories, in words
def commodity_history_line(result: Dict[str, Any]) -> Optional[str]:
    """The commodity settlement history's sentence, or None when the engine gave no block."""
    ch = result.get("commodity_history")
    if not ch:
        return None
    if ch.get("available"):
        s = (f"Commodity history: {ch.get('path')}, settlements {ch.get('first_date') or NA} to "
             f"{ch.get('last_date') or NA} ({ch.get('roots', 0)} roots, {ch.get('contracts', 0)} contracts)")
        if ch.get("used_to"):
            s += f"; used to {ch['used_to']}, lag-2 date {ch.get('lag2_date')}"
        if ch.get("fx_pairs"):
            s += f"; USD conversion pairs {', '.join(ch['fx_pairs'])}"
        return s + ". Read-only, a risk input only: nothing from it is a mark or enters P&L."
    return (f"Commodity history unavailable: {ch.get('reason') or 'no reason given'}. The commodity rows' risk "
            "figures are dashes; their positions, the margin and the scenarios that need no history stand.")


def commodity_history_words(result: Dict[str, Any]) -> str:
    """The commodity price history in plain words for the caption's hover: no path (the file
    is `commodity_history_line`'s, in the Notes drawer)."""
    ch = result.get("commodity_history") or {}
    if not ch:
        return ""
    if not ch.get("available"):
        return (f"No commodity price history: {plain_reason(ch.get('reason')) or 'no reason given'}. The commodity "
                "rows' risk figures are dashes; their positions, the margin and the scenarios that need no history "
                "stand. The files tried are in the Notes drawer.")
    s = (f"Commodity price history: settlements {_day_words(ch.get('first_date')) or NA} to "
         f"{_day_words(ch.get('last_date')) or NA} ({ch.get('roots', 0)} commodities, {ch.get('contracts', 0)} contracts)")
    if ch.get("used_to"):
        s += f", used to {_day_words(ch['used_to'])}, valued two business days back at {_day_words(ch.get('lag2_date'))}"
    return s + ". Read-only, a risk input only: nothing from it is a mark or enters P&L. The file is in the Notes drawer."


def history_words(result: Dict[str, Any]) -> str:
    """The FX price history in plain words for a hover: no path (those are `history_line`'s,
    in the Notes drawer)."""
    h = result.get("history") or {}
    if not h.get("available"):
        return ("No FX price history on this PC: every currency and metal risk figure is a dash; the positions "
                "and the scenarios stand. The folders tried are in the Notes drawer.")
    s = f"FX price history to {_day_words(h.get('last_date'))}"
    if h.get("used_to"):
        s += f", used to {_day_words(h['used_to'])}, valued two business days back at {_day_words(h.get('lag2_date'))}"
    return s + "."


def history_line(result: Dict[str, Any]) -> str:
    """The FX / metal market history's sentence for the Notes drawer: the folder, its last
    close, the lag-2 date and its files, or why there is none (the folders tried, the one
    place a path is shown)."""
    h = result.get("history") or {}
    if not h.get("available"):
        return (f"FX price history: none on this PC ({h.get('reason') or 'no reason given'}). Every currency and "
                "metal risk figure is a dash; the positions and the scenarios stand.")
    s = f"FX price history: {h.get('path')}, last close {h.get('last_date')}"
    if h.get("used_to"):
        s += f"; used to {h['used_to']}, lag-2 date {h.get('lag2_date')}"
    files = []
    for key, f in (h.get("files") or {}).items():
        if key in RETIRED_HISTORY_FILES:
            continue
        state = (f"{f.get('first_date')} to {f.get('last_date')}, {f.get('rows')} rows" if f.get("loaded")
                 else f"not loaded ({f.get('reason') or 'no reason given'})")
        files.append(f"{key}: {f.get('file')} ({state})")
    return s + "." + (f" Files: {'; '.join(files)}." if files else "")


def parameters_line(config: Dict[str, Any]) -> str:
    """Every parameter the figures use, in one sentence, with the file they came from."""
    b = config.get("blended") or {}
    shocks = "; ".join(f"{d.get('date', '')} {d.get('name', '')}".strip() for d in (config.get("shock_dates") or [])) or "none"
    return (f"vol target {_usd(config.get('vol_target_usd'))}"
            + (" (placeholder)" if config.get("vol_target_placeholder") else "")
            + f"; blended vol {_weight(b.get('w_trail'))} trailing "
            f"({b.get('trail_window_bd')} bd) + {_weight(b.get('w_stress'))} crisis ({b.get('stress_start')} to "
            f"{b.get('stress_end')}), cutover {b.get('cutover')}; VaR window {config.get('var_window_bd')} bd at "
            f"{float(config.get('var_confidence') or 0) * 100:g}%; worst day from {config.get('worst_day_start')}; "
            f"shock dates: {shocks}. Read from {config.get('file') or 'config/risk.yaml'}"
            + (" (loaded)" if config.get("loaded") else f" (not loaded: {config.get('note') or 'defaults in use'})") + ".")


def _clip(column_id: str, width: str) -> dict:
    """A text column held to one line at `width`, the overflow clipped with an ellipsis (its
    full text is the cell's tooltip)."""
    return {"if": {"column_id": column_id}, "width": width, "minWidth": width, "maxWidth": width, **_ONE_LINE}



def fold(title: str, count: str, hover: str, children: List[Any], *, id: Optional[str] = None) -> html.Details:
    """A section folded by default (user, 2026-09-28: one glance, then unfold): an
    `html.Details`, closed, whose summary line is the title with its `count` beside it and
    the definitions on hover of the title (`about`), the section's content inside as it is."""
    summary = html.Summary([about(title, hover, level="span"), html.Span(f" ({count})" if count else "", className="fold-count")],
                           className="fold-summary")
    extra = {"id": id} if id else {}
    return html.Details([summary] + [c for c in children if c is not None], className="details details--fold", open=False,
                        style={"background": "var(--card)", "border": "1px solid var(--line)", "borderRadius": "8px",
                               "padding": "6px 16px", "margin": "0 0 12px"}, **extra)





# --------------------------------------------------------------------------- the book's context
def position_context(conn: sqlite3.Connection) -> Dict[str, Any]:
    """What the screen needs to name and group the engine's positions, read once per render:
    {instruments: {id: {base, quote, strike, option_type}}, trade_types: {trade_id: the broker's
    label}, roots: the contract universe}. Nothing is priced; a read that fails leaves its map
    empty (the engine's own names and "No spread type" stand)."""
    instruments: Dict[str, dict] = {}
    types: Dict[str, str] = {}
    try:
        for inst, base, quote in conn.execute("SELECT instrument_id, base_ccy, quote_ccy FROM instruments"):
            instruments[str(inst)] = {"base": str(base or ""), "quote": str(quote or ""), "strike": None, "option_type": ""}
        for inst, strike, otype in conn.execute("SELECT instrument_id, strike, option_type FROM instrument_options"):
            if str(inst) in instruments:
                instruments[str(inst)]["strike"] = _num(strike) or None
                instruments[str(inst)]["option_type"] = str(otype or "")
    except sqlite3.Error:
        pass
    try:
        types = {str(t): str(c or "") for t, c in conn.execute("SELECT trade_id, trade_type FROM trades")}
    except sqlite3.Error:
        types = {}
    try:
        from data.contracts import load_roots
        roots = dict(load_roots())
    except Exception:  # noqa: BLE001 -- the root id stands for its name
        roots = {}
    return {"instruments": instruments, "trade_types": types, "roots": roots}


def _root_of(contract_id: Any, ctx: Dict[str, Any]) -> str:
    """The contract root id of a leg ('NYMEX:CL'): the instrument's base_ccy for a future, an
    option on one or an LME metal; else the id's own prefix ('LME:CA 2026-12-10')."""
    cid = str(contract_id or "")
    base = ((ctx.get("instruments") or {}).get(cid) or {}).get("base", "")
    if ":" in base:
        return base
    head = cid.split(" ")[0]
    return head if ":" in head else ""


def _contract_words(contract_id: Any, root_id: str, roots: Dict[str, Any]) -> str:
    """A contract in plain words: 'WTI Dec26', 'LME copper 10 Dec' for an LME prompt."""
    cid = str(contract_id or "")
    head, _, prompt = cid.partition(" ")
    if head.startswith("LME:") and prompt[:4].isdigit():
        return lme_name(roots.get(head), head, prompt)
    return contract_name(cid, roots.get(root_id), root_id)


def position_name(p: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """The position in plain words, the Book's names: a strategy, bundle or pin by its own name;
    a calendar 'WTI Dec26/Jan27'; a template 'Brent–WTI Dec26', '3-2-1 crack Nov26'; an outright
    'COMEX gold Dec26'; an LME ticket 'LME copper 10 Dec'; an FX trade 'USDCNH 18 Nov forward'.
    The engine's name when the parts do not parse."""
    kind = str(p.get("kind") or "")
    name = str(p.get("name") or p.get("position_id") or "")
    roots = ctx.get("roots") or {}
    legs = [leg for leg in (p.get("legs") or []) if leg.get("contract_id")]
    try:
        if kind in ("strategy", "bundle", "pinned"):
            return name
        if kind in FX_KINDS:
            inst, _, date = name.rpartition(" ")
            info = (ctx.get("instruments") or {}).get(inst) or {}
            pair = (info.get("base", "") + info.get("quote", "")) or inst
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                return name
            return fx_name(pair, kind.upper(), date, info.get("option_type", ""), info.get("strike"))
        if len(legs) == 1:
            cid = legs[0]["contract_id"]
            return _contract_words(cid, _root_of(cid, ctx), roots)
        if legs:
            return spread_name({"kind": kind, "template": kind if "." in kind else "", "name": name,
                                "legs": [{"instrument_id": leg["contract_id"], "root_id": _root_of(leg["contract_id"], ctx)}
                                         for leg in legs]}, roots)
    except Exception:  # noqa: BLE001 -- the engine's own name stands
        return name
    return name


def position_type(p: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """The position's spread type code (CROSS_EXCHANGE | CROSS_PRODUCT | TERM_STRUCTURE): the
    engine's `trade_type` on the position when it gives one, else the broker's labels of its
    trades (one label -> that; several -> 'MIXED'; none -> '')."""
    if p.get("trade_type") is not None:
        return str(p.get("trade_type") or "")
    labels = {(ctx.get("trade_types") or {}).get(str(t), "") for t in (p.get("trade_ids") or [])} - {""}
    if len(labels) == 1:
        return labels.pop()
    return "MIXED" if labels else ""


def type_group(p: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    kind = str(p.get("kind") or "")
    if kind in FX_KINDS:
        return FX_GROUP
    code = position_type(p, ctx)
    if code == "MIXED":
        return MIXED_TYPE_GROUP
    words = trade_type_words(code)
    return words[:1].upper() + words[1:] if words else NO_TYPE_GROUP


def commodity_group(p: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """The commodity across exchanges ('Copper', 'Crude oil / Gasoline' for a position over two),
    its currency-hedge legs left aside; FX hedges for an FX trade or the SGX USD/CNH future."""
    kind = str(p.get("kind") or "")
    if kind in FX_KINDS:
        return FX_GROUP
    roots = ctx.get("roots") or {}
    names: List[str] = []
    hedge_only = True
    for leg in p.get("legs") or []:
        if leg.get("hedge"):
            continue
        rid = _root_of(leg.get("contract_id"), ctx)
        root = roots.get(rid)
        if root is None:
            words = OTHER_GROUP
        elif str(getattr(root, "sector", "") or "") == FX_SECTOR:
            words = FX_GROUP
        else:
            hedge_only = False
            try:
                from engine.curve import subsector_name
                words = subsector_name(str(getattr(root, "subsector", "") or "")) or OTHER_GROUP
            except Exception:  # noqa: BLE001
                words = str(getattr(root, "subsector", "") or OTHER_GROUP).replace("_", " ").capitalize()
        if words not in names:
            names.append(words)
    if not names:
        return FX_GROUP if (p.get("legs") and hedge_only) else OTHER_GROUP
    commodities = [n for n in names if n not in (FX_GROUP, OTHER_GROUP)]
    return " / ".join(commodities) if commodities else names[0]


# --------------------------------------------------------------------------- shared bits
def _section_head(title: str, hover: str, *extra: Any) -> html.Div:
    return html.Div([about(title, hover, level="h4", className="risk-h")] + [e for e in extra if e is not None],
                    className="risk-section-head")


def _quiet(children: Any, hover: str = "", id: Optional[str] = None) -> html.Div:
    extra = {"id": id} if id else {}
    return html.Div(children, className="risk-quiet", title=plain_reason(hover) if hover else None, **extra)


def _signed_cls(value: Optional[float]) -> str:
    if value is None or abs(value) < 0.5:
        return ""
    return "cell-neg" if value < 0 else "cell-pos"


def _share_text(share: Optional[float]) -> str:
    """A share of the book's VaR as a whole per cent: '62%', '−13%' (the fraction x 100: display)."""
    if share is None:
        return NA
    pct = share * 100.0
    text = f"{abs(pct):.0f}%"
    return (MINUS + text) if pct < 0 and text != "0%" else text


def _bar(share: Optional[float], scale: float) -> html.Div:
    """A small horizontal bar for a share: to the right of the centre line for a position that
    adds to the book's VaR, to the left for one that hedges it ("hedges the book" on hover)."""
    if share is None or scale <= 0:
        return html.Div(className="risk-bar")
    width = min(abs(share) / scale, 1.0) * 50.0
    neg = share < 0
    fill = html.Span(className="risk-bar-fill " + ("risk-bar-fill--neg" if neg else "risk-bar-fill--pos"),
                     style={"width": f"{width:.1f}%"})
    return html.Div(fill, className="risk-bar", title="hedges the book: it tends to gain on the book's bad days" if neg
                    else "adds to the book's risk")


# --------------------------------------------------------------------------- 1. headline
def _conf_words(config: Dict[str, Any]) -> Tuple[str, Any]:
    conf = float(config.get("var_confidence") or 0.95) * 100
    return f"{conf:g} %", config.get("var_window_bd") or 252


def var_definition(config: Dict[str, Any]) -> str:
    conf, window = _conf_words(config)
    return (f"1-day VaR at {conf}: minus the {100 - float(conf.split()[0]):g}th percentile of today's book's daily "
            f"P&L over the last {window} trading days, the book held as it is today across the history: a typical "
            "bad day, a positive figure is a loss. Every position with a price history is in it (currencies and "
            "metals too when their history is on this PC).")


def history_reach_line(result: Dict[str, Any]) -> html.Div:
    """One line: the price history's reach and source in plain words ('commodity prices to 28
    Sep 2026, research app · no FX price history on this PC'), 'mock data' when the engine says
    so; the full sentences on hover."""
    h = result.get("history") or {}
    ch = result.get("commodity_history") or {}
    parts: List[str] = []
    if ch:
        parts.append(f"commodity prices to {_day_words(ch.get('last_date'))}, {HISTORY_SOURCE}" if ch.get("available")
                     else "no commodity price history")
    parts.append(f"FX prices to {_day_words(h.get('last_date'))}" if h.get("available") else "no FX price history on this PC")
    if ch.get("mock") or h.get("mock"):
        parts.append("mock data")
    hover = " ".join(x for x in (commodity_history_words(result), history_words(result)) if x)
    return html.Div(_SEP.join(p for p in parts if p), className="risk-reach", title=hover)


def headline_block(result: Dict[str, Any]) -> html.Div:
    """The 1-day VaR and the share of the vol target used, then the history's reach."""
    book = result.get("book") or {}
    config = result.get("config") or {}
    pr = result.get("position_risk") or {}
    conf, window = _conf_words(config)
    var = _num(pr.get("headline_var")) if "headline_var" in pr else None
    if var is None:
        var = _num(book.get("var95_1d_usd"))
    left_out = [plain_reason(m) for m in shown_missing(result)]
    if var is None:
        why = _why(book, "var95_1d_usd") or "not available"
        var_figure: List[Any] = [html.Span(NA, className="card-value card-value--muted", title=why)]
        var_note = html.Span(why, className="card-note", title=why, style=_ONE_LINE)
    else:
        var_figure = [html.Span(short_money(var, "$"), className="card-value", title=format_cell(var)),
                      marker(f"excl. {len(left_out)}", "not in this figure: " + "; ".join(left_out)) if left_out else None]
        var_note = html.Span(f"a typical bad day over the last {window} trading days", className="card-note", style=_ONE_LINE)
    target = _num(config.get("vol_target_usd"))
    vol = _num(book.get("vol_blended_ann_usd"))
    pct = _num(book.get("vol_vs_target_pct"))
    placeholder = bool(config.get("vol_target_placeholder"))
    target_hover = (f"Blended annual vol {format_cell(vol) if vol is not None else NA} against the vol target of "
                    f"{format_cell(target) if target is not None else NA}"
                    + (f" ({config.get('vol_target_note')})" if config.get("vol_target_note") else "") + ". "
                    "Blended vol = 2/3 x the trailing 500-day vol + 1/3 x the 2008-2010 crisis vol, annualised"
                    + (f"; {book.get('vol_note')}" if book.get("vol_note") else "") + ".")
    if pct is None:
        why = _why(book, "vol_blended_ann_usd") or "not available"
        vol_figure: List[Any] = [html.Span(NA, className="card-value card-value--muted", title=why)]
    else:
        vol_figure = [html.Span(f"{pct:.0f}%", className="card-value", title=f"{pct:.1f}%",
                                style={"color": "var(--neg)"} if book.get("over_vol_target") else {}),
                      html.Span("over the target", className="tag") if book.get("over_vol_target") else None]
    vol_note = html.Span([f"of the {short_money(target, '$') if target is not None else NA} vol target",
                          html.Span(" placeholder", className="risk-grey") if placeholder else None],
                         className="card-note", style=_ONE_LINE)
    row = {"display": "flex", "alignItems": "baseline", "gap": "6px", "whiteSpace": "nowrap"}
    cards = [
        html.Div([html.Span(f"1-day VaR ({conf}, last {window} days)", className="card-label"),
                  html.Div([f for f in var_figure if f is not None], style=row), var_note],
                 className="card", title=var_definition(config)),
        html.Div([html.Span("Vol target used", className="card-label"),
                  html.Div([f for f in vol_figure if f is not None], style=row), vol_note],
                 className="card", title=target_hover),
    ]
    return html.Div([html.Div(cards, className="cards cards--two"), history_reach_line(result)],
                    id=HEADLINE_ID, className="risk-headline")


# --------------------------------------------------------------------------- 2. risk by position
def _legs_hover(p: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    roots = ctx.get("roots") or {}
    lines = []
    for leg in p.get("legs") or []:
        cid = leg.get("contract_id")
        lots, delta = _num(leg.get("lots")), _num(leg.get("delta_lots"))
        words = _contract_words(cid, _root_of(cid, ctx), roots)
        size = f"{lots:+,.4g} lots" if lots is not None else "lots not known"
        if delta is not None and lots is not None and abs(delta - lots) > 1e-9:
            size += f" (delta {delta:+,.4g})"
        extra = " · currency hedge" if leg.get("hedge") else ""
        why = plain_reason(leg.get("reason")) if leg.get("reason") else ""
        lines.append(f"{words}: {size}{extra}" + (f" · {why}" if why else ""))
    if not lines:
        lines = [f"trades {', '.join(str(t) for t in p.get('trade_ids') or [])}"]
    return "\n".join(lines)


def position_rows(result: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The included positions, the engine's order (largest contribution first), as display rows."""
    rows = []
    for p in (result.get("position_risk") or {}).get("positions") or []:
        if not p.get("included"):
            continue
        kind = str(p.get("kind") or "")
        code = position_type(p, ctx)
        sub = trade_type_words(code) if kind == "strategy" and code != "MIXED" else ""
        rows.append({
            "id": p.get("position_id"), "name": position_name(p, ctx), "sub": sub, "legs": _legs_hover(p, ctx),
            "contribution": _num(p.get("contribution_var")), "share": _num(p.get("contribution_share")),
            "contribution_reason": plain_reason(p.get("contribution_reason")),
            "standalone": _num(p.get("standalone_var")), "standalone_reason": plain_reason(p.get("standalone_reason")),
            "partial": bool(p.get("partial")), "partial_reason": str(p.get("partial_reason") or ""),
            "type_group": type_group(p, ctx), "commodity_group": commodity_group(p, ctx),
        })
    return rows


def _contribution_td(value: Optional[float], reason: str, standalone: Optional[float] = None,
                     standalone_reason: str = "", with_standalone: bool = True) -> html.Td:
    if value is None:
        return html.Td(missing_cell(reason or "not computed"))
    hover = f"{format_cell(value)} of the book's 1-day VaR"
    if with_standalone:
        hover += (f"; alone the position's VaR would be {short_money(standalone, '$')} ({format_cell(standalone)})"
                  if standalone is not None else f"; its VaR alone: {NA} ({standalone_reason or 'not computed'})")
    return html.Td(signed_money(value, "$").lstrip("+"), className=_signed_cls(value) if value < 0 else "", title=hover)


def _header_row(contribution_hover: str) -> html.Tr:
    return html.Tr([html.Th("Position", className="l"),
                    html.Th("Contribution", title=contribution_hover),
                    html.Th("Share", title="The position's contribution as a share of the book's VaR; the shares add "
                                           "up to 100 %. A negative share hedges the book."),
                    html.Th("", className="risk-bar-head")])


def _position_tr(r: Dict[str, Any], scale: float, member: bool = False) -> html.Tr:
    name = [html.Span(r["name"], title=r["legs"])]
    if r["sub"]:
        name.append(html.Span(r["sub"], className="name-sub"))
    if r["partial"]:
        name.append(html.Span("without hedge", className="name-sub risk-grey", title=plain_reason(r["partial_reason"])))
    return html.Tr([html.Td(name, className="l book-name" + (" risk-member" if member else "")),
                    _contribution_td(r["contribution"], r["contribution_reason"], r["standalone"], r["standalone_reason"]),
                    html.Td(_share_text(r["share"]), className="cell-neg" if (r["share"] or 0) < 0 else ""),
                    html.Td(_bar(r["share"], scale), className="risk-bar-cell")], className="book-row")


def _group_tr(label: str, members: List[Dict[str, Any]], scale: float) -> html.Tr:
    total, excluded, reasons = sum_known((m["contribution"], f"{m['name']}: {m['contribution_reason']}") for m in members)
    share, _x, _r = sum_known((m["share"], "") for m in members)
    count = html.Span(f"{len(members)} position{'s' if len(members) != 1 else ''}", className="name-sub")
    cell = (_contribution_td(total, "", with_standalone=False) if total is not None
            else html.Td(missing_cell("; ".join(reasons) or "not computed")))
    if excluded and total is not None:
        cell.children = [cell.children, marker(f"excl. {excluded}", "; ".join(reasons))]
    return html.Tr([html.Td([label, count], className="l"), cell,
                    html.Td(_share_text(share), className="cell-neg" if (share or 0) < 0 else ""),
                    html.Td(_bar(share, scale), className="risk-bar-cell")], className="book-group")


def _total_tr(pr: Dict[str, Any]) -> html.Tr:
    book_var = _num(pr.get("book_var"))
    headline = _num(pr.get("headline_var"))
    note = str(pr.get("book_var_note") or "")
    differs = book_var is not None and headline is not None and abs(book_var - headline) >= 0.5
    n_out = int(pr.get("excluded_count") or 0)
    if book_var is None:
        cell = html.Td(missing_cell(pr.get("book_var_reason") or "not computed"))
    else:
        cell = html.Td([short_money(book_var, "$"),
                        marker(f"excl. {n_out}", note or f"{n_out} positions not included") if n_out else None],
                       title=format_cell(book_var) + (f". {plain_reason(note)}" if note else ""))
    label = html.Span("Book (positions counted)" if differs or n_out else "Book",
                      title=plain_reason(note) if (differs and note) else None)
    return html.Tr([html.Td(label, className="l"), cell, html.Td("100%" if book_var is not None else NA),
                    html.Td("")], className="book-total")


def _grouped(rows: List[Dict[str, Any]], key: str) -> List[Tuple[str, List[Dict[str, Any]]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r)
    def rank(item: Tuple[str, List[Dict[str, Any]]]) -> Tuple[int, float]:
        total, _x, _r = sum_known((m["contribution"], "") for m in item[1])
        return (1, 0.0) if total is None else (0, -total)
    return sorted(groups.items(), key=rank)


def positions_table(rows: List[Dict[str, Any]], pr: Dict[str, Any], by: str) -> html.Table:
    """One of the three views of the positions: flat (Position) or grouped (Spread type,
    Commodity: a group line summing its positions' contributions and shares, one unit, then the
    positions), the Book line last."""
    method = plain_reason(pr.get("method")) or "The position's part of the book's 1-day VaR."
    head = _header_row(f"{method} The position's VaR alone is on the cell's hover.")
    body: List[Any] = []
    if by == GROUP_POSITION:
        scale = max([abs(r["share"]) for r in rows if r["share"] is not None] or [0.0])
        body = [_position_tr(r, scale) for r in rows]
    else:
        key = "type_group" if by == GROUP_TYPE else "commodity_group"
        groups = _grouped(rows, key)
        shares = []
        for _label, members in groups:
            s, _x, _r = sum_known((m["share"], "") for m in members)
            shares.append(abs(s) if s is not None else 0.0)
        scale = max(shares + [abs(r["share"]) for r in rows if r["share"] is not None] or [0.0])
        for label, members in groups:
            body.append(_group_tr(label, members, scale))
            body += [_position_tr(r, scale, member=True) for r in members]
    body.append(_total_tr(pr))
    return html.Table([html.Thead(head), html.Tbody(body)], className="book-table risk-table")


def diversification_line(pr: Dict[str, Any]) -> Optional[html.Div]:
    d = pr.get("diversification") or {}
    saved, alone, together = _num(d.get("saved")), _num(d.get("sum_standalone")), _num(d.get("book_var"))
    if saved is None or alone is None or together is None:
        why = plain_reason(d.get("reason"))
        return _quiet(f"Diversification: {NA}", why or "not computed", id=DIVERSIFICATION_ID) if d else None
    missing = int(d.get("standalone_missing") or 0)
    hover = (f"The positions' VaRs added up, each on its own: {format_cell(alone)}; the book's VaR with them "
             f"together: {format_cell(together)}; saved: {format_cell(saved)}. Positions offset each other on "
             "bad days, so the book loses less than its parts added up.")
    return html.Div([f"Diversification saves {short_money(saved, '$')}: positions alone {short_money(alone, '$')}, "
                     f"together {short_money(together, '$')}",
                     marker(f"excl. {missing}", f"{missing} position(s) with no VaR of their own") if missing else None],
                    id=DIVERSIFICATION_ID, className="risk-note-line", title=hover)


def _cause(reason: Any) -> str:
    """The short cause of an engine reason for a count: its first '; ' part, every parenthesis
    dropped, the text after the last 'who: ' kept ('910000030 (USDCNH): an FX trade: no FX market history (…)' -> 'no FX
    market history')."""
    text = plain_reason(reason).split("; ")[0]
    while True:
        shorter = re.sub(r"\s*\([^()]*\)", "", text)
        if shorter == text:
            break
        text = shorter
    return text.rsplit(": ", 1)[-1].strip() or "no reason given"


def excluded_line(pr: Dict[str, Any]) -> Optional[html.Div]:
    """'15 positions not included: no FX market history (12), no delta per lot (3)', every
    position and its reason on hover."""
    excluded = pr.get("excluded") or []
    if not excluded:
        return None
    counts: Dict[str, int] = {}
    for e in excluded:
        c = _cause(e.get("reason"))
        counts[c] = counts.get(c, 0) + 1
    words = ", ".join(f"{c} ({n})" if len(counts) > 1 else c for c, n in counts.items())
    n = len(excluded)
    hover = "\n".join(f"{e.get('name') or e.get('position_id')}: {plain_reason(e.get('reason'))}" for e in excluded)
    return _quiet(f"{n} position{'s' if n != 1 else ''} not included: {words}", hover, id=EXCLUDED_ID)


def partial_line(pr: Dict[str, Any]) -> Optional[html.Div]:
    note = str(pr.get("partial_note") or "")
    if not note:
        return None
    n = int(pr.get("partial_count") or 0)
    short = f"{n} position{'s' if n != 1 else ''} shown without their currency hedge" if n else plain_reason(note)
    return _quiet(short, note, id=PARTIAL_ID)


def group_switch() -> dcc.RadioItems:
    """Position | Spread type | Commodity; the value survives the body's re-render (persistence)."""
    return dcc.RadioItems(id=GROUP_ID, className="book-switch", options=[{"label": lb, "value": v} for v, lb in GROUP_OPTIONS],
                          value=GROUP_POSITION, inline=True, persistence=True, persistence_type="session")


def positions_block(result: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> html.Div:
    pr = result.get("position_risk") or {}
    ctx = ctx or {}
    hover = ("Which positions use up the book's risk: each position's part of the 1-day VaR, the parts adding up "
             "to the book's VaR. " + (plain_reason(pr.get("method")) or ""))
    if not pr.get("available") or not any(p.get("included") for p in pr.get("positions") or []):
        why = plain_reason(pr.get("reason")) or "no position has a price history"
        return html.Div([_section_head("Risk by position", hover), _quiet(f"No position figures: {why}", pr.get("reason") or ""),
                         excluded_line(pr)], id=POSITIONS_ID, className="risk-block")
    rows = position_rows(result, ctx)
    tables = [html.Div(positions_table(rows, pr, key), id=GROUP_TABLE_IDS[key],
                       style={} if key == GROUP_POSITION else _HIDDEN) for key, _label in GROUP_OPTIONS]
    under = [c for c in (diversification_line(pr), partial_line(pr), excluded_line(pr)) if c is not None]
    return html.Div([_section_head("Risk by position", hover, group_switch())] + tables + under,
                    id=POSITIONS_ID, className="risk-block")




# --------------------------------------------------------------------------- 3. correlation
CORR_SCALE = [[0.0, "#c0392b"], [0.5, "#ffffff"], [1.0, "#1f4e9c"]]


def _axis_name(text: str, width: int = 24) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"


def correlation_block(result: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> html.Div:
    pr = result.get("position_risk") or {}
    corr = pr.get("correlation")
    ctx = ctx or {}
    if not corr:
        hover = "How the positions' daily P&L moved together: near +1 = the same bet, near -1 = one offsets the other."
        why = plain_reason(pr.get("correlation_reason")) or "not computed"
        return html.Div([_section_head("Correlation", hover), _quiet(f"No correlation map: {why}", why)],
                        id=CORRELATION_ID, className="risk-block")
    by_id = {p.get("position_id"): p for p in pr.get("positions") or []}
    ids = list(corr.get("ids") or [])
    names = [position_name(by_id[i], ctx) if i in by_id else str(n) for i, n in zip(ids, corr.get("names") or ids)]
    labels, seen = [], set()
    for n in names:                   # two positions of one name stay two rows
        label = _axis_name(n)
        while label in seen:
            label += "​"
        seen.add(label)
        labels.append(label)
    z = [[_num(v) for v in row] for row in corr.get("matrix") or []]
    text = [["" if v is None else f"{v:.2f}".replace("-", MINUS) for v in row] for row in z]
    full = [[f"{names[i]} and {names[j]}: " + (text[i][j] or "no figure") for j in range(len(names))] for i in range(len(names))]
    fig = go.Figure(go.Heatmap(z=z, x=labels, y=labels, zmin=-1, zmax=1, colorscale=CORR_SCALE, text=text,
                               texttemplate="%{text}", textfont={"size": 10}, customdata=full,
                               hovertemplate="%{customdata}<extra></extra>", xgap=1, ygap=1,
                               colorbar={"thickness": 10, "len": 0.6, "tickvals": [-1, 0, 1]}))
    n = len(labels)
    fig.update_layout(height=max(260, 150 + 26 * n), margin={"l": 60, "r": 10, "t": 10, "b": 10},
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font={"family": "inherit", "size": 11, "color": "#1b2333"},
                      xaxis={"tickangle": -40, "side": "bottom", "automargin": True},
                      yaxis={"autorange": "reversed", "automargin": True})
    days = corr.get("days")
    hover = (f"How the positions' daily P&L moved together over {days} days ({_day_words(corr.get('first_date'))} to "
             f"{_day_words(corr.get('last_date'))}): near +1 = the same bet, near -1 = one offsets the other.")
    lines = []
    left = corr.get("left_out") or []
    if left:
        lines.append(_quiet(f"{len(left)} position{'s' if len(left) != 1 else ''} not in the map: {_cause(left[0].get('reason'))}"
                            if isinstance(left[0], dict) else f"{len(left)} positions not in the map",
                            "\n".join(f"{x.get('name')}: {x.get('reason')}" if isinstance(x, dict) else str(x) for x in left)))
    if corr.get("note"):
        lines.append(_quiet(plain_reason(corr["note"]), corr["note"]))
    return html.Div([_section_head("Correlation", hover),
                     dcc.Graph(figure=fig, config={"displayModeBar": False, "responsive": True}, className="risk-heatmap")]
                    + lines, id=CORRELATION_ID, className="risk-block")


# --------------------------------------------------------------------------- 4. stress
KIND_DESIGNED, KIND_REPLAY, KIND_FX = "designed", "historical replay", "FX"
_COMMODITY_KINDS = {"outright": KIND_DESIGNED, "curve": KIND_DESIGNED, "spread": KIND_DESIGNED, "replay": KIND_REPLAY,
                    "fx": KIND_FX}
PLACEHOLDER_WORDS = ("placeholder: the scenario file holds stand-in scenarios until Jason sets his own "
                     "(docs/open-questions.md)")


def _hit_from_commodity(s: Dict[str, Any], roots: Dict[str, Any]) -> Tuple[str, Optional[float], str]:
    """(name, P&L, reason) of the position hit hardest in a commodity scenario, from the engine's
    attribution: the book's spreads (`by_spread`) and the contracts in no spread (`by_contract`);
    an fx scenario's currency (`by_currency`)."""
    if s.get("kind") == "fx":
        cands = [(str(c.get("currency") or ""), _num(c.get("pnl_change_usd"))) for c in s.get("by_currency") or []]
    else:
        in_spread = {str(leg.get("instrument_id")) for sp in s.get("by_spread") or [] for leg in sp.get("legs") or []}
        cands = []
        for sp in s.get("by_spread") or []:
            family = str(sp.get("family") or "")
            words = spread_name({"kind": "calendar" if family == "calendar" else str(sp.get("template") or family),
                                 "template": str(sp.get("template") or ""), "family": family,
                                 "name": str(sp.get("name") or ""), "legs": sp.get("legs") or []}, roots)
            cands.append((words, _num(sp.get("pnl_usd"))))
        for c in s.get("by_contract") or []:
            if str(c.get("contract_id")) not in in_spread:
                cands.append((_contract_words(c.get("contract_id"), str(c.get("root_id") or ""), roots), _num(c.get("pnl_usd"))))
    valued = [(n, v) for n, v in cands if v is not None]
    if not valued:
        return "", None, "no position it moves has a figure" if cands else "the scenario moves no position"
    name, value = min(valued, key=lambda x: x[1])
    if value > -0.5:
        return "", None, "no position loses in this scenario"
    return name, value, ""


def stress_entries(result: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every scenario in one list, worst book P&L first (none last): the commodity scenarios, the
    FX scenarios and the book's worst day in history. Figures are the engines' as they are."""
    roots = (ctx or {}).get("roots") or {}
    out: List[Dict[str, Any]] = []
    cs = result.get("commodity_scenarios") or {}
    for s in cs.get("scenarios") or []:
        kind = _COMMODITY_KINDS.get(str(s.get("kind") or ""), KIND_DESIGNED)
        flag = s.get("placeholder")
        if flag is None:
            flag = cs.get("placeholder")
        if flag is None:   # the commodity stress file is placeholders until Jason's (CLAUDE.md "Risk")
            flag = kind != KIND_REPLAY
        missing = [f"{m.get('contract_id') or m.get('currency') or 'a position'}: {plain_reason(m.get('reason'))}"
                   for m in s.get("missing") or []]
        dates = f"{_day_words(s.get('start'))} to {_day_words(s.get('end'))}" if s.get("start") or s.get("end") else ""
        hit, hit_value, hit_reason = _hit_from_commodity(s, roots)
        out.append({"name": str(s.get("name") or ""), "hover": " ".join(x for x in (s.get("description"), dates) if x),
                    "kind": kind, "placeholder": bool(flag), "total": _num(s.get("total_usd")),
                    "total_reason": plain_reason(s.get("reason")) or "; ".join(missing) or "not computed",
                    "missing": missing, "hit": hit, "hit_value": hit_value, "hit_reason": hit_reason})
    for name, s in (result.get("scenarios") or {}).items():
        pnl = [(str(c), _num(v)) for c, v in (s.get("fx_pnl") or {}).items()]
        valued = [(c, v) for c, v in pnl if v is not None]
        worst = min(valued, key=lambda x: x[1]) if valued else None
        hit, hit_value, hit_reason = ((worst[0], worst[1], "") if worst and worst[1] <= -0.5
                                      else ("", None, "no currency loses in this scenario" if valued else "no currency figure"))
        out.append({"name": str(name), "hover": "FX scenario: the book's USD delta by currency x the move.",
                    "kind": KIND_FX, "placeholder": bool(s.get("placeholder")), "total": _num(s.get("total")),
                    "total_reason": plain_reason(s.get("reason")) or "not computed", "missing": [],
                    "hit": hit, "hit_value": hit_value, "hit_reason": hit_reason})
    book = result.get("book") or {}
    ch = result.get("commodity_history") or {}
    raw = _num(book.get("worst_1d_raw_usd"))
    if raw is not None or book:
        first = book.get("first_date") or ch.get("first_date")
        last = book.get("last_date") or ch.get("last_date")
        date = book.get("worst_1d_raw_date")
        ex = _num(book.get("worst_1d_ex_shocks_usd"))
        hover = ("Today's book replayed over every past day of the price history"
                 + (f" ({_month_words(first)} to {_month_words(last)})" if first and last else "")
                 + ", nothing excluded: its worst single day."
                 + (f" Without the shock days: {format_cell(ex)}" + (f" on {_day_words(book.get('worst_1d_ex_shocks_date'))}"
                                                                      if book.get("worst_1d_ex_shocks_date") else "") + "."
                    if ex is not None else ""))
        out.append({"name": "Worst day in history" + (f", {_day_words(date)}" if date else ""), "hover": hover,
                    "kind": KIND_REPLAY, "placeholder": False, "total": raw,
                    "total_reason": _why(book, "worst_1d_raw_usd") or "not available", "missing": [],
                    "hit": "", "hit_value": None, "hit_reason": "the worst day is not split by position"})
    return sorted(out, key=lambda e: (e["total"] is None, e["total"] if e["total"] is not None else 0.0))


def _stress_tr(e: Dict[str, Any]) -> html.Tr:
    kind = [e["kind"]] + ([html.Span("placeholder", className="risk-grey", title=PLACEHOLDER_WORDS)] if e["placeholder"] else [])
    if e["total"] is None:
        total = html.Td(missing_cell(e["total_reason"]))
    else:
        n = len(e["missing"])
        total = html.Td([signed_money(e["total"], "$"),
                         marker(f"excl. {n}", "not in this figure: " + "; ".join(e["missing"])) if n else None],
                        className=_signed_cls(e["total"]), title=format_cell(e["total"]))
    if e["hit_value"] is None:
        hit = html.Td(missing_cell(e["hit_reason"]), className="l")
    else:
        hit = html.Td([e["hit"], html.Span(signed_money(e["hit_value"], "$"), className="name-sub")], className="l",
                      title=f"{e['hit']}: {format_cell(e['hit_value'])}")
    return html.Tr([html.Td(e["name"], className="l book-name", title=e["hover"] or None),
                    html.Td(kind, className="l risk-kind"), total, hit], className="book-row")


def _stress_table(entries: List[Dict[str, Any]], id: Optional[str] = None) -> html.Table:
    head = html.Tr([html.Th("Scenario", className="l"), html.Th("Kind", className="l"),
                    html.Th("Book P&L", title="The book's P&L in the scenario, first order on today's delta; a replay "
                                              "is today's book over a past window."),
                    html.Th("Hit hardest", className="l", title="The position (or, for an FX scenario, the currency) "
                                                                 "that loses most in the scenario, where the engine "
                                                                 "attributes it.")])
    extra = {"id": id} if id else {}
    return html.Table([html.Thead(head), html.Tbody([_stress_tr(e) for e in entries])],
                      className="book-table risk-table risk-stress", **extra)


def stress_block(result: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> html.Div:
    entries = stress_entries(result, ctx)
    hover = ("Every scenario in one list, worst first. Designed: a move of the commodity stress file on today's "
             "positions, first order on delta (an option at its delta, its gamma not in it). Historical replay: "
             "today's book over a past window of the price history. FX: the book's USD delta by currency x the "
             "move. A dash = the engine has no figure (the reason on hover).")
    if not entries:
        return html.Div([_section_head("Stress", hover), _quiet("No scenarios on file.")], id=STRESS_ID, className="risk-block")
    shown, rest = entries[:STRESS_SHOWN], entries[STRESS_SHOWN:]
    children: List[Any] = [_section_head("Stress", hover), _stress_table(shown)]
    if rest:
        children.append(html.Details([html.Summary(f"{len(rest)} more scenario{'s' if len(rest) != 1 else ''}"),
                                      _stress_table(rest)], id=STRESS_MORE_ID, className="details details--compact",
                                     open=False))
    return html.Div(children, id=STRESS_ID, className="risk-block")


# --------------------------------------------------------------------------- margin and limits, gated
MARGIN_HIDDEN_WORDS = "Margin and limits appear once real margin rates and limits are set"


def margin_limits_real(margin: Optional[Dict[str, Any]], checks: Optional[List[Dict[str, Any]]]) -> bool:
    """True once a limit is set (a check whose level is not NOT_SET / N/A) or margin-limits says
    its rates are real (`rates_placeholder` False); until then the block is hidden (2026-09-29)."""
    any_set = any(str(c.get("level") or "") not in ("NOT_SET", "N/A", "") for c in checks or [])
    return any_set or (margin or {}).get("rates_placeholder") is False


# --------------------------------------------------------------------------- 6. margin and limits
def _roll_record(label: str, roll: Dict[str, Any]) -> Tuple[dict, dict]:
    """(record, tooltips) of one margin roll-up (a sector, a root or the book). Every
    position excluded = n/a with the reason (a sum over nothing is not a zero margin);
    some excluded = the sum with the engine's caption in the note and the reason on hover."""
    rec: Dict[str, Any] = {"label": label, "positions": roll.get("positions")}
    tip: Dict[str, dict] = {}
    positions = int(roll.get("positions") or 0)
    excluded = int(roll.get("excluded_count") or 0)
    why = roll.get("reason") or roll.get("caption") or ""
    all_out = positions > 0 and excluded >= positions
    for col in ("gross_charge_usd", "spread_credit_usd", "margin_usd"):
        v = None if all_out else _num(roll.get(col))
        rec[col] = NA if v is None else v
        if v is None:
            tip[col] = _tip(why or "no margin figure")
        elif excluded and col == "margin_usd":
            tip[col] = _tip(why)
    rec["note"] = roll.get("caption") or ("no open commodity position" if positions == 0 else "")
    if all_out and not rec["note"]:
        rec["note"] = f"excludes {excluded} of {positions} positions with no margin figure"
    return rec, tip


def _rate_text(roll: Dict[str, Any]) -> str:
    kind, rate = roll.get("rate_kind"), _num(roll.get("rate"))
    if kind == "rate" and rate is not None:
        return f"{rate * 100:g}% of |delta USD|"
    if kind == "per_lot" and rate is not None:
        return f"{format_cell(rate)} USD per lot"
    return "not set in the limits file yet"


MARGIN_MONEY = ("gross_charge_usd", "spread_credit_usd", "margin_usd")


def margin_hover(margin: Optional[Dict[str, Any]]) -> str:
    """The margin title's hover: margin-limits' own note, the basis and where the rates come
    from, the limits file named in words (`_limits_words`), never as a path."""
    m = margin or {}
    parts = [_limits_words(m.get("note")), f"Basis: {_limits_words(m.get('basis') or MARGIN_BASIS)}.",
             "Rates from the limits file; they are placeholders until you set them there.",
             "Money in k / M; the positions not in the margin are in the Data issues drawer."]
    if m.get("config_note"):
        parts.append(f"Limits file: {_limits_words(m['config_note'])}.")
    return " ".join(p for p in parts if p)


def margin_section(margin: Optional[Dict[str, Any]]) -> html.Div:
    head = [about(f"Margin ({MARGIN_BASIS})", margin_hover(margin))]
    if not margin:
        return html.Div(className="section", children=head + [
            html.P("The margin estimate was not computed.", className="section-kicker")])
    reasons = [r for r in (margin.get("reasons") or []) if r]
    if not margin.get("available", False):
        return html.Div(className="section", children=head + [
            html.P("Margin estimate unavailable: " + (_limits_words("; ".join(reasons)) or "no reason given") + ".",
                   className="section-kicker")])
    usd = rk.amount_short(nully="")
    by_sector = margin.get("by_sector") or {}
    recs = [_roll_record(sec or "no sector", roll) for sec, roll in by_sector.items()]
    book_rec, book_tip = _roll_record("Book", margin.get("book") or {})
    columns = [rk.text("Sector", "label"), rk.numeric("Gross charge USD", "gross_charge_usd", usd),
               rk.numeric("Spread credit USD", "spread_credit_usd", usd), rk.numeric("Margin USD (estimate)", "margin_usd", usd),
               rk.numeric("Positions", "positions", rk.count()), rk.text("Note", "note")]
    num_cols = list(MARGIN_MONEY)
    table = dash_table.DataTable(
        id=MARGIN_TABLE_ID, columns=columns, data=rk.whole_units([r for r, _ in recs], MARGIN_MONEY),
        tooltip_data=[t for _, t in recs],
        tooltip_delay=0, tooltip_duration=None, **rk.sortable(MARGIN_TABLE_ID),
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("label", "note")]
                               + [_clip("note", "44ch")],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=_na_styles(num_cols),
    )
    footer_style = [{"if": {"filter_query": "{label} = 'Book'"}, "fontWeight": "700", "borderTop": "2px solid #1f2933"}]
    children: List[Any] = head + [rk.with_footer(table, rk.whole_units([book_rec], MARGIN_MONEY), footer_style=footer_style,
                                                 footer_tooltips=[book_tip], skip_widths=("note",))]

    by_root = margin.get("by_root") or {}
    if by_root:
        root_recs = []
        for root_id, roll in by_root.items():
            rec, tip = _roll_record(f"{roll.get('name') or root_id} ({root_id})" if roll.get("name") and roll.get("name") != root_id
                                    else root_id, roll)
            rec["sector"] = roll.get("sector") or ""
            rec["rate"] = _rate_text(roll)
            if roll.get("rate_source"):
                tip["rate"] = _tip(_limits_words(roll["rate_source"]))
            root_recs.append((rec, tip))
        children.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"By commodity ({len(by_root)})"),
            dash_table.DataTable(
                id=MARGIN_ROOT_TABLE_ID,
                columns=[rk.text("Commodity", "label"), rk.text("Sector", "sector"), rk.text("Rate", "rate")] + columns[1:],
                data=rk.whole_units([r for r, _ in root_recs], MARGIN_MONEY), tooltip_data=[t for _, t in root_recs],
                tooltip_delay=0, tooltip_duration=None, **rk.sortable(MARGIN_ROOT_TABLE_ID),
                style_table={"overflowX": "auto"}, style_cell=_MONO,
                style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("label", "sector", "rate", "note")]
                                       + [_clip("note", "44ch")],
                style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
                style_data_conditional=_na_styles(num_cols)
                                       + [{"if": {"column_id": "rate", "filter_query": "{rate} contains 'not set'"}, **_NA_STYLE}])]))

    spreads = margin.get("spreads") or []
    if spreads:
        sp_recs, sp_tips = [], []
        for sp in spreads:
            pct = _num(sp.get("credit_pct"))
            rec = {"name": sp.get("name") or sp.get("spread_id", ""),
                   "kind": ", ".join(x for x in (sp.get("kind"), sp.get("family")) if x),
                   "credit_key": sp.get("credit_key") or "none",
                   "credit_pct": pct if pct is not None else "not set",
                   "charge_on_matched_usd": rk.value(sp.get("charge_on_matched_usd")),
                   "credit_usd": rk.value(sp.get("credit_usd")),
                   "leftover_charge_usd": rk.value(sp.get("leftover_charge_usd")),
                   "note": sp.get("note") or ""}
            tip = {}
            if pct is None:
                tip["credit_pct"] = _tip(f"no {sp.get('credit_key') or 'spread'} credit set in the limits file yet: "
                                         "every lot at the outright rate")
            if sp.get("excluded_count"):
                tip["credit_usd"] = _tip(f"excludes {sp['excluded_count']} leg(s) with no margin figure: "
                                         + ", ".join(sp.get("excluded") or []))
            sp_recs.append(rec)
            sp_tips.append(tip)
        sp_money = ("charge_on_matched_usd", "credit_usd", "leftover_charge_usd")
        children.append(html.Details(className="details details--compact", open=False, children=[
            html.Summary(f"Spread credits ({len(spreads)})",
                         title="The credit each open spread takes on its matched lots (inside the sector's spread "
                               "credit above, not added again); the lots it leaves outright stay charged at the "
                               "outright rate."),
            dash_table.DataTable(
                id=MARGIN_SPREAD_TABLE_ID,
                columns=[rk.text("Spread", "name"), rk.text("Kind", "kind"), rk.text("Credit rule", "credit_key"),
                         rk.numeric("Credit %", "credit_pct", rk.percentage(0, nully="")),
                         rk.numeric("Charge on matched USD", "charge_on_matched_usd", usd),
                         rk.numeric("Credit USD", "credit_usd", usd),
                         rk.numeric("Leftover charge USD", "leftover_charge_usd", usd), rk.text("Note", "note")],
                data=rk.whole_units(sp_recs, sp_money), tooltip_data=sp_tips, tooltip_delay=0, tooltip_duration=None,
                **rk.sortable(MARGIN_SPREAD_TABLE_ID),
                style_table={"overflowX": "auto"}, style_cell=_MONO,
                style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"} for c in ("name", "kind", "credit_key", "note")]
                                       + [_clip("note", "44ch")],
                style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
                style_data_conditional=[{"if": {"column_id": "credit_pct", "filter_query": "{credit_pct} = 'not set'"}, **_NA_STYLE}])]))
    # the positions not in the margin (margin["reasons"]) are in the tab's Data issues drawer
    return html.Div(className="section", children=children)


def limit_records(checks: List[Dict[str, Any]]) -> Tuple[List[dict], List[dict]]:
    """(records, tooltips) of the limit checks table, in the engine's order. The level is
    shown in `LEVEL_TEXT`'s words ("not set yet" for NOT_SET); a value the engine could not
    measure is a dash with the reason; a limit not set reads "not set". The reasons and basis
    name the limits file in words (`_limits_words`), never as a path."""
    records, tips = [], []
    for c in checks or []:
        level = c.get("level") or "N/A"
        rec: Dict[str, Any] = {"level": LEVEL_TEXT.get(level, level),
                               "limit": str(c.get("limit") or "").replace("_", " "), "scope": c.get("scope") or "",
                               "source": c.get("source") or "", "unit": c.get("unit") or "",
                               "reason": c.get("reason") or ""}
        tip: Dict[str, dict] = {}
        reason = _limits_words(c.get("reason"))
        if c.get("basis"):
            tip["limit"] = _tip(_limits_words(c["basis"]))
        v = rk.value(c.get("value"))
        rec["value"] = NA if v is None else v
        if v is None:
            tip["value"] = _tip(reason if level == "N/A" and reason else "the position has no figure")
        lv = rk.value(c.get("limit_value"))
        rec["limit_value"] = "not set" if lv is None else lv
        if lv is None:
            tip["limit_value"] = _tip(reason or "no limit set in the limits file yet")
        used = rk.value(c.get("used_pct"))
        rec["used_pct"] = used if used is not None else (NA if level == "N/A" else None)
        if reason:
            tip["level"] = _tip(reason)
            tip["reason"] = _tip(reason)
        records.append(rec)
        tips.append(tip)
    return records, tips


LIMITS_HOVER = ("The book against the desk's own limits and the exchanges' position limits, the limits you set "
                "in the limits file. BREACH above the limit, WARN from the warn fraction of it, OK under; a limit "
                "you have not set yet is 'not set', with the position still measured. Lots count options at their "
                "delta; the definition of each limit is on its name's hover.")


# the not-set drawer's item labels, by the engine's limit names (engine/limits/checks.py)
_NOT_SET_LABEL = {"gross_lots": "gross lots", "gross_usd": "gross USD", "net_usd_commodity": "net USD",
                  "exchange_spot_month": "spot month", "exchange_single_month": "single month",
                  "exchange_all_months": "all months"}
_NOT_SET_SOURCES = (("desk", "Desk limits"), ("exchange", "Exchange limits"))
NOT_SET_HOVER = ("Limits you have not set yet in the limits file: the position is measured but not checked. Each "
                 "item shows the position; its hover names the definition and the setting in the limits file that fixes it.")


def _is_not_set(check: Dict[str, Any]) -> bool:
    return (check.get("level") or "N/A") == "NOT_SET"


def _not_set_value(check: Dict[str, Any]) -> Tuple[str, str]:
    """(short, full) text of a not-set check's measured position: k / m for USD, lots to 2 dp;
    n/a when the engine has no figure (never zero)."""
    v, unit = _num(check.get("value")), check.get("unit") or ""
    if v is None:
        return NA, f"{NA} (the position has no figure)"
    if unit == "USD":
        return f"{short_money(v)} USD", f"{format_cell(v)} USD"
    return f"{_lots(v)} {unit}".strip(), f"{_lots(v)} {unit}".strip()


def _not_set_item(check: Dict[str, Any], rest: str) -> html.Span:
    """One not-set check as a short inline item ("spot month Z26 5 lots"), its definition,
    scope, full position and the limits-file setting that fixes it on hover (the file named in
    words, `_limits_words`)."""
    limit = str(check.get("limit") or "")
    short, full = _not_set_value(check)
    rest = re.sub(r"\s*\(.*\)\s*$", "", rest or "").strip()      # "(last trade ...)" goes to the hover
    label = _NOT_SET_LABEL.get(limit, "" if limit in ("net_usd_sector", "lots_per_contract_month")
                               else limit.replace("_", " "))
    text = " ".join(p for p in (label, rest, short) if p)
    hover = " ".join(p for p in (
        f"{limit.replace('_', ' ')}: {_limits_words(check['basis'])}." if check.get("basis") else "",
        f"Scope: {check.get('scope') or 'book'}.", f"Position: {full}.",
        (_limits_words(check.get("reason")) or "no limit set in the limits file yet") + ".") if p)
    return html.Span(text, className="limit-not-set-item", title=hover)


def _not_set_groups(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[html.Span]]]:
    """source -> group label -> items, in the engine's order: the desk's book, sector and per
    root (net USD, then the contract months), the exchange's per root. Only regrouped: every
    not-set check is one item."""
    groups: Dict[str, Dict[str, List[html.Span]]] = {}
    for c in rows:
        limit, scope = str(c.get("limit") or ""), str(c.get("scope") or "")
        source = c.get("source") or "other"
        if limit in ("gross_lots", "gross_usd"):
            group, rest = "Book", ""
        elif limit == "net_usd_sector":
            group, rest = "Net USD by sector", scope
        elif limit in ("net_usd_commodity", "lots_per_contract_month") or source == "exchange":
            group, _, rest = scope.partition(" ")
            group = group or "no root"
        else:
            group, rest = "Other", scope
        groups.setdefault(source, {}).setdefault(group, []).append(_not_set_item(c, rest))
    return groups


def not_set_drawer(checks: Optional[List[Dict[str, Any]]]) -> Optional[html.Details]:
    """Every NOT_SET check, collapsed into one line "Not set (N)" (the Phase A drawers' style);
    opened, one compact line per group (desk: book, sectors, each root; exchange: each root),
    each check an inline item with its position. None when every limit is set."""
    rows = [c for c in checks or [] if _is_not_set(c)]
    if not rows:
        return None
    groups = _not_set_groups(rows)
    order = [s for s, _ in _NOT_SET_SOURCES] + [s for s in groups if s not in dict(_NOT_SET_SOURCES)]
    body: List[Any] = []
    for source in order:
        if source not in groups:
            continue
        lines = []
        for group, items in groups[source].items():
            joined: List[Any] = []
            for i, item in enumerate(items):
                joined += ([_SEP] if i else []) + [item]
            lines.append(html.Li([html.Span(group, className="issue-label"), " "] + joined))
        n = sum(len(items) for items in groups[source].values())
        body += [html.Div(f"{dict(_NOT_SET_SOURCES).get(source, source.capitalize())} ({n})", className="issue-label"),
                 html.Ul(lines, className="issues-list")]
    return html.Details([html.Summary(f"Not set ({len(rows)})", title=NOT_SET_HOVER)] + body,
                        className="issues-drawer", open=False, id=LIMITS_NOT_SET_ID)


def limits_section(checks: Optional[List[Dict[str, Any]]]) -> html.Div:
    """The limits that are set (usage, breach or warning, or n/a with the reason) as the
    table; every limit not set yet in the limits file collapsed under it into "Not set (N)".
    While none is set, one quiet line says so above the collapsed list."""
    head = [about("Limits", LIMITS_HOVER)]
    if checks is None:
        return html.Div(className="section", children=head + [
            html.P("The limit checks were not computed.", className="section-kicker")])
    if not checks:
        return html.Div(className="section", children=head + [
            html.P("No limit checks: no open commodity position to measure.", className="section-kicker")])
    drawer = not_set_drawer(checks)
    checks = [c for c in checks if not _is_not_set(c)]
    if not checks:
        return html.Div(className="section", children=head + [
            html.P("No limit is set yet: the positions are measured, not checked. Set them in the limits file.",
                   className="section-kicker"), drawer])
    records, tips = limit_records(checks)
    counts = {lvl: sum(1 for c in checks if (c.get("level") or "N/A") == lvl) for lvl in LEVEL_TEXT}
    summary = ", ".join(f"{n} {LEVEL_TEXT[lvl]}" for lvl, n in counts.items() if n)
    columns = [rk.text("Level", "level"), rk.text("Limit", "limit"), rk.text("Scope", "scope"), rk.text("Source", "source"),
               rk.numeric("Position", "value", rk.amount(2, nully="", trim=True)),
               rk.numeric("Limit", "limit_value", rk.amount(2, nully="", trim=True)), rk.text("Unit", "unit"),
               rk.numeric("Used %", "used_pct", _pct_format()), rk.text("Reason", "reason")]
    level_styles = [{"if": {"column_id": "level", "filter_query": f"{{level}} = '{LEVEL_TEXT[lvl]}'"}, **style}
                    for lvl, style in LEVEL_STYLES.items()]
    table = dash_table.DataTable(
        id=LIMITS_TABLE_ID, columns=columns, data=records, tooltip_data=tips,
        tooltip_delay=0, tooltip_duration=None, **rk.sortable(LIMITS_TABLE_ID),
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("level", "limit", "scope", "source", "unit", "reason")]
                               + [_clip("reason", "52ch")],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=level_styles + _na_styles(["value", "used_pct"])
                               + [{"if": {"column_id": "limit_value", "filter_query": "{limit_value} = 'not set'"}, **_NA_STYLE}],
    )
    return html.Div(className="section", children=head + [
        html.P(f"The limits you set: {summary}.", className="section-kicker"), table]
        + ([drawer] if drawer is not None else []))


MARGIN_LIMITS_HOVER = (f"Margin: an initial-margin estimate per root x month at the rates of the limits file with "
                       f"spread credits ({MARGIN_BASIS}). Limits: the book against the desk's and the exchanges' "
                       "position limits, the limits you set in the same file; a limit not set yet is 'not set', the "
                       "position still measured. Each block's own definitions are on its title's hover inside.")


def margin_limits_count(margin: Optional[Dict[str, Any]], checks: Optional[List[Dict[str, Any]]]) -> str:
    """The fold's count line: the book's margin estimate (n/a when it has none), how many
    limits are set of how many, and the breaches and warnings if any."""
    m = margin or {}
    book = m.get("book") or {}
    if m.get("available") and book:
        rec, _ = _roll_record("Book", book)
        margin_words = f"margin {_money(rec['margin_usd'])}" if rec["margin_usd"] != NA else f"margin {NA}"
    else:
        margin_words = f"margin {NA}"
    parts = [margin_words]
    if checks:
        n_set = sum(1 for c in checks if not _is_not_set(c))
        parts.append(f"{n_set} of {len(checks)} limit{'s' if len(checks) != 1 else ''} set")
        for level in ("BREACH", "WARN"):
            n = sum(1 for c in checks if (c.get("level") or "N/A") == level)
            if n:
                parts.append(f"{n} {level}")
    elif checks is not None:
        parts.append("no limit check")
    return "; ".join(parts)


def margin_limits_section(margin: Optional[Dict[str, Any]], checks: Optional[List[Dict[str, Any]]]) -> html.Details:
    """Margin and limits, folded together: the margin estimate and the limit checks as they
    are, each with its own title inside."""
    return fold("Margin and limits", margin_limits_count(margin, checks), MARGIN_LIMITS_HOVER,
                [html.Div(id=MARGIN_LIMITS_ID, children=[margin_section(margin), limits_section(checks)])])


def margin_and_limits(conn: sqlite3.Connection, as_of: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """margin-limits' margin estimate and limit checks for `as_of` (`limits_pass` less the
    liquidity check)."""
    margin, checks, _liq = limits_pass(conn, as_of)
    return margin, checks


def limits_pass(conn: sqlite3.Connection, as_of: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """margin-limits' three results for `as_of` (the margin estimate, the limit checks, the
    liquidity check) on one computation of the curve positions and the spreads. A failure is a
    reason in the result's shape, never a crash of the tab."""
    from engine.curve import curve_positions
    from engine.limits import limit_checks, liquidity, margin_estimate
    try:
        curve = curve_positions(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen
        why = f"the commodity positions could not be computed ({type(exc).__name__}: {exc})"
        return ({"available": False, "reasons": [why]},
                [{"limit": "limit checks", "scope": "", "level": "N/A", "reason": why}],
                {"available": False, "reason": why, "label": "research", "positions": [], "summary": {}})
    spreads = None
    if curve.get("rows"):
        try:
            from engine.spreads import book_spreads
            spreads = book_spreads(conn, as_of)
        except Exception:  # noqa: BLE001 -- the engines read them themselves and name the failure
            spreads = None
    try:
        margin = margin_estimate(conn, as_of, curve=curve, spreads=spreads)
    except Exception as exc:  # noqa: BLE001
        margin = {"available": False, "reasons": [f"the margin estimate could not be computed ({type(exc).__name__}: {exc})"]}
    try:
        checks = limit_checks(conn, as_of, curve=curve)
    except Exception as exc:  # noqa: BLE001
        checks = [{"limit": "limit checks", "scope": "", "level": "N/A",
                   "reason": f"the limit checks could not be computed ({type(exc).__name__}: {exc})"}]
    try:
        liq = liquidity(conn, as_of, spreads=spreads, curve=curve)
    except Exception as exc:  # noqa: BLE001
        liq = {"available": False, "reason": f"the liquidity check could not be computed ({type(exc).__name__}: {exc})",
               "label": "research", "positions": [], "summary": {}}
    return margin, checks, liq


# --------------------------------------------------------------------------- liquidity (after block 2)
LIQUIDITY_ID = "risk-liquidity"
LIQUIDITY_FINE_ID = "risk-liquidity-fine"
LIQUIDITY_HOVER = ("Your lots against the contract's open interest and 20-day average volume; days to exit at 20 % "
                   "of daily volume; research data, thresholds placeholder.")
_LEVEL_CHIP = {"RED": ("red", "level-chip--red"), "AMBER": ("amber", "level-chip--amber"), "GREEN": ("fine", "level-chip--green")}


def _liquidity_tr(p: Dict[str, Any], name: str, ctx: Dict[str, Any]) -> html.Tr:
    roots = ctx.get("roots") or {}
    weakest = p.get("weakest")
    leg = next((x for x in p.get("legs") or [] if x.get("contract_id") == weakest), {}) if weakest else {}
    contract = (_contract_words(weakest, str(leg.get("root_id") or _root_of(weakest, ctx)), roots) if weakest else NA)
    note = plain_reason(leg.get("note")) if leg.get("note") else ""
    oi, days = _num(p.get("pct_of_oi")), _num(p.get("days_to_exit"))
    adv = _num(p.get("pct_of_adv"))
    words, chip_cls = _LEVEL_CHIP.get(str(p.get("level") or ""), (str(p.get("level") or "").lower(), ""))
    why = plain_reason(p.get("reason"))
    return html.Tr([
        html.Td(name, className="l book-name", title=why or None),
        html.Td(contract, className="l", title=" ".join(x for x in (str(weakest or ""), note) if x) or None),
        html.Td(f"{oi * 100:.1f}%" if oi is not None else missing_cell(why or "no open interest on file"),
                title=f"{oi * 100:.3f}% of the contract's open interest" if oi is not None else None),
        html.Td(f"{days:.1f}" if days is not None else missing_cell(why or "no volume on file"),
                title=(f"days to exit at the participation rate; the position is {adv * 100:.1f}% of a day's average volume"
                       if adv is not None else None)),
        html.Td(html.Span(words, className=f"level-chip {chip_cls}".strip()), className="l"),
    ], className="book-row")


def _liquidity_table(rows: List[Tuple[Dict[str, Any], str]], ctx: Dict[str, Any]) -> html.Table:
    head = html.Tr([html.Th("Position", className="l"), html.Th("Weakest contract", className="l"),
                    html.Th("% of open interest", title="Our lots in the contract as a share of its open interest."),
                    html.Th("Days to exit", title="Days to trade out at 20 % of the contract's 20-day average daily volume."),
                    html.Th("Level", className="l")])
    return html.Table([html.Thead(head), html.Tbody([_liquidity_tr(p, n, ctx) for p, n in rows])],
                      className="book-table risk-table risk-liquidity-table")


def liquidity_block(liq: Optional[Dict[str, Any]], result: Dict[str, Any],
                    ctx: Optional[Dict[str, Any]] = None) -> Optional[html.Div]:
    """Liquidity (engine.limits.liquidity, research data, placeholder thresholds): the engine's
    one sentence; the RED and AMBER positions in a compact table; the GREEN ones folded under
    "N positions fine"; the untested ones in one quiet line. Nothing recomputed."""
    if liq is None:
        return None
    ctx = ctx or {}
    tags = [html.Span(str(liq.get("label") or "research"), className="risk-grey", title=plain_reason(liq.get("basis")) or None)]
    if liq.get("placeholder"):
        tags.append(html.Span("placeholder", className="risk-grey", title=plain_reason(liq.get("placeholder_note")) or None))
    head = _section_head("Liquidity", LIQUIDITY_HOVER, *tags)
    if not liq.get("available"):
        why = plain_reason(liq.get("reason")) or "not computed"
        return html.Div([head, _quiet(f"No liquidity check: {why}", why)], id=LIQUIDITY_ID, className="risk-block")
    by_id = {p.get("position_id"): p for p in (result.get("position_risk") or {}).get("positions") or []}

    def name_of(p: Dict[str, Any]) -> str:
        src = by_id.get(p.get("position_id"))
        return position_name(src, ctx) if src else position_name(p, ctx)

    positions = liq.get("positions") or []
    alert = [(p, name_of(p)) for p in positions if p.get("level") in ("RED", "AMBER")]
    fine = [(p, name_of(p)) for p in positions if p.get("level") == "GREEN"]
    untested = [(p, name_of(p)) for p in positions if p.get("level") not in ("RED", "AMBER", "GREEN")]
    summary = liq.get("summary") or {}
    children: List[Any] = [head]
    if summary.get("sentence"):
        children.append(html.Div(plain_reason(summary["sentence"]), className="risk-note-line"))
    if alert:
        children.append(_liquidity_table(alert, ctx))
    if fine:
        children.append(html.Details([html.Summary(f"{len(fine)} position{'s' if len(fine) != 1 else ''} fine"),
                                      _liquidity_table(fine, ctx)], id=LIQUIDITY_FINE_ID,
                                     className="details details--compact", open=False))
    if untested:
        hover = "\n".join(f"{n}: {plain_reason(p.get('reason'))}" for p, n in untested)
        children.append(_quiet(f"{len(untested)} position{'s' if len(untested) != 1 else ''} not tested: "
                               f"{_cause(untested[0][0].get('reason'))}", hover))
    return html.Div(children, id=LIQUIDITY_ID, className="risk-block")


# --------------------------------------------------------------------------- the drawer
def issue_items(result: Dict[str, Any], margin: Optional[Dict[str, Any]] = None,
                liq: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """The tab's one Data issues drawer, as (label, sentence) pairs: the histories used or why
    none, the parameters and their notes, then every reason a figure is left out (the engine's
    `missing` list less the retired rows, the positions not included or shown without their
    hedge, the correlation's left-outs, the scenarios not valued, the liquidity check's skips,
    the positions not in the margin when that block shows)."""
    h = result.get("history") or {}
    c = result.get("config") or {}
    ch = result.get("commodity_history") or {}
    cs = result.get("commodity_scenarios") or {}
    pr = result.get("position_risk") or {}
    items: List[Tuple[str, str]] = [("Market history", history_line(result))]
    cm_line = commodity_history_line(result)
    if cm_line:
        items.append(("Commodity history", cm_line))
    items.append(("Parameters", parameters_line(c)))
    for label, note in (("Vol target", c.get("vol_target_note")), ("Config", c.get("note")), ("History", h.get("note")),
                        ("Commodity history", "" if ch.get("available") else ch.get("note")),
                        ("Commodity positions", plain_reason(ch.get("positions_note")))):
        if note:
            items.append((label, f"{note}."))
    items += [("Not in the VaR", plain_reason(m)) for m in shown_missing(result)]
    items += [(str(e.get("name") or e.get("position_id")), "not in risk by position: " + plain_reason(e.get("reason")))
              for e in pr.get("excluded") or []]
    if pr.get("partial_note"):
        items.append(("Without hedge", plain_reason(pr["partial_note"])))
    corr = pr.get("correlation") or {}
    items += [(str(x.get("name")), "not in the correlation map: " + plain_reason(x.get("reason")))
              for x in corr.get("left_out") or [] if isinstance(x, dict)]
    if pr.get("correlation") is None and pr.get("correlation_reason"):
        items.append(("Correlation", plain_reason(pr["correlation_reason"])))
    items += [("Stress", plain_reason(str(r).replace(": n/a,", ": not valued,"))) for r in (cs.get("reasons") or []) if r]
    for name, s in (result.get("scenarios") or {}).items():
        if _num(s.get("total")) is None:
            items.append(("Stress", f"{name}: not valued, {plain_reason(s.get('reason')) or 'no reason given'}"))
    if liq:
        items += [(str(x.get("instrument_id") or x.get("trade_id")), "not in the liquidity check: " + plain_reason(x.get("reason")))
                  for x in liq.get("skipped") or []]
    items += [("Not in the margin", plain_reason(r)) for r in ((margin or {}).get("reasons") or []) if r]
    return items


# --------------------------------------------------------------------------- body and shell
def body(result: Dict[str, Any], margin: Optional[Dict[str, Any]] = None,
         checks: Optional[List[Dict[str, Any]]] = None, ctx: Optional[Dict[str, Any]] = None,
         liq: Optional[Dict[str, Any]] = None) -> html.Div:
    """The whole tab body: the headline, risk by position, liquidity, correlation, stress, the
    margin and limits (one quiet line while they are placeholders), then the one drawer."""
    real = margin_limits_real(margin, checks)
    margin_part = (margin_limits_section(margin, checks) if real
                   else _quiet(MARGIN_HIDDEN_WORDS, "The margin rates in the limits file are placeholders and no desk or "
                                                    "exchange limit is set yet: the block shows once either is real."))
    drawer = issues_drawer(issue_items(result, margin if real else None, liq), id=ISSUES_ID)
    return html.Div(className="risk-body", children=[c for c in (
        headline_block(result), positions_block(result, ctx), liquidity_block(liq, result, ctx),
        correlation_block(result, ctx), stress_block(result, ctx), margin_part, drawer) if c is not None])


def render(as_of: Optional[str], db_path) -> Any:
    """The body for `as_of` from the database at `db_path`: one `book_risk` call, one
    margin-limits pass (margin, limits, liquidity) and the book's context on a read-only
    connection, closed straight after. With no trade on file, the Book's "No blotter loaded"
    card (user, 2026-09-28). A problem is a message where the body would be, never an empty tab."""
    if not as_of:
        return message_box("No as-of date available.")
    from ui.app import connect_readonly       # local, as the Ladder does: ui.app imports the tabs
    try:
        conn = connect_readonly(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        if trades_on_file(conn) == 0:
            return empty_state(idx="risk")
        result = book_risk(conn, as_of)
        margin, checks, liq = limits_pass(conn, as_of)
        ctx = position_context(conn)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"Risk could not be computed for {as_of} ({type(exc).__name__}: {exc}).", className="status-line status-line--bad")])
    finally:
        conn.close()
    return body(result, margin, checks, ctx, liq)


QUESTION = "How much can I lose, and which trades are using up my risk?"


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: a title row and the body container the callback fills. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="risk-tab", children=[
        html.Div(className="ladder-title-row", children=[
            html.H3("Risk", className="ladder-title-row-heading"), html.Span(QUESTION, className="risk-question")]),
        html.Div(id=BODY_ID, children=[message_box("Loading the risk metrics...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout

_GROUP_TOGGLE_JS = (
    "function(v) {\n"
    "  var keys = [" + ", ".join(f"'{k}'" for k, _l in GROUP_OPTIONS) + "];\n"
    "  var on = keys.indexOf(v) >= 0 ? v : keys[0];\n"
    "  return keys.map(function(k) { return k === on ? {} : {display: 'none'}; });\n"
    "}"
)


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body re-renders on the header's as-of, on every data revision and on the safety
    interval (the market histories live outside the database); the group switch shows one of
    the three risk-by-position tables in the browser (clientside, no round trip)."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path())

    app.clientside_callback(_GROUP_TOGGLE_JS, [Output(GROUP_TABLE_IDS[k], "style") for k, _l in GROUP_OPTIONS],
                            Input(GROUP_ID, "value"))
