"""Trade blotter CSV/Excel -> instruments, trades, trade_legs.

Source: the transaction-level blotter export (``data/raw/new_sample_trades.csv``-shaped
files), the app's only trade source. Shared dataclasses/regexes live in
``data/ingest/common.py`` (extracted 2026-09-17 when ``data/ingest/bnp.py`` and
``data/ingest/irs.py`` -- the retired BNP CSV parser -- were deleted).

Row kind is decided by ``Fin Type`` (``Product`` is the fallback when Fin Type is blank
or unrecognised): FORWARD, CURRENCY, FUTURE, OPTION -- matched by keyword after
normalisation, so 'Futures', 'FX Forward', 'fx option' all resolve. A label with 'swap'
is FORWARD with an FX word (FX, currency, forward, foreign exchange), because an FX
swap's rows are forward fills with their own value dates; with a rates word (interest,
rate, IRS, OIS) it is an interest rate swap, which is no longer loaded; 'Swap' alone is
counted and skipped, never coerced (reviewer finding 2026-09-22).

Products that left the app with the commodity conversion (Phase 2, user 2026-09-24):
interest rate swaps, the equity index futures (ES, NQ, RTY, YM) and the listed index
options ('SPX/E261016P7615-USAA'). A row of one of them is counted under
``n_skipped_other`` (and ``n_skipped_retired``) and named on ``skipped_other_rows`` with
a plain reason: never a reject, never coerced into another product (hard rule 6). NDFs
left too: every FX pair is written deliverable (``is_ndf`` 0, legs ``settles_cash`` 1).

Rows whose Status says cancelled/rejected/pending/void are filtered out and
counted; so are rows that ``config/book.yaml`` says are not the book's (its ``funds``,
``traders`` and ``desks`` lists, read by ``load_book_filter``: a non-empty list keeps only
rows whose populated cell is in it, case-insensitive; an empty list takes every value;
until 2026-09-24 this was a hard-coded Fund = NMMF). A missing Status / Fund / Trader /
Desk column (or a blank cell) never excludes a row.

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
  - FUTURE: a trade + 1 NOTIONAL leg (``Quantity`` = contracts signed by ``Side``). Every
    future (2026-09-24) is resolved through the contract master
    (``data.contracts.resolve_future``, from ``Symbol`` / ``Underlying Symbol`` narrowed by
    the ``Currency``, ``Execution Venue`` and ``Description`` cells): instrument id = the
    canonical contract id ('CLZ26 Comdty'), base_ccy = the root id ('NYMEX:CL'), quote_ccy
    / multiplier from ``config/contracts.csv``, the NOTIONAL leg in the contract's own
    currency. A symbol the universe does not know, or that fits more than one root,
    rejects that row naming the candidates: a multiplier is never guessed.
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
  - OPTION on a commodity future (Phase 5, 2026-09-24): product CMDTY_OPTION, resolved
    through ``data.contracts.resolve_option`` (``_is_commodity_option`` tells it from an FX
    option; ``_parse_cmdty_option``). Instrument = the canonical option id ('CLZ26C 70
    Comdty'), terms in ``instrument_options``, lots signed by Side, 1 NOTIONAL leg in the
    contract's currency. The underlying future's instrument row is written too, with no
    trade (``ParseResult.underlying_only``; ``load`` never overwrites a row on file with it).
    An unknown or ambiguous root rejects, naming it.
  - LME forwards (Phase 5, 2026-09-24): a FUTURE or FORWARD row on the LME (venue, Bloomberg's
    LME ticker, or a symbol resolving to one of ``engine.lme.lme_roots()``) becomes product
    LME_FWD on the metal's root id ('LME:CA'): tonnes signed, two FX_NEAR legs (metal, USD) on
    the prompt date (``_parse_lme_forward``). The LME ferrous contracts stay FUTUREs.

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
and fees, contracts from NetInvoice / (multiplier x Price)) with a ``ParseWarning``, and
only when nothing can
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
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

from data.ingest.common import (
    CASH_CCY_RE,
    DESCRIPTION_RE,
    FORWARD_SYMBOL_RE,
    Instrument,
    InstrumentOption,
    ParseWarning,
    PERPETUAL,
    Reject,
    Trade,
    TradeLeg,
)
from data.contracts import option_request_ticker, request_ticker, resolve_future, resolve_option
from engine import lme as _lme

log = logging.getLogger(__name__)

SOURCE = "XLSX"  # closest value in CLAUDE.md's trades.source enum ('BNP | XLSX | MANUAL')
# Which rows are the book's: config/book.yaml (see load_book_filter). With no such file the
# filter is the one the app had before it existed: Fund NMMF, any trader, any desk.
BOOK_YAML = Path(__file__).resolve().parents[2] / "config" / "book.yaml"
DEFAULT_FUNDS = ("NMMF",)
IN_SCOPE_TYPES = ("FORWARD", "CURRENCY", "FUTURE", "OPTION")
# Products that left the app with the commodity conversion (Phase 2, user 2026-09-24). A row
# of one of them is counted and skipped with a plain reason, never rejected and never coerced
# into another product (hard rule 6). The roots are recognised only to say so: nothing is
# booked from them, and a multiplier is never read from this list.
RETIRED_REASON_IRS = "interest rate swap: rates left the app on 2026-09-24 (commodity conversion); not loaded"
RETIRED_INDEX_FUTURE_ROOTS = frozenset({"ES", "NQ", "RTY", "YM"})
# listed index options, and options on the equity index futures (Phase 5, 2026-09-24: still skipped)
RETIRED_INDEX_OPTION_ROOTS = frozenset({"SPX", "SPXW", "NDX", "RUT", "SX5E"}) | RETIRED_INDEX_FUTURE_ROOTS
# 'ESU6-USAA' (the PB's equity index future form); 'SPX/E261016P7615-USAA' (a listed option:
# underlying / European-or-American, expiry yymmdd, put-call, strike)
_RETIRED_FUTURE_SYMBOL_RE = re.compile(r"^([A-Z]+)[FGHJKMNQUVXZ]\d-[A-Z]{4}$")
_LISTED_OPTION_SYMBOL_RE = re.compile(r"^([A-Z0-9]{1,6})\s*/\s*[EA]?\d{6}(?:[CP]\d+(?:\.\d+)?)?(?:-.*)?$")
# Options on commodity futures (CMDTY_OPTION, Phase 5, 2026-09-24): the shapes that say a row is
# one (contract-master's resolve_option reads them), before the FX option path is tried.
#   Bloomberg 'CLZ6C 70 Comdty' / 'C Z6P 450'; the Chinese exchanges' own codes 'CU2612C80000',
#   'I2701-C-800', 'SR611C5000' (a guess: no Chinese option row has been seen); an exchange prefix.
_BBG_OPTION_SHAPE_RE = re.compile(r"^([A-Z0-9]{1,6}?) ?[FGHJKMNQUVXZ]\d{1,2}[CP] -?\d+(?:\.\d+)?(?: [A-Z]+)?$")
_CN_OPTION_RE = re.compile(r"^(?P<fut>[A-Z]{1,2}\d{3,4})-?(?P<cp>[CP])-?(?P<strike>\d+(?:\.\d+)?)$")
_EXCHANGE_PREFIX_RE = re.compile(r"^(?P<exch>[A-Z][A-Z0-9_]*)\s*:\s*(?P<rest>.+)$")
# '75 STRIKE' / '3,500.00 STRIKE' / 'STRIKE 75' in a commodity option's Description
_CMDTY_STRIKE_RE = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s*STRIKE\b|\bSTRIKE\s*[:=]?\s*(\d[\d,]*(?:\.\d+)?)")
# LME forwards (LME_FWD, Phase 5): the venue, Bloomberg's LME cash / 3-month tickers ('LMCADY',
# 'LMCADS03'), a 3-month ticket's marks, and the date columns a prompt date may sit in.
_LME_VENUE_RE = re.compile(r"\bLME\b|LONDON METAL")
_LME_BBG_RE = re.compile(r"^LM([A-Z]{2})(DY|DS\d{2})?\b")
_THREE_MONTH_RE = re.compile(r"\b3\s*-?\s*M(?:ONTHS?|THS?)?\b|\bTHREE[\s-]MONTHS?\b|DS03\b")
LME_PROMPT_COLUMNS = ("Prompt Date", "Prompt", "Maturity", "Maturity Date")
_TEXT_DATE_RES = (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
                  re.compile(r"\b(\d{1,2}[-\s](?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*[-\s]\d{2,4})\b", re.I),
                  re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"))
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
_CANONICAL_BY_KEY = {re.sub(r"[^a-z0-9]", "", c.casefold()): c for c in REFERENCE_HEADER + list(LME_PROMPT_COLUMNS)}
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
# Set by parse() for the duration of one file: the connection whose stored Bloomberg contract
# dates (data.contracts' contract_static) replace the estimated expiry, or None.
_CONTRACT_CONN: Optional[sqlite3.Connection] = None
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


@dataclass(frozen=True)
class BookFilter:
    """Which rows of the export are the book's (``config/book.yaml``). Each list keeps only
    rows whose populated cell is in it, compared case-insensitively; an empty list takes every
    value; a blank cell or a missing column never excludes (hard rule 6). ``source`` names the
    file it was read from, '' for the built-in default (no file)."""
    funds: Tuple[str, ...] = DEFAULT_FUNDS
    traders: Tuple[str, ...] = ()
    desks: Tuple[str, ...] = ()
    source: str = ""

    # (filter name, list attribute, blotter column), in the order a row is tested
    FILTERS = (("fund", "funds", "Fund"), ("trader", "traders", "Trader"), ("desk", "desks", "Desk"))

    def excluded_by(self, row) -> Optional[str]:
        """'fund' / 'trader' / 'desk': the first filter that excludes the row, else None."""
        for name, attr, column in self.FILTERS:
            allowed = getattr(self, attr)
            cell = _s(row.get(column)).casefold()
            if allowed and cell and cell not in {a.casefold() for a in allowed}:
                return name
        return None

    def describe(self) -> str:
        """'funds NMMF; traders any; desks any'."""
        return "; ".join(f"{attr} {', '.join(getattr(self, attr)) or 'any'}" for _, attr, _ in self.FILTERS)


def _as_names(value, key: str, where: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{where}: '{key}' must be a list of names, not {value!r}")
    return tuple(str(v).strip() for v in value if v is not None and str(v).strip())


def load_book_filter(path: Optional[Union[str, Path]] = None) -> BookFilter:
    """``config/book.yaml`` (or ``path``) as a ``BookFilter``. No file: the built-in default
    (Fund NMMF, any trader, any desk), the filter the parser had before the file existed. A
    key left out, or left empty, takes every value. A file that is not valid YAML, or a key
    that is not a list of names, raises ValueError naming the file: a broken config is never
    read as 'take everything'."""
    import yaml

    p = Path(path) if path is not None else BOOK_YAML
    if not p.exists():
        if path is not None:
            raise ValueError(f"book filter file not found: {p}")
        return BookFilter()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{p}: not valid YAML ({e})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected the keys funds / traders / desks, found {type(data).__name__}")
    return BookFilter(funds=_as_names(data.get("funds"), "funds", str(p)),
                      traders=_as_names(data.get("traders"), "traders", str(p)),
                      desks=_as_names(data.get("desks"), "desks", str(p)), source=str(p))


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
    n_option: int = 0       # FX option rows
    n_cmdty_option: int = 0     # option rows on a commodity future (CMDTY_OPTION), Phase 5
    n_lme_forward: int = 0      # FUTURE / FORWARD rows that are LME prompt-date forwards (LME_FWD), Phase 5
    # plain sentences: an LME ticket whose prompt date the file does not give and that took the
    # 3-month date or its month's third Wednesday (information, not a warning)
    lme_prompt_notes: List[str] = field(default_factory=list)
    # instruments written only as the underlying future of an option on a future (no trade of
    # their own in the file): load() inserts them without ever overwriting a row already on file
    underlying_only: set = field(default_factory=set)
    n_skipped_other: int = 0
    # the rows behind n_skipped_other, named: (row_no, symbol, reason) -- a row of a type the
    # app does not load must never vanish without a trace (user, 2026-09-21)
    skipped_other_rows: list = field(default_factory=list)
    # of n_skipped_other: rows of a product that left the app on 2026-09-24 (interest rate
    # swaps, equity index futures, listed index options), each named on skipped_other_rows
    n_skipped_retired: int = 0
    n_skipped_status_or_fund: int = 0   # every excluded row: the four counts below summed
    n_excluded_status: int = 0          # Status cancelled / rejected / pending / ...
    n_excluded_fund: int = 0            # config/book.yaml's funds (a row counts under its first filter)
    n_excluded_trader: int = 0          # config/book.yaml's traders
    n_excluded_desk: int = 0            # config/book.yaml's desks
    # rows that passed the status and book filters, by their Trader cell ('' = blank)
    kept_by_trader: Dict[str, int] = field(default_factory=dict)
    book_filter: Optional[BookFilter] = None   # the filter this parse applied
    n_superseded: int = 0   # earlier versions of a Trade Id repeated within the file
    n_updated: int = 0      # set by load(): trades that already existed and were replaced
    # Rows that loaded but needed a repair or failed a cross-check (a Price that arrived
    # as a date and was rebuilt from the amounts, an option NetInvoice that disagrees
    # with Quantity x Price, ...). Never rejects.
    warnings: List[ParseWarning] = field(default_factory=list)
    # Option instruments the file gives no strike for (stored as the schema's 0 = "not
    # known" sentinel, never a real strike of 0), so the UI can ask for them by name.
    options_missing_strike: List[str] = field(default_factory=list)
    trade_rows: Dict[str, int] = field(default_factory=dict)   # trade_id -> file row number

    def information_notes(self) -> List[str]:
        """Things the app also shows persistently elsewhere (the Blotter's missing-terms
        banner), so they inform and never count as warnings: options with no strike.
        Counts and names only."""
        out: List[str] = []
        if self.options_missing_strike:
            out.append(f"{len(self.options_missing_strike)} option(s) have no strike in the file: "
                       f"{_some(self.options_missing_strike)}.")
        if self.lme_prompt_notes:
            out.append(f"{len(self.lme_prompt_notes)} LME ticket(s) carry no prompt date in the file: "
                       + "; ".join(self.lme_prompt_notes[:5])
                       + (f"; and {len(self.lme_prompt_notes) - 5} more" if len(self.lme_prompt_notes) > 5 else "")
                       + ".")
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

    def filter_summary(self) -> str:
        """One sentence: the filter applied, the rows it excluded by reason and the rows kept
        per trader. Not part of ``notes()`` (the upload summary already counts the excluded
        rows); for a caller that wants the breakdown."""
        book = self.book_filter or BookFilter()
        where = Path(book.source).name if book.source else "built-in default, no config/book.yaml"
        excluded = ", ".join(f"{n} {what}" for what, n in (
            ("status", self.n_excluded_status), ("fund", self.n_excluded_fund),
            ("trader", self.n_excluded_trader), ("desk", self.n_excluded_desk)) if n) or "none"
        kept = ", ".join(f"{t or '(blank)'} {n}" for t, n in sorted(self.kept_by_trader.items())) or "none"
        return f"Book filter ({where}): {book.describe()}. Rows excluded: {excluded}. Rows kept by trader: {kept}."

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
#   'keep'      a column written in percent units, where '3.98%' is that same 3.98 (the
#               swap FixedRate / Yield used it until rates left the app on 2026-09-24).
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


# The words that decide what a label carrying "swap" is (see _kind_of): a rates word
# makes it an interest rate swap (recognised only to be skipped with its reason since
# 2026-09-24), an FX word a forward fill; neither = not loaded.
SWAP_RATES_WORDS = frozenset({"IRS", "OIS", "INTEREST", "RATE", "RATES"})
SWAP_FX_WORDS = frozenset({"FX", "CURRENCY", "CURRENCIES", "FOREIGN", "EXCHANGE",
                           "FORWARD", "FORWARDS", "FWD"})


def _kind_of(label: str) -> Optional[str]:
    s = re.sub(r"[^A-Z0-9]+", " ", _s(label).upper()).strip()
    if not s:
        return None
    words = set(s.split())
    if words & {"IRS", "OIS", "INTEREST"} or "INTEREST RATE" in s:
        return "INTEREST_RATE_SWAP"
    if words & {"SWAP", "SWAPS"}:
        # 'FX Swap' is two forward fills with their own value dates, each loaded as an
        # outright, never a rate swap; a bare 'Swap' says too little to book at all.
        if words & SWAP_RATES_WORDS:
            return "INTEREST_RATE_SWAP"
        if words & SWAP_FX_WORDS:
            return "FORWARD"
        return None
    if words & {"OPTION", "OPTIONS", "OPT"}:
        return "OPTION"
    if words & {"FUTURE", "FUTURES", "FUT"}:
        return "FUTURE"
    if words & {"FORWARD", "FORWARDS", "FWD", "SPOT", "NDF", "OUTRIGHT"}:
        return "FORWARD"
    if words & {"CURRENCY", "CASH"}:
        return "CURRENCY"
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
def parse(source: Union[str, Path, bytes, pd.DataFrame], filename: Optional[str] = None, *,
          book: Optional[Union[BookFilter, str, Path]] = None,
          conn: Optional[sqlite3.Connection] = None) -> ParseResult:
    """Pure parse of a blotter (path, bytes or an already-read DataFrame). Never writes;
    never coerces a contradictory row. ``book`` is the row filter: a ``BookFilter``, a path
    to a book.yaml, or None for ``config/book.yaml``. ``conn`` (read only) lets a future's
    stored Bloomberg contract dates replace the estimated expiry."""
    df = source if isinstance(source, pd.DataFrame) else read_table(source, filename)
    df = canonicalize_columns(df).reset_index(drop=True)
    res = ParseResult()
    res.book_filter = book if isinstance(book, BookFilter) else load_book_filter(book)
    df, res.n_superseded = _dedupe_versions(df)
    global _DAY_FIRST, _CONTRACT_CONN
    previous, _DAY_FIRST = _DAY_FIRST, detect_day_first(df)
    previous_conn, _CONTRACT_CONN = _CONTRACT_CONN, conn
    res.day_first = _DAY_FIRST
    try:
        _parse_rows(df, res)
    finally:
        _DAY_FIRST = previous
        _CONTRACT_CONN = previous_conn
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
    if _status_excluded(row.get("Status")):
        res.n_excluded_status += 1
        res.n_skipped_status_or_fund += 1
        return
    excluded_by = (res.book_filter or BookFilter()).excluded_by(row)
    if excluded_by is not None:
        attr = f"n_excluded_{excluded_by}"
        setattr(res, attr, getattr(res, attr) + 1)
        res.n_skipped_status_or_fund += 1
        return
    trader = _s(row.get("Trader"))
    res.kept_by_trader[trader] = res.kept_by_trader.get(trader, 0) + 1
    kind = _row_kind(row)
    not_loaded = _not_loaded_reason(kind, row)
    if not_loaded is not None:
        reason, retired = not_loaded
        res.n_skipped_other += 1
        res.n_skipped_retired += int(retired)
        res.skipped_other_rows.append((row_no, _s(row.get("Symbol")), reason))
        return
    lme_match = _lme_match(row) if kind in ("FORWARD", "FUTURE") and not _is_fx_forward_shape(row) else None
    if lme_match is not None:
        res.n_lme_forward += 1
        _parse_lme_forward(res, row, row_no, *lme_match)
    elif kind == "FORWARD":
        res.n_forward += 1
        _parse_forward(res, row, row_no)
    elif kind == "CURRENCY":
        res.n_currency += 1
        _parse_currency(res, row, row_no)
    elif kind == "FUTURE":
        res.n_future += 1
        _parse_future(res, row, row_no)
    elif kind == "OPTION" and _is_commodity_option(row):
        res.n_cmdty_option += 1
        _parse_cmdty_option(res, row, row_no)
    elif kind == "OPTION":
        res.n_option += 1
        _parse_option(res, row, row_no)
    else:
        res.n_skipped_other += 1
        res.skipped_other_rows.append((row_no, _s(row.get("Symbol")),
                                       f"type not loaded by the app: Fin Type {_s(row.get('Fin Type'))!r}, "
                                       f"Product {_s(row.get('Product'))!r}"))


def _not_loaded_reason(kind: Optional[str], row: pd.Series) -> Optional[Tuple[str, bool]]:
    """``(plain reason, retired)`` for a row of a product the parser does not book, or None
    for a row it does. ``retired`` = a product that left the app on 2026-09-24: interest rate
    swaps by their kind, equity index futures by the root of ``Symbol`` / ``Underlying
    Symbol`` ('ESU6-USAA'), listed index options and options on the index futures by their
    Symbol ('SPX/E261016P7615-USAA', 'ESZ6C 6000 Index') or an index Underlying Symbol. A
    listed option on any other underlying is an option on a commodity future since Phase 5
    (``_parse_cmdty_option``). The reason names what the row is, never a fault in it."""
    if kind == "INTEREST_RATE_SWAP":
        return RETIRED_REASON_IRS, True
    if kind == "FUTURE":
        for col in ("Symbol", "Underlying Symbol"):
            m = _RETIRED_FUTURE_SYMBOL_RE.match(_s(row.get(col)).upper())
            if m and m.group(1) in RETIRED_INDEX_FUTURE_ROOTS:
                return (f"equity index future ({m.group(1)}): the equity index left the app on 2026-09-24 "
                        "(commodity conversion); not loaded"), True
    if kind == "OPTION":
        root = _retired_option_root(row)
        if root is not None:
            return (f"listed index option on {root}: the equity index left the app on 2026-09-24 "
                    "(commodity conversion); not loaded"), True
    return None


def _retired_option_root(row: pd.Series) -> Optional[str]:
    """The equity index an option row is on ('SPX', 'ES'), else None: the root of a listed
    'ROOT/[EA]yymmdd..' or Bloomberg 'ESZ6C 6000 Index' Symbol, or an Underlying Symbol that is
    an index future ('ESZ6-USAA') or carries Bloomberg's Index key ('SPX Index')."""
    symbol = _s(row.get("Symbol")).upper()
    for rx in (_LISTED_OPTION_SYMBOL_RE, _BBG_OPTION_SHAPE_RE):
        m = rx.match(symbol)
        if m and m.group(1).strip() in RETIRED_INDEX_OPTION_ROOTS:
            return m.group(1).strip()
    m = _BBG_OPTION_SHAPE_RE.match(symbol)
    if m and symbol.endswith(" INDEX"):
        return m.group(1).strip()
    underlying = _s(row.get("Underlying Symbol")).upper()
    m = _RETIRED_FUTURE_SYMBOL_RE.match(underlying)
    if m and m.group(1) in RETIRED_INDEX_FUTURE_ROOTS:
        return m.group(1)
    first = re.split(r"[^A-Z0-9]+", underlying)[0] if underlying else ""
    if first in RETIRED_INDEX_OPTION_ROOTS and (underlying.endswith(" INDEX") or underlying == first):
        return first
    return None


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

    # NDFs left the app on 2026-09-24: every FX pair is written deliverable, both legs settling.
    res.instruments.setdefault(pair, Instrument(
        instrument_id=pair, asset_class="FX", base_ccy=base_ccy, quote_ccy=quote_ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=f"{pair} Curncy", expiry_date=PERPETUAL,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=pair, product="FX_FWD", package_id=trade_id,
        trade_date=trade_date, quantity=base_amount, price=rate, **_common(row),
    ))
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", base_ccy, base_amount, trade_date, value_date, rate, 1))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", quote_ccy, quote_amount, trade_date, value_date, rate, 1))


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
    res.instruments.setdefault(pair, Instrument(
        instrument_id=pair, asset_class="FX", base_ccy=base_ccy, quote_ccy=quote_ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=f"{pair} Curncy", expiry_date=PERPETUAL,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=pair, product="FX_SPOT", package_id=trade_id,
        trade_date=trade_date, quantity=base_amount, price=rate, **_common(row),
    ))
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", base_ccy, base_amount, trade_date, value_date, rate, 1))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", quote_ccy, quote_amount, trade_date, value_date, rate, 1))
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
    _parse_commodity_future(res, row, row_no, symbol, trade_id, trade_date)


