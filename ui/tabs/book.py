"""Book tab: the app's home. "What is my book, as spreads?" (UI redesign wave 1, user 2026-09-28,
after the Screens redesign plan's Phase B of 2026-09-25). One screen:

  1. `book_section`, the book table: one row per open position, grouped by sector with sector
     subtotals of the money columns. The rows are the spread positions of
     `engine.spreads.book_spreads(conn, as_of)["positions"]` (one per spread across its trade
     dates, their levels from `engine/spreads/levels.py`) and the outright futures
     (`["outrights"]`, one row per contract, its trades' known figures added up under the
     header's display rule). Columns: Position (the legs on hover), Lots, Entry, Now, Move (today's
     change against the Daily's reference close), $ per unit (the engine's `usd_per_unit`, the USD
     P&L of a 1.0 move, labelled "$ per <unit>"), Daily, MTD, LTD (k / m, the full figure on
     hover), Next date (the legs' earliest event of `engine.expiry.expiry_schedule`, coloured by
     its level). Sorted within each sector by |Daily|, largest first. A figure the engine could
     not give is "n/a" with the reason on hover.
  2. `detail_for`, the row detail (a click on a row): the position's entries and legs, as the
     Spreads tab builds them (`ui.tabs.spreads.detail_payloads`, `members_table`,
     `detail_legs_table`), its research history chart (`ui.tabs.spreads.history_figure`), and a
     link to the Spreads tab for its own daily history from our marks.
  3. `needs_you_section`, "Needs you": the alerts, most urgent first, each naming its tab:
     expiries at EXPIRED / RED / AMBER, marks missing, positions the rule could not group, the
     VaR against the vol target (the header's memoised reading of risk-metrics, filled by a
     chained callback so it never holds up the body), the limits.
  4. `load_report_section`: what the last blotter load did, in plain words: the trades on file by
     kind, the rows that did not become trades (the `upload_issues` table the upload writes, the
     Data tab's own list), the trades unpriced today with their reasons.
  5. One Data issues drawer.

Display rules (`ui.tabs.formatting`, `ui.tabs.ranking`): definitions on hover of the titles,
reasons as short markers and in the drawer, money in k / m with the full figure on hover. A
figure the engine gives as None is "n/a" with its reason, never 0. The only arithmetic here is
the display sums of the header's rule (an outright contract's trades, the sector and Book
lines) and display rounding.

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
from dash import Input, Output, State, dash_table, dcc, html

from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import ranking as rk
from ui.tabs import spreads as spreads_ui
from ui.tabs.formatting import MINUS, about, issues_drawer, marker, short_money, tab_link
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "book-body"
REFRESH_ID = "book-refresh"
VAR_PENDING_ID = "book-var-pending"     # the as-of whose VaR is still being worked out, else None
TABLE_ID = "book-table"                 # the book table (rendered inside the body)
DETAIL_ID = "book-detail"               # the row detail under it
DETAIL_MEMBERS_ID = "book-detail-members"
DETAIL_LEGS_ID = "book-detail-legs"
DETAIL_GRAPH_ID = "book-detail-graph"
ALERTS_ID = "book-alerts"
LOAD_ID = "book-load"
ISSUES_ID = "book-issues"

NA = "n/a"
PERIODS = ("daily", "mtd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "mtd": "MTD", "ltd": "LTD"}
SPREAD, OUTRIGHT = "spread", "outright"
ROW, SECTOR, TOTAL = "row", "sector", "total"
BOOK_LABEL = "Book"
LINES_ON_HOVER = 12
AMBER_ON_LINE = 3
MUTED = "#6b7280"
EVENT_SHORT = {"first notice": "FN", "last trade": "LT", "option expiry": "Exp", "LME prompt": "Prompt"}
_LEVEL_RANK = {"EXPIRED": 0, "RED": 1, "AMBER": 2, "GREEN": 3}
_LEVEL_STYLE = {"EXPIRED": {"color": "var(--neg)", "fontWeight": "700"},
                "RED": {"color": "var(--neg)", "fontWeight": "700"},
                "AMBER": {"color": "var(--warn)", "fontWeight": "600"}}

# Alert severity: 0 act now, 1 watch, 2 for information. Chips in the app's light palette.
ACT, WATCH, INFO = 0, 1, 2
_CHIP = {"display": "inline-block", "minWidth": "58px", "textAlign": "center", "fontSize": "11px",
         "fontWeight": 700, "lineHeight": "17px", "padding": "0 6px", "borderRadius": "9px", "marginRight": "8px"}
_CHIP_COLOURS = {ACT: {"color": "#ffffff", "backgroundColor": "#c0392b"},
                 WATCH: {"color": "#8a4b00", "backgroundColor": "#fff4e5", "border": "1px solid #f0c27a"},
                 INFO: {"color": MUTED, "backgroundColor": "#f1f3f6", "border": "1px solid #dfe3ea"}}
_LIMIT_SEVERITY = {"BREACH": ACT, "WARN": WATCH}

TITLE_ABOUT = ("The book at the header's as-of date, as spreads: one row per open position with its level, "
               "today's move, what a 1.0 move is worth and its P&L, grouped by sector; what needs you; and what "
               "the last blotter load did. Every figure is the engine's, shown as it is. Money in k / m, the full "
               "figure on hover.")
TABLE_ABOUT = ("One row per open position (spreads-engine: the same spread put on over several days is one; a "
               "future in no spread is an outright, one row per contract). Lots = the spread's size (a calendar in "
               "lots of the near month), or the contract's open lots. Entry, Now and Move are the spread's level "
               "in its own unit: at the lots-weighted fills, at the day's marks, and against the Daily's reference "
               "close. $ per unit = the USD P&L of a 1.0 rise of the level on the open lots. Daily / MTD / LTD in "
               "USD (k / m), summed per sector under the header's rule (priced trades only, 'excl. N' otherwise). "
               "Next date: the legs' earliest first notice, last trade, option expiry or LME prompt, with the "
               "business days to its alert. Click a row for its entries, legs and history. FX hedges, LME "
               "forwards and options in no spread are on the Trades tab; the header is the whole book.")
NEEDS_ABOUT = ("What needs you today, most urgent first: expiries within the alert window (Expiries tab), marks "
               "the book needs and does not have (Data tab), trades the grouping rule could not put in a spread "
               "(Spreads tab), the VaR against the vol target and the desk and exchange limits (Risk tab).")
LOAD_ABOUT = ("What the last blotter load did: the trades on file by kind, the rows of the file that did not "
              "become trades (with the parser's own reason), and the trades the engine could not price today. "
              "The load itself is not changed here: the Upload blotter button above replaces the whole book.")
SCOPE_NOTE = ("P&L on this table is the futures book by spread: FX hedges, LME forwards and options on futures "
              "outside a spread are not in it (their P&L is on the Trades tab). The header is the whole book.")

_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "3px 7px", "whiteSpace": "nowrap", "fontSize": "12.5px"}
_ONE_LINE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}
_NA_STYLE = {"color": "var(--muted)", "fontStyle": "italic"}
_SECTOR_STYLE = {"fontWeight": "700", "backgroundColor": "#f3f5f8", "borderTop": "2px solid var(--line)"}
_TOTAL_STYLE = {"fontWeight": "700", "borderTop": "2px solid #1f2933"}
_SECTION = {"background": "var(--card)", "border": "1px solid var(--line)", "borderRadius": "8px",
            "padding": "10px 12px", "minWidth": 0}
_LIST = {"listStyle": "none", "margin": 0, "padding": 0, "fontSize": "12.5px"}
_LINE = {"padding": "3px 0", "borderBottom": "1px solid #eef0f4", **_ONE_LINE}
_TAB_LINK = {"marginLeft": "6px", "fontSize": "11.5px"}
# The tabs a pointer can open: the label the user reads -> its stable key (ui.app.TAB_KEYS; not
# imported from ui.app, which imports the tabs).
TAB_KEYS = {"Book": "book", "Trades": "blotter", "Spreads": "spreads", "Curve": "curve", "Risk": "risk",
            "Expiries": "expiries", "FX & cash": "ladder", "Data": "market-data"}


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


def _short_date(iso: Optional[str]) -> str:
    try:
        d = dt.date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso or "date unknown")
    return f"{d.day} {d:%b}"


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


def pointer(tab: str, idx: str) -> html.Span:
    """The tab that holds the detail, as a link that opens it (`formatting.tab_link`): `tab` is
    the label the user reads ("Curve", "Data"), `idx` the spot it sits in, "book-<spot>", unique
    on the page for links to the same tab."""
    return html.Span(tab_link(f"→ {tab}", TAB_KEYS[tab], idx), style=_TAB_LINK)


def _sector_label(sector: str) -> str:
    return str(sector or "").replace("_", " ").capitalize() or "Other"


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


def _roots() -> Dict[str, Any]:
    try:
        from data.contracts import load_roots
        return dict(load_roots())
    except Exception:  # noqa: BLE001 -- the root id stands for its name and sector
        return {}


_PRODUCT_WORDS = {"FUTURE": "futures", "CMDTY_OPTION": "options on futures", "LME_FWD": "LME forwards",
                  "FX_FWD": "FX forwards", "FX_SPOT": "FX spot", "FX_SWAP": "FX swaps", "FX_OPTION": "FX options",
                  "EQ_OPTION": "listed options"}


def _load_report(conn: sqlite3.Connection, as_of: str) -> dict:
    """What the last load did, read from the database and the priced book: the trades on file by
    product, the trades unpriced on `as_of` with `value_book`'s reason, and the rows of the file
    that did not become trades (`upload_issues`, written by the upload; empty when the last load
    was clean or predates the table)."""
    from ui.tabs.blotter_pricing import priced_value_book
    df, n_filled, n_total = priced_value_book(conn, as_of)
    counts: Dict[str, int] = {}
    unpriced: List[Tuple[str, str]] = []
    if not df.empty:
        for product, n in df["product"].value_counts().items():
            counts[str(product)] = int(n)
        pnl = df["pnl_usd"].tolist() if "pnl_usd" in df.columns else [None] * len(df)
        reasons = df["reason"].tolist() if "reason" in df.columns else [""] * len(df)
        for tid, v, why in zip(df["trade_id"].tolist(), pnl, reasons):
            if _num(v) is None:
                unpriced.append((str(tid), str(why or "no reason given")))
    try:
        found = conn.execute("SELECT row_no, symbol, kind, reason, filename, uploaded_at FROM upload_issues "
                             "ORDER BY row_no").fetchall()
        issues = [{"row_no": n, "symbol": sym, "kind": kind, "reason": why, "filename": name, "uploaded_at": at}
                  for n, sym, kind, why, name, at in found]
        issues_error = ""
    except sqlite3.Error:      # no upload since the table was added: nothing recorded
        issues, issues_error = [], ""
    return {"counts": counts, "n_total": int(n_total), "n_filled": int(n_filled), "unpriced": unpriced,
            "issues": issues, "issues_error": issues_error}


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
    data["roots"] = _roots()
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
        data["limits"], data["limits_error"] = limit_checks(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["limits"], data["limits_error"] = None, _failure("the limits could not be checked", exc)
    try:
        data["load"], data["load_error"] = _load_report(conn, as_of), ""
    except Exception as exc:  # noqa: BLE001
        data["load"], data["load_error"] = None, _failure("the load report could not be read", exc)
    if wait_for_risk:
        from ui.tabs.header import risk_summary
        try:
            data["risk"] = risk_summary(conn, as_of)
        except Exception as exc:  # noqa: BLE001
            data["risk"] = {"error": _failure("the book's risk could not be read", exc)}
    else:
        data["risk"] = _risk_if_ready(conn, as_of)
    return data


# --------------------------------------------------------------------------- 1. the rows
def _sector_of(root_ids: Sequence[str], roots: Dict[str, Any]) -> str:
    for r in root_ids:
        sector = str(getattr(roots.get(r), "sector", "") or "")
        if sector:
            return sector
    return ""


def _periods(values: dict, excluded: dict, reasons: dict, notes: dict) -> dict:
    return {"values": {p: _num(values.get(p)) for p in PERIODS},
            "excluded": {p: int(_num(excluded.get(p)) or 0) for p in PERIODS},
            "reasons": {p: str(reasons.get(p) or "") for p in PERIODS},
            "notes": {p: str(notes.get(p) or "") for p in PERIODS}}


def spread_row(position: dict, roots: Dict[str, Any]) -> dict:
    """One row of a spread position, the engine's figures as they are."""
    name = str(position.get("name") or position.get("position_id") or "")
    legs = position.get("legs") or []
    unit = str(position.get("level_unit") or "")
    lots_text, lots_hover = spreads_ui.position_size_text(position)
    entries = position.get("spread_ids") or []
    sources = position.get("level_sources") or {}
    upu = _num(position.get("usd_per_unit"))
    return {
        "id": str(position.get("position_id") or name), "kind": SPREAD, "label": name, "unit": unit,
        "sector": _sector_of([str(leg.get("root_id") or "") for leg in legs], roots),
        "instrument_ids": [str(leg.get("instrument_id") or "") for leg in legs],
        "legs_hover": "; ".join(f"{contract_label(leg.get('instrument_id'))}: "
                                f"{spreads_ui.signed(_num(leg.get('lots')))} lot(s), "
                                f"{spreads_ui.signed(_num(leg.get('open_lots')))} open" for leg in legs) or "no legs",
        "facts": "; ".join(x for x in (
            f"{_plural(len(entries), 'entry')}: {', '.join(entries)}" if entries else "",
            f"trades {', '.join(position.get('trade_ids') or [])}",
            f"account(s) {', '.join(position.get('accounts') or [])}" if position.get("accounts") else "",
            "click for its entries, legs and history") if x),
        "lots": lots_text, "lots_hover": lots_hover,
        "entry": _num(position.get("level_entry")), "now": _num(position.get("level_now")),
        "move": _num(position.get("level_change")), "prev": _num(position.get("level_prev")),
        "prev_date": position.get("level_prev_date") or "",
        "entry_reason": str(position.get("level_entry_reason") or "spreads-engine gave no entry level"),
        "now_reason": str(position.get("level_now_reason") or "spreads-engine gave no level"),
        "move_reason": str(position.get("level_change_reason") or position.get("level_prev_reason")
                           or "spreads-engine gave no level change"),
        "sources": {k: str(sources.get(k) or "") for k in ("entry", "prev", "now", "usd_per_unit")},
        "upu": upu, "upu_reason": str(position.get("usd_per_unit_reason") or "spreads-engine gave no figure"),
        **_periods(position.get("pnl_usd") or {}, position.get("pnl_excluded") or {},
                   position.get("pnl_reasons") or {}, position.get("pnl_notes") or {}),
    }


