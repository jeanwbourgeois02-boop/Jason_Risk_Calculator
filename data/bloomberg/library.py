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
  * FX option, until expiry: the pair's SPOT, the SPOT of its USD-conversion pairs, and --
    today only, nothing prices an option on a past date -- a FWD_OUTRIGHT at the expiry,
    the OIS curve of both currencies and the pair's vol smile.
  * IRS, until maturity and today only: the currency's OIS curve and overnight fixings.
  * NDF currencies (user decision 2026-09-21: "NDFs - always show 1m forward date price,
    not spot ... based off the monthly not the spot"): every FX spot / forward / swap and
    every FX option with a currency in data.ingest.common.NDF_1M_TICKERS (KRW, IDR, INR,
    TWD, BRL) also needs that currency's 1M NDF outright -- kind NDF_1M, key = the
    currency's USD pair ('USDKRW', for a cross such as EURKRW too), bbg_ticker = the
    user's own ticker ('KWN+1M Curncy'), from trade date until the last leg settles (the
    expiry for an option). Today's pull only: no P&L query reads it (the ladder does), so
    the backfill never asks for it.

  * Listed index option (EQ_OPTION, user decision 2026-09-21: "Bloomberg's option price"),
    until expiry: FUTURE_PX on the option's OWN Bloomberg ticker ('SPX US 10/16/26 P7615
    Index', `listed_option_ticker`) -- a listed option is priced exactly like a future,
    Bloomberg's own quote today and its settlement price on a past close -- which is all
    its P&L needs. For today's Greeks only: the SPOT of its underlying index ('SPX Index',
    role UNDERLYING, never asked of Bloomberg's history), the quote currency's OIS curve
    and the index's dividend yield (kind DIV_YIELD).

`kind` is the mark_type for what lands in `marks` (SPOT, FWD_OUTRIGHT, FUTURE_PX, and
NDF_1M) and OIS_CURVE / FIXINGS / VOL_SMILE / DIV_YIELD for what lands in `curve_quotes` /
`index_fixings` / `vol_quotes` / `equity_dividend_yields`; the first three stand for a set
of Bloomberg securities (`tickers` lists them).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

SENTINEL = "9999-12-31"
ROLE_PAIR = "PAIR"                 # the traded pair / contract / currency itself
ROLE_CONVERSION = "CONVERSION"     # a USD-conversion pair's SPOT
ROLE_UNDERLYING = "UNDERLYING"     # a listed option's underlying index level: today's Greeks only
MARK_KINDS = ("SPOT", "FWD_OUTRIGHT", "FUTURE_PX")
NDF_1M = "NDF_1M"                  # an NDF currency's 1M outright, on its USD pair (the ladder's rate)
DIV_YIELD = "DIV_YIELD"            # an index's dividend yield, for a listed option's Greeks
DIV_YIELD_FIELDS = ("IDX_EST_DVD_YLD", "EQY_DVD_YLD_12M")   # per cent; the first Bloomberg answers
# Asked for by today's pull only, never of Bloomberg's history: nothing prices a past date
# off them. NDF_1M lands in `marks` like the MARK_KINDS do, but no P&L query reads it, so
# it is kept out of MARK_KINDS -- which is what a past close needs and the backfill fills.
LIVE_ONLY_KINDS = ("OIS_CURVE", "FIXINGS", "VOL_SMILE", NDF_1M, DIV_YIELD)
# Products whose rows stop being asked for once the ledger has realised the trade (the
# pull's own rule for options and swaps; an FX leg or a future simply runs to its date).
_REALISED_FILTER_PRODUCTS = ("FX_OPTION", "IRS")
COLUMNS = ("trade_id", "kind", "key", "settle_date", "bbg_ticker", "role", "product",
           "needed_from", "needed_until", "added_at")

