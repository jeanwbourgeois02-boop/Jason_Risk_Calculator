---
name: exchange-holidays-close-completeness-2026-10-01
description: close_completeness drops a past close of a future / option on one / LME pillar on its exchange's holiday (exchange_closed column); FX keeps holidays.txt; today untouched
metadata:
  type: project
---

Since 2026-10-01 `inventory.close_completeness` skips, on a day before `today`, every mark whose key trades on an exchange shut that day: a future or option on one (instruments.base_ccy is the root), an LME metal (key is the root id), its LME_CURVE item too. Calendar = `config/contracts.csv` `calendar` column via `get_root(root).calendar`; `inventory.exchange_shut(cal, day)` says shut only inside `engine.calendars.coverage(cal)`. Skipped items go to a new column `exchange_closed` [{instrument_id, settle_date, mark_type, calendar}]; not in needed / missing / not_requestable.

**Why:** China National Day week 2026 (CN 1-7 Oct): the SHFE close was counted missing, the Marks chip showed a gap and the backfill re-asked hourly for a close Bloomberg never has.

**How to apply:** FX pairs, including a non-USD future's CONVERSION SPOT (USDCNY), keep config/holidays.txt. The live list (`mark_inventory`, `library.needed_on` non-historical) is untouched, so the live pull on a CN holiday still asks SHFE. The backfill's per-day `_needed_marks(historical=True)` is not filtered (it only uses the FX SPOTs from it); futures / LME asks come from close_completeness's `missing` via `backfill._wanted_keys`. `mark_checks` on a past as_of is not calendar-aware yet. A day where every need was exchange-shut has needed 0 (complete False, but `backfill._is_open` and the header treat needed 0 as nothing to do).
