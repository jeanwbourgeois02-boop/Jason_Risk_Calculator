"""The Blotter's audit trail (Phase G, round 2, 2026-09-29): "What did I load, and did it load right?"

The design doc's "Table specs", Blotter (no shared filter bar; its own search and filters; nothing
editable; a correction is a re-upload):

- **Last upload**: one line (file · time · rows · new · updated · removed as cancelled · not
  loaded, amber when any and a click opens the table · prices converted), then the rows that did
  not become trades (Row in file · Trade Id · Symbol as written · Reason in plain words · What to
  do) and the file-level warnings.
- **Every fill**, newest trade date first, eight columns: Date, Trade, Contract (our name with the
  exchange), Side, Lots, Price (as in the file), Trade Id, Landed in (the trade on the Book with
  its type). Fills in no trade are pinned to the top under an amber line. Filters: Trade, Landed
  in, Side, a trade-date range and a search over the contract, the symbol and the Trade Id; a
  click on a title sorts (descending, ascending, default); Download CSV gives the rows showing,
  every column including the hover-only ones, at full figures. Arriving from a Book trade's
  "See fills" sets the Trade filter, with a chip to clear it.
- **Upload history** (folded): time · file · new · updated · removed · not loaded; a click on an
  upload lists the Trade Ids it touched.

Read as the upload lane recorded it (`data.ingest.upload`: `last_upload_report`,
`last_upload_issues`, `upload_history`, `trade_upload_trail`), as `trades` / `instruments` hold
it, and the trade each fill landed in as `engine.spreads.trade_book` gives it (the Book's own,
`blotter_pricing.shared_trade_book`). Nothing is priced or recomputed here.

State: the filter lives in a session store (`FILTER_STORE_ID`), so it survives a data refresh
and a tab switch; the bar is drawn from it (on mount, a new as-of, new data, and when "See fills",
Clear or the chip changes it) and a change to a control writes it back, so typing in the search
never loses its focus. The sort lives in its own session store.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from dash import ALL, MATCH, Input, Output, State, dcc, html

from ui.revision import BOOK_REVISION_ID, DATA_REVISION_ID
from ui.tabs import data_kit as kit
from ui.tabs.formatting import (
    cap, tidy,
    MISSING, amount_words, compact, day_text, fx_name, is_fx_pair, missing_cell, parse_contract_id, plain_words,
    price_text, quoted_unit, price_decimals, decimals_of,
)
from ui.tabs.header import AS_OF_STORE_ID

log = logging.getLogger(__name__)

# ---- ids
SECTION_ID = "blotter-fills-section"          # the fills card (hidden while the Options sub-tab shows)
FILTER_STORE_ID = "blotter-fills-filter"      # session: {trade, landed, side, search, start, end, from_book}
SORT_STORE_ID = "blotter-fills-sort"          # session: {"key", "dir"} or None
BAR_REV_ID = "blotter-fills-bar-rev"          # bumped when the filter is set from outside the bar
BAR_SLOT_ID = "blotter-fills-bar"
FILLS_BODY_ID = "blotter-fills-body"
META_ID = "blotter-fills-meta"
CSV_ID = "blotter-fills-csv"
DOWNLOAD_ID = "blotter-fills-download"
F_TRADE_ID = "blotter-fills-f-trade"
F_LANDED_ID = "blotter-fills-f-landed"
F_SIDE_ID = "blotter-fills-f-side"
F_DATES_ID = "blotter-fills-f-dates"
F_SEARCH_ID = "blotter-fills-f-search"
CLEAR_ID = "blotter-fills-clear"
CHIP_CLEAR_ID = "blotter-fills-chip-clear"
SORT_TYPE = "blotter-fill-sort"
LAST_UPLOAD_ID = "blotter-last-upload"
NOT_LOADED_LINK_ID = "blotter-not-loaded-link"
REJECTS_ID = "blotter-rejects"
HISTORY_ID = "blotter-upload-history"
HIST_ROW_TYPE = "blotter-hist-row"
HIST_IDS_TYPE = "blotter-hist-ids"

NONE_VALUE = "__none__"                       # the filter value of "no trade name" / "not in a trade"
NO_HISTORY = "No upload recorded on this database"
FILLS_MAX_ROWS = 500                          # rows drawn at once; the CSV holds every row the filters keep
DEFAULT_FILTER = {"trade": [], "landed": [], "side": [], "search": "", "start": None, "end": None,
                  "from_book": ""}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_FX_PRODUCTS = ("FX_SPOT", "FX_FWD", "FX_SWAP", "FX_OPTION")
_LOT_PRODUCTS = ("FUTURE", "CMDTY_OPTION", "EQ_OPTION")
_FILE_COUNT_KEYS = ("futures", "options_on_futures", "lme_forwards", "fx_forwards", "fx_spot", "fx_options")

SYMBOL_NOT_RECORDED = "symbol not recorded: loaded before 29 Sep, re-upload to fill"
PRICE_NOT_RECORDED = "price not recorded: loaded before 29 Sep, re-upload the file to fill it"
PRICE_FROM_NETINVOICE = "the file's Price cell was blank or not a number: the fill was rebuilt from NetInvoice"
NO_TRADE_NAME = "no trade name: the file's PBRoot cell has no text after the underscore"
NOT_IN_TRADE = ("in no trade on the Book: the fill carries no trade name (its PBRoot cell has no text after the "
                "underscore), so it sits beside the trades, not in one")
NO_TRADE_ID = "the upload does not record the row's Trade Id: find the row by its number and symbol"

# (key, title, css, hover of the title, sortable)
COLUMNS: Tuple[kit.Column, ...] = (
    ("date", "Date", "l", "The trade date as the file gave it; newest first.", True),
    ("trade", "Trade", "l", "Jason's trade name, the text after the underscore of the PBRoot cell (the raw cell on "
                            "hover).", True),
    ("contract", "Contract", "l", "The contract the app read the row as, with its exchange ('COMEX Silver Dec26'); "
                                  "the broker's symbol and the Bloomberg ticker on hover.", True),
    ("side", "Side", "l", "Buy or sell, as the file gave it.", True),
    ("lots", "Lots", "", "Contracts for a future or an option on one; an LME ticket in lots, its tonnes on hover; an "
                         "FX trade its base-currency amount.", True),
    ("price", "Price (as in file)", "", "The file's Price cell exactly as written, in the broker's units; the price "
                                        "the app stores (Bloomberg's units) on hover.", True),
    ("trade_id", "Trade Id", "l", "The broker's Trade Id, the key an upload merges on; every upload that touched it "
                                  "on hover.", True),
    ("landed", "Landed in", "l", "The trade on the Book this fill sits in, with the trade's type. Amber when it sits "
                                 "in no trade.", True),
)

# ---- small words
def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _ny_stamp(iso):
    from zoneinfo import ZoneInfo
    try:
        stamp = dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(ZoneInfo("America/New_York"))


def ny_time(iso) -> str:
    """'Mon 28 Sep 21:34 NY' from a UTC timestamp; the text as it is when it does not parse."""
    local = _ny_stamp(iso)
    if local is None:
        return str(iso or "")
    return f"{_WEEKDAYS[local.weekday()]} {local.day} {_MONTHS[local.month - 1]} {local:%H:%M} NY"


def ny_day(iso) -> str:
    local = _ny_stamp(iso)
    return str(iso or "") if local is None else f"{local.day} {_MONTHS[local.month - 1]}"


def _parse_cell(text) -> Optional[float]:
    cleaned = str(text or "").replace(",", "").replace(" ", "").strip()
    return _num(cleaned) if cleaned else None


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


NOT_TYPED_CODE = "NOT_TYPED"                 # a trade of unrecognised contracts only: not typed until mapped


def type_words(code: str) -> str:
    """The trade's type as the shared filter bar writes it ('Cross-product', 'Calendar')."""
    if code == NOT_TYPED_CODE:
        return "Not typed"
    try:
        from ui.tabs.trade_filter import type_label
        return type_label(code)
    except Exception:  # noqa: BLE001 -- the engine's code in words
        return str(code or "").replace("_", " ").capitalize() or "Hedges only"


