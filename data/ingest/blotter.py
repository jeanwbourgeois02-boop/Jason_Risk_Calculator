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
  - CURRENCY: settlement-level cash movements; the CASH instrument only is written.
  - FUTURE: a trade + 1 NOTIONAL leg (``Quantity`` = contracts signed by ``Side``).
  - OPTION: product FX_OPTION, 1 NOTIONAL leg in the pair's base currency, quantity
    signed by ``Side``, price = premium fill. Strike from the Description when present
    (genuinely absent from the file for some rows -- 0.0 sentinel, never invented).
    Expiry / call-put come from ``Symbol`` when it parses, cross-checked against the
    Description's own date/CALL-PUT word when Description is populated (a disagreement
    rejects, per the tolerance rule below) rather than trusting the Symbol blindly.
  - INTEREST_RATE_SWAP: direction is the sign of ``Notional`` (+ = pay fixed, - =
    receive fixed; user-confirmed 2026-09-16), in full notional units.

Tolerance rule (user instruction 2026-09-17, "as flexible as possible"): a blank,
missing or oddly formatted field never rejects a row when the value can be recovered
from another column; only a genuine contradiction between two populated fields does
(pair vs buy/sell currencies, value date in Symbol vs Description, option pair vs
Currency Pair). Rejected rows are counted and reported, never coerced or invented.

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
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    IRS_DESCRIPTION_RE,
    IRS_SYMBOL_RE,
    NDF_CCYS,
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
    n_future: int = 0
    n_option: int = 0
    n_irs: int = 0
    n_skipped_irs: int = 0
    n_skipped_other: int = 0
    n_skipped_status_or_fund: int = 0
    n_superseded: int = 0   # earlier versions of a Trade Id repeated within the file
    n_updated: int = 0      # set by load(): trades that already existed and were replaced


# --------------------------------------------------------------------------- cell helpers
def _num(v) -> float:
    """float() tolerant of thousands separators, currency symbols, '(1,000)' negatives,
    trailing '-' and blank cells (-> NaN, never 0)."""
    if v is None:
        return math.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("−", "-")
    if s == "" or s.lower() in ("nan", "none", "null", "n/a", "-"):
        return math.nan
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-"):
        neg, s = True, s[:-1]
    s = re.sub(r"[^0-9eE+\-.]", "", s.replace(",", ""))
    try:
        x = float(s)
    except ValueError:
        return math.nan
    return -x if neg else x


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
def parse(source: Union[str, Path, bytes, pd.DataFrame], filename: Optional[str] = None) -> ParseResult:
    """Pure parse of a blotter (path, bytes or an already-read DataFrame). Never writes;
    never coerces a contradictory row."""
    df = source if isinstance(source, pd.DataFrame) else read_table(source, filename)
    df = canonicalize_columns(df).reset_index(drop=True)
    res = ParseResult()
    df, res.n_superseded = _dedupe_versions(df)
    global _DAY_FIRST
    previous, _DAY_FIRST = _DAY_FIRST, detect_day_first(df)
    res.day_first = _DAY_FIRST
    try:
        _parse_rows(df, res)
    finally:
        _DAY_FIRST = previous
    return res


def _parse_rows(df: pd.DataFrame, res: "ParseResult") -> None:
    for idx, row in df.iterrows():
        row_no = int(idx) + 2  # header is line 1
        if _status_excluded(row.get("Status")) or _fund_excluded(row.get("Fund")):
            res.n_skipped_status_or_fund += 1
            continue
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


def _common(row: pd.Series) -> dict:
    return dict(account=_s(row.get("ExtAccount")), counterparty=_s(row.get("Counterparty")),
                strategy="", trader=_s(row.get("Trader")), description=_s(row.get("Description")))


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

    buy_amt = _num(row.get("BuyCurrency Amount"))
    sell_amt = _num(row.get("SellCurrency Amount"))
    if math.isnan(buy_amt) or math.isnan(sell_amt):
        qty = abs(_num(row.get("Quantity")))
        if math.isnan(qty) or math.isnan(rate) or rate == 0:
            res.rejects.append(Reject(row_no, symbol, "blank BuyCurrency/SellCurrency Amount and no Quantity x Price to derive them"))
            return
        base_amt, quote_amt = qty, qty * rate
        buy_amt, sell_amt = (base_amt, quote_amt) if buy_ccy == base_ccy else (quote_amt, base_amt)
    buy_amt, sell_amt = abs(buy_amt), abs(sell_amt)
    if math.isnan(rate) or rate == 0:
        base_amt = buy_amt if buy_ccy == base_ccy else sell_amt
        quote_amt = sell_amt if buy_ccy == base_ccy else buy_amt
        if base_amt == 0:
            res.rejects.append(Reject(row_no, symbol, "no rate and zero base amount"))
            return
        rate = quote_amt / base_amt

    if buy_ccy == base_ccy:
        base_amount, quote_amount = buy_amt, -sell_amt
    else:
        base_amount, quote_amount = -sell_amt, buy_amt

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


