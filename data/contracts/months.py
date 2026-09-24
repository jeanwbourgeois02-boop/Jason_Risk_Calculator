"""A contract month: one root, one delivery month, its canonical id and its dates.

Until Bloomberg's own dates are stored (``static.store_static_dates``), a contract's last trade
date is an ESTIMATE: the last weekday of the contract month. That is never earlier than the real
last trading day of any listed monthly contract (most stop well before month end: CL in the month
before, Chinese contracts mid-month, LME on the third Wednesday), so a live contract is never
taken for expired; an ESTIMATED date means "no later than", and every reader must say it is
estimated. The first notice date has no estimate: None until Bloomberg's is on file.
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from data.contracts.static import static_dates
from data.contracts.tickers import make_contract_id, month_code, padded_root, to_date
from data.contracts.universe import ContractRoot, get_root

BLOOMBERG = "BLOOMBERG"
ESTIMATED = "ESTIMATED"


@dataclass(frozen=True)
class ContractMonth:
    """One listed contract: root + delivery month + year."""

    root: ContractRoot
    month: int                         # 1-12, the delivery (contract) month
    year: int                          # four digits
    month_code: str                    # 'FGHJKMNQUVXZ'[month - 1]
    contract_id: str                   # canonical: 'CLZ26 Comdty', 'C Z26 Comdty'
    last_trade_date: date
    first_notice_date: Optional[date]  # None: not on file (never estimated)
    dates_source: str                  # 'BLOOMBERG' or 'ESTIMATED'

    @property
    def root_id(self) -> str:
        return self.root.root_id

    @property
    def estimated(self) -> bool:
        """True while the dates are the conservative estimate, not Bloomberg's."""
        return self.dates_source == ESTIMATED


def estimated_last_trade_date(month: int, year: int) -> date:
    """The last weekday (Monday to Friday) of the contract month."""
    d = date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def contract_month(root_id: str, month: int, year: int,
                   conn: Optional[sqlite3.Connection] = None) -> ContractMonth:
    """The contract month of a root. With ``conn``, Bloomberg's stored dates win over the estimate.

    ``year`` has four digits (a two-digit year is read as 20YY). The month is not checked against
    ``active_months``, which is the main-contract cycle, not the list of listed months.
    """
    root = get_root(root_id)
    month = int(month)
    year = int(year)
    if year < 100:
        year += 2000
    if not 1900 <= year <= 2199:
        raise ValueError(f"year {year!r} is not a four-digit contract year")
    code = month_code(month)
    contract_id = make_contract_id(root.bbg_root, code, year, root.bbg_yellow_key)
    stored = static_dates(conn, contract_id) if conn is not None else None
    if stored is not None:
        fnd = stored["first_notice_date"]
        return ContractMonth(root, month, year, code, contract_id, to_date(stored["last_trade_date"]),
                             to_date(fnd) if fnd else None, BLOOMBERG)
    return ContractMonth(root, month, year, code, contract_id, estimated_last_trade_date(month, year),
                         None, ESTIMATED)


def request_ticker(contract: ContractMonth, as_of: date) -> str:
    """Bloomberg's live form while the contract trades (``'CLZ6 Comdty'``, ``'C Z6 Comdty'``),
    the canonical two-digit id once ``as_of`` is past its last trade date (``'CLZ26 Comdty'``)."""
    if to_date(as_of) <= contract.last_trade_date:
        root = contract.root
        return f"{padded_root(root.bbg_root)}{contract.month_code}{contract.year % 10} {root.bbg_yellow_key}"
    return contract.contract_id
