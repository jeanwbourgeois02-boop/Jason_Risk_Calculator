---
name: settlement-and-option-flags
description: How config/contracts.csv's settlement / option_style / option_lead_months were filled (2026-09-24, Phases 3-5 wave A), which roots were left blank on purpose, and the option id / dated-form rules
metadata:
  type: project
---

Added 2026-09-24 for curve-positions (averaging delta) and the CMDTY_OPTION path. The loader
reads the three columns as optional ('' when a file lacks them).

**settlement = 'average'** (59 roots): SGX iron ore / coking coal, CME TIO / HRC / FSF / EHR,
HKEX FEM, the 7 LME ferrous, ICE coal (NCF, ATW, AFR), the NYMEX Platts/Argus/OPIS swap futures
(DC, 7H, 1N, SG, KS, JA, UN, SE, UV, S5F, R5F, B0, 7E, PS, C0, MBE, PGP), TOCOM JCO, EEX DEBM and
NYMEX JM (month power), the COMEX premiums / alumina / lithium / cobalt / spodumene, CBOT
fertilisers, SGX MEGF / PXF / MTF. Every one is cash-settled (tested).
**Left '' on purpose:** ICE:JKM (averages 16th M-2 to 15th M-1, NOT its contract month, so
`averaging_period` would be wrong); NYMEX:9N (Saudi CP is one monthly posting); INE:EC (last 3
SCFIS prints); CME:BUS (single monthly index, unsure); DCE LF / VF / PPF (average of DCE
settlements, window not known); SGX C5 / BZF / SMCF; BMD:FSOY; HE / GF (index at expiry).

**option_style**: american = CME Group / ICE US / MGEX / Chinese / Euronext physical roots plus
CME HE, GF and ICE B, G, W, RC, C; european = CME HRC, CME TIO, SGX FEF, ICE TFM, ICE M, ICE ECF,
EEX DEBM; '' elsewhere, LME included (unsure whether LME traded options are American or
European), read as AMERICAN with style_source 'ASSUMED'.

**option_lead_months** (months between option expiry and contract month; only the PB dated
form 'CL/A261117C70' needs it): 1 = US-group and Chinese physical roots; 0 = CME HE / LE / GF,
HRC, TIO, SGX FEF, ICE ECF; 2 = ICE B; ICE TFM 1; '' elsewhere -> AmbiguousContract naming the
three candidate months unless `underlying` names the future. Why a column, not one rule: CL/grains
expire M-1, LME and averaging M, Brent M-2; a wrong month is a wrong option ticker = wrong price.

Option dates, in order (changed 2026-09-24 on ingest-parser's request: an expired option never
froze while CL/A261117 read 2026-12-31): (1) Bloomberg's own option date stored in
`contract_static` under 'CLZ26C 70 Comdty' -> BLOOMBERG; (2) the PB dated form's `symbol_expiry`
when strictly earlier than the underlying's last trade date -> dates_source 'SYMBOL', estimated
False (the broker's statement, not a guess); (3) the underlying's last trade date -> ESTIMATED.
A symbol expiry after the underlying's date is still a contradiction. `option_for` (canonical id,
no expiry in it) with `conn` reads `instruments.expiry_date` of the option id as the symbol
expiry, same strict-earlier rule, so it agrees with `resolve_option` (sentinel / bad value / no
table ignored).
Related: [[resolve-rules]], [[research-universe-quirks]].
