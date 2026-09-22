"""Re-exports engine/pnl/calendar.py's business-day helpers under this module's old
name, for backward compatibility with existing callers.

Until 2026-09-17 this module also held the retired workbook-arithmetic aggregation
layer (`aggregate_by_pair`, `book_totals`, `_ltd_total`, `period_pnl`), which called
`engine/pnl/pnl.py` (the retired workbook row arithmetic). Both were deleted the same
day as `pnl.py` itself ("no bnp fall back", docs/bnp-excel-removal.md) -- this module
now holds nothing but the calendar re-export. The calendar helpers
(`load_holidays`, `_is_business_day`, `_prev_business_day`, `_n_business_days_back`,
`_last_business_day_of_prev_month`, `_last_business_day_of_prev_year`, and the
`_NO_HOLIDAYS`/`_DEFAULT_HOLIDAYS_PATH`/`_read_holidays_cached` internals) themselves
moved to `engine/pnl/calendar.py` on 2026-09-17, since they are load-bearing for the
*live* P&L path (`engine.pnl.valuation.value_book`, `engine.pnl.ledger`,
`engine.pnl.fx_blotter`, `data.bloomberg.backfill`, `ui.tabs.header`) and must not share
a file with (or transitively import) workbook-only arithmetic. Re-exported below
(`from engine.pnl.calendar import *`, spelled out explicitly) so every existing
`from engine.pnl.aggregate import load_holidays` (or any other calendar name) caller
keeps working unchanged; new code should import `engine.pnl.calendar` directly."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from engine.pnl import calendar as _calendar
from engine.pnl.calendar import (  # noqa: F401 -- re-exported for existing callers, see module docstring
    _NO_HOLIDAYS, _is_business_day, _last_business_day_of_prev_month,
    _last_business_day_of_prev_year, _n_business_days_back, _prev_business_day,
    _read_holidays_cached,
)

# Kept as this module's OWN copy, not a re-export of engine.pnl.calendar's:
# tests/test_ledger.py monkeypatches `engine.pnl.aggregate._DEFAULT_HOLIDAYS_PATH` -- a
# bare re-export would leave that patch pointing at a name nothing reads any more, since
# `load_holidays`'s code would still resolve its default from `engine.pnl.calendar`'s
# own module globals regardless of what `aggregate.py`'s copy of the name was set to.
# The thin `load_holidays` wrapper below reads THIS copy, so the existing monkeypatch
# target keeps working unchanged.
_DEFAULT_HOLIDAYS_PATH = _calendar._DEFAULT_HOLIDAYS_PATH


def load_holidays(path: Optional[Union[str, Path]] = None):
    """Thin wrapper over `engine.pnl.calendar.load_holidays` that resolves the default
    path from *this module's* `_DEFAULT_HOLIDAYS_PATH` rather than calendar.py's own,
    for the backward-compatibility reason in the comment above. New code should import
    `engine.pnl.calendar.load_holidays` directly (and monkeypatch
    `engine.pnl.calendar._DEFAULT_HOLIDAYS_PATH` in tests) instead of this module."""
    return _calendar.load_holidays(path if path is not None else _DEFAULT_HOLIDAYS_PATH)
