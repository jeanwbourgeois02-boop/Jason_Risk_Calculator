"""The Book's "By contract" view, and the contract grouping the P&L tab's Contract slice shares
(user, 2026-09-30).

One row per contract actually held, netted across every trade that holds it: what Jason holds
at the exchange and at each clearing broker, to check against the broker statements, first
notice and margin. A contract is exchange-specific (SHFE, COMEX and LME copper are three rows);
its key is the engine's own leg key (`engine.spreads.trades._contract_key`: a future or an option
on one its instrument, an LME ticket '<metal> <prompt>', an FX trade '<pair> <value date>'), so a
contract row gathers exactly the trade legs `trade_book` gives under that key, and every fill
sits on exactly one contract row: the rows add up to the trade view's total, and to the header.

What is read, never recomputed: the legs of `engine.spreads.trade_book` (through the Book's
`gather`: position, average fill, mark, its source and time, the previous close), the per-fill
period figures the Book sums (`book.fill_sum`, the header's own split), the file's account per
fill (the clearer), and `engine.expiry.expiry_schedule` for the contract's next date. Netting the
legs' positions into a contract row and summing the fills' figures is display.

The view lives in the Book's one table area: a switch in the table's strip picks By trade (the
default) or By contract; the total row shows once. Filters are the column funnels of the kit
(`trade_filter`), stored under the Book's key with a `c-` prefix so the two views never read each
other's column filters; the shared Type, Trade and Commodity lists keep a contract held by a trade
they keep.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

import dash
import pandas as pd
from dash import ALL, Input, Output, State, dcc, html

from ui.tabs import book as bk
from ui.tabs import trade_filter as tf
from ui.tabs.formatting import (
    MISSING, day_text, full_signed, marker, missing_cell, plain_words, price_text, row_info, sign_class,
)

log = logging.getLogger(__name__)

TAB = bk.TAB
VIEW_ID = "book-view"                          # the By trade | By contract switch, in the table's strip
VIEW_TRADE, VIEW_CONTRACT = "trade", "contract"
VIEW_OPTIONS = [{"label": "By trade", "value": VIEW_TRADE}, {"label": "By contract", "value": VIEW_CONTRACT}]
VIEW_ABOUT = ("By trade: one row per trade (a PBRoot name), every leg and hedge under it. By contract: one row "
              "per contract held, netted across every trade that holds it, with its clearer, to check against "
              "the broker statements.")
ROW_TYPE = "book-contract-row"                 # a contract row: {"type", "idx": contract key}
SORT_TYPE = "book-contract-sort"               # a column title: {"type", "idx": column key}
FOLD_TYPE = "book-contract-fold"               # the closed fold: {"type", "idx": "closed"}
OPEN_STORE_ID = "book-open-contracts"          # session: the contract keys whose panel is open
SORT_STORE_ID = "book-contract-sort-store"     # session: {"key", "dir"} or None
FOLD_STORE_ID = "book-contract-folds"          # session: {"closed": bool}
LINK_IDX = "bc-"                               # a panel's link to a trade: tf.link(..., "book", trade, "bc-<key>")
COL = bk.CONTRACT_COL                          # 'c-': the view's own column filters under the Book's key

# The columns: key, title, alignment class ('l' = left), the title's one-sentence definition.
COLUMNS: Tuple[Tuple[str, str, str, str], ...] = (
    ("contract", "Contract", "l", "The contract as the exchange lists it, one row per contract netted across every "
                                  "trade that holds it: SHFE, COMEX and LME copper are separate rows. Click a row for "
                                  "the trades holding it."),
    ("clearer", "Clearer", "l", "The clearing broker, from the file's account of each fill; the split per clearer on "
                                "hover when a contract sits at more than one."),
    ("net", "Net", "", "The net position across every trade: signed lots, an LME metal in tonnes, an FX hedge in "
                       "its base currency. The figure to check against the broker statement."),
    ("gross", "Gross", "", "The trades' positions added without their signs: a contract whose trades net to zero "
                           "still shows what each holds."),
    ("trades", "Trades", "l", "The trades holding the contract; each one's position on hover."),
    ("now", "Price now", "", "The contract's latest official mark at its tick; the source, the time and the previous close "
                       "on hover."),
    ("daily", "P&L today", "", "Today's P&L in USD of every fill in the contract, across its trades."),
    ("ltd", "P&L since entry", "", "The P&L since the first fill, in USD."),
    ("next", "Next date", "l", "The contract's next key date: first notice, last trade, option expiry, LME prompt or an FX "
                          "value date; red within 3 business days, amber within 10, grey ≈ when estimated."),
)
N_BEFORE_MONEY = 6                             # the columns before Daily (the fold and total rows' spans)
SORTABLE = {k for k, *_ in COLUMNS}
NUMBER_FUNNELS = {"net": "The net position (lots; tonnes for LME; the base currency for FX).",
                  "gross": "The gross position.", "daily": "Today's P&L in USD.", "ltd": "The P&L since the first fill, in USD."}
CLEARERS = {"BOCF": "BOC", "GSIL": "Goldman"}  # the account's prefix -> the clearer's name
NO_CLEARER = "Not recorded"
METAL_FAMILIES = {"XAU": "gold", "XAG": "silver", "XPT": "platinum", "XPD": "palladium"}
FX_KEYED = {"FX_SPOT", "FX_FWD", "FX_SWAP"}    # keyed '<pair> <value date>', as the engine keys them
_EPS = 1e-9


# --------------------------------------------------------------------------- small helpers
def clearer_name(account: Any) -> str:
    """The clearer from the file's account ('BOCF-FUT-NMMF' -> 'BOC', 'GSIL-FUT-NMMF' -> 'Goldman'),
    the account as written for any other prefix, '' when the cell is empty."""
    acc = str(account or "").strip()
    if not acc:
        return ""
    return CLEARERS.get(re.split(r"[-_ ]", acc, maxsplit=1)[0].upper(), acc)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _lines(*parts: Any) -> str:
    return "\n".join(str(p) for p in parts if p)


def _info(conn: sqlite3.Connection, as_of: str) -> dict:
    """{accounts: {trade_id: account}, instruments: {id: (base_ccy, expiry_date)}, schedule: {contract
    id: expiry_schedule row}, schedule_reason}, memoised per database revision and as-of."""
    def compute():
        out: Dict[str, Any] = {"accounts": {}, "instruments": {}, "schedule": {}, "schedule_reason": ""}
        try:
            out["accounts"] = {str(t): str(a or "") for t, a in conn.execute("SELECT trade_id, account FROM trades")}
        except sqlite3.Error:
            pass
        try:
            out["instruments"] = {str(i): (str(b or ""), str(e or "")) for i, b, e in conn.execute(
                "SELECT instrument_id, base_ccy, expiry_date FROM instruments")}
        except sqlite3.Error:
            pass
        try:
            from engine.expiry import expiry_schedule
            out["schedule"] = {str(r.get("contract_id")): r for r in expiry_schedule(conn, as_of).get("rows") or []}
        except Exception as exc:  # noqa: BLE001 -- the Next cells say why
            log.exception("Book by contract: the expiry schedule could not be read for %s", as_of)
            out["schedule_reason"] = f"the expiry schedule could not be read ({type(exc).__name__}: {exc})"
        return out
    return bk._memo("contract-info", conn, as_of, compute)


def _family(data: dict, leg: dict) -> str:
    """The contract's commodity family in words ('Copper', 'Crude', 'FX', 'Gold' for an XAUUSD
    forward), the trade view's words; 'Other' for a contract not recognised."""
    from engine.spreads.trades import family_of
    root = (data.get("roots") or {}).get(str(leg.get("root_id") or ""))
    fam = family_of(root) if root is not None else ""
    product = str(leg.get("product") or "")
    if not fam and (product in bk.NOTIONAL_PRODUCTS):
        fam = METAL_FAMILIES.get(str(leg.get("instrument_id") or "")[:3], "fx")
    if not fam:
        return "Other"
    return "FX" if fam.lower() == "fx" else fam[0].upper() + fam[1:]


