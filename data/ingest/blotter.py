"""Trade blotter CSV -> instruments, trades, trade_legs.

Source: the transaction-level blotter export (``data/raw/new_sample_trades.csv``-shaped
files), which supersedes the BNP position/P&L snapshot (``data/ingest/bnp.py``) as the
trade source going forward. ``data/ingest/bnp.py`` is left untouched: it may still be
referenced elsewhere (e.g. reconciliation, futures-position snapshots) and this module
only adds a second source alongside it.

Scope: rows with ``Status == 'Completed'`` and ``Fund == 'NMMF'``. ``Fin Type`` (not
``Financial Type`` as in the BNP file) selects the row kind: FORWARD, CURRENCY, FUTURE
(singular, not FUTURES), OPTION, INTEREST_RATE_SWAP; any other value, or a non-Completed /
non-NMMF row, is filtered out silently and counted. The ``Product`` column is NOT
authoritative (mostly junk, e.g. 'FUTURE' on non-future rows) -- ``Fin Type`` decides.

This file is transaction-level (one row per fill), unlike the BNP snapshot (one netted
row per open position per day), which changes what each row produces:
  - FORWARD: a trade + 2 FX_NEAR legs, same shape as bnp.py's FORWARD handling. The
    ``Symbol`` (``<PAIR><mmddyy>-<id>``) and ``Description`` (``TD .. VD .. SELL/BUY ..
    VS .BUY/SELL .. @ rate``) regexes are byte-identical to bnp.py's on every row of the
    reference sample, so they are imported from there rather than redefined. Quantity
    direction and amounts come from the structured ``Buy Currency`` / ``Sell Currency`` /
    ``BuyCurrency Amount`` / ``SellCurrency Amount`` columns (this format gives clean
    buy/sell legs directly, unlike BNP's ``Local Cost`` which has to be reverse engineered
    from ``Quantity x rate``), cross-checked against the description's sold/bought
    currencies.
  - CURRENCY: cash trade/settlement rows, not an EOD balance snapshot. This module writes
    the CASH instrument only (``setdefault``, matching bnp.py); no trade / legs / positions
    row is produced for CURRENCY rows here -- cash movement bookkeeping stays BNP's job
    (this is a data-contract gap, flagged rather than guessed at: there is no ``positions``
    grain in this file since it is not an EOD snapshot).
  - FUTURE: unlike the BNP snapshot (which never carries a per-fill futures price and so
    produces no trade at all -- see bnp.py's module docstring), this file gives ``Trade
    Id`` and a real fill ``Price`` per contract, so a trade + 1 NOTIONAL leg IS written
    here. This is in fact the futures-fill source CLAUDE.md's "xlsx -> tables" section
    describes as coming from the workbook; that expectation is stale now that the blotter
    carries it directly (flagged for the housekeeper, not changed here).
  - OPTION: new versus bnp.py. product FX_OPTION, 1 NOTIONAL leg in the base currency of
    the pair (``Currency Pair`` column), quantity signed by ``Side`` (Buy = long = +,
    Sell = short = -), price = premium fill (``Price``, per unit).
  - INTEREST_RATE_SWAP: ``Side`` is 'Buy' on every row in the reference sample and carries
    no direction here (unlike FUTURE/OPTION). The direction signal is instead the sign of
    ``Notional`` itself (user-confirmed 2026-09-16): positive Notional = pay fixed,
    negative = receive fixed -- the same convention as ``trades.quantity`` in CLAUDE.md's
    IRS leg layout (payer/quantity > 0 has a negative FIXED leg, positive FLOAT leg).
    ``trades.quantity`` = ``Notional`` signed, in full notional units (this file's
    ``Notional`` column is already the full unit amount, e.g. ``625,000,000`` --
    unlike the millions-scaled ``Quantity`` column). This matches ``irs.py``'s BNP path
    (``quantity_mm * 1e6``) and CLAUDE.md's "notional (IRS: + = pay fixed)" wording, so
    IRS quantity/leg amounts are on the same scale regardless of source. All 10 rows in
    the 2026-09-16 reference sample have positive Notional (all payers); a negative
    Notional has not been observed but is honoured if seen.

``trades.strategy`` has no equivalent column in this file (BNP's ``NM Strategy``); left
``''`` (schema allows empty string, not NULL). ``trades.account`` = ``ExtAccount``,
``trades.trader`` = ``Trader``.

Numeric cells may carry thousands separators (``'1,137,580.00'``) and so arrive as
strings; ``_num()`` strips separators before ``float()``. Column order, and extra/missing
trailing columns, are not relied upon -- every field is looked up by name, as in bnp.py.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd

from data.ingest.bnp import (
    CASH_CCY_RE,
    DESCRIPTION_RE,
    FORWARD_SYMBOL_RE,
    FUTURE_SYMBOL_RE,
    Instrument,
    InstrumentOption,
    NDF_CCYS,
    PERPETUAL,
    Reject,
    Trade,
    TradeLeg,
    cash_ccy,
    future_expiry,
)
from data.ingest.irs import IRS_SYMBOL_RE

log = logging.getLogger(__name__)

SOURCE = "XLSX"  # 'trades' PK has no source-specific namespace of its own; the value
# just has to be one of the enum CLAUDE.md lists for `trades.source` ('BNP | XLSX |
# MANUAL'). This is a blotter export, not the xlsx calculator, but 'XLSX' is the closest
# fit in the fixed enum and keeps this module from having to widen the contract; flagged
# for the housekeeper if a dedicated 'BLOTTER' value is wanted.
FUND = "NMMF"
STATUS_OK = "Completed"
IN_SCOPE_TYPES = ("FORWARD", "CURRENCY", "FUTURE", "OPTION", "INTEREST_RATE_SWAP")

# 'EURSEK092326C-197727826' -> ('EURSEK', '092326', 'C', '197727826').
OPTION_SYMBOL_RE = re.compile(r"^([A-Z]{6})(\d{6})([CP])-(\d+)$")
# '11.058400 STRIKE' in the free-text Description column -- see _parse_option.
STRIKE_RE = re.compile(r"(\d+\.\d+)\s+STRIKE")


@dataclass
class ParseResult:
    trades: List[Trade] = field(default_factory=list)
    legs: List[TradeLeg] = field(default_factory=list)
    instruments: Dict[str, Instrument] = field(default_factory=dict)
    instrument_options: Dict[str, InstrumentOption] = field(default_factory=dict)
    rejects: List[Reject] = field(default_factory=list)
    n_forward: int = 0
    n_currency: int = 0
    n_future: int = 0
    n_option: int = 0
    n_skipped_irs: int = 0
    n_skipped_other: int = 0
    n_skipped_status_or_fund: int = 0


def _num(v) -> float:
    """float() that tolerates thousands separators and blank/NaN cells (-> NaN, not 0,
    so a genuinely missing amount fails a downstream check rather than silently zeroing)."""
    if v is None:
        return math.nan
    if isinstance(v, float):
        return v
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return math.nan
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return math.nan


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v).strip()


def _ddmy_iso(v: str) -> str:
    """'20/8/2026' (day/month/year, not zero-padded) -> ISO. Used only as a fallback /
    cross-check; FORWARD trade_date and value_date come from the (US mm/dd/yyyy)
    Description regex instead, which is unambiguous."""
    return datetime.strptime(v.strip(), "%d/%m/%Y").date().isoformat()


def parse(csv_path: Union[str, Path]) -> ParseResult:
    """Pure parse of a blotter CSV. Never writes; never coerces malformed rows.

    ``encoding='utf-8-sig'`` (matching ``data/ingest/upload.py::_parse_frame``): tolerates
    a leading UTF-8 byte-order-mark (common in an Excel-exported CSV) without corrupting
    the first column name, which would otherwise make every row look unrecognized.
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path, dtype=str, encoding='utf-8-sig')
    res = ParseResult()

    for idx, row in df.iterrows():
        row_no = int(idx) + 2  # header is line 1
        # Case-insensitive on purpose (flexibility, not a contract requirement): an
        # export with 'completed'/'COMPLETED' or 'nmmf'/'Nmmf' should still be
        # recognized rather than silently skipped over a trivial case difference.
        if _s(row.get("Status")).casefold() != STATUS_OK.casefold() or \
           _s(row.get("Fund")).casefold() != FUND.casefold():
            res.n_skipped_status_or_fund += 1
            continue
        # Upper-cased for the same reason as Status/Fund above: 'Forward'/'forward'
        # should not silently fall into "unsupported row" just because of case.
        ftype = _s(row.get("Fin Type")).upper()
        if ftype == "FORWARD":
            res.n_forward += 1
            _parse_forward(res, row, row_no)
        elif ftype == "CURRENCY":
            res.n_currency += 1
            _parse_currency(res, row, row_no)
        elif ftype == "FUTURE":
            res.n_future += 1
            _parse_future(res, row, row_no)
        elif ftype == "OPTION":
            res.n_option += 1
            _parse_option(res, row, row_no)
        elif ftype == "INTEREST_RATE_SWAP":
            n_rejects_before = len(res.rejects)
            _parse_irs(res, row, row_no)
            if len(res.rejects) > n_rejects_before:
                res.n_skipped_irs += 1
                log.info("row %d: skipping %s %s (%s)", row_no, ftype,
                         _s(row.get("Symbol")), res.rejects[-1].reason)
        else:
            res.n_skipped_other += 1
    return res


