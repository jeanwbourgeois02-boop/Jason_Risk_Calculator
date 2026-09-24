"""Options on commodity futures (product ``CMDTY_OPTION``): one listed option, its canonical id,
Bloomberg's request ticker and its dates.

Canonical id: Bloomberg's commodity option form with a two-digit year, ``'<root padded><M><YY>
<C|P> <strike> Comdty'``: ``'CLZ26C 70 Comdty'``, ``'C Z26P 450 Comdty'``. The strike is written
as Bloomberg writes it, in the root's quoted scale (cents for CBOT corn), no trailing zeros.
Bloomberg's live form has a one-digit year (``option_request_ticker``: ``'CLZ6C 70 Comdty'``).

Dates, in this order:

1. Bloomberg's own date for the option once stored (``static.store_static_dates``, same table
   and same function as the futures, keyed by the canonical option id): ``dates_source``
   BLOOMBERG.
2. Else the expiry the prime-broker dated form states (``symbol_expiry``; ``option_for`` reads
   it back from the option's ``instruments.expiry_date``) when it is earlier than the
   underlying's last trade date: ``dates_source`` SYMBOL, not estimated (it is the broker's
   statement of the option's own expiry).
3. Else an ESTIMATE: the underlying future's last trade date (Bloomberg's when stored, else its
   own estimate, the last weekday of its month). An option on a future expires on or before that
   future's last trade, so the estimate is never earlier than the real date.

``dates_note`` says which date it was taken from, in words.

Style: from the symbol when it says (the prime-broker dated form's E / A), else the root's
``option_style`` in ``config/contracts.csv``; blank there is read as AMERICAN and
``style_source`` is ``'ASSUMED'``.

``resolve_option`` reads these forms (tolerant of case and spacing, hard rule 6):

- Bloomberg: ``'CLZ6C 70 Comdty'``, ``'CLZ26P 65.5 Comdty'``, ``'C Z6C 450 Comdty'``, the yellow
  key optional after a root of two or more characters (``'CLZ6C 70'``);
- the prime-broker listed-option form ``ROOT/[EA]yymmdd[CP]strike``, a ``-XXXX`` account suffix
  allowed: ``'CL/A261117C70-USAA'`` (E = European, A = American; yymmdd = the option's expiry);
- that dated form without ``[CP]strike`` (``'CL/A261117'``), or any futures form
  ``resolve_future`` reads (``'CLZ6'``, ``'CLZ6-USAA'``, ``'SHFE:CU2611'``), with the option type
  and strike passed in ``option_type`` / ``strike``;
- any of those behind an exchange prefix (``'NYMEX:CLZ6C 70 Comdty'``).

Roots are matched and narrowed exactly as ``resolve_future`` does (``resolve._pick_root``); an
unknown or ambiguous root raises ``UnknownContract`` / ``AmbiguousContract`` naming the
candidates. A type or strike in the symbol that contradicts the one passed in raises
``UnknownContract``; neither given raises too (a strike is never guessed; 0 means not known).

The underlying future. By default the future of the option's own contract month. When
``underlying`` names a future of the same root (``'CLZ6'``, ``'CLZ6 Comdty'``), that future is
the underlying instead (a serial option on a later future). For the dated form, whose date is
the option's expiry, not a contract month, the contract month is the expiry's month plus the
root's ``option_lead_months`` (1 for CME Group's usual options that expire in the month before:
CL Dec 2026 options expire in November; 0 for options expiring in their contract month; 2 for
ICE Brent); with that blank the candidate months are named in an ``AmbiguousContract``, never
guessed, unless ``underlying`` names the future. An expiry later than the underlying's last trade
date is a contradiction and raises ``UnknownContract``.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

from data.contracts.months import BLOOMBERG, ESTIMATED, ContractMonth, contract_month
from data.contracts.resolve import (
    _PB_SUFFIX_RE,
    _PREFIX_RE,
    AmbiguousContract,
    UnknownContract,
    _parse,
    _pick_root,
    _venue_set,
    resolve_future,
)
from data.contracts.static import static_dates
from data.contracts.tickers import (
    MONTH_CODES,
    cp_letter,
    expand_year,
    format_strike,
    make_contract_id,
    make_option_id,
    month_code,
    month_from_code,
    padded_root,
    parse_option_ticker,
    to_date,
)
from data.contracts.universe import ContractRoot, get_root

CALL, PUT = "CALL", "PUT"
AMERICAN, EUROPEAN = "AMERICAN", "EUROPEAN"
# where the style came from
STYLE_FROM_SYMBOL = "SYMBOL"
STYLE_FROM_CSV = "CONTRACTS_CSV"
STYLE_ASSUMED = "ASSUMED"
# dates_source of an option whose last trade date is the expiry its prime-broker symbol states
# (not an estimate, not Bloomberg's): used while no Bloomberg date is stored, and only when
# earlier than the underlying future's last trade date
SYMBOL = "SYMBOL"


@dataclass(frozen=True)
class OptionContract:
    """One listed option on a commodity future."""

    underlying: ContractMonth
    option_type: str          # 'CALL' | 'PUT'
    strike: float             # in the root's quoted scale, as the future's price
    style: str                # 'AMERICAN' | 'EUROPEAN'
    contract_id: str          # canonical: 'CLZ26C 70 Comdty'
    last_trade_date: date     # Bloomberg's when stored, else the symbol's, else no earlier than the real one
    dates_source: str         # 'BLOOMBERG' | 'SYMBOL' | 'ESTIMATED'
    month: int = 0            # the option's contract month (the underlying's unless serial)
    year: int = 0
    style_source: str = STYLE_FROM_CSV   # 'SYMBOL' | 'CONTRACTS_CSV' | 'ASSUMED' (blank in the CSV)
    symbol_expiry: Optional[date] = None  # the expiry the prime-broker dated form states, if any
    dates_note: str = ""      # where last_trade_date came from, in words

    @property
    def root(self) -> ContractRoot:
        return self.underlying.root

    @property
    def root_id(self) -> str:
        return self.underlying.root.root_id

    @property
    def month_code(self) -> str:
        return MONTH_CODES[self.month - 1]

    @property
    def estimated(self) -> bool:
        """True while the last trade date is the conservative estimate (neither Bloomberg's nor
        the symbol's own expiry)."""
        return self.dates_source == ESTIMATED


def _style_of(root: ContractRoot, symbol_style: Optional[str]):
    if symbol_style:
        return symbol_style, STYLE_FROM_SYMBOL
    if root.option_style:
        return root.option_style.upper(), STYLE_FROM_CSV
    return AMERICAN, STYLE_ASSUMED


def option_contract(root_id: str, month: int, year: int, option_type: str, strike, *,
                    style: Optional[str] = None, underlying: Optional[ContractMonth] = None,
                    symbol_expiry: Optional[date] = None,
                    conn: Optional[sqlite3.Connection] = None) -> OptionContract:
    """The option on ``root_id`` of contract month ``month`` / ``year`` (two-digit years read as
    20YY). ``style`` ('AMERICAN' / 'EUROPEAN') overrides the root's; ``underlying`` overrides the
    future of the same month (a serial option). With ``conn``, Bloomberg's stored dates win.
    Raises ValueError for a bad type or strike."""
    root = get_root(root_id)
    month, year = int(month), int(year)
    if year < 100:
        year += 2000
    und = underlying or contract_month(root.root_id, month, year, conn=conn)
    if und.root_id != root.root_id:
        raise ValueError(f"underlying {und.contract_id} is not a {root.root_id} future")
    cp = cp_letter(option_type)
    k = float(strike)
    if not math.isfinite(k):
        raise ValueError(f"strike {strike!r} is not a finite number")
    code = month_code(month)
    contract_id = make_option_id(root.bbg_root, code, year, cp, k, root.bbg_yellow_key)
    style_value, style_source = _style_of(root, style.upper() if style else None)
    if style_value not in (AMERICAN, EUROPEAN):
        raise ValueError(f"option style {style!r} is not AMERICAN or EUROPEAN")
    stored = static_dates(conn, contract_id) if conn is not None else None
    if stored is not None:
        ltd, source = to_date(stored["last_trade_date"]), BLOOMBERG
        note = f"Bloomberg's own option date ({stored['source']})"
    elif symbol_expiry is not None and symbol_expiry < und.last_trade_date:
        ltd, source = symbol_expiry, SYMBOL
        note = (f"the option's own expiry as the broker's symbol states it, earlier than the "
                f"underlying {und.contract_id}'s last trade date; Bloomberg's date not stored yet")
    else:
        ltd, source = und.last_trade_date, ESTIMATED
        whose = ("Bloomberg's" if not und.estimated
                 else "itself estimated as the last weekday of its month")
        note = (f"estimated: no later than the underlying {und.contract_id}'s last trade date "
                f"({whose}); an option expires on or before its future")
    return OptionContract(
        underlying=und, option_type=CALL if cp == "C" else PUT, strike=k, style=style_value,
        contract_id=contract_id, last_trade_date=ltd, dates_source=source, month=month, year=year,
        style_source=style_source, symbol_expiry=symbol_expiry, dates_note=note)


def option_request_ticker(option: OptionContract, as_of) -> str:
    """Bloomberg's live form while the option trades (``'CLZ6C 70 Comdty'``, ``'C Z6C 450
    Comdty'``), the canonical two-digit id once ``as_of`` is past its last trade date."""
    if to_date(as_of) <= option.last_trade_date:
        root = option.root
        cp = "C" if option.option_type == CALL else "P"
        return (f"{padded_root(root.bbg_root)}{option.month_code}{option.year % 10}{cp} "
                f"{format_strike(option.strike)} {root.bbg_yellow_key}")
    return option.contract_id


# --- reading symbols -----------------------------------------------------------------------

_BBG_OPT_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6}?)(?P<pad> ?)(?P<code>[FGHJKMNQUVXZ])(?P<year>\d{1,2})"
                         r"(?P<cp>[CP]) (?P<strike>-?\d+(?:\.\d+)?)(?: (?P<key>COMDTY|INDEX|CURNCY))?$")