def _pseudo_legs(data: dict, t: dict, info: dict) -> List[dict]:
    """The fills on no trade name as legs of the engine's shape (they carry no legs of their own):
    grouped under the engine's contract key, their position the open fills' quantities added."""
    from engine.spreads.trades import leg_name
    df = bk._df_rows(data, t.get("trade_ids") or [])
    by = {str(r["trade_id"]): r for r in df.to_dict("records")} if not df.empty else {}
    fills = data.get("fills") or {}
    roots = data.get("roots") or {}
    legs: Dict[str, dict] = {}
    for tid in t.get("trade_ids") or []:
        f, r = fills.get(str(tid)) or {}, by.get(str(tid)) or {}
        inst = str(f.get("instrument_id") or r.get("instrument_id") or "")
        product = str(f.get("product") or r.get("product") or "")
        date = str(r.get("settle_date") or "")[:10]
        key = f"{inst} {date}".strip() if product == "LME_FWD" or product in FX_KEYED else inst
        base = (info["instruments"].get(inst) or ("", ""))[0]
        root = roots.get(base)
        if product == "LME_FWD" and root is not None:
            name = leg_name(root, date[:7], date, inst)
        else:
            name = bk._fill_name(inst, product, roots)
        leg = legs.setdefault(key, {
            "contract_id": key, "instrument_id": inst, "product": product, "root_id": base, "name": name,
            "exchange": getattr(root, "exchange", "") if root is not None else "", "commodity": "",
            "month": date[:7] if product == "LME_FWD" else "", "prompt": date if product == "LME_FWD" else "",
            "broker_symbols": [], "trade_ids": [], "open_trade_ids": [], "quantity": 0.0, "lots": 0.0,
            "mark": None, "mark_source": "", "mark_reason": "", "avg_fill": None,
            "avg_fill_reason": "fills on no trade: no average read", "hedge": False, "pseudo": True,
            "unrecognised": product == "UNRECOGNISED"})
        leg["trade_ids"].append(str(tid))
        if f.get("broker_symbol") and f["broker_symbol"] not in leg["broker_symbols"]:
            leg["broker_symbols"].append(f["broker_symbol"])
        if str(r.get("status") or "") == "OPEN" or leg["unrecognised"]:
            leg["open_trade_ids"].append(str(tid))
            leg["quantity"] += float(f.get("quantity") or 0.0)
        if leg["mark"] is None and bk._num(r.get("mark")) is not None:
            leg.update(mark=bk._num(r.get("mark")), mark_source=str(r.get("mark_source") or ""))
        elif leg["mark"] is None and r.get("reason"):
            leg["mark_reason"] = str(r.get("reason"))
    for leg in legs.values():
        q = leg["quantity"]
        leg["lots"] = q
        leg["status"] = ("unrecognised" if leg["unrecognised"] else
                         "open" if abs(q) > _EPS else "flat" if leg["open_trade_ids"] else "closed")
        leg["side"] = "long" if q > _EPS else "short" if q < -_EPS else "flat"
    return list(legs.values())


def _held(leg: dict) -> bool:
    """A leg that holds a position: open, or a contract not recognised with lots in the file."""
    return leg.get("status") == "open" or (leg.get("status") == "unrecognised" and abs(bk._num(leg.get("quantity")) or 0.0) > _EPS)


