---
name: historical-backfill-fwd-future-2026-09-18
description: backfill.py extended to also write FWD_OUTRIGHT/FUTURE_PX history (not SPOT alone); the traded_pairs USD-only gap that silently blocked crosses AND futures; BBG_INTERP becoming an official fallback for FWD_OUTRIGHT.
metadata:
  type: project
---

Large follow-up task (2026-09-18) closing the gap CLAUDE.md's "P&L conventions" implies:
Daily/5d/MTD/YTD difference LTD(t) against LTD(t-1bd) etc, and every FX leg's LTD needs
the FWD_OUTRIGHT for its own settle_date (never a shared date), every future's needs
FUTURE_PX -- `backfill.py` used to write SPOT only, so LTD(t-1bd) was unpriced for every
forward/future on a fresh Bloomberg PC.

## What changed

- `data/bloomberg/pull_marks.py`: new `fetch_historical_series(session, service, tickers,
  fields, start, end)` -- unlike `fetch_historical` (keeps only the most recent point),
  this keeps every day's point, for backfill's "one HistoricalDataRequest per ticker set
  over the whole range" pattern. Confirmed real Bloomberg behaviour by reading
  `rates_marketdata.py::RatesBloombergSource._fetch_historical_series` (a working
  precedent already in this codebase): each historical point carries its own "date"
  element separate from the requested field.
- `data/bloomberg/fwd_curve.py`: new `historical_points_by_day(tenor_series,
  tenor_tickers)` assembles `{date_iso: [(settle_date, outright), ...]}` from a pair's
  standard-tenor tickers' historical PX_LAST + SETTLE_DT (UNVERIFIED: whether a rolling-
  tenor ticker's SETTLE_DT is available/correct via HistoricalDataRequest the same way it
  is live via ReferenceDataRequest -- Bloomberg's bulk FWD_CURVE field itself has no
  historical equivalent, a limitation `pull_marks.py`'s own module docstring already
  documented for the live tenor-fallback path). `outright_for_date` (unchanged) does the
  actual interpolation, reused exactly as the live path uses it.
- `data/bloomberg/backfill.py`: `backfill()` now also computes FWD_OUTRIGHT (leg settling
  today -> that day's own SPOT, matching the live rule; otherwise historical-curve
  interpolation, `SRC_INTERP`/`BBG_INTERP` when not an EXACT tenor match) and FUTURE_PX
  (historical PX_SETTLE) per day, batched over the whole `todo` span rather than per-day.
  A row already official is never overwritten (`_drop_already_official`, checked per-row,
  not just at the day-skip level -- covers a day that's incomplete for a NEW reason but
  already has an old official row for something else). Day-selection now goes through
  `inventory.close_completeness` (SPOT+FWD_OUTRIGHT+FUTURE_PX), not a bespoke
  `_has_all_closes`, so day-selection can never drift from what live.py/inventory
  consider "needed".
- `data/bloomberg/inventory.py`: `close_completeness` rewritten to use `_needed_marks`
  (the full per-day set) instead of a hard-coded SPOT-only/USD-pairs-only count; gained a
  `missing` column (list of the actual missing items) for diagnostics. `_needed_marks`
  also now folds in FX_OPTION needs via `live.option_needed_marks` (landed concurrently
  by another session; called via `getattr(live, "option_needed_marks", None)` so this
  module never hard-depends on a function that might not exist in a given working tree).

## Two real bugs found only by tracing the reference book's actual data, not by
## reasoning about the code in the abstract -- worth remembering the technique

1. **`traded_pairs()` was USD-pairs-only, and this had two compounding effects, not one.**
   A cross like EURSEK (33 forwards in the reference book) never got a historical SPOT
   close at all, so `engine.pnl.ledger.realise_settled` (needs the pair's OWN official
   SPOT on or before settlement) could never freeze a settled EURSEK forward. *Separately*,
   `backfill()`'s very first line was `if not pairs: return []` -- for a book with EURSEK
   trades but no *direct* USD-pair FX trade at all, this bailed out before ever reaching
   the future/forward logic, so ESU6's FUTURE_PX was silently never attempted either, for
   a reason that had nothing to do with FUTURE_PX's own logic. Fixed by rewriting
   `traded_pairs(conn, start, end)` to return every FX instrument with a leg open in the
   range (crosses included, via the same range query the forward-curve fetch already
   used) plus every cross's USD-conversion pair (mirrors `live._cross_usd_legs`, unioned
   over the range) -- and by widening the upstream bailout to also check for open futures
   before giving up.
