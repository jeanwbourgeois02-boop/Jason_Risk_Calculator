"""The Bloomberg ticker check (bbg-ticker-check lane), run on request at the Bloomberg PC.

    python -m data.bloomberg.ticker_check --dry-run              # what would be asked; asks nothing
    python -m data.bloomberg.ticker_check --limit 5              # the first five roots first
    python -m data.bloomberg.ticker_check --sector energy --search
    python -m data.bloomberg.ticker_check --book --db data/raw/risk.db
    python -m data.bloomberg.ticker_check --lme --options         # only the LME curve and the options

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
4. LME curve (``--lme``, Phase 5): for each selected LME forward metal (``engine.lme.lme_roots``),
   its cash, 3-month and first monthly (third-Wednesday) ticker as ``engine.lme`` builds them,
   ``lme_fields()`` (NAME, CRNCY, PX_LAST and bbg-curves' prompt-date field), one batch. Our
   3M and monthly prompt dates are checked against Bloomberg's. Verdicts: NO_ANSWER, NOT_FOUND,
   NO_PRICE, CURRENCY_MISMATCH (not USD), DATE_MISMATCH, OK. The tickers and the prompt rules
   are code (lme-forwards), so an LME finding is a line for the housekeeper, never a worksheet row.
5. Options on futures (``--options``, Phase 5): for each selected root with an ``option_style``,
   the bulk field OPT_CHAIN on its generic front future (CHAIN_BATCH_SIZE per request), then
   one option of each chain (the middle strike of the nearest expiry) on OPTION_FIELDS, plus our
   own form of the same option where Bloomberg writes it differently. Verdicts: NO_ANSWER,
   NOT_FOUND, NO_OPTIONS, NO_PRICE, FORM_MISMATCH (our ticker form or strike scale is not
   Bloomberg's: code, contract-master), STYLE_MISMATCH and LEAD_MISMATCH (worksheet rows for
   ``option_style`` / ``option_lead_months``), DATE_MISMATCH (Bloomberg's expiry is later than
   our date), OK.
6. Book mode also checks the book's open CMDTY_OPTION instruments (BOOK_OPTION_FIELDS: price,
   fill against PX_LAST, OPT_EXPIRE_DT against the stored expiry) and places each open LME
   ticket's prompt on the metal's curve (no extra ask: the metal's LME tickers are asked).

A plain run does parts 1, 4 and 5; ``--lme`` and / or ``--options`` run only those parts. The
book check runs whenever a book is given.

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
import dataclasses
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

from data.contracts import (
    ContractRoot,
    UnknownContract,
    contract_for,
    format_strike,
    load_roots,
    make_option_id,
    request_ticker,
    static_dates,
)
from data.contracts.tickers import (
    expand_year,
    month_from_code,
    padded_root,
    parse_bbg_ticker,
    parse_option_ticker,
    to_date,
)

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

# Parts of a check. A plain run does all three; --lme / --options pick only those.
PART_ROOTS, PART_LME, PART_OPTIONS = "roots", "lme", "options"
ALL_PARTS = (PART_ROOTS, PART_LME, PART_OPTIONS)

# LME curve tickers (Phase 5). The prompt-date field is bbg-curves' (fwd_curve.LME_PROMPT_DATE_FIELD),
# read lazily; this fallback is the same unverified guess.
LME_BASE_FIELDS = ("NAME", "CRNCY", "PX_LAST")
LME_PROMPT_FIELD_FALLBACK = "FUT_DLV_DT_LAST"
LME_CURRENCY = "USD"
LME_OWNER = "lme-forwards"                # the lane whose code builds the LME tickers and prompt dates

# Options on futures (Phase 5). OPT_CHAIN is a bulk field: a long list per security, so fewer per request.
CHAIN_FIELD = "OPT_CHAIN"
CHAIN_BATCH_SIZE = 10
OPTION_FIELDS = ("NAME", "CRNCY", "PX_LAST", "OPT_EXPIRE_DT", "OPT_EXER_TYP", "OPT_STRIKE_PX", "OPT_UNDL_TICKER",
                 "OPT_UNDL_PX")
BOOK_OPTION_FIELDS = ("PX_LAST", "OPT_EXPIRE_DT", "OPT_EXER_TYP")
OPTION_OWNER = "contract-master"          # the lane whose code builds option tickers and dates
FORM_EXAMPLES = 3                         # chain tickers quoted in a form finding

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

# LME curve tickers (per pillar; a metal takes its worst pillar), the most severe first.
LME_VERDICTS = (NO_ANSWER, NOT_FOUND, NO_PRICE, CURRENCY_MISMATCH, DATE_MISMATCH, OK)

# Options on futures, per root, the most severe first.
NO_OPTIONS = "NO_OPTIONS"                 # OPT_CHAIN gave no option on the generic front future
FORM_MISMATCH = "FORM_MISMATCH"           # our option ticker form or strike scale is not Bloomberg's
STYLE_MISMATCH = "STYLE_MISMATCH"         # OPT_EXER_TYP against option_style
LEAD_MISMATCH = "LEAD_MISMATCH"           # OPT_EXPIRE_DT's month against option_lead_months
OPTION_VERDICTS = (NO_ANSWER, NOT_FOUND, NO_OPTIONS, NO_PRICE, FORM_MISMATCH, STYLE_MISMATCH, LEAD_MISMATCH,
                   DATE_MISMATCH, OK)

# The book's LME tickets placed on the metal's curve, the most severe first.
NO_CURVE = "NO_CURVE"                     # the metal's cash and 3M tickers gave no price
OFF_CURVE = "OFF_CURVE"                   # the prompt is beyond the curve's last pillar: no mark
NOT_A_PROMPT = "NOT_A_PROMPT"             # not an LME prompt date for the ticket's trade date
LME_BOOK_VERDICTS = (NO_CURVE, SCALE_FLAG, OFF_CURVE, NOT_A_PROMPT, OK)

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
    owner: str = ""        # the lane whose code must change (not config/contracts.csv): told to the housekeeper


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
    kind: str = "FUTURE"                          # 'FUTURE' | 'CMDTY_OPTION' (an option on a future)

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
class LmeTicket:
    """One open LME forward ticket of the book (product LME_FWD): its metal leg's prompt."""

    trade_id: str
    root_id: str
    trade_date: str
    prompt: str                                   # the metal leg's settle_date
    tonnes: float
    fill: float                                   # USD per tonne


@dataclass
class Book:
    db_path: Path
    as_of: date
    futures: List[BookFuture]
    spots: List[SpotNeed]
    spot_error: str = ""                          # why the library's conversion spots could not be read
    options: List[BookFuture] = field(default_factory=list)   # open CMDTY_OPTION instruments (kind CMDTY_OPTION)
    lme: List[LmeTicket] = field(default_factory=list)        # open LME_FWD tickets

    @property
    def held_roots(self) -> Set[str]:
        return {f.root_id for f in self.futures} | {o.root_id for o in self.options} | {t.root_id for t in self.lme}


_BOOK_SQL = """
SELECT i.instrument_id, i.base_ccy, i.quote_ccy, i.multiplier, i.bbg_ticker, i.expiry_date,
       t.trade_id, t.trade_date, t.quantity, t.price
FROM {trades} t JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE i.asset_class = 'FUTURE' AND t.product = 'FUTURE' AND t.trade_date <= ? AND i.expiry_date >= ?
ORDER BY i.instrument_id, t.trade_date, t.trade_id
"""

_BOOK_OPTION_SQL = """
SELECT i.instrument_id, i.base_ccy, i.quote_ccy, i.multiplier, i.bbg_ticker, i.expiry_date,
       t.trade_id, t.trade_date, t.quantity, t.price
FROM {trades} t JOIN instruments i ON i.instrument_id = t.instrument_id
WHERE i.asset_class = 'CMDTY_OPTION' AND t.product = 'CMDTY_OPTION' AND t.trade_date <= ? AND i.expiry_date >= ?
ORDER BY i.instrument_id, t.trade_date, t.trade_id
"""

