---
name: family-and-close-times
description: contracts.csv family and close_time columns (2026-09-29, Phase G, for spreads-engine); how families were chosen, which close times are guesses; research rv.sqlite on this PC is MOCK data
metadata:
  type: project
---

Added 2026-09-29 on spreads-engine's request (engine/spreads/trades.py had stand-ins `_FAMILY_OF_*`, `_CLOSES`).

- **family** (every row, validated against `universe.FAMILIES`; a file without the column falls back to the
  subsector): every ferrous-sector root is `ferrous`; otherwise coarse desk groups by subsector: products
  (gasoline, gasoil, jet, naphtha, fuel oil, bitumen), grains (corn, wheat, oats, rice, starch), oilseeds (soy
  complex, palm, rapeseed, peanuts), softs (cotton, sugar, coffees, cocoa, OJ), cattle (LE+GF), aluminium
  (+alloy, premiums, alumina), lithium, silicon, rubber (natural + butadiene), glass (soda ash + flat glass),
  polyester chain, plastics, fx (SGX:XUC). Gold, silver, platinum, palladium stay separate (the user's brief
  listed gold and silver apart), so a gold/silver ratio is cross-family.
- **close_time**: per-root override ('HH:MM Area/City', blank = `EXCHANGE_CLOSE[exchange]`); `ContractRoot.close`,
  `exchange_close()`. Meant as the settlement time PX_LAST reflects. Fairly sure: Chinese 15:00 day session, CBOT
  13:15 CT, NYMEX 14:30 ET, COMEX gold 13:30 / copper 13:00 / silver 13:25 ET, Brent 19:30 London, Matif 18:30
  Paris, CME livestock 13:00 CT. GUESSES: LME 17:00, ICE Europe default 17:30, SGX 19:00 (SGX:XUC's FX-future
  settlement likely earlier, left at default), CME HRC / TIO (exchange default 13:00 CT), HKEX, EEX, GME 12:30 Dubai.
  Changed vs the stand-in: ICE 19:30 -> 17:30 (Brent row keeps 19:30), GME 23:00 -> 12:30, CME 13:05 -> 13:00.

**Research DB is mock on this PC**: `../Commodity Dashboard/var/rv.sqlite` job 1 has provider 'mock'
(rvapp/data/mock.py, cosmetic anchor levels, e.g. CME:HRC 850). Every root is off Jason's fills (HRC 640 vs
1,285; SI 27 vs 66.6; HG 317 vs 636; SHFE:AG 6,902 vs 16,165). A "units" mismatch against it is not a
contract-definition problem: CME:HRC (20 st, USD/st, scale 1, mult 20) matches the research app's own row.
**How to apply:** check `SELECT provider FROM job` before blaming a unit; see [[research-universe-quirks]].
