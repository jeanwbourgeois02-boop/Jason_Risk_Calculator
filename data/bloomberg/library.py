"""The Bloomberg library: the market data the trades on file need for their P&L, and
nothing else (user decision 2026-09-21: "a cache to monitor the library of tickers that
are necessary - and only pull data that is essential to calculate the pnl of the trades
... we only add to the cache when there are new trades input").

`bbg_library` holds one row per (trade, thing that trade needs), with the dates the
trade needs it between. It changes only when the trades change: triggers on `trades` /
`trade_legs` (data/ingest/schema.py) mark it out of date, an upload brings it up to date
at once (`sync`), and any reader finding it out of date brings it up to date before
reading. Nothing else writes it -- a Bloomberg pull only READS it (`needed_on`), so what
is not in the library is never asked of Bloomberg.

What a trade needs, by product (the same sets the pull derived from the trades on every
cycle until 2026-09-21; the rules did not change, only where they are kept):
  * FX spot / forward / swap: the pair's SPOT until its last leg settles; a FWD_OUTRIGHT
    at each leg's own settle date until that date; for a cross, the SPOT of each
    currency's own USD pair (USD conversion of delta and P&L).
  * Future: FUTURE_PX at the contract's expiry.
  * FX option, until expiry: the pair's SPOT, the SPOT of its USD-conversion pairs, the
    OIS curve of both currencies and the pair's vol smile (on every day it is open: live
    today, and from Bloomberg's daily history for a past close, 2026-09-22, so a past day
    prices the option from its own inputs), and -- today only -- a FWD_OUTRIGHT at the
    expiry (the past day's curve is built from the tenor history instead). Dormant in the
    commodity book (2026-09-24) but kept.
  * Listed option (EQ_OPTION), until expiry: FUTURE_PX on the option's OWN Bloomberg
    ticker (`listed_option_ticker`) -- a listed option is priced exactly like a future --
    which is all its P&L needs; kept for the options on futures of the commodity
    conversion plan's Phase 5.

  * Commodity futures (2026-09-24, commodity conversion Phase 1): a future or a listed
    option quoted in a currency other than USD (CNY, EUR, GBP, JPY, MYR, CAD) also needs
    the SPOT of that currency's USD pair, role CONVERSION, from trade date until expiry --
    its P&L converts to USD at spot of the valuation date (user decision 2026-09-24), the
    cross's rule. A future whose instruments.base_ccy is a contract root ('NYMEX:CL') also
    needs Bloomberg's own contract dates (kind CONTRACT_DATES: FUT_LAST_TRADE_DT and
    FUT_NOTICE_FIRST, key = the canonical contract id, today's pull only) until its expiry;
    the need is met once data.contracts.static_dates holds them, and a met need is never
    asked for (`needed_on`, `contract_dates_needed`).

Retired (2026-09-24, commodity conversion Phase 2, user approval of the same day): the
macro book's needs are no longer listed -- a swap's OIS curve and overnight fixings, an NDF
currency's 1M outright (NDF_1M) and fixing (NDF_FIX), a listed index option's index level
(SPOT, role UNDERLYING) and dividend yield (DIV_YIELD). An older library holding such rows
drops them at its next sync (LIBRARY_VERSION).

Not requestable (2026-09-24): a row of a kind that names one security but carries no
ticker -- a future of a root whose Bloomberg ticker is an unverified placeholder
(instruments.bbg_ticker = '') -- stays in the library so the gap is listed, but every row
dict carries `requestable` (False) and `reason` ("no verified Bloomberg ticker for
<root_id>"), and `needed_on` / `needed_in_range` leave it out unless asked to include it,
so no pull and no backfill can ask Bloomberg for it.

`kind` is the mark_type for what lands in `marks` (SPOT, FWD_OUTRIGHT, FUTURE_PX),
OIS_CURVE / VOL_SMILE for what lands in `curve_quotes` / `vol_quotes` (each stands for a
set of Bloomberg securities, `tickers` lists them), and CONTRACT_DATES for a future's
contract dates. What a PAST close needs (`needed_on(..., historical=True)`,
`needed_in_range`) is the MARK_KINDS plus, for an FX option, its HISTORY_INPUT_KINDS (the
OIS curve, the vol smile); the LIVE_ONLY_KINDS are today's.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

SENTINEL = "9999-12-31"
ROLE_PAIR = "PAIR"                 # the traded pair / contract / currency itself
ROLE_CONVERSION = "CONVERSION"     # a USD-conversion pair's SPOT
MARK_KINDS = ("SPOT", "FWD_OUTRIGHT", "FUTURE_PX")

# Bump whenever `compute` learns a new need (a kind, a ticker rule) or drops one: a library
# synced by older code is then out of date although no trade changed, and the next reader
# resyncs it (2026-09-22: NDF_FIX had been added to `compute`, but every existing database
# kept the library its last upload wrote, so no pull asked for a fixing until the next upload).
# 2026-09-24.1: commodity futures -- the USD-conversion SPOT of a non-USD future or listed
# option, the CONTRACT_DATES kind, and the not-requestable flag on a row with no ticker.
# 2026-09-24.2: the macro needs retired (NDF_1M, NDF_FIX, FIXINGS, a swap's OIS curve, a
# listed option's UNDERLYING index level, DIV_YIELD and its OIS curve), so an existing
# database drops those rows before the next pull.
LIBRARY_VERSION = "2026-09-24.2"
# CONTRACT_DATES (2026-09-24): Bloomberg's own last trade and first notice dates of a
# commodity future, asked by today's pull (bbg-live) and stored through
# data.contracts.store_static_dates. Key = the canonical contract id, settle_date SENTINEL.
CONTRACT_DATES = "CONTRACT_DATES"
CONTRACT_DATES_FIELDS = ("FUT_LAST_TRADE_DT", "FUT_NOTICE_FIRST")
# Kinds that stand for a set of securities and so carry no ticker of their own; every other
# kind names one security, and a row of it with bbg_ticker '' cannot be asked for.
SET_KINDS = ("OIS_CURVE", "VOL_SMILE")
_ROOT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*:[A-Z0-9]+$")    # 'NYMEX:CL': a contract root id
# Asked for by today's pull only, never of Bloomberg's history.
LIVE_ONLY_KINDS = (CONTRACT_DATES,)
# The inputs a past close prices its FX options from (2026-09-22; until then the backfill
# wrote a past day's closes and price_close skipped every option whose smile or curve that
# day lacked, so options had no true 5d / MTD / YTD): the OIS curve of every currency an FX
# option needs that day, the vol smile of every pair with an FX option open that day -- the
# same tickers the live rates and vol steps ask for, from Bloomberg's daily history, into
# the same tables.
HISTORY_INPUT_KINDS = ("OIS_CURVE", "VOL_SMILE")
HISTORY_INPUT_PRODUCTS = ("FX_OPTION",)
# Products whose rows stop being asked for once the ledger has realised the trade (the
# pull's own rule for options; an FX leg or a future simply runs to its date).
_REALISED_FILTER_PRODUCTS = ("FX_OPTION",)

COLUMNS = ("trade_id", "kind", "key", "settle_date", "bbg_ticker", "role", "product",
           "needed_from", "needed_until", "added_at")

_FX_LEGS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.instrument_id, i.bbg_ticker, i.base_ccy, i.quote_ccy, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX'
ORDER BY i.instrument_id, l.settle_date, t.trade_id
"""