def net_unit(leg: dict) -> Tuple[str, bool]:
    """(unit, is money) of a contract's position: '' for lots, 't' for an LME metal, the base
    currency ('EUR') or 'oz' for an FX trade."""
    product = str(leg.get("product") or "")
    if product == "LME_FWD":
        return "t", False
    if product in bk.NOTIONAL_PRODUCTS:
        return bk._base_unit(leg)
    return "", False


# --------------------------------------------------------------------------- the rows
def rows_of(conn: sqlite3.Connection, data: dict) -> List[dict]:
    """One dict per contract of the gathered book (`book.gather` or `gathered`), every fill on
    exactly one: {key, name, instrument_id, product, family, commodity, exchange, month, legs: [(trade,
    leg)], trade_ids, open_ids, status ('open' | 'closed'), net, gross, unit, money, trades (names),
    holders (the trade dicts), clearers {name: net}, clearer_of {trade_id: name}, mark_leg, next,
    next_reason, unrecognised, blob}. Grouping and adding the legs' own figures only."""
    if not data.get("n_trades"):
        return []
    info = _info(conn, data["as_of"])
    groups: Dict[str, List[Tuple[dict, dict]]] = {}
    for t in data.get("trades") or []:
        legs = _pseudo_legs(data, t, info) if t.get("pseudo") else (t.get("legs") or [])
        for leg in legs:
            key = str(leg.get("contract_id") or leg.get("instrument_id") or "")
            groups.setdefault(key, []).append((t, leg))
    return [_row(data, info, key, holds) for key, holds in groups.items()]


def _row(data: dict, info: dict, key: str, holds: List[Tuple[dict, dict]]) -> dict:
    fills = data.get("fills") or {}
    accounts = info.get("accounts") or {}
    held = [(t, leg) for t, leg in holds if _held(leg)]
    first = (held or holds)[0][1]
    unit, money = net_unit(first)
    trade_ids = [str(i) for _t, leg in holds for i in leg.get("trade_ids") or []]
    open_ids = [str(i) for _t, leg in held for i in (leg.get("open_trade_ids") or
                                                     (leg.get("trade_ids") if leg.get("unrecognised") else []))]
    net = sum(bk._num(leg.get("quantity")) or 0.0 for _t, leg in held)
    gross = sum(abs(bk._num(leg.get("quantity")) or 0.0) for _t, leg in held)
    clearer_of = {i: clearer_name(accounts.get(i, "")) for i in trade_ids}
    split: Dict[str, float] = {}
    for i in (open_ids or trade_ids):
        split.setdefault(clearer_of.get(i, ""), 0.0)
        if i in open_ids:
            split[clearer_of.get(i, "")] += float((fills.get(i) or {}).get("quantity") or 0.0)
    marked = next((leg for _t, leg in (held or holds) if bk._num(leg.get("mark")) is not None), first)
    names = list(dict.fromkeys(str(t.get("trade") or "") for t, _leg in holds))
    holders = list({id(t): t for t, _leg in holds}.values())
    status = "open" if held else "closed"
    row = {
        "key": key, "name": str(first.get("name") or first.get("contract_id") or key),
        "instrument_id": str(first.get("instrument_id") or ""), "product": str(first.get("product") or ""),
        "root_id": str(first.get("root_id") or ""), "family": _family(data, first),
        "commodity": str(first.get("commodity") or ""), "exchange": str(first.get("exchange") or ""),
        "month": str(first.get("month") or str(first.get("prompt") or "")[:7]), "prompt": str(first.get("prompt") or ""),
        "legs": holds, "trade_ids": trade_ids, "open_ids": open_ids, "status": status, "net": net, "gross": gross,
        "unit": unit, "money": money, "trades": names, "holders": holders, "clearers": split, "clearer_of": clearer_of,
        "mark_leg": marked, "unrecognised": any(leg.get("unrecognised") for _t, leg in holds),
        "check": list(dict.fromkeys(x for _t, leg in holds for x in bk.leg_check_lines(data, leg))),
    }
    row["next"], row["next_reason"] = _next(data, info, row)
    syms = [s for _t, leg in holds for s in leg.get("broker_symbols") or []]
    row["blob"] = " ".join([row["name"], key, row["family"], row["exchange"], row["commodity"], *names, *syms,
                            *(c for c in split if c), *(accounts.get(i, "") for i in trade_ids)]).lower()
    return row


def _next(data: dict, info: dict, r: dict) -> Tuple[Optional[dict], str]:
    """The contract's next key date as the Book's Next cell reads it (`book.next_parts`): its row
    of the expiry schedule, else (a contract whose trades net to zero, which the schedule does not
    list) its stored expiry or prompt, grey as an estimate, the reason said; an FX trade its value
    date or an FX option its expiry."""
    if r["status"] != "open":
        return None, "nothing open"
    if r["unrecognised"]:
        return None, "contract not recognised: no key date until it is mapped"
    sched = info.get("schedule") or {}
    s = sched.get(r["key"]) or sched.get(r["instrument_id"])
    base = {"contract_id": r["key"], "leg": r["name"], "business_days": None, "level": "", "alert_date": ""}
    if s:
        return {**base, "event": s.get("next_event"), "date": s.get("next_event_date"),
                "alert_date": s.get("alert_date"), "business_days": s.get("business_days"), "level": s.get("level"),
                "estimated": bool(s.get("estimated")), "reason": s.get("reason", "")}, ""
    product = r["product"]
    expiry = (info.get("instruments") or {}).get(r["instrument_id"], ("", ""))[1]
    expiry = "" if expiry.startswith("9999") else expiry
    if product in FX_KEYED:
        date = r["key"].rpartition(" ")[2]
        return ({**base, "event": "value date", "date": date, "estimated": False,
                 "reason": "The forward's value date: it settles then"}, "") if date[:4].isdigit() else (None, "no value date")
    if product == "FX_OPTION":
        return ({**base, "event": "option expiry", "date": expiry, "estimated": False, "reason": ""}, "") if expiry \
            else (None, "no expiry on file")
    date = r["prompt"] or expiry
    if not date:
        return None, info.get("schedule_reason") or "no key date on file"
    why = (f"{'Nets to zero' if abs(r['net']) <= _EPS else 'Not listed'} across the book, so the expiry schedule has "
           f"no row for it: its stored {'prompt' if r['prompt'] else 'expiry date'} stands in")
    event = "LME prompt" if r["prompt"] else ("option expiry" if product in ("CMDTY_OPTION", "EQ_OPTION") else "last trade")
    return {**base, "event": event, "date": date, "alert_date": date, "estimated": not r["prompt"], "reason": why}, ""