_FX_LEGS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.instrument_id, i.bbg_ticker, i.base_ccy, i.quote_ccy, l.settle_date
FROM trade_legs l JOIN trades_official t USING (trade_id) JOIN instruments i USING (instrument_id)
WHERE i.asset_class = 'FX'
ORDER BY i.instrument_id, l.settle_date, t.trade_id
"""

_FUTURE_LEGS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.instrument_id, i.bbg_ticker, l.settle_date
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

_IRS_SQL = """
SELECT t.trade_id, t.product, t.trade_date, i.base_ccy, COALESCE(MAX(l.settle_date), '9999-12-31')
FROM trades_official t JOIN instruments i USING (instrument_id) LEFT JOIN trade_legs l USING (trade_id)
WHERE t.product = 'IRS'
GROUP BY t.trade_id
ORDER BY i.base_ccy, t.trade_id
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

    def add_ndf_1m(trade_id, product, ccys, needed_from, needed_until) -> None:
        # The 1M NDF outright of each NDF currency, on that currency's USD pair (a cross
        # such as EURKRW is keyed USDKRW as well): `add` keeps the latest needed_until, so
        # it runs until the trade's last leg settles.
        for ccy in ccys:
            if ccy in ndf_tickers:
                instrument_id, _ticker = _pair_row(conn, _usd_pair_name(ccy))
                add(trade_id, product, NDF_1M, instrument_id, SENTINEL, ndf_tickers[ccy], needed_from, needed_until)

    try:
        from data.ingest.common import NDF_1M_TICKERS as ndf_tickers
    except ImportError:            # an older data/ingest: no NDF list, nothing extra is asked for
        ndf_tickers = {}

    for trade_id, product, trade_date, instrument_id, ticker, base, quote, settle in conn.execute(_FX_LEGS_SQL):
        add(trade_id, product, "SPOT", instrument_id, SENTINEL, ticker, trade_date, settle)
        add(trade_id, product, "FWD_OUTRIGHT", instrument_id, settle, ticker, trade_date, settle)
        if base != "USD" and quote != "USD":
            add_conversions(trade_id, product, (base, quote), trade_date, settle)
        add_ndf_1m(trade_id, product, (base, quote), trade_date, settle)
    for trade_id, product, trade_date, instrument_id, ticker, settle in conn.execute(_FUTURE_LEGS_SQL):
        add(trade_id, product, "FUTURE_PX", instrument_id, settle, ticker, trade_date, settle)
    for trade_id, product, trade_date, base, quote, expiry in conn.execute(_OPTIONS_SQL):
        pair = f"{base}{quote}"
        instrument_id, ticker = _pair_row(conn, pair)
        add(trade_id, product, "SPOT", instrument_id, SENTINEL, ticker, trade_date, expiry)
        add(trade_id, product, "FWD_OUTRIGHT", instrument_id, expiry, ticker, trade_date, expiry)
        add_conversions(trade_id, product, (base, quote), trade_date, expiry)
        for ccy in (base, quote):
            add(trade_id, product, "OIS_CURVE", ccy, SENTINEL, "", trade_date, expiry)
        add(trade_id, product, "VOL_SMILE", pair, SENTINEL, "", trade_date, expiry)
        add_ndf_1m(trade_id, product, (base, quote), trade_date, expiry)
    for (trade_id, product, trade_date, instrument_id, root, quote, underlying, expiry,
         option_type, strike) in conn.execute(_LISTED_OPTIONS_SQL):
        ticker = listed_option_ticker(root, expiry, option_type, strike)
        if ticker:
            add(trade_id, product, "FUTURE_PX", instrument_id, expiry, ticker, trade_date, expiry)
        if underlying:       # instruments.bbg_ticker of a listed option names its underlying ('SPX Index')
            add(trade_id, product, "SPOT", underlying, SENTINEL, underlying, trade_date, expiry, role=ROLE_UNDERLYING)
            add(trade_id, product, DIV_YIELD, underlying, SENTINEL, underlying, trade_date, expiry)
        add(trade_id, product, "OIS_CURVE", quote, SENTINEL, "", trade_date, expiry)
    for trade_id, product, trade_date, ccy, maturity in conn.execute(_IRS_SQL):
        add(trade_id, product, "OIS_CURVE", ccy, SENTINEL, "", trade_date, maturity)
        add(trade_id, product, "FIXINGS", ccy, SENTINEL, "", trade_date, maturity)
    return list(rows.values())