_DATED_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6})\s*/\s*(?P<style>[EA])?(?P<ymd>\d{6})"
                       r"(?:(?P<cp>[CP])(?P<strike>-?\d+(?:\.\d+)?))?$")
_KEYS = {"COMDTY": "Comdty", "INDEX": "Index", "CURNCY": "Curncy"}
_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _strike_arg(strike, symbol: str) -> Optional[float]:
    if strike is None or (isinstance(strike, str) and not strike.strip()):
        return None
    text = str(strike).strip()
    if isinstance(strike, str) and _THOUSANDS_RE.match(text):
        text = text.replace(",", "")    # '1,250.5'; a decimal comma ('0,5') is refused, never read 5
    try:
        k = float(text)
    except (TypeError, ValueError):
        raise UnknownContract(f"option symbol {symbol!r}: strike {strike!r} is not a number") from None
    if not math.isfinite(k):
        raise UnknownContract(f"option symbol {symbol!r}: strike {strike!r} is not a number")
    return None if k == 0 else k


def _merge(symbol: str, from_symbol, from_arg, what: str, same):
    if from_symbol is not None and from_arg is not None and not same(from_symbol, from_arg):
        raise UnknownContract(f"option symbol {symbol!r} says {what} {from_symbol} but the row "
                              f"says {from_arg}")
    value = from_symbol if from_symbol is not None else from_arg
    if value is None:
        raise UnknownContract(f"option symbol {symbol!r}: no {what} in the symbol or the row "
                              "(never guessed)")
    return value


