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
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Tuple

PERPETUAL = "9999-12-31"

# The user's NDF list (2026-09-21): KRW, IDR, INR, TWD, BRL non-deliverable (INR added
# that day); TRY, MXN deliverable.
NDF_CCYS = frozenset({"BRL", "TWD", "KRW", "IDR", "INR"})

# NDF currencies trade off the 1-month, not spot (user, 2026-09-21: "always show 1m
# forward date price, not spot"). The user's own tickers: Bloomberg's 1M NDF outright,
# quoted as the USD pair (KWN+1M = USDKRW 1M). Written as mark_type NDF_1M on the pair.
NDF_1M_TICKERS = {
    "KRW": "KWN+1M Curncy",
    "IDR": "IHN+1M Curncy",
    "INR": "IRN+1M Curncy",
    "TWD": "NTN+1M Curncy",
    "BRL": "BCN+1M Curncy",
}

# An NDF settles against its currency's own official fixing, not spot (user, 2026-09-22:
# "we need the fix value from each date for the NDFs, thats the only correct way to compute
# the pnl. so the entry price is where we traded, and the exit price is the fix on that day,
# as pulled from bbg"; "each ndf has a unique fix, like inr and krw and twd have their own
# fixes"). One Bloomberg ticker per currency, PX_LAST on the fixing date, written as
# mark_type NDF_FIX on the USD pair dated the fixing date (source BBG_BDH). Quoted as the
# USD pair (units of the currency per USD), like spot.
# Status (2026-09-22): BRL and IDR are verified, their tickers returned fixings on the
# Bloomberg PC that day and the NDF_FIX marks on file came from them. KRW and TWD are the
# user's own terminal check of 2026-09-22 ("kobrusd for korea, try11 index for twd fix"),
# replacing KFTC18 and TAIFX1, which loaded but returned no PX_LAST on the fixing dates.
# INR is still unverified: the ticker spelling has not been checked against a terminal. An
# unknown security fails loudly in the pull (the Market data tab lists it as missing) and the
# P&L says "no official fix on file" and falls back to spot until the ticker is corrected here.
NDF_FIX_TICKERS = {
    "BRL": "BZFXPTAX Index",    # PTAX (Banco Central do Brasil), ~13:15 Sao Paulo; returned fixings 2026-09-22 (verified)
    "KRW": "KOBRUSD Index",     # user, 2026-09-22; yellow key assumed Index, a wrong key shows as 'Unknown/Invalid security' on the next pull
    "INR": "INRFBIL Index",     # FBIL reference rate (RBI until 2018); unverified
    "TWD": "TRY11 Index",       # user, 2026-09-22 ("try11 index for twd fix"), replacing TAIFX1 which returned no PX_LAST
    "IDR": "JISDOR Index",      # JISDOR (Bank Indonesia); returned fixings 2026-09-22 (verified)
}

DESCRIPTION_RE = re.compile(
    r"^TD (\d{2}/\d{2}/\d{4}) VD (\d{2}/\d{2}/\d{4}) (SELL|BUY) ([A-Z]{3}) VS \.(BUY|SELL) ([A-Z]{3}) @ (\d+\.\d{8})$"
)
FORWARD_SYMBOL_RE = re.compile(r"^([A-Z]{6})(\d{6})-(\d+)$")
CASH_CCY_RE = re.compile(r"^([A-Z]{3})\.C-[A-Z]{4}$")
FUTURE_SYMBOL_RE = re.compile(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d)-[A-Z]{4}$")
FUTURE_MONTH_CODES = "FGHJKMNQUVXZ"
# Roots whose expiry rule and contract multiplier are known: CME/CBOT equity-index
# futures, all expiring on the third Friday of the contract month, Bloomberg ticker
# `<root><month><year> Index`. Any other root is rejected rather than silently given
# the third-Friday rule or a guessed multiplier (a wrong multiplier is a wrong P&L).
FUTURE_MULTIPLIERS = {
    "ES": 50.0,    # E-mini S&P 500
    "NQ": 20.0,    # E-mini Nasdaq-100
    "RTY": 50.0,   # E-mini Russell 2000
    "YM": 5.0,     # E-mini Dow
}
KNOWN_FUTURE_ROOTS = frozenset(FUTURE_MULTIPLIERS)

# 'IRSOIS-USD-22860996' -> ('USD', '22860996'). Only the OIS prefix is in scope (the
# only prefix present in the reference file); any other prefix ('IRS-', 'IRSFF-',
# 'IRSBS-', 'IRSXCCY-', ...) does not match and is rejected by the caller, never
# guessed at.
IRS_SYMBOL_RE = re.compile(r"^IRSOIS-([A-Z]{3})-(\d+)$")

# 'IRS NA 11/11/2026 02/11/2027 3.98000000 USD' -> (tag, effective, maturity, rate, ccy).
# Six whitespace-separated tokens, first literally 'IRS'; the second token ('NA' in every
# reference row) is an opaque tag, not validated further.
IRS_DESCRIPTION_RE = re.compile(
    r"^IRS ([A-Z]{2}) (\d{2}/\d{2}/\d{4}) (\d{2}/\d{2}/\d{4}) (\d+\.\d+) ([A-Z]{3})$"
)


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


@dataclass(frozen=True)
class IrsDirection:
    """Which way a swap was read and what decided it, so the UI can show it.
    ``direction`` is 'PAY' (pay fixed, quantity > 0) or 'RECEIVE' (receive fixed,
    quantity < 0); ``decided_by`` names the column and cell that decided, the user's
    stored override, or NO_DIRECTION_SIGNAL when the file carries nothing at all."""
    trade_id: str
    direction: str
    decided_by: str
    defaulted: bool = False


NO_DIRECTION_SIGNAL = "no direction signal in file: defaulted to pay fixed"


# --------------------------------------------------------------------------- helpers
def cash_ccy(code: str) -> str:
    """'DOL.C-USAA' -> 'USD'; '<CCY>.C-xxAA' -> CCY."""
    m = CASH_CCY_RE.match(str(code))
    if not m:
        raise ValueError(f"unrecognised currency code {code!r}")
    ccy = m.group(1)
    return "USD" if ccy == "DOL" else ccy


def third_friday(year: int, month: int) -> date:
    first_wd = calendar.weekday(year, month, 1)  # Mon=0 ... Fri=4
    first_friday = 1 + (4 - first_wd) % 7
    return date(year, month, first_friday + 14)


def future_expiry(symbol: str, as_of: date) -> Tuple[str, str, date]:
    """'ESU6-USAA' -> ('ES', 'ESU6', third Friday of Sep 2026).

    The single year digit is resolved to the nearest year >= as_of.year - 1.
    Only roots in ``KNOWN_FUTURE_ROOTS`` are accepted; an unknown root raises
    ValueError rather than silently receiving the third-Friday rule.
    """
    m = FUTURE_SYMBOL_RE.match(symbol)
    if not m:
        raise ValueError(f"unrecognised futures symbol {symbol!r}")
    root, mcode, ydigit = m.groups()
    if root not in KNOWN_FUTURE_ROOTS:
        raise ValueError(f"unknown futures root {root!r} in {symbol!r}: no expiry rule "
                         f"(known: {sorted(KNOWN_FUTURE_ROOTS)})")
    month = FUTURE_MONTH_CODES.index(mcode) + 1
    year = (as_of.year // 10) * 10 + int(ydigit)
    if year < as_of.year - 1:
        year += 10
    return root, f"{root}{mcode}{ydigit}", third_friday(year, month)