_BOOK_LME_SQL = """
SELECT t.trade_id, t.instrument_id, t.trade_date, l.settle_date, t.quantity, t.price
FROM {trades} t JOIN trade_legs l ON l.trade_id = t.trade_id AND l.ccy = t.instrument_id
WHERE t.product = 'LME_FWD' AND t.trade_date <= ? AND l.settle_date >= ?
ORDER BY t.instrument_id, l.settle_date, t.trade_id
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
        options: Dict[str, BookFuture] = {}
        for (iid, base, quote, multiplier, ticker, expiry, trade_id, trade_date, qty, price) in conn.execute(
                _BOOK_OPTION_SQL.format(trades=trades_table), (iso, iso)):
            root = roots.get(str(base or "").strip())
            if root is None:
                continue
            opt = options.get(iid)
            if opt is None:
                ticker, why = _book_option_ticker(root, str(iid), str(ticker or "").strip(), as_of)
                opt = options[iid] = BookFuture(
                    instrument_id=str(iid), root_id=root.root_id, currency=str(quote or root.currency),
                    multiplier=float(multiplier or root.multiplier), ticker=ticker, why_no_ticker=why,
                    expiry=str(expiry or ""), trades=[], stored=static_dates(conn, str(iid)), kind="CMDTY_OPTION")
            opt.trades.append((str(trade_id), str(trade_date), float(qty or 0.0), float(price or 0.0)))
        lme = [LmeTicket(str(tid), str(iid), str(td), str(prompt), float(qty or 0.0), float(price or 0.0))
               for tid, iid, td, prompt, qty, price in conn.execute(_BOOK_LME_SQL.format(trades=trades_table),
                                                                    (iso, iso))
               if str(iid) in roots]
        spots, spot_error = _conversion_spots(conn, as_of)
    except sqlite3.Error as exc:
        raise BookError(f"cannot read the book in {path}: {exc}") from None
    finally:
        conn.close()
    return Book(path, as_of, list(futures.values()), spots, spot_error, list(options.values()), lme)


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


def _book_option_ticker(root: ContractRoot, instrument_id: str, stored: str, as_of: date) -> Tuple[str, str]:
    """As ``_book_ticker``, for an option on a future: its own bbg_ticker, else Bloomberg's live
    form rebuilt from its canonical id (``option_for`` / ``option_request_ticker``)."""
    if stored:
        return stored, ""
    if root.bbg_placeholder:
        return "", (f"no Bloomberg ticker: {root.root_id}'s bbg_root {root.bbg_root!r} is a placeholder; "
                    "fix the root, then upload the blotter again")
    try:
        from data.contracts import option_for, option_request_ticker
        return option_request_ticker(option_for(root.root_id, instrument_id), as_of), ""
    except (UnknownContract, KeyError, ValueError) as exc:
        return "", (f"the option has no Bloomberg ticker and its id does not fit {root.root_id}'s current root "
                    f"({exc}); upload the blotter again so it takes the root's ticker")


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


# --------------------------------------------------------------------------- LME curve (Phase 5)

def lme_prompt_field() -> str:
    """bbg-curves' prompt-date field for an LME ticker (``fwd_curve.LME_PROMPT_DATE_FIELD``), read
    lazily so this module loads without it; LME_PROMPT_FIELD_FALLBACK when it is not there."""
    try:
        from data.bloomberg.fwd_curve import LME_PROMPT_DATE_FIELD
    except Exception:  # noqa: BLE001 -- an import problem there must not stop the check
        return LME_PROMPT_FIELD_FALLBACK
    return str(LME_PROMPT_DATE_FIELD or LME_PROMPT_FIELD_FALLBACK)


def lme_fields() -> Tuple[str, ...]:
    return LME_BASE_FIELDS + (lme_prompt_field(),)


def lme_today() -> date:
    """The LME's own today: the calendar date in London (a check run from Hong Kong in the
    morning is still on London's previous day)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/London")).date()
    except Exception:  # noqa: BLE001 -- no tz database: the local date
        return date.today()


def lme_root_ids() -> Set[str]:
    """The root ids that are LME prompt-date forwards (``engine.lme.lme_roots``); empty when the
    package cannot be loaded."""
    try:
        from engine import lme
        return set(lme.lme_roots())
    except Exception:  # noqa: BLE001 -- reported by lme_pillars on the metals asked
        return set()


@dataclass
class LmePillar:
    """One LME ticker the check asks: the metal's cash, 3-month or first monthly price."""

    kind: str                     # 'CASH' | '3M' | 'MONTHLY'
    label: str                    # 'cash', '3M', 'monthly 2026-10'
    ticker: str
    expected: Tuple[date, ...]    # our prompt date(s) for it; the first is today's
    asked: bool = True
    why_not_asked: str = ""
    answer: Optional[Answer] = None
    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    px_last: Optional[float] = None
    bbg_date: str = ""

    @property
    def verdict(self) -> str:
        if not self.findings:
            return OK
        return min((f.verdict for f in self.findings), key=LME_VERDICTS.index)


@dataclass
class LmeResult:
    root: ContractRoot
    pillars: List[LmePillar]
    error: str = ""               # why the tickers could not be built

    @property
    def verdict(self) -> str:
        if self.error:
            return NOT_FOUND
        return min((p.verdict for p in self.pillars), key=LME_VERDICTS.index) if self.pillars else OK

    @property
    def priced(self) -> bool:
        """True when the cash or the 3M ticker priced: the metal has a curve to mark on."""
        return any(p.px_last is not None for p in self.pillars if p.kind in ("CASH", "3M"))


def lme_pillars(root: ContractRoot, day: date) -> LmeResult:
    """The metal's cash, 3M and first monthly ticker, as ``engine.lme`` builds them for the
    curve quoted on ``day``, with our prompt dates (``day``'s and the previous LME business
    day's, since Bloomberg's rolling 3M may not have rolled yet). Asks nothing."""
    try:
        from engine import calendars, lme
        rid = root.root_id
        prev = calendars.previous_business_day(lme.LME_CALENDAR, day)
        cash = LmePillar("CASH", "cash", lme.cash_ticker(rid),
                         tuple(dict.fromkeys((lme.cash_date(day), lme.cash_date(prev)))))
        three = LmePillar("3M", "3M", lme.three_month_ticker(rid),
                          tuple(dict.fromkeys((lme.three_month_date(day), lme.three_month_date(prev)))))
        first = next(p for p in lme.prompt_structure(day, months=3) if p.kind == "MONTHLY")
        year, month = (int(x) for x in first.label.split("-"))
        monthly = LmePillar("MONTHLY", f"monthly {first.label}", lme.monthly_ticker(rid, year, month), (first.date,))
    except Exception as exc:  # noqa: BLE001 -- an unknown metal or a missing package: said, never raised
        return LmeResult(root, [], f"its LME tickers could not be built ({type(exc).__name__}: {exc})")
    if root.bbg_placeholder:
        monthly.asked = False
        monthly.why_not_asked = (f"not asked: bbg_root {root.bbg_root!r} is a placeholder, and the monthly ticker "
                                 "is built on it")
    return LmeResult(root, [cash, three, monthly])


def _lme_code_action(p: LmePillar, root: ContractRoot) -> str:
    if p.kind == "MONTHLY":
        return (f"the monthly ticker is the dated form engine/lme/tickers.py builds on config/contracts.csv's bbg_root "
                f"{root.bbg_root!r}: if the root check finds that root, the dated form is wrong; find the right ticker "
                f"on the terminal ({root.bbg_root}1 {root.bbg_yellow_key} CT, or SECF) and tell the housekeeper "
                f"({LME_OWNER})")
    which = "cash_ticker" if p.kind == "CASH" else "three_month_ticker"
    return (f"the ticker is built by engine/lme/tickers.py ({which}), not by config/contracts.csv: find the right "
            f"one on the terminal (LMEX <GO>, or SECF) and tell the housekeeper ({LME_OWNER})")


def check_lme_pillar(p: LmePillar, root: ContractRoot, answer: Optional[Answer], prompt_field: str) -> LmePillar:
    """The verdicts on one LME ticker from Bloomberg's answer (None: not asked)."""
    p.answer = answer
    if not p.asked:
        p.findings.append(Finding(NOT_FOUND, p.why_not_asked, "fix bbg_root in config/contracts.csv first"))
        return p
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        p.findings.append(Finding(NO_ANSWER, f"Bloomberg did not answer for {p.ticker!r} ({words})",
                                  "run the check again"))
        return p
    if answer.security_error:
        base = _security_error_finding(p.ticker, answer, "LME data")
        if base.verdict == NOT_FOUND:
            base = Finding(NOT_FOUND, base.evidence, _lme_code_action(p, root), owner=LME_OWNER)
        p.findings.append(base)
        return p
    values = answer.fields
    p.px_last = _num(values.get("PX_LAST"))
    if p.px_last is None:
        p.findings.append(Finding(
            NO_PRICE, f"{p.ticker!r} resolves but returned no PX_LAST" + _bloomberg_said(answer, ("PX_LAST",)),
            f"an LME entitlement this terminal lacks, or the wrong ticker: look at {p.ticker} GP on the terminal"))
    ccy = str(values.get("CRNCY") or "").strip()
    if ccy and not same_currency(currency_parts(ccy)[0], LME_CURRENCY):
        p.findings.append(Finding(
            CURRENCY_MISMATCH, f"Bloomberg prices {p.ticker!r} in {ccy}; LME metals trade in {LME_CURRENCY} per tonne",
            "this ticker is another security: " + _lme_code_action(p, root), owner=LME_OWNER))
    elif not ccy:
        p.notes.append("Bloomberg gave no CRNCY: the currency was not checked")
    p.bbg_date = _date_text(values.get(prompt_field))
    ours = ", ".join(d.isoformat() for d in p.expected)
    if not p.bbg_date:
        p.notes.append(f"Bloomberg gave no {prompt_field}" + _bloomberg_said(answer, (prompt_field,))
                       + f": the prompt date was not checked (ours: {p.expected[0].isoformat()}); bbg-curves then "
                         "places this pillar on our date")
    elif p.bbg_date not in {d.isoformat() for d in p.expected}:
        if p.kind == "CASH":
            p.notes.append(f"Bloomberg's {prompt_field} {p.bbg_date} is not our cash date ({ours}); the cash price is "
                           "written as the metal's SPOT, so its date does not move a mark")
        else:
            rule = ("engine/lme/prompts.py monthly_prompt: the month's third Wednesday, rolled off an LME holiday"
                    if p.kind == "MONTHLY" else
                    "engine/lme/prompts.py three_month_date: today + 3 months, modified following on the LME calendar")
            p.findings.append(Finding(
                DATE_MISMATCH, f"Bloomberg's {prompt_field} for {p.ticker!r} is {p.bbg_date}; our {p.label} prompt is "
                               f"{ours}",
                f"check the date on the terminal ({p.ticker} DES). If Bloomberg's is the prompt, our rule is wrong "
                f"({rule}); if that field is not the prompt date, the field is wrong (bbg-curves' "
                f"LME_PROMPT_DATE_FIELD). Tell the housekeeper ({LME_OWNER})", owner=LME_OWNER))
    elif len(p.expected) > 1 and p.bbg_date != p.expected[0].isoformat():
        p.notes.append(f"Bloomberg's {p.label} date {p.bbg_date} is the previous LME business day's; it has not "
                       "rolled to today's yet")
    return p


