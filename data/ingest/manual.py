"""Manual booking of OTC trades the blotter export does not carry (user request
2026-09-18: "need a place to manually input OTC products - like the options that show
up red").

Two kinds of gap this closes, both seen on the reference data:
  - an option the export lists but whose terms it leaves blank (three digitals with no
    STRIKE clause) -- those keep their blotter trade and get their terms typed through
    `engine.options.store.set_option_terms` (the Blotter's "Option terms" editor); this
    module does not duplicate that.
  - a trade the export does not list at all (the old Excel's "USDJPY digi p 147"
    dated 2026-09-01 and "USDZAR c 16.35" dated 2026-09-09 have no blotter row) -- those
    are booked here, as `trades.source = 'MANUAL'` (the third value CLAUDE.md's schema
    reserves for exactly this), with the same instrument / trade / leg shape the blotter
    parser writes for the same product, so every engine query (value_book, the ladder,
    the options pricer, the live mark requests) sees them like any other trade.

Rules:
  - Trade ids are app-generated `MANUAL-<n>`; an option gets its own instrument
    `<PAIR><mmddyy><C|P>-MANUAL<n>` (the blotter's `OPTION_SYMBOL_RE` shape, one
    instrument per option trade, exactly as the export does), a forward uses the plain
    pair instrument. The plain pair instrument row is created if absent so a SPOT mark
    for it can be written (data.bloomberg.live.write_marks drops rows for unknown
    instruments).
  - A blotter re-upload never removes a MANUAL trade (`data/ingest/upload.py`'s full
    replace skips `source = 'MANUAL'`); `delete_manual_trade` is the only way out.
  - Validation is the same "only a contradiction rejects" rule as the parser, applied to
    typed input: a value that cannot be a trade (zero notional, expiry before trade
    date, a strike of 0 on a strike payoff) raises ValueError with a plain sentence;
    formatting variation (pair typed as "eur/sek", dates as 25/11/2026) is accepted.
  - Premium convention matches `trades.price` for blotter options: a fraction of the
    base notional (0.121 on 1,000,000 EUR = 121,000 EUR), see docs/open-questions.md
    item 61d.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from typing import Dict, List, Optional, Union

from data.ingest.common import NDF_CCYS, PERPETUAL

SOURCE = "MANUAL"
TRADE_ID_PREFIX = "MANUAL-"

# Same payoff vocabulary as engine/options/store.py (VALID_PAYOFFS) -- repeated here so
# this ingest module never imports the pricer; `book_fx_option` re-checks against the
# pricer's own list at call time when it is importable.
PAYOFFS = ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH")
STRIKE_PAYOFFS = ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO")
BARRIER_PAYOFFS = ("BARRIER_KI", "BARRIER_KO", "ONE_TOUCH", "NO_TOUCH")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d", "%d %b %Y", "%d %B %Y", "%Y/%m/%d")
# Explicit column lists (never positional VALUES): a database created by a transient
# schema variant carries extra `instruments` columns, and a positional insert of the
# contract's 8 values fails on it (found on the dev database 2026-09-18).
_INSTRUMENT_COLS = "instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, expiry_date"
_LEG_COLS = "trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, settles_cash"
_TRADE_ID_RE = re.compile(r"^MANUAL-(\d+)$")
_INSTRUMENT_N_RE = re.compile(r"-MANUAL(\d+)$")


# --------------------------------------------------------------------------- input normalisation
def _pair(value) -> str:
    """'eur/sek', ' EURSEK ', 'EUR-SEK' -> 'EURSEK'. Six letters, two different currencies."""
    letters = re.sub(r"[^A-Za-z]", "", str(value or "")).upper()
    if len(letters) != 6:
        raise ValueError(f"pair must be six letters like USDJPY, got {value!r}")
    if letters[:3] == letters[3:]:
        raise ValueError(f"pair {letters} names the same currency twice")
    return letters


def _iso(value, what: str) -> str:
    """A date typed in any common form (ISO, 25/11/2026, 20261125, 25 Nov 2026) or a
    date/datetime object -> 'YYYY-MM-DD'."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{what} is required")
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"{what} {text!r} is not a date (use YYYY-MM-DD)")


def _side(value) -> str:
    text = str(value or "").strip().upper()
    if text in ("BUY", "B", "LONG", "+"):
        return "BUY"
    if text in ("SELL", "S", "SHORT", "-"):
        return "SELL"
    raise ValueError(f"side must be Buy or Sell, got {value!r}")


