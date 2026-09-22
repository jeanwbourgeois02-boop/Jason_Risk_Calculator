---
name: recalc-status-on-no-bloomberg-press-2026-09-22
description: How a "Pull Bloomberg now" press on a PC without Bloomberg reaches the UI (feed thread always starts, so the line is feed_headline via the fast poll, never not_connected_message), and why the no-feed message stays silent about the options recalc
metadata:
  type: project
---

On a PC with no Bloomberg, a press still runs one cycle: `LiveFeed` is started asleep whether or not Bloomberg answers (`live.start_feed_if_available`), so `click_outcome` wakes it, `pull_once` takes its not-available branch and writes `status["recalc"]` + `status["recalc_summary"]` (the FX options re-priced from the marks on file, user decision 2026-09-22), and the FAST POLL (`poll_outcome` -> `feed_headline`) prints the outcome. `not_connected_message` is reached only with `app.bloomberg_feed is None` (RISK_LIVE=0 / start_feed=False), where the press ran nothing.

**Why:** the 2026-09-22 brief named "click_outcome / the not-connected message" as the line to extend, which conflates the two paths. The summary belongs on `feed_headline`'s not-connected branch; appending a summary from the status file to the no-feed message would claim a press did work it did not.

**How to apply:**
- Keep `not_connected_message` free of recalc words unless a feed actually ran the press. Extend `feed_headline` (flag `say_recalc`) and `market_data.recalc_block` instead.
- The tab shows the sentence ONCE: `status_block` calls `top_bar_status(status, say_recalc=False)` when it renders `recalc_block`, so the pull's own sentence (with its "no Bloomberg on this machine: " head) sits in the block and the headline only names the connection reason. The top bar's line (no block underneath) carries the tail with that head dropped (`feed_controls.RECALC_HEAD`).
- Per-day list = collapsed `html.Details`; skipped reasons both as the `Li`'s `title` and as a nested `Ul`, so a click-test and a hover both find them.
- Found, not done (pre-existing): the tab's own "Pull now" discards `_pending`, so its status block refreshes only through `DATA_REVISION_ID` (a DB write) or the 900 s safety timer. A recalc that prices nothing writes nothing, so the tab can show the old status for up to 15 min while the top bar's line (60 s status refresh) already says otherwise.

Related: [[missing-close-reason-and-feed-cadence-2026-09-21]], [[market-data-tab]].
