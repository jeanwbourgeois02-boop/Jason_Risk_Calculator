"""engine/pnl/aggregate.py: business-calendar helpers, notably `load_holidays`'s
2026-09-17 perf fix (complaint B). `load_holidays` is called several times per page
render (`value_book`, `fx_blotter_rows`, header figures, `scoped_period_pnl` all pull
it in) and used to re-read+re-parse config/holidays.txt from disk on every single
call; it is now memoised on `(path, file mtime)` -- these tests pin both the caching
(repeated reads of an unchanged file are cheap/consistent) and correctness (a changed
file, or a missing one, is never served stale).

Also same-day: the actual calendar implementation moved to `engine/pnl/calendar.py`
(the "no bnp fall back" split, see that module's docstring); `aggregate.load_holidays`
is now a thin wrapper kept here for backward compatibility with existing callers
(notably `tests/test_ledger.py`'s `monkeypatch.setattr("engine.pnl.aggregate.
_DEFAULT_HOLIDAYS_PATH", ...)`, not owned by this agent). `tests/test_calendar.py`
covers the calendar module directly."""
from __future__ import annotations

from engine.pnl.aggregate import load_holidays, _read_holidays_cached


def test_load_holidays_reads_dates_ignoring_comments_and_blanks(tmp_path):
    p = tmp_path / "holidays.txt"
    p.write_text("2026-01-01\n# New Year\n\n2026-12-25\n")
    assert load_holidays(p) == frozenset({"2026-01-01", "2026-12-25"})


def test_load_holidays_missing_file_is_empty_set_not_an_error(tmp_path):
    assert load_holidays(tmp_path / "does-not-exist.txt") == frozenset()


def test_load_holidays_repeated_calls_return_equal_result(tmp_path):
    p = tmp_path / "holidays.txt"
    p.write_text("2026-07-04\n")
    first = load_holidays(p)
    second = load_holidays(p)
    assert first == second == frozenset({"2026-07-04"})


def test_load_holidays_picks_up_a_changed_file_not_a_stale_cache(tmp_path):
    """The whole point of keying the cache on mtime rather than path alone: editing the
    file (a real scenario -- config/holidays.txt is hand-maintained) must be reflected
    on the very next call, not just after a process restart."""
    p = tmp_path / "holidays.txt"
    p.write_text("2026-07-04\n")
    assert load_holidays(p) == frozenset({"2026-07-04"})
    # Bump the mtime explicitly (some filesystems have coarse mtime resolution) so this
    # test is not flaky on a fast machine that rewrites within the same tick.
    import os
    p.write_text("2026-07-04\n2026-12-25\n")
    new_mtime = p.stat().st_mtime + 1
    os.utime(p, (new_mtime, new_mtime))
    assert load_holidays(p) == frozenset({"2026-07-04", "2026-12-25"})


def test_load_holidays_two_different_paths_do_not_collide(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("2026-01-01\n")
    b.write_text("2026-02-02\n")
    assert load_holidays(a) == frozenset({"2026-01-01"})
    assert load_holidays(b) == frozenset({"2026-02-02"})


def test_read_holidays_cached_is_a_real_lru_cache(tmp_path):
    """Directly exercises the memoised helper: same (path, mtime) key returns a cached
    hit, visible via `cache_info().hits` -- the mechanism the perf fix relies on."""
    p = tmp_path / "holidays.txt"
    p.write_text("2026-03-17\n")
    _read_holidays_cached.cache_clear()
    mtime = p.stat().st_mtime
    _read_holidays_cached(str(p), mtime)
    before = _read_holidays_cached.cache_info().hits
    _read_holidays_cached(str(p), mtime)
    after = _read_holidays_cached.cache_info().hits
    assert after == before + 1


def test_load_holidays_default_path_still_reads_repo_config():
    # No path override: exercises the `_DEFAULT_HOLIDAYS_PATH` branch end to end.
    holidays = load_holidays()
    assert isinstance(holidays, frozenset)


def test_load_holidays_still_honours_a_monkeypatched_aggregate_default_path(tmp_path, monkeypatch):
    """Pins the exact backward-compatibility case the calendar.py split had to
    preserve: existing test code (tests/test_ledger.py::test_holiday_shifts_prev_
    business_day, not owned by this agent) monkeypatches
    `engine.pnl.aggregate._DEFAULT_HOLIDAYS_PATH` and expects `load_holidays()` (no
    path argument) to honour it -- a bare re-export of `engine.pnl.calendar.
    load_holidays` would silently break this, since that function resolves its
    default from calendar.py's own module globals, not aggregate.py's."""
    p = tmp_path / "holidays.txt"
    p.write_text("2026-09-11\n")
    monkeypatch.setattr("engine.pnl.aggregate._DEFAULT_HOLIDAYS_PATH", p)
    assert load_holidays() == frozenset({"2026-09-11"})
