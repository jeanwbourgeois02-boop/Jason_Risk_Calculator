"""Book tab: the app's home. "How did I do today, and what needs me?" (Screens redesign plan,
Phase B, user 2026-09-25). One screen, meant to fit 1680 x 1000 without scrolling.

It holds no figure of its own: every number is another lane's, shown here once as a summary,
each section naming the tab that holds the detail (CLAUDE.md "Tabs as views"):

  1. `pnl_section`, "P&L by spread today": a horizontal bar chart of each spread position's
     Daily P&L (`engine.spreads.book_spreads(...)["positions"]`, `pnl_usd.daily`) and of the
     outright futures grouped by commodity (`["outrights"]`, their period P&L summed per root
     under the header's display rule: priced figures only, "excl. N" otherwise), winners on
     top; beside it a compact table of the same rows with Daily / MTD / LTD in k / m, the
     spread's level move in its own unit (`level_change`) and that move in sigmas against the
     research app's daily vol (`engine.risk.research_spreads.sigma_move`, labelled research).
     FX, LME forwards and options outside a spread are not in it: their P&L is on the Blotter.
  2. `sector_section`, "Net outright by sector": curve-positions' `by_sector` net USD notional
     and net USD delta, a small bar each, so the directional leak of an RV book is plain.
  3. `alerts_section`, most urgent first, each naming its tab: expiries at EXPIRED / RED /
     AMBER (`engine.expiry.expiry_schedule`), marks missing (`ui.tabs.header.needed_marks`,
     the Data tab's own list), spread groups for review (`book_spreads(...)["review"]`), the
     VaR against the vol target (`ui.tabs.header.risk_summary`, the header's cached reading
     of risk-metrics; "VaR ..." until it is worked out, never blocking the tab) and the desk
     and exchange limits (`engine.limits.limit_checks`; one quiet line while none is set).
  4. `movers_section`: the rows with the largest |sigma move| (research) and the largest
     |Daily|.

Display rules (`ui.tabs.formatting`, `ui.tabs.ranking`): definitions on hover of the titles,
reasons as short markers and in one collapsed "Data issues (N)" drawer, money in k / m with the
full figure on hover. A figure the engine gives as None is "n/a" with its reason, never 0.
The only arithmetic here is the display sums of the header's rule (an outright commodity's
trades, the Total line, the sector totals) and display rounding.

`book_spreads` values several periods, so it is memoised on the database revision and the
as-of, as the header does (`_memo`). The tab has no date picker: it follows the header's as-of
store and re-renders in place on the data revision and its safety interval.
`layout(default_date)` (alias `build_layout`) and `register_callbacks(app, get_db_path)` are the
shell's interface, the Spreads and Curve tabs' shape.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
from dash import Input, Output, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Sign

from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs.formatting import MINUS, about, issues_drawer, marker, short_money, tab_link
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "book-body"
REFRESH_ID = "book-refresh"
VAR_PENDING_ID = "book-var-pending"     # the as-of whose VaR is still being worked out, else None
PNL_ID = "book-pnl"
PNL_CHART_ID = "book-pnl-chart"
PNL_TABLE_ID = "book-pnl-table"
SECTOR_ID = "book-sector"
SECTOR_CHART_ID = "book-sector-chart"
ALERTS_ID = "book-alerts"
MOVERS_ID = "book-movers"
ISSUES_ID = "book-issues"

NA = "n/a"
PERIODS = ("daily", "mtd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "mtd": "MTD", "ltd": "LTD"}
SPREAD, OUTRIGHT = "spread", "outright"
TOTAL_LABEL = "Total"
TOP_MOVERS = 5
AMBER_ON_LINE = 3
LINES_ON_HOVER = 12
POS_COLOUR, NEG_COLOUR = "#1a7f4b", "#c0392b"          # --pos / --neg of ui/assets/style.css
NAVY, GOLD, MUTED = "#0f1f3d", "#c9a227", "#6b7280"
LABEL_CHARS = 30

# Alert severity: 0 act now, 1 watch, 2 for information. Chips in the app's light palette.
ACT, WATCH, INFO = 0, 1, 2
_CHIP = {"display": "inline-block", "minWidth": "58px", "textAlign": "center", "fontSize": "11px",
         "fontWeight": 700, "lineHeight": "17px", "padding": "0 6px", "borderRadius": "9px", "marginRight": "8px"}
_CHIP_COLOURS = {ACT: {"color": "#ffffff", "backgroundColor": "#c0392b"},
                 WATCH: {"color": "#8a4b00", "backgroundColor": "#fff4e5", "border": "1px solid #f0c27a"},
                 INFO: {"color": MUTED, "backgroundColor": "#f1f3f6", "border": "1px solid #dfe3ea"}}
_EXPIRY_SEVERITY = {"EXPIRED": ACT, "RED": ACT, "AMBER": WATCH}
_LIMIT_SEVERITY = {"BREACH": ACT, "WARN": WATCH}

TITLE_ABOUT = ("The book at the header's as-of date: today's P&L by spread, the outright left by sector, what "
               "needs you and what moved. Every figure is another tab's, summarised: the name beside a "
               "section says where the detail is. Money in k / m, the full figure on hover.")
PNL_ABOUT = ("Daily P&L in USD of each spread position (spreads-engine: one row per distinct spread, the same "
             "spread put on over several days as one) and of the outright futures in no spread, summed per "
             "commodity. Winners on top. P&L of FX hedges, LME forwards and options not in a spread is on the "
             "Blotter; the header is the whole book. Detail: Spreads tab.")
TABLE_ABOUT = ("Daily, MTD and LTD P&L in USD (k / m), as spreads-engine gives them; a commodity's outright line "
               "adds up its trades' known figures and says what it leaves out. Move = the spread's level now "
               "less at the previous close, in its own unit. Sigma = that move over the research app's 20-day "
               "daily vol of the same spread: research context, never a mark or a P&L figure.")
SECTOR_ABOUT = ("Net USD of each sector's commodity positions (curve-positions): the notional of the futures and "
                "LME prompts summed with their signs, and the delta with options at their delta. A relative-value "
                "book should hold little here: this is the directional exposure left over. Detail: Curve tab.")
ALERTS_ABOUT = ("What needs you today, most urgent first: expiries within the alert window (Expiries tab), marks "
                "the book needs and does not have (Data tab), spread groups the rule would not decide (Spreads "
                "tab), the VaR against the vol target and the desk and exchange limits (Risk tab).")
MOVERS_ABOUT = ("The spreads that moved most against their own history (the day's level move in research "
                "sigmas), and the rows with the largest Daily P&L either way.")
SCOPE_NOTE = ("P&L on this tab is the futures book by spread: FX hedges, LME forwards and options on futures "
              "outside a spread are not in it (their P&L is on the Blotter). The header is the whole book.")

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "2px 6px", "whiteSpace": "pre", "fontSize": "12px"}
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_TOTAL_STYLE = {"fontWeight": "700", "borderTop": "2px solid #1f2933"}
_SECTION = {"background": "var(--card)", "border": "1px solid var(--line)", "borderRadius": "8px",
            "padding": "10px 12px", "minWidth": 0}
_LIST = {"listStyle": "none", "margin": 0, "padding": 0, "fontSize": "12.5px"}
_LINE = {"padding": "3px 0", "borderBottom": "1px solid #eef0f4", **_ONE_LINE}
_TAB_LINK = {"marginLeft": "6px", "fontSize": "11.5px"}
# The tabs a pointer can open: the label the user reads -> its stable key (ui.app.TAB_KEYS; not
# imported from ui.app, which imports the tabs).
TAB_KEYS = {"Book": "book", "Spreads": "spreads", "Curve": "curve", "Risk": "risk", "Expiries": "expiries",
            "Blotter": "blotter", "FX & cash": "ladder", "Data": "market-data"}


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _tip(text: str) -> dict:
    return {"value": text, "type": "text"}


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _date_words(iso: Optional[str]) -> str:
    if not iso:
        return "no as-of date"
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%A} {d.day} {d:%B %Y} ({iso})"


def full_usd(v: float) -> str:
    """The full figure behind a k / m cell: 'USD -6,928' (a true minus sign)."""
    return f"USD {v:,.0f}".replace("-", MINUS)


def signed_money(v: Optional[float]) -> str:
    """'+15.3k' / '−39.4k' / '0', 'n/a' for None: a summary figure with its sign."""
    if v is None:
        return NA
    text = short_money(v)
    return text if text.startswith(MINUS) or text == "0" else "+" + text


def signed_level(v: Optional[float]) -> str:
    """A level move: '+3.06', '−2.63' (a true minus sign), at most 4 significant decimals."""
    if v is None:
        return NA
    text = f"{v:+,.2f}" if abs(v) >= 0.1 or v == 0 else f"{v:+,.4g}"
    return text.replace("-", MINUS)


def contract_label(instrument_id: Optional[str]) -> str:
    s = str(instrument_id or "")
    return s[: -len(" Comdty")] if s.endswith(" Comdty") else s


def short_label(text: str, n: int = LABEL_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def pointer(tab: str, idx: str) -> html.Span:
    """The tab that holds the detail, as a link that opens it (`formatting.tab_link`): `tab` is
    the label the user reads ("Curve", "Data"), `idx` the spot it sits in, "book-<spot>", unique
    on the page for links to the same tab."""
    return html.Span(tab_link(f"→ {tab}", TAB_KEYS[tab], idx), style=_TAB_LINK)


# --------------------------------------------------------------------------- memo
_MEMO: dict = {}
_MEMO_MAX = 64


def _db_revision(conn: sqlite3.Connection) -> Optional[tuple]:
    """(path, mtime) of the connection's main database file, None for an in-memory one (then
    nothing is memoised). The mtime moves on every write (journal_mode=delete, CLAUDE.md
    "Guard rails")."""
    try:
        path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
        return (str(path), os.path.getmtime(path)) if path else None
    except Exception:  # noqa: BLE001 -- no memo, never a failure
        return None


def _memo(kind: str, conn: sqlite3.Connection, as_of: str, build: Callable[[], Any]) -> Any:
    """`build()` memoised on (kind, database path, mtime, as_of); a failure is not memoised."""
    rev = _db_revision(conn)
    if rev is None:
        return build()
    key = (kind, *rev, as_of)
    if key not in _MEMO:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        _MEMO[key] = build()
    return _MEMO[key]


# --------------------------------------------------------------------------- reading the lanes
def _failure(what: str, exc: Exception) -> str:
    return f"{what} ({type(exc).__name__}: {exc})"


def _spreads(conn: sqlite3.Connection, as_of: str) -> dict:
    """`book_spreads` through the screens' shared filled reader (the header's and the Spreads
    tab's), one pricing snapshot, memoised on the database revision and the as-of."""
    def build():
        from engine.spreads import book_spreads
        from ui.tabs.blotter_pricing import priced_value_book, pricing_snapshot
        with pricing_snapshot(conn, "Book tab"):
            return book_spreads(conn, as_of, value_fn=priced_value_book)
    return _memo("spreads", conn, as_of, build)


