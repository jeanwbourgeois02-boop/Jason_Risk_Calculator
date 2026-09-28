"""P&L tab: "Where did the P&L come from?" (UI redesign wave 2, user 2026-09-28; rebuilt in the
screens tidy, wave 2, the same day).

A read-only attribution of the header's own figures. Every number is one of these, as given:
  - the trade valuations of the screens' shared filled reader,
    `ui.tabs.blotter_pricing.priced_value_book` (`value_book` with the fill of hard rule 2);
  - the header's period rule, `ui.tabs.header._priced_single` / `_priced_diff` over the
    reference close `engine.pnl.reference.resolve_reference` picks on the dates of
    `engine.pnl.ledger.period_reference_dates` (the step back of up to 5 business days, the
    "excl. N" / "filled N" / "ref <date>" markers), so the tab's Total line is the header's
    figure to the cent in every column;
  - the Book tab's rows (`ui.tabs.book.gather` / `book_rows` / `grouped_rows`): the spread
    positions of `engine.spreads.book_spreads`, the outright contracts, one row per other open
    trade and the settled line, named and grouped exactly as the Book names and groups them;
  - the header's LTD chart (`ui.tabs.header._build_chart`, memoised per day on the database's
    mtime), first on the tab and open by default.

Per-trade period figures (`period_rows`): for LTD a trade's own `pnl_usd`; for a period the
difference of its two valuations by exactly the header's rule (`_priced_diff`'s split,
`engine.pnl.reference.diff_split`): priced at both ends -> LTD(a) - LTD(ref); new since the
reference close -> its LTD; priced today but not on the reference close -> left out with that
reason; unpriced today -> a dash with `value_book`'s reason. A group adds those known figures up
and says "excl. N" otherwise (the display rule of CLAUDE.md "Header"); nothing is re-marked,
converted or filled here.

Layout: the title line (the question, the group-by switch: Position, Commodity, Sector, Product,
Trade; Download CSV), the LTD chart, then one attribution table with the five periods as columns
(Daily, 5d, MTD, YTD, LTD), its Total line the header's entry for every column, and under it
Realised and Open for LTD (the value rows' status: settled rows are the ledger's frozen figures);
one "Data issues (N)" drawer. Money in k / m with the full figure on hover; the Trade view keeps
full figures and pages. The tab has no date picker: it follows the header's as-of and re-renders
in place on the data revision and on its safety interval. `layout(default_date)` and
`register_callbacks(app, get_db_path)` are the shell's interface.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import Input, Output, State, dash_table, dcc, html

from ui.feed_controls import safety_refresh_ms
from ui.revision import DATA_REVISION_ID
from ui.tabs import header
from ui.tabs import ranking as rk
from ui.tabs.formatting import (
    MISSING, about, full_money, issues_drawer, marker, missing_cell, money_cell, sum_known,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

BODY_ID = "pnl-body"
REFRESH_ID = "pnl-refresh"
GROUP_ID = "pnl-group-by"
CSV_BUTTON_ID = "pnl-csv"
DOWNLOAD_ID = "pnl-download"
TABLE_ID = "pnl-table"
TRADES_TABLE_ID = "pnl-by-trade-table"
CHART_DETAILS_ID = "pnl-ltd-details"
CHART_SUMMARY_ID = f"{CHART_DETAILS_ID}-summary"
CHART_CONTAINER_ID = "pnl-ltd-chart-container"
ISSUES_ID = "pnl-issues"
CHART_HEIGHT = 220

NA = MISSING
PERIODS = ("daily", "d5", "mtd", "ytd", "ltd")
PERIOD_TITLES = {"daily": "Daily", "d5": "5d", "mtd": "MTD", "ytd": "YTD", "ltd": "LTD"}
PERIOD_TIPS = {"daily": "LTD today less LTD at the previous business day's close.",
               "d5": "LTD today less LTD five business days back.",
               "mtd": "LTD today less LTD at the last business day of the previous month.",
               "ytd": "LTD today less LTD at the last business day of the previous year.",
               "ltd": "Life to date: every trade's P&L at today's marks, settled trades frozen."}
GROUP_POSITION, GROUP_COMMODITY, GROUP_SECTOR, GROUP_PRODUCT, GROUP_TRADE = "position", "commodity", "sector", "product", "trade"
GROUP_OPTIONS = ((GROUP_POSITION, "Position"), (GROUP_COMMODITY, "Commodity"), (GROUP_SECTOR, "Sector"),
                 (GROUP_PRODUCT, "Product"), (GROUP_TRADE, "Trade"))
DEFAULT_GROUP = GROUP_POSITION
TOTAL_LABEL = "Total"
QUESTION = "where did the P&L come from"
_PRODUCT_LABELS = {"FX_SPOT": "FX spot", "FX_FWD": "FX forward", "FX_SWAP": "FX swap", "FUTURE": "Future",
                   "FX_OPTION": "FX option", "CMDTY_OPTION": "Option on future", "EQ_OPTION": "Listed option",
                   "LME_FWD": "LME forward"}
_STATUS_LABELS = {"OPEN": "Open", "SETTLED": "Settled", "CLOSED": "Closed out"}
LINES_ON_HOVER = 12
_MONO = {"textAlign": "right", "fontFamily": "monospace", "fontVariantNumeric": "tabular-nums",
         "padding": "4px 8px", "whiteSpace": "nowrap"}

TAB_ABOUT = ("Where did the P&L come from? The header's Daily, 5d, MTD, YTD and LTD attributed by position (as the "
             "Book names and groups them), commodity, sector, product or trade, each line the sum of its trades' "
             "figures by the header's own rule (priced trades only, a period over the trades priced at both ends, the "
             "reference close stepped back when it has no value); the Total line is the header; realised against open "
             "for LTD; the LTD line since the first trade. Nothing is re-marked here.")
COLUMN_TIPS = {"label": "The position, group or trade; its trades and, for a spread, its unmatched legs on hover.",
               "trades": "How many trades of the as-of book are in the line."}
REALISED_ABOUT = ("Realised = the settled trades, frozen by the ledger (realised_pnl) and never marked again; open = the "
                  "trades still marked (a closed-out option group is valued at its closing fill until expiry and counts "
                  "as open here). Each is the known LTD figures summed, excl. N otherwise; their sum is the header's LTD.")


# --------------------------------------------------------------------------- small helpers
def _num(value: Any) -> Optional[float]:
    v = rk.value(value)
    return v if isinstance(v, float) else None


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def message_box(message: str) -> html.P:
    return html.P(message, style={"color": "gray"})


def full_usd(v: float) -> str:
    return full_money(v)


# --------------------------------------------------------------------------- the period
class PeriodView:
    """One period's figures: `entry` (the header's whole-book entry with its markers), `rows`
    (one per trade on the as-of book: trade_id, instrument_id, product, status, trade_date,
    value (float or None), reason, note), `ref_iso` (the period's own reference date, '' for
    LTD), `ref_used` (the close measured from) and `blocked` (the trades priced today but not
    on the reference close, left out with that reason)."""

    def __init__(self, key: str, entry: dict, rows: pd.DataFrame, ref_iso: str = "", ref_used: str = "",
                 blocked: Sequence[str] = ()):
        self.key, self.entry, self.rows, self.ref_iso, self.ref_used = key, entry, rows, ref_iso, ref_used
        self.blocked = list(blocked)

    @property
    def title(self) -> str:
        return PERIOD_TITLES[self.key]


def _rows_frame(df: pd.DataFrame, values: Dict[str, Optional[float]], reasons: Dict[str, str],
                notes: Dict[str, str]) -> pd.DataFrame:
    out = df[["trade_id", "instrument_id", "product", "status", "trade_date"]].copy()
    out["value"] = [values.get(t) for t in out["trade_id"]]
    out["reason"] = [reasons.get(t, "") for t in out["trade_id"]]
    out["note"] = [notes.get(t, "") for t in out["trade_id"]]
    return out


def period_rows(conn: sqlite3.Connection, as_of: str, key: str, df_today: pd.DataFrame) -> PeriodView:
    """The per-trade figures of `key` (`PERIODS`) on `as_of`, by the header's rule (module
    docstring). The whole-book `entry` is `header._priced_single` / `_priced_diff` itself,
    with the fill and step-back markers `header._build_figures` puts beside the header's
    cards, so the Total line here is the header's figure."""
    from engine.pnl.calendar import load_holidays
    from engine.pnl.ledger import period_reference_dates
    from engine.pnl.reference import annotate, fill_caption, filled_from, resolve_reference
    from ui.tabs.blotter_pricing import priced_value_book

    try:
        root_reason = header._root_reason(conn, as_of, header._needs_cached(conn, as_of))
    except Exception:  # noqa: BLE001 -- the plain fallback sentence, as the header does
        root_reason = header._root_reason(conn, as_of)
    if df_today.empty:
        return PeriodView(key, dict(header._EMPTY_PRICED), _rows_frame(df_today, {}, {}, {}))

    reasons_today = {t: str(r or "") for t, r in zip(df_today["trade_id"], df_today["reason"])}
    notes_today = {t: str(n or "") for t, n in zip(df_today["trade_id"], df_today.get("note", [""] * len(df_today)))}
    a_pnl = {t: float(v) for t, v, r in zip(df_today["trade_id"], df_today["pnl_usd"], df_today["reason"])
             if not r and v == v}

    if key == "ltd":
        entry = header._priced_single(df_today, root_reason)
        n_filled = header._filled_count(df_today)
        if n_filled and entry.get("available"):
            entry["markers"] = [(f"filled {n_filled}",
                                 header._joined(fill_caption(df_today, as_of), header._fill_notes(df_today)))]
        values = {t: a_pnl.get(t) for t in df_today["trade_id"]}
        notes = {t: n for t, n in notes_today.items() if filled_from(n)}
        return PeriodView(key, entry, _rows_frame(df_today, values, reasons_today, notes))

    holidays = load_holidays()
    ref_iso = period_reference_dates(as_of)[key]
    backfill = header.backfill_status(conn)
    title = PERIOD_TITLES[key]

    def _book(iso: str) -> pd.DataFrame:
        return priced_value_book(conn, iso)[0]

    choice = resolve_reference(df_today, ref_iso, _book, holidays, frames_filled=True)
    entry = header._priced_diff(df_today, choice.frame, root_reason, choice.ref_date_used,
                                header._reference_reason(conn, ref_iso, title, backfill))
    entry = annotate(entry, choice,
                     lambda s: header._reference_reason(conn, s.date, title, backfill)(s.n_blocked, s.n_open_then))
    if entry.get("available"):
        entry["markers"] = header._reference_markers(entry, choice)
    split = choice.split
    ref_used = choice.ref_date_used
    frame_b = choice.frame
    b_pnl = ({t: float(v) for t, v, r in zip(frame_b["trade_id"], frame_b["pnl_usd"], frame_b["reason"]) if not r and v == v}
             if not frame_b.empty else {})
    b_notes = ({t: str(n or "") for t, n in zip(frame_b["trade_id"], frame_b.get("note", [""] * len(frame_b)))}
               if not frame_b.empty else {})
    values: Dict[str, Optional[float]] = {}
    reasons: Dict[str, str] = {}
    notes: Dict[str, str] = {}
    unusable = not entry.get("available")
    for t in df_today["trade_id"]:
        if unusable:
            values[t] = None
            reasons[t] = str(entry.get("reason") or f"{title} has no usable reference close")
        elif t in split.contributing_b_ids:
            values[t] = a_pnl[t] - b_pnl[t]
            fill_note = b_notes.get(t, "")
            notes[t] = f"reference close {ref_used}: {fill_note}" if filled_from(fill_note) else ""
        elif t in split.contributing_a_ids:
            values[t] = a_pnl[t]
            notes[t] = f"new since the {ref_used} close: its whole LTD counts (trading P&L)"
        elif t in split.blocked_ids:
            values[t] = None
            reasons[t] = f"priced today but not on the {ref_used} close: left out of {title} rather than faked"
        else:
            values[t] = None
            reasons[t] = reasons_today.get(t) or f"no P&L on {as_of}"
        if filled_from(notes_today.get(t, "")) and values[t] is not None:
            notes[t] = header._joined(notes[t], notes_today[t])
    return PeriodView(key, entry, _rows_frame(df_today, values, reasons, notes), ref_iso, ref_used,
                      sorted(split.blocked_ids))