def _parse_commodity_future(res: ParseResult, row: pd.Series, row_no: int, symbol: str, trade_id: str,
                            trade_date: str) -> None:
    """A future, resolved through the contract master (``data.contracts.resolve_future``,
    2026-09-24; the equity index roots are skipped before this, ``_not_loaded_reason``).
    The symbol comes from ``Symbol``, else
    ``Underlying Symbol``; a bare code shared by several roots is narrowed by the row's
    ``Currency`` (read like any currency cell: 'DOL.C-USAA' or 'USD'; blank or unreadable =
    not given), ``Execution Venue`` and an exchange named in ``Description``. A symbol the
    universe does not know, one that fits more than one root, or a populated currency / venue
    that fits no candidate rejects the row with the resolver's own reason (it names the
    candidates): the multiplier and currency are never guessed.

    The instrument is the contract month, keyed by its canonical id ('CLZ26 Comdty', stable
    for the contract's life): base_ccy = the root id ('NYMEX:CL'), quote_ccy = the root's
    currency, multiplier = quote-currency amount per 1.0 of quoted price per contract,
    bbg_ticker = Bloomberg's request form at the trade date ('' for a placeholder root that
    must never be requested), expiry = the last trade date (Bloomberg's when stored, else the
    contract master's conservative estimate). One NOTIONAL leg in the contract's currency,
    contracts x multiplier x fill, settles_cash 0. The fill is kept as quoted."""
    try:
        contract = resolve_future(
            _s(row.get("Symbol")), trade_date=trade_date, underlying=_s(row.get("Underlying Symbol")),
            description=_s(row.get("Description")), currency=_ccy(row.get("Currency")) or "",
            venue=_s(row.get("Execution Venue")), conn=_CONTRACT_CONN)
    except ValueError as e:          # UnknownContract / AmbiguousContract, and any other refusal
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    root = contract.root
    fill = _future_fill(res, row, row_no, symbol, root.multiplier, quote_unit=root.quote_unit)
    if fill is None:
        return
    signed_contracts, price = fill
    instrument_id = contract.contract_id
    expiry_iso = contract.last_trade_date.isoformat()
    ticker = "" if root.bbg_placeholder else request_ticker(contract, date.fromisoformat(trade_date))
    future = Instrument(
        instrument_id=instrument_id, asset_class="FUTURE", base_ccy=root.root_id, quote_ccy=root.currency,
        multiplier=root.multiplier, is_ndf=0, bbg_ticker=ticker, expiry_date=expiry_iso,
    )
    if instrument_id in res.underlying_only:      # first seen as an option's underlying: now traded itself
        res.underlying_only.discard(instrument_id)
        res.instruments[instrument_id] = future
    else:
        res.instruments.setdefault(instrument_id, future)
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=instrument_id, product="FUTURE",
        package_id=trade_id, trade_date=trade_date, quantity=signed_contracts, price=price, **_common(row),
    ))
    res.legs.append(TradeLeg(
        trade_id, 1, "NOTIONAL", root.currency, signed_contracts * root.multiplier * price,
        trade_date, expiry_iso, price, 0))


