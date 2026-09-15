"""BNP position / P&L CSV -> instruments, trades, trade_legs, positions.

Scope: FORWARD, CURRENCY, FUTURES and INTEREST_RATE_SWAP rows of fund NMMF; any other
Financial Type is filtered out silently.

FORWARD and INTEREST_RATE_SWAP rows produce instruments, trades and trade_legs (IRS rows
via ``data/ingest/irs.py``; IRS produces no positions row -- see that module). FORWARD
additionally produces a positions row. CURRENCY and FUTURES rows produce instruments and
positions only: the PB snapshot carries no fill date or per-fill price for futures (one
netted row per contract), so futures fills come from the xlsx blotter, never from this
file. A row whose Financial Type is INTEREST_RATE_SWAP but that fails to parse (regex
mismatch, zero Position, etc.) still counts in ``n_skipped_irs``, mirroring how a
malformed FORWARD row is rejected rather than coerced.

The file dated T (``HA_PNL_YYYYMMDD.csv``) is the T-1 close snapshot: ``as_of_date`` is
the previous weekday of T unless overridden (see ``_previous_weekday``).

See CLAUDE.md "Data contract -> BNP file -> tables" and "Reconciliation checks and
tolerances" for the specification this module implements.
"""
from __future__ import annotations

import calendar
import logging
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

import pandas as pd

log = logging.getLogger(__name__)

PERPETUAL = "9999-12-31"
SOURCE = "BNP"
FUND = "NMMF"
IN_SCOPE_TYPES = ("FORWARD", "CURRENCY", "FUTURES", "INTEREST_RATE_SWAP")
# Kept as a named constant for callers/tests that still refer to it; no longer means
# "always skipped" -- INTEREST_RATE_SWAP rows are now parsed via data/ingest/irs.py, and
# only rows that fail to parse land in n_skipped_irs.
SKIPPED_TYPES = ()

# PROVISIONAL: taken from docs/open-questions.md "NDF list" (BRL, TWD, KRW, IDR
# non-deliverable; TRY, MXN deliverable). Not yet verified with the prime broker.
NDF_CCYS = frozenset({"BRL", "TWD", "KRW", "IDR"})

DESCRIPTION_RE = re.compile(
    r"^TD (\d{2}/\d{2}/\d{4}) VD (\d{2}/\d{2}/\d{4}) (SELL|BUY) ([A-Z]{3}) VS \.(BUY|SELL) ([A-Z]{3}) @ (\d+\.\d{8})$"
)
FORWARD_SYMBOL_RE = re.compile(r"^([A-Z]{6})(\d{6})-(\d+)$")
CASH_CCY_RE = re.compile(r"^([A-Z]{3})\.C-[A-Z]{4}$")
FUTURE_SYMBOL_RE = re.compile(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d)-[A-Z]{4}$")
FUTURE_MONTH_CODES = "FGHJKMNQUVXZ"
# Roots whose expiry rule is known. ES: CME equity index, third Friday of the contract
# month. Any other root is rejected rather than silently given the third-Friday rule.
KNOWN_FUTURE_ROOTS = frozenset({"ES"})

# Recon tolerances from CLAUDE.md "Reconciliation checks and tolerances".
TOL_LOCAL_COST = 1.0
TOL_MV_LOCAL = 0.05
TOL_MV_BASE_ABS = 0.01
TOL_MV_BASE_REL = 0.5e-6
# PNL_TOL is NOT from the contract. It is the ingest's own float tolerance for the P&L
# identities (DTD = MV Base - Start Date Dirty MV, etc.), which the contract states as exact;
# 0.01 USD absorbs 2-dp rounding. Already logged as an in-force assumption in
# docs/open-questions.md. Observed maxima on the reference file are ~1e-4 USD.
PNL_TOL = 0.01

# Checks whose name appears in the contract's "Reconciliation checks and tolerances" list.
CONTRACT_CHECKS = frozenset({
    "symbol_desc_pair", "symbol_desc_value_date", "local_cost", "mv_local", "mv_base",
    "dtd_total_pnl", "mtd_total_pnl", "dtd_total_eq_trading",
})
# Extra checks beyond the contract list, derived from the column descriptions
# ("Position = Quantity", "Trade Factor = 1", sign of Quantity vs SELL/BUY, futures MV
# identity) and from the position-netting step.
EXTRA_CHECKS = frozenset({
    "position_eq_quantity", "trade_factor_one", "direction_sign", "future_mv",
    "positions_net_mark_consistent", "positions_net_fx_consistent",
})


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