def realised_entries(df_today: pd.DataFrame) -> Dict[str, dict]:
    """{'settled', 'open'}: each the known LTD figures of those trades summed (the header's
    `_priced_single` over the status subset), so the two add to the header's LTD."""
    out = {}
    for name, statuses in (("settled", ("SETTLED",)), ("open", ("OPEN", "CLOSED"))):
        subset = df_today[df_today["status"].isin(statuses)] if not df_today.empty else df_today
        entry = header._priced_single(subset, "no trade of this kind has a figure")
        entry["count"] = int(len(subset))
        out[name] = entry
    return out


# --------------------------------------------------------------------------- gathering
def _labelled(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """The trades with the Trades tab's descriptive columns (commodity, sector, exchange), looked
    up by `ui.tabs.blotter.add_instrument_fields`, never computed."""
    from ui.tabs.blotter import add_instrument_fields
    rows = df[["trade_id", "instrument_id", "product", "status", "trade_date"]].copy()
    if rows.empty:
        for col in ("commodity", "sector", "exchange"):
            rows[col] = pd.Series(dtype=object)
        return rows
    return add_instrument_fields(conn, rows)


def gather(conn: sqlite3.Connection, as_of: str) -> dict:
    """The Book tab's data (its rows, its Daily / MTD / LTD per trade, the spreads) plus the 5d
    and YTD per-trade figures and the trades' descriptive fields, in one pricing snapshot."""
    from ui.tabs import book
    from ui.tabs.blotter_pricing import pricing_snapshot
    with pricing_snapshot(conn, "P&L tab"):
        data = book.gather(conn, as_of)
        for key in ("d5", "ytd"):
            if key in data["periods"]:
                continue
            try:
                data["periods"][key] = period_rows(conn, as_of, key, data["df"])
            except Exception as exc:  # noqa: BLE001 -- the column is then dashes with the reason
                log.exception("P&L tab: %s figures failed for %s", key, as_of)
                data["periods_error"] = data.get("periods_error") or f"the {PERIOD_TITLES[key]} figures could not be built ({type(exc).__name__}: {exc})"
        try:
            data["labelled"] = _labelled(conn, data["df"])
        except Exception as exc:  # noqa: BLE001
            log.exception("P&L tab: instrument fields failed for %s", as_of)
            data["labelled"], data["labelled_error"] = None, f"the commodity and sector of each trade could not be read ({type(exc).__name__}: {exc})"
    return data


# --------------------------------------------------------------------------- the lines
def _period_of(data: dict, key: str, trade_ids: Sequence[str]) -> Tuple[Optional[float], int, List[str], str]:
    """(value, excluded, reasons, note) of a set of trades for a period (the Book's own reading of
    `period_rows`: the known figures summed, display)."""
    from ui.tabs.book import _period_of as book_period_of
    return book_period_of(data, key, trade_ids)


def _five(data: dict, trade_ids: Sequence[str]) -> Dict[str, Tuple[Optional[float], int, List[str], str]]:
    return {key: _period_of(data, key, trade_ids) for key in PERIODS}


def _leftover_words(data: dict, row: dict) -> str:
    """A spread row's unmatched legs, for its hover ('unmatched legs: CLF27 +2 lots')."""
    if row.get("kind") != "spread":
        return ""
    result = data.get("spreads") or {}
    p = next((p for p in result.get("positions") or [] if str(p.get("position_id") or p.get("name")) == row["id"]), None)
    left = [e for e in ((p or {}).get("leftover") or []) if _num(e.get("lots"))]
    if not left:
        return ""
    return "unmatched legs (the open lots the spread's ratio does not match, an outright of their own): " + "; ".join(
        f"{str(e.get('root_id') or '').split(':')[-1]} {_num(e.get('lots')):+g} lot(s)" for e in left)


def position_lines(data: dict) -> List[Tuple[str, List[dict]]]:
    """[(group, lines)] as the Book groups its rows by instrument: the futures (spread positions
    and outrights), the options on futures, the LME forwards, the FX hedges, Other, the settled
    line; each line with its five period figures."""
    from ui.tabs import book
    rows = book.book_rows(data)
    out = []
    for label, members in book.grouped_rows(rows, book.GROUP_INSTRUMENT):
        lines = []
        for r in members:
            hover = "; ".join(x for x in (r["name_hover"].replace(" Click for its trades.", "").replace(
                " Click for its entries and legs.", "").replace(" Click for the trade.", "").replace(" Click for the list.", ""),
                                          _leftover_words(data, r)) if x)
            lines.append({"id": r["id"], "label": r["name"], "hover": hover, "trades": len(r["trade_ids"]),
                          "trade_ids": r["trade_ids"], "five": _five(data, r["trade_ids"])})
        out.append((label, lines))
    return out


def group_lines(data: dict, by: str) -> List[Tuple[str, List[dict]]]:
    """One group of lines: the trades' five period figures summed per commodity, sector or
    product (the Trades tab's descriptive fields), largest |Daily| first."""
    df = data.get("labelled")
    if df is None or df.empty:
        return []
    if by == GROUP_PRODUCT:
        keys = [_PRODUCT_LABELS.get(str(p), str(p).replace("_", " ").capitalize()) for p in df["product"]]
    else:
        keys = [str(v or "") or ("Other" if by == GROUP_SECTOR else "no commodity") for v in df[by]]
    if by == GROUP_SECTOR:
        keys = [k.replace("_", " ").capitalize() for k in keys]
    groups: Dict[str, List[str]] = {}
    for k, tid in zip(keys, df["trade_id"]):
        groups.setdefault(k, []).append(str(tid))
    lines = []
    for label, tids in groups.items():
        lines.append({"id": f"{by}-{label}", "label": label, "hover": f"{_plural(len(tids), 'trade')}: {', '.join(tids[:LINES_ON_HOVER])}"
                      + (f" and {len(tids) - LINES_ON_HOVER} more" if len(tids) > LINES_ON_HOVER else ""),
                      "trades": len(tids), "trade_ids": tids, "five": _five(data, tids)})
    lines.sort(key=lambda ln: (ln["five"]["daily"][0] is None, -abs(ln["five"]["daily"][0] or 0.0)))
    return [("", lines)]


# --------------------------------------------------------------------------- the table
def _money_td(value: Optional[float], excluded: int, reasons: Sequence[str], note: str = "", bold: bool = False) -> html.Td:
    hover_parts = []
    if excluded:
        hover_parts.append(f"excludes {excluded}: " + "; ".join(reasons[:LINES_ON_HOVER])
                           + (f"; and {len(reasons) - LINES_ON_HOVER} more" if len(reasons) > LINES_ON_HOVER else ""))
    if note:
        hover_parts.append(note)
    if value is None:
        return html.Td(missing_cell("; ".join(reasons[:LINES_ON_HOVER]) or "no figure"))
    children: List[Any] = [money_cell(value, hover="\n".join(hover_parts))]
    if excluded:
        children.append(html.Span(f"excl. {excluded}", className="marker", title="\n".join(hover_parts)))
    return html.Td(children, style={"fontWeight": 700} if bold else None)


def _entry_td(entry: dict, bold: bool = True) -> html.Td:
    """The header's own entry as a cell: its value with its markers, or a dash with its reason."""
    if not entry.get("available"):
        return html.Td(missing_cell(str(entry.get("reason") or "no figure")))
    v = float(entry["value"])
    children: List[Any] = [money_cell(v)]
    for short, sentence, *_rest in header._entry_markers(entry):
        m = marker(short, sentence)
        if m is not None:
            children.append(m)
    return html.Td(children, style={"fontWeight": 700} if bold else None)


def _head(by: str) -> html.Thead:
    cells = [html.Th("Position" if by == GROUP_POSITION else dict(GROUP_OPTIONS)[by], className="l", title=COLUMN_TIPS["label"]),
             html.Th("Trades", title=COLUMN_TIPS["trades"])]
    cells += [html.Th(PERIOD_TITLES[k], title=PERIOD_TIPS[k]) for k in PERIODS]
    return html.Thead(html.Tr(cells))


def line_tr(line: dict) -> html.Tr:
    cells: List[Any] = [html.Td(line["label"], className="l book-name", title=line["hover"] or None),
                        html.Td(str(line["trades"]), className="pnl-count")]
    for key in PERIODS:
        value, excluded, reasons, note = line["five"][key]
        cells.append(_money_td(value, excluded, reasons, note))
    return html.Tr(cells, className="pnl-row")


def group_tr(label: str, lines: Sequence[dict]) -> html.Tr:
    cells: List[Any] = [html.Td(label, className="l", title=_plural(len(lines), "line")),
                        html.Td(str(sum(ln["trades"] for ln in lines)), className="pnl-count")]
    for key in PERIODS:
        pairs = [(ln["five"][key][0], f"{ln['label']}: {'; '.join(ln['five'][key][2]) or 'no figure'}") for ln in lines]
        total, excluded, reasons = sum_known(pairs)
        inner = sum(ln["five"][key][1] for ln in lines if ln["five"][key][0] is not None)
        for ln in lines:
            if ln["five"][key][0] is not None and ln["five"][key][1]:
                reasons.append(f"{ln['label']}: {'; '.join(ln['five'][key][2])}")
        cells.append(_money_td(total, excluded + inner, reasons))
    return html.Tr(cells, className="book-group")


def total_tr(data: dict, n_trades: int) -> html.Tr:
    cells: List[Any] = [html.Td([TOTAL_LABEL, html.Span("= header", className="book-note")], className="l",
                                title="The header's own figure for each period: every trade of the as-of book is in one "
                                      "line above, the known figures summed, what is left out named."),
                        html.Td(str(n_trades), className="pnl-count")]
    for key in PERIODS:
        view = (data.get("periods") or {}).get(key)
        if view is None:
            cells.append(html.Td(missing_cell(data.get("periods_error") or f"the {PERIOD_TITLES[key]} figures could not be built")))
        else:
            cells.append(_entry_td(view.entry))
    return html.Tr(cells, className="book-total")


def realised_trs(data: dict) -> List[html.Tr]:
    """Two lines under the total: Realised (settled) and Open, for LTD; the other columns empty."""
    parts = realised_entries(data["df"])
    out = []
    for key, label in (("settled", "Realised (settled)"), ("open", "Open")):
        entry = parts[key]
        cells: List[Any] = [html.Td(label, className="l", title=REALISED_ABOUT), html.Td(str(entry["count"]), className="pnl-count")]
        cells += [html.Td("") for _k in PERIODS[:-1]]
        cells.append(_entry_td(entry, bold=False))
        out.append(html.Tr(cells, className="pnl-realised"))
    return out


def attribution_table(data: dict, by: str) -> html.Div:
    groups = position_lines(data) if by == GROUP_POSITION else group_lines(data, by)
    body: List[Any] = []
    for label, lines in groups:
        if label:
            body.append(group_tr(label, lines))
        body.extend(line_tr(ln) for ln in lines)
    n_trades = int(len(data["df"])) if data.get("df") is not None else 0
    body.append(total_tr(data, n_trades))
    body.extend(realised_trs(data))
    return html.Div(className="book-card", children=[html.Table([_head(by), html.Tbody(body)], id=TABLE_ID,
                                                                className="book-table pnl-table")])


def trade_records(data: dict) -> Tuple[List[dict], List[dict]]:
    """One record per trade at full figures for every period, its reasons and notes on hover."""
    df = data.get("labelled")
    if df is None:
        df = data["df"][["trade_id", "instrument_id", "product", "status", "trade_date"]].copy()
        df["commodity"] = ""
    views = data.get("periods") or {}
    values = {key: dict(zip(views[key].rows["trade_id"], views[key].rows["value"])) if key in views else {} for key in PERIODS}
    reasons = {key: dict(zip(views[key].rows["trade_id"], views[key].rows["reason"])) if key in views else {} for key in PERIODS}
    notes = {key: dict(zip(views[key].rows["trade_id"], views[key].rows["note"])) if key in views else {} for key in PERIODS}
    records, tips = [], []
    for r in df.itertuples(index=False):
        tid = str(r.trade_id)
        rec = {"trade_id": tid, "instrument_id": str(r.instrument_id), "commodity": str(getattr(r, "commodity", "") or ""),
               "product": _PRODUCT_LABELS.get(str(r.product), str(r.product)),
               "status": _STATUS_LABELS.get(str(r.status), str(r.status)), "trade_date": str(r.trade_date)}
        tip: Dict[str, dict] = {}
        for key in PERIODS:
            v = _num(values[key].get(tid))
            rec[key] = v if v is not None else NA
            why, note = str(reasons[key].get(tid) or ""), str(notes[key].get(tid) or "")
            if v is None:
                tip[key] = {"value": why or (data.get("periods_error") or "no figure"), "type": "text"}
            elif note:
                tip[key] = {"value": note, "type": "text"}
        records.append(rec)
        tips.append(tip)
    return records, tips


def trades_table(data: dict) -> html.Div:
    records, tips = trade_records(data)
    if not records:
        return message_box("No trades on the book at this date.")
    columns = [rk.text("Trade", "trade_id"), rk.text("Instrument", "instrument_id"), rk.text("Commodity / pair", "commodity"),
               rk.text("Product", "product"), rk.text("Status", "status"), rk.text("Traded", "trade_date")]
    columns += [rk.numeric(PERIOD_TITLES[k], k, rk.amount(nully=NA)) for k in PERIODS]
    table = dash_table.DataTable(
        id=TRADES_TABLE_ID, columns=columns, data=records, tooltip_data=tips, tooltip_delay=0, tooltip_duration=None,
        **rk.sortable(TRADES_TABLE_ID), page_action="native", page_size=25,
        style_table={"overflowX": "auto"}, style_cell=_MONO,
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                for c in ("trade_id", "instrument_id", "commodity", "product", "status", "trade_date")],
        style_header={"fontWeight": "bold"},
        style_data_conditional=rk.sign_styles(list(PERIODS), nil={"color": "#9ca3af"}),
    )
    n_trades = int(len(data["df"]))
    total = html.Table([_head(GROUP_TRADE), html.Tbody([total_tr(data, n_trades), *realised_trs(data)])],
                       className="book-table pnl-table")
    return html.Div([table, html.Div(className="book-card", style={"marginTop": "8px"}, children=[total])])