def _future_fill(res: ParseResult, row: pd.Series, row_no: int, symbol: str, multiplier: float,
                 quote_unit: str = "") -> Optional[Tuple[float, float]]:
    """(signed contracts, fill price) of a futures row, or None when the row was rejected.

    ``Quantity`` signed by ``Side``; an unusable Quantity is rebuilt from ``NetInvoice /
    (multiplier x Price)``, never from ``Notional`` (what a commodity row's Notional holds is
    not known, and a Notional in gallons or bushels divided by a cents-scaled multiplier would
    be a whole number 100 times too big). An unusable Price is rebuilt from NetInvoice and
    fees. With every cell there, NetInvoice (in the contract's currency) is cross-checked
    against contracts x multiplier x Price: a gap above NET_INVOICE_TOLERANCE warns and never
    rejects; one of about 100 times says the fill looks quoted in another unit than
    ``quote_unit``."""
    price = _num(row.get("Price"))
    net = abs(_num(row.get("NetInvoice")))
    rebuilt, rebuilt_from = math.nan, ""
    if math.isnan(_num(row.get("Quantity"))):
        if not math.isnan(net) and not math.isnan(price) and price > 0:
            lots = net / (multiplier * price)
            if round(lots) >= 1 and abs(lots - round(lots)) <= 0.01:
                rebuilt, rebuilt_from = float(round(lots)), f"NetInvoice / ({multiplier:g} x Price)"
    signed_contracts = _signed_quantity(row, symbol, row_no, res, "contracts", rebuilt, rebuilt_from)
    if signed_contracts is None:
        return None
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
            return None
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
            scale = ""
            if expected > 0:
                ratio = net / expected
                if abs(ratio / 100.0 - 1.0) < 0.02 or abs(ratio * 100.0 - 1.0) < 0.02:
                    unit = f" ({quote_unit} after the contract list's price scale)" if quote_unit else ""
                    scale = (f"; the invoice is about {ratio:.4g} times that, so the Price looks quoted in "
                             f"another unit than the contract's{unit}")
            _warn(res, row_no, symbol,
                  f"NetInvoice {net:,.2f} differs from contracts x {multiplier:g} x Price = {expected:,.2f} by "
                  f"{abs(expected - net) / net:.2%} (tolerance {NET_INVOICE_TOLERANCE:.1%}){scale}; the cells are "
                  "kept as read, check the Price")
    return signed_contracts, price


