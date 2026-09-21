---
name: ndf-fixing-dates-and-1m-price-2026-09-21
description: NDF rules on the Ladder tab (user decisions 2026-09-21) - fixing-date dating, NDF_1M mark instead of spot, stale is_ndf flag on USDINR, and the judgement calls taken in engine/ladder
metadata:
  type: project
---

User decisions 2026-09-21: "NDFs, show fixing dates instead" and "KRW, IDR, INR, TWD, BRL
... always show 1m forward date price, not spot". Engine side lives in
`engine/ladder/ndf.py`; adapter, usd_marks and exposure read it.

**Data facts**
- The blotter export has NO fixing-date column. Fixing date is computed: value date less
  2 business days on `config/holidays.txt` (via `engine/pnl/calendar.py`). That file has
  no local (KRW/BRL/...) holidays, so a fixing can sit a day from the broker's.
- `NDF_1M` mark: `marks_official`, instrument_id = the USD pair ('USDKRW'), settle_date =
  as_of_date, value as quoted (1394.5), source BBG_BFXFORWARD. Tickers in
  `data.ingest.common.NDF_1M_TICKERS` (KWN / IHN / IRN / NTN / BCN +1M Curncy). Written
  by bbg-data on a pull; the dev risk.db has none.
- INR joined `NDF_CCYS` on 2026-09-21, so an older database still stores USDINR with
  is_ndf = 0 / settles_cash = 1 until the next upload. Decide "NDF ticket" with
  `ndf.is_ndf_pair(pair, stored_flag)` (flag OR currencies), never the flag alone.
  Without that, settled USDINR legs would land in Settled cash as delivered INR.
- Two value dates can share one fixing date only when one is a weekend day; the adapter
  sums them into one record because build_exposure raises on a duplicate
  (trade_id, currency, settlement_date) key and that would blank the whole tab.

**Judgement calls (keep unless the user says otherwise)**
- Both legs of an NDF ticket (the USD leg too) move to the fixing date.
- The realised USD settlement keeps `settled_on` = VALUE date (the day the cash arrives):
  the fixing rule is about currency exposure, the settled row is cash. An out-of-lane
  test (`tests/test_exposure_adapter.py`) also pins that.
- Fixed-but-not-settled NDFs are named through `unresolved` with a reason starting
  'settled at its fixing' (`exposure_adapter.NDF_FIXED_REASON_PREFIX`), because the UI
  caption filters on `reason.startswith('settled')`.
- `build_exposure` itself is NOT strict about NDF currencies needing an NDF_1M entry
  (hand-built fixtures in tests/test_exposure.py pass plain KRW rates); "never spot" is
  enforced where rates are read: `ndf.apply_ndf_1m_rates` drops the entry.

**Why:** an NDF's exposure ends at the fixing; NDF currencies trade off the 1M, and hard
rule 2 means a missing 1M price is a blank with a reason, never spot.

**How to apply:** anything new on the Ladder tab that reads rates must go through
`apply_ndf_1m_rates(conn, rates_from_marks(conn))`. Known, deliberate inconsistency:
`engine/ladder/ladder.py` (contract SQL delta, per-pair Position table) and engine/pnl
stay value-date based and at SPOT, so an NDF that has fixed still shows in the Position
table until its value date, and USDKRW there is at spot.
