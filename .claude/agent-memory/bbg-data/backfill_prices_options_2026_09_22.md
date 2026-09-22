---
name: backfill-prices-options-2026-09-22
description: Why past-day options had Daily 0 and how backfill.py now prices them via engine.options.store.price_close; status "days" block shape is pinned, so options outcome lives in a sibling "options" block; SKIPPED days are never repriced
metadata:
  type: project
---

After a DONE day's closes are written, `backfill()` calls `engine.options.store.price_close(conn, day)`
(options-pricer's function; contract: prices every FX_OPTION with trade_date <= day <= expiry from
that day's own SPOT / forward curve / OIS curve / vol smile on file, INSERT OR REPLACE at the 15:00
close stamp, returns {"day", "priced", "skipped": [{trade_id, reason}], "error"?}). Guard shape mirrors
`_import_realise_settled`: `_import_price_close()` catches ANY exception (a mid-rewrite store.py may not
even parse), `_price_options_close()` never raises. Result keys per day: `options_priced` (None when no
option open OR pricer unavailable/raised), `options_skipped`, `options_note` ('' or the failure).

**Why:** user 2026-09-22: "options daily pnl 0, that cannot be right, everything is moving" / "I expect
every time I pull bloomberg now, the options are repriced with the latest data, and that the latest data
is also logged / overwriting previous marks". Bloomberg PC snapshot had vol_quotes + curves for 09-17..21
but QL_OPTIONS_PRICER marks only for 09-21; `_mark_near` carried the later premium back -> LTD equal on
both days. Nothing new is asked of Bloomberg (no vol/OIS/forward history): hard rule 8 untouched.

**How to apply / traps:**
- The status file's `backfill.days` entries are built from close_completeness with EXACTLY
  {status, missing_count, missing}; tests/test_auto_backfill.py pins that with `==` and `set(entry) ==`.
  Do not add keys there: the options outcome is the sibling `backfill.options` block
  ({day: {priced, skipped, note}}, newest first, MAX_STATUS_DAYS, kept from the last run that worked a day).
- (Superseded later on 2026-09-22, see [[history-inputs-and-futures-settle-2026-09-22]]: a day lacking a
  smile or curve is worked for its inputs even when its marks are complete.) Only a DONE day is priced.
  A day already complete per close_completeness AND holding its inputs is SKIPPED and never repriced,
  so a complete past day with vols on file but no pricer marks stays unpriced until something makes it
  incomplete (the 2026-09-22 15:00 re-stamp run makes every past day DONE once, which covers the snapshot).
  Making completeness aware of pricer marks is inventory.py's decision, not taken.
- On an expiry day price_close runs BEFORE realise_settled (the freeze reads the expiry day's premium);
  with `order` given (auto_backfill) every day is priced in the loop and the single freeze comes after.
- Test fixture that keeps every day DONE with or without an option: one AUDUSD forward whose tenors all
  quote the leg's own SETTLE_DT (EXACT branch) plus a USDJPY option (no conversion pair -> spot fetch of
  the pair alone). `monkeypatch.undo()` inside a test also undoes the autouse pins: use monkeypatch.context().