def _underlying_month(underlying: str, root: ContractRoot, ref_year: int):
    """(month, year) when ``underlying`` is a futures symbol of ``root``; None when it cannot be
    read as a future; UnknownContract when it names a future of another root."""
    try:
        parsed = _parse(underlying, ref_year)
    except UnknownContract:
        return None
    if parsed is None:
        return None
    prefix, code, month, year, _form, _key = parsed
    if (prefix is not None and root.exchange not in prefix) or code not in (root.exchange_code, root.bbg_root):
        raise UnknownContract(f"the underlying {underlying!r} is not a {root.root_id} future")
    return month, year


def resolve_option(symbol: str, *, trade_date, underlying: str = "", description: str = "",
                   currency: str = "", venue: str = "", option_type: str = "", strike=None,
                   conn: Optional[sqlite3.Connection] = None) -> OptionContract:
    """The listed option a blotter or Bloomberg symbol names, or ``UnknownContract`` /
    ``AmbiguousContract`` (see the module docstring for the forms and rules). ``conn``
    (optional) lets Bloomberg's stored dates replace the estimates."""
    ref_year = to_date(trade_date).year
    text = re.sub(r"\s+", " ", str(symbol or "").strip().upper())
    prefix = None
    m = _PREFIX_RE.match(text)
    if m:
        prefix = _venue_set(m.group("exch"))
        if prefix is None:
            raise UnknownContract(f"option symbol {symbol!r}: exchange prefix {m.group('exch')!r} is not known")
        text = m.group("rest").strip()
    body = text
    sm = _PB_SUFFIX_RE.match(text)
    if sm and not _BBG_OPT_RE.match(text):
        body = sm.group("body").strip()

    sym_cp = sym_strike = sym_style = expiry = None
    month = year = None
    form = key = ""
    om = _BBG_OPT_RE.match(text)
    dm = _DATED_RE.match(body)
    if om:
        code = om.group("root")
        month = month_from_code(om.group("code"))
        year = expand_year(om.group("year"), ref_year)
        sym_cp, sym_strike = om.group("cp"), float(om.group("strike"))
        key = _KEYS.get(om.group("key") or "", "")
        form = "bloomberg" if key or (om.group("pad") and len(code) == 1) else "letter"
    elif dm:
        code, form = dm.group("root"), "letter"
        ymd = dm.group("ymd")
        try:
            expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
        except ValueError:
            raise UnknownContract(f"option symbol {symbol!r}: {ymd!r} is not a date (yymmdd)") from None
        sym_style = {"E": EUROPEAN, "A": AMERICAN}.get(dm.group("style") or "")
        if dm.group("cp"):
            sym_cp, sym_strike = dm.group("cp"), float(dm.group("strike"))
    else:
        parsed = _parse(symbol, ref_year)
        if parsed is None:
            raise UnknownContract(
                f"option symbol {symbol!r} is not an option such as 'CLZ6C 70 Comdty' or "
                f"'CL/A261117C70-USAA', nor a futures month with the type and strike given")
        prefix, code, month, year, form, key = parsed

    try:
        arg_cp = cp_letter(option_type) if str(option_type or "").strip() else None
    except ValueError as exc:
        raise UnknownContract(f"option symbol {symbol!r}: {exc}") from None
    cp = _merge(symbol, sym_cp, arg_cp, "option type", lambda a, b: a == b)
    k = _merge(symbol, sym_strike if sym_strike != 0 else None, _strike_arg(strike, symbol), "strike",
               lambda a, b: abs(a - b) <= 1e-9 * max(1.0, abs(a)))

    und_symbol = str(underlying or "").strip()
    try:
        root = _pick_root(symbol, prefix, code, form, key, currency=currency, venue=venue,
                          underlying=underlying, description=description, what="option symbol")
    except UnknownContract:
        # an option code the universe does not carry (CME's 'LO' for crude options): the
        # underlying future, when the row names one, says which root it is
        if not und_symbol:
            raise
        try:
            fut = resolve_future(und_symbol, trade_date=trade_date, description=description,
                                 currency=currency, venue=venue)
        except (UnknownContract, AmbiguousContract):
            raise UnknownContract(f"option symbol {symbol!r}: root {code!r} is not in "
                                  f"config/contracts.csv and the underlying {underlying!r} names "
                                  "no single future") from None
        root = fut.root

    und_month = _underlying_month(und_symbol, root, ref_year) if und_symbol else None
    und = contract_month(root.root_id, *und_month, conn=conn) if und_month else None

    if month is None:  # the dated form: the expiry says which contract month
        if und is not None:
            month, year = und.month, und.year
        elif root.option_lead_months is None:
            cands = []
            for lead in (0, 1, 2):
                mm, yy = expiry.month + lead, expiry.year
                if mm > 12:
                    mm, yy = mm - 12, yy + 1
                cands.append(make_contract_id(root.bbg_root, MONTH_CODES[mm - 1], yy, root.bbg_yellow_key))
            raise AmbiguousContract(
                f"option symbol {symbol!r}: an option expiring {expiry.isoformat()} can be on "
                f"{', '.join(cands)}; config/contracts.csv does not say in which month {root.root_id} "
                "options expire (option_lead_months): name the underlying future", cands)
        else:
            mm, yy = expiry.month + root.option_lead_months, expiry.year
            if mm > 12:
                mm, yy = mm - 12, yy + 1
            month, year = mm, yy
    try:
        option = option_contract(root.root_id, month, year, cp, k, style=sym_style, underlying=und,
                                 symbol_expiry=expiry, conn=conn)
    except ValueError as exc:
        raise UnknownContract(f"option symbol {symbol!r}: {exc}") from None
    if expiry is not None and expiry > option.underlying.last_trade_date:
        raise UnknownContract(
            f"option symbol {symbol!r} expires {expiry.isoformat()}, after its underlying "
            f"{option.underlying.contract_id}'s last trade date "
            f"{option.underlying.last_trade_date.isoformat()}"
            + (" (estimated)" if option.underlying.estimated else ""))
    return option