# --------------------------------------------------------------------------- order, filters
def default_order(rows: Sequence[dict]) -> List[dict]:
    """Commodity family (the trade view's family order: FX and Other last), then commodity, exchange,
    month, futures before options: SHFE and COMEX copper sit together."""
    rank = {f: n for n, f in enumerate(tf.group_order([r["family"] for r in rows], tf.GROUP_BY_COMMODITY))}

    def key(r):
        fx = r["product"] in bk.NOTIONAL_PRODUCTS           # an FX trade: by pair, then its date
        when = str((r.get("next") or {}).get("date") or r["key"].rpartition(" ")[2])
        return (rank.get(r["family"], 99), (r["instrument_id"][:6] if fx else r["commodity"]).lower(), r["exchange"],
                r["month"] if not fx else when, r["prompt"], "OPTION" in r["product"], r["name"])
    return sorted(rows, key=key)


def figure(data: dict, r: dict, key: str) -> Optional[float]:
    if key in ("daily", "ltd", "mtd", "ytd"):
        return bk.fill_sum(data, key, r["trade_ids"])[0]
    if key in ("net", "gross"):
        return r[key]
    if key == "now":
        return bk._num(r["mark_leg"].get("mark"))
    return None


def sort_value(data: dict, r: dict, key: str) -> Any:
    if key == "contract":
        return r["name"].lower()
    if key == "clearer":
        return " ".join(sorted(c for c in r["clearers"] if c)).lower() or None
    if key == "trades":
        return len(r["trades"])
    if key == "next":
        return str((r["next"] or {}).get("date") or "") or None
    return figure(data, r, key)


def sort_rows(data: dict, rows: Sequence[dict], sort: Optional[dict]) -> List[dict]:
    base = default_order(rows)
    key = (sort or {}).get("key")
    if key not in SORTABLE:
        return base
    have = [r for r in base if sort_value(data, r, key) is not None]
    none = [r for r in base if sort_value(data, r, key) is None]
    have.sort(key=lambda r: sort_value(data, r, key), reverse=(sort or {}).get("dir") != "asc")
    return have + none


def own_filters(state: Optional[dict]) -> Dict[str, Any]:
    """This view's column filters: {column: comparison | [ticked]}, read from the Book's 'c-' keys."""
    return {k[len(COL):]: v for k, v in tf.tab_filters(state, TAB).items() if k.startswith(COL)}


def _lists(r: dict, col: str) -> List[str]:
    if col == "family":
        return [r["family"]]
    if col == "clearer":
        return [c or NO_CLEARER for c in r["clearers"]] or [NO_CLEARER]
    return []


def visible(data: dict, rows: Sequence[dict], state: Optional[dict]) -> List[dict]:
    """The contracts the search, the shared lists (a contract held by a trade they keep) and this
    view's column filters keep: whole contracts, never a trade's part of one."""
    s = tf.normal(state)
    text = s["search"].strip().lower()
    shared = dict(s, search="")
    by_trade = bool(s["type"] or s["commodity"] or s["trade"])
    cols = own_filters(s)
    out = []
    for r in rows:
        if text and not all(w in r["blob"] for w in text.split()):
            continue
        if by_trade and not any(tf.keeps(t, shared) for t in r["holders"]):
            continue
        if cols and not tf.keeps_cols(r, cols, lambda r, c: figure(data, r, c), _lists):
            continue
        out.append(r)
    return out


def options_of(rows: Sequence[dict]) -> Dict[str, List[dict]]:
    fams: Dict[str, int] = {}
    clr: Dict[str, int] = {}
    for r in rows:
        fams[r["family"]] = fams.get(r["family"], 0) + 1
        for c in _lists(r, "clearer"):
            clr[c] = clr.get(c, 0) + 1
    return {"family": [{"label": f"{f} ({fams[f]})", "value": f} for f in tf.group_order(fams, tf.GROUP_BY_COMMODITY)],
            "clearer": [{"label": f"{c} ({n})", "value": c} for c, n in sorted(clr.items())]}


def _list_funnel(state: Optional[dict], col: str, title: str, options: List[dict]) -> html.Details:
    value = own_filters(state).get(col)
    ticked = value if isinstance(value, list) else []
    return tf.funnel(f"{TAB}:{COL}{col}", tf.pop_list(tf.col_id(TAB, COL + col), title, options, ticked),
                     bool(ticked), tf.option_words(options, ticked))