def _needs(conn: sqlite3.Connection, as_of: str) -> tuple:
    from ui.tabs.header import needed_marks
    return _memo("needs", conn, as_of, lambda: needed_marks(conn, as_of))


def _research(positions: Sequence[dict], as_of: str) -> dict:
    from engine.risk.research_spreads import research_spread_stats
    keys = list(dict.fromkeys((p.get("research_id"), p.get("research_instance") or "")
                              for p in positions if p.get("research_id")))
    return research_spread_stats(keys, as_of)


def _risk_if_ready(conn: sqlite3.Connection, as_of: str) -> Optional[dict]:
    """The header's risk reading for this revision and as-of when it has been worked out, else
    None (never computes: `book_risk` takes seconds on a cold cache)."""
    from ui.tabs import header
    ready = getattr(header, "risk_summary_if_ready", None) or getattr(header, "_risk_summary_if_ready", None)
    if ready is None:
        return None
    try:
        return ready(conn, as_of)
    except Exception:  # noqa: BLE001 -- pending, the chained callback tries the full read
        return None


def gather(conn: sqlite3.Connection, as_of: str, wait_for_risk: bool = False) -> dict:
    """Every lane's output the tab reads, each in its own try: one that fails costs its own
    section only, with the reason. `risk` is None while the VaR is being worked out (unless
    `wait_for_risk`, which reads it through the header's cache, however long that takes)."""
    data: Dict[str, Any] = {"as_of": as_of}
    try:
        data["spreads"], data["spreads_error"] = _spreads(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        log.exception("book tab: book_spreads failed for %s", as_of)
        data["spreads"], data["spreads_error"] = None, _failure("the spreads could not be built", exc)
    positions = (data["spreads"] or {}).get("positions") or []
    try:
        data["research"] = _research(positions, as_of)
    except Exception as exc:  # noqa: BLE001
        data["research"] = {"available": False, "reason": _failure("the research statistics could not be read", exc),
                            "stats": {}}
    try:
        from engine.curve import curve_positions
        data["curve"], data["curve_error"] = curve_positions(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["curve"], data["curve_error"] = None, _failure("the positions by sector could not be built", exc)
    try:
        from engine.expiry import expiry_schedule
        data["schedule"], data["schedule_error"] = expiry_schedule(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["schedule"], data["schedule_error"] = None, _failure("the roll calendar could not be built", exc)
    try:
        data["needs"], data["needs_error"] = _needs(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["needs"], data["needs_error"] = None, _failure(f"the marks the book needs on {as_of} could not be listed",
                                                            exc)
    try:
        from engine.limits import limit_checks
        kwargs = {"curve": data["curve"]} if data["curve"] is not None else {}
        data["limits"], data["limits_error"] = limit_checks(conn, as_of, **kwargs), ""
    except Exception as exc:  # noqa: BLE001
        data["limits"], data["limits_error"] = None, _failure("the limits could not be checked", exc)
    if wait_for_risk:
        from ui.tabs.header import risk_summary
        try:
            data["risk"] = risk_summary(conn, as_of)
        except Exception as exc:  # noqa: BLE001
            data["risk"] = {"error": _failure("the book's risk could not be read", exc)}
    else:
        data["risk"] = _risk_if_ready(conn, as_of)
    return data


# --------------------------------------------------------------------------- 1. P&L rows
def _research_entry(position: dict, research: dict) -> Tuple[Optional[dict], str]:
    """(the research statistics entry for the position, '' ) or (None, why there is none)."""
    if not position.get("research_id"):
        return None, position.get("research_reason") or "the research app has no id for this spread"
    key = (position.get("research_id"), position.get("research_instance") or "")
    entry = (research.get("stats") or {}).get(key)
    if entry is None:
        return None, research.get("reason") or "no research statistics read"
    return entry, ""


def spread_row(position: dict, research: dict) -> dict:
    """One P&L row of a spread position, the engine's figures as they are, and its sigma move
    from `sigma_move` (research)."""
    from engine.risk.research_spreads import sigma_move
    name = str(position.get("name") or position.get("position_id") or "")
    label = f"{name} (short)" if position.get("direction") == "short" else name
    if position.get("status") == "closed":
        label += " (closed)"
    values = position.get("pnl_usd") or {}
    move, unit = _num(position.get("level_change")), str(position.get("level_unit") or "")
    entry, why = _research_entry(position, research)
    if move is None:
        sigma, sigma_reason = None, ("no level move: " + (position.get("level_change_reason")
                                                          or "spreads-engine gave no level change"))
    elif entry is None:
        sigma, sigma_reason = None, why
    else:
        sigma, sigma_reason = sigma_move(move, entry, unit=unit or None)
    legs = " / ".join(contract_label(leg.get("instrument_id")) for leg in position.get("legs") or [])
    return {
        "key": str(position.get("position_id") or name), "kind": SPREAD, "label": label, "tab": "Spreads",
        "values": {p: _num(values.get(p)) for p in PERIODS},
        "excluded": {p: int((position.get("pnl_excluded") or {}).get(p) or 0) for p in PERIODS},
        "reasons": {p: str((position.get("pnl_reasons") or {}).get(p) or "") for p in PERIODS},
        "notes": {p: str((position.get("pnl_notes") or {}).get(p) or "") for p in PERIODS},
        "move": move, "unit": unit,
        "move_reason": "" if move is not None else str(position.get("level_change_reason")
                                                       or "spreads-engine gave no level change"),
        "level_prev": _num(position.get("level_prev")), "level_now": _num(position.get("level_now")),
        "level_prev_date": position.get("level_prev_date") or "",
        "sigma": sigma, "sigma_reason": sigma_reason,
        "research_note": (entry or {}).get("note") or "", "research_asof": (entry or {}).get("asof"),
        "facts": "; ".join(x for x in (legs and f"legs {legs}",
                                       f"{_plural(len(position.get('spread_ids') or []), 'entry')}"
                                       if len(position.get("spread_ids") or []) > 1 else "",
                                       position.get("status") or "") if x),
    }


def _root_names(roots: Sequence[str]) -> Dict[str, str]:
    try:
        from data.contracts import load_roots
        known = load_roots()
    except Exception:  # noqa: BLE001 -- the root id stands for its name
        known = {}
    return {r: str(getattr(known.get(r), "name", "") or r) for r in roots}


def outright_rows(outrights: Sequence[dict]) -> List[dict]:
    """One P&L row per commodity of the outright futures, each period the known figures of its
    trades added up (the header's display rule): None when none is known, with the trades'
    reasons; `excluded` counts the trades left out."""
    by_root: Dict[str, List[dict]] = {}
    for o in outrights:
        by_root.setdefault(str(o.get("root_id") or o.get("instrument_id") or ""), []).append(o)
    names = _root_names(list(by_root))
    rows = []
    for root_id, trades in by_root.items():
        values, excluded, reasons = {}, {}, {}
        for p in PERIODS:
            known = [_num((t.get("pnl_usd") or {}).get(p)) for t in trades]
            left = [t for t, v in zip(trades, known) if v is None]
            priced = [v for v in known if v is not None]
            values[p] = float(sum(priced)) if priced else None
            excluded[p] = len(left) if priced else 0
            reasons[p] = "; ".join(f"{t.get('trade_id')}: {(t.get('pnl_reasons') or {}).get(p) or 'no figure'}"
                                   for t in left)
        contracts = sorted({contract_label(t.get("instrument_id")) for t in trades})
        rows.append({
            "key": f"OUTRIGHT-{root_id}", "kind": OUTRIGHT, "label": f"{names[root_id]} (outright)", "tab": "Spreads",
            "values": values, "excluded": excluded, "reasons": reasons,
            "notes": {p: "; ".join(dict.fromkeys(str((t.get("pnl_notes") or {}).get(p) or "") for t in trades
                                                 if (t.get("pnl_notes") or {}).get(p))) for p in PERIODS},
            "move": None, "unit": "", "move_reason": "an outright has no spread level (its price is on the Data tab)",
            "sigma": None, "sigma_reason": "an outright has no spread level",
            "facts": f"{root_id}: {_plural(len(trades), 'trade')} in no spread ({', '.join(contracts)})",
        })
    return rows


def pnl_rows(result: Optional[dict], research: dict) -> List[dict]:
    """The spread positions and the outright commodities, largest Daily first (winners on top),
    a Daily the engine could not give last."""
    if not result:
        return []
    rows = [spread_row(p, research) for p in result.get("positions") or []]
    rows += outright_rows(result.get("outrights") or [])

    def order(pair):
        n, r = pair
        v = r["values"]["daily"]
        return (v is None, -(v if v is not None else 0.0), n)
    return [r for _n, r in sorted(enumerate(rows), key=order)]


def _unique_labels(rows: Sequence[dict]) -> List[str]:
    seen: Dict[str, int] = {}
    out = []
    for r in rows:
        base = short_label(r["label"])
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
    return out


def _period_hover(r: dict, p: str) -> str:
    v = r["values"][p]
    if v is None:
        return f"{PERIOD_TITLES[p]} {NA}: {r['reasons'][p] or 'spreads-engine gave no figure and no reason'}"
    words = [f"{PERIOD_TITLES[p]} {full_usd(v)}"]
    if r["excluded"][p]:
        words.append(f"excludes {r['excluded'][p]}: {r['reasons'][p]}")
    if r["notes"].get(p):
        words.append(r["notes"][p])
    return "; ".join(words)


def pnl_figure(rows: Sequence[dict]):
    """The horizontal bars of the rows' Daily P&L, as given (the rows with no Daily are not
    drawn: they are named under the chart). Winners on top, green up and red down."""
    import plotly.graph_objects as go
    drawn = [r for r in rows if r["values"]["daily"] is not None]
    labels = _unique_labels(drawn)
    # plotly draws the first category at the bottom: reverse, so the largest Daily is on top
    drawn, labels = list(reversed(drawn)), list(reversed(labels))
    xs = [r["values"]["daily"] for r in drawn]
    fig = go.Figure(go.Bar(
        x=xs, y=labels, orientation="h",
        marker_color=[POS_COLOUR if x >= 0 else NEG_COLOUR for x in xs],
        text=[signed_money(x) for x in xs], textposition="auto", cliponaxis=False,
        insidetextfont={"color": "#ffffff"}, outsidetextfont={"color": NAVY},
        customdata=[[r["label"], " · ".join(_period_hover(r, p) for p in PERIODS)] for r in drawn],
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>",
    ))
    fig.update_layout(
        height=max(160, min(520, 24 * len(drawn) + 40)),
        margin={"l": 8, "r": 16, "t": 6, "b": 24},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"size": 11.5, "color": "#1b2333"}, showlegend=False, bargap=0.28,
        xaxis={"showgrid": False, "zeroline": True, "zerolinecolor": "#9aa3b2", "zerolinewidth": 1,
               "tickformat": "~s", "tickfont": {"size": 10.5, "color": MUTED}, "fixedrange": True},
        yaxis={"automargin": True, "showgrid": False, "fixedrange": True, "ticks": ""},
    )
    return fig


def pnl_chart(rows: Sequence[dict]) -> html.Div:
    drawn = [r for r in rows if r["values"]["daily"] is not None]
    left = [r for r in rows if r["values"]["daily"] is None]
    children: List[Any] = []
    if drawn:
        children.append(dcc.Graph(id=PNL_CHART_ID, figure=pnl_figure(rows),
                                  config={"displayModeBar": False, "responsive": True}))
    else:
        children.append(message_box("No Daily P&L figure to chart."))
    if left:
        children.append(html.Div(className="section-kicker", children=[
            "Not charted:", marker(f"n/a {len(left)}", "; ".join(
                f"{r['label']}: {r['reasons']['daily'] or 'no Daily figure'}" for r in left))]))
    return html.Div(children)


_SIGMA_FORMAT = Format(precision=1, scheme=Scheme.fixed, sign=Sign.positive, nully="").to_plotly_json()
_MOVE_FORMAT = Format(precision=2, scheme=Scheme.fixed, sign=Sign.positive, nully="").to_plotly_json()


def pnl_record(r: dict) -> Tuple[dict, dict]:
    """(record, tooltips) of one table row: the figures as given, n/a with the reason."""
    rec: Dict[str, Any] = {"label": r["label"]}
    tip: Dict[str, dict] = {"label": _tip(f"{r['label']}: {r['facts']}" if r.get("facts") else r["label"])}
    for p in PERIODS:
        v = r["values"][p]
        rec[p] = NA if v is None else v
        tip[p] = _tip(_period_hover(r, p))
    rec["move"] = NA if r["move"] is None else r["move"]
    rec["unit"] = r["unit"] or ""
    if r["move"] is None:
        tip["move"] = _tip(r["move_reason"])
    else:
        tip["move"] = _tip(f"level {signed_level(r['move'])} {r['unit']}: {r.get('level_prev')} at the "
                           f"{r.get('level_prev_date') or 'previous'} close, {r.get('level_now')} now")
    rec["sigma"] = NA if r["sigma"] is None else r["sigma"]
    if r["sigma"] is None:
        tip["sigma"] = _tip(f"research: {r['sigma_reason']}")
    else:
        extra = f"; {r['research_note']}" if r.get("research_note") else ""
        tip["sigma"] = _tip(f"research: the day's move over the research app's 20-day daily vol "
                            f"(run of {r.get('research_asof')}){extra}")
    return rec, tip


def total_record(rows: Sequence[dict]) -> Tuple[dict, dict]:
    """The Total line: each period the rows' known figures added up (the header's rule), "excl."
    what it leaves out; the moves are not added (different units)."""
    rec: Dict[str, Any] = {"label": TOTAL_LABEL, "move": None, "unit": "", "sigma": None}
    tip: Dict[str, dict] = {"label": _tip(SCOPE_NOTE)}
    for p in PERIODS:
        known = [r["values"][p] for r in rows if r["values"][p] is not None]
        missing = [r["label"] for r in rows if r["values"][p] is None]
        inner = sum(r["excluded"][p] for r in rows if r["values"][p] is not None)
        if not known:
            rec[p] = NA
            tip[p] = _tip(f"no row has a {PERIOD_TITLES[p]} figure")
            continue
        rec[p] = float(sum(known))
        words = [full_usd(rec[p])]
        if missing:
            words.append(f"excludes {len(missing)} of {len(rows)} rows with no figure: {', '.join(missing)}")
        if inner:
            words.append(f"and {_plural(inner, 'trade or spread')} left out inside the rows")
        tip[p] = _tip("; ".join(words))
    n_out = {p: sum(1 for r in rows if r["values"][p] is None) + sum(r["excluded"][p] for r in rows) for p in PERIODS}
    if any(n_out.values()):
        rec["label"] = f"{TOTAL_LABEL} (excl. {max(n_out.values())})"
    return rec, tip


def pnl_table(rows: Sequence[dict]) -> html.Div:
    recs = [pnl_record(r) for r in rows]
    columns = [rk.text("Spread / outright", "label")] + \
              [rk.numeric(PERIOD_TITLES[p], p, rk.amount_short(nully="")) for p in PERIODS] + \
              [rk.numeric("Move", "move", _MOVE_FORMAT), rk.text("Unit", "unit"),
               rk.numeric("σ (research)", "sigma", _SIGMA_FORMAT)]
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    styles = dict(
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": "label"}, "textAlign": "left", **_ONE_LINE, "maxWidth": "230px"},
                                {"if": {"column_id": "unit"}, "textAlign": "left", "color": "var(--muted)"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto", "fontSize": "12px"},
        style_data_conditional=rk.sign_styles(numeric)
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in numeric],
        tooltip_delay=0, tooltip_duration=None,
    )
    table = dash_table.DataTable(
        id=PNL_TABLE_ID, columns=columns, data=rk.whole_units([r for r, _ in recs], PERIODS),
        tooltip_data=[t for _, t in recs],
        tooltip_header={"sigma": "The day's level move over the research app's 20-day daily vol of the same "
                                 "spread (research context).",
                        "move": "The spread's level now less at the previous close, in its own unit.",
                        **{p: f"{PERIOD_TITLES[p]} P&L in USD (k / m), the full figure on hover" for p in PERIODS}},
        **rk.sortable(PNL_TABLE_ID), **styles, page_action="none")
    footer, footer_tip = total_record(rows)
    footer = rk.whole_units([footer], PERIODS)[0]
    return rk.with_footer(table, [footer], footer_style=[{"if": {"row_index": 0}, **_TOTAL_STYLE}],
                          footer_tooltips=[footer_tip], skip_widths=("label",))


def pnl_section(data: dict, rows: Sequence[dict]) -> html.Div:
    heading = html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "6px"}, children=[
        about("P&L by spread today", PNL_ABOUT, level="h4", style={"margin": "0 0 6px"}),
        marker("futures only", SCOPE_NOTE), pointer("Spreads", "book-pnl")])
    if data.get("spreads_error"):
        return html.Div(id=PNL_ID, style=_SECTION, children=[heading, message_box(data["spreads_error"])])
    if not rows:
        return html.Div(id=PNL_ID, style=_SECTION, children=[
            heading, message_box(f"No spread or outright futures position on {data.get('as_of')}.")])
    return html.Div(id=PNL_ID, style=_SECTION, children=[
        heading,
        html.Div(style={"display": "grid", "gridTemplateColumns": "minmax(0, 5fr) minmax(0, 6fr)", "gap": "12px",
                        "alignItems": "start"}, children=[
            pnl_chart(rows),
            html.Div([about("Daily, MTD, LTD", TABLE_ABOUT, level="div", className="section-kicker"),
                      pnl_table(rows)])])])


# --------------------------------------------------------------------------- 2. sectors
def _sector_label(sector: str) -> str:
    return str(sector or "").replace("_", " ").capitalize() or "Other"


def sector_rows(curve: Optional[dict]) -> List[dict]:
    """[{sector, label, net_usd, net_delta_usd, gross_usd, reason, delta_reason}], curve-positions'
    `by_sector` figures as they are, in its order."""
    out = []
    for sector, s in ((curve or {}).get("by_sector") or {}).items():
        out.append({"sector": sector, "label": _sector_label(sector), "net_usd": _num(s.get("net_usd")),
                    "gross_usd": _num(s.get("gross_usd")), "net_delta_usd": _num(s.get("net_delta_usd")),
                    "reason": str(s.get("reason") or ""), "delta_reason": str(s.get("delta_reason") or "")})
    return out


def sector_figure(rows: Sequence[dict]):
    """Two small bars per sector: the net USD notional and the net USD delta, as given; a None is
    left undrawn (named under the chart)."""
    import plotly.graph_objects as go
    labels = [r["label"] for r in reversed(rows)]
    fig = go.Figure()
    for key, name, colour in (("net_usd", "Net notional", NAVY), ("net_delta_usd", "Net delta", GOLD)):
        xs = [r[key] for r in reversed(rows)]
        fig.add_trace(go.Bar(
            x=xs, y=labels, orientation="h", name=name, marker_color=colour,
            text=[signed_money(x) if x is not None else "" for x in xs], textposition="outside", cliponaxis=False,
            textfont={"size": 10.5, "color": NAVY},
            customdata=[full_usd(x) if x is not None else NA for x in xs],
            hovertemplate=f"<b>%{{y}}</b> {name}: %{{customdata}}<extra></extra>"))
    fig.update_layout(
        barmode="group", height=max(130, min(300, 34 * len(rows) + 50)),
        margin={"l": 8, "r": 40, "t": 4, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font={"size": 11.5, "color": "#1b2333"},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0, "font": {"size": 10.5}},
        bargap=0.3, bargroupgap=0.05,
        xaxis={"showgrid": False, "zeroline": True, "zerolinecolor": "#9aa3b2", "tickformat": "~s",
               "tickfont": {"size": 10.5, "color": MUTED}, "fixedrange": True},
        yaxis={"automargin": True, "showgrid": False, "fixedrange": True},
    )
    return fig


def _sum_known(values: Sequence[Optional[float]]) -> Tuple[Optional[float], int]:
    known = [v for v in values if v is not None]
    return (float(sum(known)) if known else None), len(values) - len(known)


def sector_totals(rows: Sequence[dict]) -> html.Div:
    """One line: the book's net notional, gross notional and net delta, each the sectors' known
    figures added up (the header's rule), with "excl. N" and the reasons on hover."""
    parts: List[Any] = []
    for key, word, why_key in (("net_usd", "Net", "reason"), ("gross_usd", "gross", "reason"),
                               ("net_delta_usd", "net delta", "delta_reason")):
        total, n_out = _sum_known([r[key] for r in rows])
        text = (signed_money(total) if key != "gross_usd" else short_money(total)) if total is not None else NA
        why = "; ".join(f"{r['label']}: {r[why_key] or 'no figure'}" for r in rows if r[key] is None)
        hover = (full_usd(total) if total is not None else f"no sector has a figure: {why}")
        if total is not None and n_out:
            hover += f"; excludes {_plural(n_out, 'sector')} with no figure: {why}"
        parts += [html.Span([f"{word} ", html.B(text)], title=hover, style={"cursor": "help"}),
                  marker(f"excl. {n_out}" if total is not None and n_out else "", why), " · "]
    return html.Div(parts[:-1], style={"fontSize": "13px", "margin": "0 0 4px"})


def sector_section(data: dict) -> html.Div:
    heading = html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "6px"}, children=[
        about("Net outright by sector", SECTOR_ABOUT, level="h4", style={"margin": "0 0 6px"}), pointer("Curve", "book-sector")])
    if data.get("curve_error"):
        return html.Div(id=SECTOR_ID, style=_SECTION, children=[heading, message_box(data["curve_error"])])
    rows = sector_rows(data.get("curve"))
    if not rows:
        note = (data.get("curve") or {}).get("note") or f"no open commodity position on {data.get('as_of')}"
        return html.Div(id=SECTOR_ID, style=_SECTION, children=[heading, message_box(note[:1].upper() + note[1:] + ".")])
    gaps = [f"{r['label']} net notional: {r['reason'] or 'no figure'}" for r in rows if r["net_usd"] is None] + \
           [f"{r['label']} net delta: {r['delta_reason'] or 'no figure'}" for r in rows if r["net_delta_usd"] is None]
    children: List[Any] = [heading, sector_totals(rows),
                           dcc.Graph(id=SECTOR_CHART_ID, figure=sector_figure(rows),
                                     config={"displayModeBar": False, "responsive": True})]
    if gaps:
        children.append(html.Div(className="section-kicker", children=[
            "Not drawn:", marker(f"n/a {len(gaps)}", "; ".join(gaps))]))
    return html.Div(id=SECTOR_ID, style=_SECTION, children=children)


