---
name: market-data-whole-book-panels-2026-09-21
description: Market data tab's three whole-book panels (What is missing / Marks that look wrong / Past closes the header needs), the two needs lists and which date picks which, status-file item keys, test-fixture traps
metadata:
  type: project
---

Three user-approved panels (2026-09-21) on the Market data tab, all read-only, filled by the existing `_update_body` through `market_data.whole_book_panels`; each is wrapped in `safe_panel` so one failure stays in its own card.

**Two needs lists, and the date decides which applies.** `data.bloomberg.inventory.mark_inventory` is the LIVE request list; `close_completeness(day, day)` is the PAST-close list the backfill fills. The live list also asks for a FWD_OUTRIGHT at every open option's expiry, which the backfill never writes for a past date by design, so counting a past date with the live list is permanently too high. `ui/tabs/header.py::needed_marks(conn, as_of)` is the one place that chooses (before `live.book_today()` = past-close list; a past non-weekday has no close row and keeps the live list). The header's "(N of M needed marks)" sentence and both panels read it, so they cannot disagree.

**Why:** the header counted past reference dates with the live list and would have contradicted the new "Past closes" panel.

**How to apply:**
- Anything new that counts "needed marks" for a date goes through `header.needed_marks`, never straight to `mark_inventory`.
- "Past closes the header needs" follows the HEADER's as-of date (the `header.AS_OF_STORE_ID` store, mirrored from the Ladder date picker by ui/app.py), added as an Input of `_update_body`; it falls back to `book_today()`. The tab's own date picker defaults to max(trade_date), which is often a PAST date, so "What is missing" opens on a past date unless the user changes it.
- Last pull's `status["items"]`: SPOT items carry settle_date = the pull's date, forwards carry their own settle date and NO as-of, so a reason is only attached when `as_of_marks` (else `as_of_date`) equals the tab's as-of. A past date gets the header's `past_close_explanation` as a caption instead of per-row reasons (the backfill's reasons are not keyed by mark).
- Which trades read which mark: `blocked_by_mark` uses `live.option_spot_pair_names(base, quote)` (public) for the pair + USD-conversion pairs, the same function the needs list is built from. Notional is a face amount off the ticket (|USD leg|, else base amount under its own currency, listed per currency, never converted).
- Bad-tick query is ONE statement over `marks_official` for both dates with a `used` CTE; a correlated EXISTS per forward row would scan trade_legs per tenor row (FWD_CURVE writes ~15-20 tenor rows per pair).
- Test fixtures on `schema.connect()`: foreign keys are enforced, so a SPOT for a cross's USD-conversion pair (EURUSD, USDSEK) needs its instrument row first (the pull creates it via `_ensure_fx_instruments`). Name the columns on `INSERT INTO instruments` / `trades`.
- `tests/test_ui_market_data.py` has a guard that every Input/Output id of this tab's callbacks exists in the full app layout (suppress_callback_exceptions drops a missing Output silently; a missing Input stops the callback firing).
- A long `cat <<'EOF'` append through the Bash tool failed on quote parsing; append test code with the Edit tool.

Related: [[market-data-tab]], [[missing-close-reason-and-feed-cadence-2026-09-21]], [[header-visible-reasons-and-trade-counts-2026-09-17]].
