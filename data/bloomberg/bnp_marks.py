"""BNP position/P&L CSV -> marks (BNP_BVAL, reconciliation only).

BNP_BVAL is never official (CLAUDE.md "Data contract -> Official marks": SPOT and
FWD_OUTRIGHT are official under BBG_BFXFORWARD). These marks exist so that:
  - the FORWARD rows' own `Price` / `Fx` columns can be reconciled against Bloomberg
    marks once pulled (BNP_BVAL vs BBG_BFXFORWARD), and
  - the pipeline (ladder, P&L) can be exercised end-to-end before Bloomberg marks exist,
    with the explicit understanding that marks_official will be empty until Bloomberg
    marks are loaded (marks_official never selects BNP_BVAL).

Only the FORWARD rows of the BNP file carry a fill/outright (`Price`) and a spot
(`Fx`); CURRENCY and FUTURES rows are not turned into marks here.

Reuses data.ingest.bnp's row-scope helpers (FUND filter, FORWARD_SYMBOL_RE,
DESCRIPTION_RE, _iso, _previous_weekday, file_date_from_name, cash_ccy) rather than
re-implementing them; data/ingest/bnp.py itself is not edited.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import pandas as pd

from data.bloomberg.marks_csv import MarkRow, load_mark_rows
from data.ingest.bnp import (
    DESCRIPTION_RE,
    FORWARD_SYMBOL_RE,
    FUND,
    _iso,
    _previous_weekday,
    _s,
    cash_ccy,
    file_date_from_name,
)

log = logging.getLogger(__name__)

SOURCE = "BNP_BVAL"
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class BnpMarkReject:
    row_no: int
    symbol: str
    reason: str


@dataclass
class BnpMarksResult:
    as_of_date: str
    rows: List[MarkRow] = field(default_factory=list)
    rejects: List[BnpMarkReject] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _snapped_at(as_of: date) -> str:
    """17:00 America/New_York on as_of_date, ISO with the resolved UTC offset for that
    date (e.g. '2026-08-17T17:00:00-04:00'). CLAUDE.md "P&L conventions -> Mark time"."""
    dt = datetime(as_of.year, as_of.month, as_of.day, 17, 0, 0, tzinfo=NY)
    return dt.isoformat()


def extract_bnp_marks(csv_path: Union[str, Path], as_of_date: Optional[Union[str, date]] = None) -> BnpMarksResult:
    """Extract BNP_BVAL FWD_OUTRIGHT and SPOT marks from a BNP PB CSV's FORWARD rows.

    Filter: Fund = NMMF, Financial Type = FORWARD (same as data.ingest.bnp.parse).
    ``as_of_date`` defaults exactly as data.ingest.bnp.parse does (previous weekday of
    the filename date); pass it explicitly on days after a holiday.

    Per FORWARD row:
      - FWD_OUTRIGHT: instrument_id = pair (first 6 chars of Symbol), settle_date =
        value date (from Symbol Description), value = Price.
      - SPOT: instrument_id = pair, settle_date = as_of_date, value = the pair spot rate
        implied by Fx. Fx is quote_ccy -> USD; that is the *only* thing it gives directly:
          * USDXXX (base = USD): spot = 1 / Fx.
          * XXXUSD (quote = USD): Fx is exactly 1.0 by construction (USD -> USD) and
            carries no information about the base currency's USD rate -- Price on this
            row is a forward outright, not a spot -- so no SPOT is derivable from this
            row at all; it is skipped (logged as a warning). Its FWD_OUTRIGHT is still
            emitted.
          * crosses (neither side USD, e.g. EURSEK): Fx alone only gives quote_ccy->USD
            (SEK->USD here). The pair spot = (base->USD) / (quote->USD), where base->USD
            is only available from a *different* row in the file whose quote_ccy equals
            this pair's base_ccy (never from an XXXUSD row, whose Fx is always 1.0). If
            no such row exists, the cross SPOT is skipped (logged as a warning); its
            FWD_OUTRIGHT is still emitted. Not observed in the reference file (which has
            no crosses; every pair has USD on one side).

    Dedupe: identical (pair, settle_date, mark_type) keys collapse to one row silently
    only when the values agree exactly (see tolerance note below). Two rows disagreeing
    on value for the same key are a reject citing both row numbers, never a silent pick.

    Tolerance: FWD_OUTRIGHT values are compared for exact equality (Price is the same
    decimal per row from the source file; no rounding is introduced here). SPOT values
    derived from Fx (rounded to 6dp in the source) are also compared for exact equality,
    since 1/Fx (or Fx) of two textually-identical Fx cells is bit-for-bit identical in
    Python; a tiny float tolerance (1e-9 relative) is applied defensively in case of
    that turning out false on some platform, but is not expected to ever trigger.
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
    snapped_at = _snapped_at(as_of)

    df = pd.read_csv(csv_path)

    # First pass: collect every row's (pair, base_ccy, quote_ccy, value_date, price, fx,
    # row_no) and build a base_ccy -> USD rate map from every FORWARD row in the file
    # (not just those we end up marking), so cross-pair SPOTs can be derived even if the
    # USDXXX/XXXUSD leg that supplies the rate is a different row.
    forward_rows = []
    usd_rate: Dict[str, float] = {"USD": 1.0}
    result = BnpMarksResult(as_of_date=as_of_iso)

    for idx, row in df.iterrows():
        row_no = int(idx) + 2  # header is line 1, matches data.ingest.bnp row numbering
        if _s(row["Fund"]) != FUND:
            continue
        if _s(row["Financial Type"]) != "FORWARD":
            continue
        symbol = _s(row["Symbol"])
        desc = _s(row["Symbol Description"])

        sm = FORWARD_SYMBOL_RE.match(symbol)
        if not sm:
            result.rejects.append(BnpMarkReject(row_no, symbol, f"Symbol does not match <PAIR><mmddyy>-<id>: {symbol!r}"))
            continue
        pair, _sym_vd, _trade_id = sm.groups()

        dm = DESCRIPTION_RE.match(desc)
        if not dm:
            result.rejects.append(BnpMarkReject(row_no, symbol, f"Symbol Description does not match FORWARD regex: {desc!r}"))
            continue
        _td_us, vd_us, verb1, ccy1, verb2, ccy2, _rate_s = dm.groups()
        if verb1 == verb2:
            result.rejects.append(BnpMarkReject(row_no, symbol, f"description verbs are both {verb1}: {desc!r}"))
            continue
        sold_ccy, bought_ccy = (ccy1, ccy2) if verb1 == "SELL" else (ccy2, ccy1)
        value_date = _iso(vd_us)

        base_ccy, quote_ccy = pair[:3], pair[3:]
        if {sold_ccy, bought_ccy} != {base_ccy, quote_ccy}:
            result.rejects.append(BnpMarkReject(row_no, symbol, f"pair {pair} disagrees with description currencies {sold_ccy}/{bought_ccy}"))
            continue

        try:
            row_quote = cash_ccy(row["Currency"])
        except ValueError as e:
            result.rejects.append(BnpMarkReject(row_no, symbol, str(e)))
            continue
        if row_quote != quote_ccy:
            result.rejects.append(BnpMarkReject(row_no, symbol, f"Currency column {row_quote} is not the quote ccy of {pair}"))
            continue

        try:
            price = float(row["Price"])
            fx = float(row["Fx"])
        except (TypeError, ValueError):
            result.rejects.append(BnpMarkReject(row_no, symbol, "Price or Fx does not parse as float"))
            continue

        forward_rows.append((row_no, symbol, pair, base_ccy, quote_ccy, value_date, price, fx))

        # quote_ccy -> USD is exactly what Fx gives, for every row (regardless of pair).
        # For an XXXUSD row this just seeds usd_rate['USD'] = 1.0 (already present) and
        # is otherwise useless: it gives no information about base_ccy -> USD. A cross's
        # base_ccy -> USD can only come from a *different* row where that currency is
        # the quote_ccy; resolved in the second pass below.
        if quote_ccy not in usd_rate:
            usd_rate[quote_ccy] = fx

    # Second pass: emit marks.
    seen: Dict[tuple, tuple] = {}  # (pair, settle_date, mark_type) -> (value, row_no)
    for row_no, symbol, pair, base_ccy, quote_ccy, value_date, price, fx in forward_rows:
        fwd_key = (pair, value_date, "FWD_OUTRIGHT")
        if fwd_key in seen:
            prev_value, prev_row = seen[fwd_key]
            if prev_value != price:
                result.rejects.append(BnpMarkReject(
                    row_no, symbol,
                    f"conflicting FWD_OUTRIGHT for {pair} {value_date}: row {prev_row}={prev_value} vs row {row_no}={price}"))
            # identical: silently dedupe, nothing more to do
        else:
            seen[fwd_key] = (price, row_no)
            result.rows.append(MarkRow(as_of_iso, pair, value_date, "FWD_OUTRIGHT", price, SOURCE, snapped_at))

        # SPOT derivation
        if base_ccy == "USD":
            if fx == 0:
                result.warnings.append(f"row {row_no} {symbol}: Fx is 0, cannot derive SPOT for {pair}")
                continue
            spot = 1.0 / fx
        elif quote_ccy == "USD":
            # Fx here is USD -> USD = 1.0 by construction; it gives no base_ccy -> USD
            # rate, and Price on this row is a forward outright, not a spot. No SPOT is
            # derivable from an XXXUSD row at all.
            result.warnings.append(
                f"row {row_no} {symbol}: cannot derive SPOT for {pair} (XXXUSD pair; "
                f"Fx is quote_ccy->USD which is always 1.0 here and gives no base spot)")
            continue
        else:
            base_usd = usd_rate.get(base_ccy)
            quote_usd = usd_rate.get(quote_ccy)
            if base_usd is None or quote_usd is None or quote_usd == 0:
                result.warnings.append(
                    f"row {row_no} {symbol}: cannot derive cross SPOT for {pair} "
                    f"(base_ccy->USD={base_usd}, quote_ccy->USD={quote_usd})")
                continue
            spot = base_usd / quote_usd

        spot_key = (pair, as_of_iso, "SPOT")
        if spot_key in seen:
            prev_value, prev_row = seen[spot_key]
            if abs(prev_value - spot) > max(1e-9, abs(prev_value) * 1e-9):
                result.rejects.append(BnpMarkReject(
                    row_no, symbol,
                    f"conflicting SPOT for {pair} {as_of_iso}: row {prev_row}={prev_value} vs row {row_no}={spot}"))
        else:
            seen[spot_key] = (spot, row_no)
            result.rows.append(MarkRow(as_of_iso, pair, as_of_iso, "SPOT", spot, SOURCE, snapped_at))

    for w in result.warnings:
        log.warning(w)

    return result