# --------------------------------------------------------------------------- 3. alerts
def _alert(severity: int, chip: str, text: str, hover: str, tab: str, source: str) -> dict:
    return {"severity": severity, "chip": chip, "text": text, "hover": hover, "tab": tab, "source": source}


def _bd_words(row: dict) -> str:
    n = row.get("business_days")
    if row.get("level") == "EXPIRED":
        return "expired"
    if n is None:
        return "bd n/a"
    return "today" if n == 0 else f"{n} bd"


def expiry_alerts(data: dict) -> List[dict]:
    """EXPIRED and RED rows one line each; AMBER rows on one line (the first few named, all on
    hover). Expiry-monitor's order and words."""
    if data.get("schedule_error"):
        return [_alert(WATCH, "EXPIRIES", "roll calendar n/a", data["schedule_error"], "Expiries", "expiry-monitor")]
    rows = (data.get("schedule") or {}).get("rows") or []
    out = []

    def line(r):
        est = " (est.)" if r.get("estimated") else ""
        return (f"{contract_label(r.get('contract_id'))} {r.get('next_event') or ''} "
                f"{r.get('next_event_date') or 'date unknown'}{est}, {_bd_words(r)}")

    for r in rows:
        if r.get("level") in ("EXPIRED", "RED"):
            hover = "; ".join(x for x in (line(r), f"alert date {r.get('alert_date')}" if r.get("alert_date") else "",
                                          str(r.get("reason") or "")) if x)
            out.append(_alert(ACT, str(r["level"]), line(r), hover, "Expiries", "expiry-monitor"))
    amber = [r for r in rows if r.get("level") == "AMBER"]
    if amber:
        named = ", ".join(f"{contract_label(r.get('contract_id'))} {_bd_words(r)}" for r in amber[:AMBER_ON_LINE])
        more = f" +{len(amber) - AMBER_ON_LINE}" if len(amber) > AMBER_ON_LINE else ""
        hover = "\n".join(line(r) for r in amber[:LINES_ON_HOVER]) + (
            f"\nand {len(amber) - LINES_ON_HOVER} more" if len(amber) > LINES_ON_HOVER else "")
        out.append(_alert(WATCH, "AMBER", f"{_plural(len(amber), 'contract')} near expiry: {named}{more}", hover,
                          "Expiries", "expiry-monitor"))
    return out