def head(sort: Optional[dict], state: Optional[dict], rows: Sequence[dict], trades: Sequence[dict]) -> html.Thead:
    opts = options_of(rows)
    ths = []
    half = len(COLUMNS) // 2
    for i, (key, title, cls, tip) in enumerate(COLUMNS):
        pop = None
        if key == "contract":
            pop = _list_funnel(state, "family", "Commodity", opts["family"])
        elif key == "clearer":
            pop = _list_funnel(state, "clearer", "Clearer", opts["clearer"])
        elif key == "trades":
            pop = tf.trade_funnel(TAB, state, tf.options_for(trades), ("trade",), COL + "trades")
        elif key in NUMBER_FUNNELS:
            pop = tf.number_funnel(TAB, state, COL + key, hint=f"{NUMBER_FUNNELS[key]} {tf.NUMBER_HINT}")
        ths.append(tf.head_th(title, cls, tip, sort_id={"type": SORT_TYPE, "idx": key}, arrow=tf.arrow_of(sort, key),
                              pop=pop, right=i > half))
    return html.Thead(html.Tr(ths))


# --------------------------------------------------------------------------- the cells
def net_text(value: Optional[float], unit: str, money: bool) -> str:
    """'+10', '−75 t', '+250,000 EUR', '0': a signed position with its unit (none for lots)."""
    if value is None:
        return MISSING
    text = bk.signed_count(value, money)
    return f"{text} {unit}" if unit and text != "0" else text


def _position_words(r: dict, leg: dict) -> str:
    q = bk._num(leg.get("quantity")) or 0.0
    return net_text(q, r["unit"], r["money"]) + ("" if r["unit"] else f" {bk._lots_word(q)}")


def _clearer_td(r: dict, accounts: Dict[str, str]) -> html.Td:
    names = [c for c in r["clearers"] if c]
    accs = sorted({accounts.get(i, "") for i in (r["open_ids"] or r["trade_ids"])} - {""})
    split = ([f"{c or NO_CLEARER}: {net_text(v, r['unit'], r['money'])}" for c, v in r["clearers"].items()]
             if len(r["clearers"]) > 1 else [])
    hover = _lines("Split per clearer" if split else "", *split, *(f"Account: {a}" for a in accs))
    if not names:
        return html.Td(missing_cell("The file's account cell is empty for these fills: no clearer recorded"), className="l")
    return html.Td(html.Span(" + ".join(names), title=plain_words(hover) or None), className="l")


def _net_td(r: dict) -> html.Td:
    per = [f"{t.get('trade')}: {_position_words(r, leg)}" for t, leg in r["legs"] if _held(leg)]
    hover = _lines(*per, f"Gross {net_text(r['gross'], r['unit'], False).lstrip('+')}" if len(per) > 1 else "",
                   "Contracts not recognised: the lots as the file gives them" if r["unrecognised"] else "")
    text = bk.signed_count(r["net"], r["money"])
    unit = html.Span(r["unit"], className="cell-unit") if r["unit"] and text != "0" else None
    return html.Td(html.Span([text, unit], className="tk-net", title=plain_words(hover) or None))


def _gross_td(r: dict) -> html.Td:
    g = r["gross"]
    body = f"{g:,.0f}" if r["money"] or g >= 1000 else f"{g:,.2f}".rstrip("0").rstrip(".")
    unit = html.Span(r["unit"], className="cell-unit") if r["unit"] and g else None
    return html.Td(html.Span([body, unit], className="tk-sub" if abs(r["net"]) > _EPS else None))


def _trades_td(r: dict) -> html.Td:
    names = r["trades"]
    lines = [f"{t.get('trade')}: {_position_words(r, leg) if _held(leg) else 'flat'}"
             + (f" at {price_text(leg.get('avg_fill'))} average" if bk._num(leg.get("avg_fill")) is not None else "")
             for t, leg in r["legs"]]
    text = ", ".join(names) if len(names) <= 2 else _plural(len(names), "trade")
    return html.Td(html.Span(text, title=plain_words(_lines(*lines)) or None), className="l")


def _now_td(data: dict, r: dict) -> html.Td:
    leg = r["mark_leg"]
    unit = "" if leg.get("unrecognised") else bk._leg_unit(data, leg)
    mark = bk._num(leg.get("mark"))
    fill = bk._num(leg.get("avg_fill"))
    if mark is None:
        return html.Td(missing_cell(leg.get("mark_reason") or ("contract not recognised: no mark"
                                                              if r["unrecognised"] else "no mark")),
                       className="tk-level tk-now")
    hover = bk.mark_hover(data, leg, unit, fill, "")
    check = r["check"]
    if check:
        hover = _lines(hover, *(f"Price to check: {x}" for x in check))
    est = bk.is_estimated_mark(leg.get("mark_source")) or bool(check)
    words = bk.unit_words(unit)
    return html.Td(html.Span([("≈ " if est else "") + price_text(mark, unit, fill),
                              html.Span(words or bk.NBSP, className="cell-unit tk-unit-slot")],
                             className="cell-estimated" if est else None, title=plain_words(hover) or None),
                   className="tk-level tk-now")


def _next_td(data: dict, r: dict) -> html.Td:
    if r["status"] != "open":
        return _closed_td(data, r)
    text, cls, hover = bk.next_parts({"next": r["next"], "next_reason": r["next_reason"]}, data["as_of"])
    if not text:
        return html.Td(missing_cell(hover), className="l")
    return html.Td(html.Span(text, className=cls or None, title=plain_words(hover)), className="l")


