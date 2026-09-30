"""The Bloomberg ticker check (bbg-ticker-check lane), run on request at the Bloomberg PC.

    python -m data.bloomberg.ticker_check --dry-run              # what would be asked; asks nothing
    python -m data.bloomberg.ticker_check --limit 5              # the first five roots first
    python -m data.bloomberg.ticker_check --sector energy --search
    python -m data.bloomberg.ticker_check --book --db data/raw/risk.db
    python -m data.bloomberg.ticker_check --lme --options         # only the LME curve and the options
    python -m data.bloomberg.ticker_check --desk --db data/raw/risk.db   # only the desk checks

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

7. Desk checks (``--desk``, 2026-09-29; ``plan_desk`` / ``run_desk``): the open questions only a
   terminal settles, each PASS / WARN / FAIL / SKIPPED with a plain line. (1) the book's futures'
   PX_LAST against PX_SETTLE over the last DESK_DAYS days, per exchange in ticks (FUT_TICK_SIZE);
   (2) their OPEN_INT and PX_VOLUME beside the figures the last pull stored in the book's own
   ``price_history`` (``contract_liquidity``, the Risk tab's Days to exit input); (3) OPEN_INT /
   PX_VOLUME on each held LME metal's 3M and our own monthly prompt tickers (the book's prompt
   months and the next LME_MONTHS_AHEAD); (4) a year of PX_LAST on the SGX USD/CNH future in
   several ticker forms; (5) physical / cash settlement (DELIVERY_FIELDS, candidates) against the
   ``delivery`` column; (6) CALENDAR_NON_SETTLEMENT_DATES per exchange calendar against
   ``config/calendars/``; (7) the price history on file in the book's ``price_history`` per held
   root (days, first and last close, the replay dates it reaches), and (8) whether Bloomberg's
   contract dates are stored, both from the book database alone. The report ends with a short
   "Manual checks" list. The desk checks never change the exit code, and when Bloomberg cannot
   be reached they still write the report (their Bloomberg checks SKIPPED, 7 and 8 filled).

8. Pull tickers (``--pull``, 2026-09-30; ``pull_list`` / ``_run_pull``): the EXACT securities
   "Pull Bloomberg now" asks for on the book date, read from the app's own functions (the
   library's needs, the LME step's pillars, the backfill's forward tenors, the risk history's
   contract chains, FX pairs and LME cash / 3M), each asked once (PULL_FUTURE_FIELDS for a
   future, PULL_FIELDS otherwise) and judged OK, CHECK, NO PRICE, UNKNOWN SECURITY (Bloomberg's
   words quoted) or NO ANSWER; a need the app cannot ask is NOT ASKED with its reason. An expired
   contract of a risk-history chain is judged only on a security error, with at most one short
   history request for those reference data refuses. The report's section "Tickers the pull asks
   for" lists problems first, then OK, one line each.

A plain run does parts 1, 4, 5 and 7; ``--lme``, ``--options``, ``--pull`` and / or ``--desk`` run
only those parts. The book check and the pull tickers run whenever a book is given, except with
``--desk`` alone.

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

``check_book(db_path, ...)`` (2026-09-30) is the Data tab's "Run Bloomberg check" button: the book
part only (the roots the book holds, its futures, options on futures, LME curve and tickets, and
conversion spots), no desk checks, search or option chains, no report file; it returns a dict of
plain rows and never raises.

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
from datetime import date, datetime, timedelta, timezone
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
# The desk checks (2026-09-29): a plain run adds them after the parts above; --desk runs them alone.
PART_DESK = "desk"
PASS, WARN, FAIL, SKIPPED = "PASS", "WARN", "FAIL", "SKIPPED"
DESK_STATUSES = (FAIL, WARN, PASS, SKIPPED)

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


# --------------------------------------------------------------------------- the pull's own tickers (2026-09-30)
#
# User, 2026-09-30: "i need the bloomberg diagnostics tool to make sure its pulling the correct
# tickers - and then provide a report i can send to you". The other parts check tickers this
# module builds itself; this part lists the EXACT securities "Pull Bloomberg now" asks for on the
# book date, read from the app's own functions and never re-derived: the Bloomberg library's rows
# in force (`library.needed_on`: spots, forward curves, futures and option prices, underlyings,
# contract dates, the option inputs' OIS and vol tickers), the LME step's pillars
# (`library.lme_curves_needed`, what `live._lme_step` asks), the backfill's forward tenor tickers
# (`backfill._tenors_needed` / `_tenor_tickers`) and the risk history (`library.risk_history_needs`:
# the contract chains, expired months included, the FX pairs, the LME cash and 3M). Each ticker is
# asked once, in batches, and judged OK, CHECK, NO PRICE, UNKNOWN SECURITY or NO ANSWER; a row the
# app itself cannot ask is NOT ASKED with the app's reason.

PART_PULL = "pull"
PULL_FIELDS = ("NAME", "CRNCY", "EXCH_CODE", "PX_LAST", "PX_MID", "LAST_UPDATE_DT")
PULL_FUTURE_FIELDS = PULL_FIELDS + ("FUT_CONT_SIZE", "FUT_VAL_PT")
PULL_PROBE_DAYS = 10          # the history probe reaches this many calendar days before an expired contract's end
PULL_STALE_DAYS = 7           # a live need whose LAST_UPDATE_DT is older than this is a CHECK
PULL_OK, PULL_CHECK, PULL_NO_PRICE, PULL_UNKNOWN, PULL_NO_ANSWER, PULL_NOT_ASKED = (
    "OK", "CHECK", "NO PRICE", "UNKNOWN SECURITY", "NO ANSWER", "NOT ASKED")
PULL_VERDICTS = (PULL_UNKNOWN, PULL_NO_PRICE, PULL_NO_ANSWER, PULL_CHECK, PULL_NOT_ASKED, PULL_OK)
PK_FUTURE, PK_OPTION, PK_FX, PK_TENOR, PK_LME, PK_OIS, PK_VOL = (
    "future", "option", "fx", "fx_tenor", "lme", "ois", "vol")
SRC_PNL, SRC_DATES, SRC_LME, SRC_OPTION_INPUTS, SRC_TENORS, SRC_RISK = (
    "P&L marks", "contract dates", "LME curve", "option inputs", "past-close forward tenors", "risk history")
PULL_SOURCES = (SRC_PNL, SRC_DATES, SRC_LME, SRC_OPTION_INPUTS, SRC_TENORS, SRC_RISK)
STALE = "STALE"                                  # an internal finding verdict: a CHECK
_FINDING_TO_PULL = {NO_ANSWER: PULL_NO_ANSWER, NOT_FOUND: PULL_UNKNOWN, NO_PRICE: PULL_NO_PRICE}
# The lane whose code builds each kind of ticker, told to the housekeeper when Bloomberg refuses one.
_PULL_OWNERS = {PK_OPTION: OPTION_OWNER, PK_FX: "bbg-library", PK_TENOR: "bbg-live", PK_LME: LME_OWNER,
                PK_OIS: "bbg-curves", PK_VOL: "bbg-curves"}
_LME_LABELS = {"CASH": "cash", "3M": "3-month"}


@dataclass
class PullItem:
    """One security the pull asks for (or a need it cannot ask: ``reason``), every purpose kept."""

    ticker: str                                   # '' when the app has no ticker to ask
    kind: str                                     # PK_*
    what: str                                     # plain words: 'WTI Dec26', 'USDCNH', 'LME copper cash'
    root_id: str = ""
    pair: str = ""                                # FX pair ('USDCNH') or OIS currency
    instrument_id: str = ""
    purposes: List[str] = field(default_factory=list)
    sources: Set[str] = field(default_factory=set)
    trade_ids: Set[str] = field(default_factory=set)
    live: bool = False                            # a need of today's pull, not the risk history alone
    history_end: str = ""                         # risk history: the last day asked of this security
    expired: bool = False                         # a risk-history contract past its last trade
    reason: str = ""                              # NOT ASKED: the app's own words
    answer: Optional[Answer] = None
    probe: Optional[History] = None
    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def asked(self) -> bool:
        return bool(self.ticker) and not self.reason

    @property
    def verdict(self) -> str:
        if not self.asked:
            return PULL_NOT_ASKED
        if not self.findings:
            return PULL_OK
        return min((_FINDING_TO_PULL.get(f.verdict, PULL_CHECK) for f in self.findings), key=PULL_VERDICTS.index)


@dataclass
class PullList:
    """The pull's securities on one book date (``pull_list``), nothing asked yet."""

    db_path: Path
    as_of: date
    items: List[PullItem]
    errors: List[str] = field(default_factory=list)   # a source the list could not be read from, in words
    roots: Mapping[str, ContractRoot] = field(default_factory=dict, repr=False)   # the universe the list was read with

    @property
    def asked_items(self) -> List[PullItem]:
        return [it for it in self.items if it.asked]

    @property
    def future_tickers(self) -> List[str]:
        return [it.ticker for it in self.asked_items if it.kind == PK_FUTURE]

    @property
    def other_tickers(self) -> List[str]:
        return [it.ticker for it in self.asked_items if it.kind != PK_FUTURE]

    @property
    def tickers(self) -> List[str]:
        return self.future_tickers + self.other_tickers

    def requests(self, batch_size: int) -> int:
        size = max(1, batch_size)
        return math.ceil(len(self.future_tickers) / size) + math.ceil(len(self.other_tickers) / size)

    @property
    def probe_requests(self) -> int:
        """At most one short history request, for the expired contracts reference data refuses."""
        return 1 if any(it.expired for it in self.asked_items) else 0

    def source_counts(self) -> Dict[str, int]:
        return {s: sum(1 for it in self.items if s in it.sources) for s in PULL_SOURCES}


def _lme_pillar_words(root: Optional[ContractRoot], root_id: str, kind: str, pillar_date: str) -> str:
    name = _plain_root_name(root, root_id)
    if kind in _LME_LABELS:
        return f"{name} {_LME_LABELS[kind]}"
    try:
        d = date.fromisoformat(str(pillar_date)[:10])
        return f"{name} {d.day} {_MONTH_ABBR[d.month - 1]}{d.year % 100:02d} prompt"
    except ValueError:
        return f"{name} {kind.lower()} {pillar_date}"


def pull_list(db_path, as_of: date, roots: Optional[Mapping[str, ContractRoot]] = None) -> PullList:
    """The securities "Pull Bloomberg now" asks for on ``as_of``, from the app's own functions,
    through a read-only connection (the library is worked out in memory when it is out of date:
    nothing is written). De-duplicated by ticker, every purpose kept. Raises BookError when the
    database cannot be read; a source that fails is named in ``errors`` and the rest is listed."""
    path = Path(db_path)
    if not path.exists():
        raise BookError(f"no database at {path}")
    roots = dict(roots) if roots is not None else load_roots()
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60.0)
    except sqlite3.Error as exc:
        raise BookError(f"cannot open {path} read-only: {exc}") from None
    iso = as_of.isoformat()
    ref_year = as_of.year
    items: Dict[str, PullItem] = {}
    errors: List[str] = []

    def item(ticker: str, kind: str, what: str, **kw) -> PullItem:
        ticker = str(ticker or "").strip()
        key = ticker.upper() if ticker and not kw.get("reason") else \
            f"NOT ASKED|{kind}|{kw.get('instrument_id') or kw.get('root_id') or what}"
        it = items.get(key)
        if it is None:
            it = items[key] = PullItem(ticker, kind, what, **kw)
        return it

    def add(it: PullItem, purpose: str, source: str, trade_ids: Iterable[str] = (), live: bool = True) -> None:
        if purpose not in it.purposes:
            it.purposes.append(purpose)
        it.sources.add(source)
        it.trade_ids.update(str(t) for t in trade_ids if t)
        it.live = it.live or live

    try:
        if not _has_object(conn, "instruments") or not _has_object(conn, "trades"):
            raise BookError(f"{path} holds no book (no instruments or trades table): upload a blotter first")
        from data.bloomberg import library
        base_of = {str(i): str(b or "") for i, b in conn.execute("SELECT instrument_id, base_ccy FROM instruments")}
        listed = tuple(getattr(library, "LISTED_OPTION_PRODUCTS", ("EQ_OPTION", "CMDTY_OPTION")))

        # 1. The library's rows in force: what the live pull, the contract dates and the curves /
        #    vol steps ask (live.needed_live reads exactly this).
        try:
            needed = list(library.needed_on(conn, iso, include_unrequestable=True))
        except Exception as exc:  # noqa: BLE001 -- a library problem is reported, the rest still listed
            errors.append(f"the Bloomberg library could not be read ({type(exc).__name__}: {exc})")
            needed = []
        lme_trades: Dict[str, Set[str]] = {}
        fwd_legs: Dict[str, List[dict]] = {}
        sets: Dict[Tuple[str, str], Set[str]] = {}
        for r in needed:
            kind, key, role, product = r["kind"], str(r["key"]), r.get("role"), r.get("product")
            tid = str(r.get("trade_id") or "")
            ticker = str(r.get("bbg_ticker") or "").strip()
            requestable = bool(r.get("requestable", True))
            if library.is_lme_row(r):
                if kind == library.LME_CURVE:
                    lme_trades.setdefault(key, set()).add(tid)
                if not requestable:
                    it = item("", PK_LME, f"{_plain_root_name(roots.get(key), key)} curve", root_id=key,
                              instrument_id=key, reason=r.get("reason") or "not requestable")
                    add(it, "P&L mark: LME curve", SRC_LME, [tid])
                continue
            if kind in library.SET_KINDS:
                sets.setdefault((kind, key), set()).add(tid)
                continue
            root_id = base_of.get(key, "")
            root = roots.get(root_id)
            if kind in ("SPOT", "FWD_OUTRIGHT"):
                pk, what, extra = PK_FX, key, {"pair": key}
            elif product in listed and not (kind == "FUTURE_PX" and role == library.ROLE_UNDERLYING):
                pk, what, extra = PK_OPTION, _contract_words(key, "CMDTY_OPTION", root, ref_year), {"root_id": root_id}
            else:
                pk, what, extra = PK_FUTURE, _contract_words(key, "FUTURE", root, ref_year), {"root_id": root_id}
            if kind == "SPOT":
                if role == library.ROLE_CONVERSION:
                    ccy = key[3:6] if key.startswith("USD") else key[:3]
                    purpose, source = f"USD conversion of {ccy}", SRC_PNL
                else:
                    purpose, source = ("P&L mark: FX option's pair spot" if product == "FX_OPTION"
                                       else "P&L mark: spot"), SRC_PNL
            elif kind == "FWD_OUTRIGHT":
                if str(r.get("settle_date") or "") > iso:
                    fwd_legs.setdefault(key, []).append(r)
                    purpose = "P&L mark: forward curve (FWD_CURVE)"
                else:
                    purpose = "P&L mark: spot (a leg settling today)"
                source = SRC_PNL
            elif kind == "FUTURE_PX":
                if role == library.ROLE_UNDERLYING:
                    purpose, source = "Option Greeks: underlying future's price", SRC_OPTION_INPUTS
                elif pk == PK_OPTION:
                    purpose, source = "P&L mark: option price (PX_MID)", SRC_PNL
                else:
                    purpose, source = "P&L mark: futures price", SRC_PNL
            elif kind == library.CONTRACT_DATES:
                purpose = "Contract dates: option expiry" if pk == PK_OPTION else \
                    "Contract dates: last trade and first notice"
                source = SRC_DATES
            else:
                continue                      # a kind the pull does not ask a security for
            if not requestable or not ticker:
                it = item("", pk, what, instrument_id=key,
                          reason=r.get("reason") or f"no Bloomberg ticker for {key}", **extra)
            else:
                it = item(ticker, pk, what, instrument_id=key, **extra)
            add(it, purpose, source, [tid])

        # The option inputs: each OIS curve's and vol smile's own tickers (the curves and vol steps).
        for (kind, key), tids in sorted(sets.items()):
            if kind == "OIS_CURVE":
                try:
                    from data.bloomberg import rates_marketdata as rm
                    specs = list(rm.ois_curve(key))
                except Exception:  # noqa: BLE001 -- no curve in scope for this currency: nothing is asked
                    continue
                for spec in specs:
                    add(item(spec.ticker, PK_OIS, f"{key} OIS {spec.tenor}", pair=key, instrument_id=key),
                        f"Option Greeks: {key} discount curve", SRC_OPTION_INPUTS, tids)
            elif kind == "VOL_SMILE":
                try:
                    from data.bloomberg import vol_marketdata as vm
                    tickers = [(t, q, vm.vol_ticker(key, t, q)) for t in vm.VOL_TENORS for q in vm.VOL_QUOTE_TYPES]
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"the {key} vol smile tickers could not be listed ({type(exc).__name__}: {exc})")
                    continue
                for tenor, quote, ticker in tickers:
                    add(item(ticker, PK_VOL, f"{key} vol {tenor} {quote}", pair=key, instrument_id=key),
                        "FX option vol smile", SRC_OPTION_INPUTS, tids)

        # 2. The LME step's pillars, exactly as live._lme_step reads them.
        try:
            curves = list(library.lme_curves_needed(conn, iso))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"the LME curves could not be listed ({type(exc).__name__}: {exc})")
            curves = []
        for entry in curves:
            rid = entry["root_id"]
            for p in entry.get("pillars") or []:
                if not p.get("ticker"):
                    continue
                it = item(p["ticker"], PK_LME, _lme_pillar_words(roots.get(rid), rid, p["kind"], p["pillar_date"]),
                          root_id=rid, instrument_id=rid)
                add(it, f"P&L mark: LME curve ({_LME_LABELS.get(p['kind'], 'monthly')} pillar)", SRC_LME,
                    lme_trades.get(rid, ()))

        # 3. The backfill's forward tenor tickers for the open forward legs (past closes).
        if fwd_legs:
            try:
                from data.bloomberg.backfill import _tenor_tickers, _tenors_needed
                from engine.pnl.calendar import load_holidays
                holidays = load_holidays()
                for pair, legs in sorted(fwd_legs.items()):
                    for tenor, ticker in _tenor_tickers(pair, _tenors_needed(pair, legs, as_of, holidays)).items():
                        add(item(ticker, PK_TENOR, f"{pair} {tenor} forward", pair=pair, instrument_id=pair),
                            "Past closes: forward tenor", SRC_TENORS, (r.get("trade_id") for r in legs))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"the backfill's forward tenors could not be listed ({type(exc).__name__}: {exc})")

        # 4. The risk history (price_history): the contract chains, the FX pairs, the LME cash and 3M.
        try:
            risk = list(library.risk_history_needs(conn, as_of))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"the risk history could not be listed ({type(exc).__name__}: {exc})")
            risk = []
        for r in risk:
            rid, iid = str(r.get("root_id") or ""), str(r["instrument_id"])
            ticker = str(r.get("bbg_ticker") or "").strip()
            reason = "" if r.get("requestable", True) and ticker else (r.get("reason") or "no Bloomberg ticker")
            if r["kind"] == library.RISK_KIND_CONTRACT:
                root = roots.get(rid)
                it = item(ticker, PK_FUTURE, _contract_words(iid, "FUTURE", root, ref_year), root_id=rid,
                          instrument_id=iid, reason=reason)
                purpose = f"Risk history ({_plain_root_name(root, rid)} chain)"
            elif r["kind"] == library.RISK_KIND_FX:
                it = item(ticker, PK_FX, iid, pair=iid, instrument_id=iid, reason=reason)
                purpose = "Risk history (FX)"
            else:
                label = iid.rsplit(" ", 1)[-1]
                it = item(ticker, PK_LME, _lme_pillar_words(roots.get(rid), rid, label, ""), root_id=rid,
                          instrument_id=iid, reason=reason)
                purpose = f"Risk history (LME {_LME_LABELS.get(label, label)})"
            add(it, purpose, SRC_RISK, live=False)
            it.history_end = max(it.history_end, str(r.get("end") or ""))

        # 5. The rows the parser could not identify (hard rule 6): never asked until the contract list knows them.
        unrecognised = getattr(library, "UNRECOGNISED", "UNRECOGNISED")
        trades_table = "trades_official" if _has_object(conn, "trades_official") else "trades"
        for iid, tid, symbol in conn.execute(
                f"SELECT instrument_id, trade_id, broker_symbol FROM {trades_table} WHERE product = ? "
                "AND trade_date <= ? ORDER BY instrument_id, trade_id", (unrecognised, iso)):
            shown = str(symbol or "").strip() or str(iid).split(":", 1)[-1]
            it = item("", PK_FUTURE, f"{shown} (contract not recognised)", instrument_id=str(iid),
                      reason="the upload could not identify this contract, so the pull never asks for it; add it to "
                             "config/contracts.csv (or fix the file's symbol), then upload the blotter again")
            add(it, "P&L mark", SRC_PNL, [tid])
    except sqlite3.Error as exc:
        raise BookError(f"cannot read the book in {path}: {exc}") from None
    finally:
        conn.close()
    for it in items.values():
        it.expired = (not it.live and it.kind == PK_FUTURE and bool(it.history_end) and it.history_end < iso)
        if it.expired and "(expired)" not in it.what:
            it.what += " (expired)"
    order = (PK_FUTURE, PK_OPTION, PK_LME, PK_FX, PK_TENOR, PK_OIS, PK_VOL)
    out = sorted(items.values(), key=lambda it: (order.index(it.kind), it.root_id or it.pair, it.expired,
                                                 it.instrument_id, it.ticker))
    return PullList(path, as_of, out, errors, roots)