def outright_rows(outrights: Sequence[dict], roots: Dict[str, Any]) -> List[dict]:
    """One row per contract of the outright futures (a future in no spread), each period the
    known figures of its trades added up (the header's display rule): None when none is known,
    with the trades' reasons; `excluded` counts the trades left out. No level: an outright has
    no spread unit (its price is on the Data tab)."""
    by_contract: Dict[str, List[dict]] = {}
    for o in outrights:
        by_contract.setdefault(str(o.get("instrument_id") or ""), []).append(o)
    rows = []
    for inst, trades in by_contract.items():
        values, excluded, reasons, notes = {}, {}, {}, {}
        for p in PERIODS:
            known = [_num((t.get("pnl_usd") or {}).get(p)) for t in trades]
            left = [t for t, v in zip(trades, known) if v is None]
            priced = [v for v in known if v is not None]
            values[p] = float(sum(priced)) if priced else None
            excluded[p] = len(left) if priced else 0
            reasons[p] = "; ".join(f"{t.get('trade_id')}: {(t.get('pnl_reasons') or {}).get(p) or 'no figure'}"
                                   for t in left)
            notes[p] = "; ".join(dict.fromkeys(str((t.get("pnl_notes") or {}).get(p) or "") for t in trades
                                               if (t.get("pnl_notes") or {}).get(p)))
        root_id = str(trades[0].get("root_id") or "")
        name = str(getattr(roots.get(root_id), "name", "") or root_id)
        open_lots = [_num(t.get("lots")) for t in trades if str(t.get("status") or "open") == "open"]
        lots = float(sum(v for v in open_lots if v is not None)) if any(v is not None for v in open_lots) else None
        why = "; ".join(dict.fromkeys(str(t.get("why_outright") or t.get("reason") or "") for t in trades
                                      if t.get("why_outright") or t.get("reason")))
        rows.append({
            "id": f"OUTRIGHT-{inst}", "kind": OUTRIGHT, "label": f"{contract_label(inst)} outright", "unit": "",
            "sector": _sector_of([root_id], roots), "instrument_ids": [inst],
            "legs_hover": f"{name}: {_plural(len(trades), 'trade')} in no spread"
                          + (f" ({why})" if why else ""),
            "facts": f"trades {', '.join(str(t.get('trade_id')) for t in trades)}",
            "lots": (spreads_ui.signed(lots) + " lots") if lots is not None else NA,
            "lots_hover": "the contract's open lots (its open trades' signed lots added up), long positive"
            if lots is not None else "spreads-engine gave no open lots",
            "entry": None, "now": None, "move": None, "prev": None, "prev_date": "",
            "entry_reason": "an outright has no spread level (its fill is on the Trades tab)",
            "now_reason": "an outright has no spread level (its price is on the Data tab)",
            "move_reason": "an outright has no spread level", "sources": {},
            "upu": None, "upu_reason": "an outright has no spread unit; its delta is on the Curve tab",
            **_periods(values, excluded, reasons, notes),
        })
    return rows


