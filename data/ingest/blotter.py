"""Trade blotter CSV/Excel -> instruments, trades, trade_legs.

Source: the transaction-level blotter export (``data/raw/new_sample_trades.csv``-shaped
files), the app's only trade source. Shared dataclasses/regexes live in
``data/ingest/common.py`` (extracted 2026-09-17 when ``data/ingest/bnp.py`` and
``data/ingest/irs.py`` -- the retired BNP CSV parser -- were deleted).

Row kind is decided by ``Fin Type`` (``Product`` is the fallback when Fin Type is blank
or unrecognised): FORWARD, CURRENCY, FUTURE, OPTION, INTEREST_RATE_SWAP -- matched by
keyword after normalisation, so 'Futures', 'FX Forward', 'Interest Rate Swap', 'fx
option' all resolve. Rows whose Status says cancelled/rejected/pending/void, or whose
Fund is populated and is not NMMF, are filtered out and counted. A missing Status or
Fund column (or a blank cell) never excludes a row.

This file is transaction-level (one row per fill), unlike the BNP snapshot:
  - FORWARD: a trade + 2 FX_NEAR legs. The ``Description`` (``TD .. VD .. SELL/BUY ..
    VS .BUY/SELL .. @ rate``) is the primary source of dates, currencies and rate when
    it parses; when it is blank or in another shape, the structured ``TradeDate`` /
    ``Settle Date`` / ``Buy Currency`` / ``Sell Currency`` / ``Price`` columns are used
    instead. Amounts always come from ``BuyCurrency Amount`` / ``SellCurrency Amount``
    (fallback: ``Quantity`` x rate).
  - CURRENCY: the CASH instrument is written for the row's own currency, and (2026-09-18,
    user's cash-ladder spec) a row naming two currencies is a SPOT FX fill: a trade
    (product FX_SPOT) + 2 FX_NEAR legs on ``Settle Date``, exactly like a forward. A
    single-currency row stays instrument-only. See ``_parse_spot_from_currency_row``.
  - FUTURE: a trade + 1 NOTIONAL leg (``Quantity`` = contracts signed by ``Side``).
  - OPTION: product FX_OPTION, 1 NOTIONAL leg in the pair's base currency, quantity
    signed by ``Side``, price = premium fill. Strike from the Description when present
    (genuinely absent from the file for some rows -- 0.0 "not known" sentinel, never
    invented, and listed on ``ParseResult.options_missing_strike`` so the UI can ask).
    Expiry / call-put come from ``Symbol`` when it parses, cross-checked against the
    Description's own date/CALL-PUT word when Description is populated (a disagreement
    rejects, per the tolerance rule below) rather than trusting the Symbol blindly.
    ``NetInvoice`` is |Quantity x Price| but its sign is unreliable, so it never gives
    direction: it rebuilds a Price / Quantity cell that is unusable, and is otherwise a
    cross-check that warns above 0.5 % and never rejects.
  - INTEREST_RATE_SWAP: + = pay fixed, - = receive fixed (user-confirmed 2026-09-16), in
    full notional units. Direction, in order: the user's stored override
    (``data/ingest/irs_direction.py`` -- the PRIMARY source, since neither the reference
    export nor the user's own carries any direction marker); else every EXPLICIT signal
    in the row (brackets / minus on Notional, Quantity, Current Face, Original Face --
    not NetInvoice or Gross Amnt/Principal, which on a swap are upfront cash amounts; a
    sell-type ``Side``; pay / receive wording in Description, Notes, Swap Type,
    RollSide, Tran Type), two of which contradicting each other reject the row; else
    pay fixed BY DEFAULT, recorded as such per swap on ``ParseResult.irs_directions``.
    ``Side`` = Buy and an unsigned amount are NOT signals (both sit on every reference
    row, receivers included), and direction is never inferred from anything else.

Tolerance rule (user instruction 2026-09-17, "as flexible as possible"): a blank,
missing or oddly formatted field never rejects a row when the value can be recovered
from another column; only a genuine contradiction between two populated fields does
(pair vs buy/sell currencies, value date in Symbol vs Description, option pair vs
Currency Pair). Rejected rows are counted and reported, never coerced or invented.

Numbers (2026-09-18, the "could not convert string to float" / "24 Jul" incident): no
text may ever reach a numeric column. Excel turns a number such as 7.24 into the date
24-Jul, and a real .xlsx date cell reads as '2026-07-24 00:00:00'. ``_num`` is strict
(the whole cell must be one number; it used to delete every non-digit, so '24 Jul'
became 24.0 and '7/24/2026' 7242026.0), a cell that is not a number is rebuilt from
another column (forward / spot rate and amounts from each other, Quantity and
NetInvoice; option Price and Quantity from |NetInvoice|; futures price from NetInvoice
and fees, contracts from Notional / multiplier; swap notional from Quantity x 1e6,
fixed rate from Yield / Description) with a ``ParseWarning``, and only when nothing can
rebuild it is that ONE row rejected, naming the column and the cell. ``_enforce_numeric``
is the last gate: only finite numbers reach a REAL column (NaN binds as NULL and used to
fail the whole upload). ``non_numeric_cells`` lists text already sitting in a database.

Input tolerance (``read_table``): UTF-8 with or without BOM, or cp1252; ',' ';' tab or
'|' delimiters; a header row anywhere in the first 50 lines (title/preamble rows are
skipped); any column casing/whitespace; extra, missing and reordered columns; single-
or multi-sheet workbooks (the first sheet that looks like a blotter is used). Within a
file, a repeated ``Trade Id`` keeps the row with the highest ``Version`` (last row
otherwise). ``load`` is idempotent: re-uploading replaces trades of the same id.
"""
from __future__ import annotations

import csv
import logging
import math
import numbers
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd

from data.ingest.common import (
    CASH_CCY_RE,
    DESCRIPTION_RE,
    FORWARD_SYMBOL_RE,
    Instrument,
    InstrumentOption,
    IrsDirection,
    IRS_DESCRIPTION_RE,
    IRS_SYMBOL_RE,
    NDF_CCYS,
    NO_DIRECTION_SIGNAL,
    ParseWarning,
    PERPETUAL,
    Reject,
    Trade,
    TradeLeg,
    future_expiry,
    FUTURE_MULTIPLIERS,
)

log = logging.getLogger(__name__)

SOURCE = "XLSX"  # closest value in CLAUDE.md's trades.source enum ('BNP | XLSX | MANUAL')
FUND = "NMMF"
IN_SCOPE_TYPES = ("FORWARD", "CURRENCY", "FUTURE", "OPTION", "INTEREST_RATE_SWAP")
EXCLUDED_STATUS_WORDS = ("cancel", "reject", "void", "pend", "delet", "fail", "error", "draft")
# Option / futures NetInvoice vs what Quantity x Price implies: a larger relative gap is a
# warning, never a reject.
NET_INVOICE_TOLERANCE = 0.005
# FX rows (FORWARD, spot CURRENCY): |base x rate - quote| / quote above this warns, never
# rejects. Chosen against the 828 reference FX rows, all of which pass: the worst is
# 0.0404 % (Trade Id 896192283, a 10 EUR spot ticket at 1.14046 whose 11.4046 USD is
# booked as 11.40 -- pure cent rounding), the worst forward 1.6e-8. The floor, in quote
# currency units, is that rounding (cents, or whole units for JPY-style amounts) on a
# ticket too small for a relative tolerance to mean anything.
FX_CONSISTENCY_TOLERANCE = 0.001
FX_CONSISTENCY_FLOOR = 1.0

# Reference header, exactly as data/raw/new_sample_trades.csv's own header row: the
# casing every row.get(...) below expects. Incoming columns matching case-insensitively
# are re-cased to this; anything else is left alone and ignored.
REFERENCE_HEADER = (
    "Status,Firm,Client Domicile,Description,Side,Fin Type,Trade Id,Version,RollSide,"
    "Swap ID,Symbol,Underlying Symbol,Quantity,Price,Yield,Total Fees,Accrued Fees,"
    "Counterparty,Execution Venue,Counterparty Desc,Trader,Clearing Cpty,"
    "Trade Request ID,Desk,Fund,PBRoot,Position Block,TradeDate,NotificationID,"
    "Email Notification Status,Is Swap,Currency,Valuation Currency,NetInvoice,Notes,"
    "CCP/Confirm ID,Commission,Commission Type,Settle Type,Settle Date,PaymentDate,"
    "Swap Type,Dividend,Spread,FixedRate,Notional,IsSellOfBook,Invoice,Invoice Comm,"
    "QtyFactor,Region,OTC Type,Oasys Ref Id,External Ref Id,Tran Type,"
    "CDS Classification,Effective Date,Termination,CreateDate,LastModified,ModifiedBy,"
    "Account Type,ExtAccount,Sub Account,PSET Code,Is Excess Return,Counterparty Id,"
    "Alt Src,Instrument Id,Product,CUSIP,SEDOL,LoanxID,ISIN,BB_YK_IDENTIFIER,FOID,"
    "BusinessLine,TradeGroup,TrailerTradeId,Error Message,PositionType Id,"
    "Repo Interest Index,Order Id,Cut Time,Cut Location,Repo Term Date,Close Date,"
    "Repo Financing Interest,Repo Interest Rate,Premium,Adj. Expiry Date,Factor,"
    "Original Face,Current Face,Gross Amnt/Principal,Accrued Interest,"
    "External Execution Id,Settlement Status,Created By,Currency Pair,Buy Currency,"
    "Sell Currency,BuyCurrency Amount,SellCurrency Amount,PayLegPmtFreq,"
    "RecvLegPmtFreq,PayLegDCF,RecvLeg DCF,FxOption Type,PM Name,CPI Factor"
).split(",")
_CANONICAL_BY_KEY = {re.sub(r"[^a-z0-9]", "", c.casefold()): c for c in REFERENCE_HEADER}
# Columns whose presence identifies the header row / a blotter-shaped sheet.
HEADER_MARKERS = ("symbol", "tradeid", "fintype", "product")

OPTION_SYMBOL_RE = re.compile(r"^([A-Z]{6})(\d{6})([CP])-(\d+)$")
STRIKE_RE = re.compile(r"(\d+\.\d+)\s+STRIKE")
STRIKE_COLUMNS = ("Strike", "Strike Price", "Strike Rate", "Strike Px", "Option Strike", "StrikePrice")
PAIR_RE = re.compile(r"^([A-Z]{3})[ /\-]?([A-Z]{3})(?![A-Z])")
US_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
# Market convention for which currency is the base when only the two currencies of a
# pair are known (last-resort fallback, logged when used).
PAIR_PRIORITY = ("XAU", "XAG", "XPT", "XPD", "EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY")
_UNAMBIGUOUS_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                             "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
                             "%Y%m%d")
_DAY_FIRST_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")
_MONTH_FIRST_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M")
DATE_FORMATS = _UNAMBIGUOUS_DATE_FORMATS + _DAY_FIRST_FORMATS  # the file's own convention
# Columns whose numeric d/m/y values decide, per file, whether the export is day-first
# (the reference export: 20/8/2026) or month-first (a US-locale export: 8/20/2026).
_DATE_ORDER_COLUMNS = ("TradeDate", "Settle Date", "Effective Date", "Termination", "Adj. Expiry Date",
                       "PaymentDate", "CreateDate", "LastModified", "Close Date")
