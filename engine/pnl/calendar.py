"""Business-day calendar helpers used by the live headline P&L path.

Split out of `engine/pnl/aggregate.py` on 2026-09-17 (user decision, "no bnp fall back -
that excel and everything linked to it need to go", `docs/bnp-excel-removal.md`), the
same day `engine/pnl/pnl.py` (the literal workbook row arithmetic) and
`aggregate.py`'s own workbook-aggregation functions (`aggregate_by_pair`, `book_totals`,
`_ltd_total`, `period_pnl`) were deleted outright. These calendar helpers are
load-bearing for the *live* path (`engine.pnl.valuation.value_book`,
`engine.pnl.fx_blotter.fx_blotter_rows`, `engine.pnl.ledger`, `ui.tabs.header`,
`data.bloomberg.backfill`) and were moved out first so they would keep working
independently of, and no longer transitively import, the workbook arithmetic being
retired around them.

`engine/pnl/aggregate.py` still re-exports every name below (`from engine.pnl.calendar
import *`-equivalent, done explicitly) for any caller still doing
`from engine.pnl.aggregate import load_holidays` etc. (`engine/pnl/ledger.py`,
`ui/tabs/blotter_pricing.py`, `data/bloomberg/backfill.py`); new code should import
`engine.pnl.calendar` directly.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Optional, Union

_DEFAULT_HOLIDAYS_PATH = Path(__file__).resolve().parents[2] / "config" / "holidays.txt"


@lru_cache(maxsize=32)
def _read_holidays_cached(path_str: str, _mtime: float) -> FrozenSet[str]:
    lines = (line.strip() for line in Path(path_str).read_text().splitlines())
    return frozenset(line for line in lines if line and not line.startswith("#"))


def load_holidays(path: Optional[Union[str, Path]] = None) -> FrozenSet[str]:
    """ISO dates (one per line, '#' comments and blank lines ignored) from
    config/holidays.txt. Missing file -> empty set (Mon-Fri only), never an error.
    `path=None` re-reads the module-level default each call, so tests can monkeypatch
    `_DEFAULT_HOLIDAYS_PATH` instead of a bound default argument.

    Perf (2026-09-17): this is called once per `value_book`/`fx_blotter_rows`/header-
    figures/period_pnl evaluation -- several times per page render -- and used to
    re-read and re-parse the file from disk every single time. The actual read is now
    memoised on `(path, file mtime)`, the same invalidation pattern already used
    elsewhere in this app (`ui.tabs.blotter_pricing._db_cache_key`, `ui.tabs.header.
    _cached_ltd`): a `Path.exists()`/`Path.stat()` check still runs on every call (cheap,
    a single stat syscall) so a file that starts missing, then appears, or is edited, is
    always picked up -- nothing here can go stale."""
    p = Path(path) if path is not None else _DEFAULT_HOLIDAYS_PATH
    if not p.exists():
        return frozenset()
    return _read_holidays_cached(str(p), p.stat().st_mtime)


# --------------------------------------------------------------------- business calendar
# Plain Monday-Friday weekday calendar, `holidays` (from `load_holidays`) subtracted.

_NO_HOLIDAYS: FrozenSet[str] = frozenset()


def _is_business_day(d: dt.date, holidays: FrozenSet[str] = _NO_HOLIDAYS) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def _prev_business_day(d: dt.date, holidays: FrozenSet[str] = _NO_HOLIDAYS) -> dt.date:
    d -= dt.timedelta(days=1)
    while not _is_business_day(d, holidays):
        d -= dt.timedelta(days=1)
    return d


def _n_business_days_back(d: dt.date, n: int, holidays: FrozenSet[str] = _NO_HOLIDAYS) -> dt.date:
    for _ in range(n):
        d = _prev_business_day(d, holidays)
    return d


def _last_business_day_of_prev_month(d: dt.date, holidays: FrozenSet[str] = _NO_HOLIDAYS) -> dt.date:
    last_day_prev_month = d.replace(day=1) - dt.timedelta(days=1)
    while not _is_business_day(last_day_prev_month, holidays):
        last_day_prev_month -= dt.timedelta(days=1)
    return last_day_prev_month


def _last_business_day_of_prev_year(d: dt.date, holidays: FrozenSet[str] = _NO_HOLIDAYS) -> dt.date:
    last_day = dt.date(d.year - 1, 12, 31)
    while not _is_business_day(last_day, holidays):
        last_day -= dt.timedelta(days=1)
    return last_day
