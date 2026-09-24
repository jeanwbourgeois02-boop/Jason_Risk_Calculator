"""Contract-month ids and Bloomberg ticker strings: pure string rules, no universe, no database.

The canonical id of a contract month is the research app's ``make_contract_id`` convention:
Bloomberg root (a one-character root padded with a space, as Bloomberg does), month code,
two-digit year, a space, the yellow key: ``'CLZ26 Comdty'``, ``'C Z26 Comdty'``. Bloomberg's
live request form has a one-digit year (``'CLZ6 Comdty'``); see ``months.request_ticker``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Tuple

MONTH_CODES = "FGHJKMNQUVXZ"  # index 0 = January
YELLOW_KEYS = {"comdty": "Comdty", "index": "Index", "curncy": "Curncy"}

# '<root><month code><1-2 digit year> <yellow key>', root padded or not: 'CLZ6 Comdty',
# 'C Z26 Comdty'. The month code is the last letter before the year digits, so the split is
# unique however many letters or digits the root has ('B0Z6', 'M65FZ26').
_BBG_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,6}?)(?P<pad>\s*)(?P<code>[FGHJKMNQUVXZ])(?P<year>\d{1,2})"
                     r"\s+(?P<key>COMDTY|INDEX|CURNCY)$")


def month_code(month: int) -> str:
    """``12`` -> ``'Z'``."""
    if not 1 <= int(month) <= 12:
        raise ValueError(f"month {month!r} is not 1-12")
    return MONTH_CODES[int(month) - 1]


def month_from_code(code: str) -> int:
    """``'Z'`` -> ``12``."""
    code = code.strip().upper()
    if len(code) != 1 or code not in MONTH_CODES:
        raise ValueError(f"month code {code!r} is not one of {MONTH_CODES}")
    return MONTH_CODES.index(code) + 1


def padded_root(bbg_root: str) -> str:
    """A one-character Bloomberg root is padded with a space: ``'C'`` -> ``'C '``."""
    return bbg_root if len(bbg_root) > 1 else bbg_root + " "


def make_contract_id(bbg_root: str, code: str, year: int, yellow_key: str = "Comdty") -> str:
    """Canonical contract id: ``('CL', 'Z', 2026)`` -> ``'CLZ26 Comdty'``, ``('C', 'Z', 2026)`` -> ``'C Z26 Comdty'``.

    A one-character root is padded with a space, as Bloomberg does.
    """
    month_from_code(code)
    return f"{padded_root(bbg_root)}{code.upper()}{int(year) % 100:02d} {yellow_key}"


def expand_year(digits: str, ref_year: int) -> int:
    """A one- or two-digit contract year as a four-digit year.

    One digit: the nearest year >= ``ref_year - 1`` ending in that digit (the rule of
    ``data/ingest/common.py::future_expiry``). Two digits: that year in ``ref_year``'s century.
    Four digits are returned as they are.
    """
    digits = digits.strip()
    if not digits.isdigit():
        raise ValueError(f"year {digits!r} is not a number")
    if len(digits) == 4:
        return int(digits)
    if len(digits) == 1:
        year = (ref_year // 10) * 10 + int(digits)
        if year < ref_year - 1:
            year += 10
        return year
    if len(digits) == 2:
        return (ref_year // 100) * 100 + int(digits)
    raise ValueError(f"year {digits!r} must have 1, 2 or 4 digits")


def parse_bbg_ticker(ticker: str) -> Optional[Tuple[str, str, str, str]]:
    """``'C Z6 Comdty'`` -> ``('C', 'Z', '6', 'Comdty')``; None when it is not a contract ticker."""
    m = _BBG_RE.match(re.sub(r"\s+", " ", str(ticker or "").strip().upper()))
    if not m:
        return None
    return m.group("root"), m.group("code"), m.group("year"), YELLOW_KEYS[m.group("key").lower()]


def canonical_from_ticker(ticker: str, near: date) -> str:
    """A contract ticker in either year form as its canonical id; a one-digit year is read as
    the year nearest ``near`` (a date in the contract's life, such as its last trade date).

    ``('CLZ6 Comdty', date(2026, 11, 19))`` -> ``'CLZ26 Comdty'``. Raises ValueError when the
    string is not a contract ticker.
    """
    parts = parse_bbg_ticker(ticker)
    if parts is None:
        raise ValueError(f"{ticker!r} is not a contract ticker such as 'CLZ26 Comdty'")
    root, code, year, key = parts
    return make_contract_id(root, code, expand_year(year, near.year), key)


def to_date(value) -> date:
    """A date from a date, a datetime, or text: 'YYYY-MM-DD', 'YYYYMMDD', 'YYYY/MM/DD', an ISO timestamp."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})(?:$|[T ])", text.replace("/", "-"))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    raise ValueError(f"{value!r} is not a date (expected YYYY-MM-DD)")
