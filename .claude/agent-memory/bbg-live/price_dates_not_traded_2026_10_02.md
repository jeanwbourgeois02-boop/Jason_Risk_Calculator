---
name: price-dates-not-traded-2026-10-02
description: 2026-10-02 user yes - live futures/listed options asked TRADING_DT_REALTIME + LAST_UPDATE_DT; a price dated before book_today is not written (CARRIED item, status["not_traded"]); why max of two fields; FX/LME left out
metadata:
  type: project
---

User approved 2026-10-02 ("do that"): SHFE shut for Golden Week (1-8 Oct); a press on 2 Oct wrote
SHFE's 30 Sep PX_LAST as an official FUTURE_PX of 2 Oct, so the header's marks chip and Daily read
it as a fresh price.

Built (live.py, pull_marks.py):
- `pull_marks.LIVE_PRICE_DATE_FIELDS = (TRADING_DT_REALTIME, LAST_UPDATE_DT)`, `live_price_date(got)`
  = the LATER of the two. Why both / the later: LAST_UPDATE_DT is in the Terminal's time zone
  (Jason's PC is HK; a NY-zoned Terminal would call an SHFE morning price "yesterday" and falsely
  hold it back), TRADING_DT_REALTIME is the exchange's trading date (tz-free, Globex / SHFE night
  sessions already carry the next day). Taking the later never holds back a price either calls
  today's. Neither field verified on a Terminal: if TRADING_DT_REALTIME pre-rolls on a holiday,
  the stale price is still written (old behaviour, never worse).
- `build_future_rows(price_dates=list)`: opt-in; asks the date fields, the PX_SETTLE fallback reads
  its point's date (`fetch_historical(dates=)`). Earlier day -> not written, entry kind "carried";
  no date -> written, kind "undated". Not passed -> request and rows exactly as before (CLI, tests).
- The combined spot+futures PX_LAST request now asks the date fields too (spots ignore them).
- Status: item status "CARRIED" (new value, never FAILED), `status["carried"]`,
  `status["not_traded"]` {as_of, fields, count, contracts[{instrument_id, name, ticker,
  settle_date, price_date, field, value, sentence}], undated_count, undated, summary}; futures
  step detail and closing sentence add "N not traded since an earlier day (last close carried)".
- Note the PX_SETTLE fallback change: a settle dated before today is no longer written as today's.

Not done (found): FX spot (24/5, and spot feeds the forwards: a false skip would cost the
forwards), LME pillars (request_lme_pillars is bbg-curves' file).

**How to apply:** verify harness = scratchpad verify_price_dates.py pattern (monkeypatch
pm.fetch_reference with per-ticker date fields, pull_once(today=date(2026,10,2)) on a throwaway db).
Tests that assert the combined or live futures request's fields == PX_LAST alone need re-pinning.
Related: [[pull-speed-2026-10-01]], [[one-close-1700-2026-09-28]].