def marks_alerts(data: dict) -> List[dict]:
    if data.get("needs_error"):
        return [_alert(WATCH, "MARKS", "marks n/a", data["needs_error"], "Data", "header.needed_marks")]
    needed, missing = data.get("needs") or (0, [])
    if not needed or not missing:
        return []
    shown = missing[:LINES_ON_HOVER]
    hover = "\n".join([f"{len(missing):,} of {needed:,} marks the book needs on {data.get('as_of')} have no "
                       "official mark:"] + [f"- {m['instrument_id']} {m['mark_type']} {m['settle_date']}" for m in shown]
                      + ([f"- and {len(missing) - len(shown)} more"] if len(missing) > len(shown) else []))
    return [_alert(WATCH, "MARKS", f"{len(missing):,} of {needed:,} marks missing", hover, "Data",
                   "header.needed_marks")]


def review_alerts(data: dict) -> List[dict]:
    review = (data.get("spreads") or {}).get("review") or []
    if not review:
        return []
    hover = "\n".join(f"- {r.get('reason') or r.get('review_id')}" for r in review[:LINES_ON_HOVER])
    return [_alert(WATCH, "REVIEW", f"{_plural(len(review), 'spread group')} for review", hover, "Spreads",
                   "spreads-engine")]


def _pct(v: float) -> str:
    return f"{v:.1f}%" if abs(v) < 100 else f"{v:,.0f}%"