# --------------------------------------------------------------------------- data issues
def issue_items(data: dict) -> List[Any]:
    items: List[Any] = []
    for key in ("periods_error", "spreads_error", "labelled_error"):
        if data.get(key):
            items.append(("P&L", data[key]))
    views = data.get("periods") or {}
    for key in PERIODS:
        view = views.get(key)
        if view is not None and not view.entry.get("available"):
            items.append((PERIOD_TITLES[key], str(view.entry.get("reason") or "no figure")))
    df = data.get("df")
    if df is not None and not df.empty:
        for tid in df["trade_id"]:
            missing, why = [], ""
            for key in PERIODS:
                view = views.get(key)
                if view is None or view.rows.empty:
                    continue
                sub = view.rows[view.rows["trade_id"] == tid]
                if not sub.empty and _num(sub["value"].iloc[0]) is None and str(sub["reason"].iloc[0] or ""):
                    missing.append(PERIOD_TITLES[key])
                    why = why or str(sub["reason"].iloc[0])
            if missing:
                items.append((str(tid), f"no figure for {', '.join(missing)}: {why}"))
        ltd = views.get("ltd")
        if ltd is not None and not ltd.rows.empty:
            for tid, note in zip(ltd.rows["trade_id"], ltd.rows["note"]):
                if "no price on" in str(note or ""):
                    items.append((str(tid), str(note)))
    spreads = data.get("spreads") or {}
    review = spreads.get("review") or []
    if review:
        items.append(("Spreads", f"{_plural(len(review), 'set')} of trades spreads-engine could not group, listed as "
                                 "outrights here (the Book tab names them)."))
    for reason in spreads.get("reasons") or []:
        items.append(("Spreads", str(reason)))
    return items


