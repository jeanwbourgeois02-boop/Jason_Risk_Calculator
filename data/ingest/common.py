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
    `instrument_options` table (see schema.py). Only produced for FX_OPTION rows."""
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
def cash_ccy(code: str) -> str:
    """'DOL.C-USAA' -> 'USD'; '<CCY>.C-xxAA' -> CCY."""
    m = CASH_CCY_RE.match(str(code))
    if not m:
        raise ValueError(f"unrecognised currency code {code!r}")
    ccy = m.group(1)
    return "USD" if ccy == "DOL" else ccy
