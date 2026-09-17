---
name: bloomberg-feed-diagnostics-trap-2026-09-17
description: Root cause of the Bloomberg PC's "0 of 0 requested marks" PASS despite 9/9 SPOT + 16/16 FWD + 1/1 FUTURE_PX genuinely missing; fix locations and what's still open elsewhere.
metadata:
  type: project
---

## The trap

On the Bloomberg PC (2026-09-17), the in-app diagnostics reported every FX/futures/rates/
options mark FAILED or missing, but a separate "Last marks pull" check said PASS: "Last
pull at 2026-09-17T15:23:23+08:00 wrote 0 of 0 requested marks."

Root cause: `data.bloomberg.live.start_feed_if_available` spawns `LiveFeed`, whose `_loop`
calls `pull_once` **immediately** on thread start — i.e. the instant `create_app
(start_feed=True)` runs, before the user has had any chance to open the browser and
upload a blotter. `pull_once` builds its request list from `trades_official` at that
instant; with zero trades in the DB it takes the early-return branch (`live.py` around
"no open FX legs or futures to price") and honestly writes `{connected: True, requested:
0, written: 0}`. Nothing then refreshes that status until the next scheduled pull
(`INTERVAL_SECONDS` = 120s later). A user who uploads the blotter and checks diagnostics
inside that window sees the stale empty pull reported as current and trustworthy.

Confirmed via `data.bloomberg.live.build_requests(conn, as_of)` returning the exact 26
requests (9 SPOT + 16 FWD + 1 FUTURE_PX) the diagnostics correctly flagged as missing —
i.e. the *book* needed marks; only the *stored pull status* was stale.

## Fixes made (in bbg-data's scope: data/bloomberg/**)

- `data.bloomberg.inventory.stale_empty_pull_reason(conn, status, as_of)` — new helper.
  Returns `None` when a `connected=True` status is trustworthy; otherwise a plain-English
  reason (uses `_needed_marks` internally, the same set `build_requests` would ask for).
  This is the one place the "requested==0 but marks are needed now" comparison lives; any
  diagnostics surface should call it rather than re-deriving the logic.
- `data.bloomberg.live.LiveFeed.trigger_now()` — new method. Sets a second
  `threading.Event` (`_wake`, separate from `_stop`) that the loop's `wait()` also listens
  on, so a caller can force an immediate extra pull cycle instead of waiting out the rest
  of the interval. **Still needs wiring**: `ui/uploads.py`'s blotter-import callback
  (ui-shell's file, not bbg-data's) must call `app.bloomberg_feed.trigger_now()` after a
  successful `import_blotter()` for this to close the loop end-to-end. Reported to the
  housekeeper 2026-09-17, not done here (out of scope).
- `ui/tabs/market_data.py::_run_bloomberg_diagnostics_placeholder`'s "Last marks pull"
  check now calls `stale_empty_pull_reason` and reports FAIL (not PASS) with the reason,
  "Pull now" and "next automatic pull" guidance. **This placeholder is not what the user
  actually sees** — see below.
- `diagnostics_panel` (same file, currently dead code / unreached by the live UI — see
  [[diagnostics_module_naming]]) now also renders `status["rates"]` /
  `status["options"]` per-currency/per-trade failure reasons via two new helpers,
  `_rates_step_lines` / `_options_step_lines`.

## What's still open (outside bbg-data's edit scope, reported to housekeeper)

**The check that actually runs in production is `tools/bbg_diagnostics.py::check_last_pull`**
(confirmed by `tests/test_ui.py::test_bbg_diagnostics_entry_point_prefers_real_module_now_that_it_exists`
— the real module always wins over the ui/tabs/market_data.py placeholder when it imports
successfully, which it does). That function has the exact same PASS-when-requested-0 bug
and is NOT fixed by anything above, since `tools/` is outside bbg-data's scope. The fix is
mechanical: import `data.bloomberg.inventory.stale_empty_pull_reason` and call it the same
way the market_data.py placeholder now does, converting PASS to FAIL when it returns a
reason. Also worth doing there while in the area: `check_irs_curve_coverage` /
`check_option_coverage` currently only inspect DB state (curve_quotes / marks_official
rows); they could additionally surface *why* the live pull's rates/options step wrote
nothing this cycle by reading `status["rates"]` / `status["options"]` the same way the new
`_rates_step_lines` / `_options_step_lines` helpers in market_data.py do.

## Data/behaviour notes worth remembering

- `pull_once`'s early-return branch (no open FX legs/futures) still runs the rates and
  options steps (`_rates_step`, `_options_step`) before returning — so `status["rates"]`
  and `status["options"]` are populated even on a 0-FX-request cycle. Don't assume that
  branch means "nothing happened."
- `_options_step`'s `status["options"]["skipped"]` is a **string** ("no FX_OPTION trades
  to price") when there is nothing to do at all, but a **list of dicts**
  (`{trade_id, reason}`) when some trades were skipped individually. This asymmetry is
  covered by an existing test (`test_pull_once_prices_irs_from_injected_rates_source` in
  tests/test_live.py asserts the string form) — don't "fix" the type without checking that
  test; any renderer must handle both.
- `trades_official` excludes `source='BNP'`; the blotter always writes `source='XLSX'`, so
  this was **not** the root cause here (ruled out explicitly) — the blotter's trades do
  reach `build_requests` once loaded, they just weren't loaded yet at the moment of the
  first pull.