def book_rows(result: Optional[dict], roots: Dict[str, Any]) -> List[dict]:
    """The open spread positions and the outright contracts, grouped by sector (alphabetical,
    the unclassified last), within each by |Daily| largest first, a Daily the engine could not
    give last."""
    if not result:
        return []
    rows = [spread_row(p, roots) for p in result.get("positions") or [] if p.get("status") != "closed"]
    rows += outright_rows(result.get("outrights") or [], roots)

    def order(pair):
        n, r = pair
        v = r["values"]["daily"]
        return (r["sector"] == "", _sector_label(r["sector"]), v is None, -abs(v if v is not None else 0.0), n)
    return [r for _n, r in sorted(enumerate(rows), key=order)]


# --------------------------------------------------------------------------- next date
def _bd_words(row: dict) -> str:
    n = row.get("business_days")
    if row.get("level") == "EXPIRED":
        return "expired"
    if n is None:
        return "bd n/a"
    return "today" if n == 0 else f"{n} bd"


def _event_short(event: Optional[str]) -> str:
    return EVENT_SHORT.get(str(event or ""), str(event or "event").capitalize())


def next_event(row: dict, schedule: Optional[dict]) -> Tuple[str, str, str]:
    """(text, level, hover): the earliest event among the row's contracts on the roll calendar,
    'FN 14 Oct · 12 bd'; ('n/a', '', why) when none of them is on it."""
    wanted = set(row.get("instrument_ids") or [])
    found = [r for r in (schedule or {}).get("rows") or [] if r.get("contract_id") in wanted]
    if not found:
        return NA, "", "none of this position's contracts is on the roll calendar (expiry-monitor lists open futures, options on futures and LME prompts)"
    found.sort(key=lambda r: (_LEVEL_RANK.get(str(r.get("level")), 9),
                              r.get("business_days") if r.get("business_days") is not None else 10 ** 6))
    r = found[0]
    est = " (est.)" if r.get("estimated") else ""
    text = f"{_event_short(r.get('next_event'))} {_short_date(r.get('next_event_date'))}{est} · {_bd_words(r)}"
    hover = "; ".join(x for x in (
        f"{contract_label(r.get('contract_id'))}: {r.get('next_event') or 'event'} {r.get('next_event_date') or 'date unknown'}{est}, {r.get('level')}, {_bd_words(r)}",
        f"alert date {r.get('alert_date')}" if r.get("alert_date") else "", str(r.get("reason") or ""),
        f"{len(found) - 1} more contract(s) of this position on the calendar" if len(found) > 1 else "") if x)
    return text, str(r.get("level") or ""), hover


