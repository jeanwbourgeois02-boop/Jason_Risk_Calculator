"""Margin and limits on the Risk tab (moved out of `ui/tabs/risk.py` unchanged in Phase G round 2b,
2026-09-29, when the tab was rebuilt around the trade). margin-limits' margin estimate and limit
checks, shown folded once real margin rates or limits are set, one quiet line until then
(`margin_limits_real`); `limits_pass` also returns the liquidity check the per-trade table reads.
Also the Risk tab's plain-words pass over the engines' reasons (`plain_reason`). Nothing here
recomputes a margin, a limit or a metric: the engines' figures, formatted.
"""
from __future__ import annotations

import math
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from dash import dash_table, html
from dash.dash_table.Format import Format, Scheme, Symbol

from ui.tabs import ranking as rk
from ui.tabs.formatting import MISSING, about, format_cell, short_money

NA = MISSING
MARGIN_TABLE_ID = "risk-margin-table"
MARGIN_ROOT_TABLE_ID = "risk-margin-root-table"
MARGIN_SPREAD_TABLE_ID = "risk-margin-spread-table"
LIMITS_TABLE_ID = "risk-limits-table"
MARGIN_LIMITS_ID = "risk-margin-limits"
LIMITS_NOT_SET_ID = "risk-limits-not-set"
MARGIN_BASIS = "estimate, not exchange SPAN"

LEVEL_TEXT = {"BREACH": "BREACH", "WARN": "WARN", "OK": "OK", "NOT_SET": "not set yet", "N/A": NA}
LEVEL_STYLES = {
    "BREACH": {"backgroundColor": "#c0392b", "color": "#ffffff", "fontWeight": "700"},
    "WARN": {"backgroundColor": "#fff4e5", "color": "#8a4b00", "fontWeight": "700"},
    "OK": {"backgroundColor": "#e3f5ea", "color": "#1a7f4b"},
    "NOT_SET": {"backgroundColor": "#f1f2f4", "color": "#6b7280", "fontStyle": "italic"},
    "N/A": {"color": "var(--muted)", "fontStyle": "italic"},
}
_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "pre"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
# one line, clipped with an ellipsis: a long name or note never makes a row taller
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
_SEP = " · "                              # a middle dot between the parts of a line


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
    from engine.limits import limit_checks, liquidity, margin_estimate
    from ui.tabs.blotter_pricing import shared_curve, shared_spreads   # one of each per revision, every tab
    try:
        curve = shared_curve(conn, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen
        why = f"the commodity positions could not be computed ({type(exc).__name__}: {exc})"
        return ({"available": False, "reasons": [why]},
                [{"limit": "limit checks", "scope": "", "level": "N/A", "reason": why}],
                {"available": False, "reason": why, "label": "research", "positions": [], "summary": {}})
    spreads = None
    if curve.get("rows"):
        try:
            spreads = shared_spreads(conn, as_of)
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