DELIVERY_LAG_DAYS = 7   # expiry + 2 business days, over a long weekend


def _days_between(earlier_iso: str, later_iso: str) -> int:
    return (datetime.fromisoformat(later_iso) - datetime.fromisoformat(earlier_iso)).days


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
        if desc_expiry and desc_expiry != expiry and 0 < _days_between(desc_expiry, expiry) <= DELIVERY_LAG_DAYS:
            # The user's live export, 2026-09-21: USDZAR101326C with 'Call 10/09/2026', USDCHF101926
            # with '10/15/2026', EURSEK092526C with '09/23/2026' -- the Symbol carries the delivery
            # date (expiry + 2 business days, a weekend or holiday in between), the Description the
            # expiry. Both are true; the expiry is the Description's.
            _warn(res, row_no, symbol, f"Symbol date {expiry} is the delivery date; expiry {desc_expiry} "
                                       "taken from the Description")
            expiry = desc_expiry
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
    if premium > 1.0 and base_ccy not in ("XAU", "XAG", "XPT", "XPD"):   # a metal is dealt in USD per ounce
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


# --------------------------------------------------------------------------- options on commodity futures
def _is_commodity_option(row: pd.Series) -> bool:
    """Whether an OPTION row is an option on a commodity future (CMDTY_OPTION) rather than an FX
    option. The FX option's own Symbol ('EURUSD111826C-500041') always goes the FX way; a Symbol
    in a listed / Bloomberg / Chinese option shape, with an exchange prefix or Bloomberg's Comdty
    key goes the commodity way; otherwise a currency pair in ``Currency Pair`` / ``Underlying
    Symbol`` says FX, and any other symbol is handed to the contract master, whose refusal
    (naming the root) is the reject. A row with neither symbol nor pair stays on the FX path,
    which rejects it for want of a pair, as before."""
    symbol = _s(row.get("Symbol")).upper()
    if OPTION_SYMBOL_RE.match(symbol):
        return False
    if (_LISTED_OPTION_SYMBOL_RE.match(symbol) or _BBG_OPTION_SHAPE_RE.match(symbol)
            or _CN_OPTION_RE.match(symbol) or _EXCHANGE_PREFIX_RE.match(symbol) or symbol.endswith(" COMDTY")):
        return True
    if _pair_of(row.get("Currency Pair"), row.get("Underlying Symbol")):
        return False
    return bool(symbol or _s(row.get("Underlying Symbol")))