# --------------------------------------------------------------------------- the table
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


def _level_cell(v: Optional[float], unit: str, sign: bool = False) -> str:
    text = spreads_ui.level_text(v, unit, sign=sign)
    return text if v is None or not unit else f"{text} {unit}"


def upu_text(upu: Optional[float], unit: str) -> str:
    """'$1.25k per $/bbl': the USD P&L of a 1.0 move of the level, labelled by the unit."""
    if upu is None:
        return NA
    return f"{short_money(upu, '$')} per {unit or 'unit'}"


def row_record(r: dict, schedule: Optional[dict]) -> Tuple[dict, dict]:
    """(record, tooltips) of one position row: the figures as given, n/a with the reason."""
    rec: Dict[str, Any] = {"id": r["id"], "kind": ROW, "label": r["label"], "lots": r["lots"]}
    tip: Dict[str, dict] = {"label": _tip(f"{r['label']}: {r['legs_hover']}" + (f". {r['facts']}" if r.get("facts") else "")),
                            "lots": _tip(r["lots_hover"])}
    unit = r["unit"]
    for col, key in (("entry", "entry"), ("now", "now")):
        v = r[key]
        rec[col] = _level_cell(v, unit)
        if v is None:
            tip[col] = _tip(r[f"{key}_reason"])
        else:
            tip[col] = _tip(f"{_level_cell(v, unit)}; read from {r['sources'].get(key) or 'no source named'}")
    rec["move"] = _level_cell(r["move"], unit, sign=True)
    if r["move"] is None:
        tip["move"] = _tip(r["move_reason"])
    else:
        tip["move"] = _tip(f"now {_level_cell(r['now'], unit)} against {_level_cell(r['prev'], unit)} at the "
                           f"{r['prev_date'] or 'previous'} close (the Daily's reference); read from "
                           f"{r['sources'].get('prev') or 'no source named'}")
    rec["upu"] = upu_text(r["upu"], unit)
    tip["upu"] = _tip(r["upu_reason"] if r["upu"] is None else
                      f"{full_usd(r['upu'])} for a 1.0 rise of the level ({unit}) on the open lots (+ = a rise is a "
                      f"gain); {r['sources'].get('usd_per_unit') or 'USD contracts, no conversion'}")
    for p in PERIODS:
        v = r["values"][p]
        rec[p] = NA if v is None else v
        tip[p] = _tip(_period_hover(r, p))
    rec["next"], rec["next_level"], next_hover = next_event(r, schedule)
    tip["next"] = _tip(next_hover)
    return rec, tip