def _closed_td(data: dict, r: dict) -> html.Td:
    """A contract no trade holds any more: settled or expired on its date, else flat since its last fill."""
    fills = data.get("fills") or {}
    last = max(((fills.get(i) or {}).get("trade_date", "") for i in r["trade_ids"]), default="")
    date = r["prompt"] or (r["key"].rpartition(" ")[2] if r["product"] in FX_KEYED else "")
    settled = all(leg.get("status") == "closed" for _t, leg in r["legs"])
    if settled and date and date <= str(data["as_of"]):
        text, hover = f"Settled {day_text(date, data['as_of'])}", "Every fill of the contract has settled"
    elif settled:
        text, hover = "Settled", "Every fill of the contract has settled or expired; the P&L is frozen"
    else:
        text, hover = (f"Flat {day_text(last, data['as_of'])}" if last else "Flat",
                       "The trades' fills in this contract net to zero: nothing held since the last fill")
    return html.Td(html.Span(text, className="tk-sub", title=hover), className="l")


def contract_tr(data: dict, r: dict, opened: bool, accounts: Dict[str, str]) -> html.Tr:
    ids = r["trade_ids"]
    daily, ltd = bk.fill_sum(data, "daily", ids), bk.fill_sum(data, "ltd", ids)
    mtd, ytd = bk.fill_sum(data, "mtd", ids), bk.fill_sum(data, "ytd", ids)
    info = row_info([bk.excl_line("P&L today", daily), bk.excl_line("P&L since entry", ltd), bk.excl_line("MTD", mtd),
                     bk.excl_line("YTD", ytd)])
    leg = r["mark_leg"]
    name_hover = _lines(bk._leg_hover(data, leg) if not leg.get("pseudo") else "",
                        f"{_plural(len(ids), 'fill')} in {_plural(len(r['trades']), 'trade')}")
    name: List[Any] = [html.Span("▾ " if opened else "▸ ", className="tk-chev"),
                       html.Span(r["name"], className="tk-name", title=plain_words(name_hover) or None)]
    if r["unrecognised"]:
        name.append(html.Span(" not recognised", className="tk-red",
                              title=plain_words(leg.get("unrecognised_reason") or "Contract not recognised")))
    name.append(info)
    check = r["check"]
    return html.Tr([
        html.Td(name, className="l"), _clearer_td(r, accounts), _net_td(r), _gross_td(r), _trades_td(r),
        _now_td(data, r),
        bk.money_td(*daily, check=check, row=True),
        bk.money_td(*ltd, hover=_lines(f"MTD {full_signed(mtd[0])}", f"YTD {full_signed(ytd[0])}"), check=check,
                    row=True),
        _next_td(data, r),
    ], id={"type": ROW_TYPE, "idx": r["key"]}, n_clicks=0,
        className="tk-row" + (" tk-row--open" if opened else "") + (" tk-row--pseudo" if leg.get("pseudo") else ""))


def _money(tot) -> html.Td:
    v, n, reasons = tot
    if v is None:
        return html.Td(missing_cell(_lines(*reasons[:6]) or "no figure"))
    return html.Td([html.Span(full_signed(v), className=sign_class(v) or None),
                    marker(f"excl. {n}", _lines(*reasons[:6]), "marker--small") if n else None])


def panel_tr(data: dict, r: dict, accounts: Dict[str, str]) -> html.Tr:
    """The trades holding the contract: each one's clearer, position, average fill, Daily and LTD,
    and a link to it in the trade view; the split per clearer when there is more than one."""
    body = []
    for t, leg in r["legs"]:
        name = str(t.get("trade") or "")
        ids = [str(i) for i in leg.get("trade_ids") or []]
        clr = sorted({clearer_name(accounts.get(i, "")) for i in ids} - {""})
        fill = bk._num(leg.get("avg_fill"))
        unit = "" if leg.get("unrecognised") else bk._leg_unit(data, leg)
        link = (tf.link("Open trade", TAB, name, f"{LINK_IDX}{r['key']}", "Show this trade in the By trade view, "
                        "its legs open") if not t.get("pseudo") else html.Span("Fills on no trade", className="tk-sub"))
        body.append(html.Tr([
            html.Td(name, className="l"),
            html.Td(" + ".join(clr) if clr else missing_cell("No account on file for these fills"), className="l"),
            html.Td(_position_words(r, leg) if _held(leg) else html.Span("Flat", className="tk-sub")),
            html.Td(price_text(fill, unit, fill) if fill is not None
                    else missing_cell(leg.get("avg_fill_reason") or "no open lots")),
            _money(bk.fill_sum(data, "daily", ids)), _money(bk.fill_sum(data, "ltd", ids)),
            html.Td(link, className="l"),
        ], className="tk-leg" + ("" if _held(leg) else " tk-leg--flat")))
    ids = r["trade_ids"]
    foot = html.Tr([html.Td("Contract total", className="l", colSpan=2),
                    html.Td(net_text(r["net"], r["unit"], r["money"])), html.Td(""),
                    _money(bk.fill_sum(data, "daily", ids)), _money(bk.fill_sum(data, "ltd", ids)), html.Td("")])
    heads = ("Trade", "Clearer", "Position", "Avg fill", "P&L today", "P&L since entry", "")
    table = html.Table([html.Thead(html.Tr([html.Th(h, className="l" if i in (0, 1, 6) else None)
                                            for i, h in enumerate(heads)])),
                        html.Tbody(body), html.Tfoot(foot)], className="book-table tk-table tk-legs")
    parts: List[Any] = [table]
    if len(r["clearers"]) > 1:
        parts.append(html.Table(html.Tbody([html.Tr([html.Td(c or NO_CLEARER, className="tk-kv-k"),
                                                     html.Td(net_text(v, r["unit"], r["money"]))])
                                            for c, v in r["clearers"].items()]),
                                className="tk-table tk-kv", title="The net position at each clearer"))
    return html.Tr(html.Td(html.Div(parts, className="tk-panel-legs"), colSpan=len(COLUMNS),
                           className="l tk-panel-cell"), className="tk-panel")