_FUTURE_LEGS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.instrument_id, i.bbg_ticker, l.settle_date,
       i.base_ccy, i.quote_ccy, i.expiry_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FUTURE'
ORDER BY i.instrument_id, l.settle_date, t.trade_id
"""

_OPTIONS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.base_ccy, i.quote_ccy, i.expiry_date
FROM trades_official t JOIN instruments i USING (instrument_id)
WHERE t.product = 'FX_OPTION'
ORDER BY i.base_ccy || i.quote_ccy, i.expiry_date, t.trade_id
"""

_LISTED_OPTIONS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.instrument_id, i.base_ccy, i.quote_ccy, i.bbg_ticker, i.expiry_date,
       COALESCE(o.option_type, ''), COALESCE(o.strike, 0)
FROM trades_official t JOIN instruments i USING (instrument_id)
LEFT JOIN instrument_options o USING (instrument_id)
WHERE t.product = 'EQ_OPTION'
ORDER BY i.instrument_id, t.trade_id
"""

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _pair_row(conn: sqlite3.Connection, pair: str) -> tuple:
    """(instrument_id, bbg_ticker) of the plain FX pair: the `instruments` row when there
    is one, else the conventional name and '<pair> Curncy' (the row `sync` then creates)."""
    row = conn.execute("SELECT instrument_id, bbg_ticker FROM instruments WHERE instrument_id = ?", (pair,)).fetchone()
    return (row[0], row[1]) if row else (pair, f"{pair} Curncy")


def listed_option_ticker(root: str, expiry_iso: str, option_type: str, strike: float) -> str:
    """Bloomberg's ticker of a listed index option: 'SPX US 10/16/26 P7615 Index' (root,
    exchange code, expiry mm/dd/yy, C or P with the strike). '' when a term is missing --
    nothing is asked of Bloomberg under a guessed name."""
    if not (root and strike and strike > 0 and option_type in ("CALL", "PUT")):
        return ""
    try:
        expiry = datetime.strptime(expiry_iso, "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{root} US {expiry:%m/%d/%y} {option_type[0]}{strike:g} Index"


def is_contract_root(base_ccy: Optional[str]) -> bool:
    """Is `base_ccy` a commodity contract root id ('NYMEX:CL', EXCHANGE:CODE), as the
    contract master writes it on a commodity future's instrument? ('ES' for an equity index
    future, a currency for everything else, is not.)"""
    return bool(base_ccy) and bool(_ROOT_ID_RE.match(str(base_ccy)))


def unrequestable_reason(row: dict, root: str = "") -> str:
    """'' when a pull may ask Bloomberg for this library row; otherwise why it may not. A
    kind that stands for a set of securities (SET_KINDS) never carries a ticker of its own;
    any other row with bbg_ticker '' has no security to ask for: for a commodity future,
    because its root's Bloomberg ticker is an unverified placeholder (2026-09-24). `root`
    is the instrument's base_ccy when known."""
    if row["kind"] in SET_KINDS or row.get("bbg_ticker"):
        return ""
    if is_contract_root(root):
        return f"no verified Bloomberg ticker for {root}"
    return f"no Bloomberg ticker for {row['key']}"