_NUMERIC_DMY_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")
# Set by parse() for the duration of one file; True = the export writes day before month.
_DAY_FIRST = True
# Free-text markers of a non-vanilla payoff, matched as whole words on the Description /
# FxOption Type / Notes text. Order matters: the first hit wins.
_PAYOFF_KEYWORDS = (
    (("NO TOUCH", "NOTOUCH", "DNT", "NT"), "NO_TOUCH"),
    (("ONE TOUCH", "ONETOUCH", "OT"), "ONE_TOUCH"),
    (("DIGITAL", "DIGI", "BINARY", "EUROPEAN DIGITAL", "CASH OR NOTHING"), "DIGITAL"),
    (("KNOCK IN", "KNOCKIN", "KI", "RKI", "UP AND IN", "DOWN AND IN"), "BARRIER_KI"),
    (("KNOCK OUT", "KNOCKOUT", "KO", "RKO", "UP AND OUT", "DOWN AND OUT"), "BARRIER_KO"),
    (("ASIAN", "AVERAGE RATE", "AVERAGE"), "ASIAN"),
    (("AMERICAN",), "AMERICAN"),
)
EXCEL_EPOCH = date(1899, 12, 30)
# Payoffs that cannot be priced without a strike (touch options use the barrier level
# instead) -- the same set data/ingest/manual.py and engine/options enforce.
STRIKE_PAYOFFS = ("VANILLA", "DIGITAL", "AMERICAN", "ASIAN", "BARRIER_KI", "BARRIER_KO")


@dataclass
class ParseResult:
    day_first: bool = True  # date order detect_day_first found for this file
    trades: List[Trade] = field(default_factory=list)
    legs: List[TradeLeg] = field(default_factory=list)
    instruments: Dict[str, Instrument] = field(default_factory=dict)
    instrument_options: Dict[str, InstrumentOption] = field(default_factory=dict)
    rejects: List[Reject] = field(default_factory=list)
    n_forward: int = 0
    n_currency: int = 0
    n_spot: int = 0         # CURRENCY rows that named two currencies and became FX_SPOT trades
    n_future: int = 0
    n_option: int = 0
    n_irs: int = 0
    n_skipped_irs: int = 0
    n_skipped_other: int = 0
    # the rows behind n_skipped_other, named: (row_no, symbol, reason) -- a row of a type the
    # app does not load must never vanish without a trace (user, 2026-09-21)
    skipped_other_rows: list = field(default_factory=list)
    n_skipped_status_or_fund: int = 0
    n_superseded: int = 0   # earlier versions of a Trade Id repeated within the file
    n_updated: int = 0      # set by load(): trades that already existed and were replaced
    # Rows that loaded but needed a repair or failed a cross-check (a Price that arrived
    # as a date and was rebuilt from the amounts, an option NetInvoice that disagrees
    # with Quantity x Price, ...). Never rejects.
    warnings: List[ParseWarning] = field(default_factory=list)
    # trade_id -> which way each swap was read and which signal decided it (or
    # NO_DIRECTION_SIGNAL when the file carries none and pay fixed was assumed).
    irs_directions: Dict[str, IrsDirection] = field(default_factory=dict)
    # Option instruments the file gives no strike for (stored as the schema's 0 = "not
    # known" sentinel, never a real strike of 0), so the UI can ask for them by name.
    options_missing_strike: List[str] = field(default_factory=list)
    trade_rows: Dict[str, int] = field(default_factory=dict)   # trade_id -> file row number
    # Input, not output: the user's stored pay/receive overrides (load() passes
    # irs_direction.get_overrides). An override always wins over a file signal.
    direction_overrides: Dict[str, str] = field(default_factory=dict)
    n_direction_overrides: int = 0   # set by load(): swaps on file facing the way the user set

    def information_notes(self) -> List[str]:
        """Things the app also shows persistently elsewhere (the Rates notice, the
        Blotter's missing-terms banner), so they inform and never count as warnings:
        overrides kept, swaps defaulted to pay fixed, swaps that took their direction
        from a marker in the file, options with no strike. Counts and names only."""
        out: List[str] = []
        if self.n_direction_overrides:
            n = self.n_direction_overrides
            out.append(f"{n} rate swap direction{'s' if n != 1 else ''} set by you kept (your setting wins over the file).")
        defaulted = sorted(t for t, d in self.irs_directions.items() if d.defaulted)
        if defaulted:
            out.append(f"{len(defaulted)} rate swap(s) carry no pay/receive marker in the file and were read as pay "
                       f"fixed: {_some(defaulted)}. Set Pay or Receive in the Rates table.")
        signalled = sorted(f"{t} {_DIRECTION_WORDS[d.direction]}" for t, d in self.irs_directions.items()
                           if not d.defaulted and not d.decided_by.startswith("user override"))
        if signalled:
            out.append(f"{len(signalled)} rate swap(s) took their direction from a marker in the file: {_some(signalled)}.")
        if self.options_missing_strike:
            out.append(f"{len(self.options_missing_strike)} option(s) have no strike in the file: "
                       f"{_some(self.options_missing_strike)}.")
        return out

    def warning_notes(self) -> List[str]:
        """Where the file's content was doubtful and the parser had to rebuild, ignore or
        distrust a cell (`self.warnings`): one sentence, the rows named, the first few
        spelled out."""
        if not self.warnings:
            return []
        rows = sorted({w.row_no for w in self.warnings})
        shown = "; ".join(f"row {w.row_no} {w.symbol}: {w.message}" for w in self.warnings[:3])
        more = f"; and {len(self.warnings) - 3} more" if len(self.warnings) > 3 else ""
        return [f"{len(self.warnings)} cell(s) in {len(rows)} row(s) were doubtful and were rebuilt or ignored "
                f"(rows {_some([str(r) for r in rows])}): {shown}{more}."]

    def notes(self) -> List[str]:
        """Plain sentences for the upload summary: `information_notes` then `warning_notes`."""
        return self.information_notes() + self.warning_notes()


def _some(names: List[str], limit: int = 8, head: int = 5) -> str:
    """'a, b, c' -- or the first few and 'and n more' once the list passes `limit`."""
    names = list(names)
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:head]) + f" and {len(names) - head} more"


# --------------------------------------------------------------------------- cell helpers
_BLANK_WORDS = frozenset(("nan", "nat", "none", "null", "n/a", "#n/a", "-", "--"))
_MONTH_WORDS = frozenset(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))
_CURRENCY_MARKS_RE = re.compile(r"[$€£¥]")
# What a '%' sign in a numeric cell means is decided per column, never silently (`_num`'s
# `percent` argument). The columns, as decided 2026-09-18:
#   'keep'      FixedRate, Yield (INTEREST_RATE_SWAP): the file writes 3.98 for 3.98 %, so
#               '3.98%' is that same 3.98 -- the sign adds nothing and nothing is scaled.
#   'fraction'  Price, Premium on an OPTION row: the value is a fraction of the notional
#               (0.00579), so '0.58%' means 0.0058 -- divided by 100, and warned about,
#               because stripping the sign (the old behaviour) read it 100 times too big.
#   'refuse'    every other numeric cell (amounts, Quantity, Notional, NetInvoice, fees,
#               FX and futures Price, strike columns, Version): a percent sign there makes
#               no sense, so the cell is not a number and is rebuilt or rejected by name.
PERCENT_KEEP, PERCENT_FRACTION, PERCENT_REFUSE = "keep", "fraction", "refuse"
_CODE_PREFIX_RE = re.compile(r"^([A-Za-z]{3})\s*(?=[-+.\d])")     # 'USD 5', 'USD-5'
_CODE_SUFFIX_RE = re.compile(r"(?<=[\d.])\s*([A-Za-z]{3})$")       # '5 USD'
_PLAIN_NUMBER_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_MONTH_NAME_RE = re.compile(r"(?<![A-Za-z])(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?![A-Za-z])", re.I)
_DATE_SHAPE_RE = re.compile(r"^\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}(\s|T|$)|^\s*\d{1,2}:\d{2}")


def _ungroup(s: str) -> Optional[str]:
    """Digits with their thousands separators removed and a decimal comma turned into a
    point, or None when the separators fit no number layout (so '24,07,2026' or
    '1,2,3' is refused, never squeezed into a number)."""
    if re.fullmatch(r"\d{1,3}(?:[ '’]\d{3})+(?:[.,]\d+)?", s):          # 1 000 000,5 / 1'000'000.5
        s = re.sub(r"[ '’]", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):                                       # 1.234.567,89
            if not re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d*", s):
                return None
            return s.replace(".", "").replace(",", ".")
        if not re.fullmatch(r"\d{1,3}(?:,\d{2,3})+\.\d*(?:[eE][+-]?\d+)?", s):  # 1,137,580.00
            return None
        return s.replace(",", "")
    if "," in s:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s):                           # 35,000,000
            return s.replace(",", "")
        if re.fullmatch(r"\d+,\d+", s):                                      # 0,00579 (decimal comma)
            return s.replace(",", ".")
        return None
    return s


def _num(v, percent: str = PERCENT_REFUSE) -> float:
    """A cell as a finite float, or NaN -- never 0, never a guess. Tolerates thousands
    separators, a decimal comma, currency symbols / a 3-letter currency code, '(1,000)'
    negatives, a trailing '-' and blank cells. A '%' sign is handled as the caller says
    the column means it (PERCENT_KEEP / PERCENT_FRACTION, see above); by default it makes
    the cell not a number.

    STRICT about what is left (2026-09-18): the whole cell has to read as one number.
    Until then every character that was not a digit was simply deleted, so text that
    Excel had turned into a date became a wrong number instead of a miss -- '24 Jul'
    read as 24.0, 'Jul-24' as -24.0, '7/24/2026' as 7242026.0, a time '05:45:36' as
    54536.0 -- and '1e999' as inf. A date / datetime / time cell object, a bool, and
    any text that is not a number are NaN here, so the caller recovers the value from
    another column or rejects that one row, naming the cell (`_bad_cell`)."""
    if v is None or isinstance(v, bool):
        return math.nan
    if isinstance(v, numbers.Real):
        x = float(v)
        return x if math.isfinite(x) else math.nan
    if isinstance(v, (datetime, date, time)):          # pd.Timestamp is a datetime
        return math.nan
    s = str(v).replace("−", "-").replace(" ", " ").strip()
    if s == "" or s.lower() in _BLANK_WORDS:
        return math.nan
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    has_percent = "%" in s
    if has_percent:
        if percent == PERCENT_REFUSE or s.count("%") > 1:
            return math.nan
        s = s.replace("%", "").strip()
    s = _CURRENCY_MARKS_RE.sub("", s).strip()
    for rx in (_CODE_PREFIX_RE, _CODE_SUFFIX_RE):
        m = rx.search(s)
        if m:
            if m.group(1).upper() in _MONTH_WORDS:      # '24 Jul', 'Jul-24': a date, not a currency code
                return math.nan
            s = (s[:m.start()] + s[m.end():]).strip()
    if s.endswith("-"):
        neg, s = True, s[:-1].strip()
    if s[:1] in ("+", "-"):
        neg, s = neg or s[0] == "-", s[1:].strip()
    s = _ungroup(s)
    if s is None or not _PLAIN_NUMBER_RE.fullmatch(s):
        return math.nan
    x = float(s)
    if not math.isfinite(x):
        return math.nan
    if has_percent and percent == PERCENT_FRACTION:
        x /= 100.0
    return -x if neg else x