def pull_describe(pl: PullList, batch_size: int) -> List[str]:
    """The dry-run lines of the pull part: how many tickers, from where, in how many requests."""
    asked = pl.asked_items
    n_not = len(pl.items) - len(asked)
    reqs = pl.requests(batch_size)
    counts = pl.source_counts()
    lines = [f"Tickers the pull asks for on {pl.as_of.isoformat()}: {len(asked)} securit"
             f"{'ies' if len(asked) != 1 else 'y'} to ask ({len(pl.future_tickers)} futures with their contract "
             f"fields, {len(pl.other_tickers)} others) in {reqs} ReferenceDataRequest{'s' if reqs != 1 else ''} of up "
             f"to {batch_size}"
             + (", then at most one short history request for expired contracts Bloomberg refuses"
                if pl.probe_requests else "")
             + f"; {n_not} need{'s' if n_not != 1 else ''} the app cannot ask (NOT ASKED)."]
    if any(counts.values()):
        lines.append("  From: " + ", ".join(f"{counts[s]} {s}" for s in PULL_SOURCES if counts[s])
                     + " (one ticker can serve several).")
    for e in pl.errors:
        lines.append(f"  Not listed: {e}.")
    return lines


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
    pull: Optional[PullList] = None           # the pull's own tickers (``pull_list``), when that part runs

    @property
    def pull_tickers(self) -> List[str]:
        return self.pull.tickers if self.pull is not None else []

    @property
    def pull_requests(self) -> int:
        return self.pull.requests(self.batch_size) if self.pull is not None else 0

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
                                    self.book_option_tickers, self.spot_tickers, self.pull_tickers))

    @property
    def requests(self) -> int:
        size = max(1, self.batch_size)
        plain = (self.root_tickers, self.lme_tickers, self.contract_tickers, self.book_option_tickers,
                 self.spot_tickers)
        return (sum(math.ceil(len(t) / size) for t in plain)
                + math.ceil(len(self.chain_tickers) / max(1, min(size, CHAIN_BATCH_SIZE))) + self.pull_requests)

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
                    or self.spots or self.book_options or self.book_lme or (self.pull is not None and self.pull.items))

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
        if self.pull is not None:
            lines += pull_describe(self.pull, self.batch_size)
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

    def bulk(self, securities: Sequence[str], field_name: str,
             overrides: Optional[Mapping[str, str]] = None) -> Dict[str, Answer]:
        """A ReferenceDataRequest for one bulk field (OPT_CHAIN, CALENDAR_NON_SETTLEMENT_DATES),
        with optional field overrides: each security's answer carries the field as a list of
        rows, each row a {sub-field: value} dict. The app's request helper reads one value per
        field, so a bulk field has its own loop here."""
        blpapi = self._blpapi
        request = self.service.createRequest("ReferenceDataRequest")
        for s in securities:
            request.getElement("securities").appendValue(s)
        request.getElement("fields").appendValue(field_name)
        for name, value in (overrides or {}).items():
            o = request.getElement("overrides").appendElement()
            o.setElement("fieldId", name)
            o.setElement("value", str(value))
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

    def history(self, securities: Sequence[str], fields: Sequence[str], start: date, end: date) -> Dict[str, History]:
        """A HistoricalDataRequest over [start, end] through the app's own helper
        (``pull_marks.fetch_historical_series``): each security's days, with Bloomberg's own words
        for a refused security or field. A timeout is an unanswered History, never a crash."""
        timed_out = ""
        data: Dict[str, Dict[str, Dict[str, object]]] = {}
        try:
            data = self._pm.fetch_historical_series(self.session, self.service, list(securities), list(fields),
                                                    start, end, diag=self.diag, tag={"purpose": PURPOSE})
        except self._pm.BloombergRequestError as exc:
            timed_out = exc.detail or exc.classification
        except Exception as exc:  # noqa: BLE001 -- the session failed under the request: unreachable
            raise BloombergUnavailable(f"the history request failed ({type(exc).__name__}: {exc})") from None
        raw = (self.diag.requests[-1].get("raw_response") if self.diag.requests else None) or []
        out: Dict[str, History] = {}
        for sec in raw:
            err = sec.get("securityError") or None
            name = str(sec.get("security"))
            out[name] = History(
                name, dict(data.get(name) or {}),
                security_error=str(err.get("message") or "security error") if err else "",
                field_errors={str(fx.get("fieldId") or "?"): str(fx.get("message") or "")
                              for fx in sec.get("fieldExceptions") or []})
        if timed_out:
            for s in securities:
                out.setdefault(s, History(s, {}, security_error=f"no answer: {timed_out}", answered=False))
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
    # progress(done, total, words), called before each request goes out (``run_check(progress=)``)
    progress: Optional[Callable[[int, int, str], None]] = field(default=None, repr=False, compare=False)
    progress_total: int = 0
    pull: List[PullItem] = field(default_factory=list)      # the pull's own tickers, judged (``_run_pull``)

    def pull_counts(self) -> Dict[str, int]:
        return {v: sum(1 for it in self.pull if it.verdict == v) for v in PULL_VERDICTS}

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
                or any(t.verdict != OK for t in self.book_lme) or any(it.verdict != PULL_OK for it in self.pull))

    def code_findings(self) -> List[Tuple[str, str, Finding]]:
        """(lane, subject, finding) of every finding whose fix is code, not config/contracts.csv:
        the lines the report asks the user to pass to the housekeeper."""
        out: List[Tuple[str, str, Finding]] = []
        for m in self.lme:
            for p in m.pillars:
                out += [(f.owner, f"{m.root.root_id} {p.label} [{p.ticker}]", f) for f in p.findings if f.owner]
        for o in self.options:
            out += [(f.owner, f"{o.root.root_id} options [{o.picked or o.ticker}]", f) for f in o.findings if f.owner]
        out += _pull_code_findings(self.pull)
        return out

    @property
    def any_answered(self) -> bool:
        return any(a.answered for _part, a in self.answers.values())


_PART_WORDS = {"root": "contract roots", "lme": "LME curve tickers", "contract": "futures",
               "book_option": "options on futures", "spot": "USD conversion spots", "option_chain": "option chains",
               "option": "options", "pull": "tickers the pull asks for",
               "pull_probe": "expired contracts' history (a short probe)"}


def _tell(result: CheckResult, n: int, part: str) -> None:
    """Report progress before a request goes out; a failing callback never stops the check."""
    if result.progress is None:
        return
    words = f"Asking Bloomberg for {n} {_PART_WORDS.get(part, part)}"
    try:
        result.progress(result.requests_sent, max(result.progress_total, result.requests_sent + 1), words)
    except Exception:  # noqa: BLE001 -- progress is display only
        pass


def _ask(client, tickers: Sequence[str], fields: Sequence[str], batch_size: int, result: CheckResult,
         part: str) -> Dict[str, Answer]:
    out: Dict[str, Answer] = {}
    for batch in _chunks(list(tickers), max(1, batch_size)):
        _tell(result, len(batch), part)
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
        _tell(result, len(batch), part)
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


def run_check(client, the_plan: Plan, *, log: Callable[[str], None] = print, host: str = "",
              progress: Optional[Callable[[int, int, str], None]] = None) -> CheckResult:
    """Ask Bloomberg what ``the_plan`` lists, through ``client`` (``reference(securities, fields)``
    -> {security: Answer}; ``search(query, max_results)`` -> [(security, description)]), and
    judge every answer. Writes nothing. ``progress(done, total, words)``, when given, is called
    before each request goes out (total: the plan's requests and its most option follow-ups)."""
    result = CheckResult(the_plan, [], [], [], host=host, progress=progress,
                         progress_total=(the_plan.requests + the_plan.max_followup_requests
                                         + (the_plan.pull.probe_requests if the_plan.pull is not None else 0)))
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
    if the_plan.pull is not None:
        _run_pull(client, the_plan.pull, result, size)
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


def _run_pull(client, pl: PullList, result: CheckResult, size: int) -> None:
    """Ask each ticker of the pull once (futures with their contract fields, the rest with
    PULL_FIELDS), then at most one short history request for the expired contracts reference data
    refuses, and judge every one into ``result.pull``."""
    items = [dataclasses.replace(it, purposes=list(it.purposes), sources=set(it.sources),
                                 trade_ids=set(it.trade_ids), answer=None, probe=None, findings=[], notes=[])
             for it in pl.items]
    answers: Dict[str, Answer] = {}
    futures = list(dict.fromkeys(pl.future_tickers))
    others = list(dict.fromkeys(pl.other_tickers))
    if futures:
        answers.update(_ask(client, futures, PULL_FUTURE_FIELDS, size, result, "pull"))
    if others:
        answers.update(_ask(client, others, PULL_FIELDS, size, result, "pull"))
    for it in items:
        if it.asked:
            it.answer = answers.get(it.ticker)
    refused = [it for it in items if it.asked and it.expired and it.answer is not None and it.answer.answered
               and it.answer.security_error and not _ENTITLEMENT_RE.search(it.answer.security_error)]
    refused = refused[:max(1, size)]
    history = getattr(client, "history", None)
    if refused and history is not None:
        ends = [date.fromisoformat(it.history_end[:10]) for it in refused]
        start, end = min(ends) - timedelta(days=PULL_PROBE_DAYS), max(ends)
        _tell(result, len(refused), "pull_probe")
        result.requests_sent += 1
        try:
            got = history([it.ticker for it in refused], ("PX_LAST",), start, end) or {}
        except BloombergUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 -- the probe is extra evidence; its failure is said, never fatal
            got = {it.ticker: History(it.ticker, {}, security_error=f"no answer: {type(exc).__name__}: {exc}",
                                      answered=False) for it in refused}
        by_upper = {str(k).upper(): v for k, v in got.items()}
        for it in refused:
            it.probe = got.get(it.ticker) or by_upper.get(it.ticker.upper())
    for it in items:
        judge_pull(it, pl.roots, pl.as_of)
    result.pull = items