def _annotate(conn: sqlite3.Connection, found: List[dict]) -> List[dict]:
    """Adds `requestable` (bool) and `reason` (str, '' when requestable) to every row."""
    blank = sorted({r["key"] for r in found if r["kind"] not in SET_KINDS and not r["bbg_ticker"]})
    roots: Dict[str, str] = {}
    if blank:
        try:
            marks = ",".join("?" * len(blank))
            roots = {k: v for k, v in conn.execute(
                f"SELECT instrument_id, base_ccy FROM instruments WHERE instrument_id IN ({marks})", blank)}
        except sqlite3.OperationalError:
            roots = {}
    for r in found:
        reason = unrequestable_reason(r, roots.get(r["key"], ""))
        r["requestable"] = not reason
        r["reason"] = reason
    return found


def _contract_dates_on_file(conn: sqlite3.Connection, contract_ids) -> set:
    """The contracts among `contract_ids` whose Bloomberg dates are stored
    (data.contracts.static_dates): their CONTRACT_DATES need is met."""
    ids = sorted(set(contract_ids))
    if not ids:
        return set()
    try:
        from data.contracts import static_dates
    except ImportError:         # no contract master in this tree: nothing is on file
        return set()
    out = set()
    for contract_id in ids:
        try:
            if static_dates(conn, contract_id) is not None:
                out.add(contract_id)
        except sqlite3.Error:
            continue
    return out


