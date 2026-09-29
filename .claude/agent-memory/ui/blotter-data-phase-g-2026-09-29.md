---
name: blotter-data-phase-g-2026-09-29
description: Phase G round 2 - Blotter (8-column fills, session filter store, See fills) and Data (Problems, four-check Marks check) rebuilt in new modules blotter_fills / data_checks / data_kit; the Dash traps met and the browser proof recipe
metadata:
  type: project
---

Phase G round 2 (2026-09-29), run in parallel with another ui agent (Book, P&L, filter bar, app.py). Built to the
design doc's "Table specs" (Blotter, Data). Where things live now:

- `ui/tabs/data_kit.py`: the one table helper of Blotter and Data (kit classes `book-table book-grid tk-table` in a
  `book-card book-main tk-card`, `tk-strip`, sortable head with pattern ids {"type", "idx"}, `next_sort`
  desc -> asc -> None, `sort_records` (None last), total row `book-total`, check cell ✓/✗, level chips). The look
  pass switches both tabs here.
- `ui/tabs/blotter_fills.py`: fills frame (memo `screen_memo("blotter-fills", extra=research_inputs_key())`, landed
  trade from `shared_trade_book`, contract names via `engine.spreads.trades.leg_name`, FX via `fx_name`), bar,
  table, CSV, last upload line + rejects fold, history fold, `register()`. `blotter.py` keeps the shell,
  `_update` (now 6 outputs: content, built set, subtabs style, fills section style, history, drawer; still
  callable as `_update(as_of, scope)`), Options scope.
- `ui/tabs/data_checks.py`: rows from `inventory.mark_checks` (memo "mark-checks" keyed on `book_today`), problem rows
  (marks MISSING/CHECK + status["steps"] failed/partial + backfill errors/not-numbers + unpriced trades not
  covered), steps / backfill / left-out diagnostics. `market_data.render` now 11 outputs; body callback still
  takes `(as_of, *triggers)`.

**Filter state pattern (works, browser-proved):** session store `blotter-fills-filter` is the truth; the bar is
redrawn from it only on mount / as-of / data / `blotter-fills-bar-rev` bumps (never on its own change, so the
search keeps focus); controls write the store (prevent_initial_call); Clear, the chip and See fills write the
store AND bump bar-rev. See fills reads the other agent's `trade_filter.FILLS_STORE_ID` ({"trade"}), in the
shell, with `prevent_initial_call="initial_duplicate"` and consumes it (writes None).

**Traps:** a callback with ANY Input missing from the page never fires - Clear and the chip (only present after
See fills) must be separate callbacks (a combined one silently did nothing). Mark-check frame text fields come
back NaN, not None: sanitize (`_s`) before `.lower()`. `data.ingest.upload` stores no execution time and no
Trade Id for rows not loaded (asked of ingest-booking).

**Proof recipe:** scratch `ui-g2/`: `build.py` (real.db from the template + a v2 file with a bad symbol and a
PBRoot without suffix; sample.db), `make_data_db.py` (synthetic marks: 100x, +15 % jump, missing, carried; fake
status file with partial/failed steps and backfill errors), `verify_blotter.py` / `verify_data.py` (callbacks
called directly, `context_value.set(AttributeDict(triggered_inputs=[...]))` for ctx), `server.py <db> <port>` +
`cdp.py <port> <devtools> <png>` (headless Chrome over CDP: sort cycle, search, tab switch persistence, Clear,
history toggle, not-loaded link, See fills from the Book, Data sorts). Kill only the server's own PID.