def _pull_refusal(it: PullItem, root: Optional[ContractRoot], answer: Answer) -> Finding:
    """Bloomberg refused the security: its own words, and what to change (config or code)."""
    words = answer.security_error
    said = f'Bloomberg said: "{words}"'
    if _ENTITLEMENT_RE.search(words):
        return Finding(NO_PRICE, f"Bloomberg refused {it.ticker!r}. {said}",
                       "this terminal is not entitled to it: ask Bloomberg for the entitlement, then run the check "
                       "again")
    probe = ""
    if it.probe is not None:
        pw = it.probe.security_error or ("no answer" if not it.probe.answered else "no closes in the window asked")
        probe = f'; its daily history was refused too ("{pw}")'
    owner = _PULL_OWNERS.get(it.kind, "")
    bare = _strip_key(it.ticker)
    if it.kind == PK_FUTURE and it.expired:
        owner = "contract-master"     # data.contracts builds every contract ticker, expired ones included
        action = (f"check {bare} on the terminal (SECF <GO>): if Bloomberg names this expired contract another "
                  "way, the request ticker rule (data.contracts.request_ticker) changes; until then its risk "
                  "history is missing")
    elif it.kind == PK_FUTURE:
        owner = ""
        action = (f"{root.root_id}'s bbg_root {root.bbg_root!r} or its yellow key in config/contracts.csv is likely "
                  "wrong: run again with --search, or SECF <GO> on the terminal, and put the right root in bbg_root"
                  if root is not None else "the contract is not in config/contracts.csv")
    elif it.kind == PK_OPTION:
        action = "our option ticker form is not Bloomberg's: run again with --options to compare it with its chain"
    elif it.kind == PK_FX:
        action = f"the pair's ticker comes from the book's instrument row; check {bare} on the terminal"
    elif it.kind == PK_TENOR:
        action = (f"the forward tenor naming ('<pair><tenor> Curncy', pull_marks.tenor_ticker) does not fit "
                  f"{it.pair}: find the tenor's ticker on the terminal")
    elif it.kind == PK_LME:
        action = f"the LME ticker naming (engine.lme) does not fit this metal: find {bare}'s right form on the terminal"
    else:
        action = ("the ticker map (data/bloomberg/rates_marketdata.py or vol_marketdata.py) is wrong for this "
                  "ticker: find the right one on the terminal")
    return Finding(NOT_FOUND, f"Bloomberg does not know {it.ticker!r}. {said}{probe}", action, owner=owner)


def judge_pull(it: PullItem, roots: Mapping[str, ContractRoot], as_of: date) -> None:
    """The verdict on one pull ticker from Bloomberg's answer (``it.answer``, ``it.probe``): its
    findings and notes are rebuilt. An expired contract is judged on a security error only."""
    it.findings, it.notes = [], []
    if not it.asked:
        return
    a, t = it.answer, it.ticker
    root = roots.get(it.root_id) if it.root_id else None
    if a is None or not a.answered:
        words = a.security_error if a is not None else "not asked"
        it.findings.append(Finding(NO_ANSWER, f"Bloomberg did not answer for {t!r} ({words})", "run the check again"))
        return
    if a.security_error:
        p = it.probe
        if p is not None and p.answered and not p.security_error and p.days:
            n = len(p.days)
            it.notes.append(f'reference data refused it ("{a.security_error}"), but its daily history answers '
                            f"({n} close{'s' if n != 1 else ''}, {min(p.days)} to {max(p.days)})")
            return
        it.findings.append(_pull_refusal(it, root, a))
        return
    v = a.fields
    name = str(v.get("NAME") or "").strip()
    bare = _strip_key(t)
    if it.expired:
        # Judged on a security error only: the live contracts of the same root carry the identity checks.
        if _num(v.get("PX_LAST")) is None and _num(v.get("PX_MID")) is None:
            it.notes.append("expired: no reference price, as expected; the pull asks its daily history")
        return
    if _num(v.get("PX_LAST")) is None and _num(v.get("PX_MID")) is None:
        it.findings.append(Finding(
            NO_PRICE, f"{t!r} resolves{f' as {name!r}' if name else ''} but returned no PX_LAST or PX_MID"
                      + _bloomberg_said(a, ("PX_LAST", "PX_MID")),
            f"an entitlement this terminal lacks, or a contract that does not trade: look at {bare} GP on the "
            "terminal"))
    if v.get("LAST_UPDATE_DT") not in (None, ""):
        try:
            updated = to_date(v.get("LAST_UPDATE_DT"))
        except ValueError:
            updated = None
        if updated is not None and (as_of - updated).days > PULL_STALE_DAYS:
            it.findings.append(Finding(
                STALE, f"Bloomberg last updated {t!r} on {updated.isoformat()}, {(as_of - updated).days} days before "
                       f"the book date {as_of.isoformat()}",
                "a stale price: check on the terminal that this is the contract the book means and that it trades"))
    ccy_raw = str(v.get("CRNCY") or "").strip()
    major, minor = currency_parts(ccy_raw)
    exch = str(v.get("EXCH_CODE") or "").strip().upper()
    if it.kind in (PK_FUTURE, PK_OPTION) and root is not None:
        if ccy_raw and not same_currency(major, root.currency):
            fix = ("currency", root.currency, major) if it.kind == PK_FUTURE else ("", "", "")
            it.findings.append(Finding(
                CURRENCY_MISMATCH, f"Bloomberg prices {t!r} in {ccy_raw}; config/contracts.csv says {root.currency} "
                                   f"for {root.root_id}",
                f"if the name and exchange are right, set currency to {major} (the worksheet); if not, the ticker "
                "is another contract and bbg_root is wrong", *fix))
        elif it.kind == PK_FUTURE:
            it.findings.extend(_scale_findings(root, t, v, ccy_raw, major, minor, it.notes))
        if exch and not exchange_matches(root.exchange, exch):
            known = _exchange_named_by(exch)
            meaning = f"which this check reads as {'/'.join(known)}" if known else "a code this check does not know"
            it.findings.append(Finding(
                EXCHANGE_MISMATCH, f"Bloomberg lists {t!r} on exchange code {exch!r}, {meaning}; config/contracts.csv "
                                   f"says {root.exchange}",
                f"confirm on the terminal ({bare} DES) that this is the {root.exchange} contract and not a look-alike"))
        if it.kind == PK_FUTURE:
            problems = _name_problems(root, v)
            if problems:
                it.findings.append(Finding(NAME_CHECK, "; ".join(problems),
                                           f"check on the terminal ({bare} DES) that this ticker is the {root.name}"))
        elif name and not words_fit(root.name, name):
            it.notes.append(f"Bloomberg's name {name!r} has none of the words of {root.name!r}")
    elif it.kind == PK_FX and len(it.pair) == 6 and ccy_raw and not same_currency(major, it.pair[3:]):
        it.findings.append(Finding(
            CURRENCY_MISMATCH, f"Bloomberg prices {t!r} in {ccy_raw}; the pair {it.pair}'s quote currency is "
                               f"{it.pair[3:]}",
            f"check {bare} DES on the terminal: the ticker may name another pair (it comes from the book's "
            "instrument row)", owner="bbg-library"))
    elif it.kind == PK_LME:
        if ccy_raw and not same_currency(major, LME_CURRENCY):
            it.findings.append(Finding(
                CURRENCY_MISMATCH, f"Bloomberg prices {t!r} in {ccy_raw}; an LME metal is priced in {LME_CURRENCY}",
                "the LME ticker is another contract: find the right one on the terminal", owner=LME_OWNER))
        metal = _plain_root_name(root, it.root_id)
        if root is not None and name and not words_fit(metal, name):
            it.findings.append(Finding(
                NAME_CHECK, f"none of the words of {metal!r} are in Bloomberg's name {name!r}",
                f"check on the terminal ({bare} DES) that this is {metal}", owner=LME_OWNER))
        if exch and not exchange_matches("LME", exch):
            it.notes.append(f"Bloomberg lists it on exchange code {exch!r}, not LME")


_PULL_KIND_WORDS = {PK_FUTURE: "contracts", PK_OPTION: "options", PK_FX: "currency pairs", PK_TENOR: "forward tenors",
                    PK_LME: "LME tickers", PK_OIS: "OIS tickers", PK_VOL: "vol tickers"}


def _pull_code_findings(items: Sequence[PullItem]) -> List[Tuple[str, str, Finding]]:
    """The pull's findings whose fix is code, one line per (lane, root or pair, kind, verdict):
    ``(lane, subject, finding)`` as ``CheckResult.code_findings`` gives them."""
    groups: Dict[Tuple[str, str, str, str], List[Tuple[PullItem, Finding]]] = {}
    for it in items:
        label = "expired contracts" if it.expired else _PULL_KIND_WORDS.get(it.kind, it.kind)
        for f in it.findings:
            if f.owner:
                groups.setdefault((f.owner, it.root_id or it.pair or it.kind, label, f.verdict), []).append((it, f))
    out: List[Tuple[str, str, Finding]] = []
    for (owner, group, label, verdict), pairs in groups.items():
        tickers = [it.ticker for it, _f in pairs]
        shown = ", ".join(tickers[:FORM_EXAMPLES]) + (f" +{len(tickers) - FORM_EXAMPLES} more"
                                                      if len(tickers) > FORM_EXAMPLES else "")
        first = pairs[0][1]
        evidence = first.evidence if len(pairs) == 1 else \
            f"{len(pairs)} tickers the pull asks, the first: {first.evidence}"
        out.append((owner, f"{group} {label} [{shown}]", Finding(verdict, evidence, first.action, owner=owner)))
    return out


# --------------------------------------------------------------------------- desk checks (2026-09-29)
#
# User, 2026-09-29: one place to settle every open question that needs a Bloomberg terminal
# ("there are many - hopefully the diagnostics program can help"). Eight checks, each PASS /
# WARN / FAIL / SKIPPED with a plain line. They run on a plain `bbg-check` and alone with
# `--desk`, after the other parts, in their own requests; they never change the exit code
# (they report; the app's rules do not follow from them), write no marks and no trades, and a
# config change they suggest is a worksheet row (delivery, the SGX root) or a line for the
# housekeeper (a calendar file, the LME and SGX history notes). Every field name here that the
# pull does not already use is a best guess until a terminal answers (agent memory
# bloomberg_field_assumptions).

DESK_TITLES = {
    1: "Futures close against exchange settlement",
    2: "Open interest and volume against the stored history",
    3: "LME per-prompt liquidity",
    4: "SGX USD/CNH future history",
    5: "Delivery type",
    6: "Exchange holidays",
    7: "Price history on file",
    8: "Contract dates stored",
}
DESK_DAYS = 5                               # business days of history compared in checks 1 and 2
DESK_WINDOW_CALENDAR_DAYS = 14              # asked back from the as-of, so 5 business days fit round a holiday
DESK_HISTORY_FIELDS = ("PX_LAST", "PX_SETTLE", "OPEN_INT", "PX_VOLUME")
DESK_TICK_FIELDS = ("FUT_TICK_SIZE",)
LME_LIQUIDITY_FIELDS = ("OPEN_INT", "PX_VOLUME")
LME_MONTHS_AHEAD = 3                        # the nearest monthly prompts asked per metal, beside the book's own
HISTORY_FRESH_DAYS = 7                      # check 7: a root's last close on file this old or newer is fresh
PRICE_HISTORY_TABLE = "price_history"       # the book's own Bloomberg history (bbg-backfill writes it on a pull)
SGX_XUC_ROOT = "SGX:XUC"
SGX_HISTORY_DAYS = 365
SGX_MIN_CLOSES = 200                        # a year's history counts as there with this many closes
SGX_FIELDS = ("PX_LAST",)
# Candidate fields for a future's physical / cash settlement: the first that answers is used.
# None has been seen on a terminal (2026-09-29); if none answers, the check is SKIPPED with
# Bloomberg's words, and FLDS <GO> on a front future finds the right one.
DELIVERY_FIELDS = ("FUT_DELIVERY_TYPE", "FUT_SETTLE_TYP", "CASH_SETTLED", "FUT_DLV_TYP")
CALENDAR_FIELD = "CALENDAR_NON_SETTLEMENT_DATES"
CALENDAR_START, CALENDAR_END = date(2026, 1, 1), date(2027, 12, 31)
# Our calendar id -> Bloomberg's calendar code (override SETTLEMENT_CALENDAR_CODE). Empty: each
# calendar is asked on a front future of that exchange with no code, assumed to answer with that
# exchange's own calendar. Fill a code in only when a terminal shows it is needed.
CALENDAR_CODES: Dict[str, str] = {}
REPLAYS = (("the negative-WTI replay", date(2020, 4, 17)), ("the LME nickel replay", date(2022, 3, 7)))
CHINA_EXCHANGE_CODES = ("SHFE", "DCE", "ZCE", "INE", "GFEX")
CALENDAR_OWNER = "exchange-calendars"
HISTORY_OWNER = "risk-history"
SHFE_MANUAL = ("compare one SHFE copper contract's OPEN_INT and PX_VOLUME with SHFE's own daily report "
               "(shfe.com.cn, 成交持仓排名 / daily trading statistics) to confirm one- or two-sided counting")
