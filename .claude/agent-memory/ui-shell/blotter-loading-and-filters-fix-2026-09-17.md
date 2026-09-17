---
name: blotter-loading-and-filters-fix-2026-09-17
description: Root causes of "blotter sub tabs do not load" (Options HTTP 500 + slow Total render), per-section failure isolation, filters already worked; SAME DAY the BNP_BVAL fallback perf fix was superseded by its outright removal (user: "no bnp fall back") -- read the final section first.
metadata:
  type: project
---

2026-09-17 bug report: "the tabs - especially the blotter sub tabs - they do not load"
and "the filters in all of the blotter part they do not work well", against the
analyst's dev DB (`data/raw/risk.db`, 772 trades, marks only for 2026-08-17 BNP_BVAL).

**Root cause 1 (the actual "does not load" bug): Options sub-tab HTTP 500.**
`ui/tabs/options.py::_leg_rows`/`option_instruments` did `COALESCE(o.payoff, 'VANILLA')`
against `instrument_options`, but this dev DB's copy of that table predated the
`payoff` column (`CREATE TABLE IF NOT EXISTS` in `data/ingest/schema.py` never adds a
column to an already-existing table — a genuine migration gap, since fixed by the
ingest agent for this DB, but any other un-migrated DB will hit it again). The query
raised `sqlite3.OperationalError: no such column: o.payoff` → pandas re-raises as
`DatabaseError` → uncaught all the way up through `ui/tabs/blotter.py::_update`, whose
only `except` clause at the time was `ImportError`. Dash's callback wrapper turns an
uncaught exception into a plain HTTP 500 with **no** `Output` update at all — the
browser just keeps showing whatever the tab showed before (or blank on first load),
which is exactly what "does not load" looks like; there was no error banner anywhere.
**Fix**: `options._instrument_options_columns(conn)` (a `PRAGMA table_info` query, not
cached — cheap, and caching across an actual schema change would hide a real migration
gap) builds the SQL's SELECT clause only from columns that exist, defaulting
`payoff`/`option_type`/`strike`/`barrier_level` to safe literals when missing. Applies
to both `_leg_rows` and `option_instruments` (the terms-editor dropdown hits the same
query shape). **Lesson**: on this project, `CREATE TABLE IF NOT EXISTS` additions to an
existing table in `data/ingest/schema.py` are NOT retroactive on already-created DBs —
any `ui/` read of a table that has grown columns over time should defend against an
older shape rather than assume schema.py's current DDL is what's on disk.