def _sum_record(label: str, rows: Sequence[dict], kind: str, hover_head: str) -> Tuple[dict, dict]:
    """A sector or Book line: each period the rows' known figures added up (the header's rule),
    "excl. N" in the label when something is left out, the detail on hover."""
    rec: Dict[str, Any] = {"id": f"{kind}-{label}", "kind": kind, "label": label, "lots": "", "entry": "", "now": "",
                           "move": "", "upu": "", "next": "", "next_level": ""}
    tip: Dict[str, dict] = {"label": _tip(hover_head)}
    n_out = 0
    for p in PERIODS:
        known = [r["values"][p] for r in rows if r["values"][p] is not None]
        missing = [r["label"] for r in rows if r["values"][p] is None]
        inner = sum(r["excluded"][p] for r in rows if r["values"][p] is not None)
        n_out = max(n_out, len(missing) + inner)
        if not known:
            rec[p] = NA
            tip[p] = _tip(f"no row has a {PERIOD_TITLES[p]} figure")
            continue
        rec[p] = float(sum(known))
        words = [full_usd(rec[p])]
        if missing:
            words.append(f"excludes {len(missing)} of {len(rows)} rows with no figure: {', '.join(missing)}")
        if inner:
            words.append(f"and {_plural(inner, 'trade')} left out inside the rows")
        tip[p] = _tip("; ".join(words))
    if n_out:
        rec["label"] = f"{label} (excl. {n_out})"
    return rec, tip


def table_records(rows: Sequence[dict], schedule: Optional[dict]) -> Tuple[List[dict], List[dict]]:
    """The table's records: each sector's line first, its rows under it, the Book line last."""
    records, tips = [], []
    sectors: List[str] = list(dict.fromkeys(r["sector"] for r in rows))
    for sector in sectors:
        members = [r for r in rows if r["sector"] == sector]
        rec, tip = _sum_record(_sector_label(sector), members, SECTOR,
                               f"{_sector_label(sector)}: {_plural(len(members), 'position')}, its P&L the rows' "
                               "known figures added up")
        records.append(rec)
        tips.append(tip)
        for r in members:
            rec, tip = row_record(r, schedule)
            records.append(rec)
            tips.append(tip)
    rec, tip = _sum_record(BOOK_LABEL, list(rows), TOTAL, SCOPE_NOTE)
    records.append(rec)
    tips.append(tip)
    return records, tips


def book_columns() -> List[dict]:
    return [rk.text("Position", "label"), rk.text("Lots", "lots"), rk.text("Entry", "entry"), rk.text("Now", "now"),
            rk.text("Move", "move"), rk.text("$ per unit", "upu")] + \
           [rk.numeric(PERIOD_TITLES[p], p, rk.amount_short(nully="")) for p in PERIODS] + \
           [rk.text("Next date", "next")]


HEADER_TIPS = {
    "label": "The spread (its legs on hover) or the outright contract. Click a row for its entries, legs and history.",
    "lots": "A spread's size: a calendar in lots of the near month (long = long the near month), a template in "
            "its quantity unit. An outright: its open lots, long positive.",
    "entry": "The spread's level at entry, in its own unit: the lots-weighted fills of its legs.",
    "now": "The spread's level at the day's official marks, in its own unit.",
    "move": "Now less the level at the Daily's reference close, in the spread's unit.",
    "upu": "The USD P&L of a 1.0 rise of the level on the open lots (spreads-engine's usd_per_unit), labelled by "
           "the unit: '$500 per ¢' means a one-cent rise of the spread is worth USD 500.",
    "next": "The position's next event on the roll calendar (first notice FN, last trade LT, option expiry Exp, "
            "LME prompt) and the business days to its alert, coloured by its level.",
    **{p: f"{PERIOD_TITLES[p]} P&L in USD (k / m), the full figure on hover; a sector line adds up the known "
          "figures and says what it leaves out" for p in PERIODS},
}