2. **The `NO_CLOSES` day-skip fired for a futures-only book even when a future genuinely
   needed pricing that day.** `tickers == []` (no FX pairs at all) makes `spot_rows`
   trivially empty every day, which used to be read as "holiday, nothing written" and
   skipped the FWD_OUTRIGHT/FUTURE_PX logic too. Fixed: only treat it as NO_CLOSES when
   `tickers` is non-empty AND nothing came back for them.
   - Related, and worth remembering generally: **a future's own expiry day (settle_date
     == as_of) IS "needed" and must be priced**, not excluded the way a *forward's* past
     settle date is (`live._OPEN_FUTURE_SQL`/`_OPEN_FX_SQL` both use `settle_date >=
     as_of`, inclusive) -- "after expiry the freeze wants the last official FUTURE_PX on
     or before the expiry day, or a PC first connected after expiry leaves it unpriced
     forever." This one turned out to already work correctly once (1) was fixed (the
     `>=` boundary was already right); it was the upstream bailout and the NO_CLOSES
     misfire that were actually blocking it. Don't assume a reported symptom points at
     the code nearest the symptom -- trace the whole call path first.

## BBG_INTERP became an official fallback mid-task (another session's concurrent change,
## data/ingest/schema.py, confirmed real and correct -- adjusted to it, did not fight it)

2026-09-18 user decision, landed by another session while this task was in progress:
`OFFICIAL_FALLBACK_SOURCE = {"FWD_OUTRIGHT": "BBG_INTERP"}` -- a BBG_INTERP row is now
official via `marks_official` wherever no BBG_BFXFORWARD row exists for the same
`(as_of_date, instrument_id, settle_date, mark_type)` key (audit finding: 720 of 743
forwards in the reference book had no P&L before this, since a broken-date leg's only
Bloomberg-servable value ever *is* the interpolated one). This directly affects
`close_completeness`: a day needing only an interpolated FWD_OUTRIGHT can now read
complete, which it structurally could never do before this landed. `inventory
.STATUS_INTERP` is consequently only reachable for a BBG_INTERP row of a mark_type with
no fallback entry (currently none). `backfill.py`/`live.py` still write the *actual*
source (BBG_BFXFORWARD for an exact tenor hit, BBG_INTERP for an interpolated one) --
the view, not the writer, is what decides official-ness now.

**Lesson**: CLAUDE.md as read at a session's start can go stale mid-session in a
multi-agent shared tree -- a concurrent commit landing a genuine, reasoned user decision
is not something to "correct" back to the older text; verify against the current code
(`git show origin/main:<file>`, or the file on disk if already merged into the shared
working copy) and adapt, the same as with any other memory-vs-reality conflict.

## Lane discipline in a fast-moving multi-session task

This task's coordinator narrowed/widened bbg-data's file lane THREE times over its
course as a second concurrent Bloomberg session (adding FX_OPTION support to live.py
etc.) landed changes to the same area: first a broad "your lane" list, then an explicit
"do NOT touch these files, another session owns them right now" list (live.py,
rates_marketdata.py, vol_marketdata.py, engine/options/store.py, data/ingest/schema.py,
2_launcher.py, docs/bloomberg-pc-checklist.md, tests/test_live.py,
tests/test_options_pricing.py), then follow-up integration instructions once that
session's commit (`d5c87aa` on origin/main) was ready to be relied on (`live
.option_needed_marks`, `live.patch_status`). Each message was internally consistent and
narrowed rather than contradicted the one before it. Practical pattern that worked: use
`getattr(live, "some_new_function", None)` with a `[]`/no-op fallback for anything the
other session was concurrently adding, so this module's tests stay green regardless of
exactly when the other session's function lands in the shared working tree; confirm the
real signature via `git show origin/main:<path>` (read-only, never `git pull`) once told
it's landed, rather than guessing the shape up front.