# --------------------------------------------------------------------------- options on futures (Phase 5)

@dataclass
class OptionRootResult:
    """The options check of one root: its option chain, one option asked, and what it says."""

    root: ContractRoot
    ticker: str                                       # the generic front future asked for OPT_CHAIN
    asked: bool
    chain_answer: Optional[Answer] = None
    chain: List[str] = field(default_factory=list)    # option tickers read from the chain
    picked: str = ""                                  # Bloomberg's own ticker of the option asked
    ours: str = ""                                    # our live form of the same option, '' when identical
    canonical: str = ""                               # our canonical id of it
    picked_answer: Optional[Answer] = None
    ours_answer: Optional[Answer] = None
    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    bbg_expiry: str = ""
    our_expiry: str = ""
    bbg_style: str = ""
    bbg_lead: Optional[int] = None

    @property
    def verdict(self) -> str:
        if not self.findings:
            return OK
        return min((f.verdict for f in self.findings), key=OPTION_VERDICTS.index)


def _norm_ticker(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).upper()


def chain_tickers(value) -> List[str]:
    """The option tickers of an OPT_CHAIN answer: a list of rows, each a dict ('Security
    Description': 'CLZ6C 70.00 Comdty') or a plain string; whitespace collapsed."""
    if value is None:
        return []
    rows = value if isinstance(value, (list, tuple)) else [value]
    out: List[str] = []
    for row in rows:
        text = ""
        if isinstance(row, Mapping):
            keyed = {str(k).strip().lower(): v for k, v in row.items()}
            text = keyed.get("security description") or next(
                (v for v in row.values() if isinstance(v, str) and v.strip()), "")
        elif isinstance(row, str):
            text = row
        text = re.sub(r"\s+", " ", str(text or "").strip())
        if text and text not in out:
            out.append(text)
    return out


def our_option_form(root: ContractRoot, code: str, year: int, cp: str, strike: float) -> Tuple[str, str]:
    """(our live request form, our canonical id) of one option, the forms contract-master's
    ``option_request_ticker`` and ``make_option_id`` write: 'CLZ6C 70 Comdty', 'CLZ26C 70 Comdty'."""
    live = (f"{padded_root(root.bbg_root)}{code.upper()}{int(year) % 10}{cp.upper()} {format_strike(strike)} "
            f"{root.bbg_yellow_key}")
    return live, make_option_id(root.bbg_root, code, int(year), cp, strike, root.bbg_yellow_key)