def _parse_forward(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol"))
    desc = _s(row.get("Description"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return

    sm = FORWARD_SYMBOL_RE.match(symbol)
    if not sm:
        res.rejects.append(Reject(row_no, symbol, f"Symbol does not match <PAIR><mmddyy>-<id>: {symbol!r}"))
        return
    pair, sym_vd, sym_id = sm.groups()
    # NOTE: unlike the BNP file, the trailing id in Symbol here is 'Instrument Id', not
    # 'Trade Id' (e.g. Symbol '...-197584766' vs Trade Id '934530555' on the same row) --
    # confirmed against the 'Instrument Id' column in the reference sample. Not
    # cross-checked against Trade Id; Instrument Id is not otherwise used by this parser.

    dm = DESCRIPTION_RE.match(desc)
    if not dm:
        res.rejects.append(Reject(row_no, symbol, f"Description does not match FORWARD regex: {desc!r}"))
        return
    td_us, vd_us, verb1, ccy1, verb2, ccy2, rate_s = dm.groups()
    if verb1 == verb2:
        res.rejects.append(Reject(row_no, symbol, f"description verbs are both {verb1}: {desc!r}"))
        return
    sold_ccy, bought_ccy = (ccy1, ccy2) if verb1 == "SELL" else (ccy2, ccy1)
    rate = float(rate_s)
    trade_date = datetime.strptime(td_us, "%m/%d/%Y").date().isoformat()
    value_date = datetime.strptime(vd_us, "%m/%d/%Y").date().isoformat()
    vd_desc_mmddyy = datetime.strptime(vd_us, "%m/%d/%Y").strftime("%m%d%y")
    if vd_desc_mmddyy != sym_vd:
        res.rejects.append(Reject(row_no, symbol,
                                  f"value date {sym_vd} in Symbol disagrees with description VD {vd_us}"))
        return

    base_ccy, quote_ccy = pair[:3], pair[3:]
    if {sold_ccy, bought_ccy} != {base_ccy, quote_ccy}:
        res.rejects.append(Reject(row_no, symbol,
                                  f"pair {pair} disagrees with description currencies {sold_ccy}/{bought_ccy}"))
        return

    buy_ccy_raw = _s(row.get("Buy Currency"))
    sell_ccy_raw = _s(row.get("Sell Currency"))
    try:
        buy_ccy = cash_ccy(buy_ccy_raw)
        sell_ccy = cash_ccy(sell_ccy_raw)
    except ValueError as e:
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    if {buy_ccy, sell_ccy} != {base_ccy, quote_ccy}:
        res.rejects.append(Reject(row_no, symbol,
                                  f"Buy/Sell Currency columns {buy_ccy}/{sell_ccy} disagree with pair {pair}"))
        return

    buy_amt = _num(row.get("BuyCurrency Amount"))
    sell_amt = _num(row.get("SellCurrency Amount"))
    if math.isnan(buy_amt) or math.isnan(sell_amt):
        res.rejects.append(Reject(row_no, symbol, "blank BuyCurrency/SellCurrency Amount"))
        return

    # base_ccy amount, signed: + if we bought the base ccy, - if we sold it.
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
        trade_date=trade_date, quantity=base_amount, price=rate, account=_s(row.get("ExtAccount")),
        counterparty=_s(row.get("Counterparty")), strategy="", trader=_s(row.get("Trader")),
        description=desc,
    ))
    settles_cash = 0 if is_ndf else 1
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", base_ccy, base_amount, trade_date, value_date, rate, settles_cash))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", quote_ccy, quote_amount, trade_date, value_date, rate, settles_cash))