**Root cause 2 (perf, made "load" feel broken even where it didn't 500): a per-row
`.at[]` Python loop in `ui/tabs/blotter_pricing.py::_priced_value_book_uncached`.**
This DB has zero official Bloomberg marks for most dates (only 2026-08-17 BNP_BVAL), so
on any other as_of nearly every one of the 772 rows needs the BNP_BVAL fallback merge.
The old code did `for idx in missing: ... df.at[idx, col] = frow[col]` — 11 columns ×
~772 rows = ~8,500 individual pandas cell writes, repeated once per distinct reference
date `row_scoped_headline`/`row_scoped_period_pnl` evaluate (LTD/T-1/T-2/5d/MTD/YTD = 6
dates), i.e. up to ~50k cell writes for one "Total book" render. cProfile showed this
was ~46% of a 4.4s render, dwarfing the actual SQL cost. **Fix**: vectorised the merge
(`.loc[target_idx, col] = fb_rows[col].values` instead of a per-row loop; duplicate
trade_ids in the fallback frame handled with `drop_duplicates(keep="first")` to match
the old `frow.iloc[0]` behaviour). Cut "Total book" cold-render from ~4.4s to ~0.8-2.1s
on this DB (machine-load-dependent; warm/repeat renders of an already-cached date are
~0.2-0.3s via the existing `lru_cache` in the same module). **Subtlety discovered
writing the regression test**: `note` is itself one of `_MERGE_COLS`, so the merge loop
(old AND new) overwrites a row's `note` with the *fallback* pass's own note **before**
reading it to append the "priced from BNP_BVAL" suffix — a pre-existing official-pass
note is always discarded on fallback, never preserved/prefixed. This is intentional
parity with the original per-row loop's exact operation order, not something this fix
changed; don't "fix" it into preserving the official note without checking with the
coordinator first, since it may be relied on as-is.

**Remaining perf gap, NOT ui-shell's to fix**: after the above, ~75-80% of a cold
"Total book" render is still inside `engine/pnl/valuation.py::value_book`'s **fallback**
pass (`marks_source='BNP_BVAL'`). `_BookConn`'s `official_marks` preload/cache
(commit 60e4e47, "loads a date's marks once instead of one query per trade") only
applies when `_mark_at`'s `source is None` (the official pass) — the explicit-source
fallback path always falls through to one `conn.execute(...)` per (instrument,
settle_date, mark_type) lookup, i.e. still ~1 SQL query per trade when marks_source is
set. On a Bloomberg-less DB (this one), the fallback pass is the ONLY pass that ever
prices anything, so this gap is fully exposed. Reported to pnl-engine/housekeeper:
extending `_BookConn` to also preload `marks` rows for a given non-None `source`
(latest `as_of_date <= as_of` per key, same "load once, not once per trade" idea)
would close this — out of ui-shell's directory (`engine/pnl/` is pnl-engine's), only
described here, not implemented.

**Filters themselves were not broken.** Verified end-to-end via
`app.server.test_client().post("/_dash-update-component", ...)` (the actual level a
prior 2026-09-16 filter bug — see [[blotter-filters-suppress-callback-exceptions-2026-09-16]]
— was only catchable at): instrument_id/side/status/strategy/theme dropdown filters on
Total/Futures narrow both the table (`Output(table_id,"data")`) and the P&L strip
(`Input(table_id,"derived_virtual_data")`, which Dash recomputes as a pass-through even
with no `filter_action`/`sort_action` set, since the strip listens on the *derived*
prop not the plain `data` prop) correctly; "Clear filters" resets all dropdowns; the
row-click detail panel works. The user's "filters ... do not work well" complaint was
most likely just the same slow/500'ing renders above making the whole tab feel broken,
not a distinct filter bug — no separate root cause was found after live testing every
filter control (Futures multi-select confirmed too: `side=['Buy','Sell']` → all 11
rows, `side=['Buy']` → 5 rows).

**New pattern added the same day, per coordinator instruction after this fix landed**:
`ui/tabs/blotter.py::_safe_section(label, builder)` / `_error_card(label, exc)` — every
sub-section of a scope's layout (a P&L strip, a delegated FX/Rates/Options table, the
Total book's asset-class rollup, the filter bar, the trade table, and the Bundles
dispatch in `_update`) is now individually wrapped so one broken piece degrades to an
inline one-line error card (logged server-side via `logging.exception`) instead of
blanking the whole sub-tab. `_safe_section` is a **pure pass-through on success** — on
the happy path it returns `builder()` unchanged, zero extra nesting — specifically so
every pre-existing test asserting on `scope_layout`'s exact returned structure
(`layout.children[0].id == ...`, `next(c for c in layout.children if isinstance(c,
DataTable))`, etc.) kept passing unmodified; only wrap two things that were previously
two separate list items (e.g. filter bar + trade table) as two separate `_safe_section`
calls each, never combine them into one nested Div, or those positional-index tests
break. `_update`'s own top-level `except Exception` (added earlier the same session)
stays as a final defense-in-depth net for anything outside these named sections (e.g. a
date-parsing bug), now rarely the one that actually fires.

