---
name: missing-close-reason-and-feed-cadence-2026-09-21
description: Header/blotter "needs the <date> close" caption now reads the backfill's own status block; no screen can run the backfill; feed cadence 900 s and how the UI timers follow it
metadata:
  type: project
---

No screen in the app has a control that runs the Bloomberg backfill. The Market data tab's only Bloomberg buttons are "Pull now" (a synchronous `pull_once`) and "Check Bloomberg connection". The backfill runs by itself after every feed cycle (`LiveFeed` calls `start_auto_backfill`), so "Pull Bloomberg now" is the only user action that leads to one.

**Why:** on 2026-09-21 the user pasted "5d n/a ... run the Bloomberg backfill (Market data tab)" from the Bloomberg PC and asked why it fails: the caption told them to do something impossible. A reason sentence must say what is happening, not name an action, unless a real control exists for it.

**How to apply:**
- A past date's missing-close sentence ends with `ui/tabs/header.py::past_close_explanation(backfill_block, day)`; the block comes from `backfill_status(conn)`, which finds the status file from the connection itself (`PRAGMA database_list` gives the real path even for a `file:...?mode=ro` URI; an in-memory DB gives `''`, so tests on `schema.connect()` get the "nothing known" sentence). `blotter_pricing._reference_missing_reason` reuses both (lazy import, `conn` may be None).
- Status contract read defensively (every key may be absent): `status["backfill"]` = `running`, `remaining`, `reason`, `days: {date: {status: DONE|NO_CLOSES|INCOMPLETE, missing_count, missing[<=5]}}`, `last_run`. `reason` with `running: false` is either the availability text or `"auto-backfill failed: ..."`; the second is worded as a failed run, never as an unreachable terminal.
- `status["timings"]` (seconds per step, `total` = whole pull) shows as "last pull took N s" in `feed_controls.feed_headline` (connected status only) and as one slowest-first line on the Market data tab (`pull_timings_line`).
- Feed cadence is `data.bloomberg.live.INTERVAL_SECONDS` = 900 since 2026-09-21. Tab safety-net timers come from `feed_controls.safety_refresh_ms()` (fallback 900 s), never a typed-in number. The passive status line is capped at 60 s (`status_refresh_ms`) because a FAILED pull never touches the DB, so `ui/revision.py` (5 s poll) cannot show it; the 2 s `PULL_POLL_MS` after a click is independent of the cadence and must stay.
- `cadence_words` now spells whole minutes out ("every 15 minutes", "every minute"); mixed values stay "every 1 min 30 s".

Left for others at the time (outside my lane): `ui/tabs/exposure.py` legend still said "every 2 minutes" and its settled-cash caption "run the Bloomberg pull or backfill"; `docs/README.md` said "started (every 2 min)". Check whether they were fixed before repeating this.

Related: [[header-visible-reasons-and-trade-counts-2026-09-17]], [[header-partial-pricing-2026-09-17]], [[blotter-strips-partial-pricing-2026-09-17]].