# --------------------------------------------------------------------------- the table
def _filtered(state: Optional[dict]) -> bool:
    s = tf.normal(state)
    return bool(s["search"].strip() or s["type"] or s["commodity"] or s["trade"] or own_filters(s))


def _filter_words(state: Optional[dict]) -> str:
    s = tf.normal(state)
    bits = []
    if s["search"].strip():
        bits.append(f"search \"{s['search'].strip()}\"")
    for part, title in (("type", "Type"), ("commodity", "Commodity"), ("trade", "Trade")):
        if s[part]:
            bits.append(f"{title}: {', '.join(tf.key_label(x) if part == 'type' else x for x in s[part])}")
    return ("Filters: " + "; ".join(bits) + " (a contract shows when a trade holding it passes)") if bits else ""


def total_tr(data: dict, shown: Sequence[dict], rows: Sequence[dict], state: Optional[dict]) -> html.Tr:
    filtered = _filtered(state)
    ids = [i for r in shown for i in r["trade_ids"]]
    daily, ltd, mtd = (bk.fill_sum(data, k, ids) for k in ("daily", "ltd", "mtd"))
    n_open = sum(1 for r in rows if r["status"] == "open")
    label = (f"Filtered · {len(shown)} of {_plural(len(rows), 'contract')}" if filtered
             else f"Book · {_plural(len(rows), 'contract')}")
    hover = _lines(f"{len(rows)} contracts: {n_open} held, {len(rows) - n_open} closed",
                   _filter_words(state) if filtered else "P&L today and P&L since entry equal the top bar's Daily and "
                   "LTD to the cent")
    nxt = [(r["next"], r) for r in shown if r["status"] == "open" and r["next"] and r["next"].get("date")]
    nx_cell: Any = ""
    if nxt:
        e, r = min(nxt, key=lambda x: (str(x[0]["date"]), x[1]["name"]))
        text, cls, nh = bk.next_parts({"next": e}, data["as_of"])
        nx_cell = html.Span(f"{r['name']} · {text}", className=cls or None, title=plain_words(nh))
    checked = [r["name"] for r in shown if r["check"]]

    def money(fig, hover=""):
        td = bk.money_td(*fig, hover=hover)
        if checked and fig[0] is not None:
            td.children = list(td.children) + [marker(f"≈ {len(checked)} to check", _lines(
                "Includes the P&L of contracts priced off a price the marks check flags:", *checked), "marker--small")]
        return td
    trades = {t for r in shown for t in r["trades"]}
    return html.Tr([html.Td(html.Span(label, title=hover), className="l"), html.Td(""), html.Td(""), html.Td(""),
                    html.Td(html.Span(_plural(len(trades), "trade"), className="tk-sub",
                                      title="The trades holding the contracts showing")
                            if trades else "", className="l"),
                    html.Td(""), money(daily), money(ltd, f"MTD {full_signed(mtd[0])}"),
                    html.Td(nx_cell, className="l")], className="tk-total book-total")


def table(data: dict, rows: Sequence[dict], state: Optional[dict], sort: Optional[dict], opened: Sequence[str],
          folds: Optional[dict], accounts: Dict[str, str]) -> Tuple[html.Table, List[str]]:
    """The By contract table and the keys showing."""
    shown = visible(data, rows, state)
    opened_set = set(opened or [])
    body: List[Any] = [total_tr(data, shown, rows, state)]
    open_rows = [r for r in shown if r["status"] == "open"]
    closed_rows = [r for r in shown if r["status"] != "open"]

    def add(r):
        is_open = r["key"] in opened_set
        body.append(contract_tr(data, r, is_open, accounts))
        if is_open:
            try:
                body.append(panel_tr(data, r, accounts))
            except Exception as exc:  # noqa: BLE001 -- one panel's reason, never the table
                log.exception("Book by contract: the panel of %s failed", r["key"])
                body.append(html.Tr(html.Td(missing_cell(f"the panel could not be built ({type(exc).__name__}: {exc})"),
                                            colSpan=len(COLUMNS), className="l"), className="tk-panel"))
    for r in sort_rows(data, open_rows, sort):
        add(r)
    if closed_rows:
        is_open = bool((folds or {}).get("closed"))
        ids = [i for r in closed_rows for i in r["trade_ids"]]
        body.append(html.Tr([
            html.Td([html.Span("▾ " if is_open else "▸ ", className="tk-chev"), f"Closed ({len(closed_rows)})"],
                    className="l", colSpan=N_BEFORE_MONEY,
                    title="The contracts no trade holds any more: settled, expired or flat; P&L since entry the final P&L"),
            bk.money_td(*bk.fill_sum(data, "daily", ids)), bk.money_td(*bk.fill_sum(data, "ltd", ids)), html.Td("")],
            id={"type": FOLD_TYPE, "idx": "closed"}, n_clicks=0, className="tk-fold"))
        if is_open:
            for r in default_order(closed_rows):
                add(r)
    return (html.Table([head(sort, state, rows, data.get("trades") or []), html.Tbody(body)], id=bk.TABLE_ID,
                       className="book-table book-grid tk-table tk-contracts"),
            [r["key"] for r in shown])


