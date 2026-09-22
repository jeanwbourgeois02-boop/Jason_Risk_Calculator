---
name: ledger-refreeze-block-2026-09-22
description: Status-file ledger keys (refrozen / kept / refrozen_count / refrozen_summary) shown as a labelled block on the tab's feed status; where the backfill blocks were guessed to live and must be verified against bbg-data's real shape; Dash components never compare by ==
metadata:
  type: project
---

On 2026-09-22 (user yes) the tab's feed status (`market_data.status_block`, last part after the
recalc block) gained `ledger_block`: per ledger block a "<label>: <refrozen_summary>" line and a
collapsed `html.Details` with one `Li` per re-frozen trade ("trade_id product: USD a -> USD b (why)",
mark_type + spot_as_of_date on `title`) then "kept: trade_id product: reason" lines. Money goes
through `usd_words` (`USD -1,234`, the `notional_words` convention; "n/a" for a non-number).

**Why:** the ledger step (`status["ledger"]`) was never shown on the tab before, only as
"ledger 0.4 s" in the timings line; the re-freeze (a row frozen at a spot or an older fix dropped
and frozen again at the fix) was invisible.

**How to apply:**
- Labels: `status["ledger"]` -> "Ledger"; `status["backfill"]["ledger"]` -> "Backfill closing step"
  when it is itself a block, else a dict keyed by day -> "Backfill <day> ledger";
  `status["backfill"]["days"][d]["ledger"]` -> "Backfill <day> ledger". The backfill locations
  were a GUESS made while bbg-data was still writing them in parallel: check the real
  `pull_status.json` (data/bbg_snapshot/) and `backfill._publish` before relying on the labels;
  if bbg-data put them elsewhere, only `ledger_blocks` needs the extra path.
- A block "says something" only when refrozen / kept / refrozen_count has content, so an older
  file (`ledger` = as_of_date / realised / unrealisable only) renders exactly as before; existing
  tests pin `len(status_block(RECALC_STATUS)) == 3`, so a new part must stay conditional.
- Dash components do not compare equal by value (`Div(...) == Div(...)` is False even when
  identical): assert on `str(component)` when comparing two rendered blocks.

Related: [[recalc-status-on-no-bloomberg-press-2026-09-22]], [[closed-out-count-and-future-close-stamp-2026-09-22]].