_DATE_LINE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*(#.*)?$")


@dataclass(frozen=True)
class History:
    """Bloomberg's daily history of one security, in plain Python types."""

    security: str
    days: Mapping[str, Mapping[str, object]]            # ISO date -> {field: value}
    security_error: str = ""
    field_errors: Mapping[str, str] = field(default_factory=dict)
    answered: bool = True


@dataclass
class DeskCheck:
    number: int
    status: str = SKIPPED
    summary: str = ""
    lines: List[str] = field(default_factory=list)
    worksheet: List[Dict[str, str]] = field(default_factory=list)
    housekeeper: List[Tuple[str, str]] = field(default_factory=list)   # (lane, line)

    @property
    def title(self) -> str:
        return DESK_TITLES[self.number]


@dataclass
class DeskPlan:
    """What the desk checks would ask, worked out without asking anything."""

    as_of: date
    book: Optional[Book]
    roots: Mapping[str, ContractRoot]
    futures: List[BookFuture] = field(default_factory=list)          # checks 1, 2 and 8
    options: List[BookFuture] = field(default_factory=list)          # check 8 (read, not asked)
    lme: List[Tuple[ContractRoot, List[Tuple[str, str]]]] = field(default_factory=list)  # 3: (metal, [(label, ticker)])
    lme_note: str = ""
    sgx: Optional[ContractRoot] = None
    sgx_tickers: List[Tuple[str, str]] = field(default_factory=list)  # check 4: (what it is, ticker)
    delivery_roots: List[ContractRoot] = field(default_factory=list)  # check 5, asked
    delivery_placeholders: List[ContractRoot] = field(default_factory=list)
    delivery_why: str = ""                                            # why check 5 has no roots
    calendars: List[Tuple[str, str, str]] = field(default_factory=list)     # check 6: (calendar, security, root id)
    calendars_unasked: List[Tuple[str, str]] = field(default_factory=list)  # (calendar, why)
    history_roots: List[str] = field(default_factory=list)            # check 7
    batch_size: int = BATCH_SIZE

    @property
    def history_db(self) -> Optional[Path]:
        """The book database whose ``price_history`` checks 2 and 7 read (None without a book)."""
        return Path(self.book.db_path) if self.book is not None else None

    @property
    def window(self) -> Tuple[date, date]:
        return self.as_of - timedelta(days=DESK_WINDOW_CALENDAR_DAYS), self.as_of

    @property
    def sgx_window(self) -> Tuple[date, date]:
        return self.as_of - timedelta(days=SGX_HISTORY_DAYS), self.as_of

    @property
    def contract_tickers(self) -> List[str]:
        return list(dict.fromkeys(f.ticker for f in self.futures if f.ticker))

    @property
    def lme_tickers(self) -> List[str]:
        return list(dict.fromkeys(t for _m, pairs in self.lme for _label, t in pairs))

    @property
    def sgx_ticker_list(self) -> List[str]:
        return list(dict.fromkeys(t for _label, t in self.sgx_tickers))

    @property
    def delivery_tickers(self) -> List[str]:
        return list(dict.fromkeys(generic_ticker(r) for r in self.delivery_roots))

    @property
    def calendar_tickers(self) -> List[str]:
        return list(dict.fromkeys(s for _c, s, _r in self.calendars))

    def _n(self, tickers: Sequence[str]) -> int:
        return math.ceil(len(tickers) / max(1, self.batch_size))

    @property
    def calendar_requests(self) -> int:
        groups: Dict[str, List[str]] = {}
        for cal, sec, _r in self.calendars:
            groups.setdefault(CALENDAR_CODES.get(cal, ""), []).append(sec)
        return sum(self._n(list(dict.fromkeys(v))) for v in groups.values())

    @property
    def securities(self) -> int:
        """Securities asked (a contract of checks 1-2 counts twice: its history and its tick size)."""
        return (2 * len(self.contract_tickers) + len(self.lme_tickers) + len(self.sgx_ticker_list)
                + len(self.delivery_tickers) + len(self.calendar_tickers))

    @property
    def requests(self) -> int:
        return (2 * self._n(self.contract_tickers) + self._n(self.lme_tickers) + self._n(self.sgx_ticker_list)
                + self._n(self.delivery_tickers) + self.calendar_requests)

    @property
    def needs_bloomberg(self) -> bool:
        return self.securities > 0

    def describe(self) -> List[str]:
        def s(n: int, word: str = "") -> str:
            return "" if n == 1 else (word or "s")

        start, end = self.window
        n = len(self.contract_tickers)
        lines = [f"Desk checks on {self.as_of.isoformat()}:"]
        if self.book is None:
            lines.append("  1-2. Futures close and liquidity: skipped (no book: give --db).")
        else:
            lines.append(f"  1-2. Futures close and liquidity: {len(self.futures)} open contract{s(len(self.futures))}, "
                         f"{n} ticker{s(n)}: {self._n(self.contract_tickers)} HistoricalDataRequest (fields "
                         f"{', '.join(DESK_HISTORY_FIELDS)}, {start.isoformat()} to {end.isoformat()}, the last "
                         f"{DESK_DAYS} days kept) and {self._n(self.contract_tickers)} ReferenceDataRequest "
                         f"({', '.join(DESK_TICK_FIELDS)}).")
        if self.book is None:
            lines.append("  3. LME per-prompt liquidity: skipped (no book: give --db).")
        else:
            lines.append(f"  3. LME per-prompt liquidity: {len(self.lme)} metal{s(len(self.lme))}, "
                         f"{len(self.lme_tickers)} ticker{s(len(self.lme_tickers))} (3M and monthly prompts), fields "
                         f"{', '.join(LME_LIQUIDITY_FIELDS)}, {self._n(self.lme_tickers)} ReferenceDataRequest"
                         + (f"; {self.lme_note}" if self.lme_note else "") + ".")
        if self.sgx is None:
            lines.append(f"  4. SGX USD/CNH history: skipped ({SGX_XUC_ROOT} not in config/contracts.csv or the filter).")
        else:
            s0, s1 = self.sgx_window
            k = len(self.sgx_ticker_list)
            lines.append(f"  4. SGX USD/CNH history: {k} ticker form{s(k)} ({', '.join(self.sgx_ticker_list)}), field "
                         f"PX_LAST {s0.isoformat()} to {s1.isoformat()}, {self._n(self.sgx_ticker_list)} "
                         "HistoricalDataRequest.")
        if self.delivery_roots or self.delivery_placeholders:
            k = len(self.delivery_roots)
            lines.append(f"  5. Delivery type: {k} root{s(k)} on their front generic "
                         f"({len(self.delivery_placeholders)} placeholder{s(len(self.delivery_placeholders))} not "
                         f"asked), {len(DELIVERY_FIELDS)} candidate fields ({', '.join(DELIVERY_FIELDS)}), "
                         f"{self._n(self.delivery_tickers)} ReferenceDataRequest.")
        else:
            lines.append(f"  5. Delivery type: skipped ({self.delivery_why}).")
        k = len(self.calendars)
        lines.append(f"  6. Exchange holidays: {k} calendar{s(k)}, one front generic each, bulk field {CALENDAR_FIELD} "
                     f"{CALENDAR_START.isoformat()} to {CALENDAR_END.isoformat()}, {self.calendar_requests} "
                     "ReferenceDataRequest"
                     + (f"; not asked: {', '.join(f'{c} ({w})' for c, w in self.calendars_unasked)}"
                        if self.calendars_unasked else "") + ".")
        if self.book is None:
            lines.append("  7-8. Price history on file and contract dates stored: skipped (no book: give --db).")
        else:
            k = len(self.history_roots)
            lines.append(f"  7. Price history on file: local, no Bloomberg: {k} root{s(k)} in {PRICE_HISTORY_TABLE} "
                         f"of {self.history_db}.")
            lines.append(f"  8. Contract dates stored: local, no Bloomberg: {len(self.futures)} future"
                         f"{s(len(self.futures))} and {len(self.options)} option{s(len(self.options))} on futures.")
        lines.append(f"  Desk total: {self.securities} securit{'y' if self.securities == 1 else 'ies'} in "
                     f"{self.requests} request{s(self.requests)} (up to {self.batch_size} securities each).")
        return lines


@dataclass
class DeskResult:
    plan: DeskPlan
    checks: List[DeskCheck]
    requests_sent: int = 0

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in DESK_STATUSES}
        for c in self.checks:
            out[c.status] += 1
        return out

    def worksheet_rows(self) -> List[Dict[str, str]]:
        return [row for c in self.checks for row in c.worksheet]

    def housekeeper_lines(self) -> List[Tuple[str, str]]:
        return [item for c in self.checks for item in c.housekeeper]


# ---- the plan

def _book_connect(path: Path) -> sqlite3.Connection:
    """A read-only connection to the book database (``mode=ro``: any write through it raises)."""
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)


def _lme_monthly_tickers(root_id: str, as_of: date,
                         prompt_months: Sequence[Tuple[int, int]]) -> Tuple[List[Tuple[str, str]], str]:
    """Our monthly prompt tickers of one metal (``engine.lme.monthly_ticker``): the months of the
    book's open prompts and the next LME_MONTHS_AHEAD monthly prompts on or after ``as_of``.
    ([(label, ticker)], note)."""
    try:
        from engine.lme import monthly_prompt, monthly_ticker
        months: List[Tuple[int, int]] = []
        y, m = as_of.year, as_of.month
        while len(months) < LME_MONTHS_AHEAD:
            if monthly_prompt(y, m) >= as_of:
                months.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        wanted = sorted(dict.fromkeys(list(months) + list(prompt_months)))
        return [(f"monthly {y}-{m:02d}", monthly_ticker(root_id, y, m)) for y, m in wanted], ""
    except Exception as exc:  # noqa: BLE001 -- reported, never a crash
        return [], f"{root_id}: our monthly tickers could not be built ({type(exc).__name__}: {exc})"


def _calendar_securities(roots: Mapping[str, ContractRoot], held: Set[str],
                         wanted: Optional[Set[str]]) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str]]]:
    """One front generic per exchange calendar on file: a root the book holds first, else a
    verified one, else the first with a real Bloomberg root."""
    try:
        from engine.calendars import calendar_ids
        cals = list(calendar_ids())
    except Exception as exc:  # noqa: BLE001 -- reported, never a crash
        return [], [("all", f"the calendars could not be read ({type(exc).__name__}: {exc})")]
    asked: List[Tuple[str, str, str]] = []
    unasked: List[Tuple[str, str]] = []
    for cal in cals:
        if wanted is not None and cal not in wanted:
            continue
        cands = [r for r in roots.values() if str(r.calendar or "").upper() == cal and not r.bbg_placeholder]
        if not cands:
            unasked.append((cal, "no root of this calendar has a Bloomberg root yet"))
            continue
        cands.sort(key=lambda r: (r.root_id not in held, not r.bbg_verified, r.root_id))
        asked.append((cal, generic_ticker(cands[0]), cands[0].root_id))
    return asked, unasked


def plan_desk(roots: Optional[Iterable[ContractRoot]] = None, *, book: Optional[Book] = None, as_of: date,
              root_ids: Sequence[str] = (), sector: Optional[str] = None,
              batch_size: int = BATCH_SIZE) -> DeskPlan:
    """What the desk checks would ask. A root or sector filter narrows the book's contracts, the
    delivery roots and the calendars; without a book, checks 1-3, 7 and 8 are skipped and the
    delivery check takes the filtered roots (skipped without a filter). Asks nothing. Raises
    ValueError for an unknown root id."""
    by_id = dict(roots.items()) if isinstance(roots, Mapping) else (
        {r.root_id: r for r in roots} if roots is not None else load_roots())
    narrowed = bool(root_ids or sector)
    selected = _select(list(by_id.values()), root_ids, sector) if narrowed else []
    in_scope = {r.root_id for r in selected} if narrowed else None
    out = DeskPlan(as_of=as_of, book=book, roots=by_id, batch_size=batch_size)
    held: Set[str] = set()
    if book is not None:
        out.futures = [f for f in book.futures if in_scope is None or f.root_id in in_scope]
        out.options = [o for o in book.options if in_scope is None or o.root_id in in_scope]
        lme = [t for t in book.lme if in_scope is None or t.root_id in in_scope]
        held = {f.root_id for f in out.futures} | {o.root_id for o in out.options} | {t.root_id for t in lme}
        out.history_roots = sorted(held)
        for rid in dict.fromkeys(t.root_id for t in lme):
            metal = by_id.get(rid)
            if metal is None:
                continue
            months: List[Tuple[int, int]] = []
            for t in lme:
                if t.root_id == rid:
                    try:
                        d = to_date(t.prompt)
                    except ValueError:
                        continue
                    months.append((d.year, d.month))
            pairs: List[Tuple[str, str]] = []
            try:
                from engine.lme import three_month_ticker
                pairs.append(("3M", three_month_ticker(rid)))
            except Exception as exc:  # noqa: BLE001 -- reported, never a crash
                out.lme_note = f"{rid}: the 3M ticker could not be built ({exc})"
            monthly, note = _lme_monthly_tickers(rid, as_of, months)
            pairs += monthly
            if note:
                out.lme_note = note
            out.lme.append((metal, pairs))
        if not lme:
            out.lme_note = "no open LME ticket in the book"
    sgx = by_id.get(SGX_XUC_ROOT)
    if sgx is not None and (in_scope is None or SGX_XUC_ROOT in in_scope):
        out.sgx = sgx
        forms = [("config/contracts.csv's form", generic_ticker(sgx))]
        for code in dict.fromkeys((sgx.bbg_root, sgx.exchange_code)):
            for key in ("Curncy", "Comdty"):
                forms.append((f"root {code}, yellow key {key}", f"{padded_root(code)}1 {key}"))
        if book is not None:
            held_xuc = [f for f in book.futures if f.root_id == SGX_XUC_ROOT and f.ticker]
            if held_xuc:
                forms.append((f"held contract {held_xuc[0].instrument_id}", held_xuc[0].ticker))
        seen: Set[str] = set()
        for label, t in forms:
            if t not in seen:
                seen.add(t)
                out.sgx_tickers.append((label, t))
    if narrowed:
        delivery = selected
    elif book is not None:
        delivery = [by_id[r] for r in sorted(held) if r in by_id]
        if not delivery:
            out.delivery_why = "the book holds no commodity contract"
    else:
        delivery = []
        out.delivery_why = "no book and no --root / --sector: give --db, or name the roots"
    out.delivery_roots = [r for r in delivery if not r.bbg_placeholder]
    out.delivery_placeholders = [r for r in delivery if r.bbg_placeholder]
    wanted = {str(r.calendar or "").upper() for r in selected} if narrowed else None
    out.calendars, out.calendars_unasked = _calendar_securities(by_id, held, wanted)
    return out


# ---- asking

def _desk_reference(client, tickers: Sequence[str], fields: Sequence[str], dr: DeskResult) -> Dict[str, Answer]:
    out: Dict[str, Answer] = {}
    for batch in _chunks(list(tickers), max(1, dr.plan.batch_size)):
        dr.requests_sent += 1
        got = client.reference(batch, fields) or {}
        by_upper = {str(k).upper(): v for k, v in got.items()}
        for t in batch:
            out[t] = got.get(t) or by_upper.get(t.upper()) or Answer(
                t, {}, security_error="Bloomberg sent nothing back for this security", answered=False)
    return out


def _desk_history(client, tickers: Sequence[str], fields: Sequence[str], start: date, end: date,
                  dr: DeskResult) -> Dict[str, History]:
    out: Dict[str, History] = {}
    for batch in _chunks(list(tickers), max(1, dr.plan.batch_size)):
        dr.requests_sent += 1
        got = client.history(batch, fields, start, end) or {}
        by_upper = {str(k).upper(): v for k, v in got.items()}
        for t in batch:
            out[t] = got.get(t) or by_upper.get(t.upper()) or History(
                t, {}, security_error="Bloomberg sent nothing back for this security", answered=False)
    return out


def _can(client, method: str, keyword: str = "") -> bool:
    fn = getattr(client, method, None)
    if fn is None:
        return False
    if not keyword:
        return True
    try:
        import inspect
        return keyword in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _refused(sec: str, err: str) -> str:
    return f'Bloomberg refused {sec}: "{err}"'


def _field_words(errors: Mapping[str, str], names: Sequence[str]) -> str:
    said = [f'{n}: "{errors[n]}"' for n in names if errors.get(n)]
    return ("; Bloomberg said " + "; ".join(said)) if said else ""


def _agree(a: Optional[float], b: Optional[float]) -> Optional[bool]:
    """Whether two figures of the same day agree (within 0.5 %); None when either is missing."""
    if a is None or b is None:
        return None
    return abs(a - b) <= 0.005 * max(1.0, abs(a), abs(b))


def _exchange(root: Optional[ContractRoot]) -> str:
    return root.exchange if root is not None else "?"


def _is_china(root: Optional[ContractRoot]) -> bool:
    return root is not None and root.exchange.upper() in CHINA_EXCHANGE_CODES


# ---- the checks

def _check_settlement(dp: DeskPlan, hist: Mapping[str, History], ticks: Mapping[str, Answer]) -> DeskCheck:
    """1: PX_LAST against PX_SETTLE over the last DESK_DAYS days, per contract and per exchange."""
    c = DeskCheck(1)
    per_ex: Dict[str, Dict[str, float]] = {}
    errors = 0
    agreeing: List[str] = []
    for fut in dp.futures:
        root = dp.roots.get(fut.root_id)
        ex = _exchange(root)
        if not fut.ticker:
            c.lines.append(f"{ex:<6} {fut.instrument_id}: not asked ({fut.why_no_ticker})")
            continue
        h = hist.get(fut.ticker)
        if h is None or h.security_error:
            errors += 1
            c.lines.append(f"{ex:<6} {fut.instrument_id}: " + (_refused(fut.ticker, h.security_error) if h
                                                               else "not asked"))
            continue
        tick_answer = ticks.get(fut.ticker)
        tick = _num(tick_answer.fields.get("FUT_TICK_SIZE")) if tick_answer is not None else None
        days = sorted(h.days)[-DESK_DAYS:]
        same = differ = no_both = 0
        worst = 0.0
        for d in days:
            last, settle = _num(h.days[d].get("PX_LAST")), _num(h.days[d].get("PX_SETTLE"))
            if last is None or settle is None:
                no_both += 1
                continue
            gap = abs(last - settle)
            if gap <= 1e-9 * max(1.0, abs(settle)):
                same += 1
            else:
                differ += 1
                worst = max(worst, gap)
        stats = per_ex.setdefault(ex, {"days": 0, "differ": 0, "worst_ticks": 0.0})
        stats["days"] += same + differ
        stats["differ"] += differ
        in_ticks = worst / tick if tick else None
        if in_ticks is not None:
            stats["worst_ticks"] = max(stats["worst_ticks"], in_ticks)
        if not days:
            c.lines.append(f"{ex:<6} {fut.instrument_id} [{fut.ticker}]: no history in the window"
                           + _field_words(h.field_errors, DESK_HISTORY_FIELDS))
            continue
        if same and not differ and not no_both and not h.field_errors:
            agreeing.append(fut.instrument_id)
            continue
        size = f"{in_ticks:.1f} ticks" if in_ticks is not None else f"{_g(worst)} in price, tick size unknown"
        n = same + differ
        c.lines.append(f"{ex:<6} {fut.instrument_id} [{fut.ticker}]: {n} day{'s' if n != 1 else ''} compared, "
                       f"PX_LAST = PX_SETTLE on {same}, differs on {differ}" + (f" (up to {size})" if differ else "")
                       + (f"; {no_both} day{'s' if no_both != 1 else ''} without both" if no_both else "")
                       + _field_words(h.field_errors, ("PX_SETTLE", "PX_LAST"))
                       + (" [China: settlement is the day's volume-weighted average]" if _is_china(root) else ""))
    if agreeing:
        c.lines.insert(0, f"PX_LAST = PX_SETTLE on every day compared: {', '.join(agreeing)}")
    summary = [f"{ex} {int(s['differ'])} of {int(s['days'])} days differ"
               + (f", up to {s['worst_ticks']:.1f} ticks" if s["worst_ticks"] else "")
               for ex, s in sorted(per_ex.items()) if s["differ"]]
    same_ex = sorted(ex for ex, s in per_ex.items() if s["days"] and not s["differ"])
    compared = sum(int(s["days"]) for s in per_ex.values())
    differing = sum(int(s["differ"]) for s in per_ex.values())
    if differing:
        c.status = WARN
        c.summary = ("the close the app marks at (PX_LAST, a user decision) is not the exchange settlement on "
                     + "; ".join(summary) + (f"; they agree on {', '.join(same_ex)}" if same_ex else "")
                     + ". This only reports; nothing changes unless you decide it should")
    elif compared:
        c.status = PASS
        c.summary = f"PX_LAST equals PX_SETTLE on all {compared} contract-days ({', '.join(same_ex)})"
    elif errors:
        c.status = FAIL
        c.summary = f"Bloomberg refused {errors} of the book's contracts; nothing to compare"
    else:
        c.summary = "no day had both PX_LAST and PX_SETTLE"
    return c