@dataclass
class Position:
    as_of_date: str
    source: str
    account: str
    instrument_id: str
    settle_date: str
    quantity: float
    cost_local: float
    mark: float
    fx_to_usd: float
    mv_local: float
    mv_usd: float
    pnl_dtd_usd: float
    pnl_mtd_usd: float
    pnl_ytd_usd: float


@dataclass(frozen=True)
class Reject:
    row_no: int  # 1-based CSV line number (header = line 1)
    symbol: str
    reason: str


@dataclass(frozen=True)
class CheckResult:
    check: str
    row_no: int
    symbol: str
    deviation: float
    tolerance: float
    detail: str = ""

    @property
    def passed(self) -> bool:
        # A NaN deviation (blank input cell) is a failure, never a silent pass.
        if math.isnan(self.deviation):
            return False
        return self.deviation <= self.tolerance


@dataclass
class ReconReport:
    results: List[CheckResult] = field(default_factory=list)

    def add(self, check: str, row_no: int, symbol: str, deviation: float,
            tolerance: float, detail: str = "") -> None:
        self.results.append(CheckResult(check, row_no, symbol, float(deviation), float(tolerance), detail))

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    def max_deviation(self) -> Dict[str, float]:
        """Largest deviation per check. NaN propagates: one NaN deviation makes the
        maximum for that check NaN, so a blank input cell is never hidden by max()."""
        out: Dict[str, float] = {}
        for r in self.results:
            cur = out.get(r.check)
            if cur is None:
                out[r.check] = r.deviation
            elif math.isnan(cur) or math.isnan(r.deviation):
                out[r.check] = math.nan
            else:
                out[r.check] = max(cur, r.deviation)
        return out

    def by_check(self) -> Dict[str, List[CheckResult]]:
        out: Dict[str, List[CheckResult]] = {}
        for r in self.results:
            out.setdefault(r.check, []).append(r)
        return out