def _cmdty_option_type(row: pd.Series) -> str:
    """'CALL' / 'PUT' from the ``FxOption Type`` cell or a whole word CALL(S) / PUT(S) in the
    Description, '' when neither says or they say both. Single letters C / P in the Description
    are not read (a counterparty code such as 'CPTY-C' would turn a put into a call)."""
    cell = _s(row.get("FxOption Type")).upper()
    from_cell = {"C": "CALL", "CALL": "CALL", "CALLS": "CALL", "P": "PUT", "PUT": "PUT", "PUTS": "PUT"}.get(cell)
    words = set(re.sub(r"[^A-Z]+", " ", _s(row.get("Description")).upper()).split())
    from_desc = {t for w, t in (("CALL", "CALL"), ("CALLS", "CALL"), ("PUT", "PUT"), ("PUTS", "PUT")) if w in words}
    if from_cell and from_desc and from_desc != {from_cell}:
        return ""          # the two disagree: left to the symbol (and a symbol without a type rejects)
    if from_cell:
        return from_cell
    return next(iter(from_desc)) if len(from_desc) == 1 else ""


def _cmdty_option_strike(row: pd.Series, symbol: str, row_no: int, res: ParseResult) -> Tuple[Optional[float], Optional[str]]:
    """(strike, problem) from a strike column or the Description's '<n> STRIKE'; None when the row
    gives none. Two populated strikes that disagree are the problem (a reject)."""
    col_strike = None
    for col in STRIKE_COLUMNS:
        v = _num(row.get(col))
        if not math.isnan(v) and v > 0:
            col_strike = v
            break
        bad = _bad_cell(row, col)
        if bad is not None:
            _warn(res, row_no, symbol, f"{_not_a_number(col, bad)}; ignored")
    desc_strike = None
    m = _CMDTY_STRIKE_RE.search(_s(row.get("Description")).upper())
    if m:
        v = _num(m.group(1) or m.group(2))
        desc_strike = None if math.isnan(v) or v <= 0 else v
    if col_strike and desc_strike and abs(col_strike - desc_strike) > 1e-9 * max(col_strike, desc_strike):
        return None, f"strike column {col_strike:g} disagrees with Description strike {desc_strike:g}"
    return col_strike or desc_strike, None