def var_alert(data: dict) -> dict:
    """The VaR against the vol target, the header's reading of risk-metrics as it is."""
    risk = data.get("risk")
    if risk is None:
        return _alert(INFO, "RISK", "VaR …",
                      "The book's VaR and vol against the target are being worked out from the risk history; "
                      "they show here in a few seconds.", "Risk", "header.risk_summary")
    if risk.get("error"):
        return _alert(INFO, "RISK", "VaR n/a", str(risk["error"]), "Risk", "header.risk_summary")
    book, config = risk.get("book") or {}, risk.get("config") or {}
    var, pct = _num(book.get("var95_1d_usd")), _num(book.get("vol_vs_target_pct"))
    reasons = book.get("reasons") or {}
    target = _num(config.get("vol_target_usd"))
    placeholder = bool(config.get("vol_target_placeholder"))
    target_words = (f"vol target {full_usd(target)}" if target is not None else "vol target not set") + (
        " (a placeholder, not Jason's figure yet)" if placeholder else "")
    if var is None:
        why = str(reasons.get("var95_1d_usd") or reasons.get("all") or book.get("reason") or "not available")
        return _alert(INFO, "RISK", "VaR n/a", f"1y VaR n/a: {why}", "Risk", "header.risk_summary")
    over = bool(book.get("over_vol_target"))
    text = f"VaR {short_money(var, '$')} (1-day, 1y 95%)"
    if pct is not None:
        text += f" · vol {_pct(pct)} of target" + (" (placeholder)" if placeholder else "")
    hover = "; ".join(x for x in (f"1y 95% VaR (1-day) {full_usd(var)}",
                                  f"blended vol {_pct(pct)} of the {target_words}" if pct is not None else target_words,
                                  "over the target" if over else "", str(book.get("vol_note") or "")) if x)
    return _alert(ACT if over else INFO, "OVER" if over else "RISK", text, hover, "Risk", "header.risk_summary")