def _check_liquidity(dp: DeskPlan, hist: Mapping[str, History], stored: Mapping[str, dict],
                     stored_note: str) -> DeskCheck:
    """2: Bloomberg's OPEN_INT and PX_VOLUME now beside the figures the last pull stored in the
    book's ``price_history`` (``contract_liquidity``: the Risk tab's Days to exit reads them), on
    the same day where the window has it. Agreement says the pull stores what Bloomberg gives;
    a contract with nothing on file has no Days to exit until a pull fetches its history."""
    c = DeskCheck(2)
    same = differ = missing = 0
    bbg_any = china = False
    for fut in dp.futures:
        root = dp.roots.get(fut.root_id)
        ex = _exchange(root)
        china = china or _is_china(root)
        tag = " [China: one- or two-sided counting, confirm by hand]" if _is_china(root) else ""
        if not fut.ticker:
            continue
        h = hist.get(fut.ticker)
        if h is None or h.security_error:
            c.lines.append(f"{ex:<6} {fut.instrument_id}: "
                           + (_refused(fut.ticker, h.security_error) if h else "not asked") + tag)
            continue
        rec = stored.get(fut.instrument_id) or {}
        days = sorted(h.days)

        def on(fname: str, day: Optional[str], h: History = h, days: List[str] = days) -> Tuple[Optional[float], str]:
            if day and day in h.days and _num(h.days[day].get(fname)) is not None:
                return _num(h.days[day].get(fname)), day
            for d in reversed(days):
                v = _num(h.days[d].get(fname))
                if v is not None:
                    return v, d
            return None, ""

        s_oi_day, s_vol_day = rec.get("oi_date") or "", rec.get("volume_date") or ""
        oi, oi_day = on("OPEN_INT", s_oi_day)
        vol, vol_day = on("PX_VOLUME", s_vol_day)
        bbg_any = bbg_any or oi is not None or vol is not None
        s_oi, s_vol = _num(rec.get("open_interest")), _num(rec.get("volume_last"))
        bbg = (f"Bloomberg OI {_g(oi)} ({oi_day or '-'}), volume {_g(vol)} ({vol_day or '-'})"
               + _field_words(h.field_errors, ("OPEN_INT", "PX_VOLUME")))
        if s_oi is None and s_vol is None:
            missing += 1
            why = rec.get("reason") or stored_note or "nothing on file"
            c.lines.append(f"{ex:<6} {fut.instrument_id}: {bbg}; on file: {why}{tag}")
            continue
        verdicts = [v for v in (_agree(oi, s_oi) if oi_day == s_oi_day else None,
                                _agree(vol, s_vol) if vol_day == s_vol_day else None) if v is not None]
        if verdicts and all(verdicts):
            same += 1
            word = "agree"
        elif verdicts:
            differ += 1
            word = "DIFFER on the same day"
        else:
            word = "no common day to compare"
        c.lines.append(f"{ex:<6} {fut.instrument_id}: {bbg}; on file OI {_g(s_oi)} ({s_oi_day or '-'}), volume "
                       f"{_g(s_vol)} ({s_vol_day or '-'}); {word}{tag}")
    if not bbg_any:
        c.status = FAIL
        c.summary = "Bloomberg returned no OPEN_INT or PX_VOLUME for any of the book's contracts"
    elif differ or missing:
        c.status = WARN
        bits = []
        if differ:
            bits.append(f"{differ} contract{'s differ' if differ != 1 else ' differs'} from what the last pull "
                        "stored on the same day (the pull's figure is stale or another contract's)")
        if missing:
            bits.append(f"{missing} contract{'s have' if missing != 1 else ' has'} no open interest or volume on "
                        "file, so no Days to exit: press Pull Bloomberg now")
        c.summary = "; ".join(bits)
    elif same:
        c.status = PASS
        c.summary = f"Bloomberg's figures agree with those on file on all {same} contracts compared"
    else:
        c.status = WARN
        c.summary = "Bloomberg answered, but no stored figure falls on a day it gave"
    if china:
        c.summary += ". Chinese contracts: one- or two-sided counting is settled by hand (Manual checks)"
    if stored_note:
        c.lines.append(f"note: {stored_note}")
    return c


def _check_lme_liquidity(dp: DeskPlan, answers: Mapping[str, Answer]) -> DeskCheck:
    """3: which LME tickers (3M, the monthly prompts) Bloomberg gives OPEN_INT / PX_VOLUME for."""
    c = DeskCheck(3)
    full = partial = refused = 0
    three_m: List[str] = []
    monthly: List[str] = []
    for metal, pairs in dp.lme:
        for label, ticker in pairs:
            a = answers.get(ticker)
            if a is None:
                continue
            bucket = three_m if label == "3M" else monthly
            if a.security_error:
                refused += 1
                bucket.append(f"{ticker} refused")
                c.lines.append(f"{metal.root_id:<8} {label:<26} {_refused(ticker, a.security_error)}")
                continue
            oi, vol = _num(a.fields.get("OPEN_INT")), _num(a.fields.get("PX_VOLUME"))
            got = [n for n, v in (("OPEN_INT", oi), ("PX_VOLUME", vol)) if v is not None]
            if len(got) == 2:
                full += 1
            else:
                partial += 1
            bucket.append(f"{ticker} {' and '.join(got) or 'neither'}")
            c.lines.append(f"{metal.root_id:<8} {label:<26} {ticker:<16} OPEN_INT {_g(oi)}, PX_VOLUME {_g(vol)}"
                           + _field_words(a.field_errors, LME_LIQUIDITY_FIELDS))
    total = full + partial + refused
    if dp.lme_note:
        c.lines.append(f"note: {dp.lme_note}")
    if not total:
        c.summary = dp.lme_note or "no LME ticker to ask"
        return c
    c.status = PASS if full == total else (FAIL if refused == total else WARN)
    c.summary = (f"{full} of {total} LME tickers give both OPEN_INT and PX_VOLUME, {partial} one or neither, "
                 f"{refused} refused")
    c.housekeeper.append((HISTORY_OWNER, "LME per-prompt liquidity from Bloomberg: 3M: " + ("; ".join(three_m) or "-")
                          + "; monthly prompts: " + ("; ".join(monthly) or "-")))
    return c


def _check_sgx(dp: DeskPlan, hist: Mapping[str, History]) -> DeskCheck:
    """4: a year of PX_LAST history for the SGX USD/CNH future, and which ticker form gives it."""
    c = DeskCheck(4)
    root = dp.sgx
    if root is None:
        c.summary = f"{SGX_XUC_ROOT} is not in config/contracts.csv"
        return c
    works: List[Tuple[str, str, int]] = []
    for label, ticker in dp.sgx_tickers:
        h = hist.get(ticker)
        if h is None:
            continue
        if h.security_error:
            c.lines.append(f"{ticker:<16} ({label}): {_refused(ticker, h.security_error)}")
            continue
        closes = sorted(d for d, v in h.days.items() if _num(v.get("PX_LAST")) is not None)
        if closes:
            works.append((label, ticker, len(closes)))
            c.lines.append(f"{ticker:<16} ({label}): {len(closes)} daily closes, {closes[0]} to {closes[-1]}")
        else:
            c.lines.append(f"{ticker:<16} ({label}): resolves, no PX_LAST history"
                           + _field_words(h.field_errors, SGX_FIELDS))
    ours = generic_ticker(root)
    ours_ok = [w for w in works if w[1] == ours and w[2] >= SGX_MIN_CLOSES]
    generics = [w for w in works if not w[0].startswith("held") and w[2] >= SGX_MIN_CLOSES]
    if ours_ok:
        c.status = PASS
        c.summary = (f"{ours} has a year of history ({ours_ok[0][2]} closes): the Risk tab can use it for the CNY "
                     "hedges")
    elif works:
        best = max(generics or works, key=lambda w: w[2])
        c.status = WARN
        c.summary = (f"{ours} (config/contracts.csv's form) gives no full year; {best[1]} ({best[0]}) gives "
                     f"{best[2]} closes")
        if best in generics:
            head, key = best[1].rsplit(" ", 1)
            code = head[:-1].strip()
            reason = f"desk check 4: {best[1]} has {best[2]} daily closes in a year, {ours} does not"
            if code and code != root.bbg_root:
                c.worksheet.append({"root_id": root.root_id, "field": "bbg_root", "current": root.bbg_root,
                                    "suggested": code, "verdict": "TICKER_FORM", "reason": reason, "apply": ""})
            if key != root.bbg_yellow_key:
                c.worksheet.append({"root_id": root.root_id, "field": "bbg_yellow_key", "current": root.bbg_yellow_key,
                                    "suggested": key, "verdict": "TICKER_FORM", "reason": reason, "apply": ""})
        c.housekeeper.append((HISTORY_OWNER, f"SGX USD/CNH history: {best[1]} works ({best[2]} closes in a year); "
                                             f"{ours} does not"))
    else:
        c.status = FAIL
        c.summary = "no ticker form tried gives a PX_LAST history: find the SGX USD/CNH future with SECF <GO>"
    return c


def _delivery_from(field_name: str, value) -> Optional[str]:
    """'physical' | 'cash' | None from one delivery field's value."""
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return None
    if "CASH" in field_name.upper() and text in ("y", "yes", "true", "1", "n", "no", "false", "0"):
        return "cash" if text in ("y", "yes", "true", "1") else "physical"
    if "cash" in text or "financ" in text:
        return "cash"
    if "phys" in text or "deliver" in text:
        return "physical"
    return None


def _check_delivery(dp: DeskPlan, answers: Mapping[str, Answer]) -> DeskCheck:
    """5: Bloomberg's physical / cash settlement against config/contracts.csv's delivery column."""
    c = DeskCheck(5)
    agree = disagree = unread = 0
    used: Set[str] = set()
    rejected: Dict[str, str] = {}
    for root in dp.delivery_roots:
        ticker = generic_ticker(root)
        a = answers.get(ticker)
        if a is None:
            continue
        if a.security_error:
            unread += 1
            c.lines.append(f"{root.root_id:<12} {_refused(ticker, a.security_error)}")
            continue
        for fname, words in a.field_errors.items():
            rejected.setdefault(fname, words)
        hit = next(((f, a.fields.get(f)) for f in DELIVERY_FIELDS if a.fields.get(f) not in (None, "")), None)
        if hit is None:
            unread += 1
            continue
        used.add(hit[0])
        bbg = _delivery_from(*hit)
        ours = root.delivery or "(blank)"
        said = f"{root.root_id:<12} [{ticker}]: {hit[0]} = {hit[1]!r}"
        if bbg is None:
            unread += 1
            c.lines.append(f"{said}, not read as physical or cash; ours {ours}")
        elif bbg == root.delivery:
            agree += 1
            c.lines.append(f"{said} ({bbg}), ours {ours}: agree")
        else:
            disagree += 1
            c.lines.append(f"{said} ({bbg}), ours {ours}: DISAGREE")
            c.worksheet.append({"root_id": root.root_id, "field": "delivery", "current": root.delivery,
                                "suggested": bbg, "verdict": "DELIVERY_MISMATCH",
                                "reason": f"desk check 5: Bloomberg {ticker} {hit[0]} = {hit[1]!r}. It decides "
                                          "whether the app warns at first notice (physical) or last trade (cash)",
                                "apply": ""})
    for root in dp.delivery_placeholders:
        c.lines.append(f"{root.root_id:<12} not asked (bbg_root {root.bbg_root!r} is a placeholder)")
    if not used:
        said = "; ".join(f'{f}: "{w}"' for f, w in rejected.items() if f in DELIVERY_FIELDS)
        c.status = SKIPPED
        c.summary = ("Bloomberg answered none of the candidate fields " + ", ".join(DELIVERY_FIELDS)
                     + (f"; Bloomberg said {said}" if said else "")
                     + ". Find the field with FLDS <GO> on a front future (search 'delivery' or 'settle')")
        return c
    c.status = WARN if disagree else PASS
    c.summary = (f"field {', '.join(sorted(used))}: {agree} agree, {disagree} disagree (worksheet rows), "
                 f"{unread} not read")
    return c


def _calendar_file_notes(cal: str) -> Dict[date, str]:
    """{date: 'unverified' | 'estimated' | ''} of one calendar file's dates."""
    try:
        from engine.calendars import CALENDAR_DIR
    except Exception:  # noqa: BLE001 -- no notes; the dates themselves come from engine.calendars
        return {}
    path = next((p for p in Path(CALENDAR_DIR).glob("*.txt") if p.stem.upper() == cal), None)
    out: Dict[date, str] = {}
    if path is None:
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _DATE_LINE_RE.match(raw)
        if not m:
            continue
        note = (m.group(2) or "").lower()
        try:
            out[date.fromisoformat(m.group(1))] = ("estimated" if "estimated" in note
                                                   else "unverified" if "unverified" in note else "")
        except ValueError:
            continue
    return out


def _dates_in(rows) -> Set[date]:
    """The weekday dates inside CALENDAR_START..CALENDAR_END of a bulk calendar field's rows."""
    out: Set[date] = set()
    for row in rows if isinstance(rows, (list, tuple)) else []:
        for v in (row.values() if isinstance(row, Mapping) else [row]):
            try:
                d = to_date(v)
            except (ValueError, TypeError):
                continue
            if CALENDAR_START <= d <= CALENDAR_END and d.weekday() < 5:
                out.add(d)
    return out


def _check_calendars(dp: DeskPlan, answers: Mapping[str, Answer]) -> DeskCheck:
    """6: Bloomberg's non-settlement dates against config/calendars/, per calendar."""
    c = DeskCheck(6)
    from engine.calendars import holidays
    same = differ = failed = 0
    for cal, sec, rid in dp.calendars:
        a = answers.get(sec)
        if a is None:
            continue
        if a.security_error or not a.fields.get(CALENDAR_FIELD):
            failed += 1
            words = a.security_error or a.field_errors.get(CALENDAR_FIELD) or "no dates returned"
            c.lines.append(f"{cal:<9} [{sec}, {rid}]: Bloomberg said \"{words}\"")
            continue
        bbg = _dates_in(a.fields.get(CALENDAR_FIELD))
        try:
            ours = {d for d in holidays(cal) if CALENDAR_START <= d <= CALENDAR_END}
        except ValueError as exc:
            failed += 1
            c.lines.append(f"{cal:<9} our file could not be read ({exc})")
            continue
        notes = _calendar_file_notes(cal)
        missing, extra = sorted(bbg - ours), sorted(ours - bbg)
        confirmed = sorted(d for d in ours & bbg if notes.get(d))
        if not missing and not extra:
            same += 1
            c.lines.append(f"{cal:<9} [{sec}]: {len(bbg)} dates, the same as the file"
                           + (f"; {len(confirmed)} marked unverified / estimated there are now confirmed"
                              if confirmed else ""))
            continue
        differ += 1
        bits = []
        if missing:
            bits.append("missing from the file: " + ", ".join(d.isoformat() for d in missing))
        if extra:
            bits.append("in the file, not on Bloomberg: " + ", ".join(
                d.isoformat() + (f" ({notes[d]})" if notes.get(d) else "") for d in extra))
        if confirmed:
            bits.append(f"{len(confirmed)} unverified / estimated dates confirmed")
        c.lines.append(f"{cal:<9} [{sec}]: Bloomberg {len(bbg)} dates, file {len(ours)}; " + "; ".join(bits))
        c.housekeeper.append((CALENDAR_OWNER, f"config/calendars/{cal}.txt against Bloomberg's {CALENDAR_FIELD} on "
                                              f"{sec} ({CALENDAR_START.year}-{CALENDAR_END.year}): " + "; ".join(bits)))
    for cal, why in dp.calendars_unasked:
        c.lines.append(f"{cal:<9} not asked ({why})")
    total = same + differ + failed
    if not total:
        c.summary = "no calendar asked"
        return c
    c.status = PASS if same == total else (FAIL if failed == total else WARN)
    c.summary = (f"{same} calendar{'s' if same != 1 else ''} agree with Bloomberg, {differ} differ (lines for the "
                 f"housekeeper), {failed} not answered")
    return c