def pick_option(chain: Sequence[str], ref_year: Optional[int] = None) -> Optional[str]:
    """The option of the chain to ask: the nearest contract month, calls first, the middle strike
    (roughly at the money without asking a price). None when no ticker reads as an option."""
    ref = ref_year or date.today().year
    parsed = []
    for t in chain:
        parts = parse_option_ticker(t)
        if parts is None:
            continue
        _root, code, year, cp, strike, _key = parts
        parsed.append((expand_year(year, ref), month_from_code(code), cp != "C", float(strike), t))
    if not parsed:
        return None
    first = min((y, m) for y, m, _p, _k, _t in parsed)
    side = min(p for y, m, p, _k, _t in parsed if (y, m) == first)
    same = sorted((k, t) for y, m, p, k, t in parsed if (y, m) == first and p == side)
    return same[(len(same) - 1) // 2][1]


def _exercise_style(text) -> str:
    t = str(text or "").strip().upper()
    if t.startswith("AMER"):
        return "AMERICAN"
    if t.startswith("EURO"):
        return "EUROPEAN"
    return ""


def _chain_findings(res: OptionRootResult, today: date) -> None:
    """From the chain answer: NO_OPTIONS, or the option to ask and our form of it."""
    answer = res.chain_answer
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        res.findings.append(Finding(NO_ANSWER, f"Bloomberg did not answer for {res.ticker!r} ({words})",
                                    "run the check again"))
        return
    if answer.security_error:
        res.findings.append(Finding(
            NO_OPTIONS, f'Bloomberg refused {res.ticker!r} for its option chain: "{answer.security_error}"',
            "the root check's verdict on this root comes first: fix the future's bbg_root, then check again"))
        return
    res.chain = chain_tickers(answer.fields.get(CHAIN_FIELD))
    if not res.chain:
        res.findings.append(Finding(
            NO_OPTIONS, f"Bloomberg's {CHAIN_FIELD} on {res.ticker!r} lists no option"
                        + _bloomberg_said(answer, (CHAIN_FIELD,)),
            f"look at {res.ticker} OMON on the terminal: if options trade there, they are keyed elsewhere (tell the "
            f"housekeeper, {OPTION_OWNER}); if none trade, set option_style blank for {res.root.root_id}"))
        return
    unreadable = [t for t in res.chain if parse_option_ticker(t) is None]
    picked = pick_option(res.chain, today.year)
    if picked is None:
        res.findings.append(Finding(
            FORM_MISMATCH, f"none of the {len(res.chain)} tickers of {res.ticker}'s {CHAIN_FIELD} reads as "
                           f"'<root><month><year><C|P> <strike> {res.root.bbg_yellow_key}' (first: "
                           f"{', '.join(repr(t) for t in res.chain[:FORM_EXAMPLES])})",
            f"the app's option tickers (data/contracts/options.py) are not in Bloomberg's form: tell the housekeeper "
            f"({OPTION_OWNER})", owner=OPTION_OWNER))
        return
    if unreadable:
        res.notes.append(f"{len(unreadable)} of the chain's {len(res.chain)} tickers are not in the option form "
                         f"(e.g. {unreadable[0]!r})")
    root_code, code, year, cp, strike, _key = parse_option_ticker(picked)
    full_year = expand_year(year, today.year)
    live, canonical = our_option_form(res.root, code, full_year, cp, float(strike))
    res.picked, res.canonical = picked, canonical
    res.ours = live if _norm_ticker(live) != _norm_ticker(picked) else ""
    if root_code != res.root.bbg_root.upper():
        res.notes.append(f"Bloomberg keys these options on root {root_code!r}, the future's is {res.root.bbg_root!r}")


def _option_findings(res: OptionRootResult, root_px: Optional[float], conn: Optional[sqlite3.Connection]) -> None:
    """From the answers on the option asked (and our form of it): form, strike scale, price,
    style, lead months and expiry."""
    root = res.root
    answer = res.picked_answer
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        res.findings.append(Finding(NO_ANSWER, f"Bloomberg did not answer for {res.picked!r} ({words})",
                                    "run the check again"))
        return
    if answer.security_error:
        res.findings.append(_security_error_finding(res.picked, answer, f"{root.exchange} options"))
        return
    values = answer.fields
    _root_code, code, year_digits, cp, strike_text, _key = parse_option_ticker(res.picked)
    strike = float(strike_text)

    if res.ours:
        ours = res.ours_answer
        if ours is None or not ours.answered:
            res.notes.append(f"our form {res.ours!r} was not answered: the form was not confirmed")
        elif ours.security_error:
            res.findings.append(Finding(
                FORM_MISMATCH, f"Bloomberg's chain writes {res.picked!r}; our form of the same option, {res.ours!r}, "
                               f'is refused: "{ours.security_error}"',
                "the option tickers the app asks (data/contracts/options.py option_request_ticker, tickers.py "
                f"format_strike) are not in Bloomberg's form: tell the housekeeper ({OPTION_OWNER})",
                owner=OPTION_OWNER))
        else:
            theirs_k, ours_k = _num(values.get("OPT_STRIKE_PX")), _num(ours.fields.get("OPT_STRIKE_PX"))
            theirs_n, ours_n = str(values.get("NAME") or "").strip(), str(ours.fields.get("NAME") or "").strip()
            if (theirs_k is not None and ours_k is not None and abs(theirs_k - ours_k) > 1e-9 * max(1.0, theirs_k)) \
                    or (theirs_n and ours_n and theirs_n.upper() != ours_n.upper()):
                res.findings.append(Finding(
                    FORM_MISMATCH, f"our form {res.ours!r} resolves, but to another option than Bloomberg's "
                                   f"{res.picked!r} (name {ours_n or '-'} against {theirs_n or '-'}, strike "
                                   f"{_g(ours_k)} against {_g(theirs_k)})",
                    f"the app would price the wrong option: tell the housekeeper ({OPTION_OWNER})", owner=OPTION_OWNER))
            else:
                res.notes.append(f"Bloomberg writes {res.picked!r}; our form {res.ours!r} resolves to the same option")

    bbg_strike = _num(values.get("OPT_STRIKE_PX"))
    if bbg_strike is not None and strike and fill_ratio_kind(abs(bbg_strike) / abs(strike)) != "same":
        res.findings.append(Finding(
            FORM_MISMATCH, f"{res.picked!r} writes the strike as {strike_text}, but Bloomberg's OPT_STRIKE_PX is "
                           f"{_g(bbg_strike)}",
            f"the app reads the strike from the ticker: its strike scale is not Bloomberg's; tell the housekeeper "
            f"({OPTION_OWNER})", owner=OPTION_OWNER))
    und_px = _num(values.get("OPT_UNDL_PX"))
    und_px = und_px if und_px is not None else root_px
    if und_px and strike:
        kind = fill_ratio_kind(abs(strike) / abs(und_px))
        if kind in ("x100", "x0.01"):
            res.findings.append(Finding(
                FORM_MISMATCH, f"{res.picked!r} has strike {strike_text} against its future's price {_g(und_px)}: "
                               f"{'about 100 times' if kind == 'x100' else 'about a hundredth of'} it",
                "the options quote strikes in another unit than the future (cents against dollars): the Greeks would "
                f"be wrong; tell the housekeeper ({OPTION_OWNER})", owner=OPTION_OWNER))

    if _num(values.get("PX_LAST")) is None:
        res.findings.append(Finding(
            NO_PRICE, f"{res.picked!r} resolves but returned no PX_LAST" + _bloomberg_said(answer, ("PX_LAST",)),
            f"an entitlement to {root.exchange} options this terminal lacks, or a dead strike: look at {res.picked} GP"))

    res.bbg_style = _exercise_style(values.get("OPT_EXER_TYP"))
    ours_style = root.option_style.upper()
    if not res.bbg_style:
        shown = values.get("OPT_EXER_TYP")
        res.notes.append(f"Bloomberg's OPT_EXER_TYP is {shown!r}" if shown not in (None, "") else
                         "Bloomberg gave no OPT_EXER_TYP" + _bloomberg_said(answer, ("OPT_EXER_TYP",))
                         + ": the style was not checked")
    elif res.bbg_style != ours_style:
        res.findings.append(Finding(
            STYLE_MISMATCH, f"Bloomberg's OPT_EXER_TYP for {res.picked!r} is {values.get('OPT_EXER_TYP')!r}; "
                            f"config/contracts.csv says option_style {root.option_style!r}",
            f"set option_style {root.option_style} -> {res.bbg_style.lower()} (Black-76 against American changes "
            "the Greeks, not the P&L)", "option_style", root.option_style, res.bbg_style.lower()))

    res.bbg_expiry = _date_text(values.get("OPT_EXPIRE_DT"))
    if not res.bbg_expiry:
        res.notes.append("Bloomberg gave no OPT_EXPIRE_DT" + _bloomberg_said(answer, ("OPT_EXPIRE_DT",))
                         + ": the expiry month and date were not checked")
        return
    expiry = date.fromisoformat(res.bbg_expiry)
    und_month, und_year = month_from_code(code), expand_year(year_digits, expiry.year)
    und_text = str(values.get("OPT_UNDL_TICKER") or "").strip()
    und_parts = (parse_bbg_ticker(und_text) or parse_bbg_ticker(f"{und_text} {root.bbg_yellow_key}")) \
        if und_text else None
    if und_parts is not None:
        und_month, und_year = month_from_code(und_parts[1]), expand_year(und_parts[2], expiry.year)
    res.bbg_lead = (und_year * 12 + und_month) - (expiry.year * 12 + expiry.month)
    und_label = und_text or f"the {code}{year_digits} future"
    if res.bbg_lead != root.option_lead_months:
        current = "" if root.option_lead_months is None else str(root.option_lead_months)
        evidence = (f"{res.picked!r} expires {res.bbg_expiry} on {und_label}: {res.bbg_lead} month"
                    f"{'s' if res.bbg_lead != 1 else ''} before the future's month; config/contracts.csv says "
                    f"option_lead_months {current or 'blank'}")
        if 0 <= res.bbg_lead <= 3:
            res.findings.append(Finding(
                LEAD_MISMATCH, evidence,
                f"set option_lead_months {current or 'blank'} -> {res.bbg_lead} (it decides which future a broker's "
                "dated option symbol is on)", "option_lead_months", current, str(res.bbg_lead)))
        else:
            res.notes.append(evidence + " (outside 0-3: a serial option, no change suggested)")
    try:
        from data.contracts import option_for
        mine = option_for(root.root_id, res.canonical, conn)
    except Exception as exc:  # noqa: BLE001 -- our date could not be worked out: said, never raised
        res.notes.append(f"our expiry of {res.canonical!r} could not be worked out ({exc})")
        return
    res.our_expiry = mine.last_trade_date.isoformat()
    if expiry > mine.last_trade_date:
        res.findings.append(Finding(
            DATE_MISMATCH, f"Bloomberg's OPT_EXPIRE_DT for {res.picked!r} is {res.bbg_expiry}, later than our date "
                           f"{res.our_expiry} ({mine.dates_note})",
            "our date is meant to be no earlier than the real one; until a pull stores Bloomberg's date the "
            f"option would be treated as expired too early: tell the housekeeper ({OPTION_OWNER})", owner=OPTION_OWNER))
    elif expiry < mine.last_trade_date and mine.estimated:
        res.notes.append(f"our date {res.our_expiry} is the conservative estimate (its future's last trade); "
                         f"Bloomberg's {res.bbg_expiry} replaces it once a pull stores it")


# --------------------------------------------------------------------------- the plan

@dataclass
class Plan:
    """What a check would ask Bloomberg, worked out without asking anything."""

    roots: List[ContractRoot]                 # every selected root of the root check (none without that part)
    asked: List[ContractRoot]                 # the ones whose generic is asked
    placeholders: List[ContractRoot]          # never asked
    book: Optional[Book] = None
    futures: List[BookFuture] = field(default_factory=list)   # the book's futures in scope
    spots: List[SpotNeed] = field(default_factory=list)       # the conversion spots in scope
    search: bool = False
    batch_size: int = BATCH_SIZE
    parts: Tuple[str, ...] = ALL_PARTS
    today: date = field(default_factory=date.today)           # the LME's today; the options' reference year
    lme: List[LmeResult] = field(default_factory=list)        # the LME metals, their tickers not yet asked
    option_roots: List[ContractRoot] = field(default_factory=list)          # asked for their option chain
    option_placeholders: List[ContractRoot] = field(default_factory=list)   # option_style set, bbg_root a placeholder
    book_options: List[BookFuture] = field(default_factory=list)            # the book's options in scope
    book_lme: List[LmeTicket] = field(default_factory=list)                 # the book's LME tickets in scope

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
    def lme_tickers(self) -> List[str]:
        return list(dict.fromkeys(p.ticker for m in self.lme for p in m.pillars if p.asked))

    @property
    def chain_tickers(self) -> List[str]:
        return list(dict.fromkeys(generic_ticker(r) for r in self.option_roots))

    @property
    def book_option_tickers(self) -> List[str]:
        return list(dict.fromkeys(o.ticker for o in self.book_options if o.ticker))

    @property
    def securities(self) -> int:
        """Securities known before anything is asked (the option follow-ups come on top)."""
        return sum(len(t) for t in (self.root_tickers, self.lme_tickers, self.chain_tickers, self.contract_tickers,
                                    self.book_option_tickers, self.spot_tickers))

    @property
    def requests(self) -> int:
        size = max(1, self.batch_size)
        plain = (self.root_tickers, self.lme_tickers, self.contract_tickers, self.book_option_tickers,
                 self.spot_tickers)
        return (sum(math.ceil(len(t) / size) for t in plain)
                + math.ceil(len(self.chain_tickers) / max(1, min(size, CHAIN_BATCH_SIZE))))

    @property
    def max_option_followups(self) -> int:
        """At most two option tickers per chain: Bloomberg's own, and ours where it differs."""
        return 2 * len(self.chain_tickers)

    @property
    def max_followup_requests(self) -> int:
        return math.ceil(self.max_option_followups / max(1, self.batch_size))

    @property
    def max_searches(self) -> int:
        if not self.search:
            return 0
        return sum(len(search_queries(r)) for r in self.asked + self.placeholders)

    @property
    def needs_bloomberg(self) -> bool:
        return self.securities > 0 or self.max_searches > 0

    @property
    def empty(self) -> bool:
        return not (self.roots or self.lme or self.option_roots or self.option_placeholders or self.futures
                    or self.spots or self.book_options or self.book_lme)

    def describe(self) -> List[str]:
        lines: List[str] = []
        if PART_ROOTS in self.parts:
            lines.append(f"{len(self.roots)} contract root{'s' if len(self.roots) != 1 else ''} selected: "
                         f"{len(self.asked)} asked on their generic ticker, {len(self.placeholders)} placeholder"
                         f"{'s' if len(self.placeholders) != 1 else ''} not asked (NOT_FOUND as they stand).")
        if self.lme or PART_LME in self.parts:
            months = sorted({p.label for m in self.lme for p in m.pillars if p.kind == "MONTHLY"})
            lines.append(f"LME curve on {self.today.isoformat()}: {len(self.lme)} metal{'s' if len(self.lme) != 1 else ''}, "
                         f"{len(self.lme_tickers)} ticker{'s' if len(self.lme_tickers) != 1 else ''} (cash, 3M and "
                         f"the {', '.join(months) or 'first monthly'} of each), fields {', '.join(lme_fields())}.")
        if PART_OPTIONS in self.parts:
            n = len(self.chain_tickers)
            chain_requests = math.ceil(n / max(1, min(self.batch_size, CHAIN_BATCH_SIZE)))
            lines.append(f"Options: {len(self.option_roots) + len(self.option_placeholders)} root"
                         f"{'s' if len(self.option_roots) + len(self.option_placeholders) != 1 else ''} with an "
                         f"option_style ({len(self.option_placeholders)} placeholder not asked): {n} option chain"
                         f"{'s' if n != 1 else ''} ({CHAIN_FIELD} on the generic front future) in {chain_requests} "
                         f"request{'s' if chain_requests != 1 else ''} of up to {CHAIN_BATCH_SIZE}, then up to "
                         f"{self.max_option_followups} option tickers (one per chain, and our own form of it where "
                         f"Bloomberg writes it differently) in up to {self.max_followup_requests} more.")
        if self.book is not None:
            unaskable = sum(1 for f in self.futures if not f.ticker)
            lines.append(f"Book {self.book.db_path} on {self.book.as_of.isoformat()}: {len(self.futures)} unexpired "
                         f"commodity future{'s' if len(self.futures) != 1 else ''} ({len(self.contract_tickers)} "
                         f"ticker{'s' if len(self.contract_tickers) != 1 else ''} to ask, {unaskable} without one), "
                         f"{len(self.spot_tickers)} USD conversion spot{'s' if len(self.spot_tickers) != 1 else ''}.")
            if self.book_options or self.book_lme:
                no_ticker = sum(1 for o in self.book_options if not o.ticker)
                lines.append(f"Book: {len(self.book_options)} open option{'s' if len(self.book_options) != 1 else ''} "
                             f"on futures ({len(self.book_option_tickers)} to ask, {no_ticker} without a ticker), "
                             f"{len(self.book_lme)} open LME ticket{'s' if len(self.book_lme) != 1 else ''} placed on "
                             "the LME curve above (nothing more asked).")
            if self.book.spot_error:
                lines.append(f"Conversion spots not checked: {self.book.spot_error}.")
        lines.append(f"{self.securities} securit{'ies' if self.securities != 1 else 'y'} in {self.requests} "
                     f"ReferenceDataRequest{'s' if self.requests != 1 else ''} (up to {self.batch_size} each)"
                     + (f", then up to {self.max_option_followups} option tickers in up to "
                        f"{self.max_followup_requests} more" if self.max_option_followups else "") + ".")
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
         batch_size: int = BATCH_SIZE, parts: Optional[Sequence[str]] = None, today: Optional[date] = None) -> Plan:
    """What the check would ask: the roots selected (``root_ids``, ``sector``, ``book_only``: only
    the roots the book holds; then the first ``limit``), the parts run on them (``parts``, default
    ALL_PARTS: the root check, the LME curve of the LME forward metals among them, the option
    chains of those with an option_style), the book's futures, options, LME tickets and conversion
    spots in scope (a root or sector filter narrows them too; ``limit`` does not; the curve of a
    metal the book holds an LME ticket in is asked whatever the parts), and the request count.
    ``today`` is the LME's day (default London's). Asks nothing. Raises ValueError for an unknown
    root id or part."""
    parts = tuple(parts) if parts else ALL_PARTS
    unknown = [p for p in parts if p not in ALL_PARTS]
    if unknown:
        raise ValueError(f"unknown part {', '.join(unknown)}; the parts are {', '.join(ALL_PARTS)}")
    today = today or lme_today()
    all_roots = list((roots.values() if isinstance(roots, Mapping) else roots) if roots is not None
                     else load_roots().values())
    selected = _select(all_roots, root_ids, sector)
    if book_only:
        held = book.held_roots if book is not None else set()
        selected = [r for r in selected if r.root_id in held]
    if limit is not None:
        selected = selected[:max(0, int(limit))]
    checked = selected if PART_ROOTS in parts else []
    out = Plan(roots=checked, asked=[r for r in checked if not r.bbg_placeholder],
               placeholders=[r for r in checked if r.bbg_placeholder], book=book, search=search,
               batch_size=batch_size, parts=parts, today=today)
    if PART_OPTIONS in parts:
        styled = [r for r in selected if r.option_style]
        out.option_roots = [r for r in styled if not r.bbg_placeholder]
        out.option_placeholders = [r for r in styled if r.bbg_placeholder]
    metals: List[ContractRoot] = []
    if PART_LME in parts:
        lme_ids = lme_root_ids()
        metals = [r for r in selected if r.root_id in lme_ids]
    if book is not None:
        narrowed = bool(root_ids or sector)
        in_scope = {r.root_id for r in _select(all_roots, root_ids, sector)} if narrowed else None
        out.futures = [f for f in book.futures if in_scope is None or f.root_id in in_scope]
        out.book_options = [o for o in book.options if in_scope is None or o.root_id in in_scope]
        out.book_lme = [t for t in book.lme if in_scope is None or t.root_id in in_scope]
        if in_scope is None:
            out.spots = list(book.spots)
        else:
            trade_ids = {t[0] for f in out.futures + out.book_options for t in f.trades}
            out.spots = [s for s in book.spots if s.trade_ids & trade_ids]
        by_id = {r.root_id: r for r in all_roots}
        for rid in dict.fromkeys(t.root_id for t in out.book_lme):
            if rid in by_id and by_id[rid] not in metals:
                metals.append(by_id[rid])
    out.lme = [lme_pillars(r, today) for r in metals]
    return out


