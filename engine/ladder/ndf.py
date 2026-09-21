"""NDF rules of the Ladder tab (user decisions 2026-09-21).

Two things set a non-deliverable forward apart on the ladder:

1. It is dated on its FIXING date ("NDFs, show fixing dates instead"): an NDF's currency
   exposure ends when the rate is fixed, not when the USD difference is paid. The
   blotter export carries no fixing-date column, so the date is COMPUTED here, on one
   stated assumption: fixing date = value date less 2 business days on the app's only
   calendar (config/holidays.txt through engine/pnl/calendar.py). Local holidays of the
   NDF currency are not in that file, so a fixing can sit a day from the broker's around
   one; FIXING_CAPTION says the rule in the user's words under the grid. P&L is not
   touched: engine/pnl still marks an NDF at its value date's forward.

2. It is valued at Bloomberg's 1-month NDF price, never spot ("always show 1m forward
   date price, not spot"). `apply_ndf_1m_rates` takes the rates dict of
   `data.bloomberg.live.rates_from_marks` and, for every currency in
   `data.ingest.common.NDF_1M_TICKERS`, replaces the SPOT entry with the latest official
   `NDF_1M` mark of that currency's USD pair (latest as_of_date, then latest snapped_at,
   the same rule the spot entries follow). No such mark on file -> the currency has NO
   entry at all, so every consumer's missing-rate path shows a blank with the reason
   (CLAUDE.md hard rule 2: missing stays missing); spot is never used in its place.
   One exception (user decision 2026-09-21: "the NDF ticket that fixes out should be
   handled like settled cash ... priced using spot rate"): the amount of a FIXED NDF
   ticket in the Settled cash row is valued at spot, so the 1M entry also carries the
   currency's official spot as `spot_rate` (absent when there is none on file) for
   engine/ladder/usd_marks.py to read. Every open date is still at the 1M price.

"Is this an NDF ticket" is decided from the pair's currencies against
`data.ingest.common.NDF_CCYS`, not only from the stored `instruments.is_ndf` flag: INR
joined the list on 2026-09-21, and a database loaded before that still carries
is_ndf = 0 / settles_cash = 1 on its USDINR rows until the next upload.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Dict, Iterable, Mapping, Optional

from data.ingest.common import NDF_1M_TICKERS, NDF_CCYS

FIXING_LAG_BUSINESS_DAYS = 2
MARK_TYPE_NDF_1M = "NDF_1M"
FIXING_CAPTION = "NDF rows are dated on fixing dates (value date less 2 business days)."
PULL_BUTTON = "Pull Bloomberg now"


def is_ndf_pair(pair: str, stored_flag: int = 0) -> bool:
    """True when the ticket is non-deliverable: the stored `instruments.is_ndf` flag is
    set, OR either currency of the 6-letter pair is in NDF_CCYS (module docstring: the
    flag alone is stale on a database loaded before a currency joined the list)."""
    if stored_flag:
        return True
    pair = str(pair or "")
    return len(pair) == 6 and (pair[:3] in NDF_CCYS or pair[3:] in NDF_CCYS)


def is_ndf_currency(ccy: str) -> bool:
    return str(ccy) in NDF_CCYS


def currency_label(ccy: str) -> str:
    """Row label of a currency on the grid: 'KRW (NDF)' for an NDF currency, the plain
    code otherwise. Short on purpose (it is the grid's pinned column); FIXING_CAPTION under
    the grid says that NDF rows are dated on fixing dates."""
    return f"{ccy} (NDF)" if is_ndf_currency(ccy) else str(ccy)


def fixing_date(value_date: str, holidays: Optional[Iterable[str]] = None) -> str:
    """Fixing date of an NDF leg, ISO. ASSUMPTION (module docstring): value date less
    FIXING_LAG_BUSINESS_DAYS (2) business days, Monday to Friday less config/holidays.txt
    -- the blotter export has no fixing-date column to read instead. `holidays` overrides
    the file (tests). A value that is not an ISO date is returned unchanged, never
    guessed at."""
    from engine.pnl.calendar import _n_business_days_back, load_holidays
    try:
        day = dt.date.fromisoformat(str(value_date))
    except ValueError:
        return str(value_date)
    cal = frozenset(holidays) if holidays is not None else load_holidays()
    return _n_business_days_back(day, FIXING_LAG_BUSINESS_DAYS, cal).isoformat()


def ticker_label(ticker: str) -> str:
    """'KWN+1M Curncy' -> 'KWN+1M' (the rate row shows 'KWN+1M 1,394.5')."""
    return str(ticker).replace(" Curncy", "").strip()


_LATEST_NDF_1M_SQL = """
SELECT instrument_id, value, source, snapped_at, as_of_date FROM marks_official
WHERE mark_type = 'NDF_1M'
ORDER BY instrument_id, as_of_date, snapped_at
"""


def apply_ndf_1m_rates(conn: sqlite3.Connection, rates: Mapping[str, Mapping],
                       now: Optional[dt.datetime] = None,
                       stale_after_seconds: Optional[int] = None) -> Dict[str, dict]:
    """A copy of `rates` (the `data.bloomberg.live.rates_from_marks` shape) in which
    every currency of NDF_1M_TICKERS is priced at its latest official NDF_1M mark
    instead of spot (module docstring, point 2). The entry keeps the spot entries' keys
    (rate as quoted, inverted = True for a USD-base pair, source, timestamp, stale,
    as_of_date, pair) and adds `mark_type` = 'NDF_1M', `ticker` ('KWN+1M Curncy') and
    `label` ('KWN+1M'). A currency with no usable NDF_1M mark is REMOVED, spot entry
    included: it has no rate, and `missing_rate_reason` says why. Every other currency
    passes through untouched."""
    if stale_after_seconds is None:
        from data.bloomberg.live import STALE_AFTER_SECONDS
        stale_after_seconds = STALE_AFTER_SECONDS
    now = now or dt.datetime.now(dt.timezone.utc)
    out: Dict[str, dict] = {ccy: dict(entry) for ccy, entry in rates.items() if ccy not in NDF_1M_TICKERS}
    pair_ccy = {}
    for ccy in NDF_1M_TICKERS:
        pair_ccy["USD" + ccy] = ccy
        pair_ccy[ccy + "USD"] = ccy
    try:
        rows = conn.execute(_LATEST_NDF_1M_SQL).fetchall()
    except sqlite3.OperationalError:  # no marks_official view on this connection
        rows = []
    for pair, value, source, snapped, as_of in rows:  # ordered ascending: the last row wins
        ccy = pair_ccy.get(pair)
        if ccy is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if not value > 0:
            continue  # an unusable mark is no mark
        stale = True
        try:
            ts = dt.datetime.fromisoformat(snapped)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
            stale = (now - ts) > dt.timedelta(seconds=stale_after_seconds)
        except (TypeError, ValueError):
            pass
        ticker = NDF_1M_TICKERS[ccy]
        out[ccy] = {"rate": value, "inverted": pair.startswith("USD"), "source": source,
                    "timestamp": snapped, "stale": stale, "as_of_date": as_of, "pair": pair,
                    "mark_type": MARK_TYPE_NDF_1M, "ticker": ticker, "label": ticker_label(ticker)}
        spot = rates.get(ccy) or {}
        # Settled cash of a fixed NDF is at spot (module docstring): the spot entry this 1M
        # entry replaces, kept only when it is quoted the same way round.
        if spot.get("mark_type") != MARK_TYPE_NDF_1M and bool(spot.get("inverted")) == pair.startswith("USD"):
            try:
                if float(spot["rate"]) > 0:
                    out[ccy]["spot_rate"] = float(spot["rate"])
            except (KeyError, TypeError, ValueError):
                pass
    return out


def missing_rate_message(ccy: str) -> str:
    """Why one currency has no rate. An NDF currency: its 1M NDF price is missing and
    the pull button fetches it. Any other: no official SPOT."""
    ticker = NDF_1M_TICKERS.get(ccy)
    if ticker:
        return (f"the 1M NDF price for {ccy} ({ticker_label(ticker)}) is missing: press "
                f"\"{PULL_BUTTON}\" to fetch it (an NDF currency is never valued at spot)")
    return f"no official SPOT for {ccy}"


def missing_rate_reason(ccys: Iterable[str], as_of_date: str = "") -> str:
    """One sentence for a list of currencies with no rate (the header's Net / Gross USD
    delta and the Ladder's headline): NDF currencies and spot currencies are named
    apart, each with its own reason. '' for an empty list."""
    ccys = sorted(set(ccys))
    ndf = [c for c in ccys if c in NDF_1M_TICKERS]
    spot = [c for c in ccys if c not in NDF_1M_TICKERS]
    parts = []
    if spot:
        parts.append(f"no official SPOT{f' for {as_of_date}' if as_of_date else ''}: " + ", ".join(spot))
    if ndf:
        tickers = ", ".join(f"{c} ({ticker_label(NDF_1M_TICKERS[c])})" for c in ndf)
        parts.append(f"the 1M NDF price is missing for {tickers}: press \"{PULL_BUTTON}\" to fetch it "
                     f"(NDF currencies are never valued at spot)")
    return "; ".join(parts)
