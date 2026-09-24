"""The Bloomberg ticker check (bbg-ticker-check lane), run on request at the Bloomberg PC.

    python -m data.bloomberg.ticker_check --dry-run              # what would be asked; asks nothing
    python -m data.bloomberg.ticker_check --limit 5              # the first five roots first
    python -m data.bloomberg.ticker_check --sector energy --search
    python -m data.bloomberg.ticker_check --book --db data/raw/risk.db

The contract universe's Bloomberg roots and price scales are best guesses until a terminal
confirms them (user, 2026-09-24: "add a bloomberg diagnostic tool I will be able to use so that
I can diagnose when I finally have access to bloomberg"), and a wrong price scale (cents against
dollars per bushel, pence against pounds per therm) is a 100x P&L error. This asks Bloomberg what
each ticker really is and says, in plain words, which rows of ``config/contracts.csv`` are right,
which are wrong and what to change.

What it asks (``plan`` works it out and asks nothing):

1. Root check: each selected root's generic front ticker ('CL1 Comdty', 'C 1 Comdty'), fields
   ROOT_FIELDS, BATCH_SIZE securities per ReferenceDataRequest. A placeholder root ('ZZ...')
   is never asked: it is NOT_FOUND as it stands.
2. Book check (``--db`` or ``--book``; the database is opened read-only): every unexpired
   commodity future the book holds, on its own ticker (CONTRACT_FIELDS), and every USD
   conversion spot the Bloomberg library lists for the book date (SPOT_FIELDS).
3. Search (``--search``): for each root Bloomberg does not know, the ``//blp/instruments``
   security search (SECF <GO> from Python) with the root's name and exchange. It lists
   candidate roots and asks for no data fields.

Verdicts per root, most severe first: NO_ANSWER (the request timed out), NOT_FOUND (Bloomberg's
own error words), NO_PRICE (resolves, but no PX_LAST or PX_SETTLE: an entitlement or a dead
contract), CURRENCY_MISMATCH (another currency; CNH and CNY count as the same), SCALE_MISMATCH
(FUT_VAL_PT against our multiplier, or a minor-unit currency such as 'USd' / 'GBp' against our
price_scale; a ratio of 100 or 0.01 suggests the price_scale that makes them agree),
EXCHANGE_MISMATCH and NAME_CHECK (warnings only), OK.

Outputs under ``reports/`` (git-ignored): ``bbg_check_<stamp>.txt`` (plain English),
``bbg_check_<stamp>.csv`` (every field returned, per security) and ``contract_fixes_<stamp>.csv``
(WORKSHEET_COLUMNS, the worksheet ``data.contracts.apply_fixes`` reads; ``apply`` is pre-filled
'yes' only on an OK root's bbg_verified row, so the user decides the rest).

Hard rule 8: nothing here runs unless the user runs it. It never writes ``marks``, never touches
a trade and never edits ``config/contracts.csv``. blpapi is imported only when a real check
connects, so the module and its tests run on a PC without it.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import itertools
import math
import os
import re
import sqlite3
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from data.contracts import ContractRoot, UnknownContract, contract_for, load_roots, request_ticker, static_dates
from data.contracts.tickers import padded_root, to_date

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "reports"
DEFAULT_DB = REPO_ROOT / "data" / "raw" / "risk.db"   # ui/app.py's default, read without importing the UI

ROOT_FIELDS = ("NAME", "SECURITY_DES", "EXCH_CODE", "CRNCY", "FUT_CONT_SIZE", "FUT_TRADING_UNITS", "QUOTE_UNITS",
               "FUT_VAL_PT", "FUT_TICK_SIZE", "FUT_TICK_VAL", "FUT_CUR_GEN_TICKER", "FUT_LAST_TRADE_DT",
               "FUT_NOTICE_FIRST", "PX_LAST", "PX_SETTLE")
CONTRACT_FIELDS = ("PX_LAST", "FUT_LAST_TRADE_DT", "FUT_NOTICE_FIRST")
SPOT_FIELDS = ("PX_LAST",)
BATCH_SIZE = 50
PURPOSE = "ticker_check"                  # the diagnostics tag of this module's requests

INSTRUMENTS_SERVICE = "//blp/instruments"
YELLOW_KEY_FILTER = "YK_FILTER_CMDT"
SEARCH_MAX_RESULTS = 50
SEARCH_TIMEOUT_MS = 60000
SEARCH_QUERIES_PER_ROOT = 2
MAX_SEARCH_ERRORS = 3                     # consecutive failed searches before the rest are skipped
CANDIDATES_SHOWN = 5
CANDIDATE_ROWS = 3                        # bbg_root rows per root in the worksheet when no candidate stands out

WORKSHEET_COLUMNS = ("root_id", "field", "current", "suggested", "verdict", "reason", "apply")

# Root verdicts, the most severe first.
NO_ANSWER = "NO_ANSWER"
NOT_FOUND = "NOT_FOUND"
NO_PRICE = "NO_PRICE"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
SCALE_MISMATCH = "SCALE_MISMATCH"
EXCHANGE_MISMATCH = "EXCHANGE_MISMATCH"
NAME_CHECK = "NAME_CHECK"
OK = "OK"
ROOT_VERDICTS = (NO_ANSWER, NOT_FOUND, NO_PRICE, CURRENCY_MISMATCH, SCALE_MISMATCH, EXCHANGE_MISMATCH, NAME_CHECK, OK)
WARNING_VERDICTS = (EXCHANGE_MISMATCH, NAME_CHECK)

# Book verdicts (a contract or a spot), the most severe first.
NO_TICKER = "NO_TICKER"
SCALE_FLAG = "SCALE_FLAG"                 # fill against PX_LAST about 100 or 0.01
PRICE_GAP = "PRICE_GAP"                   # fill against PX_LAST far apart, but not a factor of 100
DATE_MISMATCH = "DATE_MISMATCH"           # stored contract_static dates are not Bloomberg's
BOOK_VERDICTS = (NO_TICKER, NO_ANSWER, NOT_FOUND, NO_PRICE, SCALE_FLAG, PRICE_GAP, DATE_MISMATCH, OK)

# Bloomberg's EXCH_CODE values believed to mean each exchange of config/contracts.csv. UNVERIFIED
# against a terminal: an unknown code is an EXCHANGE_MISMATCH warning, never an error.
BBG_EXCHANGE_CODES: Dict[str, Tuple[str, ...]] = {
    "CME": ("CME",),
    "CBOT": ("CBT", "CBOT"),
    "NYMEX": ("NYM", "NYMEX"),
    "COMEX": ("CMX", "COMEX"),
    "MGEX": ("MGE", "MGEX", "MIAX"),
    "ICEUS": ("NYF", "ICE", "IFUS", "NYB"),
    "ICE": ("ICE", "IFEU", "IFE", "NDX", "IEU", "ENDEX"),
    "LME": ("LME",),
    "SHFE": ("SHF", "SHFE"),
    "INE": ("INE", "SHF", "SIE"),
    "DCE": ("DCE",),
    "ZCE": ("CZC", "ZCE"),
    "GFEX": ("GFE", "GFEX", "GZF"),
    "OSE": ("OSE", "JPX"),
    "TOCOM": ("TCM", "TOCOM", "OSE"),
    "SGX": ("SGX", "SMX", "SES"),
    "EURONEXT": ("EOP", "MATIF", "ENX", "MAT"),
    "BMD": ("MDX", "MDE", "BMD", "KLS"),
    "HKEX": ("HKG", "HKF", "HKFE"),
    "EEX": ("EEX",),
    "GME": ("GME", "DME"),
}
# Words Bloomberg uses for each exchange in a search result's description (upper case, whole words).
EXCHANGE_WORDS: Dict[str, Tuple[str, ...]] = {
    "SHFE": ("SHFE", "SHANGHAI FUTURES", "SHANGHAI"),
    "INE": ("INE", "SHANGHAI INTL ENERGY", "SHANGHAI INTERNATIONAL ENERGY"),
    "DCE": ("DCE", "DALIAN"),
    "ZCE": ("ZCE", "CZCE", "ZHENGZHOU"),
    "GFEX": ("GFEX", "GUANGZHOU"),
    "SGX": ("SGX", "SINGAPORE EXCHANGE", "SICOM"),
    "LME": ("LME", "LONDON METAL"),
    "CME": ("CME", "CHICAGO MERC"),
    "CBOT": ("CBOT", "CBT", "CHICAGO BOARD"),
    "NYMEX": ("NYMEX", "NYM", "NY MERC", "NEW YORK MERC"),
    "COMEX": ("COMEX", "CMX"),
    "MGEX": ("MGEX", "MINNEAPOLIS", "MIAX"),
    "ICE": ("ICE", "ICE FUTURES EUROPE", "ICE ENDEX", "ENDEX"),
    "ICEUS": ("ICE", "ICE FUTURES US", "NYBOT"),
    "OSE": ("OSE", "OSAKA", "JPX"),
    "TOCOM": ("TOCOM", "TOKYO", "OSE", "JPX"),
    "EURONEXT": ("EURONEXT", "MATIF", "PARIS"),
    "BMD": ("BMD", "BURSA", "MALAYSIA"),
    "HKEX": ("HKEX", "HKFE", "HONG KONG"),
    "EEX": ("EEX", "EUROPEAN ENERGY"),
    "GME": ("GME", "DME", "DUBAI"),
}
_CURRENCY_ALIASES = {"CNH": "CNY", "RMB": "CNY"}
_ENTITLEMENT_RE = re.compile(r"not\s+authori[sz]ed|entitle|permission|not\s+subscribed|no\s+access", re.I)
_GENERIC_NAME_RE = re.compile(r"generic\s+\d+\w*\s+'([^']*)'\s+fut", re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {"the", "and", "future", "futures", "futr", "fut", "generic", "monthly", "month", "contract", "index", "std",
     "standard", "platts", "argus", "fastmarkets", "tsi", "cru", "cfr", "fob", "cif", "exchange", "price", "average"}
    | {e.lower() for e in BBG_EXCHANGE_CODES}
    | {c.lower() for codes in BBG_EXCHANGE_CODES.values() for c in codes}
    | {"cmx", "bursa", "matif", "endex", "icus", "miax"})
_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "aluminium": ("aluminum", "alum"), "sulphur": ("sulfur",), "soybean": ("soy", "soyb"),
    "soybeans": ("soy", "soyb"), "gasoline": ("rbob", "gas"), "ulsd": ("heating", "diesel", "gasoil"),
    "heating": ("ulsd", "heat"), "cattle": ("catl", "cattl"), "hogs": ("hog",), "wheat": ("wht",),
    "crude": ("wti", "brent", "crd"), "natural": ("natgas", "nat"), "rolled": ("hrc",), "coil": ("hrc",),
    "polypropylene": ("pp",), "lldpe": ("polyethylene", "pe"), "corn": ("maize",),
}
# A Bloomberg root can start with a digit (NYMEX '7H', '1N', '9N').
_GENERIC_TICKER_RE = re.compile(r"^([A-Z0-9][A-Z0-9]{0,4}?)\s?(1[0-2]|[1-9])$")
_CONTRACT_TICKER_RE = re.compile(r"^([A-Z0-9][A-Z0-9]{0,4}?)\s?([FGHJKMNQUVXZ])(\d{1,2})$")
_YELLOW_KEYS = ("COMDTY", "INDEX", "CURNCY", "EQUITY", "CORP", "GOVT", "MTGE", "MUNI", "PFD", "M-MKT")
_SEARCH_CIDS = itertools.count(900001)   # CorrelationIds apart from pull_marks' counter on a shared session


# --------------------------------------------------------------------------- errors and answers

class BloombergUnavailable(RuntimeError):
    """Bloomberg could not be reached: blpapi missing, no session on host:port, no service."""


class SearchError(RuntimeError):
    """One security search failed; the root is marked and the check goes on."""


class BookError(RuntimeError):
    """The database given for the book check cannot be read."""


@dataclass(frozen=True)
class Answer:
    """What Bloomberg said about one security, in plain Python types (no blpapi objects)."""

    security: str
    fields: Mapping[str, object]
    security_error: str = ""                                  # Bloomberg's own words, '' when none
    field_errors: Mapping[str, str] = field(default_factory=dict)   # field -> Bloomberg's words
    answered: bool = True                                     # False: no answer came back (timeout)


# --------------------------------------------------------------------------- small helpers

def _num(value) -> Optional[float]:
    """A finite float from a Bloomberg value (number or numeric text), else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(str(value).replace(",", "").strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _plain(value: float) -> str:
    """A number as the worksheet and contracts.csv write it: '0.01', '50', '1120'."""
    return format(float(value), ".12g")


def _g(value) -> str:
    """A number for the report: '1,120', '0.01', '215.3'."""
    n = _num(value)
    if n is None:
        return str(value) if value not in (None, "") else "-"
    return f"{n:,.10g}" if abs(n) >= 1000 else f"{n:.10g}"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


def _date_text(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return to_date(value).isoformat()
    except ValueError:
        return str(value)


def generic_ticker(root: ContractRoot) -> str:
    """The root's generic front ticker: 'CL1 Comdty'; a one-character root is padded: 'C 1 Comdty'."""
    return f"{padded_root(root.bbg_root)}1 {root.bbg_yellow_key}"


def currency_parts(text: str) -> Tuple[str, bool]:
    """Bloomberg's CRNCY as (major currency, quoted in the minor unit): 'USd' -> ('USD', True),
    'GBp' -> ('GBP', True), 'EUR' -> ('EUR', False)."""
    t = str(text or "").strip()
    if len(t) == 3 and t[:2].isalpha() and t[:2].isupper() and t[2].isalpha() and t[2].islower():
        return t.upper(), True
    return t.upper(), False


def same_currency(a: str, b: str) -> bool:
    """CNH and CNY (and RMB) count as the same currency."""
    a, b = str(a or "").upper(), str(b or "").upper()
    return _CURRENCY_ALIASES.get(a, a) == _CURRENCY_ALIASES.get(b, b)


def ratio_kind(ratio: float) -> str:
    """Bloomberg's value per point over our multiplier: 'same' (within 1 %), 'x100' / 'x0.01'
    (within 2 % of either: a price-scale error), else 'other'."""
    if abs(ratio - 1.0) <= 0.01:
        return "same"
    if abs(ratio / 100.0 - 1.0) <= 0.02:
        return "x100"
    if abs(ratio / 0.01 - 1.0) <= 0.02:
        return "x0.01"
    return "other"


def fill_ratio_kind(ratio: float) -> str:
    """A blotter fill over Bloomberg's PX_LAST: 'same' within a factor of about 3 (a price move),
    'x100' / 'x0.01' within a factor of about 3 of either (a unit 100 times off), else 'other'."""
    lg = math.log10(ratio)
    if abs(lg) < 0.5:
        return "same"
    if abs(lg - 2.0) < 0.5:
        return "x100"
    if abs(lg + 2.0) < 0.5:
        return "x0.01"
    return "other"


def value_per_point(values: Mapping[str, object]) -> Tuple[Optional[float], str]:
    """(value of a 1.0 price move per contract, where it came from): FUT_VAL_PT, else
    FUT_TICK_VAL / FUT_TICK_SIZE; (None, '') when Bloomberg gave neither."""
    vp = _num(values.get("FUT_VAL_PT"))
    if vp is not None and vp > 0:
        return vp, "FUT_VAL_PT"
    tick_val, tick_size = _num(values.get("FUT_TICK_VAL")), _num(values.get("FUT_TICK_SIZE"))
    if tick_val is not None and tick_size is not None and tick_val > 0 and tick_size > 0:
        return tick_val / tick_size, "FUT_TICK_VAL / FUT_TICK_SIZE"
    return None, ""


def _words(text: str) -> List[str]:
    out: List[str] = []
    for w in _WORD_RE.findall(str(text or "").lower()):
        if len(w) >= 3 and not w.isdigit() and w not in _STOP_WORDS and w not in out:
            out.append(w)
    return out


def _word_match(a: str, b: str) -> bool:
    n = min(len(a), len(b))
    if n < 2:
        return False
    common = len(os.path.commonprefix([a, b]))
    return common >= min(5, n) and (n >= 3 or a == b)


def words_fit(ours: str, theirs: str) -> bool:
    """Does any significant word of our name (or a known synonym) appear in Bloomberg's text?
    Prefix-tolerant ('harbor' fits 'HARB'); True when our name has no significant word."""
    mine = _words(ours)
    if not mine:
        return True
    their_words = _WORD_RE.findall(str(theirs or "").lower())
    for w in mine:
        for form in (w,) + _SYNONYMS.get(w, ()):
            if any(_word_match(form, t) for t in their_words):
                return True
    return False


def exchange_matches(exchange: str, exch_code: str) -> bool:
    code = str(exch_code or "").strip().upper()
    return code in BBG_EXCHANGE_CODES.get(exchange, (exchange.upper(),)) or code == exchange.upper()


def _exchange_named_by(code: str) -> List[str]:
    return sorted(e for e, codes in BBG_EXCHANGE_CODES.items() if code in codes)


def _strip_key(security: str) -> str:
    """'CL1<cmdty>' / 'CL1 Comdty' / 'C 1 Comdty' -> 'CL1' / 'C 1' (upper case)."""
    text = str(security or "").strip()
    angle = re.match(r"^(.*?)\s*<[A-Za-z\-]+>$", text)
    if angle:
        return angle.group(1).strip().upper()
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].upper() in _YELLOW_KEYS:
        text = parts[0]
    return text.strip().upper()


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


# --------------------------------------------------------------------------- findings and results

@dataclass
class Finding:
    """One thing Bloomberg's answer says about a row: the verdict, what Bloomberg said, what to
    change, and (for a worksheet row) the field with its current and suggested value."""

    verdict: str
    evidence: str
    action: str
    field: str = ""
    current: str = ""
    suggested: str = ""


@dataclass
class RootResult:
    root: ContractRoot
    ticker: str
    asked: bool
    answer: Optional[Answer]
    findings: List[Finding]
    notes: List[str] = field(default_factory=list)          # informational, never a verdict
    candidates: List[dict] = field(default_factory=list)    # from --search
    search_status: str = ""

    @property
    def verdict(self) -> str:
        if not self.findings:
            return OK
        return min((f.verdict for f in self.findings), key=ROOT_VERDICTS.index)

    @property
    def needs_attention(self) -> bool:
        return self.verdict != OK


def _bloomberg_said(answer: Answer, names: Sequence[str]) -> str:
    said = [f"{n}: {answer.field_errors[n]}" for n in names if answer.field_errors.get(n)]
    return ("; Bloomberg said " + "; ".join(f'"{s}"' for s in said)) if said else ""


def _security_error_finding(ticker: str, answer: Answer, subject: str) -> Finding:
    words = answer.security_error
    if _ENTITLEMENT_RE.search(words):
        return Finding(NO_PRICE, f'Bloomberg refused {ticker!r}: "{words}"',
                       f"this terminal is not entitled to {subject}; ask Bloomberg for the entitlement, "
                       "then run the check again")
    return Finding(NOT_FOUND, f'Bloomberg does not know {ticker!r}: "{words}"',
                   "the Bloomberg root is wrong: find the right one (run again with --search, or SECF <GO> "
                   "on the terminal) and put it in bbg_root")


def check_root(root: ContractRoot, answer: Optional[Answer]) -> RootResult:
    """The verdicts on one root from Bloomberg's answer on its generic ticker (None: not asked)."""
    ticker = generic_ticker(root)
    if root.bbg_placeholder:
        return RootResult(root, ticker, False, None, [Finding(
            NOT_FOUND, f"not asked: bbg_root {root.bbg_root!r} is a placeholder, no Bloomberg root is known yet",
            "find the root (run again with --search, or SECF <GO> on the terminal) and put it in bbg_root")])
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        return RootResult(root, ticker, True, answer, [Finding(
            NO_ANSWER, f"Bloomberg did not answer for {ticker!r} ({words})", "run the check again")])
    if answer.security_error:
        finding = _security_error_finding(ticker, answer, f"{root.exchange} data")
        return RootResult(root, ticker, True, answer, [finding])

    values = answer.fields
    findings: List[Finding] = []
    notes: List[str] = []
    name = str(values.get("NAME") or "").strip()
    if _num(values.get("PX_LAST")) is None and _num(values.get("PX_SETTLE")) is None:
        what = f"resolves ({name})" if name else "resolves"
        findings.append(Finding(
            NO_PRICE, f"{ticker!r} {what} but returned no PX_LAST or PX_SETTLE"
                      + _bloomberg_said(answer, ("PX_LAST", "PX_SETTLE")),
            f"an entitlement this terminal lacks, or a dead contract: look at {ticker} GP on the terminal; "
            "nothing in config/contracts.csv changes until it prices"))

    ccy_raw = str(values.get("CRNCY") or "").strip()
    major, minor = currency_parts(ccy_raw)
    if ccy_raw and not same_currency(major, root.currency):
        findings.append(Finding(
            CURRENCY_MISMATCH,
            f"Bloomberg prices {ticker!r} in {ccy_raw}; config/contracts.csv says {root.currency}",
            f"if the name and exchange are right, set currency to {major} (the quote_unit and the USD "
            "conversion change with it); if not, the ticker is another contract and bbg_root is wrong",
            "currency", root.currency, major))
    else:
        findings.extend(_scale_findings(root, ticker, values, ccy_raw, major, minor, notes))
    if not ccy_raw:
        notes.append("Bloomberg gave no CRNCY: the currency and the cents / pence question were not checked")

    exch = str(values.get("EXCH_CODE") or "").strip().upper()
    if exch and not exchange_matches(root.exchange, exch):
        known = _exchange_named_by(exch)
        meaning = f"which this check reads as {'/'.join(known)}" if known else "a code this check does not know"
        findings.append(Finding(
            EXCHANGE_MISMATCH,
            f"Bloomberg lists {ticker!r} on exchange code {exch!r}, {meaning}; config/contracts.csv says "
            f"{root.exchange}",
            f"a warning only: confirm on the terminal ({ticker} DES) that this is the {root.exchange} contract "
            "and not a look-alike on another exchange"))
    elif not exch:
        notes.append("Bloomberg gave no EXCH_CODE: the exchange was not checked")

    problems = _name_problems(root, values)
    if problems:
        findings.append(Finding(
            NAME_CHECK, "; ".join(problems),
            f"a warning only: check on the terminal ({ticker} DES) that this ticker is the {root.name}"))
    return RootResult(root, ticker, True, answer, findings, notes)


def _scale_findings(root: ContractRoot, ticker: str, values: Mapping[str, object], ccy_raw: str, major: str,
                    minor: bool, notes: List[str]) -> List[Finding]:
    """Price-scale findings from FUT_VAL_PT (or tick value / tick size) against our multiplier,
    with Bloomberg's minor-unit currency ('USd', 'GBp') as the second witness."""
    ours_minor = abs(root.price_scale / 0.01 - 1.0) < 0.01
    ccy_factor: Optional[float] = None
    unit_words = "cents / pence" if minor else f"whole {major}"
    if ccy_raw and minor and not ours_minor:
        ccy_factor = 0.01
    elif ccy_raw and not minor and ours_minor:
        ccy_factor = 100.0
    ccy_text = (f"; its currency {ccy_raw!r} says the price is quoted in {unit_words}, our price_scale is "
                f"{_plain(root.price_scale)}") if ccy_factor is not None else ""
    ours = (f"our multiplier is {_g(root.multiplier)} ({_g(root.contract_size)} {root.size_unit} in "
            f"{root.quote_unit} x price_scale {_plain(root.price_scale)})")
    vp, vp_source = value_per_point(values)
    if vp is None:
        if ccy_factor is None:
            notes.append("Bloomberg gave no value per point (FUT_VAL_PT, tick value / tick size): the multiplier "
                         "was not confirmed")
            return []
        suggested = root.price_scale * ccy_factor
        return [Finding(
            SCALE_MISMATCH,
            f"Bloomberg's currency {ccy_raw!r} says {ticker!r} is quoted in {unit_words}, but our price_scale is "
            f"{_plain(root.price_scale)}; Bloomberg gave no value per point to confirm",
            f"set price_scale {_plain(root.price_scale)} -> {_plain(suggested)} (the multiplier becomes "
            f"{_g(root.multiplier * ccy_factor)}); a wrong scale is a 100x P&L error",
            "price_scale", _plain(root.price_scale), _plain(suggested))]
    ratio = vp / root.multiplier
    kind = ratio_kind(ratio)
    theirs = (f"Bloomberg values a 1.0 move of {ticker!r} at {_g(vp)} {major or root.currency} per contract "
              f"({vp_source})")
    if kind == "same":
        if ccy_factor is None:
            return []
        return [Finding(
            SCALE_MISMATCH, f"{theirs}, which agrees with {ours}{ccy_text}",
            f"the value per point and the currency disagree: check the quote on the terminal ({ticker} DES); "
            "no change is suggested until they agree")]
    if kind in ("x100", "x0.01"):
        factor = 100.0 if kind == "x100" else 0.01
        suggested = root.price_scale * factor
        conflict = ""
        if ccy_factor is not None and ccy_factor != factor:
            conflict = f"; note that its currency {ccy_raw!r} points the other way: check on the terminal"
        elif ccy_raw and ccy_factor is None:
            conflict = f"; note that its currency {ccy_raw!r} agrees with our price_scale: check on the terminal"
        return [Finding(
            SCALE_MISMATCH, f"{theirs}; {ours}: Bloomberg's figure is {ratio:.4g} times ours{ccy_text}{conflict}",
            f"set price_scale {_plain(root.price_scale)} -> {_plain(suggested)} (the multiplier becomes "
            f"{_g(root.multiplier * factor)}); a wrong scale is a 100x P&L error",
            "price_scale", _plain(root.price_scale), _plain(suggested))]
    implied = root.contract_size * ratio
    size = str(values.get("FUT_CONT_SIZE") or "").strip()
    units = str(values.get("FUT_TRADING_UNITS") or "").strip()
    quote_units = str(values.get("QUOTE_UNITS") or "").strip()
    bbg_size = f"; Bloomberg's contract size is {_g(size)} {units}".rstrip() if size else ""
    bbg_quote = f", quoted in {quote_units}" if quote_units else ""
    return [Finding(
        SCALE_MISMATCH, f"{theirs}; {ours}: Bloomberg's figure is {ratio:.4g} times ours{bbg_size}{bbg_quote}"
                        f"{ccy_text}",
        f"not a factor of 100, so not the price scale: review contract_size and the units in "
        f"config/contracts.csv (a contract_size of {_g(implied)} {root.size_unit} would agree); no change is "
        "suggested")]


def _name_problems(root: ContractRoot, values: Mapping[str, object]) -> List[str]:
    name = str(values.get("NAME") or "").strip()
    des = str(values.get("SECURITY_DES") or "").strip()
    front = str(values.get("FUT_CUR_GEN_TICKER") or "").strip()
    problems: List[str] = []
    generic = _GENERIC_NAME_RE.search(name)
    if generic:
        # A generic's NAME is its own ("Generic 1st 'CL' Future"): it names the root, not the product.
        if generic.group(1).strip().upper() != root.bbg_root.upper():
            problems.append(f"Bloomberg's name {name!r} is the generic of root {generic.group(1).strip()!r}, "
                            f"not {root.bbg_root!r}")
    elif name and not words_fit(root.name, f"{name} {des}"):
        problems.append(f"none of the words of our name {root.name!r} are in Bloomberg's name {name!r}")
    if front:
        compact = _strip_key(front).replace(" ", "")
        m = _CONTRACT_TICKER_RE.match(compact)
        if not m or m.group(1) != root.bbg_root.upper().replace(" ", ""):
            problems.append(f"Bloomberg's current front contract {front!r} is not a {root.bbg_root!r} contract")
    return problems


# --------------------------------------------------------------------------- search

def core_name(root: ContractRoot) -> str:
    """The product part of the name: 'DCE iron ore' -> 'iron ore', parentheses dropped."""
    words = re.sub(r"\([^)]*\)", " ", root.name).split()
    aliases = {root.exchange.upper()} | {w.upper() for w in EXCHANGE_WORDS.get(root.exchange, ())}
    while words and words[0].upper() in aliases:
        words.pop(0)
    return " ".join(words)


def search_queries(root: ContractRoot) -> List[str]:
    """Most specific first: 'DCE iron ore', then 'iron ore' (SEARCH_QUERIES_PER_ROOT at most)."""
    core = core_name(root)
    queries: List[str] = []
    for q in (f"{root.exchange} {core}", core):
        q = " ".join(q.split())
        if q and q.lower() not in (x.lower() for x in queries):
            queries.append(q)
    return queries[:SEARCH_QUERIES_PER_ROOT]


def parse_search_security(security: str, description: str) -> Optional[Tuple[str, str]]:
    """(root, 'generic' | 'contract') of a futures search hit, or None: 'IOE1<cmdty>' -> ('IOE',
    'generic'), 'IOEF7<cmdty>' -> ('IOE', 'contract'), 'C 1<cmdty>' -> ('C', 'generic'). A ticker
    that reads both ways ('AK1') is a generic when the description says 'Generic'."""
    ticker = _strip_key(security)
    if not ticker or len(ticker.split()) > 2:
        return None
    generic = _GENERIC_TICKER_RE.match(ticker)
    contract = _CONTRACT_TICKER_RE.match(ticker)
    if generic and "GENERIC" in str(description).upper():
        return generic.group(1), "generic"
    if contract:
        return contract.group(1), "contract"
    if generic:
        return generic.group(1), "generic"
    return None


def _padded_upper(text: str) -> str:
    return " " + re.sub(r"[^A-Z0-9]+", " ", str(text).upper()).strip() + " "


def rank_candidates(root: ContractRoot, hits: Iterable[Tuple[str, str]]) -> List[dict]:
    """Search hits grouped by Bloomberg root, scored 10 for naming the exchange and 2 per product word."""
    exch_words = [w.upper() for w in EXCHANGE_WORDS.get(root.exchange, (root.exchange,))]
    product = [w.upper() for w in _words(core_name(root))]
    by_root: Dict[str, dict] = {}
    seen = set()
    for security, description in hits:
        parsed = parse_search_security(security, description)
        if parsed is None or (security, description) in seen:
            continue
        seen.add((security, description))
        cand_root, _kind = parsed
        padded = _padded_upper(description)
        exchange_hit = any(f" {w} " in padded for w in exch_words)
        score = (10 if exchange_hit else 0) + 2 * sum(1 for w in product if w in padded)
        cand = by_root.setdefault(cand_root, {"root": cand_root, "score": -1, "example": "", "security": "",
                                              "hits": 0, "exchange_hit": False})
        cand["hits"] += 1
        cand["exchange_hit"] = cand["exchange_hit"] or exchange_hit
        if score > cand["score"]:
            cand["score"], cand["example"], cand["security"] = score, " ".join(str(description).split()), security
    return sorted(by_root.values(), key=lambda c: (-c["score"], -c["hits"], c["root"]))


def choose_candidate(ranked: List[dict]) -> Tuple[str, str]:
    """(root, status): a root only when it names the exchange and beats every rival by 2 or more."""
    if not ranked:
        return "", "no results"
    top = ranked[0]
    if not top["exchange_hit"]:
        return "", "no result names the exchange: pick by hand"
    rivals = [c["root"] for c in ranked[1:] if c["exchange_hit"] and top["score"] - c["score"] < 2]
    if rivals:
        return "", f"ambiguous: {top['root']} or {', '.join(rivals)}"
    return top["root"], "one candidate stands out"


def _candidate_text(c: dict) -> str:
    return f"{c['root']} (score {c['score']}, {c['hits']} hit{'s' if c['hits'] != 1 else ''}: {c['example']})"


# --------------------------------------------------------------------------- the book

@dataclass
class BookFuture:
    """One unexpired commodity future the book holds, as the database has it."""

    instrument_id: str
    root_id: str
    currency: str
    multiplier: float
    ticker: str                                   # '' when it cannot be asked
    why_no_ticker: str
    expiry: str                                   # instruments.expiry_date
    trades: List[Tuple[str, str, float, float]]   # (trade_id, trade_date, quantity, fill)
    stored: Optional[dict]                        # contract_static row, None when not stored

    @property
    def net_lots(self) -> float:
        return sum(q for _t, _d, q, _p in self.trades)

    @property
    def avg_fill(self) -> Optional[float]:
        weight = sum(abs(q) for _t, _d, q, _p in self.trades)
        if weight <= 0:
            return None
        return sum(abs(q) * p for _t, _d, q, p in self.trades) / weight


@dataclass
class SpotNeed:
    instrument_id: str
    ticker: str
    trade_ids: Set[str]


@dataclass
class Book:
    db_path: Path
    as_of: date
    futures: List[BookFuture]
    spots: List[SpotNeed]
    spot_error: str = ""                          # why the library's conversion spots could not be read

    @property
    def held_roots(self) -> Set[str]:
        return {f.root_id for f in self.futures}


_BOOK_SQL = """
SELECT i.instrument_id, i.base_ccy, i.quote_ccy, i.multiplier, i.bbg_ticker, i.expiry_date,
       t.trade_id, t.trade_date, t.quantity, t.price
FROM {trades} t JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE i.asset_class = 'FUTURE' AND t.product = 'FUTURE' AND t.trade_date <= ? AND i.expiry_date >= ?
ORDER BY i.instrument_id, t.trade_date, t.trade_id
"""


def _has_object(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def default_db_path() -> Path:
    """The app's database: RISK_DB, else data/raw/risk.db (the rule of ui/app.py::get_db_path)."""
    raw = os.environ.get("RISK_DB")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else REPO_ROOT / p
    return DEFAULT_DB


def load_book(db_path, as_of: date, roots: Optional[Mapping[str, ContractRoot]] = None) -> Book:
    """The book's unexpired commodity futures and the USD conversion spots the library lists for
    ``as_of``, read through a read-only connection. Raises BookError when the file is missing or
    holds no book."""
    path = Path(db_path)
    if not path.exists():
        raise BookError(f"no database at {path}")
    roots = dict(roots) if roots is not None else load_roots()
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60.0)
    except sqlite3.Error as exc:
        raise BookError(f"cannot open {path} read-only: {exc}") from None
    try:
        if not _has_object(conn, "instruments") or not _has_object(conn, "trades"):
            raise BookError(f"{path} holds no book (no instruments or trades table): upload a blotter first")
        trades_table = "trades_official" if _has_object(conn, "trades_official") else "trades"
        iso = as_of.isoformat()
        futures: Dict[str, BookFuture] = {}
        for (iid, base, quote, multiplier, ticker, expiry, trade_id, trade_date, qty, price) in conn.execute(
                _BOOK_SQL.format(trades=trades_table), (iso, iso)):
            root = roots.get(str(base or "").strip())
            if root is None:
                continue              # not a commodity contract (an equity index future: base_ccy 'ES')
            fut = futures.get(iid)
            if fut is None:
                ticker, why = _book_ticker(root, str(iid), str(ticker or "").strip(), as_of)
                fut = futures[iid] = BookFuture(
                    instrument_id=str(iid), root_id=root.root_id, currency=str(quote or root.currency),
                    multiplier=float(multiplier or root.multiplier), ticker=ticker, why_no_ticker=why,
                    expiry=str(expiry or ""), trades=[], stored=static_dates(conn, str(iid)))
            fut.trades.append((str(trade_id), str(trade_date), float(qty or 0.0), float(price or 0.0)))
        spots, spot_error = _conversion_spots(conn, as_of)
    except sqlite3.Error as exc:
        raise BookError(f"cannot read the book in {path}: {exc}") from None
    finally:
        conn.close()
    return Book(path, as_of, list(futures.values()), spots, spot_error)


def _book_ticker(root: ContractRoot, instrument_id: str, stored: str, as_of: date) -> Tuple[str, str]:
    """(ticker to ask, '' ) or ('', why not): the instrument's own bbg_ticker; when it has none,
    Bloomberg's request form from the root as config/contracts.csv now has it."""
    if stored:
        return stored, ""
    if root.bbg_placeholder:
        return "", (f"no Bloomberg ticker: {root.root_id}'s bbg_root {root.bbg_root!r} is a placeholder; "
                    "fix the root, then upload the blotter again")
    try:
        return request_ticker(contract_for(root.root_id, instrument_id), as_of), ""
    except (UnknownContract, KeyError, ValueError) as exc:
        return "", (f"the instrument has no Bloomberg ticker and its id does not fit {root.root_id}'s current "
                    f"root ({exc}); upload the blotter again so it takes the root's ticker")


def _conversion_spots(conn: sqlite3.Connection, as_of: date) -> Tuple[List[SpotNeed], str]:
    """The USD conversion SPOTs the Bloomberg library lists for ``as_of`` (role CONVERSION)."""
    try:
        from data.bloomberg import library
        needed = library.needed_on(conn, as_of.isoformat())
        role = getattr(library, "ROLE_CONVERSION", "CONVERSION")
    except Exception as exc:  # noqa: BLE001 -- a library problem must not stop the check; it is reported
        return [], f"the Bloomberg library could not be read ({type(exc).__name__}: {exc})"
    by_ticker: Dict[str, SpotNeed] = {}
    for r in needed:
        if r.get("kind") == "SPOT" and r.get("role") == role and r.get("bbg_ticker"):
            need = by_ticker.setdefault(r["bbg_ticker"], SpotNeed(str(r["key"]), str(r["bbg_ticker"]), set()))
            need.trade_ids.add(str(r["trade_id"]))
    return sorted(by_ticker.values(), key=lambda s: s.ticker), ""


# --------------------------------------------------------------------------- the plan

@dataclass
class Plan:
    """What a check would ask Bloomberg, worked out without asking anything."""

    roots: List[ContractRoot]                 # every selected root
    asked: List[ContractRoot]                 # the ones whose generic is asked
    placeholders: List[ContractRoot]          # never asked
    book: Optional[Book] = None
    futures: List[BookFuture] = field(default_factory=list)   # the book's futures in scope
    spots: List[SpotNeed] = field(default_factory=list)       # the conversion spots in scope
    search: bool = False
    batch_size: int = BATCH_SIZE

    @property
    def root_tickers(self) -> List[str]:
        return [generic_ticker(r) for r in self.asked]

    @property
    def contract_tickers(self) -> List[str]:
        return list(dict.fromkeys(f.ticker for f in self.futures if f.ticker))

    @property
    def spot_tickers(self) -> List[str]:
        return list(dict.fromkeys(s.ticker for s in self.spots))

    @property
    def securities(self) -> int:
        return len(self.root_tickers) + len(self.contract_tickers) + len(self.spot_tickers)

    @property
    def requests(self) -> int:
        size = max(1, self.batch_size)
        return sum(math.ceil(len(t) / size) for t in (self.root_tickers, self.contract_tickers, self.spot_tickers))

    @property
    def max_searches(self) -> int:
        if not self.search:
            return 0
        return sum(len(search_queries(r)) for r in self.asked + self.placeholders)

    @property
    def needs_bloomberg(self) -> bool:
        return self.securities > 0 or self.max_searches > 0

    def describe(self) -> List[str]:
        lines = [f"{len(self.roots)} contract root{'s' if len(self.roots) != 1 else ''} selected: "
                 f"{len(self.asked)} asked on their generic ticker, {len(self.placeholders)} placeholder"
                 f"{'s' if len(self.placeholders) != 1 else ''} not asked (NOT_FOUND as they stand)."]
        if self.book is not None:
            unaskable = sum(1 for f in self.futures if not f.ticker)
            lines.append(f"Book {self.book.db_path} on {self.book.as_of.isoformat()}: {len(self.futures)} unexpired "
                         f"commodity future{'s' if len(self.futures) != 1 else ''} ({len(self.contract_tickers)} "
                         f"ticker{'s' if len(self.contract_tickers) != 1 else ''} to ask, {unaskable} without one), "
                         f"{len(self.spot_tickers)} USD conversion spot{'s' if len(self.spot_tickers) != 1 else ''}.")
            if self.book.spot_error:
                lines.append(f"Conversion spots not checked: {self.book.spot_error}.")
        lines.append(f"{self.securities} securit{'ies' if self.securities != 1 else 'y'} in {self.requests} "
                     f"ReferenceDataRequest{'s' if self.requests != 1 else ''} (up to {self.batch_size} each).")
        if self.search:
            lines.append(f"Search: up to {self.max_searches} security searches on {INSTRUMENTS_SERVICE}, only for the "
                         "roots Bloomberg does not know; no data fields.")
        return lines


def _select(roots: Sequence[ContractRoot], root_ids: Sequence[str], sector: Optional[str]) -> List[ContractRoot]:
    by_id = {r.root_id: r for r in roots}
    if root_ids:
        picked: List[ContractRoot] = []
        for raw in root_ids:
            key = re.sub(r"\s+", "", str(raw or "")).upper()
            if key not in by_id:
                close = difflib.get_close_matches(key, list(by_id), n=5, cutoff=0.6)
                close += [r for r in by_id if r.split(":")[-1] == key.split(":")[-1] and r not in close]
                hint = f"; close matches: {', '.join(close)}" if close else ""
                raise ValueError(f"unknown contract root {raw!r}{hint}")
            if by_id[key] not in picked:
                picked.append(by_id[key])
        roots = picked
    if sector:
        roots = [r for r in roots if r.sector.lower() == sector.strip().lower()]
    return list(roots)


def plan(roots: Optional[Iterable[ContractRoot]] = None, *, root_ids: Sequence[str] = (), sector: Optional[str] = None,
         book: Optional[Book] = None, book_only: bool = False, limit: Optional[int] = None, search: bool = False,
         batch_size: int = BATCH_SIZE) -> Plan:
    """What the check would ask: the roots selected (``root_ids``, ``sector``, ``book_only``: only
    the roots the book holds; then the first ``limit``), the book's futures and conversion spots
    in scope (a root or sector filter narrows them too; ``limit`` does not), and the request count.
    Asks nothing. Raises ValueError for an unknown root id."""
    all_roots = list((roots.values() if isinstance(roots, Mapping) else roots) if roots is not None
                     else load_roots().values())
    selected = _select(all_roots, root_ids, sector)
    if book_only:
        held = book.held_roots if book is not None else set()
        selected = [r for r in selected if r.root_id in held]
    if limit is not None:
        selected = selected[:max(0, int(limit))]
    out = Plan(roots=selected, asked=[r for r in selected if not r.bbg_placeholder],
               placeholders=[r for r in selected if r.bbg_placeholder], book=book, search=search,
               batch_size=batch_size)
    if book is not None:
        narrowed = bool(root_ids or sector)
        in_scope = {r.root_id for r in _select(all_roots, root_ids, sector)} if narrowed else None
        out.futures = [f for f in book.futures if in_scope is None or f.root_id in in_scope]
        if in_scope is None:
            out.spots = list(book.spots)
        else:
            trade_ids = {t[0] for f in out.futures for t in f.trades}
            out.spots = [s for s in book.spots if s.trade_ids & trade_ids]
    return out


# --------------------------------------------------------------------------- Bloomberg client

class BlpapiClient:
    """One Bloomberg session for the check: reference data on //blp/refdata (through
    ``pull_marks.fetch_reference``, the app's own request helper) and the security search on
    //blp/instruments. Raises BloombergUnavailable, with the reason, when it cannot connect."""

    def __init__(self, host: str = "localhost", port: int = 8194):
        try:
            import blpapi
        except ImportError as exc:
            raise BloombergUnavailable(f"blpapi is not installed on this computer ({exc}); on the Bloomberg PC "
                                       "'py 2_launcher.py setup' installs it") from None
        from data.bloomberg import pull_marks
        from data.bloomberg.live import availability
        ok, why = availability(host, port, timeout=2.0)
        if not ok:
            raise BloombergUnavailable(f"{why}. Is the Bloomberg Terminal running and logged in on this PC?")
        self._blpapi = blpapi
        self._pm = pull_marks
        self.diag = pull_marks.Diagnostics()
        try:
            self.session, self.service = pull_marks.open_session(host, port, diag=self.diag)
        except Exception as exc:  # noqa: BLE001 -- any failure to open is "unreachable", with its words
            raise BloombergUnavailable(f"no Bloomberg session on {host}:{port} ({exc}). Is the Terminal running "
                                       "and logged in?") from None
        self._instruments = None

    def reference(self, securities: Sequence[str], fields: Sequence[str]) -> Dict[str, Answer]:
        timed_out = ""
        try:
            self._pm.fetch_reference(self.session, self.service, list(securities), list(fields), diag=self.diag,
                                     tag={"purpose": PURPOSE})
        except self._pm.BloombergRequestError as exc:
            timed_out = exc.detail or exc.classification
        except Exception as exc:  # noqa: BLE001 -- the session failed under the request: unreachable
            raise BloombergUnavailable(f"the reference request failed ({type(exc).__name__}: {exc})") from None
        raw = (self.diag.requests[-1].get("raw_response") if self.diag.requests else None) or []
        out: Dict[str, Answer] = {}
        for sec in raw:
            err = sec.get("securityError") or None
            out[str(sec.get("security"))] = Answer(
                security=str(sec.get("security")), fields=dict(sec.get("fieldData") or {}),
                security_error=str(err.get("message") or "security error") if err else "",
                field_errors={str(fx.get("fieldId") or "?"): str(fx.get("message") or "")
                              for fx in sec.get("fieldExceptions") or []})
        if timed_out:
            for s in securities:
                out.setdefault(s, Answer(s, {}, security_error=f"no answer: {timed_out}", answered=False))
        return out

    def search(self, query: str, max_results: int = SEARCH_MAX_RESULTS) -> List[Tuple[str, str]]:
        blpapi = self._blpapi
        if self._instruments is None:
            if not self.session.openService(INSTRUMENTS_SERVICE):
                raise SearchError(f"could not open {INSTRUMENTS_SERVICE}")
            self._instruments = self.session.getService(INSTRUMENTS_SERVICE)
        request = self._instruments.createRequest("instrumentListRequest")
        request.set("query", query)
        request.set("yellowKeyFilter", YELLOW_KEY_FILTER)
        request.set("languageOverride", "LANG_OVERRIDE_NONE")
        request.set("maxResults", int(max_results))
        cid = blpapi.CorrelationId(next(_SEARCH_CIDS))
        self.session.sendRequest(request, correlationId=cid)
        out: List[Tuple[str, str]] = []
        while True:
            event = self.session.nextEvent(SEARCH_TIMEOUT_MS)
            etype = event.eventType()
            if etype == getattr(blpapi.Event, "TIMEOUT", None):
                raise SearchError(f"search for {query!r} timed out after {SEARCH_TIMEOUT_MS // 1000} s")
            ours = False
            for msg in event:
                cids = list(msg.correlationIds()) if hasattr(msg, "correlationIds") else []
                if cids and cid not in cids:
                    continue          # a late answer to another request
                ours = True
                if msg.hasElement("responseError"):
                    raise SearchError(f"search for {query!r}: Bloomberg said {msg.getElement('responseError')}")
                if not msg.hasElement("results"):
                    continue
                results = msg.getElement("results")
                for i in range(results.numValues()):
                    item = results.getValueAsElement(i)
                    security = item.getElementAsString("security") if item.hasElement("security") else ""
                    description = item.getElementAsString("description") if item.hasElement("description") else ""
                    out.append((security, description))
            if etype == blpapi.Event.RESPONSE and ours:
                return out

    def close(self) -> None:
        try:
            self.session.stop()
        except Exception:  # noqa: BLE001 -- closing must never mask the check's own outcome
            pass


# --------------------------------------------------------------------------- the check

@dataclass
class ContractResult:
    future: BookFuture
    answer: Optional[Answer]
    findings: List[Finding]
    notes: List[str] = field(default_factory=list)
    px_last: Optional[float] = None
    fill_ratio: Optional[float] = None
    bbg_last_trade: str = ""
    bbg_first_notice: str = ""

    @property
    def verdict(self) -> str:
        if not self.findings:
            return OK
        return min((f.verdict for f in self.findings), key=BOOK_VERDICTS.index)


@dataclass
class SpotResult:
    need: SpotNeed
    answer: Optional[Answer]
    findings: List[Finding]
    px_last: Optional[float] = None

    @property
    def verdict(self) -> str:
        if not self.findings:
            return OK
        return min((f.verdict for f in self.findings), key=BOOK_VERDICTS.index)


@dataclass
class CheckResult:
    plan: Plan
    roots: List[RootResult]
    contracts: List[ContractResult]
    spots: List[SpotResult]
    requests_sent: int = 0
    searches_sent: int = 0
    answers: Dict[str, Tuple[str, Answer]] = field(default_factory=dict)   # security -> (part, answer)
    host: str = ""

    def counts(self) -> Dict[str, int]:
        out = {v: 0 for v in ROOT_VERDICTS}
        for r in self.roots:
            out[r.verdict] += 1
        return out

    @property
    def needs_attention(self) -> bool:
        return (any(r.needs_attention for r in self.roots) or any(c.verdict != OK for c in self.contracts)
                or any(s.verdict != OK for s in self.spots))

    @property
    def any_answered(self) -> bool:
        return any(a.answered for _part, a in self.answers.values())


def _ask(client, tickers: Sequence[str], fields: Sequence[str], batch_size: int, result: CheckResult,
         part: str) -> Dict[str, Answer]:
    out: Dict[str, Answer] = {}
    for batch in _chunks(list(tickers), max(1, batch_size)):
        result.requests_sent += 1
        got = client.reference(batch, fields) or {}
        by_upper = {str(k).upper(): v for k, v in got.items()}
        for t in batch:
            answer = got.get(t) or by_upper.get(t.upper()) or Answer(
                t, {}, security_error="Bloomberg sent nothing back for this security", answered=False)
            out[t] = answer
            result.answers[t] = (part, answer)
    return out


def _check_contract(fut: BookFuture, answer: Optional[Answer]) -> ContractResult:
    if not fut.ticker:
        return ContractResult(fut, None, [Finding(NO_TICKER, fut.why_no_ticker, "nothing was asked for this contract")])
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        return ContractResult(fut, answer, [Finding(NO_ANSWER, f"Bloomberg did not answer for {fut.ticker!r} "
                                                               f"({words})", "run the check again")])
    if answer.security_error:
        return ContractResult(fut, answer, [_security_error_finding(fut.ticker, answer, "this contract")])
    values = answer.fields
    res = ContractResult(fut, answer, [])
    res.bbg_last_trade = _date_text(values.get("FUT_LAST_TRADE_DT"))
    res.bbg_first_notice = _date_text(values.get("FUT_NOTICE_FIRST"))
    px = _num(values.get("PX_LAST"))
    res.px_last = px
    if px is None:
        res.findings.append(Finding(NO_PRICE, f"{fut.ticker!r} resolves but returned no PX_LAST"
                                              + _bloomberg_said(answer, ("PX_LAST",)),
                                    f"an entitlement this terminal lacks, or a dead contract: look at {fut.ticker} GP"))
    else:
        _fill_findings(res, fut, px)
    _date_findings(res, fut)
    return res


def _fill_findings(res: ContractResult, fut: BookFuture, px: float) -> None:
    fill = fut.avg_fill
    if fill is None or px == 0 or fill == 0:
        return
    res.fill_ratio = abs(fill) / abs(px)
    kinds = {}
    for trade_id, _d, _q, price in fut.trades:
        if price and px:
            kinds.setdefault(fill_ratio_kind(abs(price) / abs(px)), []).append(trade_id)
    scaled = kinds.get("x100", []) + kinds.get("x0.01", [])
    fills = ", ".join(_g(p) for _t, _d, _q, p in fut.trades[:5]) + (" ..." if len(fut.trades) > 5 else "")
    if scaled:
        which = "about 100 times" if kinds.get("x100") else "about a hundredth of"
        res.findings.append(Finding(
            SCALE_FLAG,
            f"the blotter's fills ({fills}) are {which} Bloomberg's PX_LAST {_g(px)} (trade"
            f"{'s' if len(scaled) != 1 else ''} {', '.join(scaled)})",
            "the blotter and Bloomberg quote this contract in units 100 times apart (dollars against cents, "
            f"pounds against pence): P&L = contracts x {_g(fut.multiplier)} x (mark - fill) would be 100 times off. "
            "Check the root's price_scale in the worksheet and the blotter's price unit before trusting its P&L"))
    elif kinds.get("other"):
        res.findings.append(Finding(
            PRICE_GAP,
            f"the blotter's fills ({fills}) are far from Bloomberg's PX_LAST {_g(px)} (trades "
            f"{', '.join(kinds['other'])}), but not by a factor of 100",
            "check that the ticker is the contract traded and that the blotter's price is in Bloomberg's unit"))


def _date_findings(res: ContractResult, fut: BookFuture) -> None:
    bbg_ltd, bbg_fnd = res.bbg_last_trade, res.bbg_first_notice
    stored = fut.stored
    if stored is None:
        if bbg_ltd:
            res.notes.append(f"Bloomberg's dates are not stored yet (last trade {bbg_ltd}, first notice "
                             f"{bbg_fnd or 'none'}); the next Pull Bloomberg now stores them")
    else:
        diffs = []
        if bbg_ltd and stored.get("last_trade_date") != bbg_ltd:
            diffs.append(f"last trade {stored.get('last_trade_date')} stored, {bbg_ltd} on Bloomberg")
        if bbg_fnd and (stored.get("first_notice_date") or "") != bbg_fnd:
            diffs.append(f"first notice {stored.get('first_notice_date') or 'none'} stored, {bbg_fnd} on Bloomberg")
        if diffs:
            res.findings.append(Finding(
                DATE_MISMATCH,
                "; ".join(diffs) + f" (stored {stored.get('fetched_at', '')} from {stored.get('source', '')})",
                "the stored contract dates are not Bloomberg's current ones and a pull does not ask again once "
                "they are stored: this check writes nothing, so ask for them to be replaced (contract-master)"))
    if bbg_ltd and fut.expiry and fut.expiry != bbg_ltd:
        res.notes.append(f"the instrument's expiry is {fut.expiry}, Bloomberg's last trade date {bbg_ltd}")


def _check_spot(need: SpotNeed, answer: Optional[Answer]) -> SpotResult:
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        return SpotResult(need, answer, [Finding(NO_ANSWER, f"Bloomberg did not answer for {need.ticker!r} ({words})",
                                                 "run the check again")])
    if answer.security_error:
        return SpotResult(need, answer, [_security_error_finding(need.ticker, answer, "this currency pair")])
    px = _num(answer.fields.get("PX_LAST"))
    if px is None:
        return SpotResult(need, answer, [Finding(NO_PRICE, f"{need.ticker!r} returned no PX_LAST"
                                                           + _bloomberg_said(answer, ("PX_LAST",)),
                                                 "the USD conversion of this currency's futures has no spot")])
    return SpotResult(need, answer, [], px)


def _search_root(client, result: RootResult) -> None:
    hits: List[Tuple[str, str]] = []
    for query in search_queries(result.root):
        hits.extend(client.search(query, SEARCH_MAX_RESULTS))
    result.candidates = rank_candidates(result.root, hits)
    _found, status = choose_candidate(result.candidates)
    result.search_status = status
    if not result.candidates:
        result.search_status = f"no futures among {len(hits)} search result{'s' if len(hits) != 1 else ''}"


def run_check(client, the_plan: Plan, *, log: Callable[[str], None] = print, host: str = "") -> CheckResult:
    """Ask Bloomberg what ``the_plan`` lists, through ``client`` (``reference(securities, fields)``
    -> {security: Answer}; ``search(query, max_results)`` -> [(security, description)]), and
    judge every answer. Writes nothing."""
    result = CheckResult(the_plan, [], [], [], host=host)
    size = the_plan.batch_size
    root_answers = _ask(client, the_plan.root_tickers, ROOT_FIELDS, size, result, "root") if the_plan.asked else {}
    for root in the_plan.roots:
        result.roots.append(check_root(root, None if root.bbg_placeholder else root_answers.get(generic_ticker(root))))
    if the_plan.search:
        errors = 0
        for r in result.roots:
            if r.verdict != NOT_FOUND:
                continue
            if errors >= MAX_SEARCH_ERRORS:
                r.search_status = f"not searched: {MAX_SEARCH_ERRORS} searches in a row failed"
                continue
            try:
                result.searches_sent += len(search_queries(r.root))
                _search_root(client, r)
                errors = 0
            except SearchError as exc:
                errors += 1
                r.search_status = f"search failed: {exc}"
                log(f"  search for {r.root.root_id} failed: {exc}")
    if the_plan.book is not None:
        contract_answers = _ask(client, the_plan.contract_tickers, CONTRACT_FIELDS, size, result, "contract")
        for fut in the_plan.futures:
            result.contracts.append(_check_contract(fut, contract_answers.get(fut.ticker) if fut.ticker else None))
        spot_answers = _ask(client, the_plan.spot_tickers, SPOT_FIELDS, size, result, "spot")
        for need in the_plan.spots:
            result.spots.append(_check_spot(need, spot_answers.get(need.ticker)))
    return result


# --------------------------------------------------------------------------- reports

def worksheet_rows(result: CheckResult) -> List[Dict[str, str]]:
    """One row per suggested change, WORKSHEET_COLUMNS; ``apply`` is 'yes' only on an OK root's
    bbg_verified row and blank otherwise."""
    rows: List[Dict[str, str]] = []

    def add(r: RootResult, fld: str, current: str, suggested: str, verdict: str, reason: str, apply: str = "") -> None:
        rows.append({"root_id": r.root.root_id, "field": fld, "current": current, "suggested": suggested,
                     "verdict": verdict, "reason": reason, "apply": apply})

    for r in result.roots:
        verdict = r.verdict
        if verdict == OK and not r.root.bbg_verified:
            add(r, "bbg_verified", "false", "true", OK, _ok_reason(r), "yes")
        elif verdict in WARNING_VERDICTS and not r.root.bbg_verified:
            add(r, "bbg_verified", "false", "true", verdict,
                "resolves and prices, but: " + "; ".join(f.evidence for f in r.findings))
        elif verdict == NOT_FOUND and r.root.bbg_verified:
            add(r, "bbg_verified", "true", "false", NOT_FOUND, r.findings[0].evidence)
        for f in r.findings:
            if f.field:
                add(r, f.field, f.current, f.suggested, f.verdict, f"{f.evidence}. {f.action}")
        if r.candidates:
            found, status = choose_candidate(r.candidates)
            picks = [c for c in r.candidates if c["root"] == found] if found else \
                [c for c in r.candidates if c["score"] > 0][:CANDIDATE_ROWS]
            for c in picks:
                if c["root"] != r.root.bbg_root:
                    add(r, "bbg_root", r.root.bbg_root, c["root"], NOT_FOUND,
                        f"search ({status}): {_candidate_text(c)}")
    return rows


def _ok_reason(r: RootResult) -> str:
    values = r.answer.fields if r.answer is not None else {}
    bits = [f"{r.ticker} resolves as {str(values.get('NAME') or '').strip()!r}"]
    if values.get("EXCH_CODE"):
        bits.append(f"exchange {values.get('EXCH_CODE')}")
    if values.get("CRNCY"):
        bits.append(f"currency {values.get('CRNCY')}")
    vp, _src = value_per_point(values)
    if vp is not None:
        bits.append(f"a 1.0 move is worth {_g(vp)}, our multiplier {_g(r.root.multiplier)}")
    return ", ".join(bits)


def _identity(answer: Optional[Answer]) -> str:
    if answer is None or not answer.answered or answer.security_error:
        return ""
    v = answer.fields
    bits = []
    for label, key in (("name", "NAME"), ("exchange", "EXCH_CODE"), ("currency", "CRNCY"),
                       ("contract size", "FUT_CONT_SIZE"), ("units", "FUT_TRADING_UNITS"), ("quoted", "QUOTE_UNITS"),
                       ("1.0 move", "FUT_VAL_PT"), ("front", "FUT_CUR_GEN_TICKER"), ("PX_LAST", "PX_LAST")):
        if v.get(key) not in (None, ""):
            shown = _g(v.get(key)) if key in ("FUT_CONT_SIZE", "FUT_VAL_PT", "PX_LAST") else v.get(key)
            bits.append(f"{label} {shown}")
    return "Bloomberg: " + ", ".join(bits) if bits else ""


def render_text(result: CheckResult, *, now: datetime, paths: Mapping[str, Path]) -> str:
    """The plain-English report."""
    p = result.plan
    lines = [f"Bloomberg ticker check, {now:%Y-%m-%d %H:%M}" + (f" ({result.host})" if result.host else ""), ""]
    lines += p.describe()
    lines.append(f"Sent {result.requests_sent} reference request{'s' if result.requests_sent != 1 else ''}"
                 + (f" and {result.searches_sent} searches" if p.search else "") + ".")
    lines += ["", "Summary of the contract roots"]
    counts = result.counts()
    for v in ROOT_VERDICTS:
        if counts[v] or v in (OK, NOT_FOUND, NO_PRICE, CURRENCY_MISMATCH, SCALE_MISMATCH):
            lines.append(f"  {v:<18} {counts[v]:>4}")
    ok_ids = [r.root.root_id for r in result.roots if r.verdict == OK]
    if ok_ids:
        lines += [""] + textwrap.wrap("OK: " + ", ".join(ok_ids), width=110, subsequent_indent="    ")
    attention = [r for r in result.roots if r.needs_attention]
    if attention:
        lines += ["", f"Roots that need attention ({len(attention)})"]
    for r in attention:
        lines += ["", f"{r.root.root_id}  {r.root.name}  [{r.ticker}]  {r.verdict}"]
        ident = _identity(r.answer)
        if ident:
            lines.append(f"  {ident}.")
        for f in r.findings:
            lines.append(f"  - {f.verdict}: {f.evidence}. What to do: {f.action}.")
        for n in r.notes:
            lines.append(f"  - note: {n}.")
        if r.search_status:
            lines.append(f"  - search: {r.search_status}.")
        for c in r.candidates[:CANDIDATES_SHOWN]:
            lines.append(f"      candidate {_candidate_text(c)}")
    noted = [r for r in result.roots if r.verdict == OK and r.notes]
    if noted:
        lines += ["", "OK, with notes"]
        for r in noted:
            lines.append(f"  {r.root.root_id} [{r.ticker}]: " + "; ".join(r.notes) + ".")
    if p.book is not None:
        lines += ["", *_render_book(result)]
    lines += ["", "Files"]
    for label, path in paths.items():
        lines.append(f"  {label}: {path}")
    lines += ["", "Nothing was written to the marks, the trades or config/contracts.csv. Mark 'yes' in the "
              "worksheet's apply column for each change you accept, then apply it "
              "(py 2_launcher.py contracts-apply <worksheet>)."]
    return "\n".join(lines) + "\n"


def _render_book(result: CheckResult) -> List[str]:
    p = result.plan
    book = p.book
    flagged = [c for c in result.contracts if c.verdict != OK]
    lines = [f"Book check ({book.db_path}, book date {book.as_of.isoformat()})",
             f"  {len(result.contracts)} unexpired commodity future{'s' if len(result.contracts) != 1 else ''}, "
             f"{len(flagged)} flagged; {len(result.spots)} USD conversion spot{'s' if len(result.spots) != 1 else ''}, "
             f"{sum(1 for s in result.spots if s.verdict != OK)} flagged."]
    if result.contracts:
        header = (f"  {'Contract':<16} {'Ticker':<16} {'Lots':>6} {'Avg fill':>12} {'PX_LAST':>12} {'Fill/PX':>8}  "
                  f"{'Last trade stored / Bloomberg':<25}  {'First notice stored / Bloomberg':<25}  Verdict")
        lines += ["", header]
        for c in result.contracts:
            f = c.future
            stored = f.stored or {}
            ltd = f"{stored.get('last_trade_date') or 'not stored'} / {c.bbg_last_trade or '-'}"
            fnd_stored = (stored.get("first_notice_date") or "none") if stored else "not stored"
            fnd = f"{fnd_stored} / {c.bbg_first_notice or '-'}"
            ratio = f"{c.fill_ratio:.3g}" if c.fill_ratio is not None else "-"
            lines.append(f"  {f.instrument_id:<16} {f.ticker or '-':<16} {f.net_lots:>+6g} {_g(f.avg_fill):>12} "
                         f"{_g(c.px_last):>12} {ratio:>8}  {ltd:<30}  {fnd:<30}  {c.verdict}")
    for c in flagged:
        fut = c.future
        lines += ["", f"  {fut.instrument_id} ({fut.root_id}) [{fut.ticker or 'no ticker'}]  {c.verdict}"]
        for fnd in c.findings:
            lines.append(f"    - {fnd.verdict}: {fnd.evidence}. What to do: {fnd.action}.")
        for n in c.notes:
            lines.append(f"    - note: {n}.")
    noted = [c for c in result.contracts if c.verdict == OK and c.notes]
    for c in noted:
        lines.append(f"  note on {c.future.instrument_id}: " + "; ".join(c.notes) + ".")
    if book.spot_error:
        lines += ["", f"  Conversion spots not checked: {book.spot_error}."]
    if result.spots:
        lines += ["", f"  {'Pair':<10} {'Ticker':<16} {'PX_LAST':>12}  Verdict"]
        for s in result.spots:
            lines.append(f"  {s.need.instrument_id:<10} {s.need.ticker:<16} {_g(s.px_last):>12}  {s.verdict}"
                         + (f": {s.findings[0].evidence}" if s.findings else ""))
    return lines


def _field_rows(result: CheckResult) -> Tuple[List[str], List[List[str]]]:
    columns = ["part", "root_id", "instrument_id", "security", "verdict", "answered", "security_error",
               "field_errors", *ROOT_FIELDS]
    rows: List[List[str]] = []

    def emit(part: str, root_id: str, instrument_id: str, security: str, verdict: str,
             answer: Optional[Answer]) -> None:
        a = answer or Answer(security, {}, answered=False)
        extra = [_text(a.fields.get(f)) for f in ROOT_FIELDS]
        errors = "; ".join(f"{k}: {v}" for k, v in a.field_errors.items())
        rows.append([part, root_id, instrument_id, security, verdict, "yes" if a.answered else "no",
                     a.security_error, errors, *extra])

    for r in result.roots:
        if r.asked:
            emit("root", r.root.root_id, "", r.ticker, r.verdict, r.answer)
    for c in result.contracts:
        if c.future.ticker:
            emit("contract", c.future.root_id, c.future.instrument_id, c.future.ticker, c.verdict, c.answer)
    for s in result.spots:
        emit("spot", "", s.need.instrument_id, s.need.ticker, s.verdict, s.answer)
    return columns, rows


def write_reports(result: CheckResult, out_dir, *, now: datetime) -> Dict[str, Path]:
    """Write the three files under ``out_dir``; returns {'report', 'fields', 'worksheet'} -> path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    paths = {"report": out / f"bbg_check_{stamp}.txt", "fields": out / f"bbg_check_{stamp}.csv",
             "worksheet": out / f"contract_fixes_{stamp}.csv"}
    columns, rows = _field_rows(result)
    with open(paths["fields"], "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)
    with open(paths["worksheet"], "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(WORKSHEET_COLUMNS))
        writer.writeheader()
        writer.writerows(worksheet_rows(result))
    paths["report"].write_text(render_text(result, now=now, paths=paths), encoding="utf-8")
    return paths


# --------------------------------------------------------------------------- command line

def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m data.bloomberg.ticker_check",
        description="Ask Bloomberg what each contract root's ticker really is and compare it with "
                    "config/contracts.csv. On request only; writes reports under reports/ and nothing else.")
    ap.add_argument("--dry-run", action="store_true", help="print what would be asked; ask nothing, write nothing")
    ap.add_argument("--root", action="append", default=[], metavar="ROOT_ID",
                    help="one contract root, e.g. NYMEX:CL (repeatable)")
    ap.add_argument("--sector", default=None, help="only the roots of this sector (energy, metals, ...)")
    ap.add_argument("--book", action="store_true",
                    help="only the roots the book holds; reads --db, else the app's database")
    ap.add_argument("--db", default=None, help="the app's database, for the book check (opened read-only)")
    ap.add_argument("--search", action="store_true",
                    help="search //blp/instruments for candidates for every root Bloomberg does not know")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8194)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="folder for the reports (default: reports/)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N roots")
    return ap


def main(argv: Optional[Sequence[str]] = None, *, client_factory: Optional[Callable[[str, int], object]] = None,
         now: Optional[datetime] = None, as_of: Optional[date] = None,
         roots: Optional[Iterable[ContractRoot]] = None) -> int:
    """The command line. Exit 0 when every checked root (and the book) is OK, 1 when anything
    needs attention, 2 when Bloomberg could not be reached (the reason is printed).
    ``client_factory(host, port)``, ``now``, ``as_of`` and ``roots`` are for tests."""
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.limit is not None and args.limit < 1:
        print("--limit must be 1 or more")
        return 1
    root_map = load_roots() if roots is None else {r.root_id: r for r in roots}
    book: Optional[Book] = None
    if args.db or args.book:
        if as_of is None:
            from data.bloomberg.live import book_today
            as_of = book_today()
        try:
            book = load_book(Path(args.db) if args.db else default_db_path(), as_of, root_map)
        except BookError as exc:
            print(f"Book check not possible: {exc}.")
            return 1
    try:
        the_plan = plan(root_map, root_ids=args.root, sector=args.sector, book=book, book_only=args.book,
                        limit=args.limit, search=args.search)
    except ValueError as exc:
        print(str(exc))
        return 1
    if not the_plan.roots and not the_plan.futures and not the_plan.spots:
        print("Nothing to check: no contract root matches the filters" + (" and the book holds none." if book else "."))
        return 1
    for line in the_plan.describe():
        print(line)
    if args.dry_run:
        print("Dry run: nothing was asked and nothing was written.")
        return 0

    client = None
    host = f"{args.host}:{args.port}"
    if the_plan.needs_bloomberg:
        print(f"Asking Bloomberg on {host} for {the_plan.securities} securities in {the_plan.requests} requests"
              + (f", and up to {the_plan.max_searches} searches" if the_plan.search else "") + ".")
        try:
            client = (client_factory or BlpapiClient)(args.host, args.port)
        except BloombergUnavailable as exc:
            print(f"Bloomberg could not be reached: {exc}")
            return 2
    try:
        result = run_check(client, the_plan, host=host)
    except BloombergUnavailable as exc:
        print(f"Bloomberg could not be reached: {exc}")
        return 2
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()
    if the_plan.securities and not result.any_answered:
        print(f"Bloomberg did not answer any request on {host}; nothing to judge. Run the check again.")
        return 2
    stamp_time = now or datetime.now()
    paths = write_reports(result, args.out, now=stamp_time)
    counts = result.counts()
    if result.roots:
        print("Roots: " + ", ".join(f"{counts[v]} {v}" for v in ROOT_VERDICTS if counts[v]) + ".")
    if book is not None:
        bad_contracts = sum(1 for c in result.contracts if c.verdict != OK)
        bad_spots = sum(1 for s in result.spots if s.verdict != OK)
        print(f"Book: {len(result.contracts)} contracts, {bad_contracts} flagged; "
              f"{len(result.spots)} spots, {bad_spots} flagged.")
    ws = worksheet_rows(result)
    print(f"Report:    {paths['report']}")
    print(f"Fields:    {paths['fields']}")
    print(f"Worksheet: {paths['worksheet']} ({len(ws)} rows, {sum(1 for w in ws if w['apply'] == 'yes')} "
          "pre-filled 'yes')")
    return 1 if result.needs_attention else 0


if __name__ == "__main__":
    sys.exit(main())