# --------------------------------------------------------------------------- Bloomberg client

def _bulk_rows(element) -> List[dict]:
    """A blpapi bulk field (an array of sequences) as a list of {sub-field: value} dicts."""
    rows: List[dict] = []
    for i in range(element.numValues()):
        row = element.getValueAsElement(i)
        values: dict = {}
        for j in range(row.numElements()):
            sub = row.getElement(j)
            try:
                values[str(sub.name())] = sub.getValue()
            except Exception:  # noqa: BLE001 -- a sub-field that is not a plain value is kept as text
                values[str(sub.name())] = str(sub)
        rows.append(values)
    return rows


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

    def bulk(self, securities: Sequence[str], field_name: str) -> Dict[str, Answer]:
        """A ReferenceDataRequest for one bulk field (OPT_CHAIN): each security's answer carries
        the field as a list of rows, each row a {sub-field: value} dict. The app's request helper
        reads one value per field, so a bulk field has its own loop here."""
        blpapi = self._blpapi
        request = self.service.createRequest("ReferenceDataRequest")
        for s in securities:
            request.getElement("securities").appendValue(s)
        request.getElement("fields").appendValue(field_name)
        cid = blpapi.CorrelationId(next(_SEARCH_CIDS))
        try:
            self.session.sendRequest(request, correlationId=cid)
        except Exception as exc:  # noqa: BLE001 -- the session failed under the request: unreachable
            raise BloombergUnavailable(f"the {field_name} request failed ({type(exc).__name__}: {exc})") from None
        out: Dict[str, Answer] = {}
        timeout_ms = int(getattr(self._pm, "EVENT_TIMEOUT_MS", SEARCH_TIMEOUT_MS))
        while True:
            event = self.session.nextEvent(timeout_ms)
            etype = event.eventType()
            if etype == getattr(blpapi.Event, "TIMEOUT", None):
                for s in securities:
                    out.setdefault(s, Answer(s, {}, security_error=f"no answer: {field_name} timed out after "
                                                                   f"{timeout_ms // 1000} s", answered=False))
                return out
            ours = False
            for msg in event:
                cids = list(msg.correlationIds()) if hasattr(msg, "correlationIds") else []
                if cids and cid not in cids:
                    continue          # a late answer to another request
                ours = True
                if not msg.hasElement("securityData"):
                    if msg.hasElement("responseError"):
                        words = str(msg.getElement("responseError"))
                        for s in securities:
                            out.setdefault(s, Answer(s, {}, security_error=f"Bloomberg said {words}"))
                    continue
                data = msg.getElement("securityData")
                for i in range(data.numValues()):
                    sd = data.getValueAsElement(i)
                    security = sd.getElementAsString("security")
                    err = self._pm._parse_security_error(sd)
                    rows: List[dict] = []
                    if sd.hasElement("fieldData") and sd.getElement("fieldData").hasElement(field_name):
                        rows = _bulk_rows(sd.getElement("fieldData").getElement(field_name))
                    out[security] = Answer(
                        security, {field_name: rows} if rows else {},
                        security_error=str(err.get("message") or "security error") if err else "",
                        field_errors={str(fx.get("fieldId") or "?"): str(fx.get("message") or "")
                                      for fx in self._pm._parse_field_exceptions(sd)})
            if etype == blpapi.Event.RESPONSE and ours:
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
class LmeTicketResult:
    ticket: LmeTicket
    findings: List[Finding]
    notes: List[str] = field(default_factory=list)
    position: str = ""                # where the prompt sits on the curve, in words

    @property
    def verdict(self) -> str:
        if not self.findings:
            return OK
        return min((f.verdict for f in self.findings), key=LME_BOOK_VERDICTS.index)


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
    lme: List[LmeResult] = field(default_factory=list)
    options: List[OptionRootResult] = field(default_factory=list)
    book_options: List[ContractResult] = field(default_factory=list)
    book_lme: List[LmeTicketResult] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        out = {v: 0 for v in ROOT_VERDICTS}
        for r in self.roots:
            out[r.verdict] += 1
        return out

    @property
    def needs_attention(self) -> bool:
        return (any(r.needs_attention for r in self.roots) or any(c.verdict != OK for c in self.contracts)
                or any(s.verdict != OK for s in self.spots) or any(m.verdict != OK for m in self.lme)
                or any(o.verdict != OK for o in self.options) or any(c.verdict != OK for c in self.book_options)
                or any(t.verdict != OK for t in self.book_lme))

    def code_findings(self) -> List[Tuple[str, str, Finding]]:
        """(lane, subject, finding) of every finding whose fix is code, not config/contracts.csv:
        the lines the report asks the user to pass to the housekeeper."""
        out: List[Tuple[str, str, Finding]] = []
        for m in self.lme:
            for p in m.pillars:
                out += [(f.owner, f"{m.root.root_id} {p.label} [{p.ticker}]", f) for f in p.findings if f.owner]
        for o in self.options:
            out += [(f.owner, f"{o.root.root_id} options [{o.picked or o.ticker}]", f) for f in o.findings if f.owner]
        return out

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


