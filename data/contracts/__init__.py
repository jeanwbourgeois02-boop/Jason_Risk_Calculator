"""The commodity contract universe (contract-master lane).

The one record of what a futures contract is: everything that needs a contract's currency,
multiplier, unit, Bloomberg ticker or expiry asks here.

- ``config/contracts.csv``: one row per contract root (``'NYMEX:CL'``), read by ``load_roots``.
- ``ContractRoot`` / ``load_roots`` / ``get_root``: the roots (``universe.py``), with how each
  settles (``settlement``: 'average' for a contract settled on a monthly average of an index)
  and its options' style and expiry month (``option_style``, ``option_lead_months``).
- ``ContractMonth`` / ``contract_month`` / ``request_ticker``: one listed contract, its canonical
  id (``'CLZ26 Comdty'``), Bloomberg's live request ticker (``'CLZ6 Comdty'``) and its dates:
  Bloomberg's when stored, else a conservative estimate flagged ``ESTIMATED``;
  ``averaging_period``: an averaging contract's first and last business day (``months.py``).
- ``resolve_future``: a blotter or Bloomberg symbol to its contract month, or
  ``UnknownContract`` / ``AmbiguousContract``; ``contract_for``: a stored canonical id back to
  its contract month for a known root (``resolve.py``).
- ``OptionContract`` / ``option_contract`` / ``resolve_option`` / ``option_for`` /
  ``option_request_ticker`` / ``SYMBOL``: options on futures (``CMDTY_OPTION``), canonical id
  ``'CLZ26C 70 Comdty'``, live ticker ``'CLZ6C 70 Comdty'`` (``options.py``).
- ``ensure_static_table`` / ``store_static_dates`` / ``static_dates``: Bloomberg's own contract
  and option dates in this lane's ``contract_static`` table (``static.py``).
- ``apply_fixes``: the Bloomberg check's fixes worksheet (columns ``WORKSHEET_COLUMNS``) applied
  to ``config/contracts.csv``, only the ``FIXABLE_FIELDS``, the multiplier recomputed, the whole
  file validated by the loader before it is written (``fixes.py``).
- ``quantity_factor``: how many of one unit make another of the same dimension (mass, volume,
  energy), the table the multiplier check uses (``universe.py``).

Nothing here asks Bloomberg for anything or writes ``marks``.
"""

from data.contracts.fixes import FIXABLE_FIELDS, WORKSHEET_COLUMNS, apply_fixes
from data.contracts.months import (
    BLOOMBERG,
    ESTIMATED,
    ContractMonth,
    averaging_period,
    contract_month,
    estimated_last_trade_date,
    request_ticker,
)
from data.contracts.options import (
    AMERICAN,
    CALL,
    EUROPEAN,
    PUT,
    SYMBOL,
    OptionContract,
    option_contract,
    option_for,
    option_request_ticker,
    resolve_option,
)
from data.contracts.resolve import AmbiguousContract, UnknownContract, contract_for, resolve_future
from data.contracts.static import ensure_static_table, static_dates, store_static_dates
from data.contracts.tickers import MONTH_CODES, format_strike, make_contract_id, make_option_id
from data.contracts.universe import CONTRACTS_CSV, ContractRoot, get_root, load_roots, quantity_factor

__all__ = [
    "AMERICAN", "AmbiguousContract", "BLOOMBERG", "CALL", "CONTRACTS_CSV", "ContractMonth",
    "ContractRoot", "ESTIMATED", "EUROPEAN", "FIXABLE_FIELDS", "MONTH_CODES", "OptionContract", "PUT",
    "SYMBOL", "UnknownContract", "WORKSHEET_COLUMNS", "apply_fixes", "averaging_period", "contract_for",
    "contract_month", "ensure_static_table", "estimated_last_trade_date", "format_strike", "get_root",
    "load_roots", "make_contract_id", "make_option_id", "option_contract", "option_for",
    "option_request_ticker", "quantity_factor", "request_ticker", "resolve_future", "resolve_option",
    "static_dates", "store_static_dates",
]