def book_table(records: Sequence[dict], tips: Sequence[dict]) -> dash_table.DataTable:
    columns = book_columns()
    numeric = [c["id"] for c in columns if c["type"] == "numeric"]
    styles = [{"if": {"row_index": i}, **_SECTOR_STYLE} for i, r in enumerate(records) if r["kind"] == SECTOR]
    styles += [{"if": {"row_index": i}, **_TOTAL_STYLE} for i, r in enumerate(records) if r["kind"] == TOTAL]
    styles += [{"if": {"row_index": i, "column_id": "next"}, **_LEVEL_STYLE[r["next_level"]]}
               for i, r in enumerate(records) if r.get("next_level") in _LEVEL_STYLE]
    styles += [{"if": {"row_index": i, "column_id": "label"}, "paddingLeft": "18px"}
               for i, r in enumerate(records) if r["kind"] == ROW]
    return dash_table.DataTable(
        id=TABLE_ID, columns=columns, data=rk.whole_units(list(records), PERIODS), tooltip_data=list(tips),
        tooltip_header=HEADER_TIPS, tooltip_delay=0, tooltip_duration=None,
        style_table={"overflowX": "auto"},
        style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": "label"}, "textAlign": "left", **_ONE_LINE, "maxWidth": "260px"},
                                {"if": {"column_id": "next"}, "textAlign": "left"},
                                {"if": {"column_id": "lots"}, "textAlign": "left"}],
        style_header={"fontWeight": "bold", "whiteSpace": "normal", "height": "auto", "fontSize": "12px"},
        style_data_conditional=rk.sign_styles(numeric)
                               + [{"if": {"column_id": c, "filter_query": f"{{{c}}} = '{NA}'"}, **_NA_STYLE}
                                  for c in numeric + ["entry", "now", "move", "upu", "next"]]
                               + styles,
        sort_action="none", page_action="none", cell_selectable=True, row_selectable=False,
    )


def book_section(data: dict, rows: Sequence[dict]) -> html.Div:
    heading = html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "6px"}, children=[
        about("Positions by spread", TABLE_ABOUT, level="h4", style={"margin": "0 0 6px"}),
        marker("futures only", SCOPE_NOTE), pointer("Spreads", "book-table")])
    if data.get("spreads_error"):
        return html.Div(style=_SECTION, children=[heading, message_box(data["spreads_error"]), html.Div(id=DETAIL_ID)])
    if not rows:
        return html.Div(style=_SECTION, children=[
            heading, message_box(f"No spread or outright futures position on {data.get('as_of')}."),
            html.Div(id=DETAIL_ID)])
    records, tips = table_records(rows, data.get("schedule"))
    return html.Div(style=_SECTION, children=[heading, book_table(records, tips), html.Div(id=DETAIL_ID)])


# --------------------------------------------------------------------------- 2. the row detail
def detail_for(data: dict, position_id: Optional[str]) -> Any:
    """The detail of the position `position_id` (a row's `id`): its entries and legs as the
    Spreads tab builds them, its research history chart, and a link to the Spreads tab for its
    own history from our marks. None for a sector or Book line, or an id that is no position."""
    if not position_id or str(position_id).startswith((f"{SECTOR}-", f"{TOTAL}-")):
        return None
    result, research, as_of = data.get("spreads") or {}, data.get("research") or {}, data.get("as_of")
    if str(position_id).startswith("OUTRIGHT-"):
        inst = str(position_id)[len("OUTRIGHT-"):]
        trades = [o for o in result.get("outrights") or [] if str(o.get("instrument_id") or "") == inst]
        if not trades:
            return None
        lines = [html.Li(f"{o.get('trade_id')}: {spreads_ui.signed(_num(o.get('lots')))} lot(s), "
                         f"{o.get('status') or ''}, traded {o.get('trade_date') or ''}"
                         + (f"; {o.get('why_outright')}" if o.get("why_outright") else ""), style=_LINE)
                 for o in trades]
        return html.Div(className="section section--secondary", children=[
            html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "6px"}, children=[
                about(f"{contract_label(inst)} outright", "A future in no spread: its trades as spreads-engine lists "
                      "them. The trade rows are on the Trades tab, the contract's curve on the Curve tab.",
                      level="h5", style={"margin": "0 0 6px"}),
                pointer("Trades", "book-detail-trades"), pointer("Curve", "book-detail-curve")]),
            html.Ul(lines, style=_LIST)])
    payloads = spreads_ui.detail_payloads(result, research)
    payload = payloads.get(str(position_id))
    if payload is None:
        return None
    unit = payload.get("unit") or ""
    summary = (f"{payload.get('name') or position_id}: {payload.get('size_text') or ''}, entry "
               f"{spreads_ui.level_text(_num(payload.get('level_entry')), unit)}, now "
               f"{spreads_ui.level_text(_num(payload.get('level_now')), unit)} {unit}").strip()
    chart = _research_chart(payload, as_of)
    members = spreads_ui.members_table(payload)
    members.id = DETAIL_MEMBERS_ID
    legs = spreads_ui.detail_legs_table(payload)
    legs.id = DETAIL_LEGS_ID
    return html.Div(className="section section--secondary", children=[
        html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "6px"}, children=[
            about(summary, "The position's entries and legs (spreads-engine), and the research app's history of "
                  "the spread as context (never a mark). Its own daily history from our marks, LTD and level "
                  "by day, is on the Spreads tab.", level="h5", style={"margin": "0 0 6px"}),
            pointer("Spreads", "book-detail-spreads")]),
        chart,
        about("Entries", "The spreads found on each trade date that make this position, with their own entry "
                         "level.", level="div", className="section-kicker"),
        members,
        about("Legs", "Each leg's prices as quoted (entry = its lots-weighted average fill), and the factor that "
                      "turns its price into the spread's unit.", level="div", className="section-kicker"),
        legs])


