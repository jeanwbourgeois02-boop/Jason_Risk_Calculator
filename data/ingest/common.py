"""Shared dataclasses, regexes and helpers used by the live trade-ingest path.

Extracted 2026-09-17 from `data/ingest/bnp.py` and `data/ingest/irs.py` (the retired BNP
CSV parser and its IRS row handler) when both were deleted per the user's "no bnp fall
back - that excel and everything linked to it need to go" instruction
(`docs/bnp-excel-removal.md`). `data/ingest/blotter.py` -- the app's only live trade
source -- depends on these symbols directly; they are kept here, independent of any
BNP-specific parsing logic, so the blotter parser has no dependency on the retired
module.

Everything BNP-format-specific (the CSV row parser itself, the reconciliation-check
machinery, the `positions`/P&L-snapshot handling, the `HA_PNL_<date>.csv` filename
convention) was deleted along with `bnp.py` and is NOT reproduced here.

The IRS regexes and direction record, the equity index futures' roots, multipliers and
third-Friday expiry, and the NDF lists and tickers (NDF_CCYS, NDF_1M_TICKERS,
NDF_FIX_TICKERS; every FX pair is written deliverable) left on 2026-09-24 with the commodity
conversion (Phase 2); a commodity future's terms come from `data/contracts/`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PERPETUAL = "9999-12-31"

DESCRIPTION_RE = re.compile(
    r"^TD (\d{2}/\d{2}/\d{4}) VD (\d{2}/\d{2}/\d{4}) (SELL|BUY) ([A-Z]{3}) VS \.(BUY|SELL) ([A-Z]{3}) @ (\d+\.\d{8})$"
)
FORWARD_SYMBOL_RE = re.compile(r"^([A-Z]{6})(\d{6})-(\d+)$")
CASH_CCY_RE = re.compile(r"^([A-Z]{3})\.C-[A-Z]{4}$")


# --------------------------------------------------------------------------- records
@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    asset_class: str
    base_ccy: str
    quote_ccy: str
    multiplier: float
    is_ndf: int
    bbg_ticker: str
    expiry_date: str


@dataclass(frozen=True)
class InstrumentOption:
    """Option-specific attributes for an `instruments` row, written to the sibling
    `instrument_options` table (see schema.py). Produced for FX_OPTION and CMDTY_OPTION rows
    (an option on a commodity future: strike in the future's quoted scale, payoff AMERICAN or
    VANILLA for a European one)."""
    instrument_id: str
    strike: float = 0.0
    option_type: str = ""
    barrier_level: float = 0.0
    avg_start_date: str = "9999-12-31"
    payoff: str = "VANILLA"


@dataclass(frozen=True)
class Trade:
    trade_id: str
    source: str
    instrument_id: str
    product: str
    package_id: str
    trade_date: str
    quantity: float
    price: float
    account: str
    counterparty: str
    strategy: str
    trader: str
    description: str
    theme: str = ""
    # The prime broker's PBRoot cell (2026-09-28: '<letters><digits>[.<d>]_<STRATEGY>', e.g.
    # 'JSHY10.3_COPAR3'), kept raw, and what `parse_pb_root` reads off it: `strategy` above is
    # the name after the underscore, `trade_type` the decimal's meaning.
    pb_root: str = ""
    trade_type: str = ""   # CROSS_EXCHANGE | CROSS_PRODUCT | TERM_STRUCTURE | ''


@dataclass(frozen=True)
class TradeLeg:
    trade_id: str
    leg_no: int
    leg_type: str
    ccy: str
    amount: float
    start_date: str
    settle_date: str
    rate: float
    settles_cash: int


@dataclass(frozen=True)
class Reject:
    row_no: int  # 1-based row number (header = line 1)
    symbol: str
    reason: str


@dataclass(frozen=True)
class ParseWarning:
    """Something the parser recovered or could not cross-check on a row it still loaded
    (a Price that arrived as a date and was rebuilt from the amounts, an option
    NetInvoice that disagrees with Quantity x Price, ...). Never a reject."""
    row_no: int
    symbol: str
    message: str


# --------------------------------------------------------------------------- helpers
# The prime broker's PBRoot cell: '<letters><digits>[.<decimal>]_<STRATEGY>' ('JSHY10.3_COPAR3',
# 'JSHY10_CATTLE'). Seen on Jason's export of 2026-09-28; the decimal's meaning is his own coding.
PB_ROOT_RE = re.compile(r"^\s*[A-Za-z]+\d+(?:\.(?P<decimal>\d+))?(?:_(?P<strategy>.*?))?\s*$")
PB_ROOT_TRADE_TYPES = {"3": "CROSS_EXCHANGE", "4": "CROSS_PRODUCT", "5": "TERM_STRUCTURE"}


def parse_pb_root(text) -> tuple:
    """``(strategy, trade_type)`` read off a PBRoot cell: the strategy is the name after the
    underscore ('COPAR3' from 'JSHY10.3_COPAR3'), the trade type what the decimal means
    (.3 CROSS_EXCHANGE, .4 CROSS_PRODUCT, .5 TERM_STRUCTURE; any other decimal, or none, '').
    Tolerant (hard rule 6): a cell with no underscore gives strategy '' and the type from its
    decimal if any; a blank cell, or one in another shape, gives ('', '')."""
    s = "" if text is None else str(text).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return "", ""
    m = PB_ROOT_RE.match(s)
    if not m:
        return "", ""
    strategy = (m.group("strategy") or "").strip()
    return strategy, PB_ROOT_TRADE_TYPES.get(m.group("decimal") or "", "")


_STRATEGY_KEY_DROP_RE = re.compile(r"[^A-Z0-9]+")


def strategy_key(strategy) -> str:
    """The comparison form of a strategy name: upper case with every space and punctuation
    character dropped ('copar 3', 'Copar-3' and 'COPAR3' share the key 'COPAR3'). Used only
    to *notice* labels that look like one strategy spelled two ways (2026-09-28): nothing is
    renamed or merged on it, and ``trades.strategy`` keeps the name as written. '' for blank."""
    s = "" if strategy is None else str(strategy)
    return _STRATEGY_KEY_DROP_RE.sub("", s.upper())


def cash_ccy(code: str) -> str:
    """'DOL.C-USAA' -> 'USD'; '<CCY>.C-xxAA' -> CCY."""
    m = CASH_CCY_RE.match(str(code))
    if not m:
        raise ValueError(f"unrecognised currency code {code!r}")
    ccy = m.group(1)
    return "USD" if ccy == "DOL" else ccy