def is_out_of_date(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT dirty FROM bbg_library_state WHERE id = 1").fetchone()
    return row is None or bool(row[0])


def sync(conn: sqlite3.Connection) -> dict:
    """Bring `bbg_library` into line with the trades on file: rows a trade newly calls for
    are added (their `added_at` is now), rows of trades that are gone are removed, a row
    whose dates moved is updated, everything else is left as it was. Also creates the
    plain pair `instruments` row a conversion pair or an option's pair needs before a
    mark can be written for it. Returns {added, removed, total}."""
    from data.bloomberg.live import _ensure_fx_instruments, _ensure_index_instruments
    wanted = {(r["trade_id"], r["kind"], r["key"], r["settle_date"]): r for r in compute(conn)}
    _ensure_fx_instruments(conn, [r["key"] for r in wanted.values() if r["kind"] in ("SPOT", "FWD_OUTRIGHT", NDF_1M)])
    _ensure_index_instruments(conn, [r["key"] for r in wanted.values() if r["role"] == ROLE_UNDERLYING])
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
        conn.execute("INSERT OR REPLACE INTO bbg_library_state (id, dirty, synced_at) VALUES (1, 0, ?)", (now,))
    return {"added": added, "removed": removed, "total": len(wanted)}


def rows(conn: sqlite3.Connection) -> List[dict]:
    """The library, brought up to date first when the trades changed since it was last
    written. On a connection that cannot write (every tab callback reads through a
    read-only handle) or a database without the table, the same rows are worked out in
    memory instead, so a reader is never shown a library that is behind the book."""
    try:
        if is_out_of_date(conn):
            sync(conn)
        found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM bbg_library").fetchall()
        return [dict(zip(COLUMNS, r)) for r in found]
    except sqlite3.OperationalError:
        return [{**r, "added_at": ""} for r in compute(conn)]


def _realised_ids(conn: sqlite3.Connection) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT trade_id FROM realised_pnl")}
    except sqlite3.OperationalError:
        return set()


def needed_on(conn: sqlite3.Connection, as_of: str, historical: bool = False) -> List[dict]:
    """The library rows in force on `as_of` (needed_from <= as_of <= needed_until), one
    per trade. Live (the default): an option or a swap the ledger has realised is left
    out. `historical=True` is what a PAST close needed: marks only (SPOT, FWD_OUTRIGHT,
    FUTURE_PX), no forward at an option's expiry, and realised or not -- the expiry-day
    catch-up needs the expiry date's closing SPOT for an option already frozen
    (data.bloomberg.live.option_needed_marks says why)."""
    realised = set() if historical else _realised_ids(conn)
    out = []
    for r in rows(conn):
        if not (r["needed_from"] <= as_of <= r["needed_until"]):
            continue
        if historical:
            if r["kind"] not in MARK_KINDS or (r["kind"] == "FWD_OUTRIGHT" and r["product"] == "FX_OPTION") \
                    or r["role"] == ROLE_UNDERLYING:
                continue
        elif r["product"] in _REALISED_FILTER_PRODUCTS and r["trade_id"] in realised:
            continue
        out.append(r)
    return out


def needed_in_range(conn: sqlite3.Connection, start: str, end: str) -> List[dict]:
    """`needed_on(historical=True)` for a span: every marks row in force on at least one
    day of [start, end]. What the backfill asks Bloomberg's history for."""
    return [r for r in rows(conn)
            if r["needed_from"] <= end and r["needed_until"] >= start and r["kind"] in MARK_KINDS
            and not (r["kind"] == "FWD_OUTRIGHT" and r["product"] == "FX_OPTION")
            and r["role"] != ROLE_UNDERLYING]


def keys(conn: sqlite3.Connection, as_of: str, kind: str) -> List[str]:
    """Sorted distinct `key`s of one kind in force on `as_of` (live): the currencies whose
    OIS curve or fixings the pull asks for, the pairs whose vol smile it asks for."""
    return sorted({r["key"] for r in needed_on(conn, as_of) if r["kind"] == kind})


def tickers(conn: sqlite3.Connection, as_of: str) -> List[dict]:
    """The Bloomberg securities a pull on `as_of` asks for, one row per (ticker, field):
    {ticker, field, used_for, trades, needed_until, added_at}. A curve or a smile is
    expanded into its own securities; a currency with no OIS curve in scope has none (its
    option rate is implied from the pair's forward curve, engine/options/rates.py)."""
    found: Dict[tuple, dict] = {}

    def put(ticker: str, field: str, used_for: str, r: dict) -> None:
        entry = found.setdefault((ticker, field), {"ticker": ticker, "field": field, "used_for": used_for,
                                                   "trade_ids": set(), "needed_until": r["needed_until"],
                                                   "added_at": r["added_at"]})
        entry["trade_ids"].add(r["trade_id"])
        entry["needed_until"] = max(entry["needed_until"], r["needed_until"])
        entry["added_at"] = min(entry["added_at"], r["added_at"])

    for r in needed_on(conn, as_of):
        kind, key = r["kind"], r["key"]
        if kind == "SPOT":
            if r["role"] == ROLE_UNDERLYING:
                put(r["bbg_ticker"], "PX_LAST", f"{key} level, for the Greeks of the options on it", r)
            else:
                put(r["bbg_ticker"], "PX_LAST", f"{key} spot" + (" (USD conversion)" if r["role"] == ROLE_CONVERSION else ""), r)
        elif kind == "FWD_OUTRIGHT":
            if r["settle_date"] > as_of:     # a leg settling today is marked at spot: no curve request
                put(r["bbg_ticker"], "FWD_CURVE", f"{key} forward curve", r)
        elif kind == "FUTURE_PX":
            if r["product"] == "EQ_OPTION":
                put(r["bbg_ticker"], "PX_MID", f"{key} listed option price", r)
            else:
                put(r["bbg_ticker"], "PX_LAST", f"{key} futures price", r)
        elif kind == DIV_YIELD:
            put(r["bbg_ticker"], DIV_YIELD_FIELDS[0], f"{key} dividend yield, for the Greeks of the options on it", r)
        elif kind == NDF_1M:
            ccy = key[3:] if key.startswith("USD") else key[:3]
            put(r["bbg_ticker"], "PX_LAST", f"1M NDF price, the ladder's rate for {ccy}", r)
        elif kind in ("OIS_CURVE", "FIXINGS"):
            try:
                from data.bloomberg import rates_marketdata as rm
                if kind == "FIXINGS":
                    put(rm.ois_fixing_ticker(key), "PX_LAST", f"{key} overnight fixings", r)
                else:
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
    """{tickers, trades, synced_at} for a status line: how many Bloomberg securities the
    book needs on `as_of` (default: today's book date), for how many trades, and when the
    library last changed."""
    if as_of is None:
        from data.bloomberg.live import book_today
        as_of = book_today().isoformat()
    needed = needed_on(conn, as_of)
    try:
        row = conn.execute("SELECT synced_at FROM bbg_library_state WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        row = None
    return {"tickers": len(tickers(conn, as_of)), "trades": len({r["trade_id"] for r in needed}),
            "synced_at": row[0] if row else ""}