# ---- the frame
UNRECOGNISED = "UNRECOGNISED"                # a row the parser could not identify (every row loads, 2026-09-29)
FRAME_COLUMNS = [
    "trade_id", "trade_date", "side", "quantity", "qty_unit", "lots", "lots_text", "lots_tip", "broker_symbol",
    "description", "contract", "contract_tip", "instrument_id", "bbg_ticker", "product", "price", "price_unit",
    "price_text", "broker_price", "broker_price_num", "broker_price_tip", "price_scale", "price_scaled", "strategy",
    "pb_root", "landed", "landed_type", "landed_tip", "assigned", "status", "uploads", "uploads_tip", "account",
    "unrecognised", "unrecognised_reason"]


def _read_trades(conn: sqlite3.Connection) -> List[dict]:
    """Every trade on file with its instrument's currencies, expiry and Bloomberg ticker, its last
    leg date and option terms; a database from before the PBRoot or raw-cell columns reads ''."""
    base = ("SELECT t.trade_id, t.trade_date, t.quantity, t.price, t.product, t.instrument_id, t.description, "
            "t.account, {labels}, COALESCE(i.base_ccy, ''), COALESCE(i.quote_ccy, ''), COALESCE(i.expiry_date, ''), "
            "COALESCE(i.bbg_ticker, '') FROM trades t LEFT JOIN instruments i ON i.instrument_id = t.instrument_id")
    keys = ("trade_id", "trade_date", "quantity", "price", "product", "instrument_id", "description", "account",
            "strategy", "pb_root", "broker_symbol", "broker_price", "base_ccy", "quote_ccy", "expiry_date",
            "bbg_ticker")
    rows: list = []
    for labels in ("t.strategy, t.pb_root, t.broker_symbol, t.broker_price", "t.strategy, t.pb_root, '', ''",
                   "t.strategy, '', '', ''"):
        try:
            rows = conn.execute(base.format(labels=labels)).fetchall()
            break
        except sqlite3.OperationalError:
            continue
    out = [dict(zip(keys, r)) for r in rows]
    try:
        last_leg = {str(t): str(d or "") for t, d in
                    conn.execute("SELECT trade_id, MAX(settle_date) FROM trade_legs GROUP BY trade_id")}
    except sqlite3.Error:
        last_leg = {}
    try:
        terms = {str(i): (_num(k), str(o or "")) for i, k, o in
                 conn.execute("SELECT instrument_id, strike, option_type FROM instrument_options")}
    except sqlite3.Error:
        terms = {}
    for r in out:
        r["trade_id"] = str(r["trade_id"])
        r["settle_date"] = last_leg.get(r["trade_id"], "")
        r["strike"], r["option_type"] = terms.get(str(r["instrument_id"]), (None, ""))
    return out


def _contract_words(r: dict, root, leg_names: Dict[str, str]) -> str:
    """Our name of the fill's contract, with its exchange: the Book's leg name when the fill sits
    in a trade ('COMEX Silver Dec26', 'LME Zinc 18 Nov26'), else the same rule from the contract
    master; an FX trade 'USDCNH 18 Nov forward'."""
    tid, product, inst = r["trade_id"], str(r["product"] or ""), str(r["instrument_id"] or "")
    base, quote = str(r["base_ccy"]), str(r["quote_ccy"])
    if product == UNRECOGNISED:
        # the file's symbol as written: the app has no contract for it
        return str(r["broker_symbol"] or "") or inst.split(":", 1)[-1]
    if product in _FX_PRODUCTS:
        pair = f"{base}{quote}" if base and quote else inst
        return fx_name(pair, product, r["settle_date"] or r["expiry_date"], r["option_type"], r["strike"])
    if tid in leg_names:
        return leg_names[tid]
    try:
        from engine.spreads.trades import leg_name
    except Exception:  # noqa: BLE001 -- the id as it is
        return inst
    if product == "LME_FWD":
        prompt = str(r["settle_date"] or "")
        return leg_name(root, prompt[:7], prompt, inst)
    parsed = parse_contract_id(inst)
    if parsed is None or root is None:
        return inst
    name = leg_name(root, f"{parsed['year']:04d}-{parsed['month']:02d}", "", inst)
    if parsed["option_type"]:
        name += f" {parsed['strike']} {parsed['option_type']}"
    return name


def _lots(r: dict, root) -> Tuple[Optional[float], str, str, str]:
    """(lots for sorting, the cell's text, its hover, the quantity's unit)."""
    product, q = str(r["product"] or ""), _num(r["quantity"])
    if q is None:
        return None, MISSING, "the quantity on file is not a number", ""
    n = abs(q)
    if product in _LOT_PRODUCTS:
        return n, f"{n:,.0f}" if n == int(n) else f"{n:,.2f}", "", "lots"
    if product == "LME_FWD":
        tonnes_text = f"{n:,.0f} t" if n == int(n) else f"{n:,.2f} t"
        try:
            from engine.lme import lot_tonnes
            per = float(lot_tonnes(str(getattr(root, "root_id", "") or r["base_ccy"])))
        except Exception:  # noqa: BLE001 -- the tonnes alone
            per = 0.0
        if per > 0:
            lots = n / per
            text = f"{lots:,.0f}" if abs(lots - round(lots)) < 1e-9 else f"{lots:,.2f}"
            return lots, text, f"{tonnes_text} ({per:g} t a lot)", "t"
        return n, tonnes_text, "the lot size of this metal is not known: shown in tonnes", "t"
    if product in _FX_PRODUCTS:
        return n, amount_words(n, str(r["base_ccy"] or "")), f"{n:,.2f} {r['base_ccy']}".strip(), str(r["base_ccy"] or "")
    return n, f"{n:,g}", "", ""