def compute(conn: sqlite3.Connection) -> List[dict]:
    """Every row the trades on file call for, worked out from `trades_official`,
    `trade_legs` and `instruments`. Read-only; `sync` stores it."""
    from data.bloomberg.live import _usd_pair_name
    rows: Dict[tuple, dict] = {}

    def add(trade_id, product, kind, key, settle, ticker, needed_from, needed_until, role=ROLE_PAIR) -> None:
        pk = (trade_id, kind, key, settle)
        if pk in rows:
            rows[pk]["needed_until"] = max(rows[pk]["needed_until"], needed_until)
            return
        rows[pk] = {"trade_id": trade_id, "kind": kind, "key": key, "settle_date": settle, "bbg_ticker": ticker,
                    "role": role, "product": product, "needed_from": needed_from, "needed_until": needed_until}

    def add_conversions(trade_id, product, ccys, needed_from, needed_until) -> None:
        for ccy in ccys:
            if ccy and ccy != "USD":
                instrument_id, ticker = _pair_row(conn, _usd_pair_name(ccy))
                add(trade_id, product, "SPOT", instrument_id, SENTINEL, ticker, needed_from, needed_until,
                    role=ROLE_CONVERSION)

    for trade_id, product, trade_date, instrument_id, ticker, base, quote, settle in conn.execute(_FX_LEGS_SQL):
        add(trade_id, product, "SPOT", instrument_id, SENTINEL, ticker, trade_date, settle)
        add(trade_id, product, "FWD_OUTRIGHT", instrument_id, settle, ticker, trade_date, settle)
        if base != "USD" and quote != "USD":
            add_conversions(trade_id, product, (base, quote), trade_date, settle)
    for (trade_id, product, trade_date, instrument_id, ticker, settle,
         root, quote, expiry) in conn.execute(_FUTURE_LEGS_SQL):
        ticker = ticker or ""
        add(trade_id, product, "FUTURE_PX", instrument_id, settle, ticker, trade_date, settle)
        # 2026-09-24: a non-USD future's P&L converts at spot of the valuation date.
        add_conversions(trade_id, product, (quote,), trade_date, settle)
        if is_contract_root(root):
            # Bloomberg's own contract dates, until the (estimated) expiry; met once stored.
            until = expiry if expiry and expiry != SENTINEL else settle
            add(trade_id, product, CONTRACT_DATES, instrument_id, SENTINEL, ticker, trade_date, until)
    for trade_id, product, trade_date, base, quote, expiry in conn.execute(_OPTIONS_SQL):
        pair = f"{base}{quote}"
        instrument_id, ticker = _pair_row(conn, pair)
        add(trade_id, product, "SPOT", instrument_id, SENTINEL, ticker, trade_date, expiry)
        add(trade_id, product, "FWD_OUTRIGHT", instrument_id, expiry, ticker, trade_date, expiry)
        add_conversions(trade_id, product, (base, quote), trade_date, expiry)
        for ccy in (base, quote):
            add(trade_id, product, "OIS_CURVE", ccy, SENTINEL, "", trade_date, expiry)
        add(trade_id, product, "VOL_SMILE", pair, SENTINEL, "", trade_date, expiry)
    for (trade_id, product, trade_date, instrument_id, root, quote, _underlying, expiry,
         option_type, strike) in conn.execute(_LISTED_OPTIONS_SQL):
        # Bloomberg's own price of the option is all its P&L needs; the Greeks' index level,
        # dividend yield and OIS curve were retired with the equity index (2026-09-24).
        ticker = listed_option_ticker(root, expiry, option_type, strike)
        if ticker:
            add(trade_id, product, "FUTURE_PX", instrument_id, expiry, ticker, trade_date, expiry)
        add_conversions(trade_id, product, (quote,), trade_date, expiry)    # 2026-09-24: non-USD listed option
    # IRS (retired 2026-09-24): a swap needs nothing from the library any more.
    return list(rows.values())


def is_out_of_date(conn: sqlite3.Connection) -> bool:
    """True when the trades changed since the last sync, or the code did (LIBRARY_VERSION)."""
    row = conn.execute("SELECT dirty, code_version FROM bbg_library_state WHERE id = 1").fetchone()
    return row is None or bool(row[0]) or str(row[1] or "") != LIBRARY_VERSION