# --------------------------------------------------------------------------- the LTD chart
def chart_section() -> html.Details:
    """The LTD chart, first on the tab and open by default; its body is filled by the chart
    callback while it is open."""
    return html.Details(id=CHART_DETAILS_ID, className="section details pnl-chart", open=True, children=[
        html.Summary("LTD since the first trade", id=CHART_SUMMARY_ID,
                     title="The LTD line over every business day since the first trade: each day the sum of its "
                           "priced trades, the excluded count and the fill on hover, a gap where nothing priced."),
        html.Div(id=CHART_CONTAINER_ID)])


_SUMMARY_OPEN_MIRROR_JS = (
    "function(n_clicks) {\n"
    "    if (!n_clicks) { return window.dash_clientside.no_update; }\n"
    f"    var details = document.getElementById({CHART_DETAILS_ID!r});\n"
    "    if (!details) { return window.dash_clientside.no_update; }\n"
    "    return Boolean(details.open);\n"
    "}"
)


# --------------------------------------------------------------------------- body and shell
def body(data: dict, by: str = DEFAULT_GROUP) -> html.Div:
    by = by if by in dict(GROUP_OPTIONS) else DEFAULT_GROUP
    if data.get("df") is None or data["df"].empty:
        return html.Div(className="pnl-body", children=[message_box(f"No trades on the book on {data.get('as_of')}.")])
    children: List[Any] = [trades_table(data) if by == GROUP_TRADE else attribution_table(data, by)]
    drawer = issues_drawer(issue_items(data), id=ISSUES_ID)
    if drawer is not None:
        children.append(drawer)
    return html.Div(className="pnl-body", children=children)