def _research_chart(payload: dict, as_of: Optional[str]) -> Any:
    rid, inst = payload.get("research_id") or "", payload.get("research_instance") or ""
    label = "context history (the research app's prices)"
    if not rid:
        return html.P(f"No {label}: {payload.get('research_reason') or 'no research id'}", className="section-kicker")
    start = None
    if as_of:
        try:
            d = dt.date.fromisoformat(as_of)
            start = d.replace(year=d.year - 5, day=min(d.day, 28)).isoformat()
        except ValueError:
            start = None
    try:
        from engine.risk.research_spreads import research_spread_history
        series = research_spread_history((rid, inst), start=start, end=as_of)
        figure, note = spreads_ui.history_figure(series, payload, as_of)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a broken panel
        return html.P(f"No {label}: the research history could not be read ({type(exc).__name__}: {exc})",
                      className="section-kicker")
    if figure is None:
        return html.P(f"No {label}: {note}", className="section-kicker")
    return html.Div([
        html.Div(f"{label}: {payload.get('research_name') or rid} in {series.attrs.get('unit') or 'its unit'}; "
                 "context only, not a mark", className="section-kicker"),
        dcc.Graph(id=DETAIL_GRAPH_ID, figure=figure, config={"displayModeBar": False}),
        *([html.Div(note, className="section-kicker")] if note else [])])