def _bad_cell(row: pd.Series, col: str, percent: str = PERCENT_REFUSE) -> Optional[str]:
    """The cell's own text when it is populated but is not a number (the '24-Jul' /
    datetime-cell case), else None -- a blank cell is not a bad cell. `percent` must be
    the mode the caller reads the column with."""
    v = row.get(col)
    text = _s(v)
    if text == "" or text.lower() in _BLANK_WORDS:
        return None
    return text if math.isnan(_num(v, percent)) else None


def _unusable(row: pd.Series, col: str, percent: str = PERCENT_REFUSE) -> str:
    """How a cell that could not be used reads in a warning or a reject: 'Price is
    blank', or 'Price '24-Jul' is not a number (it reads as a date ...)'."""
    bad = _bad_cell(row, col, percent)
    return f"{col} is blank" if bad is None else _not_a_number(col, bad)


def _looks_like_date(text: str) -> bool:
    return bool(_MONTH_NAME_RE.search(text) or _DATE_SHAPE_RE.search(text))


def _not_a_number(col: str, text: str) -> str:
    """'Price '24-Jul' is not a number (it reads as a date ...)' -- the wording every
    numeric-cell reject and repair warning uses, naming the column and the cell. (Excel
    is the usual cause: it turns a typed 7.24 into the date 24-Jul.)"""
    why = " (it reads as a date, as Excel makes of 7.24)" if _looks_like_date(text) else ""
    return f"{col} {text!r} is not a number{why}"


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def detect_day_first(df: pd.DataFrame) -> bool:
    """Whether this export writes numeric dates day-first. Looks at every value of the
    date columns: a first component above 12 proves day-first, a second component above
    12 proves month-first. Ties and no evidence keep the reference export's convention
    (day-first). One convention per file: a real export never mixes them."""
    day_first_votes = month_first_votes = 0
    for col in _DATE_ORDER_COLUMNS:
        if col not in df.columns:
            continue
        for v in df[col].astype(str):
            m = _NUMERIC_DMY_RE.match(v)
            if not m:
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12 and b <= 12:
                day_first_votes += 1
            elif b > 12 and a <= 12:
                month_first_votes += 1
    if month_first_votes > day_first_votes:
        return False
    return True


def _date(v, day_first: Optional[bool] = None) -> Optional[str]:
    """Any common date shape -> ISO, or None if blank/unparseable. Numeric d/m/y shapes
    follow the order `detect_day_first` found for the file (`_DAY_FIRST`, set by
    `parse`); an impossible reading in that order (e.g. 8/20/2026 in a day-first file)
    falls back to the other order rather than rejecting."""
    s = _s(v)
    if not s:
        return None
    if day_first is None:
        day_first = _DAY_FIRST
    ordered = (_DAY_FIRST_FORMATS + _MONTH_FIRST_FORMATS) if day_first else (_MONTH_FIRST_FORMATS + _DAY_FIRST_FORMATS)
    for fmt in _UNAMBIGUOUS_DATE_FORMATS + ordered:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    if re.fullmatch(r"\d{5}(\.0+)?", s):
        return (EXCEL_EPOCH + timedelta(days=int(float(s)))).isoformat()
    try:
        ts = pd.to_datetime(s, dayfirst=day_first)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.date().isoformat()


def detect_payoff(*texts: str) -> str:
    """Payoff type named in free text (Description / FxOption Type / Notes), else
    'VANILLA'. Whole-word match after upper-casing so 'KO' never fires inside 'TOKYO'."""
    words = " ".join(re.sub(r"[^A-Z0-9]+", " ", _s(t).upper()) for t in texts)
    padded = f" {words} "
    for keys, payoff in _PAYOFF_KEYWORDS:
        if any(f" {k} " in padded for k in keys):
            return payoff
    return "VANILLA"


def _us_date(s: str) -> str:
    return datetime.strptime(s, "%m/%d/%Y").date().isoformat()


def _us_date_or_none(s: str) -> Optional[str]:
    try:
        return _us_date(s)
    except ValueError:
        return None


def _option_terms_from_text(desc: str, fxoption_type) -> tuple:
    """Expiry / call-put implied by free text (Description, FxOption Type), or None for
    either that isn't present there. US_DATE_RE dates are always mm/dd/yyyy (the fixed
    shape of this file's Description text, like the FORWARD 'TD .. VD ..' dates --
    verified on the reference sample's 8 OPTION rows), so this always parses them with
    ``_us_date``, never the file's own day-first/month-first convention (``_date``):
    a day <= 12 mm/dd/yyyy Description date would otherwise silently misread as
    day-first instead of failing and falling back (undetected on the reference sample,
    where every embedded day happens to be > 12, but a real bug for any other file).
    Used both as the fallback source when Symbol doesn't parse, and to cross-check the
    Symbol-derived terms when it does (a populated Description that disagrees is a
    contradiction, not silently trusted -- the tolerance rule rejects contradictions
    between two populated fields, it does not exempt the Symbol)."""
    dates = US_DATE_RE.findall(desc)
    expiry = _us_date_or_none(dates[-1]) if dates else None
    words = set(re.sub(r"[^A-Z]+", " ", (desc + " " + _s(fxoption_type)).upper()).split())
    option_type = "CALL" if words & {"CALL", "C"} else "PUT" if words & {"PUT", "P"} else None
    return expiry, option_type


def _mmddyy(s: str) -> Optional[str]:
    try:
        return datetime.strptime(s, "%m%d%y").date().isoformat()
    except ValueError:
        return None


def _side(v) -> Optional[str]:
    s = _s(v).casefold()
    if s in ("buy", "b", "bought", "long", "bot", "+", "purchase"):
        return "Buy"
    if s in ("sell", "s", "sold", "short", "sld", "-", "sale"):
        return "Sell"
    return None


def _ccy(code) -> Optional[str]:
    """'DOL.C-USAA' -> 'USD'; 'JPY.C-JPAA' -> 'JPY'; 'usd' -> 'USD'; None if unrecognised."""
    s = _s(code).upper()
    m = CASH_CCY_RE.match(s)
    if m:
        c = m.group(1)
    elif re.fullmatch(r"[A-Z]{3}", s):
        c = s
    else:
        return None
    return "USD" if c == "DOL" else c


def _pair_of(*candidates) -> Optional[str]:
    for c in candidates:
        m = PAIR_RE.match(_s(c).upper())
        if m and m.group(1) != m.group(2):
            return m.group(1) + m.group(2)
    return None


def _pair_by_convention(a: str, b: str) -> str:
    def rank(c):
        return PAIR_PRIORITY.index(c) if c in PAIR_PRIORITY else len(PAIR_PRIORITY) + ord(c[0])
    base, quote = sorted((a, b), key=rank)
    return base + quote


def _status_excluded(v) -> bool:
    s = _s(v).casefold()
    return any(w in s for w in EXCLUDED_STATUS_WORDS)


def _fund_excluded(v) -> bool:
    s = _s(v).casefold()
    return bool(s) and s != FUND.casefold()


def _kind_of(label: str) -> Optional[str]:
    s = re.sub(r"[^A-Z0-9]+", " ", _s(label).upper()).strip()
    if not s:
        return None
    words = set(s.split())
    if words & {"IRS", "OIS", "INTEREST"} or "INTEREST RATE" in s:
        return "INTEREST_RATE_SWAP"
    if words & {"OPTION", "OPTIONS", "OPT"}:
        return "OPTION"
    if words & {"FUTURE", "FUTURES", "FUT"}:
        return "FUTURE"
    if words & {"FORWARD", "FORWARDS", "FWD", "SPOT", "NDF", "OUTRIGHT"}:
        return "FORWARD"
    if words & {"CURRENCY", "CASH"}:
        return "CURRENCY"
    if "SWAP" in words:
        return "INTEREST_RATE_SWAP"
    return None


def _row_kind(row: pd.Series) -> Optional[str]:
    return _kind_of(row.get("Fin Type")) or _kind_of(row.get("Product"))


# --------------------------------------------------------------------------- table reading
def _header_key(v) -> str:
    return re.sub(r"[^a-z0-9]", "", _s(v).casefold())


def _looks_like_header(cells) -> bool:
    keys = {_header_key(c) for c in cells}
    return "symbol" in keys and bool(keys & set(HEADER_MARKERS) - {"symbol"})


def canonicalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Re-case any column matching a reference header name (ignoring case, spaces and
    punctuation) to the reference casing; leave anything else untouched."""
    rename = {}
    for col in frame.columns:
        canonical = _CANONICAL_BY_KEY.get(_header_key(col))
        if canonical is not None and canonical != col:
            rename[col] = canonical
    frame = frame.rename(columns=rename) if rename else frame
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def _frame_from_rows(rows: List[list]) -> Optional[pd.DataFrame]:
    for i, cells in enumerate(rows[:50]):
        if _looks_like_header(cells):
            header = [_s(c) for c in cells]
            body = [list(r) + [""] * (len(header) - len(r)) for r in rows[i + 1:]]
            body = [r[:len(header)] for r in body]
            frame = pd.DataFrame(body, columns=header, dtype=str).fillna("")
            frame = frame.loc[:, [c for c in frame.columns if c != ""]]
            return frame[~(frame == "").all(axis=1)].reset_index(drop=True)
    return None


def _read_csv_text(text: str) -> Optional[pd.DataFrame]:
    lines = text.splitlines()
    for i, line in enumerate(lines[:50]):
        for sep in (",", ";", "\t", "|"):
            cells = next(csv.reader([line], delimiter=sep))
            if len(cells) > 1 and _looks_like_header(cells):
                frame = pd.read_csv(StringIO("\n".join(lines[i:])), sep=sep, dtype=str,
                                    keep_default_na=False, engine="python")
                frame = frame.loc[:, [c for c in frame.columns if not str(c).startswith("Unnamed")]]
                return frame
    return None


def read_table(source: Union[str, Path, bytes], filename: Optional[str] = None) -> pd.DataFrame:
    """Blotter file (path or raw bytes) -> DataFrame of strings with canonical column
    names. Raises ValueError only when no sheet/section of the file has a header row
    containing a Symbol column plus one of Trade Id / Fin Type / Product."""
    if isinstance(source, (str, Path)):
        filename = filename or Path(source).name
        payload = Path(source).read_bytes()
    else:
        payload = source
    suffix = Path(filename or "").suffix.lower()
    frame = None
    if suffix in (".xlsx", ".xlsm", ".xls") or payload[:2] == b"PK":
        with pd.ExcelFile(BytesIO(payload)) as book:
            for name in book.sheet_names:
                raw = pd.read_excel(book, sheet_name=name, header=None, dtype=str)
                frame = _frame_from_rows(raw.fillna("").values.tolist())
                if frame is not None:
                    break
    else:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = payload.decode("cp1252", errors="replace")
        frame = _read_csv_text(text)
    if frame is None:
        raise ValueError("This file is not a trade blotter: no header row with a Symbol column "
                         "plus Trade Id / Fin Type / Product was found.")
    return canonicalize_columns(frame)


def _dedupe_versions(df: pd.DataFrame) -> tuple:
    """Keep one row per Trade Id: the highest Version, else the last occurrence."""
    if "Trade Id" not in df.columns:
        return df, 0
    ids = df["Trade Id"].map(_s)
    version = df["Version"].map(_num).fillna(-1.0) if "Version" in df.columns else pd.Series(0.0, index=df.index)
    keyed = pd.DataFrame({"id": ids, "v": version, "i": range(len(df))}, index=df.index)
    keyed = keyed[keyed["id"] != ""]
    winners = keyed.sort_values(["v", "i"]).drop_duplicates("id", keep="last").index
    drop = keyed.index.difference(winners)
    return df.drop(index=drop), len(drop)


# --------------------------------------------------------------------------- parse
def parse(source: Union[str, Path, bytes, pd.DataFrame], filename: Optional[str] = None,
          direction_overrides: Optional[Dict[str, str]] = None) -> ParseResult:
    """Pure parse of a blotter (path, bytes or an already-read DataFrame). Never writes;
    never coerces a contradictory row. ``direction_overrides`` ({trade_id: 'PAY' |
    'RECEIVE'}, from ``irs_direction.get_overrides``) is the user's own swap direction,
    which wins over anything the file says."""
    df = source if isinstance(source, pd.DataFrame) else read_table(source, filename)
    df = canonicalize_columns(df).reset_index(drop=True)
    res = ParseResult(direction_overrides=dict(direction_overrides or {}))
    df, res.n_superseded = _dedupe_versions(df)
    global _DAY_FIRST
    previous, _DAY_FIRST = _DAY_FIRST, detect_day_first(df)
    res.day_first = _DAY_FIRST
    try:
        _parse_rows(df, res)
    finally:
        _DAY_FIRST = previous
    _enforce_numeric(res)
    res.options_missing_strike = sorted(
        k for k, o in res.instrument_options.items() if o.strike == 0 and o.payoff in STRIKE_PAYOFFS)
    return res


# Every numeric field that reaches a REAL column, per record type. `_enforce_numeric`
# checks each one, so "no text and no NaN can reach a numeric column" holds by
# construction and does not rest on every parse path being read correctly.
_NUMERIC_FIELDS = (("quantity", "price"), ("amount", "rate"), ("multiplier",), ("strike", "barrier_level"))


def _is_number(x) -> bool:
    return isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(x)


def _enforce_numeric(res: "ParseResult") -> None:
    """Last gate before anything can be written: drop (as a named, single-row reject)
    any trade whose own numbers, legs, instrument or option terms hold anything but a
    finite number. Python's sqlite3 binds NaN as NULL, which fails NOT NULL and used to
    take the whole upload down with it, and SQLite stores non-numeric text in a REAL
    column as text, on which the pricing layer later dies in float()."""
    trade_fields, leg_fields, instrument_fields, option_fields = _NUMERIC_FIELDS
    bad_instruments = {}
    for iid, inst in res.instruments.items():
        for f in instrument_fields:
            if not _is_number(getattr(inst, f)):
                bad_instruments[iid] = f"instruments.{f} = {getattr(inst, f)!r}"
    for iid, opt in res.instrument_options.items():
        for f in option_fields:
            if not _is_number(getattr(opt, f)):
                bad_instruments[iid] = f"instrument_options.{f} = {getattr(opt, f)!r}"
    bad_trades: Dict[str, str] = {}
    for t in res.trades:
        for f in trade_fields:
            if not _is_number(getattr(t, f)):
                bad_trades[t.trade_id] = f"trades.{f} = {getattr(t, f)!r}"
        if t.instrument_id in bad_instruments:
            bad_trades.setdefault(t.trade_id, bad_instruments[t.instrument_id])
    for leg in res.legs:
        for f in leg_fields:
            if not _is_number(getattr(leg, f)):
                bad_trades.setdefault(leg.trade_id, f"trade_legs.{f} (leg {leg.leg_no}) = {getattr(leg, f)!r}")
    if not bad_trades and not bad_instruments:
        return
    for t in res.trades:
        if t.trade_id in bad_trades:
            res.rejects.append(Reject(res.trade_rows.get(t.trade_id, 0), t.instrument_id,
                                      f"not a finite number: {bad_trades[t.trade_id]}; row not loaded"))
            res.irs_directions.pop(t.trade_id, None)
    res.trades = [t for t in res.trades if t.trade_id not in bad_trades]
    res.legs = [l for l in res.legs if l.trade_id not in bad_trades]
    for iid in bad_instruments:
        res.instruments.pop(iid, None)
        res.instrument_options.pop(iid, None)


def _parse_rows(df: pd.DataFrame, res: "ParseResult") -> None:
    for idx, row in df.iterrows():
        row_no = int(idx) + 2  # header is line 1
        n_trades_before = len(res.trades)
        _parse_row(res, row, row_no)
        for t in res.trades[n_trades_before:]:
            res.trade_rows[t.trade_id] = row_no


def _parse_row(res: "ParseResult", row: pd.Series, row_no: int) -> None:
    if _status_excluded(row.get("Status")) or _fund_excluded(row.get("Fund")):
        res.n_skipped_status_or_fund += 1
        return
    kind = _row_kind(row)
    if kind == "FORWARD":
        res.n_forward += 1
        _parse_forward(res, row, row_no)
    elif kind == "CURRENCY":
        res.n_currency += 1
        _parse_currency(res, row, row_no)
    elif kind == "FUTURE":
        res.n_future += 1
        _parse_future(res, row, row_no)
    elif kind == "OPTION":
        res.n_option += 1
        _parse_option(res, row, row_no)
    elif kind == "INTEREST_RATE_SWAP":
        res.n_irs += 1
        n_rejects_before = len(res.rejects)
        _parse_irs(res, row, row_no)
        if len(res.rejects) > n_rejects_before:
            res.n_skipped_irs += 1
    else:
        res.n_skipped_other += 1
        res.skipped_other_rows.append((row_no, _s(row.get("Symbol")),
                                       f"type not loaded by the app: Fin Type {_s(row.get('Fin Type'))!r}, "
                                       f"Product {_s(row.get('Product'))!r}"))


def _common(row: pd.Series) -> dict:
    return dict(account=_s(row.get("ExtAccount")), counterparty=_s(row.get("Counterparty")),
                strategy="", trader=_s(row.get("Trader")), description=_s(row.get("Description")))


def _warn(res: ParseResult, row_no: int, symbol: str, message: str) -> None:
    res.warnings.append(ParseWarning(row_no, symbol, message))
    log.warning("row %d %s: %s", row_no, symbol, message)


def _fx_amounts(row: pd.Series, buy_is_base: bool, rate: float, rate_from_description: bool = False) -> tuple:
    """Base amount, quote amount (both unsigned) and rate of a FORWARD / spot CURRENCY
    row -> ``(base_amt, quote_amt, rate, repairs, problem)``.

    Each of the three is read from its own column and, when that cell is blank or is not
    a number (Excel turning 7.24 into 24-Jul is the known way a number becomes a date),
    rebuilt from the others -- never coerced, never 0:
      base amount   <- BuyCurrency/SellCurrency Amount, else |Quantity| (the base amount
                       on all 828 FX rows of the reference sample), else quote / rate
      quote amount  <- the other Amount column, else base x rate, else |NetInvoice| (the
                       quote amount on all 828 reference rows)
      rate          <- the Description's '@ rate', else Price, else quote / base
    ``repairs`` are the warnings for the row: one for EVERY fallback used, whether the
    cell was blank or was not a number, naming the column and what it was rebuilt from
    (P&L scales with these numbers, so a silent rebuild is never acceptable). What is NOT
    a fallback is the parser's normal path for the product: a forward's rate read from a
    Description that parses (how every reference forward loads) and each amount read from
    its own column. Then, when the row has all three from independent cells, the
    CONSISTENCY check: |base x rate - quote| above FX_CONSISTENCY_TOLERANCE of the quote
    amount (and above FX_CONSISTENCY_FLOOR, the cent / whole-unit rounding of a tiny
    ticket) warns and never rejects. That is what catches a Price cell holding an Excel
    date SERIAL such as 46227.0, which is a perfectly good float and so passes `_num`.
    ``problem`` is the reject reason (naming column and cell) when something cannot be
    rebuilt, else None."""
    base_col, quote_col = (("BuyCurrency Amount", "SellCurrency Amount") if buy_is_base
                           else ("SellCurrency Amount", "BuyCurrency Amount"))
    base_amt, quote_amt = abs(_num(row.get(base_col))), abs(_num(row.get(quote_col)))
    rate_ok = not math.isnan(rate) and rate != 0
    rebuilt: Dict[str, str] = {}
    derived = False            # one of the three was computed from the other two: nothing left to cross-check
    if math.isnan(base_amt):
        qty = abs(_num(row.get("Quantity")))
        if not math.isnan(qty):
            base_amt, rebuilt[base_col] = qty, "Quantity"
        elif rate_ok and not math.isnan(quote_amt):
            base_amt, rebuilt[base_col], derived = quote_amt / rate, f"{quote_col} / rate", True
    if math.isnan(quote_amt):
        net = abs(_num(row.get("NetInvoice")))
        if rate_ok and not math.isnan(base_amt):
            quote_amt, rebuilt[quote_col], derived = base_amt * rate, "base amount x rate", True
        elif not math.isnan(net):
            quote_amt, rebuilt[quote_col] = net, "NetInvoice"
    if not rate_ok and not math.isnan(base_amt) and not math.isnan(quote_amt) and base_amt != 0:
        rate, rebuilt["Price"], derived = quote_amt / base_amt, f"{quote_col} / {base_col}", True
        rate_ok = rate != 0

    missing = [name for name, v in ((base_col, base_amt), (quote_col, quote_amt)) if math.isnan(v)]
    if not rate_ok:
        missing.append("rate")
    if missing:
        bad = [_not_a_number(c, t) for c in ("Price", base_col, quote_col, "Quantity", "NetInvoice")
               if (t := _bad_cell(row, c)) is not None]
        if bad:
            return base_amt, quote_amt, rate, [], ("; ".join(bad) + "; cannot be rebuilt from the other columns "
                                                   f"(still missing: {', '.join(missing)})")
        if missing == ["rate"]:
            return base_amt, quote_amt, rate, [], "no rate and zero base amount"
        return base_amt, quote_amt, rate, [], ("blank BuyCurrency/SellCurrency Amount and no Quantity x Price "
                                               "to derive them")
    values = {"Price": rate, base_col: base_amt, quote_col: quote_amt}
    repairs = [f"{_unusable(row, c)}; rebuilt {values[c]:.10g} from {how}" for c, how in rebuilt.items()]
    price_cell = _num(row.get("Price"))
    if rate_from_description:
        # Normal path, not a fallback -- but a Price cell that is there and is wrong is
        # still doubtful content the user should hear about.
        bad_price = _bad_cell(row, "Price")
        if bad_price is not None:
            repairs.append(f"{_not_a_number('Price', bad_price)}; rebuilt {rate:.10g} from the Description")
        elif not math.isnan(price_cell) and abs(price_cell - rate) > FX_CONSISTENCY_TOLERANCE * abs(rate):
            repairs.append(f"Price {price_cell:.10g} differs from the Description's rate {rate:.10g} by "
                           f"{abs(price_cell - rate) / abs(rate):.2%} (tolerance {FX_CONSISTENCY_TOLERANCE:.1%}); "
                           "the Description's rate is used")
    if not derived and quote_amt > 0:
        expected = base_amt * rate
        gap = abs(expected - quote_amt)
        if gap > max(FX_CONSISTENCY_TOLERANCE * quote_amt, FX_CONSISTENCY_FLOOR):
            said = (f"{base_col} x rate = {base_amt:,.2f} x {rate:.10g} = {expected:,.2f} against {quote_col} "
                    f"{quote_amt:,.2f}: {gap / quote_amt:.2%} apart (tolerance {FX_CONSISTENCY_TOLERANCE:.1%})")
            # Five cells describe one fill: two amounts, Quantity (= base), NetInvoice
            # (= quote) and Price. When the rate came from the Price cell and BOTH amounts
            # are confirmed by their second cell, Price is provably the odd one out (a date
            # serial, typically) and the fill is rebuilt from the amounts. Anything short
            # of that double confirmation is warned about and kept exactly as read.
            qty, net = abs(_num(row.get("Quantity"))), abs(_num(row.get("NetInvoice")))
            confirmed = (not rate_from_description and base_col not in rebuilt and quote_col not in rebuilt
                         and not math.isnan(qty) and not math.isnan(net) and base_amt != 0
                         and abs(qty - base_amt) <= max(FX_CONSISTENCY_TOLERANCE * base_amt, FX_CONSISTENCY_FLOOR)
                         and abs(net - quote_amt) <= max(FX_CONSISTENCY_TOLERANCE * quote_amt, FX_CONSISTENCY_FLOOR))
            if confirmed:
                rate = quote_amt / base_amt
                repairs.append(f"{said}. Quantity and NetInvoice both confirm the amounts, so the Price cell is the "
                               f"odd one out (an Excel date serial looks like this): fill rebuilt {rate:.10g} "
                               f"from {quote_col} / {base_col}")
            else:
                repairs.append(f"{said}; the cells are kept as read, check them")
    return base_amt, quote_amt, rate, repairs, None


def _parse_forward(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol"))
    desc = _s(row.get("Description"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return

    sm = FORWARD_SYMBOL_RE.match(symbol)
    dm = DESCRIPTION_RE.match(desc)
    pair = sm.group(1) if sm else _pair_of(row.get("Currency Pair"), row.get("Underlying Symbol"))

    buy_ccy = _ccy(row.get("Buy Currency"))
    sell_ccy = _ccy(row.get("Sell Currency"))
    rate = math.nan
    trade_date = value_date = None
    if dm:
        td_us, vd_us, verb1, ccy1, verb2, ccy2, rate_s = dm.groups()
        if verb1 == verb2:
            res.rejects.append(Reject(row_no, symbol, f"description verbs are both {verb1}: {desc!r}"))
            return
        d_sold, d_bought = (ccy1, ccy2) if verb1 == "SELL" else (ccy2, ccy1)
        if buy_ccy and sell_ccy and {buy_ccy, sell_ccy} != {d_sold, d_bought}:
            res.rejects.append(Reject(row_no, symbol,
                                      f"Buy/Sell Currency columns {buy_ccy}/{sell_ccy} disagree with "
                                      f"description currencies {d_bought}/{d_sold}"))
            return
        buy_ccy, sell_ccy = buy_ccy or d_bought, sell_ccy or d_sold
        rate = float(rate_s)
        trade_date, value_date = _us_date(td_us), _us_date(vd_us)
        if sm and datetime.strptime(vd_us, "%m/%d/%Y").strftime("%m%d%y") != sm.group(2):
            res.rejects.append(Reject(row_no, symbol,
                                      f"value date {sm.group(2)} in Symbol disagrees with description VD {vd_us}"))
            return
    if not (buy_ccy and sell_ccy):
        res.rejects.append(Reject(row_no, symbol, "cannot tell the two currencies: Buy/Sell Currency "
                                                   "blank and Description not in 'TD .. VD .. SELL x VS .BUY y' form"))
        return
    if buy_ccy == sell_ccy:
        res.rejects.append(Reject(row_no, symbol, f"Buy and Sell Currency are both {buy_ccy}"))
        return
    if math.isnan(rate):
        rate = _num(row.get("Price"))
    trade_date = trade_date or _date(row.get("TradeDate"))
    value_date = value_date or _date(row.get("Settle Date")) or (_mmddyy(sm.group(2)) if sm else None)
    if trade_date is None or value_date is None:
        res.rejects.append(Reject(row_no, symbol, "no trade date / value date in Description, TradeDate or Settle Date"))
        return
    if pair is None:
        pair = _pair_by_convention(buy_ccy, sell_ccy)
        log.warning("row %d %s: pair not given; assuming %s by market convention", row_no, trade_id, pair)
    base_ccy, quote_ccy = pair[:3], pair[3:]
    if {buy_ccy, sell_ccy} != {base_ccy, quote_ccy}:
        res.rejects.append(Reject(row_no, symbol,
                                  f"Buy/Sell Currency columns {buy_ccy}/{sell_ccy} disagree with pair {pair}"))
        return

    buy_is_base = buy_ccy == base_ccy
    base_amt, quote_amt, rate, repairs, problem = _fx_amounts(row, buy_is_base, rate, rate_from_description=bool(dm))
    if problem:
        res.rejects.append(Reject(row_no, symbol, problem))
        return
    for message in repairs:
        _warn(res, row_no, symbol, message)
    base_amount, quote_amount = (base_amt, -quote_amt) if buy_is_base else (-base_amt, quote_amt)

    is_ndf = 1 if (quote_ccy in NDF_CCYS or base_ccy in NDF_CCYS) else 0
    res.instruments.setdefault(pair, Instrument(
        instrument_id=pair, asset_class="FX", base_ccy=base_ccy, quote_ccy=quote_ccy,
        multiplier=1.0, is_ndf=is_ndf, bbg_ticker=f"{pair} Curncy", expiry_date=PERPETUAL,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=pair, product="FX_FWD", package_id=trade_id,
        trade_date=trade_date, quantity=base_amount, price=rate, **_common(row),
    ))
    settles_cash = 0 if is_ndf else 1
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", base_ccy, base_amount, trade_date, value_date, rate, settles_cash))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", quote_ccy, quote_amount, trade_date, value_date, rate, settles_cash))


def _parse_currency(res: ParseResult, row: pd.Series, row_no: int) -> None:
    """CURRENCY row's own currency: Symbol first (verified: matches on every row of the
    reference sample, 85/85). If it's ever blank, ``Currency`` is NOT a safe fallback --
    on the reference sample ``Currency`` disagrees with ``Symbol`` on all 85 rows and is
    provably the OTHER leg's currency of the FX deal this cash movement settles (e.g.
    Symbol=EUR.C-EUAA/Currency=SEK.C-SSAA on a EURSEK trade), never this row's own. The
    correct fallback is Side-aware, matching how the FORWARD parser tells its two legs
    apart: Buy Currency on a Buy row, Sell Currency on a Sell row (both verified equal to
    Symbol whenever Symbol is present); an unrecognised/blank Side leaves it unknown
    rather than guessing."""
    symbol = _s(row.get("Symbol"))
    ccy = _ccy(symbol)
    if ccy is None:
        side = _side(row.get("Side"))
        if side == "Buy":
            ccy = _ccy(row.get("Buy Currency"))
        elif side == "Sell":
            ccy = _ccy(row.get("Sell Currency"))
    if ccy is None:
        res.rejects.append(Reject(row_no, symbol, f"unrecognised currency code {symbol!r}"))
        return
    instrument_id = f"CASH-{ccy}"
    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="CASH", base_ccy=ccy, quote_ccy=ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=f"{ccy} Curncy", expiry_date=PERPETUAL,
    ))
    _parse_spot_from_currency_row(res, row, row_no, symbol)


def _parse_spot_from_currency_row(res: ParseResult, row: pd.Series, row_no: int, symbol: str) -> None:
    """A CURRENCY row that names two currencies is a SPOT FX trade (user's cash-ladder
    spec, 2026-09-18: "Keep rows whose Fin Type is FORWARD or CURRENCY (spot) and whose
    Buy Currency is non-blank" -- every one of the reference sample's 85 CURRENCY rows
    is a T+1/T+2 fill with its own Trade Id, both currencies, both amounts and a price,
    and none matches any FORWARD row's amounts, so they are conversions in their own
    right, not settlements of forwards). It becomes a trade (product FX_SPOT) with the
    same two FX_NEAR legs a forward gets, dated on ``Settle Date``, so its cash joins
    the ladder (a settled ZAR balance stays ZAR until a spot trade in the file converts
    it) and its P&L joins the book. A CURRENCY row with only one currency (a fee, a
    balance, a single-sided movement) stays what it was: the CASH instrument only,
    never a trade, never a reject. Tolerance rule as for forwards (`_fx_amounts`): the
    amounts and the rate rebuild each other (Quantity, NetInvoice, Price), the pair falls
    back to market convention, the trade date to the settle date and vice versa; a blank
    Trade Id, two identical currencies or blank amounts stop the trade being written
    without rejecting the row. The one reject: a populated amount / Price cell that is
    not a number (a date, say) and cannot be rebuilt -- named, that row only."""
    buy_ccy = _ccy(row.get("Buy Currency"))
    sell_ccy = _ccy(row.get("Sell Currency"))
    trade_id = _s(row.get("Trade Id"))
    if not (buy_ccy and sell_ccy and trade_id) or buy_ccy == sell_ccy:
        return
    pair = _pair_of(row.get("Currency Pair"), row.get("Underlying Symbol"))
    if pair is None or {pair[:3], pair[3:]} != {buy_ccy, sell_ccy}:
        pair = _pair_by_convention(buy_ccy, sell_ccy)
    base_ccy, quote_ccy = pair[:3], pair[3:]
    buy_is_base = buy_ccy == base_ccy
    base_amt, quote_amt, rate, repairs, problem = _fx_amounts(row, buy_is_base, _num(row.get("Price")))
    if problem:
        # Blank amounts: a cash movement with nothing to book, as before (instrument
        # only, not a reject). A populated cell that is not a number and cannot be
        # rebuilt is different: a real fill would silently leave the book, so that one
        # row is rejected by name.
        if "is not a number" in problem:
            res.rejects.append(Reject(row_no, symbol, problem))
        else:
            log.warning("row %d %s: CURRENCY row names %s/%s but has no amounts; cash instrument only",
                        row_no, trade_id, buy_ccy, sell_ccy)
        return
    trade_date = _date(row.get("TradeDate"))
    value_date = _date(row.get("Settle Date"))
    trade_date, value_date = trade_date or value_date, value_date or trade_date
    if trade_date is None:
        log.warning("row %d %s: CURRENCY row names %s/%s but has no date; cash instrument only",
                    row_no, trade_id, buy_ccy, sell_ccy)
        return
    for message in repairs:
        _warn(res, row_no, symbol, message)
    base_amount, quote_amount = (base_amt, -quote_amt) if buy_is_base else (-base_amt, quote_amt)
    is_ndf = 1 if (quote_ccy in NDF_CCYS or base_ccy in NDF_CCYS) else 0
    res.instruments.setdefault(pair, Instrument(
        instrument_id=pair, asset_class="FX", base_ccy=base_ccy, quote_ccy=quote_ccy,
        multiplier=1.0, is_ndf=is_ndf, bbg_ticker=f"{pair} Curncy", expiry_date=PERPETUAL,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=pair, product="FX_SPOT", package_id=trade_id,
        trade_date=trade_date, quantity=base_amount, price=rate, **_common(row),
    ))
    settles_cash = 0 if is_ndf else 1
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", base_ccy, base_amount, trade_date, value_date, rate, settles_cash))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", quote_ccy, quote_amount, trade_date, value_date, rate, settles_cash))
    res.n_spot += 1


def _signed_quantity(row: pd.Series, symbol: str, row_no: int, res: ParseResult, label: str,
                     rebuilt: float = math.nan, rebuilt_from: str = "") -> Optional[float]:
    """``Quantity`` signed by ``Side``. When the cell is blank or is not a number the
    caller's ``rebuilt`` magnitude (from other columns, NaN if there is none) is used
    instead, ALWAYS with a warning naming the column and the source -- blank or not: the
    position's size, and so its P&L and delta, now rests on another cell. When nothing
    can rebuild it the row is rejected, naming the cell."""
    qty = _num(row.get("Quantity"))
    if math.isnan(qty):
        bad = _bad_cell(row, "Quantity")
        if math.isnan(rebuilt) or rebuilt == 0:
            reason = (f"blank Quantity ({label})" if bad is None else
                      f"{_not_a_number('Quantity', bad)}; cannot be rebuilt from the other columns ({label})")
            res.rejects.append(Reject(row_no, symbol, reason))
            return None
        qty = abs(rebuilt)
        _warn(res, row_no, symbol, f"{_unusable(row, 'Quantity')}; rebuilt {qty:.10g} {label} from {rebuilt_from}")
    side = _side(row.get("Side"))
    if side is None:
        if qty < 0:
            return qty
        res.rejects.append(Reject(row_no, symbol, f"unrecognised Side {_s(row.get('Side'))!r} and Quantity carries no sign"))
        return None
    return abs(qty) if side == "Buy" else -abs(qty)


def _parse_future(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol")) or _s(row.get("Underlying Symbol"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    trade_date = _date(row.get("TradeDate")) or _date(row.get("Settle Date"))
    if trade_date is None:
        res.rejects.append(Reject(row_no, symbol, f"unparseable TradeDate {_s(row.get('TradeDate'))!r}"))
        return
    try:
        root, code, expiry = future_expiry(symbol, date.fromisoformat(trade_date))
    except ValueError as e:
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    instrument_id = f"{code} Index"
    multiplier = FUTURE_MULTIPLIERS[root]  # root already validated by future_expiry
    price = _num(row.get("Price"))
    net = abs(_num(row.get("NetInvoice")))
    # Contracts, when the Quantity cell is unusable: Notional / multiplier (the export's
    # Notional is contracts x multiplier on all 11 reference FUTURE rows), else
    # NetInvoice / (multiplier x Price), which is whole contracts to within the fees.
    rebuilt, rebuilt_from = math.nan, ""
    if math.isnan(_num(row.get("Quantity"))):
        lots = abs(_num(row.get("Notional"))) / multiplier
        if not math.isnan(lots) and round(lots) >= 1 and abs(lots - round(lots)) < 1e-6:
            rebuilt, rebuilt_from = float(round(lots)), f"Notional / {multiplier:g}"
        elif not math.isnan(net) and not math.isnan(price) and price > 0:
            lots = net / (multiplier * price)
            if round(lots) >= 1 and abs(lots - round(lots)) <= 0.01:
                rebuilt, rebuilt_from = float(round(lots)), f"NetInvoice / ({multiplier:g} x Price)"
    signed_contracts = _signed_quantity(row, symbol, row_no, res, "contracts", rebuilt, rebuilt_from)
    if signed_contracts is None:
        return
    if math.isnan(price):
        # Fill price from the invoice: NetInvoice = contracts x multiplier x price, plus
        # the fees on a buy, less them on a sell (exact on 10 of the 11 reference rows,
        # and within the Price column's own 2-decimal rounding on the 11th).
        bad = _bad_cell(row, "Price")
        fees = _num(row.get("Total Fees"))
        fee_note = ""
        if math.isnan(fees):
            fees, fee_note = 0.0, " (Total Fees not given: the rebuilt price still includes any fees)"
        if not math.isnan(net) and net > 0 and signed_contracts != 0:
            sign = 1.0 if signed_contracts > 0 else -1.0
            price = (net - sign * fees) / (abs(signed_contracts) * multiplier)
        if math.isnan(price) or price <= 0:
            res.rejects.append(Reject(row_no, symbol, "blank Price" if bad is None else
                                      f"{_not_a_number('Price', bad)}; cannot be rebuilt (needs NetInvoice and Quantity)"))
            return
        _warn(res, row_no, symbol,
              (f"{_not_a_number('Price', bad)}" if bad is not None else "Price is blank")
              + f"; rebuilt {price:.10g} from NetInvoice / (contracts x {multiplier:g}){fee_note}")
    elif not math.isnan(net) and net > 0 and not math.isnan(_num(row.get("Quantity"))):
        # Everything is there: does the invoice agree with contracts x multiplier x price?
        # (Worst reference row: 5e-7, the Price column's 2-decimal rounding.) A Price cell
        # holding an Excel date serial is a fine float and only this catches it. Warns,
        # never rejects, and the cells are kept as read: two sources cannot say which is wrong.
        fees = _num(row.get("Total Fees"))
        fees = 0.0 if math.isnan(fees) else fees
        expected = abs(signed_contracts) * multiplier * price + (fees if signed_contracts > 0 else -fees)
        if abs(expected - net) / net > NET_INVOICE_TOLERANCE:
            _warn(res, row_no, symbol,
                  f"NetInvoice {net:,.2f} differs from contracts x {multiplier:g} x Price = {expected:,.2f} by "
                  f"{abs(expected - net) / net:.2%} (tolerance {NET_INVOICE_TOLERANCE:.1%}); the cells are kept "
                  "as read, check the Price")
    expiry_iso = expiry.isoformat()

    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="FUTURE", base_ccy=root, quote_ccy="USD",
        multiplier=multiplier, is_ndf=0, bbg_ticker=instrument_id, expiry_date=expiry_iso,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=instrument_id, product="FUTURE",
        package_id=trade_id, trade_date=trade_date, quantity=signed_contracts, price=price, **_common(row),
    ))
    res.legs.append(TradeLeg(
        trade_id, 1, "NOTIONAL", "USD", signed_contracts * multiplier * price,
        trade_date, expiry_iso, price, 0))


def _parse_option(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol"))
    desc = _s(row.get("Description"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    om = OPTION_SYMBOL_RE.match(symbol)
    col_pair = _pair_of(row.get("Currency Pair"), row.get("Underlying Symbol"))
    desc_expiry, desc_type = _option_terms_from_text(desc, row.get("FxOption Type"))
    if om:
        pair, exp_s, cp, _sym_id = om.groups()
        if col_pair and col_pair != pair:
            res.rejects.append(Reject(row_no, symbol,
                                      f"Currency Pair {_s(row.get('Currency Pair'))!r} disagrees with Symbol pair {pair}"))
            return
        expiry = _mmddyy(exp_s)
        if expiry is None:
            res.rejects.append(Reject(row_no, symbol, f"unparseable expiry {exp_s!r} in Symbol"))
            return
        option_type = "CALL" if cp == "C" else "PUT"
        # Symbol is the primary source, but a populated Description is a real second
        # source of the same facts (verified on the reference sample's 8 OPTION rows,
        # always in agreement) -- cross-checked rather than blindly trusting the Symbol.
        if desc_expiry and desc_expiry != expiry:
            res.rejects.append(Reject(row_no, symbol,
                                      f"Symbol expiry {expiry} disagrees with Description date {desc_expiry}: {desc!r}"))
            return
        if desc_type and desc_type != option_type:
            res.rejects.append(Reject(row_no, symbol,
                                      f"Symbol call/put {option_type} disagrees with Description {desc_type}: {desc!r}"))
            return
    else:
        pair = col_pair
        if pair is None:
            res.rejects.append(Reject(row_no, symbol, f"cannot tell the pair: Symbol {symbol!r} and Currency Pair blank"))
            return
        expiry = _date(row.get("Adj. Expiry Date")) or _date(row.get("Termination")) or desc_expiry
        option_type = desc_type
        if expiry is None:
            res.rejects.append(Reject(row_no, symbol, "Symbol not <PAIR><mmddyy>[CP]-<id> and no expiry "
                                                       "in Adj. Expiry Date, Termination or Description"))
            return
        if option_type is None:
            # A touch or another structure with no call / put in the file (2026-09-21: a ZAR
            # option was missing from the book): loaded with the type unknown, typed in the
            # Options table like a missing strike, never rejected for it.
            option_type = ""
            _warn(res, row_no, symbol, "no call / put in Symbol, Description or FxOption Type; loaded with the "
                                       "type unknown: pick it in the Options table")
        symbol = symbol or f"{pair}{datetime.fromisoformat(expiry).strftime('%m%d%y')}{(option_type or 'X')[0]}-{trade_id}"
    strike_m = STRIKE_RE.search(desc)
    desc_strike = float(strike_m.group(1)) if strike_m else 0.0
    # A structured strike column wins when the export carries one (2026-09-18: three
    # options in the reference file have no "<n> STRIKE" in their Description and the
    # export has no strike column at all, so a re-export with one is the way to get
    # them priced without typing). Both populated and different = contradiction.
    col_strike = 0.0
    for col in STRIKE_COLUMNS:
        v = _num(row.get(col))
        if not math.isnan(v) and v > 0:
            col_strike = v
            break
        bad_strike = _bad_cell(row, col)
        if bad_strike is not None:   # never stored as text, never coerced: left unknown
            _warn(res, row_no, symbol, f"{_not_a_number(col, bad_strike)}; ignored"
                  + ("" if desc_strike else ": the strike stays unknown until it is entered in the app"))
    if col_strike and desc_strike and abs(col_strike - desc_strike) > 1e-9 * max(col_strike, desc_strike):
        res.rejects.append(Reject(row_no, symbol, f"strike column {col_strike} disagrees with Description strike {desc_strike}"))
        return
    strike = col_strike or desc_strike  # 0.0 sentinel when neither is present, never invented
    base_ccy = pair[:3]
    trade_date = _date(row.get("TradeDate")) or _date(row.get("Settle Date"))
    if trade_date is None:
        res.rejects.append(Reject(row_no, symbol, f"unparseable TradeDate {_s(row.get('TradeDate'))!r}"))
        return
    # Premium fill and notional. NetInvoice is |Quantity x Price| on the reference
    # sample (35,000,000 x 0.00579 = 202,650.00) but its SIGN is unreliable: -142,500 on
    # Trade Id 934168029 whose Side is Buy, positive on the one Sell row. Direction
    # therefore comes from Side alone, and NetInvoice is used for two things only:
    # (1) rebuilding a Price or Quantity cell that is blank or is not a number, and
    # (2) a magnitude cross-check that warns above 0.5 % and never rejects.
    # The premium is a FRACTION of the notional (0.00579), so a percent sign in these two
    # cells means "divide by 100" ('0.58%' = 0.0058) and is warned about (PERCENT_FRACTION).
    premium, premium_col = _num(row.get("Price"), PERCENT_FRACTION), "Price"
    if math.isnan(premium):
        premium, premium_col = _num(row.get("Premium"), PERCENT_FRACTION), "Premium"
        if not math.isnan(premium):   # a fallback, and an unverified one: the reference file's Premium is blank
            _warn(res, row_no, symbol, f"{_unusable(row, 'Price', PERCENT_FRACTION)}; premium {premium:.10g} "
                                       "read from the Premium column instead")
    if not math.isnan(premium) and "%" in _s(row.get(premium_col)):
        _warn(res, row_no, symbol, f"{premium_col} {_s(row.get(premium_col))!r} carries a percent sign; read as "
                                   f"{premium:.10g} of the notional (divided by 100)")
    net = abs(_num(row.get("NetInvoice")))
    net_ok = not math.isnan(net) and net > 0
    quantity_cell = _num(row.get("Quantity"))
    rebuilt, rebuilt_from = math.nan, ""
    if math.isnan(quantity_cell) and net_ok and not math.isnan(premium) and premium > 0:
        rebuilt, rebuilt_from = net / premium, "|NetInvoice| / Price (NetInvoice may include fees: check it)"
    signed_notional = _signed_quantity(row, symbol, row_no, res, "notional", rebuilt, rebuilt_from)
    if signed_notional is None:
        return
    if math.isnan(premium):
        bad = [(c, t) for c in ("Price", "Premium") if (t := _bad_cell(row, c, PERCENT_FRACTION)) is not None]
        if net_ok and not math.isnan(quantity_cell) and quantity_cell != 0:
            premium = net / abs(quantity_cell)
            _warn(res, row_no, symbol,
                  ("; ".join(_not_a_number(c, t) for c, t in bad) if bad else "Price is blank")
                  + f"; rebuilt {premium:.10g} from |NetInvoice| / |Quantity| (NetInvoice may include fees: check it)")
        else:
            res.rejects.append(Reject(row_no, symbol, "blank Price/Premium" if not bad else
                                      "; ".join(_not_a_number(c, t) for c, t in bad)
                                      + "; cannot be rebuilt (needs NetInvoice and Quantity)"))
            return
    elif net_ok and not math.isnan(quantity_cell):
        expected = abs(quantity_cell * premium)
        if expected > 0 and abs(net - expected) / expected > NET_INVOICE_TOLERANCE:
            _warn(res, row_no, symbol,
                  f"NetInvoice {net:,.2f} differs from |Quantity x Price| {expected:,.2f} by "
                  f"{abs(net - expected) / expected:.2%} (above {NET_INVOICE_TOLERANCE:.1%}); the Price fill is kept")
    if premium > 1.0:
        # More than the whole notional: not a premium any desk pays. An Excel date serial
        # (46227.0) in the Price cell is a finite float and looks exactly like this.
        _warn(res, row_no, symbol, f"premium {premium:.10g} is above 100 % of the notional; kept as read, "
                                   "check the Price cell (an Excel date serial looks like this)")

    res.instruments.setdefault(symbol, Instrument(
        instrument_id=symbol, asset_class="FX_OPTION", base_ccy=base_ccy, quote_ccy=pair[3:],
        multiplier=1.0, is_ndf=0, bbg_ticker=symbol, expiry_date=expiry,
    ))
    res.instrument_options.setdefault(symbol, InstrumentOption(
        instrument_id=symbol, strike=strike, option_type=option_type,
        payoff=detect_payoff(desc, row.get("FxOption Type"), row.get("Notes")),
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=symbol, product="FX_OPTION",
        package_id=trade_id, trade_date=trade_date, quantity=signed_notional, price=premium, **_common(row),
    ))
    res.legs.append(TradeLeg(
        trade_id, 1, "NOTIONAL", base_ccy, signed_notional, trade_date, expiry, premium, 0))


# ------------------------------------------------------------------- IRS direction signals
_DIRECTION_WORDS = {"PAY": "pay fixed", "RECEIVE": "receive fixed"}
# Size columns on which brackets or a minus sign mark a short = receive fixed (user,
# 2026-09-18: "if the book has brackets or a negative sign that's a short"). NetInvoice
# and 'Gross Amnt/Principal' are deliberately NOT among them: on a swap both are CASH
# amounts (an upfront payment), so their sign says which way cash moved, not which way
# the swap faces -- a payer with a negative upfront would load as RECEIVE, tagged as
# decided by the file, and never be flagged for the user's choice.
IRS_SIGN_COLUMNS = ("Notional", "Quantity", "Current Face", "Original Face")
# Free-text columns searched for pay / receive wording.
IRS_TEXT_COLUMNS = ("Description", "Notes", "Swap Type", "RollSide", "Tran Type")
_PAY_WORDS = frozenset(("PAY", "PAYS", "PAYER", "PAYING"))
_RECEIVE_WORDS = frozenset(("REC", "RCV", "RECV", "RECEIVE", "RECEIVES", "RECEIVER", "RECEIVING"))
# 'Pay Float' / 'Rec SOFR' describes the floating leg: the fixed leg faces the other way.
_FLOAT_LEG_WORDS = frozenset(("FLOAT", "FLOATING", "FLT", "FLTG", "VARIABLE", "OIS", "SOFR", "ESTR", "SONIA",
                              "TONA", "TONAR", "SARON", "CORRA", "AONIA", "LIBOR", "EURIBOR"))
# 'Pay date', 'Pay freq', 'Rec leg DCF': schedule wording, not a direction.
_NOT_A_DIRECTION_NEXT = frozenset(("DATE", "DATES", "FREQ", "FREQUENCY", "LEG", "LEGS", "DAY", "DAYS", "LAG",
                                   "DELAY", "CALENDAR", "DCF", "BASIS", "CONVENTION", "SCHEDULE"))


def _direction_in_text(text: str) -> set:
    """{'PAY'}, {'RECEIVE'}, both (the text contradicts itself) or empty, from whole
    words only: REC / RCV / RECEIVE / RECEIVER / 'Rec Fixed' = receive fixed; PAY /
    PAYER / 'Pay Fixed' = pay fixed; the same word followed by a floating-leg word
    ('Pay Float', 'Rec SOFR') names the other leg, so the fixed leg is the opposite."""
    tokens = re.sub(r"[^A-Z0-9]+", " ", text.upper()).split()
    found = set()
    for i, token in enumerate(tokens):
        if token not in _PAY_WORDS and token not in _RECEIVE_WORDS:
            continue
        following = tokens[i + 1] if i + 1 < len(tokens) else ""
        if following in _NOT_A_DIRECTION_NEXT:
            continue
        pays = token in _PAY_WORDS
        if following in _FLOAT_LEG_WORDS:
            pays = not pays
        found.add("PAY" if pays else "RECEIVE")
    return found


def _irs_direction_signals(row: pd.Series) -> tuple:
    """Every EXPLICIT pay/receive signal a swap row carries -> ``(signals, ambiguous)``,
    ``signals`` a list of (direction, 'Column 'cell'' it came from).

      - brackets or a minus sign on any of IRS_SIGN_COLUMNS  -> RECEIVE (a short)
      - ``Side`` in the sell-synonym set (`_side`)             -> RECEIVE
      - pay / receive wording in any of IRS_TEXT_COLUMNS       -> PAY or RECEIVE

    What is deliberately NOT a signal: ``Side`` = Buy and an unsigned amount. Both are
    on every one of the 10 reference swap rows, the three the desk holds as receivers
    included, so they say nothing about direction; only an explicit sell / short /
    receive marker counts. ``ambiguous`` lists text cells naming both directions at
    once, which are reported and ignored rather than allowed to decide anything."""
    signals: List[tuple] = []
    ambiguous: List[str] = []
    for col in IRS_SIGN_COLUMNS:
        v = _num(row.get(col))
        if not math.isnan(v) and v < 0:
            signals.append(("RECEIVE", f"{col} {_s(row.get(col))!r} (brackets / minus = short)"))
    if _side(row.get("Side")) == "Sell":
        signals.append(("RECEIVE", f"Side {_s(row.get('Side'))!r}"))
    for col in IRS_TEXT_COLUMNS:
        text = _s(row.get(col))
        found = _direction_in_text(text) if text else set()
        if len(found) == 1:
            signals.append((next(iter(found)), f"{col} {text!r}"))
        elif len(found) > 1:
            ambiguous.append(f"{col} {text!r}")
    return signals, ambiguous


def _parse_irs(res: ParseResult, row: pd.Series, row_no: int) -> None:
    """Magnitude from ``Notional`` (else ``Quantity`` x 1e6); direction from the user's
    stored override, else the row's explicit signals (`_irs_direction_signals`), else
    pay fixed -- recorded on ``res.irs_directions`` either way. Two explicit signals that
    contradict each other reject the row. Legs: FIXED = -quantity, FLOAT = +quantity."""
    symbol = _s(row.get("Symbol"))
    desc = _s(row.get("Description"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    sm = IRS_SYMBOL_RE.match(symbol)
    dm = IRS_DESCRIPTION_RE.match(desc)
    ccy = (sm.group(1) if sm else None) or (dm.group(5) if dm else None) or _ccy(row.get("Currency"))
    if ccy is None:
        res.rejects.append(Reject(row_no, symbol, "cannot tell the swap currency from Symbol, Description or Currency"))
        return
    effective_date = _date(row.get("Effective Date")) or (_us_date(dm.group(2)) if dm else None)
    maturity_date = _date(row.get("Termination")) or (_us_date(dm.group(3)) if dm else None)
    if effective_date is None or maturity_date is None:
        res.rejects.append(Reject(row_no, symbol, "no Effective Date / Termination (nor in Description)"))
        return
    if maturity_date <= effective_date:
        res.rejects.append(Reject(row_no, symbol, f"maturity {maturity_date} is not after effective date {effective_date}"))
        return
    notional = _num(row.get("Notional"))
    qty_mm = _num(row.get("Quantity"))
    if not math.isnan(notional) and not math.isnan(qty_mm) and notional != 0:
        # Both cells are there (Quantity is the notional in millions on all 10 reference
        # rows, exactly): a gap means one of them is mangled. Warned, kept as read.
        if abs(abs(qty_mm) * 1e6 - abs(notional)) / abs(notional) > FX_CONSISTENCY_TOLERANCE:
            _warn(res, row_no, symbol, f"Notional {abs(notional):,.0f} differs from Quantity x 1,000,000 = "
                                       f"{abs(qty_mm) * 1e6:,.0f}; the Notional is kept as read, check both cells")
    if math.isnan(notional):
        notional = qty_mm * 1e6 if not math.isnan(qty_mm) else math.nan  # Quantity is in millions
        if math.isnan(notional):
            bad_cells = [_not_a_number(c, t) for c in ("Notional", "Quantity") if (t := _bad_cell(row, c)) is not None]
            res.rejects.append(Reject(row_no, symbol, "blank Notional (and no Quantity)" if not bad_cells else
                                      "; ".join(bad_cells) + "; the notional cannot be rebuilt"))
            return
        # A fallback, so always said (blank or not): the swap's size now rests on the
        # millions-scaled Quantity column.
        _warn(res, row_no, symbol, f"{_unusable(row, 'Notional')}; rebuilt {abs(notional):,.0f} from Quantity x 1,000,000")
    if notional == 0.0:
        res.rejects.append(Reject(row_no, symbol, "Notional is zero; cannot infer pay/receive direction"))
        return
    # FixedRate / Yield are already in percent units (3.98 = 3.98 %), so '3.98%' is the
    # same number: the sign is accepted and nothing is scaled (PERCENT_KEEP).
    fixed_rate_pct, rate_from = _num(row.get("FixedRate"), PERCENT_KEEP), "FixedRate"
    if math.isnan(fixed_rate_pct):
        fixed_rate_pct, rate_from = _num(row.get("Yield"), PERCENT_KEEP), "Yield"
    if math.isnan(fixed_rate_pct) and dm:
        fixed_rate_pct, rate_from = float(dm.group(4)), "the Description"
    bad_rate = _bad_cell(row, "FixedRate", PERCENT_KEEP)
    if math.isnan(fixed_rate_pct):
        res.rejects.append(Reject(row_no, symbol, "blank FixedRate (and no Yield / rate in Description)" if bad_rate is None else
                                  f"{_not_a_number('FixedRate', bad_rate)}; no Yield / rate in Description to rebuild it from"))
        return
    if rate_from != "FixedRate":
        _warn(res, row_no, symbol, f"{_unusable(row, 'FixedRate', PERCENT_KEEP)}; rebuilt {fixed_rate_pct:.10g} from {rate_from}")
    # Signed, full units: + = pay fixed, - = receive fixed; the magnitude comes from
    # ``Notional``. The user's stored override decides when there is one; otherwise every
    # explicit signal in the row (`_irs_direction_signals`); otherwise pay fixed, recorded
    # as defaulted so the Rates table can ask. Never inferred from anything else.
    signals, ambiguous = _irs_direction_signals(row)
    for cell in ambiguous:
        _warn(res, row_no, symbol, f"{cell} names both pay and receive; not used as a direction signal")
    said = {d for d, _ in signals}
    override = res.direction_overrides.get(trade_id)
    if override in ("PAY", "RECEIVE"):
        direction = override
        file_view = "; ".join(f"{why} says {_DIRECTION_WORDS[d]}" for d, why in signals)
        decided = IrsDirection(trade_id, direction, f"user override ({_DIRECTION_WORDS[direction]})"
                               + (f"; the file: {file_view}" if file_view else ""))
    elif len(said) > 1:
        res.rejects.append(Reject(row_no, symbol, "direction signals contradict: "
                                  + "; ".join(f"{why} says {_DIRECTION_WORDS[d]}" for d, why in signals)
                                  + ". The row is not loaded until the file agrees with itself"))
        return
    elif said:
        direction = said.pop()
        decided = IrsDirection(trade_id, direction, "; ".join(why for _, why in signals))
    else:
        direction = "PAY"
        decided = IrsDirection(trade_id, direction, NO_DIRECTION_SIGNAL, defaulted=True)
    res.irs_directions[trade_id] = decided
    quantity = abs(notional) if direction == "PAY" else -abs(notional)
    fixed_rate = fixed_rate_pct / 100.0
    trade_date = _date(row.get("TradeDate")) or effective_date
    instrument_id = symbol or f"IRS-{ccy}-{trade_id}"

    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="IRS", base_ccy=ccy, quote_ccy=ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=instrument_id, expiry_date=maturity_date,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=instrument_id, product="IRS", package_id=trade_id,
        trade_date=trade_date, quantity=quantity, price=fixed_rate, **_common(row),
    ))
    res.legs.append(TradeLeg(trade_id, 1, "FIXED", ccy, -quantity, effective_date, maturity_date, fixed_rate, 0))
    res.legs.append(TradeLeg(trade_id, 2, "FLOAT", ccy, quantity, effective_date, maturity_date, 0.0, 0))


# --------------------------------------------------------------------------- load
def _rows(objs) -> List[tuple]:
    return [tuple(vars(o).values()) for o in objs]


def load(source: Union[str, Path, bytes, pd.DataFrame], conn: sqlite3.Connection,
         strict: bool = False, filename: Optional[str] = None, turn_swap_marks: bool = True) -> ParseResult:
    """Parse and upsert. Re-loading a trade id replaces its trade and legs; a swap
    package containing a replaced trade is dissolved so the packaging rule can re-run on
    the new data. With ``strict=True`` any reject raises ValueError before anything is
    written; otherwise rejected rows are skipped and the rest is loaded.

    Swap direction: the user's stored overrides (``data/ingest/irs_direction.py``) are
    handed to the parser, so an overridden swap is written the way the user set it
    whatever the file says, and the overrides are re-applied once more at the end as the
    guarantee (``irs_direction.reapply_after_load``); ``res.n_direction_overrides`` is
    how many swaps on file carry one. A swap that was already on file and comes back
    facing the other way (the file changed its mind, no override) has its priced history
    turned round with it, by sign reversal, never deleted (``irs_direction.reverse_flipped``).
    ``turn_swap_marks=False`` leaves that last step to the caller: the app's upload loads
    into a staging copy and publishes it by upsert, so it reverses ONCE on the live
    database itself (``upload._stage_and_publish``) -- reversing here as well would carry
    the reversed values across and the live step would then put the old signs back.

    Numbers: everything written to a REAL column has passed ``_enforce_numeric`` (finite
    numbers only), so neither text nor NaN can reach ``trades.quantity`` / ``price``,
    ``trade_legs.amount`` / ``rate``, ``instruments.multiplier`` or the option strike."""
    from data.ingest import irs_direction
    from data.ingest.schema import create_schema

    create_schema(conn)
    signs_before = irs_direction.irs_signs(conn)
    res = parse(source, filename, direction_overrides=irs_direction.get_overrides(conn))
    name = filename or (Path(source).name if isinstance(source, (str, Path)) else "blotter")
    for rj in res.rejects:
        log.warning("%s row %d %s: REJECT %s", name, rj.row_no, rj.symbol, rj.reason)
    if strict and res.rejects:
        head = [f"reject row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in res.rejects[:5]]
        raise ValueError(f"{name}: {len(res.rejects)} reject(s); nothing loaded (strict=True). First: " + " | ".join(head))

    trade_cols = list(vars(res.trades[0]).keys()) if res.trades else []
    with conn:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _incoming (trade_id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _incoming")
        conn.executemany("INSERT OR IGNORE INTO _incoming VALUES (?)", [(t.trade_id,) for t in res.trades])
        res.n_updated = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_id IN (SELECT trade_id FROM _incoming)").fetchone()[0]
        conn.execute(
            "DELETE FROM swap_review WHERE trade_id IN (SELECT trade_id FROM _incoming) OR trade_id IN ("
            "SELECT trade_id FROM trades WHERE package_id IN ("
            "SELECT package_id FROM trades WHERE trade_id IN (SELECT trade_id FROM _incoming) AND package_id != trade_id))")
        conn.execute(
            "UPDATE trades SET product = 'FX_FWD', package_id = trade_id WHERE product = 'FX_SWAP' AND package_id IN ("
            "SELECT package_id FROM trades WHERE trade_id IN (SELECT trade_id FROM _incoming) AND package_id != trade_id)")
        conn.execute("DELETE FROM trade_legs WHERE trade_id IN (SELECT trade_id FROM _incoming)")
        # Column lists are explicit (2026-09-18): a database created by a transient
        # schema variant carries extra `instruments` columns (strike/option_type/...,
        # before `instrument_options` existed), and a positional VALUES list of 8 then
        # failed the whole upload with "table instruments has 12 columns but 8 values
        # were supplied" -- an import error over nothing the file did wrong.
        conn.executemany(
            "INSERT OR REPLACE INTO instruments (instrument_id, asset_class, base_ccy, quote_ccy, multiplier, "
            "is_ndf, bbg_ticker, expiry_date) VALUES (?,?,?,?,?,?,?,?)",
            _rows(res.instruments.values()))
        if res.trades:
            updates = ",".join(f"{c}=excluded.{c}" for c in trade_cols if c != "trade_id")
            conn.executemany(
                f"INSERT INTO trades ({','.join(trade_cols)}) VALUES ({','.join('?' for _ in trade_cols)}) "
                f"ON CONFLICT(trade_id) DO UPDATE SET {updates}", _rows(res.trades))
        conn.executemany(
            "INSERT INTO trade_legs (trade_id, leg_no, leg_type, ccy, amount, start_date, settle_date, rate, "
            "settles_cash) VALUES (?,?,?,?,?,?,?,?,?)", _rows(res.legs))
        # Option terms: the blotter only ever knows strike (when its Description carries
        # one), call/put and a payoff keyword. Terms typed in the app for options the
        # export leaves incomplete (digitals with no strike, barrier levels) must survive
        # a re-upload, so a field is only overwritten by a populated blotter value.
        conn.executemany(
            "INSERT INTO instrument_options (instrument_id, strike, option_type, barrier_level, avg_start_date, payoff) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(instrument_id) DO UPDATE SET "
            "strike = CASE WHEN excluded.strike != 0 THEN excluded.strike ELSE strike END, "
            "option_type = CASE WHEN excluded.option_type != '' THEN excluded.option_type ELSE option_type END, "
            "payoff = CASE WHEN excluded.payoff != 'VANILLA' THEN excluded.payoff ELSE payoff END",
            _rows(res.instrument_options.values()))
        conn.execute("DROP TABLE _incoming")
    # Overrides first (trades and legs only), then ONE decision per swap on its priced
    # history: direction before this load against direction after it.
    res.n_direction_overrides = irs_direction.reapply_after_load(conn)
    if turn_swap_marks:
        irs_direction.reverse_flipped(conn, signs_before)
    for note in res.notes():
        log.info("%s: %s", name, note)
    return res


# --------------------------------------------------------------------------- diagnostics
# Columns the pricing layer reads with float(): (table, key columns, numeric columns).
NUMERIC_COLUMNS = (
    ("trades", ("trade_id",), ("quantity", "price")),
    ("trade_legs", ("trade_id", "leg_no"), ("amount", "rate")),
    ("instruments", ("instrument_id",), ("multiplier",)),
    ("instrument_options", ("instrument_id",), ("strike", "barrier_level")),
    ("marks", ("as_of_date", "instrument_id", "settle_date", "mark_type", "source"), ("value",)),
    ("realised_pnl", ("trade_id",), ("local_amount", "usd_entry_amount", "spot_usd_per_local", "pnl_usd")),
)


def non_numeric_cells(conn: sqlite3.Connection, limit_per_column: int = 200) -> List[dict]:
    """Rows ALREADY in a database that hold something other than a number in a numeric
    column -- SQLite keeps text that is not a number as text even in a REAL column, and
    the pricing layer then dies on it with "could not convert string to float". One dict
    per offending cell: ``table``, ``key`` ('trade_id=934530555, leg_no=1'), ``column``,
    ``value`` (the stored text, None for a NULL) and ``stored_as`` (SQLite's typeof:
    'text' | 'blob' | 'null'). Report only: nothing is changed or deleted. Read-only
    safe; tables and columns an older database lacks are skipped."""
    out: List[dict] = []
    for table, keys, columns in NUMERIC_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have or not set(keys) <= have:
            continue
        key_sql = ", ".join(f'"{k}"' for k in keys)
        for column in columns:
            if column not in have:
                continue
            rows = conn.execute(
                f'SELECT {key_sql}, "{column}", typeof("{column}") FROM {table} '
                f'WHERE typeof("{column}") NOT IN (\'real\', \'integer\') ORDER BY {key_sql} LIMIT ?',
                (int(limit_per_column),)).fetchall()
            for r in rows:
                out.append({"table": table, "key": ", ".join(f"{k}={v}" for k, v in zip(keys, r)),
                            "column": column, "value": r[len(keys)], "stored_as": r[len(keys) + 1]})
    return out