def _option_type(value) -> str:
    text = str(value or "").strip().upper()
    if text in ("CALL", "C"):
        return "CALL"
    if text in ("PUT", "P"):
        return "PUT"
    raise ValueError(f"option type must be Call or Put, got {value!r}")


def _number(value, what: str, *, positive: bool = True, allow_zero: bool = False) -> float:
    try:
        number = float(str(value).replace(",", "").strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number, got {value!r}") from None
    if number != number:
        raise ValueError(f"{what} must be a number")
    if positive and (number < 0 or (number == 0 and not allow_zero)):
        raise ValueError(f"{what} must be greater than 0, got {number}")
    return number


def _today_ny() -> str:
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _text(value) -> str:
    return str(value or "").strip()


# --------------------------------------------------------------------------- ids and instruments
def _next_number(conn: sqlite3.Connection) -> int:
    """1 + the highest number still in use by a MANUAL trade id or a `-MANUAL<n>` option
    instrument. A deleted trade's number can come round again only once nothing on file
    carries it any more (its legs, realised row, instrument and marks go with it)."""
    used = 0
    for (trade_id,) in conn.execute("SELECT trade_id FROM trades WHERE trade_id LIKE 'MANUAL-%'"):
        m = _TRADE_ID_RE.match(trade_id)
        if m:
            used = max(used, int(m.group(1)))
    for (instrument_id,) in conn.execute("SELECT instrument_id FROM instruments WHERE instrument_id LIKE '%-MANUAL%'"):
        m = _INSTRUMENT_N_RE.search(instrument_id)
        if m:
            used = max(used, int(m.group(1)))
    return used + 1


def _ensure_pair_instrument(conn: sqlite3.Connection, pair: str) -> None:
    base, quote = pair[:3], pair[3:]
    is_ndf = 1 if (base in NDF_CCYS or quote in NDF_CCYS) else 0
    conn.execute(
        f"INSERT OR IGNORE INTO instruments ({_INSTRUMENT_COLS}) VALUES (?, 'FX', ?, ?, 1, ?, ?, ?)",
        (pair, base, quote, is_ndf, f"{pair} Curncy", PERPETUAL))


def _pair_theme(conn: sqlite3.Connection, pair: str) -> str:
    """The bundle the pair already belongs to (Blotter's Bundles sub-tab), so a manual
    trade joins its pair's bundle like a blotter trade in that pair would."""
    row = conn.execute("SELECT theme FROM instrument_theme WHERE instrument_id = ?", (pair,)).fetchone()
    return row[0] if row else ""


def _insert_trade(conn: sqlite3.Connection, *, trade_id: str, instrument_id: str, product: str,
                  trade_date: str, quantity: float, price: float, account: str, counterparty: str,
                  trader: str, description: str, theme: str) -> None:
    conn.execute(
        "INSERT INTO trades (trade_id, source, instrument_id, product, package_id, trade_date, quantity, price, "
        "account, counterparty, strategy, trader, description, theme) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, SOURCE, instrument_id, product, trade_id, trade_date, quantity, price,
         account, counterparty, "", trader, description, theme))


