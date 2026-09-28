---
name: single-flight-pricing-cache-2026-09-28
description: The shared reader's cache (ui/tabs/blotter_pricing.py) is single-flight per (db path, mtime, date) since 2026-09-28, with a raw and a filled store; werkzeug's request log is at WARNING in ui/launch.py; how to prove both on a scratch database without tests
metadata:
  type: project
---

Since 2026-09-28 `priced_value_book`'s cache prices each (db path, mtime, as_of) once across
every thread (`_single_flight`: a lock per store entry, never one global lock), because the
tabs render concurrently on one page load and `functools.lru_cache` let each of them price
"today" before the first had finished (a dev-database page load: 11 pricings over 6 dates;
the golden book from four threads: 9 -> 6 pricings, one per date). Two stores: `_RAW`
(`value_book` unfilled) and `_FILLED`; the fill's look-back (`_earlier_rows`) reads the raw
store when that close is already priced, else prices the trades asked and nothing more.
`_priced_value_book_cached.cache_clear` still exists (tests/test_ui_revision.py calls it).
`full_pricings_by_date()` is the diagnostic counter. The slow-render warning now also says
how many dates a render waited for another render to price.

Gotcha learned the hard way: the raw and the filled entry of one date share the tuple
`(path, mtime, as_of)`, so the lock table must be keyed on the store too (`id(store)`), or
the filled compute deadlocks on its own lock when it asks for the raw frame.

`ui/launch.py::main` sets the `werkzeug` logger to WARNING right after `basicConfig`, so the
per-request "GET / HTTP/1.1 200" lines stop while the app's INFO / WARNING / ERROR lines print.

**Why:** the user's terminal was drowned in request lines and the same book was priced up to
four times per date on one page load.

**How to apply:** any new screen values a date through `priced_value_book` only (never
`value_book` directly), inside a `pricing_snapshot`; a fill look-back goes through
`_earlier_rows`. Proof without tests: build `tests.golden_book.build_book()` to a scratch
file (`mem.backup(disk)`), wrap `ui.tabs.blotter_pricing.value_book` with a counter by date,
render header `_build_figures`, `book.render`, `pnl.render` per period and
`blotter.scope_layout("total", ...)` from four threads; for the launcher, drive
`ui.launch.main([])` with `werkzeug.serving.make_server` wrapped to capture the server,
`launch.webbrowser.open` patched to make real requests and call `server.shutdown()`,
`launch.PORTS` narrowed, `RISK_DB` / `RISK_LIVE=0` / `RISK_SNAPSHOT=0` set, and grep the
captured output for "HTTP/1.1". Killing a hung proof: Get-CimInstance Win32_Process by
command line, never taskkill python by name.