def sync(conn: sqlite3.Connection) -> dict:
    """Bring `bbg_library` into line with the trades on file: rows a trade newly calls for
    are added (their `added_at` is now), rows of trades that are gone are removed, a row
    whose dates moved is updated, everything else is left as it was. Also creates the
    plain pair `instruments` row a conversion pair or an option's pair needs before a
    mark can be written for it. Returns {added, removed, total}."""
    from data.bloomberg.live import _ensure_fx_instruments
    wanted = {(r["trade_id"], r["kind"], r["key"], r["settle_date"]): r for r in compute(conn)}
    _ensure_fx_instruments(conn, [r["key"] for r in wanted.values() if r["kind"] in ("SPOT", "FWD_OUTRIGHT")])
    have = {(r[0], r[1], r[2], r[3]): r for r in conn.execute(
        "SELECT trade_id, kind, key, settle_date, bbg_ticker, role, product, needed_from, needed_until FROM bbg_library")}
    now = _now_iso()
    added = removed = 0
    with conn:
        for pk in have.keys() - wanted.keys():
            conn.execute("DELETE FROM bbg_library WHERE trade_id = ? AND kind = ? AND key = ? AND settle_date = ?", pk)
            removed += 1
        for pk, r in wanted.items():
            values = (r["bbg_ticker"], r["role"], r["product"], r["needed_from"], r["needed_until"])
            if pk not in have:
                conn.execute("INSERT INTO bbg_library (trade_id, kind, key, settle_date, bbg_ticker, role, product, "
                             "needed_from, needed_until, added_at) VALUES (?,?,?,?,?,?,?,?,?,?)", pk + values + (now,))
                added += 1
            elif tuple(have[pk][4:]) != values:
                conn.execute("UPDATE bbg_library SET bbg_ticker = ?, role = ?, product = ?, needed_from = ?, "
                             "needed_until = ? WHERE trade_id = ? AND kind = ? AND key = ? AND settle_date = ?",
                             values + pk)
        conn.execute("INSERT OR REPLACE INTO bbg_library_state (id, dirty, synced_at, code_version) VALUES (1, 0, ?, ?)",
                     (now, LIBRARY_VERSION))
    return {"added": added, "removed": removed, "total": len(wanted)}


def rows(conn: sqlite3.Connection) -> List[dict]:
    """The library, brought up to date first when the trades changed since it was last
    written. On a connection that cannot write (every tab callback reads through a
    read-only handle) or a database without the table, the same rows are worked out in
    memory instead, so a reader is never shown a library that is behind the book.

    Every row also carries `requestable` and `reason` (2026-09-24, `unrequestable_reason`),
    worked out on read, never stored."""
    try:
        if is_out_of_date(conn):
            sync(conn)
        found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM bbg_library").fetchall()
        return _annotate(conn, [dict(zip(COLUMNS, r)) for r in found])
    except sqlite3.OperationalError:
        return _annotate(conn, [{**r, "added_at": ""} for r in compute(conn)])


def _realised_ids(conn: sqlite3.Connection) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT trade_id FROM realised_pnl")}
    except sqlite3.OperationalError:
        return set()


def _is_historical_need(r: dict) -> bool:
    """Is this library row something a PAST close needed? The marks (SPOT, FWD_OUTRIGHT,
    FUTURE_PX) less a forward at an option's expiry, plus (2026-09-22) an FX option's OIS
    curve and vol smile (HISTORY_INPUT_KINDS): what the backfill asks Bloomberg's history
    for."""
    if r["kind"] in HISTORY_INPUT_KINDS:
        return r["product"] in HISTORY_INPUT_PRODUCTS
    return r["kind"] in MARK_KINDS and not (r["kind"] == "FWD_OUTRIGHT" and r["product"] == "FX_OPTION")