def _parse_cmdty_option(res: ParseResult, row: pd.Series, row_no: int) -> None:
    """An option on a commodity future (product CMDTY_OPTION, Phase 5, user decision 2026-09-24),
    resolved through the contract master (``data.contracts.resolve_option``) from ``Symbol`` (else
    ``Underlying Symbol``) with the row's ``Underlying Symbol``, ``Currency``, ``Execution Venue``,
    ``Description``, the option type (``_cmdty_option_type``) and strike (``_cmdty_option_strike``).
    The Chinese exchanges' own code ('CU2612C80000') is split into its future and type / strike
    first. An unknown or ambiguous root, or a type / strike the row contradicts, rejects with the
    resolver's reason: a multiplier is never guessed.

    Instrument = the canonical option id ('CLZ26C 70 Comdty'): base_ccy the root id, quote_ccy
    the contract's currency, multiplier the underlying future's, bbg_ticker Bloomberg's form at
    the trade date ('' for a placeholder root), expiry = the option's last trade date: Bloomberg's
    when stored, else the expiry a dated symbol states when it is earlier than the contract
    master's estimate, else the estimate. instrument_options: strike, CALL / PUT, payoff AMERICAN
    for an American option, VANILLA for a European one. Quantity = lots signed by Side, price =
    the premium as quoted (the future's price scale), one NOTIONAL leg in the quote currency of
    lots x multiplier x premium, settling on the expiry, settles_cash 0. The fill and lots are
    read and cross-checked like a future's (``_future_fill``)."""
    symbol = _s(row.get("Symbol"))
    underlying = _s(row.get("Underlying Symbol"))
    shown = symbol or underlying
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, shown, "blank Trade Id"))
        return
    trade_date = _date(row.get("TradeDate")) or _date(row.get("Settle Date"))
    if trade_date is None:
        res.rejects.append(Reject(row_no, shown, f"unparseable TradeDate {_s(row.get('TradeDate'))!r}"))
        return
    option_type = _cmdty_option_type(row)
    strike, problem = _cmdty_option_strike(row, shown, row_no, res)
    if problem:
        res.rejects.append(Reject(row_no, shown, problem))
        return
    sym_arg, und_arg = (symbol, underlying) if symbol else (underlying, "")
    text = re.sub(r"\s+", " ", sym_arg.upper()).strip()
    prefix = ""
    pm = _EXCHANGE_PREFIX_RE.match(text)
    if pm:
        prefix, text = pm.group("exch") + ":", pm.group("rest").strip()
    cn = _CN_OPTION_RE.match(text)
    if cn:
        cn_type = "CALL" if cn.group("cp") == "C" else "PUT"
        cn_strike = float(cn.group("strike"))
        if option_type and option_type != cn_type:
            res.rejects.append(Reject(row_no, shown, f"option symbol {sym_arg!r} says {cn_type} but the row says {option_type}"))
            return
        if strike and abs(strike - cn_strike) > 1e-9 * max(strike, cn_strike):
            res.rejects.append(Reject(row_no, shown, f"option symbol {sym_arg!r} says strike {cn_strike:g} but the row "
                                                     f"says {strike:g}"))
            return
        sym_arg, option_type, strike = prefix + cn.group("fut"), cn_type, cn_strike
    try:
        option = resolve_option(sym_arg, trade_date=trade_date, underlying=und_arg,
                                description=_s(row.get("Description")), currency=_ccy(row.get("Currency")) or "",
                                venue=_s(row.get("Execution Venue")), option_type=option_type, strike=strike,
                                conn=_CONTRACT_CONN)
    except ValueError as e:          # UnknownContract / AmbiguousContract, and any other refusal
        res.rejects.append(Reject(row_no, shown, str(e)))
        return
    root = option.root
    fill = _future_fill(res, row, row_no, shown, root.multiplier, quote_unit=root.quote_unit)
    if fill is None:
        return
    lots, premium = fill
    expiry = option.last_trade_date
    if option.dates_source != "BLOOMBERG" and option.symbol_expiry is not None and option.symbol_expiry < expiry:
        expiry = option.symbol_expiry     # the file states the option's own expiry; the estimate is its future's
    expiry_iso = expiry.isoformat()
    ticker = "" if root.bbg_placeholder else option_request_ticker(option, date.fromisoformat(trade_date))
    instrument_id = option.contract_id
    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="CMDTY_OPTION", base_ccy=root.root_id, quote_ccy=root.currency,
        multiplier=root.multiplier, is_ndf=0, bbg_ticker=ticker, expiry_date=expiry_iso,
    ))
    res.instrument_options.setdefault(instrument_id, InstrumentOption(
        instrument_id=instrument_id, strike=option.strike, option_type=option.option_type,
        payoff="AMERICAN" if option.style == "AMERICAN" else "VANILLA",
    ))
    # The underlying future's own instrument, with no trade (as a CURRENCY row writes CASH-<ccy>):
    # its official FUTURE_PX, which the option's Greeks need, must hang off an instruments row.
    # Never replaces a future the file trades, nor (load) a row already on file.
    und = option.underlying
    if und.contract_id not in res.instruments:
        res.instruments[und.contract_id] = Instrument(
            instrument_id=und.contract_id, asset_class="FUTURE", base_ccy=root.root_id, quote_ccy=root.currency,
            multiplier=root.multiplier, is_ndf=0,
            bbg_ticker="" if root.bbg_placeholder else request_ticker(und, date.fromisoformat(trade_date)),
            expiry_date=und.last_trade_date.isoformat())
        res.underlying_only.add(und.contract_id)
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=instrument_id, product="CMDTY_OPTION",
        package_id=trade_id, trade_date=trade_date, quantity=lots, price=premium, **_common(row),
    ))
    res.legs.append(TradeLeg(
        trade_id, 1, "NOTIONAL", root.currency, lots * root.multiplier * premium, trade_date, expiry_iso, premium, 0))