# --------------------------------------------------------------------------- booking
def book_fx_option(conn: sqlite3.Connection, *, pair, side, option_type, expiry, notional, premium,
                   strike=0.0, payoff: str = "VANILLA", barrier_level=0.0, trade_date=None,
                   counterparty: str = "", account: str = "", trader: str = "",
                   description: str = "") -> str:
    """Book an FX option by hand. Returns the new trade id.

    `notional` is the base-currency amount (always positive; `side` gives the sign:
    Buy = long = +quantity, like the blotter's `Side`), `premium` the fill as a fraction
    of that notional (blotter `Price` convention), `strike`/`barrier_level` in quote
    currency per base unit, `payoff` one of PAYOFFS. Writes the option instrument, its
    `instrument_options` terms, the trade and its single NOTIONAL leg (settles_cash 0,
    settle_date = expiry), exactly the shape `data/ingest/blotter.py::_parse_option`
    writes. Raises ValueError with a plain sentence on an impossible trade."""
    pair = _pair(pair)
    side = _side(side)
    option_type = _option_type(option_type)
    payoff = _text(payoff).upper() or "VANILLA"
    try:
        from engine.options.store import VALID_PAYOFFS as _valid
    except Exception:  # the pricer is optional for booking; keep the local list
        _valid = PAYOFFS
    if payoff not in _valid:
        raise ValueError(f"payoff must be one of {', '.join(_valid)}, got {payoff!r}")
    trade_date = _iso(trade_date, "trade date") if trade_date else _today_ny()
    expiry = _iso(expiry, "expiry")
    if expiry <= trade_date:
        raise ValueError(f"expiry {expiry} must be after the trade date {trade_date}")
    notional = _number(notional, "notional")
    premium = _number(premium, "premium", allow_zero=True)
    strike = _number(strike, "strike", allow_zero=True) if strike not in (None, "") else 0.0
    barrier_level = _number(barrier_level, "barrier / touch level", allow_zero=True) if barrier_level not in (None, "") else 0.0
    if payoff in STRIKE_PAYOFFS and strike <= 0:
        raise ValueError(f"a {payoff.lower()} option needs a strike greater than 0")
    if payoff in BARRIER_PAYOFFS and barrier_level <= 0:
        raise ValueError(f"a {payoff.lower().replace('_', ' ')} option needs a barrier / touch level greater than 0")

    base, quote = pair[:3], pair[3:]
    quantity = notional if side == "BUY" else -notional
    with conn:
        n = _next_number(conn)
        trade_id = f"{TRADE_ID_PREFIX}{n}"
        mmddyy = dt.date.fromisoformat(expiry).strftime("%m%d%y")
        instrument_id = f"{pair}{mmddyy}{option_type[0]}-MANUAL{n}"
        _ensure_pair_instrument(conn, pair)
        conn.execute(
            f"INSERT INTO instruments ({_INSTRUMENT_COLS}) VALUES (?, 'FX_OPTION', ?, ?, 1, 0, ?, ?)",
            (instrument_id, base, quote, instrument_id, expiry))
        conn.execute(
            "INSERT INTO instrument_options (instrument_id, strike, option_type, barrier_level, avg_start_date, payoff) "
            "VALUES (?, ?, ?, ?, '9999-12-31', ?)",
            (instrument_id, strike, option_type, barrier_level, payoff))
        if not description:
            words = f"{pair} {payoff.lower().replace('_', ' ')} {base} {option_type.lower()}"
            description = (f"MANUAL {side} {words}" + (f" strike {strike:g}" if strike else "")
                           + (f" barrier {barrier_level:g}" if barrier_level else "") + f" exp {expiry}")
        _insert_trade(conn, trade_id=trade_id, instrument_id=instrument_id, product="FX_OPTION",
                      trade_date=trade_date, quantity=quantity, price=premium, account=_text(account),
                      counterparty=_text(counterparty), trader=_text(trader), description=description,
                      theme=_pair_theme(conn, pair))
        conn.execute(
            f"INSERT INTO trade_legs ({_LEG_COLS}) VALUES (?, 1, 'NOTIONAL', ?, ?, ?, ?, ?, 0)",
            (trade_id, base, quantity, trade_date, expiry, premium))
    return trade_id


def book_fx_forward(conn: sqlite3.Connection, *, pair, side, base_amount, rate, value_date, trade_date=None,
                    counterparty: str = "", account: str = "", trader: str = "",
                    description: str = "") -> str:
    """Book an FX forward (or spot) by hand: `side` Buy/Sell of the BASE currency,
    `base_amount` positive, `rate` the outright fill (quote per base). Two FX_NEAR legs
    like `data/ingest/blotter.py::_parse_forward` (base leg = signed base amount, quote
    leg = the opposite sign x rate; NDF currencies settle no cash). Returns the trade id.
    A manual forward pairs into an FX_SWAP package with another manual forward under
    CLAUDE.md's package rule (same source), never with a blotter trade."""
    pair = _pair(pair)
    side = _side(side)
    trade_date = _iso(trade_date, "trade date") if trade_date else _today_ny()
    value_date = _iso(value_date, "value date")
    if value_date < trade_date:
        raise ValueError(f"value date {value_date} is before the trade date {trade_date}")
    base_amount = _number(base_amount, "amount")
    rate = _number(rate, "rate")
    base, quote = pair[:3], pair[3:]
    quantity = base_amount if side == "BUY" else -base_amount
    is_ndf = 1 if (base in NDF_CCYS or quote in NDF_CCYS) else 0
    settles_cash = 0 if is_ndf else 1
    with conn:
        n = _next_number(conn)
        trade_id = f"{TRADE_ID_PREFIX}{n}"
        _ensure_pair_instrument(conn, pair)
        if not description:
            verb_base, verb_quote = ("BUY", "SELL") if side == "BUY" else ("SELL", "BUY")
            description = f"MANUAL TD {trade_date} VD {value_date} {verb_base} {base} VS {verb_quote} {quote} @ {rate:g}"
        _insert_trade(conn, trade_id=trade_id, instrument_id=pair, product="FX_FWD", trade_date=trade_date,
                      quantity=quantity, price=rate, account=_text(account), counterparty=_text(counterparty),
                      trader=_text(trader), description=description, theme=_pair_theme(conn, pair))
        conn.execute(f"INSERT INTO trade_legs ({_LEG_COLS}) VALUES (?, 1, 'FX_NEAR', ?, ?, ?, ?, ?, ?)",
                     (trade_id, base, quantity, trade_date, value_date, rate, settles_cash))
        conn.execute(f"INSERT INTO trade_legs ({_LEG_COLS}) VALUES (?, 2, 'FX_NEAR', ?, ?, ?, ?, ?, ?)",
                     (trade_id, quote, -quantity * rate, trade_date, value_date, rate, settles_cash))
    return trade_id