def needed_on(conn: sqlite3.Connection, as_of: str, historical: bool = False,
              include_unrequestable: bool = False) -> List[dict]:
    """The library rows in force on `as_of` (needed_from <= as_of <= needed_until), one
    per trade. Live (the default): an FX option the ledger has realised is left out.
    `historical=True` is what a PAST close needed (`_is_historical_need`): the marks
    (SPOT, FWD_OUTRIGHT, FUTURE_PX), no forward at an option's expiry, plus the OIS
    curve and vol smile an FX option needs that day (2026-09-22), and
    realised or not -- the expiry-day catch-up needs the expiry date's closing SPOT for an
    option already frozen (data.bloomberg.live.option_needed_marks says why).

    2026-09-24: a row that is not requestable (no ticker to ask for, `unrequestable_reason`)
    is left out unless `include_unrequestable` -- the listings that show the gap ask for it,
    the pull and the backfill never do; and a CONTRACT_DATES row whose contract already has
    Bloomberg's dates on file is met and left out of the live list."""
    realised = set() if historical else _realised_ids(conn)
    out = []
    for r in rows(conn):
        if not (r["needed_from"] <= as_of <= r["needed_until"]):
            continue
        if not include_unrequestable and not r["requestable"]:
            continue
        if historical:
            if not _is_historical_need(r):
                continue
        elif r["product"] in _REALISED_FILTER_PRODUCTS and r["trade_id"] in realised:
            continue
        out.append(r)
    met = _contract_dates_on_file(conn, [r["key"] for r in out if r["kind"] == CONTRACT_DATES])
    return [r for r in out if not (r["kind"] == CONTRACT_DATES and r["key"] in met)] if met else out


def needed_in_range(conn: sqlite3.Connection, start: str, end: str,
                    include_unrequestable: bool = False) -> List[dict]:
    """`needed_on(historical=True)` for a span: every row a past close needs
    (`_is_historical_need`: the marks, and since 2026-09-22 the OIS_CURVE / VOL_SMILE rows
    of the FX options) in force on at least one day of [start, end]. What the
    backfill asks Bloomberg's history for; readers filter by `kind`. A row that is not
    requestable is left out unless `include_unrequestable` (2026-09-24)."""
    return [r for r in rows(conn)
            if r["needed_from"] <= end and r["needed_until"] >= start and _is_historical_need(r)
            and (include_unrequestable or r["requestable"])]


