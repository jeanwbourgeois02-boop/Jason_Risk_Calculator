"""BNP INTEREST_RATE_SWAP rows -> instruments, trades, trade_legs.

Parsing logic (the ``IRSOIS-<CCY>-<id>`` symbol regex, the six-token description regex,
and direction-from-``Position``-sign inference) is ported from the reference project at
``Rates Swap Calculator/swapcalc/parser/{symbol,parsefile}.py``. Only the parsing logic is
ported, not that project's dataclass shapes: rows here are turned into this repo's own
``Instrument`` / ``Trade`` / ``TradeLeg`` records (see ``data/ingest/bnp.py``), not into a
``Swap`` object.

Scope, deliberately narrow: only the ``IRSOIS`` prefix is handled (the only prefix present
in the reference file, ``IRSOIS-USD-<id>``). ``IRS``, ``IRSFF``, ``IRSBS`` and ``IRSXCCY``
prefixes seen in the reference project are UNCONFIRMED / out of scope here and are rejected
(counted in ``n_skipped_irs`` by the caller in ``bnp.py``), not guessed at.

Direction convention (UNVERIFIED by the PM -- carry this caveat forward, do not assert it
as confirmed): BNP ``Position > 0`` -> pay fixed, i.e. ``trades.quantity > 0`` means pay
fixed, matching CLAUDE.md's documented sign ("notional (IRS: + = pay fixed)"). Note this is
the OPPOSITE of the reference project's own convention (there, ``Position > 0`` ->
RECEIVE_FIXED); the reference project's own prefix comment block says its polarity is a
guess, so it is not treated as more authoritative than the task's explicit instruction.

The BNP file carries no trade date for IRS rows (no "TD" token in the description, unlike
FORWARD rows, and no separate trade-date column). In the absence of any trade-date data,
``trade_date`` is set to the swap's ``effective_date`` as a documented placeholder -- this
is known to be economically wrong whenever a swap trades before its effective date (e.g. a
forward-starting swap) and should be revisited if a real trade date ever becomes available.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import at load time
    from data.ingest.bnp import ParseResult

# 'IRSOIS-USD-22860996' -> ('USD', '22860996'). Only the OIS prefix is in scope (see
# module docstring); any other prefix ('IRS-', 'IRSFF-', 'IRSBS-', 'IRSXCCY-', ...) does
# not match and is rejected by the caller, never guessed at.
IRS_SYMBOL_RE = re.compile(r"^IRSOIS-([A-Z]{3})-(\d+)$")

# 'IRS NA 11/11/2026 02/11/2027 3.98000000 USD' -> (tag, effective, maturity, rate, ccy).
# Six whitespace-separated tokens, first literally 'IRS'; the second token ('NA' in every
# reference row) is an opaque tag, not validated further.
IRS_DESCRIPTION_RE = re.compile(
    r"^IRS ([A-Z]{2}) (\d{2}/\d{2}/\d{4}) (\d{2}/\d{2}/\d{4}) (\d+\.\d+) ([A-Z]{3})$"
)


def _iso(us_date: str) -> str:
    return datetime.strptime(us_date, "%m/%d/%Y").date().isoformat()


def parse_row(res: "ParseResult", row: pd.Series, row_no: int, as_of: str) -> None:
    """Parse one INTEREST_RATE_SWAP row and append to ``res`` in place.

    Mirrors the calling convention of ``bnp._parse_forward``: mutates ``res.instruments``
    / ``res.trades`` / ``res.legs`` / ``res.recon`` on success, or appends to
    ``res.rejects`` and returns without mutating anything else on failure. Produces no
    ``positions`` row: the reference file carries no separate trade-date / fill data for
    IRS beyond what is used here, and (unlike FORWARD/CURRENCY/FUTURES) the task scope for
    this module is instruments/trades/trade_legs only.
    """
    from data.ingest.bnp import Instrument, Reject, Trade, TradeLeg  # lazy: avoids a
    # module-level cycle since bnp.py imports this module's parse_row lazily too.

    symbol = str(row["Symbol"])
    desc = str(row["Symbol Description"])

    sm = IRS_SYMBOL_RE.match(symbol)
    if not sm:
        res.rejects.append(Reject(row_no, symbol, f"Symbol does not match IRSOIS-<CCY>-<id>: {symbol!r}"))
        return
    sym_ccy, trade_id = sm.groups()

    dm = IRS_DESCRIPTION_RE.match(desc)
    if not dm:
        res.rejects.append(Reject(row_no, symbol, f"Symbol Description does not match IRS regex: {desc!r}"))
        return
    _tag, eff_us, mat_us, rate_s, desc_ccy = dm.groups()

    if desc_ccy != sym_ccy:
        res.rejects.append(Reject(
            row_no, symbol, f"Symbol ccy {sym_ccy} disagrees with description ccy {desc_ccy}"))
        return

    effective_date = _iso(eff_us)
    maturity_date = _iso(mat_us)
    if maturity_date <= effective_date:
        res.rejects.append(Reject(
            row_no, symbol, f"maturity {maturity_date} is not after effective date {effective_date}"))
        return

    try:
        position = float(row["Position"])
        quantity_mm = float(row["Quantity"])
    except (TypeError, ValueError):
        res.rejects.append(Reject(row_no, symbol, f"non-numeric Position/Quantity on row {row_no}"))
        return
    if position == 0.0:
        res.rejects.append(Reject(row_no, symbol, "Position is zero; cannot infer pay/receive direction"))
        return

    quantity = quantity_mm * 1e6  # Quantity is notional in millions
    res.recon.add(
        "irs_position_eq_quantity", row_no, symbol, abs(abs(position) - abs(quantity)), 1.0,
        f"|Position|={abs(position)} |Quantity*1e6|={abs(quantity)}")
    res.recon.add(
        "irs_direction_sign", row_no, symbol, 0.0 if (position > 0) == (quantity > 0) else 1.0, 0.0,
        f"Position {position} vs Quantity*1e6 {quantity}")
    # trades.quantity carries the same sign as Position (Position > 0 -> pay fixed; see
    # module docstring caveat), regardless of any Quantity/Position sign disagreement
    # already flagged above.
    quantity = abs(quantity) if position > 0 else -abs(quantity)

    fixed_rate = float(rate_s) / 100.0

    res.instruments.setdefault(symbol, Instrument(
        instrument_id=symbol, asset_class="IRS", base_ccy=sym_ccy, quote_ccy=sym_ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=symbol, expiry_date=maturity_date,
    ))

    res.trades.append(Trade(
        trade_id=trade_id, source="BNP", instrument_id=symbol, product="IRS", package_id=trade_id,
        trade_date=effective_date, quantity=quantity, price=fixed_rate, account=str(row["Account"]),
        counterparty=str(row["CounterParty"]), strategy=str(row["NM Strategy"]),
        trader=str(row["Trader Name"]), description=desc,
    ))
    # IRS notional never exchanges: settles_cash = 0 on both legs, so neither enters the
    # cash ladder (CLAUDE.md "Cash ladder" view filters on settles_cash = 1).
    res.legs.append(TradeLeg(
        trade_id, 1, "FIXED", sym_ccy, -quantity, effective_date, maturity_date, fixed_rate, 0))
    res.legs.append(TradeLeg(
        trade_id, 2, "FLOAT", sym_ccy, quantity, effective_date, maturity_date, 0.0, 0))