def _history_ids_by_root(conn: sqlite3.Connection, roots: Mapping[str, ContractRoot],
                         wanted: Sequence[str]) -> Dict[str, List[str]]:
    """The ``price_history`` ids of each wanted root: a future's canonical ids ('CLZ26 Comdty')
    by its Bloomberg root and yellow key, an LME metal's 'LME:CA CASH' / 'LME:CA 3M' pillars."""
    keys: Dict[Tuple[str, str], List[str]] = {}
    for rid in wanted:
        r = roots.get(rid)
        if r is not None and str(r.bbg_root or "").strip():
            keys.setdefault((r.bbg_root.strip().upper(), r.bbg_yellow_key.upper()), []).append(rid)
    out: Dict[str, List[str]] = {rid: [] for rid in wanted}
    for (iid,) in conn.execute(f"SELECT DISTINCT instrument_id FROM {PRICE_HISTORY_TABLE}"):
        text = str(iid)
        head, _, pillar = text.rpartition(" ")
        if head in out and pillar in ("CASH", "3M"):
            out[head].append(text)
            continue
        parts = parse_bbg_ticker(text)
        if parts is not None:
            for rid in keys.get((parts[0], parts[3].upper()), []):
                out[rid].append(text)
    return out


def _check_history_depth(dp: DeskPlan) -> DeskCheck:
    """7: the price history on file in the book database's ``price_history`` (Bloomberg's daily
    closes, fetched by a pull) per root the book holds: days, first and last close, whether the
    last is fresh, and which replay dates it reaches. Local data only, read-only."""
    c = DeskCheck(7)
    if dp.book is None or dp.history_db is None:
        c.summary = "no book: give --db"
        return c
    if not dp.history_roots:
        c.summary = "the book holds no commodity contract"
        return c
    conn = _book_connect(dp.history_db)
    try:
        if not _has_object(conn, PRICE_HISTORY_TABLE):
            c.status = WARN
            c.summary = (f"no {PRICE_HISTORY_TABLE} table in {dp.history_db}: no risk figure or replay until a "
                         "pull (Pull Bloomberg now) fetches the history")
            return c
        ids = _history_ids_by_root(conn, dp.roots, dp.history_roots)
        fresh = stale = absent = 0
        reach = {name: 0 for name, _d in REPLAYS}
        for rid in dp.history_roots:
            mine = ids.get(rid) or []
            row = None
            if mine:
                marks = ",".join("?" * len(mine))
                row = conn.execute(f"SELECT MIN(as_of_date), MAX(as_of_date), COUNT(DISTINCT as_of_date) FROM "
                                   f"{PRICE_HISTORY_TABLE} WHERE instrument_id IN ({marks})", mine).fetchone()
            if not row or not row[0]:
                absent += 1
                c.lines.append(f"{rid:<12} no history on file: no risk figure or replay until a pull fetches it")
                continue
            first, last, n = str(row[0])[:10], str(row[1])[:10], int(row[2] or 0)
            age = (dp.as_of - to_date(last)).days
            if age <= HISTORY_FRESH_DAYS:
                fresh += 1
            else:
                stale += 1
            start = to_date(first)
            for name, d in REPLAYS:
                if start <= d:
                    reach[name] += 1
            c.lines.append(f"{rid:<12} {n} days on file, {len(mine)} contract{'s' if len(mine) != 1 else ''}, "
                           f"{first} to {last}" + (f" ({age} days old)" if age > HISTORY_FRESH_DAYS else "")
                           + "; " + "; ".join(f"{'reaches' if start <= d else 'does not reach'} {d.isoformat()} "
                                              f"({name})" for name, d in REPLAYS))
    finally:
        conn.close()
    total = len(dp.history_roots)
    c.status = PASS if not stale and not absent else WARN
    c.summary = (f"{fresh} of {total} roots have fresh history on file (last close within {HISTORY_FRESH_DAYS} "
                 f"days), {stale} stale, {absent} none" + ("" if not (stale or absent) else ": press Pull Bloomberg now")
                 + "; " + ", ".join(f"{k} of {total} reach {name}" for name, k in reach.items()))
    return c


def _check_dates_stored(dp: DeskPlan) -> DeskCheck:
    """8: whether Bloomberg's contract dates are in contract_static for the book's contracts."""
    c = DeskCheck(8)
    if dp.book is None:
        c.summary = "no book: give --db"
        return c
    items = list(dp.futures) + list(dp.options)
    if not items:
        c.summary = "the book holds no open future or option on a future"
        return c
    stored = 0
    for f in items:
        s = f.stored
        kind = "option" if f.kind == "CMDTY_OPTION" else "future"
        if s:
            stored += 1
            c.lines.append(f"{f.instrument_id:<22} {kind}: stored, last trade {s.get('last_trade_date') or '-'}, "
                           f"first notice {s.get('first_notice_date') or '-'} ({s.get('source')}, "
                           f"{s.get('fetched_at')})")
        else:
            c.lines.append(f"{f.instrument_id:<22} {kind}: not stored; the app runs on the estimate "
                           f"{f.expiry or '-'} until a pull stores Bloomberg's dates")
    missing = len(items) - stored
    c.status = PASS if not missing else WARN
    c.summary = (f"{stored} of {len(items)} have Bloomberg's dates in contract_static"
                 + (f"; {missing} not yet: pull once (Pull Bloomberg now) to store them" if missing else ""))
    return c


def run_desk(client, dp: DeskPlan, *, offline: str = "") -> DeskResult:
    """Run the eight desk checks through ``client`` (``reference``, ``history(securities, fields,
    start, end)``, ``bulk(securities, field, overrides=...)``). ``offline``: why Bloomberg is not
    asked; every Bloomberg check is then SKIPPED with it, and 7 and 8 still run from local data.
    A check that fails in itself is FAIL with its error; the others go on. Writes nothing."""
    dr = DeskResult(dp, [])

    def skipped(n: int, why: str) -> DeskCheck:
        return DeskCheck(n, SKIPPED, why)

    def guarded(n: int, fn: Callable[[], DeskCheck]) -> DeskCheck:
        try:
            return fn()
        except BloombergUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 -- one check failing must not stop the others
            return DeskCheck(n, FAIL, f"the check itself failed ({type(exc).__name__}: {exc})")

    no_bbg = offline or ("" if client is not None else "Bloomberg was not asked")
    no_history = no_bbg or ("" if _can(client, "history") else "this Bloomberg client cannot ask history")
    # 1 and 2: the book's futures, one history request and one tick-size request.
    if dp.book is None:
        dr.checks += [skipped(1, "no book: give --db"), skipped(2, "no book: give --db")]
    elif not dp.contract_tickers:
        why = "the book holds no open future with a Bloomberg ticker"
        dr.checks += [skipped(1, why), skipped(2, why)]
    elif no_history:
        dr.checks += [skipped(1, no_history), skipped(2, no_history)]
    else:
        start, end = dp.window
        hist = _desk_history(client, dp.contract_tickers, DESK_HISTORY_FIELDS, start, end, dr)
        ticks = _desk_reference(client, dp.contract_tickers, DESK_TICK_FIELDS, dr)
        dr.checks.append(guarded(1, lambda: _check_settlement(dp, hist, ticks)))
        stored: Dict[str, dict] = {}
        note = ""
        try:
            from engine.risk.commodity_history import contract_liquidity
            stored = contract_liquidity({f.instrument_id: f.root_id for f in dp.futures}, dp.as_of,
                                        window=DESK_DAYS, db_path=dp.history_db)
        except Exception as exc:  # noqa: BLE001 -- the stored side is a comparison; its failure is a note
            note = f"the book's stored history could not be read ({type(exc).__name__}: {exc})"
        dr.checks.append(guarded(2, lambda: _check_liquidity(dp, hist, stored, note)))
    # 3: LME per-prompt liquidity.
    if not dp.lme_tickers:
        dr.checks.append(skipped(3, "no book: give --db" if dp.book is None else (dp.lme_note or "nothing to ask")))
    elif no_bbg:
        dr.checks.append(skipped(3, no_bbg))
    else:
        lme_answers = _desk_reference(client, dp.lme_tickers, LME_LIQUIDITY_FIELDS, dr)
        dr.checks.append(guarded(3, lambda: _check_lme_liquidity(dp, lme_answers)))
    # 4: SGX USD/CNH history.
    if dp.sgx is None:
        dr.checks.append(skipped(4, f"{SGX_XUC_ROOT} is not in config/contracts.csv (or not in the --root / "
                                    "--sector filter)"))
    elif no_history:
        dr.checks.append(skipped(4, no_history))
    else:
        s0, s1 = dp.sgx_window
        sgx_hist = _desk_history(client, dp.sgx_ticker_list, SGX_FIELDS, s0, s1, dr)
        dr.checks.append(guarded(4, lambda: _check_sgx(dp, sgx_hist)))
    # 5: delivery type.
    if not dp.delivery_roots:
        dr.checks.append(skipped(5, dp.delivery_why or "no root with a real Bloomberg root to ask"))
    elif no_bbg:
        dr.checks.append(skipped(5, no_bbg))
    else:
        dlv = _desk_reference(client, dp.delivery_tickers, DELIVERY_FIELDS, dr)
        dr.checks.append(guarded(5, lambda: _check_delivery(dp, dlv)))
    # 6: exchange holidays.
    no_bulk = no_bbg or ("" if _can(client, "bulk", "overrides")
                         else "this Bloomberg client cannot ask a bulk field with overrides")
    if not dp.calendars:
        dr.checks.append(skipped(6, "; ".join(f"{c}: {w}" for c, w in dp.calendars_unasked) or "no calendar"))
    elif no_bulk:
        dr.checks.append(skipped(6, no_bulk))
    else:
        cal_answers: Dict[str, Answer] = {}
        groups: Dict[str, List[str]] = {}
        for cal, sec, _r in dp.calendars:
            groups.setdefault(CALENDAR_CODES.get(cal, ""), []).append(sec)
        for code, secs in groups.items():
            overrides = {"CALENDAR_START_DATE": CALENDAR_START.strftime("%Y%m%d"),
                         "CALENDAR_END_DATE": CALENDAR_END.strftime("%Y%m%d")}
            if code:
                overrides["SETTLEMENT_CALENDAR_CODE"] = code
            for batch in _chunks(list(dict.fromkeys(secs)), max(1, dp.batch_size)):
                dr.requests_sent += 1
                got = client.bulk(batch, CALENDAR_FIELD, overrides=overrides) or {}
                for s in batch:
                    cal_answers[s] = got.get(s) or Answer(
                        s, {}, security_error="Bloomberg sent nothing back for this security", answered=False)
        dr.checks.append(guarded(6, lambda: _check_calendars(dp, cal_answers)))
    # 7 and 8: local data only.
    dr.checks.append(guarded(7, lambda: _check_history_depth(dp)))
    dr.checks.append(guarded(8, lambda: _check_dates_stored(dp)))
    dr.checks.sort(key=lambda c: c.number)
    return dr


def manual_checks(dr: Optional[DeskResult]) -> List[str]:
    """The things no Bloomberg field can settle, one line each."""
    lines = [f"SHFE counting: {SHFE_MANUAL}."]
    if dr is not None and any(c.number == 5 and c.status == SKIPPED and "FLDS" in c.summary for c in dr.checks):
        lines.append("Delivery field: FLDS <GO> on a front future, search 'delivery', and tell the session which "
                     "field says physical or cash.")
    lines.append("Send the report's 'For the housekeeper' section back to the session.")
    return lines


def render_desk(dr: DeskResult) -> List[str]:
    counts = dr.counts()
    lines = [f"Desk checks on {dr.plan.as_of.isoformat()}: " + ", ".join(f"{counts[s]} {s}" for s in DESK_STATUSES)
             + f" ({dr.requests_sent} Bloomberg request{'s' if dr.requests_sent != 1 else ''})"]
    for c in dr.checks:
        lines += ["", f"  {c.number}. {c.title}: {c.status}. {c.summary}."]
        for ln in c.lines:
            lines.append(f"       {ln}")
    return lines


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
    # The pull's own tickers: a currency or price scale Bloomberg disagrees with (one row per root and
    # suggestion, the dedupe below; a refused ticker has no suggestion of its own: the root check's search).
    for it in result.pull:
        for f in it.findings:
            if f.field and it.root_id:
                rows.append({"root_id": it.root_id, "field": f.field, "current": f.current,
                             "suggested": f.suggested, "verdict": f.verdict,
                             "reason": f"the pull's ticker {it.ticker}: {f.evidence}. {f.action}", "apply": ""})
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


def render_text(result: CheckResult, *, now: datetime, paths: Mapping[str, Path],
                desk: Optional[DeskResult] = None, not_run: str = "") -> str:
    """The plain-English report; ``desk`` adds the desk checks and the manual checks, ``not_run``
    says why the root, LME, options and book checks did not run (Bloomberg unreachable)."""
    p = result.plan
    lines = [f"Bloomberg ticker check, {now:%Y-%m-%d %H:%M}" + (f" ({result.host})" if result.host else ""), ""]
    if not_run:
        reason = not_run.rstrip() if not_run.rstrip()[-1:] in ".?!" else not_run.rstrip() + "."
        lines.append(f"Bloomberg could not be reached: {reason} The root, LME, options and book checks did not "
                     "run; the desk checks below say what could be read without it.")
    elif p.parts or p.book is not None or p.pull is not None:
        lines += p.describe()
        lines.append(f"Sent {result.requests_sent} reference request{'s' if result.requests_sent != 1 else ''}"
                     + (f" and {result.searches_sent} searches" if p.search else "") + ".")
    if p.pull is not None and not not_run:
        lines += ["", *_render_pull(result)]
    if PART_ROOTS in p.parts and not not_run:
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
    if p.book is not None and not not_run:
        lines += ["", *_render_book(result)]
    if desk is not None:
        lines += ["", *render_desk(desk)]
    code = result.code_findings()
    desk_code = desk.housekeeper_lines() if desk is not None else []
    if code or desk_code:
        lines += ["", "For the housekeeper: these are code or files outside config/contracts.csv, so the worksheet "
                      "cannot fix them. Paste this section to the housekeeper, who passes each line to the lane named."]
        for owner, subject, f in code:
            lines.append(f"  - {owner}: {subject}: {f.verdict}: {f.evidence}.")
        for owner, line in desk_code:
            lines.append(f"  - {owner}: {line}.")
    lines += ["", "Files"]
    for label, path in paths.items():
        lines.append(f"  {label}: {path}")
    lines += ["", "Nothing was written to the marks, the trades or config/contracts.csv. Mark 'yes' in the "
              "worksheet's apply column for each change you accept, then apply it "
              "(py 2_launcher.py contracts-apply <worksheet>)."]
    if desk is not None:
        lines += ["", "Manual checks (no Bloomberg field can settle these)"]
        lines += [f"  - {m}" for m in manual_checks(desk)]
    return "\n".join(lines) + "\n"