# --------------------------------------------------------------------------- 3. needs you
def _alert(severity: int, chip: str, text: str, hover: str, tab: str, source: str) -> dict:
    return {"severity": severity, "chip": chip, "text": text, "hover": hover, "tab": tab, "source": source}


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
    """The groups the grouping rule could not decide ("could not group", never "for review")."""
    review = (data.get("spreads") or {}).get("review") or []
    if not review:
        return []
    hover = "\n".join(f"- {r.get('reason') or r.get('review_id')}" for r in review[:LINES_ON_HOVER])
    return [_alert(WATCH, "GROUPING", f"could not group {_plural(len(review), 'set')} of trades", hover, "Spreads",
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
    """Every alert, most urgent first (severity, then the order above: expiries, marks, grouping,
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


def needs_you_section(data: dict) -> html.Div:
    return html.Div(style=_SECTION, children=[
        about("Needs you", NEEDS_ABOUT, level="h4", style={"margin": "0 0 6px"}),
        html.Div(id=ALERTS_ID, children=alerts_list(data))])


# --------------------------------------------------------------------------- 4. load report
def _kind_words(product: str, n: int) -> str:
    words = _PRODUCT_WORDS.get(product, product)
    if n == 1 and words.endswith("s") and product not in ("FX_SPOT",):
        words = words[:-1] if not words.endswith("futures") else "future" + words[len("futures"):]
    return f"{n} {words}"


def load_report_lines(load: Optional[dict], as_of: str) -> List[Tuple[str, str, str]]:
    """[(text, hover, severity-word)] of the load report, in plain words."""
    if not load:
        return []
    lines: List[Tuple[str, str, str]] = []
    counts = load.get("counts") or {}
    if counts:
        by_kind = ", ".join(_kind_words(p, n) for p, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        lines.append((f"{load.get('n_total', 0):,} trades on file: {by_kind}",
                      "Every trade the last blotter load booked, plus the manual trades, counted by product.", "info"))
    else:
        lines.append(("No trades on file: upload a blotter to fill the book.", "", "info"))
    issues = load.get("issues") or []
    if issues:
        kinds: Dict[str, int] = {}
        for i in issues:
            kinds[str(i.get("kind") or "")] = kinds.get(str(i.get("kind") or ""), 0) + 1
        head = ", ".join(f"{n} {k.lower()}" for k, n in kinds.items())
        hover = "\n".join([f"{len(issues)} rows of {issues[0].get('filename') or 'the file'} (uploaded "
                           f"{issues[0].get('uploaded_at') or 'date unknown'}) did not become trades:"]
                          + [f"- row {i.get('row_no')} {i.get('symbol') or ''}: {i.get('kind')}: {i.get('reason')}"
                             for i in issues[:LINES_ON_HOVER]]
                          + ([f"- and {len(issues) - LINES_ON_HOVER} more"] if len(issues) > LINES_ON_HOVER else []))
        lines.append((f"{_plural(len(issues), 'row')} of the file did not become trades ({head})", hover, "watch"))
    else:
        lines.append(("Every row of the last upload became a trade, or no upload has been recorded since this list "
                      "was added.", "The upload writes the rows it could not load; none is on file.", "info"))
    unpriced = load.get("unpriced") or []
    if unpriced:
        hover = "\n".join([f"{len(unpriced)} trades have no P&L on {as_of}:"]
                          + [f"- {tid}: {why}" for tid, why in unpriced[:LINES_ON_HOVER]]
                          + ([f"- and {len(unpriced) - LINES_ON_HOVER} more"] if len(unpriced) > LINES_ON_HOVER else []))
        lines.append((f"{_plural(len(unpriced), 'trade')} unpriced on {_short_date(as_of)}", hover, "watch"))
    if load.get("n_filled"):
        lines.append((f"{_plural(int(load['n_filled']), 'trade')} valued from an earlier close (the fill)",
                      "No price on the as-of date: each takes its own valuation from the last earlier business day "
                      "that has one, at most 5 back. Each trade's note is on the Trades tab.", "info"))
    return lines


def load_report_section(data: dict) -> html.Div:
    heading = html.Div(style={"display": "flex", "alignItems": "baseline", "gap": "6px"}, children=[
        about("Last load", LOAD_ABOUT, level="h4", style={"margin": "0 0 6px"}),
        pointer("Data", "book-load-data"), pointer("Trades", "book-load-trades")])
    if data.get("load_error"):
        return html.Div(id=LOAD_ID, style=_SECTION, children=[heading, message_box(data["load_error"])])
    items = []
    for text, hover, severity in load_report_lines(data.get("load"), str(data.get("as_of") or "")):
        style = {**_LINE, "cursor": "help"} if hover else _LINE
        if severity == "info":
            style = {**style, "color": MUTED}
        items.append(html.Li(text, title=hover or None, style=style))
    return html.Div(id=LOAD_ID, style=_SECTION, children=[heading, html.Ul(items, style=_LIST)])


# --------------------------------------------------------------------------- issues, body
def issue_items(data: dict, rows: Sequence[dict]) -> List[Tuple[str, str]]:
    """The Data issues drawer: each section's failure, the engines' book-level reasons, every
    row with an n/a or a partial figure, and the load's unpriced trades."""
    items: List[Tuple[str, str]] = []
    for key, label in (("spreads_error", "Spreads"), ("schedule_error", "Expiries"), ("needs_error", "Marks"),
                       ("limits_error", "Limits"), ("load_error", "Load")):
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
        if r["kind"] == SPREAD:
            for key in ("entry", "now", "move", "upu"):
                if r[key] is None:
                    lines.append(f"{key.replace('upu', '$ per unit')} n/a: {r[f'{key}_reason']}")
        if lines:
            items.append((r["label"], "; ".join(dict.fromkeys(lines))))
    for tid, why in (data.get("load") or {}).get("unpriced") or []:
        items.append((tid, f"no P&L on {data.get('as_of')}: {why}"))
    research = data.get("research") or {}
    if not research.get("available") and research.get("reason"):
        items.append(("Context", str(research["reason"])))
    return items


def caption_block(data: dict, rows: Sequence[dict]) -> html.Div:
    n_spreads = sum(1 for r in rows if r["kind"] == SPREAD)
    n_out = sum(1 for r in rows if r["kind"] == OUTRIGHT)
    children: List[Any] = [html.Div(className="meta-line", children=[
        html.Span(f"As of {_date_words(data.get('as_of'))}"),
        html.Span(f"{_plural(n_spreads, 'spread position')}, {_plural(n_out, 'outright contract')}")])]
    drawer = issues_drawer(issue_items(data, rows), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
    return html.Div(children)


def body(data: dict) -> html.Div:
    """The whole tab body from `gather`'s output."""
    rows = book_rows(data.get("spreads"), data.get("roots") or {})
    children: List[Any] = [caption_block(data, rows)]
    if not rows and not data.get("spreads_error"):
        children.append(message_box(f"No commodity futures in the book on {data.get('as_of') or 'this date'}: "
                                    "no spread or outright position to show."))
        children.append(html.Div(id=DETAIL_ID))
    else:
        children.append(book_section(data, rows))
    children.append(html.Div(style={"display": "grid", "gridTemplateColumns": "minmax(0, 1fr) minmax(0, 1fr)",
                                    "gap": "12px", "alignItems": "start", "marginTop": "12px"},
                             children=[needs_you_section(data), load_report_section(data)]))
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


def render_detail(active_cell, records, as_of: Optional[str], db_path) -> Any:
    """The detail of the clicked row (`active_cell` and the table's `data`), from the memoised
    spreads; None (nothing under the table) for a sector or Book line or no click."""
    if not active_cell or not records or not as_of:
        return None
    try:
        row = records[active_cell["row"]]
    except (IndexError, KeyError, TypeError):
        return None
    position_id = row.get("id") if isinstance(row, dict) else None
    if not position_id:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        data: Dict[str, Any] = {"as_of": as_of, "spreads": _spreads(conn, as_of)}
        try:
            data["research"] = _research(data["spreads"].get("positions") or [], as_of)
        except Exception as exc:  # noqa: BLE001
            data["research"] = {"available": False, "reason": _failure("the research statistics could not be read", exc),
                                "stats": {}}
        return detail_for(data, position_id)
    except Exception as exc:  # noqa: BLE001 -- the reason under the table, never a 500
        log.exception("book tab detail failed for %s", position_id)
        return message_box(f"The position's detail could not be built ({type(exc).__name__}: {exc}).")
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
    """Three callbacks: the body on the header's as-of, every data revision and the safety
    interval; then, only when the body went out with the VaR pending, the alerts again once the
    header's risk reading is worked out (chained on `VAR_PENDING_ID`, so it never holds up the
    body); and the row detail on a click in the table."""

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

    @app.callback(Output(DETAIL_ID, "children"), Input(TABLE_ID, "active_cell"), State(TABLE_ID, "data"),
                  State(AS_OF_STORE_ID, "data"), prevent_initial_call=True)
    def _detail(active_cell, records, as_of):
        return render_detail(active_cell, records, as_of, get_db_path())
