---
name: ndf-fix-tickers
description: Bloomberg NDF fixing tickers (NDF_FIX_TICKERS) - which returned PX_LAST on a terminal and which loaded but returned nothing; KRW yellow key is an assumption
metadata:
  type: project
---

NDF fixing tickers in `data/ingest/common.py::NDF_FIX_TICKERS`, terminal status 2026-09-22:
BZFXPTAX Index (BRL) and JISDOR Index (IDR) return PX_LAST on fixing dates (verified by
marks on file). KFTC18 Index (KRW) and TAIFX1 Index (TWD) LOAD on the terminal but return
no PX_LAST on the fixing dates, so a ticker that loads is not proof it fixes. The user
replaced them: "kobrusd for korea, try11 index for twd fix". KRW was given without a
yellow key; `Index` is assumed (a wrong key shows as 'Unknown/Invalid security' on the
next pull). INRFBIL Index (INR) still unverified.

**Why:** an NDF's exit price is its own fixing (`PnL = Q x (FIX - f)`); a ticker that
returns nothing leaves the ticket on the spot substitute and the Daily wrong.

**How to apply:** changing a ticker here needs bbg-data to bump
`data/bloomberg/library.py::LIBRARY_VERSION`, or existing databases keep the old ticker
in `bbg_library` until the next upload. `tests/test_live.py` carries "KFTC18 Index" as a
literal in a fake fetch (bbg-data's file, not read from the table). Pin the table in
`tests/test_ingest.py::test_ndf_fix_tickers_are_the_users_verified_fixings`.