def limit_alerts(data: dict) -> List[dict]:
    """Every limit set and breached (or at its warning level), and a set limit with no figure;
    one quiet line while none is set."""
    if data.get("limits_error"):
        return [_alert(WATCH, "LIMITS", "limits n/a", data["limits_error"], "Risk", "margin-limits")]
    checks = data.get("limits") or []
    config_rows = [c for c in checks if c.get("limit") == "config"]
    if config_rows:
        return [_alert(WATCH, "LIMITS", "limits n/a", "; ".join(str(c.get("reason") or "") for c in config_rows),
                       "Risk", "margin-limits")]
    set_rows = [c for c in checks if c.get("level") != "NOT_SET"]
    if not set_rows:
        return [_alert(INFO, "LIMITS", "limits not set",
                       "No desk or exchange limit is set in config/limits.yaml yet: every check is NOT_SET.",
                       "Risk", "margin-limits")]
    out = []
    for c in set_rows:
        level = str(c.get("level") or "")
        value, limit = _num(c.get("value")), _num(c.get("limit_value"))
        unit = str(c.get("unit") or "")
        fmt = (lambda v: short_money(v)) if unit == "USD" else (lambda v: f"{v:,.0f}")
        what = f"{c.get('source')} {str(c.get('limit') or '').replace('_', ' ')} {c.get('scope')}"
        if level in _LIMIT_SEVERITY:
            out.append(_alert(_LIMIT_SEVERITY[level], level,
                              f"{what}: {fmt(abs(value)) if value is not None else NA} of {fmt(limit)} {unit}".strip(),
                              f"{c.get('reason') or ''}; {c.get('basis') or ''}".strip("; "), "Risk", "margin-limits"))
        elif level == "N/A":
            out.append(_alert(WATCH, "LIMIT N/A", f"{what}: no figure", str(c.get("reason") or ""), "Risk",
                              "margin-limits"))
    if not out:
        out.append(_alert(INFO, "LIMITS", f"{_plural(len(set_rows), 'limit')} set, none near",
                          "Every limit set in config/limits.yaml is below its warning level.", "Risk", "margin-limits"))
    return out