**`dcc.Interval` check (coordinator asked)**: none of the three live refresh timers are
in any ui-shell-blotter-owned file — `ui/tabs/blotter.py`, `blotter_fx.py`,
`blotter_bundles.py`, `blotter_pricing.py`, `options.py` have zero `dcc.Interval`
components. Only two exist anywhere in `ui/`: `ui/tabs/cash_ladder.py` and
`ui/tabs/market_data.py`, both `REFRESH_MS = 120_000` (2 min, matching
`data.bloomberg.live.INTERVAL_SECONDS`) — neither owned by ui-shell's blotter lane.
Wherever the third timer the coordinator saw in the log actually lives (header.py?),
it wasn't found in a repo-wide `dcc.Interval` grep at the time of this session — worth
re-checking if it resurfaces, since another agent may have added it mid-session.

See [[blotter-subtabs-2026-09-15]], [[options-tab-phase8-2026-09-17]] for the features
these fixes sit on top of; [[blotter-filters-suppress-callback-exceptions-2026-09-16]]
for the *previous* (different) filter bug and the test_client-level verification
technique this session reused.

**SUPERSEDES root cause 2 above, same day, user decision verbatim "no bnp fall back"**:
after this fix landed, the coordinator relayed a direct user decision that the entire
BNP_BVAL fallback-pricing feature (not just its vectorisation) had to be *deleted*, not
optimised — CLAUDE.md's "Official marks" table already says BNP_BVAL is reconciliation
-only and never feeds P&L, so retrying a missing official mark against it (the whole
premise of "root cause 2" above, live since 2026-09-15) was itself a standing violation
of that rule, independent of how fast the merge ran. **What changed**:
`ui/tabs/blotter_pricing.py::_priced_value_book_uncached` now just calls `value_book`
once and returns `(df, 0, len(df))` — the second `value_book(..., marks_source=
'BNP_BVAL')` pass, `FALLBACK_SOURCE`, `FALLBACK_LABEL`, `_MERGE_COLS` and the
`priced_from_bnp` column are gone entirely (not merely faster). `priced_value_book`
still returns the `(df, n_fallback, n_total)` 3-tuple with `n_fallback` hardcoded `0` —
**kept deliberately** so `ui/tabs/header.py` (not ui-shell's blotter lane; it still
unpacks that tuple and builds its own local "n of m rows on BNP file rates" string)
doesn't break: with `n_fallback` always 0 its `if n_fallback else ""` guard means that
note simply never renders any more, no code change needed there. `ui/tabs/blotter.py`'s
`fallback_caption()` function and `render_headline_strip`'s `caption` parameter were
both deleted outright (the parameter had no other use). On the real dev DB, 2026-09-17
now shows every one of 772 rows unpriced (`reason` set, `pnl_usd` NaN) — including
2026-08-17, which *does* have BNP_BVAL marks on file, also stays fully unpriced (237/237
rows), confirming BNP_BVAL genuinely never feeds P&L any more, by design.

**Lesson for next time — this is the more important half of this memory**: don't
mistake "the user asked me to optimise X" for tacit endorsement of X's existence. This
session vectorised the BNP fallback merge for a legitimate, separately-reported perf
complaint, wrote a regression test suite for it, and reported it as a clean win — all
technically correct, but it never re-examined whether the feature the perf fix was
speeding up should exist at all, even though CLAUDE.md already documented the rule it
violated ("BNP_BVAL is reconciliation only, never official, and never feeds P&L") in the
same file this agent reads at the start of every task. **How to apply**: when a fix
touches code that treats a CLAUDE.md-designated reconciliation-only/non-official data
source as if it priced something, flag the conflict explicitly rather than just
optimising the code as found — don't wait for the user to notice and correct it
separately. This applies broadly to this project: `BNP_BVAL` and `BBG_INTERP` are both
marked reconciliation/fallback-only in CLAUDE.md's "Official marks" table, and any
`ui/`-side code that reads `marks` with an explicit non-None `source=` (bypassing
`marks_official`) should be treated as suspect by default, not assumed intentional.