def _ask_bulk(client, tickers: Sequence[str], field_name: str, batch_size: int, result: CheckResult,
              part: str) -> Dict[str, Answer]:
    """As ``_ask``, for a bulk field: ``client.bulk(securities, field)`` when the client has it,
    else its ``reference``."""
    out: Dict[str, Answer] = {}
    ask = getattr(client, "bulk", None)
    for batch in _chunks(list(tickers), max(1, batch_size)):
        result.requests_sent += 1
        got = (ask(batch, field_name) if ask is not None else client.reference(batch, (field_name,))) or {}
        by_upper = {str(k).upper(): v for k, v in got.items()}
        for t in batch:
            answer = got.get(t) or by_upper.get(t.upper()) or Answer(
                t, {}, security_error="Bloomberg sent nothing back for this security", answered=False)
            out[t] = answer
            result.answers[t] = (part, answer)
    return out


def _check_book_option(opt: BookFuture, answer: Optional[Answer]) -> ContractResult:
    """An open option on a future of the book: its ticker prices, its fill against PX_LAST (100x
    flagged, as a future's), and OPT_EXPIRE_DT against the stored expiry."""
    if not opt.ticker:
        return ContractResult(opt, None, [Finding(NO_TICKER, opt.why_no_ticker, "nothing was asked for this option")])
    if answer is None or not answer.answered:
        words = answer.security_error if answer is not None else "not asked"
        return ContractResult(opt, answer, [Finding(NO_ANSWER, f"Bloomberg did not answer for {opt.ticker!r} "
                                                               f"({words})", "run the check again")])
    if answer.security_error:
        return ContractResult(opt, answer, [_security_error_finding(opt.ticker, answer, "this option")])
    values = answer.fields
    res = ContractResult(opt, answer, [])
    res.bbg_last_trade = _date_text(values.get("OPT_EXPIRE_DT"))
    px = _num(values.get("PX_LAST"))
    res.px_last = px
    if px is None:
        res.findings.append(Finding(NO_PRICE, f"{opt.ticker!r} resolves but returned no PX_LAST"
                                              + _bloomberg_said(answer, ("PX_LAST",)),
                                    f"an entitlement this terminal lacks, or a strike that does not trade: look at "
                                    f"{opt.ticker} GP; the option has no P&L until it prices"))
    else:
        _fill_findings(res, opt, px, option=True)
    style = _exercise_style(values.get("OPT_EXER_TYP"))
    if style:
        res.notes.append(f"Bloomberg says {style.lower()} exercise")
    bbg = res.bbg_last_trade
    if not bbg:
        res.notes.append("Bloomberg gave no OPT_EXPIRE_DT" + _bloomberg_said(answer, ("OPT_EXPIRE_DT",)))
        return res
    diffs = []
    if opt.expiry and opt.expiry != bbg:
        diffs.append(f"the stored expiry is {opt.expiry} (instruments.expiry_date), Bloomberg's OPT_EXPIRE_DT {bbg}")
    stored = opt.stored
    if stored and stored.get("last_trade_date") and stored.get("last_trade_date") != bbg:
        diffs.append(f"Bloomberg's date stored {stored.get('last_trade_date')} ({stored.get('fetched_at', '')}), "
                     f"{bbg} now")
    if diffs:
        later = bool(opt.expiry) and bbg > opt.expiry
        res.findings.append(Finding(
            DATE_MISMATCH, "; ".join(diffs),
            ("the app would treat the option as expired before Bloomberg does" if later else
             "the app holds a later expiry than Bloomberg's (the conservative estimate)")
            + ": the next Pull Bloomberg now stores Bloomberg's option dates; this check writes nothing"))
    return res


def _check_lme_ticket(ticket: LmeTicket, metal: Optional[LmeResult], day: date) -> LmeTicketResult:
    """An open LME ticket: is its prompt an LME prompt, where does it sit on the metal's curve,
    does the curve price, and is the fill in Bloomberg's unit."""
    res = LmeTicketResult(ticket, [])
    try:
        from engine import lme
        valid = lme.is_valid_prompt(ticket.prompt, ticket.trade_date)
        structure = lme.prompt_structure(day)
    except Exception as exc:  # noqa: BLE001 -- the prompt could not be judged: said, never raised
        res.notes.append(f"the prompt could not be judged ({type(exc).__name__}: {exc})")
        valid, structure = True, []
    if not valid:
        res.findings.append(Finding(
            NOT_A_PROMPT, f"prompt {ticket.prompt} is not an LME prompt date for a ticket dealt on {ticket.trade_date}",
            "check the ticket's prompt in the blotter: it is valued as given, on its own date"))
    if metal is None or not metal.priced:
        why = ("its metal's curve was not asked" if metal is None else
               f"none of {metal.root.root_id}'s cash and 3M tickers priced "
               f"({'; '.join(f'{p.ticker}: {p.verdict}' for p in metal.pillars if p.kind in ('CASH', '3M'))})")
        res.findings.append(Finding(NO_CURVE, f"no LME curve to mark {ticket.trade_id} on: {why}",
                                    "fix the LME tickers first (see the LME curve section)"))
    if structure:
        prompt = date.fromisoformat(ticket.prompt[:10])
        pillars = sorted(structure, key=lambda p: p.date)
        labels = {p.date: p.label for p in pillars}
        if prompt < pillars[0].date:
            res.position = f"before the cash date {pillars[0].date.isoformat()}: marked at the cash price (BBG_INTERP)"
        elif prompt in labels:
            res.position = f"on the {labels[prompt]} pillar"
        elif prompt > pillars[-1].date:
            res.position = f"beyond the curve's last pillar {pillars[-1].date.isoformat()} ({pillars[-1].label})"
            res.findings.append(Finding(
                OFF_CURVE, f"prompt {ticket.prompt} is {res.position}",
                "a forward beyond the last pillar is never extrapolated: it has no mark and no P&L until the curve "
                f"reaches it (tell the housekeeper, {LME_OWNER}, if the curve should be longer)"))
        else:
            lo = [p for p in pillars if p.date < prompt][-1]
            hi = next(p for p in pillars if p.date > prompt)
            res.position = (f"between {lo.label} {lo.date.isoformat()} and {hi.label} {hi.date.isoformat()}: "
                            "interpolated (BBG_INTERP)")
        res.notes.append("only the cash, 3M and first monthly tickers were asked here; the other monthly pillars "
                         "are bbg-curves' pull")
    if metal is not None and ticket.fill:
        px = next((p.px_last for p in metal.pillars if p.kind == "CASH" and p.px_last), None) or \
            next((p.px_last for p in metal.pillars if p.px_last), None)
        if px:
            kind = fill_ratio_kind(abs(ticket.fill) / abs(px))
            if kind in ("x100", "x0.01"):
                res.findings.append(Finding(
                    SCALE_FLAG, f"the ticket's fill {_g(ticket.fill)} is "
                                f"{'about 100 times' if kind == 'x100' else 'about a hundredth of'} Bloomberg's LME "
                                f"price {_g(px)}",
                    "the blotter and Bloomberg quote this metal in units 100 times apart: check the blotter's price "
                    "unit (USD per tonne) before trusting the ticket's P&L"))
    return res


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