def option_for(root_id: str, contract_id: str,
               conn: Optional[sqlite3.Connection] = None) -> OptionContract:
    """The option a stored canonical id names for a known root: ``('NYMEX:CL', 'CLZ26C 70
    Comdty')`` -> the CL Dec 2026 70 call. The inverse of ``OptionContract.contract_id``, like
    ``contract_for``: the id must be the two-digit-year form of that root. With ``conn``,
    Bloomberg's stored dates win; failing those, the option's own ``instruments.expiry_date``
    (the expiry its broker symbol stated when it was booked) is taken as the symbol's expiry,
    so the answer agrees with ``resolve_option``: used only when earlier than the underlying's
    last trade date, ``dates_source`` 'SYMBOL'. Raises ``UnknownContract`` otherwise."""
    try:
        root = get_root(root_id)
    except KeyError as exc:
        raise UnknownContract(exc.args[0]) from None
    parts = parse_option_ticker(contract_id)
    example = make_option_id(root.bbg_root, "Z", 2026, "C", 100, root.bbg_yellow_key)
    if parts is None or len(parts[2]) != 2:
        raise UnknownContract(f"option id {contract_id!r} is not a canonical option id such as {example!r}")
    bbg_root, code, yy, cp, strike, key = parts
    if bbg_root != root.bbg_root or key != root.bbg_yellow_key:
        raise UnknownContract(f"option id {contract_id!r} is not a {root.root_id} option "
                              f"(Bloomberg root {root.bbg_root!r}, {root.bbg_yellow_key})")
    booked = _booked_expiry(conn, contract_id) if conn is not None else None
    option = option_contract(root.root_id, month_from_code(code), 2000 + int(yy), cp, float(strike),
                             symbol_expiry=booked, conn=conn)
    if option.dates_source == SYMBOL:
        option = replace(option, dates_note=(
            "the option's own expiry as booked (instruments.expiry_date, from the broker's "
            f"symbol), earlier than the underlying {option.underlying.contract_id}'s last trade "
            "date; Bloomberg's date not stored yet"))
    return option


def _booked_expiry(conn: sqlite3.Connection, contract_id: str) -> Optional[date]:
    """The option instrument's stored ``expiry_date``; None when there is no instruments table,
    no row, the perpetual sentinel or a value that is not a date."""
    try:
        row = conn.execute("SELECT expiry_date FROM instruments WHERE instrument_id = ?",
                           (contract_id,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0] or str(row[0]).startswith("9999"):
        return None
    try:
        return date.fromisoformat(str(row[0]).strip()[:10])
    except ValueError:
        return None