def contract_dates_needed(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """The commodity futures whose Bloomberg contract dates (CONTRACT_DATES_FIELDS) today's
    pull on `as_of` asks for (2026-09-24): open, of a contract root, with a verified ticker
    and no dates on file yet. [{contract_id, bbg_ticker, fields, needed_until, trades}],
    one per contract, sorted. What bbg-live requests, and nothing else of this kind."""
    found: Dict[str, dict] = {}
    for r in needed_on(conn, as_of):
        if r["kind"] != CONTRACT_DATES:
            continue
        entry = found.setdefault(r["key"], {"contract_id": r["key"], "bbg_ticker": r["bbg_ticker"],
                                            "fields": CONTRACT_DATES_FIELDS, "needed_until": r["needed_until"],
                                            "trade_ids": set()})
        entry["trade_ids"].add(r["trade_id"])
        entry["needed_until"] = max(entry["needed_until"], r["needed_until"])
    out = []
    for key in sorted(found):
        entry = found[key]
        out.append({**{k: v for k, v in entry.items() if k != "trade_ids"}, "trades": len(entry["trade_ids"])})
    return out


def history_inputs_needed(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """The OIS curves and vol smiles a past close on `as_of` prices its FX options from: [{kind: OIS_CURVE | VOL_SMILE, key: currency | pair}], sorted, one per key. Only
    a currency with an OIS curve in scope (rates_marketdata.OIS_INDEX) is listed: SEK has
    none to ask for, and its option's rate is implied from the pair's forward curve."""
    try:
        from data.bloomberg.rates_marketdata import OIS_INDEX
    except Exception:  # noqa: BLE001 -- no rates layer importable: no curve can be asked for
        OIS_INDEX = {}
    found = set()
    for r in needed_on(conn, as_of, historical=True):
        if r["kind"] == "VOL_SMILE" or (r["kind"] == "OIS_CURVE" and r["key"] in OIS_INDEX):
            found.add((r["kind"], r["key"]))
    return [{"kind": kind, "key": key} for kind, key in sorted(found)]


def keys(conn: sqlite3.Connection, as_of: str, kind: str) -> List[str]:
    """Sorted distinct `key`s of one kind in force on `as_of` (live): the currencies whose
    OIS curve the pull asks for, the pairs whose vol smile it asks for. A retired kind
    (FIXINGS, DIV_YIELD, 2026-09-24) has none."""
    return sorted({r["key"] for r in needed_on(conn, as_of) if r["kind"] == kind})


def tickers(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """The Bloomberg securities a pull on `as_of` asks for, one row per (ticker, field):
    {ticker, field, used_for, trades, needed_until, added_at}. A curve or a smile is
    expanded into its own securities; a currency with no OIS curve in scope has none (its
    option rate is implied from the pair's forward curve, engine/options/rates.py).

    2026-09-24: every entry carries `requestable` and `reason`. A row with no ticker to ask
    for (a future of a placeholder root) is listed too, one entry per contract with ticker
    '', `requestable` False and its reason in `used_for`, so the gap is in sight; no pull
    reads this listing."""
    found: Dict[tuple, dict] = {}

    def put(ticker: str, field: str, used_for: str, r: dict) -> None:
        group = (ticker, field) if r["requestable"] else ("", field, r["key"])
        if not r["requestable"]:
            used_for = f"{used_for} -- not asked of Bloomberg: {r['reason']}"
        entry = found.setdefault(group, {"ticker": ticker, "field": field, "used_for": used_for,
                                         "trade_ids": set(), "needed_until": r["needed_until"],
                                         "added_at": r["added_at"], "requestable": r["requestable"],
                                         "reason": r["reason"]})
        entry["trade_ids"].add(r["trade_id"])
        entry["needed_until"] = max(entry["needed_until"], r["needed_until"])
        entry["added_at"] = min(entry["added_at"], r["added_at"])

    for r in needed_on(conn, as_of, include_unrequestable=True):
        kind, key = r["kind"], r["key"]
        if kind == "SPOT":
            put(r["bbg_ticker"], "PX_LAST", f"{key} spot" + (" (USD conversion)" if r["role"] == ROLE_CONVERSION else ""), r)
        elif kind == "FWD_OUTRIGHT":
            if r["settle_date"] > as_of:     # a leg settling today is marked at spot: no curve request
                put(r["bbg_ticker"], "FWD_CURVE", f"{key} forward curve", r)
        elif kind == "FUTURE_PX":
            if r["product"] == "EQ_OPTION":
                put(r["bbg_ticker"], "PX_MID", f"{key} listed option price", r)
            else:
                put(r["bbg_ticker"], "PX_LAST", f"{key} futures price", r)
        elif kind == CONTRACT_DATES:
            put(r["bbg_ticker"], ", ".join(CONTRACT_DATES_FIELDS), f"{key} contract dates (expiry and first notice)", r)
        elif kind == "OIS_CURVE":
            try:
                from data.bloomberg import rates_marketdata as rm
                for spec in rm.ois_curve(key):
                    put(spec.ticker, spec.field, f"{key} OIS curve {spec.tenor}", r)
            except Exception:  # noqa: BLE001 -- no curve in scope for this currency: nothing is asked for
                continue
        elif kind == "VOL_SMILE":
            from data.bloomberg import vol_marketdata as vm
            for tenor in vm.VOL_TENORS:
                for quote_type in vm.VOL_QUOTE_TYPES:
                    put(vm.vol_ticker(key, tenor, quote_type), vm.VOL_FIELD, f"{key} vol {tenor} {quote_type}", r)
    out = []
    for entry in found.values():
        ids = entry.pop("trade_ids")
        out.append({**entry, "trades": len(ids)})
    return sorted(out, key=lambda e: (e["used_for"], e["ticker"]))


def summary(conn: sqlite3.Connection, as_of: Optional[str] = None) -> dict:
    """{tickers, trades, synced_at, not_requestable} for a status line: how many Bloomberg
    securities the book needs on `as_of` (default: today's book date), for how many trades,
    and when the library last changed; `not_requestable` (2026-09-24) counts the listed
    needs with no ticker to ask for, which `tickers` leaves out of the count."""
    if as_of is None:
        from data.bloomberg.live import book_today
        as_of = book_today().isoformat()
    needed = needed_on(conn, as_of)
    try:
        row = conn.execute("SELECT synced_at FROM bbg_library_state WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        row = None
    listed = tickers(conn, as_of)
    return {"tickers": sum(1 for t in listed if t["requestable"]), "trades": len({r["trade_id"] for r in needed}),
            "synced_at": row[0] if row else "", "not_requestable": sum(1 for t in listed if not t["requestable"])}