def _build_frame(conn: sqlite3.Connection, as_of: str) -> Tuple[pd.DataFrame, List[tuple], bool]:
    from data.contracts import load_roots
    try:
        roots = dict(load_roots())
    except Exception:  # noqa: BLE001 -- names fall back to the ids on file
        roots = {}
    trades = _read_trades(conn)
    issues: List[tuple] = []
    landed: Dict[str, Tuple[str, str]] = {}
    leg_names: Dict[str, str] = {}
    unassigned: set = set()
    book_error = ""
    if trades:
        try:
            from ui.tabs.blotter_pricing import shared_trade_book
            tb = shared_trade_book(conn, as_of) or {}
            for t in tb.get("trades") or []:
                from ui.tabs.trade_filter import NOT_TYPED, trade_type_label
                code = NOT_TYPED_CODE if trade_type_label(t) == NOT_TYPED else str(t.get("type") or "")
                for tid in t.get("trade_ids") or []:
                    landed[str(tid)] = (str(t.get("trade") or ""), code)
                for leg in t.get("legs") or []:
                    for tid in leg.get("trade_ids") or []:
                        leg_names.setdefault(str(tid), str(leg.get("name") or ""))
            unassigned = {str(t) for t in tb.get("unassigned") or []}
            for note in tb.get("notes") or []:
                issues.append(("Trades", str(note)))
        except Exception as exc:  # noqa: BLE001 -- the fills still show; Landed in says why
            log.exception("Blotter: the trade book could not be read on %s", as_of)
            book_error = f"the Book's trades could not be read on {as_of} ({type(exc).__name__}: {exc})"
            issues.append(("Landed in", book_error))
    unrec_why: Dict[str, str] = {}
    if any(str(r["product"] or "") == UNRECOGNISED for r in trades):
        try:
            from data.bloomberg.inventory import unrecognised as read_unrecognised
            unrec_why = {str(u.get("trade_id")): str(u.get("reason") or "") for u in read_unrecognised(conn)}
        except Exception as exc:  # noqa: BLE001 -- the fill still shows its red flag, with the general reason
            issues.append(("Not recognised", f"the reasons could not be read ({type(exc).__name__}: {exc})"))
    status_of: Dict[str, str] = {}
    try:
        from ui.tabs.blotter_pricing import priced_value_book
        df, _, _ = priced_value_book(conn, as_of)
        if not df.empty:
            status_of = {str(t): str(s or "") for t, s in zip(df["trade_id"], df["status"])}
    except Exception:  # noqa: BLE001 -- the CSV's status says why
        log.exception("Blotter: statuses unavailable on %s", as_of)
    history: list = []
    try:
        from data.ingest.upload import trade_upload_trail, upload_history
        history = upload_history(conn)
    except Exception as exc:  # noqa: BLE001
        issues.append(("Uploads", f"the upload history could not be read ({type(exc).__name__}: {exc})"))
    records = []
    for r in trades:
        tid, product = r["trade_id"], str(r["product"] or "")
        base, inst = str(r["base_ccy"]), str(r["instrument_id"])
        root = roots.get(base)
        if product in ("FUTURE", "CMDTY_OPTION", "LME_FWD") and root is None:
            issues.append((tid, f"{inst}: its contract root {base or '(none)'} is not in the contract universe"))
        contract = _contract_words(r, root, leg_names)
        broker_symbol = str(r["broker_symbol"] or "")
        bbg = str(r["bbg_ticker"] or "")
        contract_tip = " · ".join(x for x in (
            f"Broker symbol {broker_symbol}" if broker_symbol else SYMBOL_NOT_RECORDED,
            f"Bloomberg ticker {bbg}" if bbg else "no Bloomberg ticker on file",
            inst, str(r["description"] or "")) if x)
        lots, lots_text, lots_tip, unit = _lots(r, root)
        q = _num(r["quantity"])
        price = _num(r["price"])
        scale = float(getattr(root, "broker_price_scale", 1.0) or 1.0) if product in ("FUTURE", "CMDTY_OPTION") else 1.0
        broker_price = str(r["broker_price"] or "")
        parsed = _parse_cell(broker_price)
        scaled = (scale != 1.0 and price is not None and parsed is not None
                  and abs(price - parsed * scale) <= 1e-9 * max(1.0, abs(price)))
        if product in ("FX_SPOT", "FX_FWD") and is_fx_pair(f"{base}{r['quote_ccy']}"):
            price_unit = f"{base}{r['quote_ccy']}"
        elif product in _FX_PRODUCTS:
            price_unit = ""
        else:
            price_unit = quoted_unit(root)
        # the stored fill exactly (its own decimals, never fewer than the unit's tick): an audit figure
        stored = (price_text(price, price_unit, decimals=max(price_decimals(price_unit), decimals_of(price)))
                  if price is not None else MISSING)
        tip = [f"Stored price {stored}" + (f" {price_unit}" if price_unit else "")
               + " (Bloomberg's units, the price every P&L figure uses)"]
        if scaled:
            tip.append(f"×{scale:g}: the broker books this contract in whole currency units (dollars per pound), "
                       f"Bloomberg in cents or pence: the file's price × {scale:g} is the fill the app stores")
        if not broker_price:
            tip.insert(0, PRICE_FROM_NETINVOICE if broker_symbol else PRICE_NOT_RECORDED)
        strategy = str(r["strategy"] or "").strip()
        if tid in landed:
            name, code = landed[tid]
            landed_label, landed_tip, assigned = f"{name} · {type_words(code)}", f"The Book's trade {name}.", True
        elif tid in unassigned or not strategy:
            name, code, landed_label, landed_tip, assigned = "", "", "", NOT_IN_TRADE, False
        elif str(r["trade_date"] or "") > str(as_of):
            name, code, landed_label, assigned = strategy, "", strategy, True
            landed_tip = f"dealt after the as-of date {as_of}: the Book on {as_of} does not hold it yet"
        else:
            name, code, landed_label, assigned = strategy, "", strategy, True
            landed_tip = book_error or f"{strategy} is not on the Book on {as_of}: see Data issues"
        uploads, uploads_tip = "", ""
        if history:
            trail = trade_upload_trail(conn, tid)
            if trail:
                uploads = f"{trail[0]['action']} {ny_day(trail[0]['uploaded_at'])}"
                uploads_tip = "\n".join(f"{ny_time(x['uploaded_at'])} · {x['filename']} · {x['action']}" for x in trail)
            else:
                uploads_tip = "on file before the upload history began: no recorded upload names it"
        else:
            uploads_tip = "no upload is recorded on this database"
        records.append({
            "trade_id": tid, "trade_date": str(r["trade_date"] or ""),
            "side": "" if q is None or q == 0 else ("Buy" if q > 0 else "Sell"),
            "quantity": None if q is None else abs(q), "qty_unit": unit, "lots": lots, "lots_text": lots_text,
            "lots_tip": lots_tip, "broker_symbol": broker_symbol, "description": str(r["description"] or ""),
            "contract": contract, "contract_tip": contract_tip, "instrument_id": inst, "bbg_ticker": bbg,
            "product": product, "price": price, "price_unit": price_unit, "price_text": stored,
            "broker_price": broker_price, "broker_price_num": parsed, "broker_price_tip": "\n".join(tip),
            "price_scale": scale, "price_scaled": bool(scaled), "strategy": strategy,
            "pb_root": str(r["pb_root"] or ""), "landed": landed_label, "landed_type": type_words(code) if code else "",
            "landed_tip": landed_tip, "assigned": assigned, "status": status_of.get(tid, ""),
            "uploads": uploads, "uploads_tip": uploads_tip, "account": str(r["account"] or ""),
            "unrecognised": product == UNRECOGNISED,
            "unrecognised_reason": (unrec_why.get(tid) or f"contract not recognised: {broker_symbol or inst}: P&L can't "
                                    "be computed until it is mapped") if product == UNRECOGNISED else "",
        })
    n_loose = sum(1 for x in records if not x["assigned"])
    if n_loose:
        issues.insert(0, ("In no trade", f"{_plural(n_loose, 'fill')} {'carries' if n_loose == 1 else 'carry'} no "
                                         "trade name, so it sits in no trade on the Book; pinned to the top of the "
                                         "fills." if n_loose == 1 else
                                         f"{_plural(n_loose, 'fill')} carry no trade name, so they sit in no trade on "
                                         "the Book; pinned to the top of the fills."))
    frame = pd.DataFrame(records, columns=FRAME_COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(["trade_date", "trade_id"], ascending=[False, False], kind="mergesort")
        frame = frame.reset_index(drop=True)
    return frame, issues, bool(history)


def fills_frame(conn: sqlite3.Connection, as_of: str) -> Tuple[pd.DataFrame, List[tuple], bool]:
    """(frame, issues, with_uploads): one row per trade on file (a fill), newest trade date first,
    every column the table and its CSV show with their hovers (`FRAME_COLUMNS`); the Data issues
    lines [(label, sentence)]; whether any upload is recorded. Memoised per database revision and
    as-of (with the trade book's outside inputs); shared: never edit the result."""
    from ui.tabs.blotter_pricing import research_inputs_key, screen_memo
    return screen_memo("blotter-fills", conn, as_of, lambda: _build_frame(conn, as_of), extra=research_inputs_key())


# ---- the filter
def normal_filter(state: Optional[dict]) -> dict:
    s = dict(DEFAULT_FILTER)
    for k, v in (state or {}).items():
        if k in ("trade", "landed", "side"):
            s[k] = [str(x) for x in (v or []) if x is not None]
        elif k in ("search", "from_book"):
            s[k] = str(v or "")
        elif k in ("start", "end"):
            s[k] = str(v)[:10] if v else None
    if s["from_book"] and s["trade"] != [s["from_book"]]:
        s["from_book"] = ""
    return s


def is_filtered(state: Optional[dict]) -> bool:
    s = normal_filter(state)
    return bool(s["trade"] or s["landed"] or s["side"] or s["search"].strip() or s["start"] or s["end"])


def filter_fills(df: pd.DataFrame, state: Optional[dict]) -> pd.DataFrame:
    """The fills the filter keeps: the trade names, the trades landed in and the sides picked
    (none picked keeps all), the trade date within [start, end], and the search text in the
    contract, the symbol as written, the Trade Id, the description or the instrument id."""
    s = normal_filter(state)
    out = df
    if out.empty:
        return out
    if s["trade"]:
        values = out["strategy"].where(out["strategy"] != "", NONE_VALUE)
        out = out[values.isin(s["trade"])]
    if s["landed"]:
        names = pd.Series([_landed_name(r) for r in out.to_dict("records")], index=out.index)
        out = out[names.isin(s["landed"])]
    if s["side"]:
        out = out[out["side"].isin(s["side"])]
    if s["start"]:
        out = out[out["trade_date"] >= s["start"]]
    if s["end"]:
        out = out[out["trade_date"] <= s["end"]]
    needle = s["search"].strip().lower()
    if needle and not out.empty:
        hit = pd.Series(False, index=out.index)
        for col in ("contract", "broker_symbol", "trade_id", "description", "instrument_id", "strategy"):
            hit |= out[col].astype(str).str.lower().str.contains(needle, regex=False)
        out = out[hit]
    return out


def _landed_name(r: dict) -> str:
    """The Landed in filter's value: the trade name, or NONE_VALUE for a fill in no trade."""
    if not r.get("assigned"):
        return NONE_VALUE
    return str(r.get("landed") or "").split(" · ")[0] or NONE_VALUE


def filter_options(df: pd.DataFrame) -> Dict[str, List[dict]]:
    """Each multi-select's choices, only the values present, each with its fill count."""
    if df.empty:
        return {"trade": [], "landed": [], "side": []}

    def counted(values: List[str], none_label: str) -> List[dict]:
        counts: Dict[str, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        opts = [{"label": f"{v} ({n})", "value": v} for v, n in sorted(counts.items()) if v != NONE_VALUE]
        if NONE_VALUE in counts:
            opts.insert(0, {"label": f"{none_label} ({counts[NONE_VALUE]})", "value": NONE_VALUE})
        return opts
    recs = df.to_dict("records")
    return {"trade": counted([r["strategy"] or NONE_VALUE for r in recs], "no trade name"),
            "landed": counted([_landed_name(r) for r in recs], "not in a trade"),
            "side": counted([r["side"] for r in recs if r["side"]], "")}


_SORT_KEYS: Dict[str, Callable[[dict], object]] = {
    "date": lambda r: (r["trade_date"], r["trade_id"]) if r.get("trade_date") else None,
    "trade": lambda r: str(r["strategy"]).lower() or None,
    "contract": lambda r: str(r["contract"]).lower() or None,
    "side": lambda r: r["side"] or None,
    "lots": lambda r: r["lots"],
    "price": lambda r: r["broker_price_num"] if r["broker_price_num"] is not None else r["price"],
    "trade_id": lambda r: str(r["trade_id"]),
    "landed": lambda r: str(r["landed"]).lower() or None,
}


def ordered(df: pd.DataFrame, sort: Optional[dict]) -> Tuple[List[dict], List[dict]]:
    """(fills in no trade, the rest), each in the sort's order (the frame's own, newest first, by
    default). The first list is pinned to the top of the table whatever the sort."""
    recs = df.to_dict("records")
    loose = kit.sort_records([r for r in recs if not r["assigned"]], sort, _SORT_KEYS)
    rest = kit.sort_records([r for r in recs if r["assigned"]], sort, _SORT_KEYS)
    return loose, rest


# ---- the table
def _row(r: dict, as_of: str) -> html.Tr:
    trade = (html.Span(r["strategy"], className="tk-name", title=plain_words(f"PBRoot {r['pb_root']}")
                       if r["pb_root"] else None)
             if r["strategy"] else missing_cell(NO_TRADE_NAME))
    date_tip = f"Trade date {_long_date(r['trade_date'])}"
    if r.get("broker_price"):
        price = html.Span(r["broker_price"], title=plain_words(r["broker_price_tip"]))
    else:
        price = missing_cell(r["broker_price_tip"])
    if r["assigned"]:
        landed = html.Span(r["landed"], title=plain_words(r["landed_tip"])) if r["landed"] else missing_cell(
            r["landed_tip"])
    else:
        landed = html.Span("not in a trade", className="cell-amber", title=plain_words(r["landed_tip"]))
    if r.get("unrecognised"):
        # every row loads (2026-09-29): on file as a trade, its contract not recognised: a red flag
        landed = html.Span([html.Span("● Not recognised", className="cell-red",
                                      title=reason_words(r["unrecognised_reason"])),
                            html.Span(" · ", className="tk-sub"), landed])
    lots = html.Span(r["lots_text"], title=plain_words(r["lots_tip"]) or None) if r["lots_text"] else missing_cell()
    return html.Tr([
        kit.td(day_text(r["trade_date"], as_of), left=True, title=date_tip),
        kit.td(trade, left=True),
        kit.td(r["contract"], left=True, title=r["contract_tip"]),
        kit.td(r["side"] or missing_cell("the quantity is zero: no side"), left=True),
        kit.td(lots),
        kit.td(price),
        kit.td(r["trade_id"], left=True, title=r["uploads_tip"]),
        kit.td(landed, left=True),
    ])


def fills_table(df: pd.DataFrame, total: int, sort: Optional[dict], state: Optional[dict], as_of: str) -> html.Div:
    """The table of the fills the filter keeps: the total row first ("All fills · 89" or
    "Filtered · 12 of 89"), the fills in no trade under an amber line, then the rest; at most
    `FILLS_MAX_ROWS` drawn (the CSV holds them all)."""
    thead = kit.head(COLUMNS, sort, SORT_TYPE)
    n = len(df)
    names = {r for r in df["strategy"].tolist() if r} if not df.empty else set()
    label = (f"All fills · {total:,}" if not is_filtered(state) and n == total
             else f"Filtered · {n:,} of {total:,} fills")
    label += f" · {_plural(len(names), 'trade')}"
    rows: list = [kit.total_row([html.Td(label, colSpan=len(COLUMNS), className="l")])]
    if df.empty:
        rows.append(kit.note_row("No fill matches the filters.", len(COLUMNS), className="cell-missing"))
        return html.Div(kit.table(thead, rows))
    loose, rest = ordered(df, sort)
    drawn = 0
    if loose:
        rows.append(kit.note_row(f"{_plural(len(loose), 'fill')} in no trade: no trade name in the PBRoot cell",
                                 len(COLUMNS), title=NOT_IN_TRADE))
        for r in loose[:FILLS_MAX_ROWS]:
            rows.append(_row(r, as_of))
            drawn += 1
    for r in rest[:max(FILLS_MAX_ROWS - drawn, 0)]:
        rows.append(_row(r, as_of))
        drawn += 1
    children = [kit.table(thead, rows)]
    if n > drawn:
        children.append(html.P(f"The first {drawn:,} of {n:,} are drawn; narrow them with the filters, or Download "
                               "CSV for every one.", className="book-section-meta"))
    return tidy(html.Div(children))


CSV_COLUMNS = (("trade_date", "Trade date"), ("strategy", "Trade"), ("pb_root", "PBRoot"), ("contract", "Contract"),
               ("broker_symbol", "Symbol as written"), ("bbg_ticker", "Bloomberg ticker"),
               ("instrument_id", "Instrument"), ("description", "Description"), ("side", "Side"),
               ("lots", "Lots"), ("quantity", "Quantity"), ("qty_unit", "Unit"), ("broker_price", "Price (as in file)"),
               ("price", "Stored price"), ("price_unit", "Price unit"), ("price_scale", "Broker price scale"),
               ("trade_id", "Trade Id"), ("landed", "Landed in"), ("unrecognised_reason", "Needs a fix"),
               ("status", "Status"), ("account", "Account"),
               ("uploads_tip", "Uploads"))


def fills_csv(df: pd.DataFrame, sort: Optional[dict]):
    """`dcc.send_data_frame` of the fills showing, in the table's order, every column including
    the hover-only ones, at full figures; None with no rows."""
    if df.empty:
        return None
    loose, rest = ordered(df, sort)
    frame = pd.DataFrame(loose + rest, columns=FRAME_COLUMNS)[[c for c, _ in CSV_COLUMNS]].copy()
    frame["landed"] = [x if x else "not in a trade" for x in frame["landed"]]
    frame["uploads_tip"] = [str(t or "").replace("\n", "; ") for t in frame["uploads_tip"]]
    frame = frame.rename(columns=dict(CSV_COLUMNS))
    return dcc.send_data_frame(frame.to_csv, "blotter-fills.csv", index=False)


def bar(state: Optional[dict], options: Dict[str, List[dict]], dates: Tuple[Optional[str], Optional[str]]) -> list:
    """The fills' own filter bar, its controls set from the stored filter: Trade, Landed in, Side
    (multi-selects of the values present with their counts), the trade-date range, the search,
    Clear (only when something is set) and, after a Book trade's "See fills", the chip that
    clears it."""
    s = normal_filter(state)

    def pick(cid: str, key: str, label: str, width: int) -> html.Div:
        known = {str(o["value"]) for o in options.get(key) or []}
        extra = [{"label": f"{v} (none now)", "value": v} for v in s[key] if v not in known]
        return html.Div(className="blotter-filter", style={"minWidth": f"{width}px"}, children=[
            html.Label(label),
            dcc.Dropdown(id=cid, options=list(options.get(key) or []) + extra, value=list(s[key]), multi=True,
                         placeholder="All", clearable=True, className="blotter-filter-dropdown")])

    children = [
        html.Div(className="blotter-filter", children=[
            html.Label("Search"),
            dcc.Input(id=F_SEARCH_ID, type="text", value=s["search"], debounce=True,
                      placeholder="Search contract, symbol or Trade Id", className="blotter-filter-search")]),
        pick(F_TRADE_ID, "trade", "Trade", 170),
        pick(F_LANDED_ID, "landed", "Landed in", 170),
        pick(F_SIDE_ID, "side", "Side", 110),
        html.Div(className="blotter-filter", children=[
            html.Label("Trade date"),
            dcc.DatePickerRange(id=F_DATES_ID, start_date=s["start"], end_date=s["end"],
                                min_date_allowed=dates[0], max_date_allowed=dates[1], display_format="D MMM YYYY",
                                first_day_of_week=1, clearable=True, start_date_placeholder_text="from",
                                end_date_placeholder_text="to", className="blotter-date-range")]),
        html.Button("Clear", id=CLEAR_ID, n_clicks=0, className="btn btn--ghost",
                    style={} if is_filtered(s) else {"display": "none"},
                    title="Clear every filter of the fills"),
    ]
    if s["from_book"]:
        children.append(html.Button([f"From the Book: {s['from_book']} ", html.Span("×", className="tk-chev")],
                                    id=CHIP_CLEAR_ID, n_clicks=0, className="btn btn--ghost blotter-from-book",
                                    title=f"Showing the fills of {s['from_book']}, opened from its Book row; click to "
                                          f"show every fill"))
    return [html.Div(children, className="blotter-filter-bar")]


def section() -> html.Div:
    """The fills card, static in the tab's layout: the strip (title, count, Download CSV), the bar
    slot and the table slot, both filled by callbacks; the two session stores."""
    return kit.card(id=SECTION_ID, children=[
        kit.strip([kit.strip_title("Every fill", "One row per fill on file, open, settled or closed out, as the file "
                                                 "gave it and as the app read it. No P&L here: the Book is the one "
                                                 "place for it. A correction is a re-upload."),
                   html.Span(id=META_ID, className="book-section-meta"),
                   html.Button("Download CSV", id=CSV_ID, n_clicks=0, className="btn btn--ghost",
                               title="The fills showing, every column, at full figures"),
                   dcc.Download(id=DOWNLOAD_ID)]),
        html.Div(id=BAR_SLOT_ID, style={"padding": "8px 12px 0"}),
        html.Div(id=FILLS_BODY_ID, className=kit.SLOT_CLASS),
        dcc.Store(id=FILTER_STORE_ID, storage_type="session"),
        dcc.Store(id=SORT_STORE_ID, storage_type="session"),
        dcc.Store(id=BAR_REV_ID, data=0),
    ])


# ---- the last upload
_CONVERTED_RE = re.compile(r"(\d+) fill\(s\) converted from the broker's price units[^:]*:\s*([^.]+)\.")


def _in_file(report: dict) -> int:
    return sum(int(report.get(k) or 0) for k in _FILE_COUNT_KEYS)


def _long_date(iso) -> str:
    """'Tue 22 Sep 2026' (the text as it is when it is not a date)."""
    try:
        return dt.date.fromisoformat(str(iso)[:10]).strftime("%a %d %b %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return str(iso or "")


def reason_words(reason) -> str:
    """The upload's reason in plain words, with no file path (`formatting.plain_words`: the
    contract list, the book filter)."""
    return cap(plain_words(reason))


def what_to_do(kind: str, reason: str) -> str:
    """What the user does about a row that did not become a trade, in plain words (display only,
    read off the upload's own reason)."""
    why = str(reason or "").lower()
    if "left the app" in why or "retired" in why or ("not loaded" in why and "product" in why):
        return "Nothing: the app no longer carries this product."
    if any(w in why for w in ("fee", "cash movement", "single-currency", "one currency", "balance")):
        return "Nothing: a cash movement, not a trade."
    if any(w in why for w in ("cancel", "pending", "void", "draft", "status")):
        return "Nothing: its status keeps it out."
    if "ambiguous" in why or "candidates" in why:
        return "Name the exchange in the row (Currency or Execution Venue), then re-upload."
    if any(w in why for w in ("not in the contract universe", "contracts.csv", "unknown", "no contract")):
        return "Check the symbol; if it is right, add the contract to the contract list. Then re-upload."
    if "contradict" in why or "disagree" in why:
        return "Correct the two cells that disagree in the file, then re-upload."
    if "fund" in why or "trader" in why or "desk" in why:
        return "Nothing, unless it is Jason's: then the book filter needs its code."
    if kind == "REJECTED":
        return "Correct the row in the file, then re-upload."
    return "Nothing: the app does not load this kind of row."


def need_fix_count(report: Optional[dict], issues: List[dict]) -> Optional[int]:
    """How many rows of the last upload need a fix (on file as trades whose contract is not
    recognised: every row loads, 2026-09-29): the report's `need_fix`, else its rows of kind
    UNRECOGNISED in the upload's issues; None when neither the report nor the issues know the kind
    (an upload recorded before the rule: the line keeps its old wording)."""
    listed = sum(1 for i in issues if str(i.get("kind") or "") == UNRECOGNISED and _of_upload(i, report))
    if report is not None and "need_fix" in report:
        return int(report.get("need_fix") or 0)
    return listed if listed else None


def _of_upload(issue: dict, report: Optional[dict]) -> bool:
    """Whether a need-fix row is the last upload's own (an earlier upload's trade still on file keeps
    its row, with that upload's file name and time)."""
    if report is None:
        return True
    name, at = str(issue.get("filename") or ""), str(issue.get("uploaded_at") or "")
    if name and name != str(report.get("filename") or ""):
        return False
    return not at or not report.get("uploaded_at") or at[:16] == str(report.get("uploaded_at"))[:16]


def last_upload_line(report: dict, n_rejects: int, need_fix: Optional[int] = None, older: int = 0) -> list:
    """The one line: file · time · rows · new · updated · removed as cancelled · N rows need a fix
    (red, a link to the list, when any) · not loaded (amber, only when an older upload left some) ·
    prices converted; every figure's detail on hover. `need_fix` None: an upload recorded before
    every row loaded, said in the old words."""
    loaded = _in_file(report)
    excluded = int(report.get("excluded_rows") or 0)
    rows = loaded + n_rejects + excluded + (need_fix or 0)
    parts: list = [
        html.Span(str(report.get("filename") or "(no file name)"), className="tk-bold",
                  title=plain_words(report.get("summary")) or None),
        html.Span(ny_time(report.get("uploaded_at")), title=f"{report.get('uploaded_at')} (UTC)"),
        html.Span(_plural(rows, "row"), title=f"{loaded:,} became trades, "
                                              + (f"{need_fix:,} on file needing a fix, " if need_fix else "")
                                              + f"{n_rejects:,} not loaded, {excluded:,} left "
                                              f"out by the book filter"
                                              + (f": {plain_words(report.get('excluded_text'))}" if excluded else "")),
        html.Span(f"{int(report.get('added') or 0):,} new", title="Trade Ids not on file before: added"),
        html.Span(f"{int(report.get('replaced') or 0):,} updated",
                  title="Trade Ids already on file: replaced by the file's rows"),
        html.Span(f"{int(report.get('removed') or 0):,} removed as cancelled",
                  title="Rows whose status is cancelled, void, deleted, rejected or failed: those trades were removed"),
    ]
    if need_fix is None:
        if n_rejects:
            parts.append(html.A(f"{n_rejects:,} not loaded", id=NOT_LOADED_LINK_ID, href=f"#{REJECTS_ID}", n_clicks=0,
                                className="cell-amber", title="Rows of the file that did not become trades: click for "
                                                              "the list"))
        else:
            parts.append(html.Span("0 not loaded", title="Every row of the file became a trade or was left out by the "
                                                         "book filter"))
    else:
        if need_fix:
            parts.append(html.A(f"{need_fix:,} row{'s' if need_fix != 1 else ''} need{'s' if need_fix == 1 else ''} a fix",
                                id=NOT_LOADED_LINK_ID, href=f"#{REJECTS_ID}", n_clicks=0, className="cell-red",
                                title="Rows on file as trades whose contract the app does not recognise: their P&L is "
                                      "blank until the symbol is added to the contract list. Click for the list."))
        else:
            parts.append(html.Span("0 need a fix", title="Every row of the file was recognised"))
        if older:
            parts.append(html.A(f"+ {older:,} older on file need{'s' if older == 1 else ''} a fix", href=f"#{REJECTS_ID}",
                                className="cell-red", title="Trades from earlier uploads still on file whose contract "
                                                            "the app does not recognise: listed below"))
        if n_rejects:
            parts.append(html.Span(f"{n_rejects:,} not loaded", className="cell-amber",
                                   title="Rows an older parser did not turn into trades: listed below"))
    found = _CONVERTED_RE.search(str(report.get("summary") or ""))
    if found:
        what = found.group(2).strip()
        parts.append(html.Span(f"{found.group(1)} prices converted",
                               title=plain_words(f"Fills converted from the broker's price units: {what}")))
    else:
        parts.append(html.Span("0 prices converted", title="No fill needed converting from the broker's price units"))
    out: list = []
    for p in parts:
        if out:
            out.append(html.Span(" · ", className="data-status-sep"))
        out.append(p)
    out.append(html.Span(f" · {int(report.get('on_file_after') or 0):,} fills on file after it",
                         className="book-section-meta"))
    return out


def rejects_table(rows: List[dict], need_fix: Sequence[dict] = (), warnings: Sequence[dict] = ()) -> html.Details:
    """The rows of the last upload that need the user: those on file needing a fix (a contract not
    recognised: red, their Trade Id set), those not loaded (an older parser's) and the row-level
    warnings (loaded on the primary field: amber). Row in file · Trade Id · Symbol as written ·
    Reason · What to do; a fold under the line, opened by its link (or its own title)."""
    cols: Tuple[kit.Column, ...] = (
        ("row", "Row in file", "", "The row's number in the file, the header row not counted.", False),
        ("trade_id", "Trade Id", "l", "The row's Trade Id.", False),
        ("symbol", "Symbol as written", "l", "The file's Symbol cell as written.", False),
        ("reason", "Reason", "l", "What is wrong with the row, in plain words.", False),
        ("todo", "What to do", "l", "What would fix it, or why nothing needs doing.", False),
    )
    body = []
    for i in need_fix:
        body.append(html.Tr([
            kit.td(str(i.get("row_no") or MISSING)),
            kit.td(str(i.get("trade_id") or "") or missing_cell(NO_TRADE_ID), left=True),
            kit.td(html.Span(str(i.get("symbol") or "") or MISSING, className="cell-red"), left=True),
            kit.td(reason_words(i.get("reason")), left=True,
                   title="on file as a trade; its P&L is blank until the contract is mapped"),
            kit.td("Check the symbol; if it is right, add the contract to the contract list. The trade then prices "
                   "without a new upload.", left=True),
        ]))
    for i in rows:
        kind = str(i.get("kind") or "")
        body.append(html.Tr([
            kit.td(str(i.get("row_no") or MISSING)),
            kit.td(str(i.get("trade_id") or "") or missing_cell(NO_TRADE_ID), left=True),
            kit.td(str(i.get("symbol") or "") or missing_cell("the row has no symbol"), left=True),
            kit.td(reason_words(i.get("reason")), left=True,
                   title="could not be read" if kind == "REJECTED" else "a kind of row the app does not load"),
            kit.td(what_to_do(kind, str(i.get("reason") or "")), left=True),
        ]))
    for i in warnings:
        body.append(html.Tr([
            kit.td(str(i.get("row_no") or MISSING)),
            kit.td(str(i.get("trade_id") or "") or missing_cell(NO_TRADE_ID), left=True),
            kit.td(str(i.get("symbol") or "") or missing_cell("the row has no symbol"), left=True),
            kit.td(html.Span(reason_words(i.get("reason")), className="cell-amber"), left=True,
                   title="loaded, on the primary field"),
            kit.td("Check the two cells in the file; nothing to do if the loaded value is right.", left=True),
        ]))
    heads = []
    if need_fix:
        heads.append(html.Span(f"Rows that need a fix ({len(need_fix)})", className="cell-red"))
    if rows:
        heads.append(html.Span(f"Rows not loaded ({len(rows)})", className="cell-amber"))
    if warnings:
        heads.append(html.Span(f"Row warnings ({len(warnings)})", className="cell-amber"))
    summary: list = []
    for h in heads:
        if summary:
            summary.append(html.Span(" · ", className="data-status-sep"))
        summary.append(h)
    return html.Details(id=REJECTS_ID, open=False, className="book-fold tk-fold-block", children=[
        html.Summary(summary),
        kit.table(kit.head(cols, None, SORT_TYPE + "-none"), body, className="tk-small"),
    ])


def last_upload_block(conn: sqlite3.Connection) -> html.Div:
    """The last upload: its line, the rows that need a fix or did not load (with the row-level
    warnings), the file-level warnings."""
    try:
        from data.ingest.upload import last_upload_issues, last_upload_report
        report, issues = last_upload_report(conn), last_upload_issues(conn)
    except Exception as exc:  # noqa: BLE001
        return html.Div(id=LAST_UPLOAD_ID, className="book-section-head", children=[
            html.Span("Last upload", className="book-section-title"), " ",
            missing_cell(f"the upload record could not be read ({type(exc).__name__}: {exc})")])
    title = html.Span("Last upload", className="book-section-title",
                      title="What the last blotter upload did, as it was recorded: the merge by Trade Id and every "
                            "row of the file that needs a fix.")
    if report is None and not issues:
        return html.Div(id=LAST_UPLOAD_ID, className="blotter-last-upload-section",
                        children=[html.Div([title, ": ", html.Span(cap(NO_HISTORY) + ".", className="book-section-meta")])])
    kinds = [str(i.get("kind") or "") for i in issues]
    need = [i for i, k in zip(issues, kinds) if k == UNRECOGNISED]
    rejects = [i for i, k in zip(issues, kinds) if k not in ("WARNING", UNRECOGNISED)]
    warnings = [i for i, k in zip(issues, kinds) if k == "WARNING"]
    row_warnings = [w for w in warnings if w.get("row_no") or w.get("trade_id")]
    file_warnings = [w for w in warnings if not (w.get("row_no") or w.get("trade_id"))]
    n_fix = need_fix_count(report, issues)
    n_older = sum(1 for i in need if not _of_upload(i, report))
    children: list = []
    if report is not None:
        children.append(html.Div([title, " ", *last_upload_line(report, len(rejects), n_fix, n_older)],
                                 className="blotter-upload-line"))
    else:
        children.append(html.Div([title, ": ", html.Span("No summary recorded", className="book-section-meta")]))
    if need or rejects or row_warnings:
        children.append(rejects_table(rejects, need, row_warnings))
    for w in file_warnings:
        children.append(html.Div([html.Span("About the file", className="marker marker--amber"), " ",
                                  plain_words(w.get("reason"))], className="blotter-file-warning"))
    return html.Div(id=LAST_UPLOAD_ID, className="blotter-last-upload-section", children=children)


# ---- the upload history
def history_fold(conn: sqlite3.Connection) -> Optional[html.Details]:
    """Every recorded upload, newest first, in a closed fold: time · file · new · updated ·
    removed · not loaded; a click on an upload opens the Trade Ids it touched under it."""
    try:
        from data.ingest.upload import last_upload_issues, last_upload_report, upload_history
        history, report = upload_history(conn), last_upload_report(conn)
        n_rejects = sum(1 for i in last_upload_issues(conn) if str(i.get("kind") or "") not in ("WARNING", UNRECOGNISED))
    except Exception as exc:  # noqa: BLE001
        return html.Details(id=HISTORY_ID, className="book-fold tk-fold-block", children=[
            html.Summary("Upload history · could not be read"),
            missing_cell(f"the history could not be read ({type(exc).__name__}: {exc})")])
    if not history and report is None:
        return None
    summary = html.Summary([html.Span("Upload history", className="book-section-title",
                                      title="Every upload recorded on this database, newest first. Click an upload for "
                                            "the Trade Ids it added, replaced and removed."),
                            html.Span(f" · {_plural(len(history), 'upload')}" if history
                                      else " · none recorded yet: the history starts at the next upload",
                                      className="book-section-meta")])
    if not history:
        return html.Details([summary], id=HISTORY_ID, className="book-fold tk-fold-block")
    cols: Tuple[kit.Column, ...] = (
        ("when", "Time", "l", "When the upload was made, New York time.", False),
        ("file", "File", "l", "The file uploaded; its summary on hover.", False),
        ("new", "New", "", "Trade Ids added.", False),
        ("updated", "Updated", "", "Trade Ids already on file, replaced by the file's rows.", False),
        ("removed", "Removed", "", "Trades removed: cancelled in the file, or an old database's manual entries.",
         False),
        ("not_loaded", "Need a fix", "", "Rows of the file on file as trades whose contract is not recognised (P&L "
                                        "blank until mapped); for an upload before 29 Sep, the rows not loaded (the "
                                        "last upload only).", False),
    )
    body = []
    latest = history[0].get("id")
    for h in history:
        hid = str(h.get("id"))
        removed = int(h.get("removed") or 0) + int(h.get("removed_manual") or 0)
        if "need_fix" in h:
            nf = int(h.get("need_fix") or 0)
            not_loaded = html.Span(str(nf), className="cell-red" if nf else None)
        else:
            not_loaded = (html.Span(str(n_rejects), className="cell-amber" if n_rejects else None)
                          if h.get("id") == latest else missing_cell("recorded for the last upload only"))
        body.append(html.Tr(id={"type": HIST_ROW_TYPE, "idx": hid}, n_clicks=0, className="tk-row", children=[
            kit.td([html.Span("▸ ", className="tk-chev"), ny_time(h.get("uploaded_at"))], left=True),
            kit.td(str(h.get("filename") or ""), left=True, title=h.get("summary")),
            kit.td(f"{int(h.get('added') or 0):,}"), kit.td(f"{int(h.get('replaced') or 0):,}"),
            kit.td(f"{removed:,}"), kit.td(not_loaded),
        ]))
        lines = []
        for key, word in (("added_ids", "New"), ("replaced_ids", "Updated"), ("removed_ids", "Removed")):
            ids = [str(t) for t in h.get(key) or []]
            if ids:
                lines.append(html.Div([html.Span(f"{word} ({len(ids)}): ", className="tk-bold"), ", ".join(ids)]))
        if not lines:
            lines = [html.Div("No Trade Id touched.", className="book-section-meta")]
        body.append(html.Tr(id={"type": HIST_IDS_TYPE, "idx": hid}, className="tk-panel", style={"display": "none"},
                            children=html.Td(lines, colSpan=len(cols))))
    return html.Details([summary, kit.table(kit.head(cols, None, SORT_TYPE + "-none"), body, className="tk-small")],
                        id=HISTORY_ID, className="book-fold tk-fold-block")


# ---- the callbacks
def _open_ro(get_db_path):
    from ui.app import connect_readonly
    return connect_readonly(get_db_path())


def register(app, get_db_path: Callable[[], object]) -> None:
    """The fills' callbacks: the bar drawn from the stored filter, a control writing it back,
    Clear and the chip, "See fills" from a Book trade, the table, the sort, the CSV, the history
    rows opening, the "not loaded" link."""
    import dash
    from ui.tabs.blotter_pricing import pricing_snapshot

    def _frame(as_of: str):
        conn = _open_ro(get_db_path)
        try:
            with pricing_snapshot(conn, "Blotter fills"):
                return fills_frame(conn, as_of)
        finally:
            conn.close()

    @app.callback(Output(BAR_SLOT_ID, "children"),
                  Input(AS_OF_STORE_ID, "data"), Input(DATA_REVISION_ID, "data"), Input(BOOK_REVISION_ID, "data"),
                  Input(BAR_REV_ID, "data"), State(FILTER_STORE_ID, "data"))
    def _bar(as_of, _data_rev, _book_rev, _bar_rev, state):
        """The bar from the stored filter: on mount, a new as-of or new data, and when the filter was
        set from outside the bar; never on a control's own change, so the search keeps its focus."""
        options: Dict[str, List[dict]] = {}
        dates: Tuple[Optional[str], Optional[str]] = (None, None)
        if as_of:
            try:
                df, _issues, _with = _frame(as_of)
                options = filter_options(df)
                days = sorted(d for d in df["trade_date"].tolist() if d) if not df.empty else []
                dates = (days[0], days[-1]) if days else (None, None)
            except Exception:  # noqa: BLE001 -- the bar still filters on the values set
                log.exception("Blotter: the filter choices could not be read for %s", as_of)
        return bar(state, options, dates)

    @app.callback(Output(FILTER_STORE_ID, "data"),
                  Input(F_TRADE_ID, "value"), Input(F_LANDED_ID, "value"), Input(F_SIDE_ID, "value"),
                  Input(F_DATES_ID, "start_date"), Input(F_DATES_ID, "end_date"), Input(F_SEARCH_ID, "value"),
                  State(FILTER_STORE_ID, "data"), prevent_initial_call=True)
    def _write_filter(trade, landed, side, start, end, search, current):
        cur = normal_filter(current)
        new = normal_filter({"trade": trade, "landed": landed, "side": side, "start": start, "end": end,
                             "search": search, "from_book": cur["from_book"]})
        return dash.no_update if new == cur else new

    # Clear and the chip are two callbacks: the chip is in the bar only after "See fills", and a
    # callback with an input missing from the page never fires.
    @app.callback(Output(FILTER_STORE_ID, "data", allow_duplicate=True),
                  Output(BAR_REV_ID, "data", allow_duplicate=True),
                  Input(CLEAR_ID, "n_clicks"), State(BAR_REV_ID, "data"), prevent_initial_call=True)
    def _clear(clicks, rev):
        if not clicks:
            return dash.no_update, dash.no_update
        return dict(DEFAULT_FILTER), int(rev or 0) + 1

    @app.callback(Output(FILTER_STORE_ID, "data", allow_duplicate=True),
                  Output(BAR_REV_ID, "data", allow_duplicate=True),
                  Input(CHIP_CLEAR_ID, "n_clicks"), State(FILTER_STORE_ID, "data"), State(BAR_REV_ID, "data"),
                  prevent_initial_call=True)
    def _chip_clear(clicks, current, rev):
        if not clicks:
            return dash.no_update, dash.no_update
        s = normal_filter(current)
        s.update(trade=[], from_book="")
        return s, int(rev or 0) + 1

    @app.callback(Output(CLEAR_ID, "style"), Input(FILTER_STORE_ID, "data"))
    def _clear_shown(state):
        """Clear shows only while something is set (the bar is not redrawn on a filter change)."""
        return {} if is_filtered(state) else {"display": "none"}

    try:
        from ui.tabs.trade_filter import FILLS_STORE_ID
    except Exception:  # noqa: BLE001 -- no "See fills" link on this build: nothing to follow
        FILLS_STORE_ID = None
    if FILLS_STORE_ID:
        @app.callback(Output(FILTER_STORE_ID, "data", allow_duplicate=True),
                      Output(BAR_REV_ID, "data", allow_duplicate=True),
                      Output(FILLS_STORE_ID, "data", allow_duplicate=True),
                      # fired when the Blotter mounts (its table's slot appears), the request read then:
                      # never while the tab is not in the page (its outputs would not exist)
                      Input(FILLS_BODY_ID, "id"), State(FILLS_STORE_ID, "data"), State(BAR_REV_ID, "data"),
                      prevent_initial_call="initial_duplicate")
        def _see_fills(_mounted, request, rev):
            """A Book trade's "See fills" ({"trade": name}, set as the tab switches here): the fills
            filtered to that trade alone, with the chip; the request is then consumed (None), so a
            later visit keeps whatever filter the user sets."""
            name = str((request or {}).get("trade") or "") if isinstance(request, dict) else ""
            if not name:
                return dash.no_update, dash.no_update, dash.no_update
            state = dict(DEFAULT_FILTER, trade=[name], from_book=name)
            return state, int(rev or 0) + 1, None

    @app.callback(Output(FILLS_BODY_ID, "children"), Output(META_ID, "children"),
                  Input(FILTER_STORE_ID, "data"), Input(SORT_STORE_ID, "data"), Input(AS_OF_STORE_ID, "data"),
                  Input(DATA_REVISION_ID, "data"), Input(BOOK_REVISION_ID, "data"))
    def _table(state, sort, as_of, _data_rev, _book_rev):
        if not as_of:
            return html.P("No as-of date available.", className="book-section-meta"), ""
        try:
            df, _issues, _with = _frame(as_of)
        except Exception as exc:  # noqa: BLE001 -- the table says why instead of an HTTP 500
            log.exception("Blotter: the fills could not be read for %s", as_of)
            return html.P(f"The fills could not be read ({type(exc).__name__}: {exc}).",
                          className="book-section-meta"), ""
        if df.empty:
            return html.P("No trades on file: upload a blotter.", className="book-section-meta"), ""
        kept = filter_fills(df, state)
        meta = f"{len(kept):,} of {len(df):,}" if len(kept) != len(df) else f"{len(df):,} fills"
        return compact(fills_table(kept, len(df), sort, state, as_of)), meta

    @app.callback(Output(SORT_STORE_ID, "data"), Input({"type": SORT_TYPE, "idx": ALL}, "n_clicks"),
                  State(SORT_STORE_ID, "data"), prevent_initial_call=True)
    def _sort(_clicks, current):
        trig = dash.ctx.triggered_id
        if not isinstance(trig, dict) or not kit.clicked(dash.ctx.triggered):
            return dash.no_update
        return kit.next_sort(current, str(trig.get("idx") or ""))

    @app.callback(Output(DOWNLOAD_ID, "data"), Input(CSV_ID, "n_clicks"),
                  State(FILTER_STORE_ID, "data"), State(SORT_STORE_ID, "data"), State(AS_OF_STORE_ID, "data"),
                  prevent_initial_call=True)
    def _csv(n_clicks, state, sort, as_of):
        if not n_clicks or not as_of:
            return None
        df, _issues, _with = _frame(as_of)
        return fills_csv(filter_fills(df, state), sort)

    app.clientside_callback(
        """function(n, style) {
            if (!n) { return window.dash_clientside.no_update; }
            return (style && style.display === 'none') ? {} : {display: 'none'};
        }""",
        Output({"type": HIST_IDS_TYPE, "idx": MATCH}, "style"),
        Input({"type": HIST_ROW_TYPE, "idx": MATCH}, "n_clicks"),
        State({"type": HIST_IDS_TYPE, "idx": MATCH}, "style"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """function(n) { return n ? true : window.dash_clientside.no_update; }""",
        Output(REJECTS_ID, "open"),
        Input(NOT_LOADED_LINK_ID, "n_clicks"),
        prevent_initial_call=True,
    )