# --------------------------------------------------------------------------- LME forwards
def _is_fx_forward_shape(row: pd.Series) -> bool:
    """A FORWARD row in the FX export's own shape ('USDCNH111826-500030' Symbol or the 'TD .. VD
    .. SELL x VS .BUY y' Description): never an LME forward."""
    return bool(FORWARD_SYMBOL_RE.match(_s(row.get("Symbol"))) or DESCRIPTION_RE.match(_s(row.get("Description"))))


def _lme_match(row: pd.Series):
    """``(root_id, contract month or None)`` when a FUTURE / FORWARD row is an LME prompt-date
    forward (LME_FWD, Phase 5), else None. Read, in order, from ``Symbol`` then ``Underlying
    Symbol``: Bloomberg's LME cash / 3-month ticker ('LMCADS03 Comdty'); a futures symbol the
    contract master resolves ('LPZ6 Comdty', 'CAZ6-USAA' with venue LME), which is an LME forward
    only when its root is one of ``engine.lme.lme_roots()`` (the LME ferrous contracts are
    monthly futures and stay FUTUREs, as does any other exchange's contract); and, when the
    ``Execution Venue`` or the Description says LME, a bare code or metal name ('CA', 'LME:CA',
    'Copper'), then a metal named in the Description ('LME COPPER 3M')."""
    roots = _lme.lme_roots()
    venue = _s(row.get("Execution Venue"))
    desc = _s(row.get("Description")).upper()
    hinted = bool(_LME_VENUE_RE.search(venue.upper()) or _LME_VENUE_RE.search(desc))
    trade_date = _date(row.get("TradeDate")) or _date(row.get("Settle Date")) or date.today().isoformat()
    for col in ("Symbol", "Underlying Symbol"):
        s = _s(row.get(col)).upper()
        if not s:
            continue
        m = _LME_BBG_RE.match(s)
        if m and f"LME:{m.group(1)}" in roots:
            return f"LME:{m.group(1)}", None
        try:
            contract = resolve_future(s, trade_date=trade_date, description=desc,
                                      currency=_ccy(row.get("Currency")) or "", venue=venue, conn=_CONTRACT_CONN)
        except ValueError:
            contract = None
        if contract is not None:
            return (contract.root_id, contract) if contract.root_id in roots else None
        if hinted:
            token = re.split(r"[^A-Z0-9:]+", s)[0]
            try:
                return _lme.lme_root(token), None
            except ValueError:
                pass
    if hinted:
        words = [w for w in re.findall(r"[A-Z]+", desc) if w != "LME"]
        phrases = [" ".join(words[i:i + 2]) for i in range(len(words) - 1)]
        for candidate in phrases + [w for w in words if len(w) > 2] + [w for w in words if len(w) == 2]:
            try:
                return _lme.lme_root(candidate), None
            except ValueError:
                continue
    return None


def _date_in_text(text: str) -> Optional[str]:
    for rx in _TEXT_DATE_RES:
        m = rx.search(text)
        if m:
            d = _date(m.group(1))
            if d is not None:
                return d
    return None


def _lme_prompt(res: ParseResult, row: pd.Series, row_no: int, symbol: str, trade_date: str,
                contract) -> Tuple[Optional[str], str]:
    """(prompt date, where it came from) of an LME ticket, or (None, '') when the file gives none
    and nothing implies one. In order: a ``Prompt Date`` / ``Prompt`` / ``Maturity`` / ``Maturity
    Date`` column; a date in the Description; ``Settle Date`` when it is after the trade date;
    a 3-month ticket's (the Symbol or Description says 3M, or Bloomberg's 'DS03') 3-month date;
    a monthly contract's third Wednesday ('LPZ6'); ``Settle Date`` whatever it is. The 3M and
    monthly cases are said in the load report."""
    settle = _date(row.get("Settle Date"))
    for col in LME_PROMPT_COLUMNS:
        d = _date(row.get(col))
        if d is not None:
            if settle and settle > trade_date and settle != d:
                _warn(res, row_no, symbol, f"Settle Date {settle} differs from {col} {d}; the prompt is taken from {col}")
            return d, col
    d = _date_in_text(_s(row.get("Description")))
    if d is not None:
        return d, "Description"
    if settle and settle > trade_date:
        return settle, "Settle Date"
    marks = f"{_s(row.get('Symbol'))} {_s(row.get('Underlying Symbol'))} {_s(row.get('Description'))}".upper()
    if _THREE_MONTH_RE.search(marks):
        return _lme.three_month_date(trade_date).isoformat(), "3M"
    if contract is not None:
        return _lme.monthly_prompt(contract.year, contract.month).isoformat(), "MONTH"
    if settle:
        return settle, "Settle Date"
    return None, ""


