---
name: calendars-phase1
description: Exchange calendars: the 12 ids, file format, API semantics, coverage ends (LME 2029, others 2028), which dates are estimates, health-audit and test traps.
metadata:
  type: project
---

# Exchange calendars (built 2026-09-24, extended the same day in Phase 5)

- Ids (fixed by the housekeeper, contract-master uses the same): US, ICE_US, ICE_EU, LME, CN, JP,
  SG, EURONEXT, MY, HK, EEX, AE. One file each, `config/calendars/<ID>.txt`, weekday closures
  only, `# coverage: <first> to <last>` header line, `# unverified` after guessed dates.
- Coverage: LME to 2029-12-31 (lme-forwards' 27 monthly pillars reach 2028-12), all others to
  2028-12-31. A test requires every covered year to have dates, so extending the coverage line
  without the year's dates fails.
- Markers: `# estimated  # unverified` = worked out from rules / lunar calendar before the
  official announcement (all CN 2027+, the 2028 lunar and Islamic dates in HK, SG, MY). A test
  requires `# estimated` on every CN line from 2027 on.
- Rules used for estimates: SE Asian Hari Raya usually one day after the Saudi date; MY gives a
  Sunday holiday's in-lieu day to the next free weekday (Tuesday if Monday is taken); CN breaks
  modelled on 2025 (new rules: Spring Festival 4 legal days from the Eve, Labour Day 2), bridge
  days NOT listed; 2028 has a leap fifth lunar month (Mid-Autumn 3 Oct, Dragon Boat 28 May).
- Moon phases / Easter were computed with a Meeus script in the session scratchpad (not kept).
- API in `engine/calendars/__init__.py`: calendar_ids, holidays, coverage, is_business_day,
  add_business_days (n=0 returns d unchanged; start never counted), business_days_between
  (business days in (min, max], signed; antisymmetric), last_business_day_of_month,
  previous/next_business_day (strict). Beyond coverage: weekends only, no raise.
- Least certain data: CN 2027+, AE whole file (UAE holidays commented out, 2028 not worked
  out), MY/SG Islamic and Hindu dates, ICE_EU Easter Monday / Boxing Day / substitutes for
  energy, EEX 24 / 31 Dec. No UK special bank holiday for 2027-2029 known at 2026-09-24.
- ICE_EU is one file for energy and softs; UK bank holidays other than Christmas/New Year/
  Easter left out. Canola (ICE Winnipeg) not covered by ICE_US.
- Health audit traps: YYYY-MM-DD strings in .py comments/docstrings count as dated citations
  (a ratchet); use placeholders or `D(...)` calls. Tests count as importers.
- Test trap: a backward count from a holiday start excludes the start; start round trips on a
  business day.