def _pull_line(it: PullItem) -> str:
    """One line per pull ticker: verdict, ticker, the contract in words, what it is for, how many
    trades, then Bloomberg's answer (OK) or its words and what to do (a problem)."""
    n = len(it.trade_ids)
    who = (f"{n} trade{'s' if n != 1 else ''}" if n else
           "risk history only" if it.sources == {SRC_RISK} else "no trade named")
    head = f"{it.verdict:<16} {it.ticker or '-':<20} {it.what}: {'; '.join(it.purposes)}; {who}"
    if it.verdict == PULL_NOT_ASKED:
        return f"{head}. Not asked: {it.reason}."
    a = it.answer
    said = ""
    if a is not None and a.answered and not a.security_error:
        v = a.fields
        px = v.get("PX_LAST") if _num(v.get("PX_LAST")) is not None else v.get("PX_MID")
        bits = [str(v.get("NAME") or "").strip(), str(v.get("CRNCY") or "").strip(),
                str(v.get("EXCH_CODE") or "").strip(),
                f"{'PX_LAST' if _num(v.get('PX_LAST')) is not None else 'PX_MID'} {_g(px)}"
                if _num(px) is not None else "no price",
                f"updated {_date_text(v.get('LAST_UPDATE_DT'))}" if v.get("LAST_UPDATE_DT") not in (None, "") else ""]
        said = "Bloomberg: " + ", ".join(b for b in bits if b)
    if it.verdict == PULL_OK:
        if not said:
            return f"{head}. {it.notes[0][:1].upper() + it.notes[0][1:]}." if it.notes else f"{head}."
        return f"{head}. {said}" + (f" (note: {it.notes[0]})" if it.notes else "") + "."

    message, fix = _findings_words(it.findings)
    return f"{head}. {message}." + (f" {said}." if said else "") + (f" What to do: {fix}." if fix else "")


def _render_pull(result: CheckResult) -> List[str]:
    pl = result.plan.pull
    items = result.pull
    tally = result.pull_counts()
    asked = sum(1 for it in items if it.asked)
    lines = [f"Tickers the pull asks for (book date {pl.as_of.isoformat()}, {pl.db_path})",
             f"  {asked} asked: {tally[PULL_OK]} OK, {tally[PULL_CHECK]} check, {tally[PULL_NO_PRICE]} no price, "
             f"{tally[PULL_UNKNOWN]} unknown security, {tally[PULL_NO_ANSWER]} no answer; {tally[PULL_NOT_ASKED]} "
             "not asked.",
             "  What the pull asks: the Bloomberg library's needs on the book date (P&L marks, USD conversions, "
             "contract dates, the options' inputs), the LME curve pillars, the backfill's forward tenors and the "
             "risk history (every contract of each held root's chain, expired months included, the FX pairs, "
             "LME cash and 3M). An expired contract is judged only on Bloomberg refusing it."]
    counts = pl.source_counts()
    if any(counts.values()):
        lines.append("  From: " + ", ".join(f"{counts[s]} {s}" for s in PULL_SOURCES if counts[s])
                     + " (one ticker can serve several).")
    for e in pl.errors:
        lines.append(f"  Not listed: {e}.")
    order = (PK_FUTURE, PK_OPTION, PK_LME, PK_FX, PK_TENOR, PK_OIS, PK_VOL)
    problems = sorted((it for it in items if it.verdict != PULL_OK),
                      key=lambda it: (PULL_VERDICTS.index(it.verdict), order.index(it.kind), it.expired,
                                      it.root_id or it.pair, it.instrument_id))
    oks = [it for it in items if it.verdict == PULL_OK]
    if problems:
        lines += ["", f"  Problems first ({len(problems)})"] + [f"  {_pull_line(it)}" for it in problems]
    if oks:
        lines += ["", f"  OK ({len(oks)})"] + [f"  {_pull_line(it)}" for it in oks]
    return lines


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
    value_fields = list(dict.fromkeys((*ROOT_FIELDS, *lme_fields(), *OPTION_FIELDS, CHAIN_FIELD, *PULL_FUTURE_FIELDS)))
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
    for it in result.pull:
        if it.asked:
            emit(f"pull_{it.kind}", it.root_id, it.instrument_id, it.ticker, it.verdict, it.answer)
    return columns, rows