def _parse_currency(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol"))
    try:
        ccy = cash_ccy(symbol)
    except ValueError as e:
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    instrument_id = f"CASH-{ccy}"
    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="CASH", base_ccy=ccy, quote_ccy=ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=f"{ccy} Curncy", expiry_date=PERPETUAL,
    ))
    # No trade / legs / positions row: see module docstring (CURRENCY rows here are
    # settlement-level cash movements, not the EOD balance the `positions` grain expects).


def _parse_future(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    side = _s(row.get("Side"))
    if side not in ("Buy", "Sell"):
        res.rejects.append(Reject(row_no, symbol, f"unrecognised Side {side!r}"))
        return

    trade_date_raw = _s(row.get("TradeDate"))
    try:
        trade_date = _ddmy_iso(trade_date_raw)
    except ValueError:
        res.rejects.append(Reject(row_no, symbol, f"unparseable TradeDate {trade_date_raw!r}"))
        return

    try:
        root, code, expiry = future_expiry(symbol, date.fromisoformat(trade_date))
    except ValueError as e:
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    instrument_id = f"{code} Index"

    contracts = _num(row.get("Quantity"))
    price = _num(row.get("Price"))
    if math.isnan(contracts) or math.isnan(price):
        res.rejects.append(Reject(row_no, symbol, "blank Quantity/Price"))
        return
    signed_contracts = contracts if side == "Buy" else -contracts
    multiplier = 50.0  # ES only (KNOWN_FUTURE_ROOTS in bnp.py); revisit if other roots added.
    expiry_iso = expiry.isoformat()

    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="FUTURE", base_ccy=root, quote_ccy="USD",
        multiplier=multiplier, is_ndf=0, bbg_ticker=instrument_id, expiry_date=expiry_iso,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=instrument_id, product="FUTURE",
        package_id=trade_id, trade_date=trade_date, quantity=signed_contracts, price=price,
        account=_s(row.get("ExtAccount")), counterparty=_s(row.get("Counterparty")), strategy="",
        trader=_s(row.get("Trader")), description=_s(row.get("Description")),
    ))
    res.legs.append(TradeLeg(
        trade_id, 1, "NOTIONAL", "USD", signed_contracts * multiplier * price,
        trade_date, expiry_iso, price, 0))