def _signed_quantity(row: pd.Series, symbol: str, row_no: int, res: ParseResult, label: str) -> Optional[float]:
    qty = _num(row.get("Quantity"))
    if math.isnan(qty):
        res.rejects.append(Reject(row_no, symbol, f"blank Quantity ({label})"))
        return None
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
    signed_contracts = _signed_quantity(row, symbol, row_no, res, "contracts")
    if signed_contracts is None:
        return
    price = _num(row.get("Price"))
    if math.isnan(price):
        res.rejects.append(Reject(row_no, symbol, "blank Price"))
        return
    multiplier = FUTURE_MULTIPLIERS[root]  # root already validated by future_expiry
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
        if expiry is None or option_type is None:
            res.rejects.append(Reject(row_no, symbol, "Symbol not <PAIR><mmddyy>[CP]-<id> and no expiry / call-put "
                                                       "in Adj. Expiry Date, Description or FxOption Type"))
            return
        symbol = symbol or f"{pair}{datetime.fromisoformat(expiry).strftime('%m%d%y')}{option_type[0]}-{trade_id}"
    strike_m = STRIKE_RE.search(desc)
    strike = float(strike_m.group(1)) if strike_m else 0.0  # 0.0 sentinel, never invented
    base_ccy = pair[:3]
    trade_date = _date(row.get("TradeDate")) or _date(row.get("Settle Date"))
    if trade_date is None:
        res.rejects.append(Reject(row_no, symbol, f"unparseable TradeDate {_s(row.get('TradeDate'))!r}"))
        return
    signed_notional = _signed_quantity(row, symbol, row_no, res, "notional")
    if signed_notional is None:
        return
    premium = _num(row.get("Price"))
    if math.isnan(premium):
        premium = _num(row.get("Premium"))
    if math.isnan(premium):
        res.rejects.append(Reject(row_no, symbol, "blank Price/Premium"))
        return

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


def _parse_irs(res: ParseResult, row: pd.Series, row_no: int) -> None:
    """Direction: sign of ``Notional`` (+ = pay fixed). ``Side`` is not used ('Buy' on
    every reference row). Leg shape mirrors ``irs.py`` (FIXED = -quantity, FLOAT = +quantity)."""
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
    if math.isnan(notional):
        qty_mm = _num(row.get("Quantity"))
        notional = qty_mm * 1e6 if not math.isnan(qty_mm) else math.nan  # Quantity is in millions
    if math.isnan(notional):
        res.rejects.append(Reject(row_no, symbol, "blank Notional (and no Quantity)"))
        return
    if notional == 0.0:
        res.rejects.append(Reject(row_no, symbol, "Notional is zero; cannot infer pay/receive direction"))
        return
    fixed_rate_pct = _num(row.get("FixedRate"))
    if math.isnan(fixed_rate_pct):
        fixed_rate_pct = _num(row.get("Yield"))
    if math.isnan(fixed_rate_pct) and dm:
        fixed_rate_pct = float(dm.group(4))
    if math.isnan(fixed_rate_pct):
        res.rejects.append(Reject(row_no, symbol, "blank FixedRate (and no Yield / rate in Description)"))
        return
    quantity = notional  # signed, full units: + = pay fixed, - = receive fixed
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
         strict: bool = False, filename: Optional[str] = None) -> ParseResult:
    """Parse and upsert. Re-loading a trade id replaces its trade and legs; a swap
    package containing a replaced trade is dissolved so the packaging rule can re-run on
    the new data. With ``strict=True`` any reject raises ValueError before anything is
    written; otherwise rejected rows are skipped and the rest is loaded."""
    from data.ingest.schema import create_schema

    create_schema(conn)
    res = parse(source, filename)
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
        conn.executemany("INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                         _rows(res.instruments.values()))
        if res.trades:
            updates = ",".join(f"{c}=excluded.{c}" for c in trade_cols if c != "trade_id")
            conn.executemany(
                f"INSERT INTO trades ({','.join(trade_cols)}) VALUES ({','.join('?' for _ in trade_cols)}) "
                f"ON CONFLICT(trade_id) DO UPDATE SET {updates}", _rows(res.trades))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", _rows(res.legs))
        # Option terms: the blotter only ever knows strike (when its Description carries
        # one), call/put and a payoff keyword. Terms typed in the app for options the
        # export leaves incomplete (digitals with no strike, barrier levels) must survive
        # a re-upload, so a field is only overwritten by a populated blotter value.
        conn.executemany(
            "INSERT INTO instrument_options VALUES (?,?,?,?,?,?) ON CONFLICT(instrument_id) DO UPDATE SET "
            "strike = CASE WHEN excluded.strike != 0 THEN excluded.strike ELSE strike END, "
            "option_type = CASE WHEN excluded.option_type != '' THEN excluded.option_type ELSE option_type END, "
            "payoff = CASE WHEN excluded.payoff != 'VANILLA' THEN excluded.payoff ELSE payoff END",
            _rows(res.instrument_options.values()))
        conn.execute("DROP TABLE _incoming")
    return res