def headline(data: dict, rows: Sequence[dict], state: Optional[dict]) -> Optional[html.Span]:
    """The contracts' MTD and YTD in the strip, only while a filter is set (as the trade view)."""
    if not _filtered(state):
        return None
    ids = [i for r in visible(data, rows, state) for i in r["trade_ids"]]

    def money(label, k):
        v, n, reasons = bk.fill_sum(data, k, ids)
        return html.Span(title=f"The {label} P&L of the contracts showing (the header's is the whole book's)",
                         className="tk-strip-fig", children=[
            html.Span(label, className="tk-k"), " ", bk.km_cell(v, reason=_lines(*reasons[:6])),
            marker(f"excl. {n}", bk._excl_hover(n, reasons), "marker--small") if n and v is not None else None])
    return html.Span([money("MTD", "mtd"), money("YTD", "ytd")], className="tk-strip-figs")


def csv_frame(data: dict, rows: Sequence[dict], state: Optional[dict], sort: Optional[dict],
              accounts: Dict[str, str]) -> pd.DataFrame:
    out = []
    shown = visible(data, rows, state)
    for r in sort_rows(data, [x for x in shown if x["status"] == "open"], sort) + \
            default_order([x for x in shown if x["status"] != "open"]):
        leg = r["mark_leg"]
        nx = r["next"] or {}
        out.append({
            "Contract": r["name"], "Instrument": r["instrument_id"], "Contract key": r["key"], "Status": r["status"],
            "Commodity": r["family"], "Exchange": r["exchange"],
            "Clearer": " + ".join(c for c in r["clearers"] if c),
            "Accounts": " ".join(sorted({accounts.get(i, "") for i in r["trade_ids"]} - {""})),
            "Net": r["net"], "Gross": r["gross"], "Unit": r["unit"] or "lots",
            "Per clearer": "; ".join(f"{c or NO_CLEARER} {v:g}" for c, v in r["clearers"].items()),
            "Trades": " ".join(r["trades"]),
            "Now": leg.get("mark"), "Mark source": leg.get("mark_source"), "Previous close": leg.get("prev_mark"),
            "Daily": figure(data, r, "daily"), "MTD": figure(data, r, "mtd"), "YTD": figure(data, r, "ytd"),
            "LTD": figure(data, r, "ltd"), "Next event": nx.get("event"), "Next date": nx.get("date"),
            "Next estimated": nx.get("estimated"), "Fills": len(r["trade_ids"]), "Trade ids": " ".join(r["trade_ids"]),
        })
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- render and callbacks
def switch(value: Optional[str] = None) -> html.Span:
    """The By trade | By contract switch, for the table's strip (static in the layout; the session
    keeps the choice)."""
    return html.Span(className="book-view-switch", children=[
        html.Label("View", className="tk-k", title=VIEW_ABOUT),
        dcc.RadioItems(id=VIEW_ID, className="book-switch", options=VIEW_OPTIONS, value=value or VIEW_TRADE,
                       inline=True, persistence=True, persistence_type="session")])


def render(conn: sqlite3.Connection, data: dict, state: Optional[dict], sort: Optional[dict], opened: Sequence[str],
           folds: Optional[dict]) -> Tuple[Any, Any, List[str]]:
    """(table, headline, keys showing) of the By contract view."""
    rows = rows_of(conn, data)
    accounts = _info(conn, data["as_of"]).get("accounts") or {}
    tbl, keys = table(data, rows, state, sort, opened, folds, accounts)
    return tbl, headline(data, rows, state), keys


def csv_of(conn: sqlite3.Connection, data: dict, state: Optional[dict], sort: Optional[dict]) -> pd.DataFrame:
    return csv_frame(data, rows_of(conn, data), state, sort, _info(conn, data["as_of"]).get("accounts") or {})


def _clicked() -> bool:
    trig = dash.ctx.triggered or []
    return bool(trig and trig[0].get("value"))


def register_callbacks(app, get_db_path) -> None:
    """The contract rows' panels (a click), the sort, the
    closed fold, and a panel's "Open trade" link (the By trade view, that trade's panel open; the
    shared filter and the tab are set by `trade_filter`'s link callback)."""
    @app.callback(Output(OPEN_STORE_ID, "data"), Input({"type": ROW_TYPE, "idx": ALL}, "n_clicks"),
                  State(OPEN_STORE_ID, "data"), prevent_initial_call=True)
    def _toggle(_rows, current):
        if not _clicked():
            return dash.no_update
        trig = dash.ctx.triggered_id
        if isinstance(trig, dict):
            key = str(trig.get("idx") or "")
            cur = list(current or [])
            return [x for x in cur if x != key] if key in cur else cur + [key]
        return dash.no_update

    @app.callback(Output(SORT_STORE_ID, "data"), Input({"type": SORT_TYPE, "idx": ALL}, "n_clicks"),
                  State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _sort(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked():
            return dash.no_update
        return bk.next_sort(current, str(trig.get("idx") or ""))

    @app.callback(Output(FOLD_STORE_ID, "data"), Input({"type": FOLD_TYPE, "idx": ALL}, "n_clicks"),
                  State(FOLD_STORE_ID, "data"), prevent_initial_call=True)
    def _fold(_clicks, current):
        if not _clicked():
            return dash.no_update
        cur = dict(current or {})
        cur["closed"] = not cur.get("closed")
        return cur

    @app.callback(Output(VIEW_ID, "value", allow_duplicate=True),
                  Output(bk.OPEN_STORE_ID, "data", allow_duplicate=True),
                  Input({"type": tf.LINK_TYPE, "to": TAB, "trade": ALL, "idx": ALL}, "n_clicks"),
                  State(bk.OPEN_STORE_ID, "data"), prevent_initial_call=True)
    def _to_trade(_clicks, opened):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not _clicked() or not str(trig.get("idx") or "").startswith(LINK_IDX):
            return dash.no_update, dash.no_update
        name = str(trig.get("trade") or "")
        cur = list(opened or [])
        return VIEW_TRADE, cur if name in cur else cur + [name]