def load_bnp_marks(
    csv_path: Union[str, Path],
    conn: sqlite3.Connection,
    as_of_date: Optional[Union[str, date]] = None,
    strict: bool = True,
) -> BnpMarksResult:
    """Extract BNP_BVAL marks and insert them into ``marks``.

    BNP_BVAL is never official (marks_official always resolves SPOT/FWD_OUTRIGHT to
    BBG_BFXFORWARD): these rows exist for reconciliation and to exercise the pipeline
    before Bloomberg marks exist. Requires ``instruments`` to already contain the pairs
    (i.e. data.ingest.bnp.load must have been run first).

    Insertion is routed through marks_csv.load_mark_rows (the same validation
    load_marks_csv applies to a CSV), not a direct executemany: an unknown
    instrument_id, or a row whose primary key already exists in ``marks`` (e.g.
    re-running load_bnp_marks a second time on the same file/as_of), is a reject with a
    clear reason rather than a foreign-key or IntegrityError raised out of sqlite.

    strict=True (default): any BNP-row-level reject (bad symbol/description, conflicting
    FWD_OUTRIGHT or SPOT values) or any marks_csv-level reject (unknown instrument,
    duplicate primary key) raises ValueError before anything is written.
    strict=False: rejected rows (both levels) are skipped and appended to
    ``result.rejects``; ``result.rows`` is narrowed to only the rows actually inserted;
    everything else is inserted.
    """
    result = extract_bnp_marks(csv_path, as_of_date)
    name = Path(csv_path).name
    for rj in result.rejects:
        log.warning("%s row %d %s: REJECT %s", name, rj.row_no, rj.symbol, rj.reason)
    if strict and result.rejects:
        head = [f"row {rj.row_no} {rj.symbol}: {rj.reason}" for rj in result.rejects[:10]]
        raise ValueError(
            f"{name}: {len(result.rejects)} reject(s); nothing loaded (strict=True). First: "
            + " | ".join(head))

    load_result = load_mark_rows(result.rows, conn, strict=strict, name=name)
    for rj in load_result.rejects:
        result.rejects.append(BnpMarkReject(row_no=rj.row_no, symbol="", reason=rj.detail))
        log.warning("%s row %d: REJECT %s", name, rj.row_no, rj.detail)
    result.rows = load_result.rows
    return result
