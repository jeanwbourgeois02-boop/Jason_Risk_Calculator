---
name: one-close-stamp-2026-09-28
description: Since 2026-09-28 every past mark is a close only at 17:00 New York of its own date (is_close_row); inventory's today/instrument_id args are inert
metadata:
  type: project
---

One close stamp for every mark type and instrument since 2026-09-28 (user decision, commit 3c63b30): a past day's official row counts as a close in `close_completeness` / `lme_curve_status` iff `backfill.is_close_row` says it is stamped 17:00 New York of its own date (compared as an instant, so a `+00:00` stamp of the same moment counts). The 15:00 FX close of 2026-09-21 / 09-22, the intraday floor and the LME-versus-FX distinction are gone.

**Why:** the user wanted one close for everything so Daily is one convention across FX, futures, options on futures and LME; the 15:00 bar path was retired with it.

**How to apply:** inventory.py still passes `today` and `instrument_id` to `is_close_row` (signature kept by bbg-backfill) but neither changes the answer; do not reintroduce a per-instrument close hour in inventory. When re-pinning tests, every past-day stamp must be `T17:00:00` with the New York offset (`-04:00` in summer, `-05:00` in winter); a 15:00 or press-time row is `not_closed`. Today's own rows (day >= today) count whatever their stamp.