@dataclass
class ParseResult:
    as_of_date: str
    file_date: str
    instruments: Dict[str, Instrument] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    legs: List[TradeLeg] = field(default_factory=list)
    positions: List[Position] = field(default_factory=list)
    rejects: List[Reject] = field(default_factory=list)
    recon: ReconReport = field(default_factory=ReconReport)
    n_forward: int = 0
    n_currency: int = 0
    n_futures: int = 0
    n_skipped_irs: int = 0
    n_skipped_other: int = 0
    n_skipped_fund: int = 0
    # Populated only by load() when on_duplicate='skip': counts of rows whose primary
    # key already existed in the DB AND whose values matched the existing row (so were
    # correctly left alone) vs rows whose key existed but whose values differed (the
    # existing DB row is kept; the amended row is NOT applied -- see conflict_details).
    skipped: Dict[str, int] = field(default_factory=lambda: {"trades": 0, "legs": 0, "positions": 0})
    conflicts: Dict[str, int] = field(default_factory=lambda: {"trades": 0, "legs": 0, "positions": 0})
    conflict_details: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def file_date_from_name(path: Union[str, Path]) -> date:
    m = re.search(r"HA_PNL_(\d{8})\.csv$", Path(path).name)
    if not m:
        raise ValueError(f"cannot derive file date from name {Path(path).name!r}")
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def _previous_weekday(d: date) -> date:
    """Previous Mon-Fri calendar day of ``d``.

    This is NOT a trading calendar: it has no holiday list, so the file dated the day
    after a US holiday (e.g. the Tuesday after Labor Day) would be given the holiday as
    its ``as_of_date``. Callers must pass ``as_of_date`` explicitly to ``parse`` /
    ``load`` on such days. Kept private so the P&L engine does not import it as a
    business-day calendar.
    """
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _iso(us_date: str) -> str:
    return datetime.strptime(us_date, "%m/%d/%Y").date().isoformat()


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


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _f(v) -> float:
    """float() with blank/NaN -> 0.0 (used only for numeric cells that may be empty)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(x) else x


def _s(v) -> str:
    return "" if v is None or _isnan(v) else str(v)


def _position(as_of: str, row: pd.Series, instrument_id: str, settle_date: str,
              quantity: float, fx: float) -> Position:
    return Position(
        as_of_date=as_of,
        source=SOURCE,
        account=_s(row["Account"]),
        instrument_id=instrument_id,
        settle_date=settle_date,
        quantity=float(quantity),
        cost_local=_f(row["Local Cost"]),
        mark=_f(row["Price"]),
        fx_to_usd=fx,
        mv_local=_f(row["Market Value Local"]),
        mv_usd=_f(row["Market Value Base"]),
        pnl_dtd_usd=_f(row["DTD Total P&L"]),
        pnl_mtd_usd=_f(row["MTD Total P&L"]),
        pnl_ytd_usd=_f(row["YTD Total P&L"]),
    )


# (row_no, Position) pairs before netting, so netting failures can cite the source rows.
RawPositions = List[Tuple[int, Position]]


# --------------------------------------------------------------------------- parse
def parse(csv_path: Union[str, Path], as_of_date: Optional[Union[str, date]] = None) -> ParseResult:
    """Pure parse of a BNP CSV. Never writes; never coerces malformed rows.

    ``as_of_date`` defaults to the previous weekday of the file date; pass it explicitly
    on days after a holiday (see ``_previous_weekday``).
    """
    csv_path = Path(csv_path)
    file_date = file_date_from_name(csv_path)
    if as_of_date is None:
        as_of = _previous_weekday(file_date)
    elif isinstance(as_of_date, str):
        as_of = date.fromisoformat(as_of_date)
    else:
        as_of = as_of_date
    as_of_iso = as_of.isoformat()

    df = pd.read_csv(csv_path)
    res = ParseResult(as_of_date=as_of_iso, file_date=file_date.isoformat())
    raw_positions: RawPositions = []

    for idx, row in df.iterrows():
        row_no = int(idx) + 2  # header is line 1
        if _s(row["Fund"]) != FUND:
            res.n_skipped_fund += 1
            continue
        ftype = _s(row["Financial Type"])
        symbol = _s(row["Symbol"])
        if ftype == "FORWARD":
            res.n_forward += 1
            _parse_forward(res, raw_positions, row, row_no, as_of_iso)
        elif ftype == "CURRENCY":
            res.n_currency += 1
            _parse_currency(res, raw_positions, row, row_no, as_of_iso)
        elif ftype == "FUTURES":
            res.n_futures += 1
            _parse_future(res, raw_positions, row, row_no, as_of, as_of_iso)
        elif ftype == "INTEREST_RATE_SWAP":
            from data.ingest.irs import parse_row as _parse_irs_row  # lazy: see irs.py

            n_rejects_before = len(res.rejects)
            _parse_irs_row(res, row, row_no, as_of_iso)
            if len(res.rejects) > n_rejects_before:
                res.n_skipped_irs += 1
                log.info("row %d: skipping %s %s (%s)", row_no, ftype, symbol, res.rejects[-1].reason)
        else:
            res.n_skipped_other += 1

    res.positions = _net_positions(res, raw_positions)
    return res


def _parse_forward(res: ParseResult, positions: RawPositions, row: pd.Series,
                   row_no: int, as_of: str) -> None:
    symbol = _s(row["Symbol"])
    desc = _s(row["Symbol Description"])

    sm = FORWARD_SYMBOL_RE.match(symbol)
    if not sm:
        res.rejects.append(Reject(row_no, symbol, f"Symbol does not match <PAIR><mmddyy>-<id>: {symbol!r}"))
        return
    pair, sym_vd, trade_id = sm.groups()

    dm = DESCRIPTION_RE.match(desc)
    if not dm:
        res.rejects.append(Reject(row_no, symbol, f"Symbol Description does not match FORWARD regex: {desc!r}"))
        return
    td_us, vd_us, verb1, ccy1, verb2, ccy2, rate_s = dm.groups()
    if verb1 == verb2:
        res.rejects.append(Reject(row_no, symbol, f"description verbs are both {verb1}: {desc!r}"))
        return
    sold_ccy, bought_ccy = (ccy1, ccy2) if verb1 == "SELL" else (ccy2, ccy1)
    rate = float(rate_s)
    trade_date = _iso(td_us)
    value_date = _iso(vd_us)

    base_ccy, quote_ccy = pair[:3], pair[3:]
    # Symbol vs description agreement: recorded as a check and rejected on mismatch.
    desc_pair_ok = {sold_ccy, bought_ccy} == {base_ccy, quote_ccy}
    vd_desc_mmddyy = datetime.strptime(vd_us, "%m/%d/%Y").strftime("%m%d%y")
    vd_ok = vd_desc_mmddyy == sym_vd
    res.recon.add("symbol_desc_pair", row_no, symbol, 0.0 if desc_pair_ok else 1.0, 0.0,
                  f"symbol pair {pair} vs description {sold_ccy}/{bought_ccy}")
    res.recon.add("symbol_desc_value_date", row_no, symbol, 0.0 if vd_ok else 1.0, 0.0,
                  f"symbol VD {sym_vd} vs description VD {vd_us}")
    if not desc_pair_ok:
        res.rejects.append(Reject(row_no, symbol,
                                  f"pair {pair} disagrees with description currencies {sold_ccy}/{bought_ccy}"))
        return
    if not vd_ok:
        res.rejects.append(Reject(row_no, symbol,
                                  f"value date {sym_vd} in Symbol disagrees with description VD {vd_us}"))
        return
    try:
        row_quote = cash_ccy(row["Currency"])
    except ValueError as e:
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    if row_quote != quote_ccy:
        res.rejects.append(Reject(row_no, symbol, f"Currency column {row_quote} is not the quote ccy of {pair}"))
        return

    # Blank numeric cells become NaN here and propagate into the checks as failures.
    quantity = float(row["Quantity"])
    local_cost = float(row["Local Cost"])
    price = float(row["Price"])
    fx = float(row["Fx"])
    mv_local = float(row["Market Value Local"])
    mv_base = float(row["Market Value Base"])
    dtd_total = _f(row["DTD Total P&L"])
    dtd_trading = _f(row["DTD Trading P&L"])
    mtd_total = _f(row["MTD Total P&L"])
    start_dirty = _f(row["Start Date Dirty MV"])
    prev_me = _f(row["Previous Month End Market Value Base"])

    # ---- contract checks (CLAUDE.md "Reconciliation checks and tolerances")
    res.recon.add("local_cost", row_no, symbol, abs(quantity * rate - local_cost), TOL_LOCAL_COST,
                  f"Q*rate={quantity * rate:.4f} LocalCost={local_cost}")
    res.recon.add("mv_local", row_no, symbol, abs(quantity * (price - rate) - mv_local), TOL_MV_LOCAL,
                  f"Q*(Price-rate)={quantity * (price - rate):.4f} MVLocal={mv_local}")
    res.recon.add("mv_base", row_no, symbol, abs(mv_local * fx - mv_base),
                  max(TOL_MV_BASE_ABS, abs(mv_local) * TOL_MV_BASE_REL),
                  f"MVLocal*Fx={mv_local * fx:.4f} MVBase={mv_base}")
    res.recon.add("dtd_total_pnl", row_no, symbol, abs(dtd_total - (mv_base - start_dirty)), PNL_TOL,
                  f"DTD={dtd_total} MVBase-StartDirty={mv_base - start_dirty:.4f}")
    res.recon.add("mtd_total_pnl", row_no, symbol, abs(mtd_total - (mv_base - prev_me)), PNL_TOL,
                  f"MTD={mtd_total} MVBase-PrevME={mv_base - prev_me:.4f}")
    res.recon.add("dtd_total_eq_trading", row_no, symbol, abs(dtd_total - dtd_trading), PNL_TOL,
                  f"DTD Total={dtd_total} DTD Trading={dtd_trading}")
    # ---- extra checks beyond the contract list (see EXTRA_CHECKS)
    res.recon.add("direction_sign", row_no, symbol,
                  0.0 if (quantity < 0) == (sold_ccy == base_ccy) else 1.0, 0.0,
                  f"Quantity {quantity} vs sold {sold_ccy}")
    res.recon.add("position_eq_quantity", row_no, symbol, abs(_f(row["Position"]) - quantity), 0.0)
    res.recon.add("trade_factor_one", row_no, symbol, abs(_f(row["Trade Factor"]) - 1.0), 0.0)

    # ---- instrument
    is_ndf = 1 if (quote_ccy in NDF_CCYS or base_ccy in NDF_CCYS) else 0
    res.instruments.setdefault(pair, Instrument(
        instrument_id=pair, asset_class="FX", base_ccy=base_ccy, quote_ccy=quote_ccy,
        multiplier=1.0, is_ndf=is_ndf, bbg_ticker=f"{pair} Curncy", expiry_date=PERPETUAL,
    ))

    # ---- trade + legs
    res.trades.append(Trade(
        trade_id=trade_id, source=SOURCE, instrument_id=pair, product="FX_FWD", package_id=trade_id,
        trade_date=trade_date, quantity=quantity, price=rate, account=_s(row["Account"]),
        counterparty=_s(row["CounterParty"]), strategy=_s(row["NM Strategy"]),
        trader=_s(row["Trader Name"]), description=desc,
    ))
    settles_cash = 0 if is_ndf else 1
    res.legs.append(TradeLeg(trade_id, 1, "FX_NEAR", base_ccy, quantity, trade_date, value_date, rate, settles_cash))
    res.legs.append(TradeLeg(trade_id, 2, "FX_NEAR", quote_ccy, -local_cost, trade_date, value_date, rate, settles_cash))

    positions.append((row_no, _position(as_of, row, pair, value_date, quantity, fx)))


def _parse_currency(res: ParseResult, positions: RawPositions, row: pd.Series,
                    row_no: int, as_of: str) -> None:
    symbol = _s(row["Symbol"])
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
    quantity = _f(row["Quantity"])
    # Fx is blank on zero balances in the reference file; USD is 1 by definition, else 0.0 sentinel.
    fx_raw = row["Fx"]
    if ccy == "USD":
        fx = 1.0
    elif _isnan(fx_raw):
        fx = 0.0
        log.info("row %d: CURRENCY %s has blank Fx (balance %s); fx_to_usd set to 0.0", row_no, symbol, quantity)
    else:
        fx = float(fx_raw)
    positions.append((row_no, _position(as_of, row, instrument_id, as_of, quantity, fx)))


def _parse_future(res: ParseResult, positions: RawPositions, row: pd.Series,
                  row_no: int, as_of: date, as_of_iso: str) -> None:
    """FUTURES row -> instrument + positions row only.

    No trade / trade_legs row is written: the snapshot is one netted row per contract with
    an average cost, not fills. Synthesising a trade here would fabricate trading P&L on
    as_of_date and duplicate the position on every daily load. Futures fills come from
    the xlsx blotter.
    """
    symbol = _s(row["Symbol"])
    try:
        root, code, expiry = future_expiry(symbol, as_of)
    except ValueError as e:
        res.rejects.append(Reject(row_no, symbol, str(e)))
        return
    instrument_id = f"{code} Index"
    multiplier = _f(row["Trade Factor"])
    if multiplier == 0.0:
        res.rejects.append(Reject(row_no, symbol, "FUTURES row has no Trade Factor (multiplier)"))
        return
    contracts = float(row["Quantity"])
    cost = float(row["Cost"])
    price = float(row["Price"])
    mv_base = float(row["Market Value Base"])
    expiry_iso = expiry.isoformat()

    # Extra check (not in the contract list): MV = Q x TF x Price - Cost.
    res.recon.add("future_mv", row_no, symbol, abs(contracts * multiplier * price - cost - mv_base), TOL_MV_BASE_ABS,
                  f"Q*TF*Price-Cost={contracts * multiplier * price - cost:.4f} MVBase={mv_base}")

    res.instruments.setdefault(instrument_id, Instrument(
        instrument_id=instrument_id, asset_class="FUTURE", base_ccy=root, quote_ccy="USD",
        multiplier=multiplier, is_ndf=0, bbg_ticker=instrument_id, expiry_date=expiry_iso,
    ))
    fx_raw = row["Fx"]
    fx = 1.0 if _isnan(fx_raw) else float(fx_raw)
    positions.append((row_no, _position(as_of_iso, row, instrument_id, expiry_iso, contracts, fx)))


def _net_positions(res: ParseResult, raw: RawPositions) -> List[Position]:
    """Net rows sharing the positions primary key.

    The contract says "one row per PB position per day (BNP grain)", but the PK is
    (as_of_date, source, account, instrument_id, settle_date) and the reference file has
    several forwards on one pair / value date / account (e.g. 3 x USDTRY 09/16/26), so the
    BNP grain collides with the PK. The housekeeper has logged this as open question 17
    (docs/open-questions.md). Until it is resolved, rows in a group are netted: mark and
    fx_to_usd must agree within the group (recon failure otherwise, citing the row
    numbers), the other columns are summed.
    """
    out: Dict[tuple, Position] = {}
    rows_in_group: Dict[tuple, List[int]] = {}
    for row_no, p in raw:
        key = (p.as_of_date, p.source, p.account, p.instrument_id, p.settle_date)
        if key not in out:
            out[key] = Position(**vars(p))
            rows_in_group[key] = [row_no]
            continue
        q = out[key]
        first_row = rows_in_group[key][0]
        rows_in_group[key].append(row_no)
        detail = f"rows {rows_in_group[key]} key={key}"
        res.recon.add("positions_net_mark_consistent", first_row, p.instrument_id, abs(q.mark - p.mark), 0.0,
                      f"{detail} mark {q.mark} vs row {row_no} mark {p.mark}")
        res.recon.add("positions_net_fx_consistent", first_row, p.instrument_id, abs(q.fx_to_usd - p.fx_to_usd),
                      0.0, f"{detail} fx {q.fx_to_usd} vs row {row_no} fx {p.fx_to_usd}")
        q.quantity += p.quantity
        q.cost_local += p.cost_local
        q.mv_local += p.mv_local
        q.mv_usd += p.mv_usd
        q.pnl_dtd_usd += p.pnl_dtd_usd
        q.pnl_mtd_usd += p.pnl_mtd_usd
        q.pnl_ytd_usd += p.pnl_ytd_usd
    return list(out.values())


# --------------------------------------------------------------------------- load
def _rows(objs: Iterable) -> List[tuple]:
    return [tuple(vars(o).values()) for o in objs]


# ---------------------------------------------------- duplicate-key content comparison
_CONFLICT_TOL_ABS = 1e-9  # absolute only: CSV -> SQLite REAL round-trips exactly, so no relative band


def _values_match(a, b) -> bool:
    """True if ``a`` (parsed) and ``b`` (existing DB value) are the same. Floats compare
    with a 1e-9 absolute tolerance only (no relative term: a relative band would let a
    1-unit amendment on a large leg pass as a skip)."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            af, bf = float(a), float(b)
        except (TypeError, ValueError):
            return a == b
        if math.isnan(af) or math.isnan(bf):
            return math.isnan(af) and math.isnan(bf)
        return abs(af - bf) <= _CONFLICT_TOL_ABS
    return a == b