def alerts(data: dict) -> List[dict]:
    """Every alert, most urgent first (severity, then the order above: expiries, marks, review,
    risk, limits)."""
    items = expiry_alerts(data) + marks_alerts(data) + review_alerts(data) + [var_alert(data)] + limit_alerts(data)
    return [a for _n, a in sorted(enumerate(items), key=lambda pair: (pair[1]["severity"], pair[0]))]


def alert_line(a: dict, n: int) -> html.Li:
    """One alert: its chip, its words (the detail on hover) and a link to its tab, idx
    "book-alert-<n>" (n = its place in the list, so no two share one)."""
    return html.Li(style=_LINE, children=[
        html.Span(a["chip"], style={**_CHIP, **_CHIP_COLOURS[a["severity"]]}),
        html.Span(a["text"], title=a["hover"], style={"cursor": "help", **({"color": MUTED}
                                                                           if a["severity"] == INFO else {})}),
        pointer(a["tab"], f"book-alert-{n}")])


def alerts_list(data: dict) -> html.Ul:
    return html.Ul([alert_line(a, n) for n, a in enumerate(alerts(data), start=1)], style=_LIST)


def alerts_section(data: dict) -> html.Div:
    return html.Div(style=_SECTION, children=[
        about("Alerts", ALERTS_ABOUT, level="h4", style={"margin": "0 0 6px"}),
        html.Div(id=ALERTS_ID, children=alerts_list(data))])


# --------------------------------------------------------------------------- 4. movers
def movers(rows: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """(largest |sigma| first, largest |Daily| first), TOP_MOVERS each, rows with no figure left out."""
    by_sigma = sorted((r for r in rows if r["sigma"] is not None), key=lambda r: -abs(r["sigma"]))[:TOP_MOVERS]
    by_daily = sorted((r for r in rows if r["values"]["daily"] is not None),
                      key=lambda r: -abs(r["values"]["daily"]))[:TOP_MOVERS]
    return by_sigma, by_daily


def _colour(v: float) -> dict:
    return {"color": "var(--pos)" if v > 0 else "var(--neg)" if v < 0 else "var(--text)", "fontWeight": 600}


def movers_section(data: dict, rows: Sequence[dict]) -> html.Div:
    by_sigma, by_daily = movers(rows)
    research = data.get("research") or {}
    spread_rows = [r for r in rows if r["kind"] == SPREAD]
    sigma_items: List[Any]
    if by_sigma:
        sigma_items = [html.Li(style=_LINE, title=f"{r['label']}: move {signed_level(r['move'])} {r['unit']} (research)",
                               children=[html.Span(f"{r['sigma']:+.1f}σ".replace("-", MINUS),
                                                   style={**_colour(r["sigma"]), "display": "inline-block",
                                                          "minWidth": "52px"}),
                                         short_label(r["label"], 34)]) for r in by_sigma]
    else:
        why = research.get("reason") or "; ".join(dict.fromkeys(r["sigma_reason"] for r in spread_rows)) or \
            "no spread position"
        sigma_items = [html.Li(style={**_LINE, "color": MUTED}, children=[
            "no σ today", marker(NA, f"research: {why}")])]
    daily_items: List[Any] = [html.Li(style=_LINE, title=_period_hover(r, "daily"), children=[
        html.Span(signed_money(r["values"]["daily"]), style={**_colour(r["values"]["daily"]), "display": "inline-block",
                                                             "minWidth": "52px"}),
        short_label(r["label"], 34)]) for r in by_daily] or [
        html.Li("no Daily figure", style={**_LINE, "color": MUTED})]
    col = {"minWidth": 0}
    return html.Div(id=MOVERS_ID, style=_SECTION, children=[
        about("Top movers", MOVERS_ABOUT, level="h4", style={"margin": "0 0 6px"}),
        html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"}, children=[
            html.Div(style=col, children=[
                about("By σ (research)", "The day's level move over the research app's 20-day daily vol of "
                      "the same spread. Research context: never a mark or a P&L figure.", level="div",
                      className="section-kicker"), html.Ul(sigma_items, style=_LIST)]),
            html.Div(style=col, children=[
                about("By Daily P&L", "The rows of the P&L chart with the largest Daily either way.", level="div",
                      className="section-kicker"), html.Ul(daily_items, style=_LIST)])])])


