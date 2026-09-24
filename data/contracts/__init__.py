"""The commodity contract universe (contract-master lane).

The one record of what a futures contract is: everything that needs a contract's currency,
multiplier, unit, Bloomberg ticker or expiry asks here.

- ``config/contracts.csv``: one row per contract root (``'NYMEX:CL'``), read by ``load_roots``.
- ``ContractRoot`` / ``load_roots`` / ``get_root``: the roots (``universe.py``).
- ``ContractMonth`` / ``contract_month`` / ``request_ticker``: one listed contract, its canonical
  id (``'CLZ26 Comdty'``), Bloomberg's live request ticker (``'CLZ6 Comdty'``) and its dates:
  Bloomberg's when stored, else a conservative estimate flagged ``ESTIMATED`` (``months.py``).
- ``resolve_future``: a blotter or Bloomberg symbol to its contract month, or
  ``UnknownContract`` / ``AmbiguousContract``; ``contract_for``: a stored canonical id back to
  its contract month for a known root (``resolve.py``).
- ``ensure_static_table`` / ``store_static_dates`` / ``static_dates``: Bloomberg's own contract
  dates in this lane's ``contract_static`` table (``static.py``).

Nothing here asks Bloomberg for anything or writes ``marks``.
"""

from data.contracts.months import (
    BLOOMBERG,
    ESTIMATED,
    ContractMonth,
    contract_month,
    estimated_last_trade_date,
    request_ticker,
)
from data.contracts.resolve import AmbiguousContract, UnknownContract, contract_for, resolve_future
from data.contracts.static import ensure_static_table, static_dates, store_static_dates
from data.contracts.tickers import MONTH_CODES, make_contract_id
from data.contracts.universe import CONTRACTS_CSV, ContractRoot, get_root, load_roots

__all__ = [
    "AmbiguousContract", "BLOOMBERG", "CONTRACTS_CSV", "ContractMonth", "ContractRoot", "ESTIMATED",
    "MONTH_CODES", "UnknownContract", "contract_for", "contract_month", "ensure_static_table",
    "estimated_last_trade_date", "get_root", "load_roots", "make_contract_id", "request_ticker",
    "resolve_future", "static_dates", "store_static_dates",
]