def csv_frame(data: dict, by: str) -> pd.DataFrame:
    if by == GROUP_TRADE:
        records, _tips = trade_records(data)
        return pd.DataFrame(records)
    records = []
    for label, lines in (position_lines(data) if by == GROUP_POSITION else group_lines(data, by)):
        for ln in lines:
            rec = {"group": label, "line": ln["label"], "trades": ln["trades"]}
            for key in PERIODS:
                value, excluded, _r, _n = ln["five"][key]
                rec[f"{key}_usd"], rec[f"{key}_excluded"] = value, excluded
            rec["trade_ids"] = " ".join(ln["trade_ids"])
            records.append(rec)
    return pd.DataFrame(records)


def _open(db_path):
    from ui.app import connect_readonly       # local: ui.app imports the tabs
    return connect_readonly(db_path)


def render(as_of: Optional[str], db_path, by: str = DEFAULT_GROUP) -> Any:
    if not as_of:
        return message_box("No as-of date available.")
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError as exc:
        return message_box(f"Database not available ({exc}).")
    try:
        return body(gather(conn, as_of), by or DEFAULT_GROUP)
    except Exception as exc:  # noqa: BLE001 -- the reason on screen, never a blank tab
        log.exception("P&L tab failed for as_of=%s", as_of)
        return html.Div(className="status-panel status-panel--down", children=[
            html.P(f"The P&L attribution could not be built for {as_of} ({type(exc).__name__}: {exc}).",
                   className="status-line status-line--bad")])
    finally:
        conn.close()