# --------------------------------------------------------------------------- issues, body
def issue_items(data: dict, rows: Sequence[dict]) -> List[Tuple[str, str]]:
    """The Data issues drawer: each section's failure, the engines' book-level reasons, every
    row with an n/a or a partial figure, and why the research sigmas are missing."""
    items: List[Tuple[str, str]] = []
    for key, label in (("spreads_error", "Spreads"), ("curve_error", "Curve"), ("schedule_error", "Expiries"),
                       ("needs_error", "Marks"), ("limits_error", "Limits")):
        if data.get(key):
            items.append((label, data[key]))
    items += [("Grouping", str(r)) for r in (data.get("spreads") or {}).get("reasons") or [] if r]
    for r in rows:
        lines = []
        for p in PERIODS:
            if r["values"][p] is None:
                lines.append(f"{PERIOD_TITLES[p]} n/a: {r['reasons'][p] or 'no figure and no reason'}")
            elif r["excluded"][p]:
                lines.append(f"{PERIOD_TITLES[p]} excludes {r['excluded'][p]}: {r['reasons'][p]}")
        if lines:
            items.append((r["label"], "; ".join(dict.fromkeys(lines))))
    research = data.get("research") or {}
    if not research.get("available") and research.get("reason"):
        items.append(("Research", str(research["reason"])))
    else:
        for r in rows:
            if r["kind"] == SPREAD and r["sigma"] is None:
                items.append((r["label"], f"σ n/a (research): {r['sigma_reason']}"))
    items += [("Curve", str(x)) for x in (data.get("curve") or {}).get("reasons") or [] if x]
    return items


def is_empty(data: dict, rows: Sequence[dict]) -> bool:
    return not rows and not sector_rows(data.get("curve")) and not ((data.get("schedule") or {}).get("rows"))


def caption_block(data: dict, rows: Sequence[dict]) -> html.Div:
    n_spreads = sum(1 for r in rows if r["kind"] == SPREAD)
    n_out = sum(1 for r in rows if r["kind"] == OUTRIGHT)
    children: List[Any] = [html.Div(className="meta-line", children=[
        html.Span(f"As of {_date_words(data.get('as_of'))}"),
        html.Span(f"{_plural(n_spreads, 'spread position')}, {n_out} outright "
                  f"{'commodity' if n_out == 1 else 'commodities'}")])]
    drawer = issues_drawer(issue_items(data, rows), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
    return html.Div(children)


def body(data: dict) -> html.Div:
    """The whole tab body from `gather`'s output."""
    rows = pnl_rows(data.get("spreads"), data.get("research") or {})
    children: List[Any] = [caption_block(data, rows)]
    if is_empty(data, rows) and not data.get("spreads_error") and not data.get("curve_error"):
        children.append(message_box(f"No commodity futures in the book on {data.get('as_of') or 'this date'}: "
                                    "no spread, outright or sector position to show."))
        children.append(alerts_section(data))
        return html.Div(children)
    left = html.Div(style={"display": "grid", "gap": "12px", "minWidth": 0}, children=[
        pnl_section(data, rows),
        html.Div(style={"display": "grid", "gridTemplateColumns": "minmax(0, 1fr) minmax(0, 1fr)", "gap": "12px"},
                 children=[sector_section(data), movers_section(data, rows)])])
    right = html.Div(style={"display": "grid", "gap": "12px", "alignContent": "start", "minWidth": 0},
                     children=[alerts_section(data)])
    children.append(html.Div(style={"display": "grid", "gridTemplateColumns": "minmax(0, 7fr) minmax(320px, 3fr)",
                                    "gap": "12px", "alignItems": "start"}, children=[left, right]))
    return html.Div(children)


# --------------------------------------------------------------------------- shell
def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render(as_of: Optional[str], db_path) -> Tuple[Any, Optional[str]]:
    """(the body for `as_of`, the as-of whose VaR is still pending or None), from one read-only
    connection closed straight after. A problem is a message where the body would be."""
    if not as_of:
        return message_box("No as-of date available."), None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc})."), None
    try:
        data = gather(conn, as_of)
        return body(data), (as_of if data.get("risk") is None else None)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("book tab failed for %s", as_of)
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The book could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")]), None
    finally:
        conn.close()


def render_alerts(as_of: Optional[str], db_path) -> Any:
    """The alerts list with the VaR worked out (the header's cached reading; this may take
    seconds on a cold cache, which is why it runs in its own callback after the body)."""
    if not as_of:
        return dash.no_update
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return dash.no_update
    try:
        return alerts_list(gather(conn, as_of, wait_for_risk=True))
    except Exception:  # noqa: BLE001 -- the body's own list stays
        log.exception("book tab alerts failed for %s", as_of)
        return dash.no_update
    finally:
        conn.close()


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: the title (its definitions on hover), the body the callback fills, the
    VaR-pending store and the safety interval. No date picker: the tab follows the header's
    as-of store."""
    follows = f"Follows the header's as-of date{f' ({default_date} when the page loaded)' if default_date else ''}."
    return html.Div(className="book-tab", children=[
        html.Div(className="ladder-title-row", children=[
            about("Book", f"{TITLE_ABOUT} {follows}", level="h3", className="ladder-title-row-heading")]),
        html.Div(id=BODY_ID, children=[message_box("Loading the book...")]),
        dcc.Store(id=VAR_PENDING_ID, data=None),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """Two callbacks: the body on the header's as-of, every data revision and the safety
    interval; then, only when the body went out with the VaR pending, the alerts again once the
    header's risk reading is worked out (chained on `VAR_PENDING_ID`, so it never holds up the
    body)."""

    @app.callback(
        Output(BODY_ID, "children"),
        Output(VAR_PENDING_ID, "data"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path())

    @app.callback(Output(ALERTS_ID, "children"), Input(VAR_PENDING_ID, "data"), prevent_initial_call=True)
    def _fill_var(pending_as_of):
        if not pending_as_of:
            return dash.no_update
        return render_alerts(pending_as_of, get_db_path())