def _parse_lme_forward(res: ParseResult, row: pd.Series, row_no: int, root_id: str, contract) -> None:
    """An LME prompt-date forward (product LME_FWD, Phase 5; P&L rule approved 2026-09-24: the FX
    forward rule in tonnes). One instrument per metal, keyed by its root id ('LME:CA'): asset_class
    LME_FWD, base_ccy the root id, quote_ccy USD, multiplier 1, bbg_ticker the metal's LME cash
    ticker, expiry 9999-12-31 (like an FX pair). ``Quantity`` is lots (a guess: no LME row has
    been seen) signed by Side, converted to tonnes with ``engine.lme.lot_tonnes``; price = USD per
    tonne as quoted; NetInvoice is cross-checked against lots x tonnes x price like a future's.
    Two FX_NEAR legs on the prompt date (``_lme_prompt``): the metal (ccy = the root id, tonnes,
    settles_cash 0) and the USD (-tonnes x fill, settles_cash 1). A prompt that is not an LME
    prompt date for the trade date (``engine.lme.is_valid_prompt``) is a warning only, never a
    reject (hard rule 6); a populated Currency other than USD contradicts the contract and rejects."""
    symbol = _s(row.get("Symbol")) or _s(row.get("Underlying Symbol"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    trade_date = _date(row.get("TradeDate"))
    if trade_date is None:
        res.rejects.append(Reject(row_no, symbol, f"unparseable TradeDate {_s(row.get('TradeDate'))!r} "
                                                  "(an LME ticket's Settle Date is its prompt, never its trade date)"))
        return
    ccy = _ccy(row.get("Currency"))
    if ccy and ccy != "USD":
        res.rejects.append(Reject(row_no, symbol, f"Currency {ccy} contradicts {root_id}, an LME forward in USD"))
        return
    tonnes_per_lot = _lme.lot_tonnes(root_id)
    fill = _future_fill(res, row, row_no, symbol, tonnes_per_lot, quote_unit="USD/t")
    if fill is None:
        return
    lots, price = fill
    prompt, source = _lme_prompt(res, row, row_no, symbol, trade_date, contract)
    if prompt is None:
        res.rejects.append(Reject(row_no, symbol, "LME ticket with no prompt date: none in Prompt Date / Maturity / "
                                                  "Settle Date or the Description, and not a 3M or monthly ticket"))
        return
    if source == "3M":
        res.lme_prompt_notes.append(f"row {row_no} {trade_id} ({root_id}) is a 3-month ticket and takes the 3-month "
                                    f"date of {trade_date}, {prompt}")
    elif source == "MONTH":
        res.lme_prompt_notes.append(f"row {row_no} {trade_id} ({root_id}) names the {contract.contract_id} month and "
                                    f"takes its third-Wednesday prompt, {prompt}")
    try:
        valid = _lme.is_valid_prompt(prompt, trade_date)
    except ValueError:
        valid = False
    if not valid:
        _warn(res, row_no, symbol, f"prompt {prompt} (from {source}) is not an LME prompt date for a ticket dealt on "
                                   f"{trade_date} (cash to 3M daily, Wednesdays to 6M, third Wednesdays beyond); "
                                   "loaded as given")
    tonnes = lots * tonnes_per_lot
    res.instruments.setdefault(root_id, Instrument(
        instrument_id=root_id, asset_class="LME_FWD", base_ccy=root_id, quote_ccy="USD",
        multiplier=1.0, is_ndf=0, bbg_ticker=_lme.cash_ticker(root_id), expiry_date=PERPETUAL,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=root_id, product="LME_FWD", package_id=trade_id,
        trade_date=trade_date, quantity=tonnes, price=price, **_common(row),
    ))
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", root_id, tonnes, trade_date, prompt, price, 0))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", "USD", -tonnes * price, trade_date, prompt, price, 1))


# --------------------------------------------------------------------------- load
def _rows(objs) -> List[tuple]:
    return [tuple(vars(o).values()) for o in objs]


def load(source: Union[str, Path, bytes, pd.DataFrame], conn: sqlite3.Connection,
         strict: bool = False, filename: Optional[str] = None,
         book: Optional[Union[BookFilter, str, Path]] = None) -> ParseResult:
    """Parse and upsert. Re-loading a trade id replaces its trade and legs. With
    ``strict=True`` any reject raises ValueError before anything is written; otherwise
    rejected rows are skipped and the rest is loaded. ``book`` is the row filter, as
    ``parse`` takes it (None = ``config/book.yaml``); ``conn`` is also handed to ``parse`` so
    a future's stored Bloomberg contract dates replace the estimated expiry.

    Numbers: everything written to a REAL column has passed ``_enforce_numeric`` (finite
    numbers only), so neither text nor NaN can reach ``trades.quantity`` / ``price``,
    ``trade_legs.amount`` / ``rate``, ``instruments.multiplier`` or the option strike."""
    from data.ingest.schema import create_schema

    create_schema(conn)
    res = parse(source, filename, book=book, conn=conn)
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
        conn.execute("DELETE FROM trade_legs WHERE trade_id IN (SELECT trade_id FROM _incoming)")
        # Column lists are explicit (2026-09-18): a database created by a transient
        # schema variant carries extra `instruments` columns (strike/option_type/...,
        # before `instrument_options` existed), and a positional VALUES list of 8 then
        # failed the whole upload with "table instruments has 12 columns but 8 values
        # were supplied" -- an import error over nothing the file did wrong.
        instrument_cols = ("(instrument_id, asset_class, base_ccy, quote_ccy, multiplier, is_ndf, bbg_ticker, "
                           "expiry_date) VALUES (?,?,?,?,?,?,?,?)")
        conn.executemany(f"INSERT OR REPLACE INTO instruments {instrument_cols}",
                         _rows(i for k, i in res.instruments.items() if k not in res.underlying_only))
        # an option's underlying future with no trade in the file: never overwrites a row on file
        conn.executemany(f"INSERT OR IGNORE INTO instruments {instrument_cols}",
                         _rows(i for k, i in res.instruments.items() if k in res.underlying_only))
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
