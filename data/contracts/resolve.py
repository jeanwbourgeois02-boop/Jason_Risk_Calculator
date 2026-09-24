"""A futures symbol, as a blotter or Bloomberg writes it, to its contract month.

Forms accepted (hard rule 6: tolerant of case and spacing):

- prime-broker export ``'<ROOT><M><Y>-<letters>'``: ``'CLZ6-USAA'``;
- Bloomberg ``'CLZ6 Comdty'``, ``'CLZ26 Comdty'``, ``'C Z6 Comdty'``;
- bare ``'CLZ6'``, ``'CLZ26'``;
- Chinese ``root + YYMM`` (``'CU2611'``, ``'cu2611'``) and ZCE's ``root + YMM`` (``'SR611'``);
- any of those behind an explicit exchange prefix: ``'SHFE:CU2611'``, ``'NYMEX:CLZ6'``.

Matching. A Bloomberg form (yellow key, or a one-character root padded with a space) is
Bloomberg's namespace: its root is matched on ``bbg_root`` (unique in the file), then on
``exchange_code`` if no Bloomberg root matches. The Chinese digit forms are matched on the
``exchange_code`` of the mainland Chinese roots (ZCE first for the three-digit form), then of any
root. Every other form is matched on ``exchange_code`` and ``bbg_root`` together: a bare code in
one namespace can be another root's code in the other (``'CO'`` is LME cobalt's exchange code and
ICE Brent's Bloomberg root), so neither is preferred silently. Bare codes collide (``ZC`` is CBOT
corn and ZCE coal; also CA, SI, PB, RS, SC ...): the candidates are narrowed by the explicit
prefix, then ``currency``, then ``venue``, then an exchange named in ``underlying`` or
``description``; more than one left raises ``AmbiguousContract`` naming every candidate. A
populated ``currency`` or recognised ``venue`` that no candidate fits is a contradiction between
two fields and raises ``UnknownContract``. A root that is not in ``config/contracts.csv`` raises
``UnknownContract``: a multiplier is never guessed.

A one-digit year is the nearest year >= ``trade_date.year - 1`` (the rule of
``data/ingest/common.py::future_expiry``); a two-digit one is in ``trade_date``'s century.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from data.contracts.months import ContractMonth, contract_month
from data.contracts.tickers import expand_year, make_contract_id, month_from_code, parse_bbg_ticker, to_date
from data.contracts.universe import ContractRoot, get_root, load_roots


class UnknownContract(ValueError):
    """The symbol names no root in the universe, or contradicts a populated field."""


class AmbiguousContract(ValueError):
    """The symbol fits more than one root; ``candidates`` lists their root_ids."""

    def __init__(self, message: str, candidates: Iterable[str] = ()):
        super().__init__(message)
        self.candidates: Tuple[str, ...] = tuple(candidates)


_ALL_CME = frozenset({"CME", "CBOT", "NYMEX", "COMEX"})
# Exchange names, abbreviations and MICs -> the universe's exchange ids they can mean.
_VENUES: Dict[str, FrozenSet[str]] = {
    "CME": _ALL_CME, "CMEGROUP": _ALL_CME, "GLOBEX": _ALL_CME, "XCME": frozenset({"CME"}),
    "CBOT": frozenset({"CBOT"}), "XCBT": frozenset({"CBOT"}),
    "NYMEX": frozenset({"NYMEX"}), "XNYM": frozenset({"NYMEX"}),
    "COMEX": frozenset({"COMEX"}), "XCEC": frozenset({"COMEX"}),
    "MGEX": frozenset({"MGEX"}), "MIAX": frozenset({"MGEX"}), "MIAXFUTURES": frozenset({"MGEX"}),
    "ICE": frozenset({"ICE", "ICEUS"}),
    "ICEEU": frozenset({"ICE"}), "ICEEUROPE": frozenset({"ICE"}), "ICEFUTURESEUROPE": frozenset({"ICE"}),
    "IFEU": frozenset({"ICE"}), "ICEENDEX": frozenset({"ICE"}), "NDEX": frozenset({"ICE"}),
    "IFED": frozenset({"ICE"}),
    "ICEUS": frozenset({"ICEUS"}), "ICEFUTURESUS": frozenset({"ICEUS"}), "IFUS": frozenset({"ICEUS"}),
    "NYBOT": frozenset({"ICEUS"}),
    "LME": frozenset({"LME"}), "XLME": frozenset({"LME"}), "LONDONMETALEXCHANGE": frozenset({"LME"}),
    "SHFE": frozenset({"SHFE"}), "XSGE": frozenset({"SHFE"}), "SHANGHAIFUTURESEXCHANGE": frozenset({"SHFE"}),
    "INE": frozenset({"INE"}), "XINE": frozenset({"INE"}),
    "DCE": frozenset({"DCE"}), "XDCE": frozenset({"DCE"}), "DALIAN": frozenset({"DCE"}),
    "ZCE": frozenset({"ZCE"}), "CZCE": frozenset({"ZCE"}), "XZCE": frozenset({"ZCE"}),
    "ZHENGZHOU": frozenset({"ZCE"}),
    "GFEX": frozenset({"GFEX"}), "GUANGZHOU": frozenset({"GFEX"}),
    "OSE": frozenset({"OSE"}), "XOSE": frozenset({"OSE"}), "JPX": frozenset({"OSE"}),
    "OSAKA": frozenset({"OSE"}),
    "TOCOM": frozenset({"TOCOM"}), "XTKT": frozenset({"TOCOM"}),
    "SGX": frozenset({"SGX"}), "XSES": frozenset({"SGX"}), "XSIM": frozenset({"SGX"}),
    "EURONEXT": frozenset({"EURONEXT"}), "MATIF": frozenset({"EURONEXT"}), "XMAT": frozenset({"EURONEXT"}),
    "BMD": frozenset({"BMD"}), "BURSA": frozenset({"BMD"}), "BURSAMALAYSIA": frozenset({"BMD"}),
    "XKLS": frozenset({"BMD"}),
    "HKEX": frozenset({"HKEX"}), "HKFE": frozenset({"HKEX"}), "XHKF": frozenset({"HKEX"}),
    "EEX": frozenset({"EEX"}), "XEEE": frozenset({"EEX"}),
    "GME": frozenset({"GME"}), "DME": frozenset({"GME"}),
}
_CURRENCY_ALIASES = {"CNH": "CNY", "RMB": "CNY"}

_PREFIX_RE = re.compile(r"^(?P<exch>[A-Z][A-Z ]*?)\s*:\s*(?P<rest>.+)$")
_PB_SUFFIX_RE = re.compile(r"^(?P<body>.+?)-[A-Z]{2,6}$")
_BBG_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6}?)(?P<pad> ?)(?P<code>[FGHJKMNQUVXZ])(?P<year>\d{1,2})"
                     r"(?: (?P<key>COMDTY|INDEX|CURNCY))?$")
_CN_RE = re.compile(r"^(?P<root>[A-Z]{1,4})(?P<digits>\d{3,4})$")


def _venue_set(text: str) -> Optional[FrozenSet[str]]:
    key = re.sub(r"[^A-Z]", "", str(text or "").upper())
    return _VENUES.get(key) if key else None


def _venues_named_in(text: str) -> FrozenSet[str]:
    """Exchanges named as whole words in free text ('NYMEX CRUDE OIL', 'SHFE:CU')."""
    found: set = set()
    for word in re.findall(r"[A-Z]+", str(text or "").upper()):
        found |= _VENUES.get(word, frozenset())
    return frozenset(found)


def _parse(symbol: str, ref_year: int):
    """(prefix exchanges or None, code, month, year, form, yellow key) or None."""
    text = re.sub(r"\s+", " ", str(symbol or "").strip().upper())
    if not text:
        return None
    prefix = None
    m = _PREFIX_RE.match(text)
    if m:
        prefix = _venue_set(m.group("exch"))
        if prefix is None:
            raise UnknownContract(f"futures symbol {symbol!r}: exchange prefix {m.group('exch')!r} is not known")
        text = m.group("rest").strip()
    m = _PB_SUFFIX_RE.match(text)
    if m and not _BBG_RE.match(text) and not _CN_RE.match(text):
        text = m.group("body").strip()
    m = _BBG_RE.match(text)
    if m:
        bloomberg = bool(m.group("key")) or bool(m.group("pad") and len(m.group("root")) == 1)
        key = {"COMDTY": "Comdty", "INDEX": "Index", "CURNCY": "Curncy"}.get(m.group("key") or "", "")
        return (prefix, m.group("root"), month_from_code(m.group("code")),
                expand_year(m.group("year"), ref_year), "bloomberg" if bloomberg else "letter", key)
    m = _CN_RE.match(text)
    if m:
        digits = m.group("digits")
        year_digits, mm = (digits[:2], digits[2:]) if len(digits) == 4 else (digits[:1], digits[1:])
        month = int(mm)
        if not 1 <= month <= 12:
            return None
        return (prefix, m.group("root"), month, expand_year(year_digits, ref_year),
                "cn4" if len(digits) == 4 else "cn3", "")
    return None


def _candidates(roots: List[ContractRoot], code: str, form: str, key: str) -> List[ContractRoot]:
    by_exchange = [r for r in roots if r.exchange_code == code]
    by_bbg = [r for r in roots if r.bbg_root == code and (not key or r.bbg_yellow_key == key)]
    if form == "bloomberg":
        return by_bbg or by_exchange
    if form in ("cn4", "cn3"):
        cn = [r for r in by_exchange if r.country == "CN"]
        if form == "cn3":
            cn = [r for r in cn if r.exchange == "ZCE"] or cn
        return cn or by_exchange or by_bbg
    return by_exchange + [r for r in by_bbg if r not in by_exchange]


def resolve_future(symbol: str, *, trade_date, underlying: str = "", description: str = "",
                   currency: str = "", venue: str = "",
                   conn: Optional[sqlite3.Connection] = None) -> ContractMonth:
    """The contract month a futures symbol names, or ``UnknownContract`` / ``AmbiguousContract``.

    ``underlying`` is tried as the symbol when ``symbol`` cannot be read, and like
    ``description`` it can name the exchange. ``conn`` (optional) lets Bloomberg's stored
    contract dates replace the estimate (``months.contract_month``).
    """
    ref_year = to_date(trade_date).year
    parsed = None
    for text in (symbol, underlying):
        parsed = _parse(text, ref_year)
        if parsed is not None:
            break
    if parsed is None:
        raise UnknownContract(f"futures symbol {symbol!r} is not a contract month such as 'CLZ6', "
                              f"'CLZ6 Comdty', 'CU2611' or 'SHFE:CU2611'")
    prefix, code, month, year, form, key = parsed
    roots = list(load_roots().values())
    if prefix is not None:
        roots = [r for r in roots if r.exchange in prefix]
    cands = _candidates(roots, code, form, key)
    if not cands:
        raise UnknownContract(f"futures symbol {symbol!r}: root {code!r} is not in config/contracts.csv"
                              + (f" on {'/'.join(sorted(prefix))}" if prefix else ""))

    ccy = str(currency or "").strip().upper()
    ccy = _CURRENCY_ALIASES.get(ccy, ccy)
    if ccy:
        kept = [r for r in cands if r.currency == ccy]
        if not kept:
            raise UnknownContract(
                f"futures symbol {symbol!r} is {', '.join(f'{r.root_id} ({r.currency})' for r in cands)} "
                f"but the row's currency is {currency!r}")
        cands = kept
    venue_set = _venue_set(venue)
    if venue_set is not None:
        kept = [r for r in cands if r.exchange in venue_set]
        if not kept:
            raise UnknownContract(
                f"futures symbol {symbol!r} is {', '.join(r.root_id for r in cands)} "
                f"but the row's venue is {venue!r}")
        cands = kept
    if len(cands) > 1:
        hinted = _venues_named_in(f"{underlying} {description}")
        kept = [r for r in cands if r.exchange in hinted]
        if kept:
            cands = kept
    if len(cands) > 1:
        ids = [r.root_id for r in cands]
        raise AmbiguousContract(
            f"futures symbol {symbol!r} fits {len(ids)} contract roots: {', '.join(ids)}; "
            "name the exchange as a prefix ('EXCHANGE:SYMBOL'), the currency or the venue", ids)
    return contract_month(cands[0].root_id, month, year, conn=conn)


def contract_for(root_id: str, contract_id: str,
                 conn: Optional[sqlite3.Connection] = None) -> ContractMonth:
    """The contract month a stored canonical id names for a known root: ``('NYMEX:CL',
    'CLZ26 Comdty')`` -> CL Dec 2026. No symbol reading and no ambiguity: the id must be the
    two-digit-year canonical form, and its Bloomberg root and yellow key must be the root's.
    With ``conn``, Bloomberg's stored dates win (``months.contract_month``).

    Raises ``UnknownContract`` for an unknown root, an id that does not parse (a one-digit year
    included: its decade would be a guess), or one that belongs to another root.
    """
    try:
        root = get_root(root_id)
    except KeyError as exc:
        raise UnknownContract(exc.args[0]) from None
    parts = parse_bbg_ticker(contract_id)
    if parts is None or len(parts[2]) != 2:
        raise UnknownContract(f"contract id {contract_id!r} is not a canonical id such as "
                              f"{make_contract_id(root.bbg_root, 'Z', 2026, root.bbg_yellow_key)!r}")
    bbg_root, code, yy, key = parts
    if bbg_root != root.bbg_root or key != root.bbg_yellow_key:
        raise UnknownContract(f"contract id {contract_id!r} is not a {root.root_id} contract "
                              f"(Bloomberg root {root.bbg_root!r}, {root.bbg_yellow_key})")
    return contract_month(root.root_id, month_from_code(code), 2000 + int(yy), conn=conn)