def all_worksheet_rows(result: CheckResult, desk: Optional[DeskResult] = None) -> List[Dict[str, str]]:
    """The worksheet: ``worksheet_rows(result)`` and the desk checks' rows, one per (root, field, suggested)."""
    rows = worksheet_rows(result) + (desk.worksheet_rows() if desk is not None else [])
    seen: Set[Tuple[str, str, str]] = set()
    out: List[Dict[str, str]] = []
    for row in rows:
        key = (row["root_id"], row["field"], row["suggested"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def write_reports(result: CheckResult, out_dir, *, now: datetime, desk: Optional[DeskResult] = None,
                  not_run: str = "") -> Dict[str, Path]:
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
        writer.writerows(all_worksheet_rows(result, desk))
    paths["report"].write_text(render_text(result, now=now, paths=paths, desk=desk, not_run=not_run),
                               encoding="utf-8")
    return paths


# --------------------------------------------------------------------------- the book check for the app (2026-09-30)
#
# The Data tab's "Run Bloomberg check" button (ui lane, from a background thread): does Bloomberg
# answer for the book's own tickers? On the press only (hard rule 8): the roots the book holds on
# their generic ticker, the book's futures and options on futures on their own tickers, the LME
# curve of the metals it holds tickets in, and its USD conversion spots. No desk checks, no search,
# no option chains, no universe-wide run; no report file, no marks, no trades.

BOOK_CHECK_PARTS = (PART_ROOTS, PART_LME)
ROW_OK, ROW_CHECK, ROW_NO_ANSWER, ROW_ERROR = "OK", "CHECK", "NO ANSWER", "ERROR"
AREA_FUTURE, AREA_ROOT, AREA_SPOT, AREA_LME, AREA_OPTION = (
    "Future", "Contract root", "FX spot", "LME", "Option on a future")
AREA_PULL = "Pull ticker"          # check_book(include_pull=True): a pull ticker the book rows do not already cover
# Verdicts where the ticker cannot price at all: the P&L it feeds has no mark.
_ERROR_VERDICTS = frozenset({NOT_FOUND, NO_PRICE, NO_TICKER, NO_CURVE, NO_OPTIONS, OFF_CURVE})
_ROW_ORDER = (ROW_ERROR, ROW_NO_ANSWER, ROW_CHECK, ROW_OK)
_AREA_ORDER = (AREA_FUTURE, AREA_OPTION, AREA_LME, AREA_SPOT, AREA_ROOT, AREA_PULL)
_PULL_ROW_STATUS = {PULL_OK: ROW_OK, PULL_CHECK: ROW_CHECK, PULL_NO_ANSWER: ROW_NO_ANSWER}
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _row_status(verdict: str) -> str:
    if verdict == OK:
        return ROW_OK
    if verdict == NO_ANSWER:
        return ROW_NO_ANSWER
    return ROW_ERROR if verdict in _ERROR_VERDICTS else ROW_CHECK


def _plain_root_name(root: Optional[ContractRoot], fallback: str) -> str:
    """'LME copper (third-Wednesday monthly prompts)' -> 'LME copper'."""
    if root is None:
        return fallback
    return re.sub(r"\s*\([^)]*\)", "", root.name).strip() or root.root_id


def _month_words(code: str, year_digits: str, ref_year: int) -> str:
    try:
        return f"{_MONTH_ABBR[month_from_code(code) - 1]}{expand_year(year_digits, ref_year) % 100:02d}"
    except ValueError:
        return f"{code}{year_digits}"


def _future_words(fut: BookFuture, root: Optional[ContractRoot], ref_year: int) -> str:
    """'COMEX copper Dec26'; an option: 'COMEX copper Dec26 call 450'."""
    return _contract_words(fut.instrument_id, fut.kind, root, ref_year, fut.root_id)


def _contract_words(instrument_id: str, kind: str, root: Optional[ContractRoot], ref_year: int,
                    fallback: str = "") -> str:
    """A contract id in plain words: 'COMEX copper Dec26'; kind CMDTY_OPTION: 'COMEX copper Dec26 call 450'."""
    name = _plain_root_name(root, fallback or (root.root_id if root is not None else ""))
    parts = parse_option_ticker(instrument_id) if kind == "CMDTY_OPTION" else None
    if parts is not None:
        _r, code, year, cp, strike, _k = parts
        return f"{name} {_month_words(code, year, ref_year)} {'call' if cp == 'C' else 'put'} {strike}".strip()
    parts4 = parse_bbg_ticker(instrument_id)
    if parts4 is not None:
        return f"{name} {_month_words(parts4[1], parts4[2], ref_year)}".strip()
    return f"{name} {instrument_id}".strip()


def _side_words(quantity: float, unit: str) -> str:
    """'Long 200 lots', 'Short 700 t', 'Flat'."""
    if not quantity:
        return "Flat"
    return f"{'Long' if quantity > 0 else 'Short'} {_g(abs(quantity))} {unit}"


def _answer_words(answer: Optional[Answer], shown: Sequence[Tuple[str, str]]) -> str:
    """What Bloomberg returned, short: its refusal in its own words, or the fields asked."""
    if answer is None:
        return "Not asked"
    if not answer.answered:
        return f"No answer ({answer.security_error})" if answer.security_error else "No answer"
    if answer.security_error:
        return f'Refused: "{answer.security_error}"'
    bits = []
    for label, key in shown:
        value = answer.fields.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (date, datetime)) or key.endswith("_DT") or "_DT_" in key \
                or key == "FUT_NOTICE_FIRST":
            text = _date_text(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            text = _g(value)
        else:
            text = str(value).strip()
        bits.append(f"{label} {text}")
    return ", ".join(bits) if bits else "Answered with no value for the fields asked"


def _findings_words(findings: Sequence[Finding]) -> Tuple[str, str]:
    """(message, fix) from a row's findings: Bloomberg's words kept whole in the message."""
    message = "; ".join(dict.fromkeys(f.evidence for f in findings if f.evidence))
    fixes = []
    for f in findings:
        text = f.action
        if f.owner and f.owner not in text:
            text += f" (a code change: the housekeeper passes it to {f.owner})"
        if text:
            fixes.append(text)
    fix = "; ".join(dict.fromkeys(fixes))
    return (message[:1].upper() + message[1:]) if message else "", (fix[:1].upper() + fix[1:]) if fix else ""


def _book_row(area: str, what: str, instrument_id: str, ticker: str, verdict: str, bloomberg: str, ours: str,
              findings: Sequence[Finding]) -> dict:
    status = _row_status(verdict)
    message, fix = ("", "") if status == ROW_OK else _findings_words(findings)
    return {"area": area, "what": what, "instrument_id": instrument_id, "ticker": ticker, "status": status,
            "bloomberg": bloomberg, "ours": ours, "message": message, "fix": fix}


def book_check_rows(result: CheckResult) -> Tuple[List[dict], Dict[str, int]]:
    """(rows, stats) of a finished book check, rows problems first. The code findings
    (``result.code_findings()``) come as extra 'Contract root' rows, status CHECK; they repeat an
    LME row's finding, so stats (checked, ok, attention, no_answer; asked and answered: the
    tickers asked and how many Bloomberg answered; code) count them apart."""
    roots = load_roots()
    ref_year = result.plan.today.year
    rows: List[Tuple[dict, bool]] = []          # (row, a ticker that was asked)

    for c in result.contracts + result.book_options:
        fut = c.future
        root = roots.get(fut.root_id)
        if fut.kind == "CMDTY_OPTION":
            area = AREA_OPTION
            said = _answer_words(c.answer, (("price", "PX_LAST"), ("expiry", "OPT_EXPIRE_DT"), ("style", "OPT_EXER_TYP")))
            ours = (f"{_side_words(fut.net_lots, 'lots')}, average fill "
                    f"{_g(round(fut.avg_fill, 4)) if fut.avg_fill is not None else '-'}, expiry {fut.expiry}")
        else:
            area = AREA_FUTURE
            said = _answer_words(c.answer, (("price", "PX_LAST"), ("last trade", "FUT_LAST_TRADE_DT"),
                                            ("first notice", "FUT_NOTICE_FIRST")))
            ours = (f"{_side_words(fut.net_lots, 'lots')}, average fill "
                    f"{_g(round(fut.avg_fill, 4)) if fut.avg_fill is not None else '-'}, expiry {fut.expiry}")
            if fut.stored:
                ours += (f"; Bloomberg's dates stored: last trade {fut.stored.get('last_trade_date') or '-'}, "
                         f"first notice {fut.stored.get('first_notice_date') or 'none'}")
        rows.append((_book_row(area, _future_words(fut, root, ref_year), fut.instrument_id, fut.ticker, c.verdict,
                               said if fut.ticker else "Not asked: no Bloomberg ticker", ours, c.findings),
                     bool(fut.ticker)))

    for s in result.spots:
        need = s.need
        n = len(need.trade_ids)
        rows.append((_book_row(AREA_SPOT, f"{need.instrument_id} spot", need.instrument_id, need.ticker, s.verdict,
                               _answer_words(s.answer, (("price", "PX_LAST"),)),
                               f"Converts {n} trade{'s' if n != 1 else ''} to USD", s.findings), True))
    book = result.plan.book
    if book is not None and book.spot_error:
        rows.append((_book_row(AREA_SPOT, "USD conversion spots", "", "", NOT_FOUND, "Not asked",
                               "The Bloomberg library's conversion spots",
                               [Finding(NOT_FOUND, f"The conversion spots were not checked: {book.spot_error}",
                                        "open the Data tab's diagnostics; the library is rebuilt at the next upload")]),
                     False))

    for m in result.lme:
        name = _plain_root_name(m.root, m.root.root_id)
        if m.error:
            rows.append((_book_row(AREA_LME, f"{name} curve", m.root.root_id, "", NOT_FOUND, "Not asked",
                                   "The metal's cash, 3M and monthly tickers",
                                   [Finding(NOT_FOUND, m.error, "tell the housekeeper", owner=LME_OWNER)]), False))
            continue
        for p in m.pillars:
            said = _answer_words(p.answer, (("price", "PX_LAST"), ("currency", "CRNCY"),
                                            ("prompt date", lme_prompt_field()))) if p.asked else p.why_not_asked
            ours = "Prompt " + (p.expected[0].isoformat() if p.expected else "-") + ", USD per tonne"
            rows.append((_book_row(AREA_LME, f"{name} {p.label}", m.root.root_id, p.ticker, p.verdict, said, ours,
                                   p.findings), p.asked))
    metals = {m.root.root_id: m for m in result.lme}
    for t in result.book_lme:
        tk = t.ticket
        name = _plain_root_name(roots.get(tk.root_id), tk.root_id)
        metal = metals.get(tk.root_id)
        cash = next((p for p in metal.pillars if p.kind == "CASH"), None) if metal is not None else None
        said = (f"Cash price {_g(cash.px_last)}" if cash is not None and cash.px_last is not None
                else "No cash price") + (f"; the prompt sits {t.position}" if t.position else "")
        ours = f"{_side_words(tk.tonnes, 't')} at {_g(tk.fill)}, prompt {tk.prompt}"
        rows.append((_book_row(AREA_LME, f"{name} ticket {tk.trade_id}, prompt {tk.prompt}", tk.root_id, "",
                               t.verdict, said, ours, t.findings), False))

    for r in result.roots:
        root = r.root
        said = _answer_words(r.answer, (("name", "NAME"), ("exchange", "EXCH_CODE"), ("currency", "CRNCY"),
                                        ("contract size", "FUT_CONT_SIZE"), ("1.0 move worth", "FUT_VAL_PT"),
                                        ("price", "PX_LAST"))) if r.asked else "Not asked: placeholder root"
        ours = (f"{root.exchange}, {root.currency}, contract size {_g(root.contract_size)} {root.size_unit}, "
                f"1.0 move worth {_g(root.multiplier)}, price scale {_g(root.price_scale)}")
        rows.append((_book_row(AREA_ROOT, _plain_root_name(root, root.root_id), root.root_id, r.ticker, r.verdict,
                               said, ours, r.findings), r.asked))

    if result.pull:
        covered = {row["ticker"].upper() for row, _a in rows if row["ticker"]}
        for it in result.pull:
            if it.ticker and it.ticker.upper() in covered:
                continue                      # judged above as the book's own ticker
            status = _PULL_ROW_STATUS.get(it.verdict, ROW_ERROR)
            if not it.asked:
                message, fix = f"Not asked: {it.reason}", "Fix the contract root in config/contracts.csv (the worksheet)"
                said = "Not asked"
            else:
                message, fix = ("", "") if status == ROW_OK else _findings_words(it.findings)
                said = _answer_words(it.answer, (("name", "NAME"), ("currency", "CRNCY"), ("exchange", "EXCH_CODE"),
                                                 ("price", "PX_LAST"), ("updated", "LAST_UPDATE_DT")))
            n = len(it.trade_ids)
            ours = "; ".join(it.purposes) + (f"; {n} trade{'s' if n != 1 else ''}" if n else "")
            rows.append(({"area": AREA_PULL, "what": it.what[:1].upper() + it.what[1:], "instrument_id": it.instrument_id,
                          "ticker": it.ticker, "status": status, "bloomberg": said, "ours": ours[:1].upper() + ours[1:],
                          "message": message, "fix": fix}, it.asked))

    code_rows = []
    for lane, subject, f in result.code_findings():
        message, fix = _findings_words([f])
        rid, _sp, rest = subject.partition(" ")
        label = rest.split(" [", 1)[0]
        code_rows.append({"area": AREA_ROOT, "what": f"{_plain_root_name(roots.get(rid), rid)} {label}: code change",
                          "instrument_id": rid,
                          "ticker": subject[subject.find("[") + 1:subject.rfind("]")] if "[" in subject else "",
                          "status": ROW_CHECK, "bloomberg": "", "ours": f"Code owned by {lane}",
                          "message": message, "fix": fix})

    asked = [row for row, was_asked in rows if was_asked]
    plain = [row for row, _a in rows]
    n_ok = sum(1 for row in plain if row["status"] == ROW_OK)
    n_none = sum(1 for row in plain if row["status"] == ROW_NO_ANSWER)
    stats = {"checked": len(plain), "ok": n_ok, "attention": len(plain) - n_ok - n_none, "no_answer": n_none,
             "asked": len(asked), "answered": sum(1 for row in asked if row["status"] != ROW_NO_ANSWER),
             "code": len(code_rows)}
    out = plain + code_rows
    out.sort(key=lambda row: (_ROW_ORDER.index(row["status"]), _AREA_ORDER.index(row["area"]), row["what"]))
    return out, stats


def check_book(db_path=None, host: str = "localhost", port: int = 8194,
               on_progress: Optional[Callable[[int, int, str], None]] = None,
               client_factory: Optional[Callable[[str, int], object]] = None,
               today: Optional[date] = None, include_pull: bool = False) -> dict:
    """The book's own tickers against Bloomberg, for the Data tab's "Run Bloomberg check" button.

    Asks, on the press only (hard rule 8), for the roots the book holds (their generic ticker),
    the book's open futures and options on futures on their own tickers, the LME curve of the
    metals it holds tickets in, and its USD conversion spots; nothing else. Writes no report,
    no marks and no trades; never raises. ``client_factory(host, port)`` replaces the Bloomberg
    session (a fake for a call without a terminal; the reachability probe is then skipped).
    ``today`` is the book's day and the LME's (default: ``live.book_today`` and London's today).
    ``on_progress(done, total, words)`` is called before each request goes out and once at the end.
    ``include_pull`` (2026-09-30, off by default: on a real book it is several hundred tickers,
    the risk history's contract chains) adds the tickers "Pull Bloomberg now" asks that the book
    rows do not already cover, as rows of area "Pull ticker".

    Returns {ok, reachable, reason, started_at, finished_at, seconds, requests_sent, summary,
    counts {checked, ok, attention, no_answer}, rows [...]}: ``ok`` True when the check ran and
    Bloomberg answered (the findings are in ``rows``), False with ``reason`` otherwise."""
    started = datetime.now().astimezone()
    out: dict = {"ok": False, "reachable": False, "reason": "", "started_at": "", "finished_at": "", "seconds": 0.0,
                 "requests_sent": 0, "summary": "", "counts": {"checked": 0, "ok": 0, "attention": 0, "no_answer": 0},
                 "rows": []}

    def finish(summary: str = "", reason: str = "") -> dict:
        ended = datetime.now().astimezone()
        out["started_at"] = started.astimezone(timezone.utc).isoformat(timespec="seconds")
        out["finished_at"] = ended.astimezone(timezone.utc).isoformat(timespec="seconds")
        out["seconds"] = round((ended - started).total_seconds(), 1)
        out["reason"] = reason
        out["summary"] = summary or reason
        return out

    def tell(done: int, total: int, words: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(done, total, words)
        except Exception:  # noqa: BLE001 -- progress is display only
            pass

    where = f"{host}:{port}"
    client = None
    try:
        if client_factory is None:
            from data.bloomberg.live import availability
            ok, why = availability(host, port)
            if not ok:
                return finish(reason=f"Bloomberg could not be reached: {why}. Is the Bloomberg Terminal running and "
                                     "logged in on this PC?")
        out["reachable"] = True
        as_of = today or _book_day()
        roots = load_roots()
        try:
            book = load_book(Path(db_path) if db_path else default_db_path(), as_of, roots)
        except BookError as exc:
            return finish(reason=f"The book could not be read: {exc}.")
        the_plan = plan(roots, book=book, book_only=True, parts=BOOK_CHECK_PARTS, today=today or lme_today())
        if include_pull:
            try:
                the_plan.pull = pull_list(book.db_path, as_of, roots)
            except BookError as exc:
                return finish(reason=f"The pull's tickers could not be listed: {exc}.")
        if not the_plan.securities:
            unaskable = sum(1 for f in the_plan.futures + the_plan.book_options if not f.ticker)
            if not unaskable and not the_plan.book_lme and not book.spot_error:
                out["ok"] = True
                return finish("The book holds nothing to check with Bloomberg: no open futures, options on futures, "
                              "LME tickets or conversion spots.")
        total = the_plan.requests
        if the_plan.securities:
            tell(0, total, f"Asking Bloomberg for {the_plan.securities} of the book's tickers in {total} "
                           f"request{'s' if total != 1 else ''}")
            try:
                client = (client_factory or BlpapiClient)(host, port)
            except BloombergUnavailable as exc:
                out["reachable"] = False
                return finish(reason=f"Bloomberg could not be reached: {exc}")
        result = run_check(client, the_plan, log=lambda _s: None, host=where, progress=tell)
        out["requests_sent"] = result.requests_sent
        rows, stats = book_check_rows(result)
        out["rows"] = rows
        out["counts"] = {k: stats[k] for k in ("checked", "ok", "attention", "no_answer")}
        n_att = stats["attention"]
        if the_plan.securities:
            tell(result.requests_sent, max(result.requests_sent, 1), "Done")
        if the_plan.securities and not result.any_answered:
            return finish(reason=f"Bloomberg did not answer any request on {where}; nothing to judge. Run the check "
                                 "again.")
        out["ok"] = True
        summary = (f"Bloomberg answered for {stats['answered']} of {stats['asked']} of the book's tickers; "
                   + (f"{n_att} need{'s' if n_att == 1 else ''} attention" if n_att else "none needs attention"))
        code = stats["code"]
        if code:
            summary += f"; {code} finding{'s are' if code != 1 else ' is'} a code change for the housekeeper"
        return finish(summary + ".")
    except BloombergUnavailable as exc:
        out["ok"] = False
        out["reachable"] = False
        return finish(reason=f"Bloomberg could not be reached: {exc}")
    except Exception as exc:  # noqa: BLE001 -- the button must never crash: said in plain words
        out["ok"] = False
        return finish(reason=f"The Bloomberg check stopped on an error ({type(exc).__name__}: {exc}).")
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:  # noqa: BLE001 -- closing never masks the outcome
                pass


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
    ap.add_argument("--desk", action="store_true",
                    help="the desk checks: futures close against settlement, open interest and volume, LME "
                         "per-prompt liquidity, SGX USD/CNH history, delivery type, exchange holidays, price "
                         "history on file, contract dates stored; a plain run includes them")
    ap.add_argument("--pull", action="store_true",
                    help="only the tickers 'Pull Bloomberg now' asks for on the book date (P&L marks, contract "
                         "dates, LME curve, option inputs, forward tenors, risk history), each asked once and judged; "
                         "reads --db, else the app's database. A run with a book includes it")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8194)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="folder for the reports (default: reports/)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N roots")
    return ap


def _book_day() -> date:
    """The book's today (``live.book_today``: the day turns at 17:00 New York); the local date
    when that cannot be loaded."""
    try:
        from data.bloomberg.live import book_today
        return book_today()
    except Exception:  # noqa: BLE001 -- the check must still run
        return date.today()


def main(argv: Optional[Sequence[str]] = None, *, client_factory: Optional[Callable[[str, int], object]] = None,
         now: Optional[datetime] = None, as_of: Optional[date] = None,
         roots: Optional[Iterable[ContractRoot]] = None) -> int:
    """The command line. Exit 0 when every checked root (and the book) is OK, 1 when anything
    needs attention, 2 when Bloomberg could not be reached (the reason is printed). The desk
    checks never change the exit code; a run that includes them still writes the report when
    Bloomberg cannot be reached (their Bloomberg checks SKIPPED with the reason, checks 7 and 8
    from local data) and exits 2.
    ``client_factory(host, port)``, ``now``, ``as_of`` and ``roots`` are for tests."""
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.limit is not None and args.limit < 1:
        print("--limit must be 1 or more")
        return 1
    root_map = load_roots() if roots is None else {r.root_id: r for r in roots}
    picked = tuple(p for p, on in ((PART_LME, args.lme), (PART_OPTIONS, args.options)) if on)
    desk_on = bool(args.desk or not (picked or args.pull))
    parts = picked or (() if (args.desk or args.pull) else ALL_PARTS)
    today = as_of or lme_today()
    book: Optional[Book] = None
    if args.db or args.book or args.pull:
        if as_of is None:
            as_of = _book_day()
        try:
            book = load_book(Path(args.db) if args.db else default_db_path(), as_of, root_map)
        except BookError as exc:
            print(f"Book check not possible: {exc}.")
            return 1
    try:
        if parts:
            the_plan = plan(root_map, root_ids=args.root, sector=args.sector, book=book, book_only=args.book,
                            limit=args.limit, search=args.search, parts=parts, today=today)
        else:
            the_plan = Plan(roots=[], asked=[], placeholders=[], parts=(), today=today)
        if book is not None and (args.pull or parts):
            # The pull's own tickers: whenever a book is checked, and alone with --pull.
            try:
                the_plan.pull = pull_list(book.db_path, book.as_of, root_map)
            except BookError as exc:
                print(f"Pull tickers not listed: {exc}.")
                return 1
        desk_plan = plan_desk(root_map, book=book, as_of=as_of or _book_day(), root_ids=args.root,
                              sector=args.sector) if desk_on else None
    except ValueError as exc:
        print(str(exc))
        return 1
    if the_plan.empty and desk_plan is None:
        print("Nothing to check: no contract root matches the filters" + (" and the book holds none." if book else "."))
        return 1
    run_main = bool(parts) or the_plan.pull is not None
    if run_main:
        for line in the_plan.describe():
            print(line)
    if desk_plan is not None:
        for line in desk_plan.describe():
            print(line)
    if args.dry_run:
        print("Dry run: nothing was asked and nothing was written.")
        return 0

    client = None
    host = f"{args.host}:{args.port}"
    desk_needs = desk_plan is not None and desk_plan.needs_bloomberg
    if (run_main and the_plan.needs_bloomberg) or desk_needs:
        if run_main and the_plan.needs_bloomberg:
            print(f"Asking Bloomberg on {host} for {the_plan.securities} securities in {the_plan.requests} requests"
                  + (f", then up to {the_plan.max_option_followups} option tickers in up to "
                     f"{the_plan.max_followup_requests} more" if the_plan.max_option_followups else "")
                  + (f", and up to {the_plan.max_searches} searches" if the_plan.search else "") + ".")
        if desk_needs:
            print(f"Desk checks: asking Bloomberg on {host} for {desk_plan.securities} securities in "
                  f"{desk_plan.requests} requests.")
        try:
            client = (client_factory or BlpapiClient)(args.host, args.port)
        except BloombergUnavailable as exc:
            print(f"Bloomberg could not be reached: {exc}")
            if desk_plan is not None:
                desk = run_desk(None, desk_plan, offline="Bloomberg could not be reached (the reason is at the top)")
                empty = CheckResult(the_plan, [], [], [], host=host)
                paths = write_reports(empty, args.out, now=now or datetime.now(), desk=desk, not_run=str(exc))
                _print_desk(desk)
                print(f"Report:    {paths['report']} (desk checks only)")
            return 2
    desk: Optional[DeskResult] = None
    try:
        result = run_check(client, the_plan, host=host) if run_main else CheckResult(the_plan, [], [], [], host=host)
        if desk_plan is not None:
            desk = run_desk(client, desk_plan)
    except BloombergUnavailable as exc:
        print(f"Bloomberg could not be reached: {exc}")
        return 2
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()
    if run_main and the_plan.securities and not result.any_answered:
        print(f"Bloomberg did not answer any request on {host}; nothing to judge. Run the check again.")
        return 2
    stamp_time = now or datetime.now()
    paths = write_reports(result, args.out, now=stamp_time, desk=desk)
    counts = result.counts()
    if result.roots:
        print("Roots: " + ", ".join(f"{counts[v]} {v}" for v in ROOT_VERDICTS if counts[v]) + ".")
    for label, items, verdicts in (("LME metals", result.lme, LME_VERDICTS),
                                   ("Option roots", result.options, OPTION_VERDICTS)):
        if items:
            tally = {v: sum(1 for i in items if i.verdict == v) for v in verdicts}
            print(f"{label}: " + ", ".join(f"{n} {v}" for v, n in tally.items() if n) + ".")
    if the_plan.book is not None:
        bad_contracts = sum(1 for c in result.contracts if c.verdict != OK)
        bad_spots = sum(1 for s in result.spots if s.verdict != OK)
        print(f"Book: {len(result.contracts)} contracts, {bad_contracts} flagged; "
              f"{len(result.spots)} spots, {bad_spots} flagged"
              + (f"; {len(result.book_options)} options, {sum(1 for c in result.book_options if c.verdict != OK)} "
                 f"flagged" if result.book_options else "")
              + (f"; {len(result.book_lme)} LME tickets, {sum(1 for t in result.book_lme if t.verdict != OK)} "
                 f"flagged" if result.book_lme else "") + ".")
    if the_plan.pull is not None:
        tally = result.pull_counts()
        print(f"Pull tickers: {sum(1 for it in result.pull if it.asked)} asked: "
              + ", ".join(f"{tally[v]} {v}" for v in (PULL_OK, PULL_CHECK, PULL_NO_PRICE, PULL_UNKNOWN, PULL_NO_ANSWER))
              + f"; {tally[PULL_NOT_ASKED]} not asked.")
    if desk is not None:
        _print_desk(desk)
    code = len(result.code_findings()) + (len(desk.housekeeper_lines()) if desk is not None else 0)
    if code:
        print(f"For the housekeeper: {code} finding{'s' if code != 1 else ''} in code, listed at the end "
              "of the report.")
    ws = all_worksheet_rows(result, desk)
    print(f"Report:    {paths['report']}")
    print(f"Fields:    {paths['fields']}")
    print(f"Worksheet: {paths['worksheet']} ({len(ws)} rows, {sum(1 for w in ws if w['apply'] == 'yes')} "
          "pre-filled 'yes')")
    return 1 if result.needs_attention else 0


def _print_desk(desk: DeskResult) -> None:
    counts = desk.counts()
    print("Desk checks: " + ", ".join(f"{counts[s]} {s}" for s in DESK_STATUSES if counts[s]) + " ("
          + "; ".join(f"{c.number} {c.status}" for c in desk.checks) + ").")


if __name__ == "__main__":
    sys.exit(main())