def _row_diff(fields: List[str], new_obj, old_row: tuple) -> List[str]:
    """Field names whose value differs (beyond tolerance) between a freshly parsed
    dataclass instance and the existing DB row (same column order as ``fields``)."""
    new_vals = tuple(vars(new_obj).values())
    return [name for name, nv, ov in zip(fields, new_vals, old_row) if not _values_match(nv, ov)]


def load(csv_path: Union[str, Path], conn: sqlite3.Connection,
         as_of_date: Optional[Union[str, date]] = None, strict: bool = True,
         on_duplicate: str = "error") -> ParseResult:
    """Parse and insert. Instruments are upserted; trades / legs / positions error on duplicates.

    Every reject and every recon failure is logged as a warning. With ``strict=True``
    (default) any reject or recon failure raises ValueError before anything is written;
    with ``strict=False`` the parsed rows are inserted anyway (rejected rows are never
    inserted in either mode).

    ``on_duplicate`` controls what happens when a parsed trade / leg / position's primary
    key already exists in the DB:
      - 'error' (default, unchanged behaviour): plain INSERT, duplicates raise
        sqlite3.IntegrityError.
      - 'skip': rows whose primary key already exists in the DB are filtered out before
        insert (so re-running load on the same file is idempotent). A key match whose
        values are identical (within a small float tolerance) is counted in
        ``res.skipped``; a key match whose values differ (e.g. an amended Local Cost /
        Price for a trade_id BNP re-issued) is a CONFLICT: it is counted in
        ``res.conflicts`` and described in ``res.conflict_details``, and the existing DB
        row is left unchanged (the amended row is never applied by 'skip' -- callers who
        want to overwrite must delete/update explicitly).
    """
    from data.ingest.schema import create_schema

    if on_duplicate not in ("error", "skip"):
        raise ValueError(f"on_duplicate must be 'error' or 'skip', got {on_duplicate!r}")

    create_schema(conn)
    res = parse(csv_path, as_of_date)
    name = Path(csv_path).name
    for rj in res.rejects:
        log.warning("%s row %d %s: REJECT %s", name, rj.row_no, rj.symbol, rj.reason)
    failures = res.recon.failures
    for f in failures:
        log.warning("%s row %d %s: recon %s FAIL deviation=%s tolerance=%s %s",
                    name, f.row_no, f.symbol, f.check, f.deviation, f.tolerance, f.detail)
    if strict and (res.rejects or failures):
        head = [f"reject row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in res.rejects[:5]]
        head += [f"recon row {f.row_no} {f.symbol} {f.check}: deviation={f.deviation} tol={f.tolerance}"
                 for f in failures[:5]]
        raise ValueError(
            f"{name}: {len(res.rejects)} reject(s), {len(failures)} recon failure(s); "
            f"nothing loaded (strict=True). First: " + " | ".join(head))

    trades, legs, positions = res.trades, res.legs, res.positions
    if on_duplicate == "skip":
        trade_fields = list(Trade.__dataclass_fields__)
        leg_fields = list(TradeLeg.__dataclass_fields__)
        pos_fields = list(Position.__dataclass_fields__)

        existing_trades = {
            r[0]: r for r in conn.execute(f"SELECT {', '.join(trade_fields)} FROM trades")
        }
        existing_legs = {
            (r[0], r[1]): r for r in conn.execute(f"SELECT {', '.join(leg_fields)} FROM trade_legs")
        }
        existing_positions = {
            (r[0], r[1], r[2], r[3], r[4]): r
            for r in conn.execute(f"SELECT {', '.join(pos_fields)} FROM positions")
        }

        new_trades = []
        for t in trades:
            old = existing_trades.get(t.trade_id)
            if old is None:
                new_trades.append(t)
                continue
            diffs = _row_diff(trade_fields, t, old)
            if diffs:
                res.conflicts["trades"] += 1
                res.conflict_details.append(f"trade {t.trade_id}: {', '.join(diffs)} differ from DB")
            else:
                res.skipped["trades"] += 1
        trades = new_trades

        new_legs = []
        for l in legs:
            key = (l.trade_id, l.leg_no)
            old = existing_legs.get(key)
            if old is None:
                new_legs.append(l)
                continue
            diffs = _row_diff(leg_fields, l, old)
            if diffs:
                res.conflicts["legs"] += 1
                res.conflict_details.append(f"leg {key}: {', '.join(diffs)} differ from DB")
            else:
                res.skipped["legs"] += 1
        legs = new_legs

        new_positions = []
        for p in positions:
            key = (p.as_of_date, p.source, p.account, p.instrument_id, p.settle_date)
            old = existing_positions.get(key)
            if old is None:
                new_positions.append(p)
                continue
            diffs = _row_diff(pos_fields, p, old)
            if diffs:
                res.conflicts["positions"] += 1
                res.conflict_details.append(f"position {key}: {', '.join(diffs)} differ from DB")
            else:
                res.skipped["positions"] += 1
        positions = new_positions

    # Theme inheritance (BUILD_PLAN task B point 4): a new trade with no theme of its own
    # inherits the theme set for its pair in instrument_theme, else stays ''.
    inherited = dict(conn.execute("SELECT instrument_id, theme FROM instrument_theme"))
    trades = [t if t.theme else Trade(**{**vars(t), "theme": inherited.get(t.instrument_id, "")})
              for t in trades]

    with conn:
        conn.executemany("INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?)",
                         _rows(res.instruments.values()))
        conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _rows(trades))
        conn.executemany("INSERT INTO trade_legs VALUES (?,?,?,?,?,?,?,?,?)", _rows(legs))
        conn.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _rows(positions))
    return res