def _parse_option(res: ParseResult, row: pd.Series, row_no: int) -> None:
    symbol = _s(row.get("Symbol"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    om = OPTION_SYMBOL_RE.match(symbol)
    if not om:
        res.rejects.append(Reject(row_no, symbol, f"Symbol does not match <PAIR><mmddyy>[CP]-<id>: {symbol!r}"))
        return
    pair, exp_s, cp, _sym_id = om.groups()
    # _sym_id is 'Instrument Id', not 'Trade Id' -- see the same note in _parse_forward.
    option_type = "CALL" if cp == "C" else "PUT"
    # Strike is not a dedicated column in this file; when present it's embedded in the
    # free-text Description as '<N.NNNNNN> STRIKE' (e.g. 'EURSEK-XXAA 11.058400 STRIKE
    # EUR Call ...'). Several real rows in the reference sample omit it entirely (e.g.
    # 'USDJPY-XXAA EUR Put 11/19/2026 MLILUK') -- those get the 0.0 sentinel per
    # CLAUDE.md, never a fabricated value.
    strike_m = STRIKE_RE.search(_s(row.get("Description")))
    strike = float(strike_m.group(1)) if strike_m else 0.0
    try:
        expiry = datetime.strptime(exp_s, "%m%d%y").date()
    except ValueError:
        res.rejects.append(Reject(row_no, symbol, f"unparseable expiry {exp_s!r} in Symbol"))
        return

    ccy_pair_raw = _s(row.get("Currency Pair"))
    # 'Currency Pair' looks like 'EURSEK-XXAA': first 6 chars are the pair, same
    # convention as the FORWARD Symbol prefix -- reused directly rather than via cash_ccy
    # (which expects the CURRENCY-row '<CCY>.C-xxAA' shape, not this one).
    if ccy_pair_raw[:6] != pair:
        res.rejects.append(Reject(row_no, symbol,
                                  f"Currency Pair {ccy_pair_raw!r} disagrees with Symbol pair {pair}"))
        return
    base_ccy = pair[:3]

    side = _s(row.get("Side"))
    if side not in ("Buy", "Sell"):
        res.rejects.append(Reject(row_no, symbol, f"unrecognised Side {side!r}"))
        return

    trade_date_raw = _s(row.get("TradeDate"))
    try:
        trade_date = _ddmy_iso(trade_date_raw)
    except ValueError:
        res.rejects.append(Reject(row_no, symbol, f"unparseable TradeDate {trade_date_raw!r}"))
        return

    notional = _num(row.get("Quantity"))
    premium = _num(row.get("Price"))
    if math.isnan(notional) or math.isnan(premium):
        res.rejects.append(Reject(row_no, symbol, "blank Quantity/Price"))
        return
    signed_notional = notional if side == "Buy" else -notional
    expiry_iso = expiry.isoformat()

    res.instruments.setdefault(symbol, Instrument(
        instrument_id=symbol, asset_class="FX_OPTION", base_ccy=base_ccy, quote_ccy=pair[3:],
        multiplier=1.0, is_ndf=0, bbg_ticker=symbol, expiry_date=expiry_iso,
    ))
    res.instrument_options.setdefault(symbol, InstrumentOption(
        instrument_id=symbol, strike=strike, option_type=option_type,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=symbol, product="FX_OPTION",
        package_id=trade_id, trade_date=trade_date, quantity=signed_notional, price=premium,
        account=_s(row.get("ExtAccount")), counterparty=_s(row.get("Counterparty")), strategy="",
        trader=_s(row.get("Trader")), description=_s(row.get("Description")),
    ))
    res.legs.append(TradeLeg(
        trade_id, 1, "NOTIONAL", base_ccy, signed_notional, trade_date, expiry_iso, premium, 0))


def _parse_irs(res: ParseResult, row: pd.Series, row_no: int) -> None:
    """INTEREST_RATE_SWAP row -> instrument + trade + FIXED/FLOAT legs.

    Direction: sign of ``Notional`` (positive = pay fixed, negative = receive fixed;
    user-confirmed 2026-09-16 -- see module docstring). ``Side`` is not used: it is
    'Buy' on every reference row and carries no direction here, unlike FUTURE/OPTION.
    Leg shape mirrors ``irs.py``'s BNP handling (FIXED amount = -quantity, FLOAT amount
    = +quantity, spread = 0 on FLOAT) rather than duplicating it independently, since
    that is the CLAUDE.md-specified leg layout, not something specific to the BNP
    column names.
    """
    symbol = _s(row.get("Symbol"))
    trade_id = _s(row.get("Trade Id"))
    if not trade_id:
        res.rejects.append(Reject(row_no, symbol, "blank Trade Id"))
        return
    sm = IRS_SYMBOL_RE.match(symbol)
    if not sm:
        res.rejects.append(Reject(row_no, symbol, f"Symbol does not match IRSOIS-<CCY>-<id>: {symbol!r}"))
        return
    ccy, _sym_id = sm.groups()  # _sym_id is 'Instrument Id', not 'Trade Id' -- see FORWARD note.

    eff_raw = _s(row.get("Effective Date"))
    mat_raw = _s(row.get("Termination"))
    try:
        effective_date = _ddmy_iso(eff_raw)
        maturity_date = _ddmy_iso(mat_raw)
    except ValueError:
        res.rejects.append(Reject(row_no, symbol,
                                  f"unparseable Effective Date/Termination {eff_raw!r}/{mat_raw!r}"))
        return
    if maturity_date <= effective_date:
        res.rejects.append(Reject(
            row_no, symbol, f"maturity {maturity_date} is not after effective date {effective_date}"))
        return

    notional = _num(row.get("Notional"))
    fixed_rate_pct = _num(row.get("FixedRate"))
    if math.isnan(notional) or math.isnan(fixed_rate_pct):
        res.rejects.append(Reject(row_no, symbol, "blank Notional/FixedRate"))
        return
    if notional == 0.0:
        res.rejects.append(Reject(row_no, symbol, "Notional is zero; cannot infer pay/receive direction"))
        return

    # quantity = Notional, signed, in full notional units -- matches irs.py's BNP path
    # (quantity_mm * 1e6) and CLAUDE.md's "notional (IRS: + = pay fixed)" wording. Do NOT
    # divide by 1e6: unlike this file's own 'Quantity' column (millions-scaled), Notional
    # is already the full unit amount.
    quantity = notional  # signed: + = pay fixed, - = receive fixed
    fixed_rate = fixed_rate_pct / 100.0

    trade_date_raw = _s(row.get("TradeDate"))
    try:
        trade_date = _ddmy_iso(trade_date_raw)
    except ValueError:
        trade_date = effective_date  # placeholder, mirrors irs.py's fallback when no trade date is given

    res.instruments.setdefault(symbol, Instrument(
        instrument_id=symbol, asset_class="IRS", base_ccy=ccy, quote_ccy=ccy,
        multiplier=1.0, is_ndf=0, bbg_ticker=symbol, expiry_date=maturity_date,
    ))
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=symbol, product="IRS", package_id=trade_id,
        trade_date=trade_date, quantity=quantity, price=fixed_rate, account=_s(row.get("ExtAccount")),
        counterparty=_s(row.get("Counterparty")), strategy="", trader=_s(row.get("Trader")),
        description=_s(row.get("Description")),
    ))
    # IRS notional never exchanges: settles_cash = 0 on both legs (matches irs.py).
    res.legs.append(TradeLeg(
        trade_id, 1, "FIXED", ccy, -quantity, effective_date, maturity_date, fixed_rate, 0))
    res.legs.append(TradeLeg(
        trade_id, 2, "FLOAT", ccy, quantity, effective_date, maturity_date, 0.0, 0))