def _fill_findings(res: ContractResult, fut: BookFuture, px: float, option: bool = False) -> None:
    """SCALE_FLAG when a fill is about 100 times (or a hundredth of) PX_LAST; PRICE_GAP when far
    apart otherwise. For an option (``option``), whose price can move that far on its own, the
    SCALE_FLAG says so and a PRICE_GAP is only a note."""
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
            "Check the root's price_scale in the worksheet and the blotter's price unit before trusting its P&L"
            + (". An option's price can also move this far on its own (a far out-of-the-money strike): check the "
               "strike and the premium's unit first" if option else "")))
    elif kinds.get("other") and option:
        res.notes.append(f"the fills ({fills}) are far from Bloomberg's PX_LAST {_g(px)}, as an option's price can be")
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
    if the_plan.lme:
        prompt_field = lme_prompt_field()
        lme_answers = _ask(client, the_plan.lme_tickers, lme_fields(), size, result, "lme") \
            if the_plan.lme_tickers else {}
        for shell in the_plan.lme:
            metal = LmeResult(shell.root, [dataclasses.replace(p, findings=[], notes=[]) for p in shell.pillars],
                              shell.error)
            for p in metal.pillars:
                check_lme_pillar(p, metal.root, lme_answers.get(p.ticker) if p.asked else None, prompt_field)
            result.lme.append(metal)
    if the_plan.option_roots or the_plan.option_placeholders:
        _run_options(client, the_plan, result, size)
    if the_plan.book is not None:
        contract_answers = _ask(client, the_plan.contract_tickers, CONTRACT_FIELDS, size, result, "contract")
        for fut in the_plan.futures:
            result.contracts.append(_check_contract(fut, contract_answers.get(fut.ticker) if fut.ticker else None))
        if the_plan.book_option_tickers:
            option_answers = _ask(client, the_plan.book_option_tickers, BOOK_OPTION_FIELDS, size, result,
                                  "book_option")
        else:
            option_answers = {}
        for opt in the_plan.book_options:
            result.book_options.append(_check_book_option(opt, option_answers.get(opt.ticker) if opt.ticker else None))
        metals = {m.root.root_id: m for m in result.lme}
        for ticket in the_plan.book_lme:
            result.book_lme.append(_check_lme_ticket(ticket, metals.get(ticket.root_id), the_plan.today))
        spot_answers = _ask(client, the_plan.spot_tickers, SPOT_FIELDS, size, result, "spot")
        for need in the_plan.spots:
            result.spots.append(_check_spot(need, spot_answers.get(need.ticker)))
    return result


