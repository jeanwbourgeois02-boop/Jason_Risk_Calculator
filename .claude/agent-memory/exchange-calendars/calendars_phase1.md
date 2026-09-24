# Exchange calendars, Phase 1 step 1 (built 2026-09-24)

- Ids (fixed by the housekeeper, contract-master uses the same): US, ICE_US, ICE_EU, LME, CN, JP,
  SG, EURONEXT, MY, HK, EEX, AE. One file each, `config/calendars/<ID>.txt`, weekday closures
  only, `# coverage: 2026-01-01 to 2027-12-31` header line, `# unverified` after guessed dates.
- API in `engine/calendars/__init__.py`: calendar_ids, holidays, coverage (added, not in the
  brief), is_business_day, add_business_days (n=0 returns d unchanged; start never counted),
  business_days_between (business days in (min, max], signed; antisymmetric; inverse of
  add_business_days only when both ends are business days), last_business_day_of_month,
  previous/next_business_day (strict). Ids matched after strip().upper(). Beyond coverage the
  calendar answers on weekends alone (no raise): consumers check coverage().
- Least certain data: all CN 2027 (State Council announces late 2026), AE whole file (GME's
  calendar unknown; UAE holidays kept commented out), MY Islamic/Hindu/lunar dates, SG and HK
  2027 lunar dates, ICE_EU Easter Monday and substitute days (energy may trade), EEX 24 / 31 Dec.
- ICE_EU is one file for energy and softs: London softs also close on UK bank holidays, energy
  does not; bank holidays left out. Canola (ICE Winnipeg) not covered by ICE_US.
- Health audit traps: YYYY-MM-DD strings in .py comments/docstrings count as dated citations
  (a ratchet); use placeholders. Tests count as importers for "unimported modules".
- Test-writing trap: a backward count from a holiday start excludes the start (2 Jan 2026 is a
  CN holiday); start round-trip tests on a business day.