# --------------------------------------------------------------------------- load
def _rows(objs) -> List[tuple]:
    return [tuple(vars(o).values()) for o in objs]


def load(csv_path: Union[str, Path], conn: sqlite3.Connection, strict: bool = True) -> ParseResult:
    """Parse and insert. Instruments are upserted; trades / legs error on duplicate keys
    (plain INSERT, matching bnp.py's default 'error' on_duplicate mode -- no idempotent
    skip mode here yet; add one the same way as bnp.load if the blotter is re-uploaded).

    With ``strict=True`` (default) any reject raises ValueError before anything is
    written; with ``strict=False`` the parsed rows are inserted anyway (rejected rows are
    never inserted in either mode).
    """
    from data.ingest.schema import create_schema

    create_schema(conn)
    res = parse(csv_path)
    name = Path(csv_path).name
    for rj in res.rejects:
        log.warning("%s row %d %s: REJECT %s", name, rj.row_no, rj.symbol, rj.reason)
    if strict and res.rejects:
        head = [f"reject row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in res.rejects[:5]]
        raise ValueError(
            f"{name}: {len(res.rejects)} reject(s); nothing loaded (strict=True). "
            f"First: " + " | ".join(head))

    with conn:
        conn.executemany("INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                         _rows(res.instruments.values()))
        conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _rows(res.trades))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", _rows(res.legs))
        conn.executemany("INSERT OR REPLACE INTO instrument_options VALUES (?,?,?,?,?,?)",
                         _rows(res.instrument_options.values()))
    return res