def _run_options(client, the_plan: Plan, result: CheckResult, size: int) -> None:
    """The options part: the chains, then one option of each (and our form of it where it differs)."""
    for root in the_plan.option_placeholders:
        result.options.append(OptionRootResult(root, generic_ticker(root), False, findings=[Finding(
            NO_OPTIONS, f"not asked: bbg_root {root.bbg_root!r} is a placeholder, so its option chain is unknown",
            "fix bbg_root first (the root check)")]))
    chain_size = max(1, min(size, CHAIN_BATCH_SIZE))
    chains = _ask_bulk(client, the_plan.chain_tickers, CHAIN_FIELD, chain_size, result, "option_chain") \
        if the_plan.chain_tickers else {}
    asked: List[OptionRootResult] = []
    for root in the_plan.option_roots:
        res = OptionRootResult(root, generic_ticker(root), True, chain_answer=chains.get(generic_ticker(root)))
        _chain_findings(res, the_plan.today)
        result.options.append(res)
        if res.picked:
            asked.append(res)
    followups = list(dict.fromkeys([r.picked for r in asked] + [r.ours for r in asked if r.ours]))
    answers = _ask(client, followups, OPTION_FIELDS, size, result, "option") if followups else {}
    root_px = {r.root.root_id: _num(r.answer.fields.get("PX_LAST")) for r in result.roots
               if r.answer is not None and r.answer.answered and not r.answer.security_error}
    for res in asked:
        res.picked_answer = answers.get(res.picked)
        res.ours_answer = answers.get(res.ours) if res.ours else None
        _option_findings(res, root_px.get(res.root.root_id), None)


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
    # Options: option_style / option_lead_months where Bloomberg disagrees. The LME part has no
    # row of its own: its tickers and prompt dates are code (the report's housekeeper lines).
    for o in result.options:
        for f in o.findings:
            if f.field:
                rows.append({"root_id": o.root.root_id, "field": f.field, "current": f.current,
                             "suggested": f.suggested, "verdict": f.verdict, "reason": f"{f.evidence}. {f.action}",
                             "apply": ""})
    seen: Set[Tuple[str, str, str]] = set()
    unique: List[Dict[str, str]] = []
    for row in rows:
        key = (row["root_id"], row["field"], row["suggested"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


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
    if PART_ROOTS in p.parts:
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
    if result.lme:
        lines += ["", *_render_lme(result)]
    if result.options:
        lines += ["", *_render_options(result)]
    if p.book is not None:
        lines += ["", *_render_book(result)]
    code = result.code_findings()
    if code:
        lines += ["", "For the housekeeper: these are code, not config/contracts.csv, so the worksheet cannot fix them. "
                      "Paste this section to the housekeeper, who passes each line to the lane named."]
        for owner, subject, f in code:
            lines.append(f"  - {owner}: {subject}: {f.verdict}: {f.evidence}.")
    lines += ["", "Files"]
    for label, path in paths.items():
        lines.append(f"  {label}: {path}")
    lines += ["", "Nothing was written to the marks, the trades or config/contracts.csv. Mark 'yes' in the "
              "worksheet's apply column for each change you accept, then apply it "
              "(py 2_launcher.py contracts-apply <worksheet>)."]
    return "\n".join(lines) + "\n"


def _render_lme(result: CheckResult) -> List[str]:
    prompt_field = lme_prompt_field()
    flagged = [m for m in result.lme if m.verdict != OK]
    lines = [f"LME curve tickers on {result.plan.today.isoformat()} ({len(result.lme)} metal"
             f"{'s' if len(result.lme) != 1 else ''}, {len(flagged)} flagged). Prompt dates are Bloomberg's "
             f"{prompt_field} (itself unverified) against engine/lme's rules.",
             "",
             f"  {'Metal':<8} {'Pillar':<16} {'Ticker':<18} {'PX_LAST':>12}  {'Bloomberg date':<14}  {'Our date':<23}  "
             "Verdict"]
    for m in result.lme:
        if m.error:
            lines.append(f"  {m.root.root_id:<8} {m.error}")
            continue
        for pl in m.pillars:
            ours = ", ".join(d.isoformat() for d in pl.expected)
            lines.append(f"  {m.root.root_id:<8} {pl.label:<16} {pl.ticker:<18} {_g(pl.px_last):>12}  "
                         f"{pl.bbg_date or '-':<14}  {ours:<23}  {pl.verdict}")
    for m in flagged:
        lines += ["", f"  {m.root.root_id}  {m.root.name}  {m.verdict}"]
        if m.error:
            lines.append(f"    - {m.error}. What to do: tell the housekeeper ({LME_OWNER}).")
        for pl in m.pillars:
            for f in pl.findings:
                lines.append(f"    - {pl.label} [{pl.ticker}] {f.verdict}: {f.evidence}. What to do: {f.action}.")
            for n in pl.notes:
                lines.append(f"    - {pl.label} note: {n}.")
    for m in result.lme:
        if m.verdict == OK and any(pl.notes for pl in m.pillars):
            lines.append(f"  note on {m.root.root_id}: "
                         + "; ".join(f"{pl.label}: {n}" for pl in m.pillars for n in pl.notes) + ".")
    return lines


def _render_options(result: CheckResult) -> List[str]:
    flagged = [o for o in result.options if o.verdict != OK]
    lines = [f"Options on futures ({len(result.options)} root{'s' if len(result.options) != 1 else ''} with an "
             f"option_style, {len(flagged)} flagged). Each root's {CHAIN_FIELD} on its generic front future, one "
             "option of it asked.",
             "",
             f"  {'Root':<12} {'Chain on':<14} {'Options':>7}  {'Asked':<22} {'Our form':<22} {'Style CSV/BBG':<19} "
             f"{'Lead CSV/BBG':<12}  {'Expiry ours/BBG':<23}  Verdict"]
    for o in result.options:
        style = f"{o.root.option_style or '-'}/{o.bbg_style.lower() or '-'}"
        lead = f"{'-' if o.root.option_lead_months is None else o.root.option_lead_months}/" \
               f"{'-' if o.bbg_lead is None else o.bbg_lead}"
        expiry = f"{o.our_expiry or '-'}/{o.bbg_expiry or '-'}"
        lines.append(f"  {o.root.root_id:<12} {o.ticker:<14} {len(o.chain):>7}  {o.picked or '-':<22} "
                     f"{o.ours or ('same' if o.picked else '-'):<22} {style:<19} {lead:<12}  {expiry:<23}  {o.verdict}")
    for o in flagged:
        lines += ["", f"  {o.root.root_id}  {o.root.name}  [{o.ticker}]  {o.verdict}"]
        if o.chain:
            lines.append(f"    Bloomberg's chain, first: {', '.join(o.chain[:FORM_EXAMPLES])}.")
        for f in o.findings:
            lines.append(f"    - {f.verdict}: {f.evidence}. What to do: {f.action}.")
        for n in o.notes:
            lines.append(f"    - note: {n}.")
    for o in result.options:
        if o.verdict == OK and o.notes:
            lines.append(f"  note on {o.root.root_id}: " + "; ".join(o.notes) + ".")
    return lines


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
    if result.book_options:
        bad = [c for c in result.book_options if c.verdict != OK]
        lines += ["", f"  Options on futures: {len(result.book_options)} open, {len(bad)} flagged.",
                  f"  {'Option':<22} {'Ticker':<22} {'Lots':>6} {'Avg fill':>12} {'PX_LAST':>12} {'Fill/PX':>8}  "
                  f"{'Expiry stored / Bloomberg':<25}  Verdict"]
        for c in result.book_options:
            o = c.future
            ratio = f"{c.fill_ratio:.3g}" if c.fill_ratio is not None else "-"
            expiry = f"{o.expiry or '-'} / {c.bbg_last_trade or '-'}"
            lines.append(f"  {o.instrument_id:<22} {o.ticker or '-':<22} {o.net_lots:>+6g} {_g(o.avg_fill):>12} "
                         f"{_g(c.px_last):>12} {ratio:>8}  {expiry:<25}  {c.verdict}")
        for c in bad:
            lines += ["", f"  {c.future.instrument_id} ({c.future.root_id}) [{c.future.ticker or 'no ticker'}]  "
                          f"{c.verdict}"]
            for fnd in c.findings:
                lines.append(f"    - {fnd.verdict}: {fnd.evidence}. What to do: {fnd.action}.")
            for n in c.notes:
                lines.append(f"    - note: {n}.")
    if result.book_lme:
        bad_lme = [t for t in result.book_lme if t.verdict != OK]
        lines += ["", f"  LME tickets: {len(result.book_lme)} open, {len(bad_lme)} flagged.",
                  f"  {'Trade':<14} {'Metal':<8} {'Traded':<10} {'Prompt':<10} {'Tonnes':>10} {'Fill':>10}  Verdict, "
                  "place on the curve"]
        for t in result.book_lme:
            k = t.ticket
            lines.append(f"  {k.trade_id:<14} {k.root_id:<8} {k.trade_date:<10} {k.prompt:<10} {k.tonnes:>+10g} "
                         f"{_g(k.fill):>10}  {t.verdict}, {t.position or '-'}")
        for t in bad_lme:
            for fnd in t.findings:
                lines.append(f"    - {t.ticket.trade_id} {fnd.verdict}: {fnd.evidence}. What to do: {fnd.action}.")
        if result.book_lme and result.book_lme[0].notes:
            lines.append(f"  note: {result.book_lme[0].notes[-1]}.")
    return lines


def _field_rows(result: CheckResult) -> Tuple[List[str], List[List[str]]]:
    value_fields = list(dict.fromkeys((*ROOT_FIELDS, *lme_fields(), *OPTION_FIELDS, CHAIN_FIELD)))
    columns = ["part", "root_id", "instrument_id", "security", "verdict", "answered", "security_error",
               "field_errors", *value_fields]
    rows: List[List[str]] = []

    def cell(value) -> str:
        if isinstance(value, (list, tuple)):          # a bulk field: how many rows, not the rows
            return f"{len(value)} rows"
        return _text(value)

    def emit(part: str, root_id: str, instrument_id: str, security: str, verdict: str,
             answer: Optional[Answer]) -> None:
        a = answer or Answer(security, {}, answered=False)
        extra = [cell(a.fields.get(f)) for f in value_fields]
        errors = "; ".join(f"{k}: {v}" for k, v in a.field_errors.items())
        rows.append([part, root_id, instrument_id, security, verdict, "yes" if a.answered else "no",
                     a.security_error, errors, *extra])

    for r in result.roots:
        if r.asked:
            emit("root", r.root.root_id, "", r.ticker, r.verdict, r.answer)
    for m in result.lme:
        for pl in m.pillars:
            if pl.asked:
                emit(f"lme_{pl.kind.lower()}", m.root.root_id, "", pl.ticker, pl.verdict, pl.answer)
    for o in result.options:
        if o.asked:
            emit("option_chain", o.root.root_id, "", o.ticker, o.verdict, o.chain_answer)
        if o.picked:
            emit("option", o.root.root_id, o.canonical, o.picked, o.verdict, o.picked_answer)
        if o.ours:
            emit("option_our_form", o.root.root_id, o.canonical, o.ours, o.verdict, o.ours_answer)
    for c in result.contracts:
        if c.future.ticker:
            emit("contract", c.future.root_id, c.future.instrument_id, c.future.ticker, c.verdict, c.answer)
    for c in result.book_options:
        if c.future.ticker:
            emit("book_option", c.future.root_id, c.future.instrument_id, c.future.ticker, c.verdict, c.answer)
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
    ap.add_argument("--lme", action="store_true",
                    help="the LME curve tickers (cash, 3M, first monthly) of the LME metals; with --options, both; "
                         "without either flag every part runs")
    ap.add_argument("--options", action="store_true",
                    help="the options on futures: each option_style root's option chain and one option of it")
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
    parts = tuple(p for p, on in ((PART_LME, args.lme), (PART_OPTIONS, args.options)) if on) or ALL_PARTS
    today = as_of or lme_today()
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
                        limit=args.limit, search=args.search, parts=parts, today=today)
    except ValueError as exc:
        print(str(exc))
        return 1
    if the_plan.empty:
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
              + (f", then up to {the_plan.max_option_followups} option tickers in up to "
                 f"{the_plan.max_followup_requests} more" if the_plan.max_option_followups else "")
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
    for label, items, verdicts in (("LME metals", result.lme, LME_VERDICTS),
                                   ("Option roots", result.options, OPTION_VERDICTS)):
        if items:
            tally = {v: sum(1 for i in items if i.verdict == v) for v in verdicts}
            print(f"{label}: " + ", ".join(f"{n} {v}" for v, n in tally.items() if n) + ".")
    if book is not None:
        bad_contracts = sum(1 for c in result.contracts if c.verdict != OK)
        bad_spots = sum(1 for s in result.spots if s.verdict != OK)
        print(f"Book: {len(result.contracts)} contracts, {bad_contracts} flagged; "
              f"{len(result.spots)} spots, {bad_spots} flagged"
              + (f"; {len(result.book_options)} options, {sum(1 for c in result.book_options if c.verdict != OK)} "
                 f"flagged" if result.book_options else "")
              + (f"; {len(result.book_lme)} LME tickets, {sum(1 for t in result.book_lme if t.verdict != OK)} "
                 f"flagged" if result.book_lme else "") + ".")
    code = result.code_findings()
    if code:
        print(f"For the housekeeper: {len(code)} finding{'s' if len(code) != 1 else ''} in code, listed at the end "
              "of the report.")
    ws = worksheet_rows(result)
    print(f"Report:    {paths['report']}")
    print(f"Fields:    {paths['fields']}")
    print(f"Worksheet: {paths['worksheet']} ({len(ws)} rows, {sum(1 for w in ws if w['apply'] == 'yes')} "
          "pre-filled 'yes')")
    return 1 if result.needs_attention else 0


if __name__ == "__main__":
    sys.exit(main())