def render_csv(as_of: Optional[str], db_path, by: str):
    if not as_of:
        return None
    try:
        conn = _open(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        frame = csv_frame(gather(conn, as_of), by if by in dict(GROUP_OPTIONS) else DEFAULT_GROUP)
        return dcc.send_data_frame(frame.to_csv, f"pnl-{as_of}.csv", index=False)
    except Exception:  # noqa: BLE001
        log.exception("P&L csv failed for %s", as_of)
        return None
    finally:
        conn.close()


def layout(default_date: Optional[str] = None) -> html.Div:
    """The static shell: the title line (the question, the group-by switch, Download CSV), the
    LTD chart open by default, the body the callback fills and the safety interval. No date
    picker: the tab follows the header's as-of store."""
    return html.Div(className="pnl-tab", children=[
        html.Div(className="book-title-row", children=[
            about("P&L", TAB_ABOUT, level="h3"),
            html.Span(QUESTION, className="book-counts"),
            dcc.RadioItems(id=GROUP_ID, className="book-switch",
                           options=[{"label": label, "value": value} for value, label in GROUP_OPTIONS],
                           value=DEFAULT_GROUP, inline=True, persistence=True, persistence_type="session"),
            html.Button("Download CSV", id=CSV_BUTTON_ID, n_clicks=0, className="book-download",
                        title="The table as shown, at full figures"),
            dcc.Download(id=DOWNLOAD_ID),
        ]),
        chart_section(),
        html.Div(id=BODY_ID, children=[message_box("Loading the P&L attribution...")]),
        dcc.Interval(id=REFRESH_ID, interval=safety_refresh_ms(), n_intervals=0),
    ])


build_layout = layout


def register_callbacks(app, get_db_path: Callable[[], object]) -> None:
    """The body re-renders on the header's as-of, the group switch, every data revision and the
    safety interval; the CSV on its button. The LTD chart (moved from the header, 2026-09-28)
    keeps the header's two callbacks on this tab's ids: a clientside mirror of the collapsible's
    DOM `open` state (Dash never syncs a native <details> toggle to `open`), and the chart itself,
    built only while the collapsible is open (`header._build_chart`, memoised per day on the
    database), about 220 px tall."""

    @app.callback(
        Output(BODY_ID, "children"),
        Input(AS_OF_STORE_ID, "data"),
        Input(GROUP_ID, "value"),
        Input(DATA_REVISION_ID, "data"),
        Input(REFRESH_ID, "n_intervals"),
    )
    def _update(as_of, by=DEFAULT_GROUP, _data_rev=None, _n_intervals=0):
        return render(as_of, get_db_path(), by or DEFAULT_GROUP)

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_BUTTON_ID, "n_clicks"), State(AS_OF_STORE_ID, "data"),
                  State(GROUP_ID, "value"), prevent_initial_call=True)
    def _csv(n_clicks, as_of, by):
        if not n_clicks:
            return dash.no_update
        return render_csv(as_of, get_db_path(), by or DEFAULT_GROUP)

    app.clientside_callback(
        _SUMMARY_OPEN_MIRROR_JS,
        Output(CHART_DETAILS_ID, "open"),
        Input(CHART_SUMMARY_ID, "n_clicks"),
    )

    @app.callback(
        Output(CHART_CONTAINER_ID, "children"),
        Input(CHART_DETAILS_ID, "open"),
        Input(AS_OF_STORE_ID, "data"),
        Input(DATA_REVISION_ID, "data"),
    )
    def _update_chart(is_open: bool, as_of: Optional[str], _data_rev=None):
        if not is_open or not as_of:
            return dash.no_update
        from ui.app import connect_readonly
        db_path = get_db_path()
        try:
            conn = connect_readonly(db_path)
        except sqlite3.OperationalError as exc:
            return html.P(f"Database not available ({exc}).")
        try:
            graph = header._build_chart(conn, as_of, db_path=db_path)
            if isinstance(graph, dcc.Graph) and isinstance(graph.figure, dict):
                graph.figure.setdefault("layout", {})["height"] = CHART_HEIGHT
                graph.figure["layout"]["margin"] = {"l": 50, "r": 20, "t": 6, "b": 26}
            return graph
        except Exception as exc:  # noqa: BLE001 -- the reason on screen, never an empty panel
            log.exception("P&L LTD chart failed for as_of=%s", as_of)
            return html.P(header._failure_reason("LTD chart could not be built", exc, conn), className="section-kicker")
        finally:
            conn.close()
