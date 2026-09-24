---
name: phase5-options-lme-rows
description: How CMDTY_OPTION and LME_FWD join the expiry schedule (2026-09-24): estimated options always held early (lead-month aware), LME alert = prompt less 2 LME bd, positions split by leg date
metadata:
  type: project
---

Built 2026-09-24 (housekeeper brief, Phase 5):
- CMDTY_OPTION: event 'option expiry'. An estimated option date is contract-master's underlying
  LTD, always after the real expiry, so EVERY estimated option (cash or physical underlying) is
  held early, from the first business day of max(1, option_lead_months) months before the
  option's contract month (ICE Brent lead 2). Futures stay physical-only, lead 1. Physical
  underlying: reason names the future's first notice / last trade.
- LME_FWD: one instrument ('LME:CA') holds many prompts; positions are split by the legs' date
  (`_SPLIT_BY_LEG_DATE`). Row contract_id 'LME:CA 2026-12-16', dates_source 'TICKET', lots =
  tonnes / lot_tonnes, alert = prompt less `LME_CASH_DAYS` (2) on the LME calendar.
- Futures rows keep exactly their old key set: the golden book pins `expiry_schedule`, so new
  keys go only on option / LME rows.

- Option dates_source can also be 'SYMBOL' (contract-master, 2026-09-24): `option_for(..., conn)`
  reads instruments.expiry_date and uses it when earlier than the estimate; not estimated, alerts
  on that date. Always test `.estimated`, never `dates_source == 'BLOOMBERG'`. A test wanting the
  estimated path must book the option's expiry_date at (not before) the estimate.

Quirk: exchange-calendars extended US coverage to 2028-12-31 the same day; a coverage test must
use a contract past the calendars' coverage (CLZ29), not a fixed "far" one.

**Why:** a paper trader must never hold into delivery / exercise; see [[estimated-dates-understate-risk]]
and [[settled-contracts-leave-schedule]].
**How to apply:** keep futures rows byte-identical unless the user approves a golden re-pin.