# --------------------------------------------------------------------------- listing and removal
def manual_trades(conn: sqlite3.Connection) -> List[Dict[str, object]]:
    """Every MANUAL trade on file, newest first, with the settle/expiry date of its
    first leg and its option terms when it is an option."""
    rows = conn.execute(
        """
        SELECT t.trade_id, t.product, t.instrument_id, t.trade_date, t.quantity, t.price,
               t.counterparty, t.description, t.package_id,
               (SELECT settle_date FROM trade_legs l WHERE l.trade_id = t.trade_id AND l.leg_no = 1) AS settle_date,
               COALESCE(o.strike, 0), COALESCE(o.option_type, ''), COALESCE(o.payoff, ''), COALESCE(o.barrier_level, 0)
        FROM trades t LEFT JOIN instrument_options o USING (instrument_id)
        WHERE t.source = ?
        ORDER BY t.trade_date DESC, t.trade_id DESC
        """, (SOURCE,)).fetchall()
    out = []
    for r in rows:
        out.append({"trade_id": r[0], "product": r[1], "instrument_id": r[2], "trade_date": r[3],
                    "quantity": r[4], "price": r[5], "counterparty": r[6], "description": r[7],
                    "package_id": r[8], "settle_date": r[9] or "", "strike": r[10], "option_type": r[11],
                    "payoff": r[12], "barrier_level": r[13]})
    return out


def delete_manual_trade(conn: sqlite3.Connection, trade_id: str) -> None:
    """Remove one MANUAL trade with everything keyed to it (legs, realised row, swap
    review row); an option's own instrument, terms and marks go with it once no other
    trade references it. A swap package it was part of is dissolved back into outright
    forwards (the same rule `blotter.load` applies when a packaged trade is replaced).
    Refuses (ValueError) anything that is not a MANUAL trade -- blotter trades are only
    ever replaced by the next upload."""
    row = conn.execute("SELECT source, instrument_id, package_id FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    if row is None:
        raise ValueError(f"no trade {trade_id!r} on file")
    source, instrument_id, package_id = row
    if source != SOURCE:
        raise ValueError(f"{trade_id} was loaded from the blotter (source {source}); only MANUAL trades can be deleted here")
    with conn:
        if package_id and package_id != trade_id:
            conn.execute("UPDATE trades SET product = 'FX_FWD', package_id = trade_id WHERE package_id = ?", (package_id,))
        conn.execute("DELETE FROM realised_pnl WHERE trade_id = ?", (trade_id,))
        conn.execute("DELETE FROM swap_review WHERE trade_id = ?", (trade_id,))
        conn.execute("DELETE FROM trade_legs WHERE trade_id = ?", (trade_id,))
        conn.execute("DELETE FROM trades WHERE trade_id = ?", (trade_id,))
        asset_class = conn.execute("SELECT asset_class FROM instruments WHERE instrument_id = ?", (instrument_id,)).fetchone()
        still_used = conn.execute("SELECT 1 FROM trades WHERE instrument_id = ? LIMIT 1", (instrument_id,)).fetchone()
        if asset_class and asset_class[0] == "FX_OPTION" and not still_used:
            conn.execute("DELETE FROM marks WHERE instrument_id = ?", (instrument_id,))
            conn.execute("DELETE FROM instrument_options WHERE instrument_id = ?", (instrument_id,))
            conn.execute("DELETE FROM instruments WHERE instrument_id = ?", (instrument_id,))
