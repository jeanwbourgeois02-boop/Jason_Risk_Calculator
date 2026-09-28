---
name: broker-export-conventions
description: What Jason's real prime-broker export (2026-09-28, data/template PnL tool.csv, git-ignored) taught about symbols, suffixes and price scales; the SGX USD/CNH future row
metadata:
  type: project
---

Jason's real export arrived 2026-09-28 (`data/template PnL tool.csv`, git-ignored, never commit).
89 FUTURE fills, no Currency and no Execution Venue column: the symbol suffix is the only
exchange disambiguator.

- **Symbol form** `<ROOT><M><Y>-<CC>AA`, the broker's own country codes: `-USAA` (CME group,
  MGEX, ICE US), `-UKAA` (LME, ICE Europe), `-CHAA` (SHFE/DCE/ZCE/INE/GFEX; CH is China, not
  Switzerland), `-SPAA` (SGX). Mapped by EXCHANGE set, not the CSV's country column (ICE Europe
  rows carry GB/NL/AE/US countries). `resolve._PB_SUFFIX_EXCHANGES`; unknown suffix narrows
  nothing; a known suffix fitting no candidate rejects (contradiction, hard rule 6).
- **Codes seen** map cleanly: LC=CME:LE, FC=CME:GF, HG, SI=COMEX:SI, HRC, LP=LME:CA, LX=LME:ZS,
  SAI=SHFE:AG, ZNA=SHFE:ZN, SCO=SGX:FEF, XUC=SGX:XUC (new row). Year mostly one digit; LME copper
  came as two-digit `LPZ26-UKAA`.
- **broker_price_scale** (column added 2026-09-28): the broker books cents-quoted contracts in
  whole currency per unit (LC 2.19 vs Bloomberg 219, FC 3.32 vs 332, HG 6.36 vs 636) so that
  lots x contract size x fill = the invoice. 100 on those three (seen) and, inferred from the same
  convention, on every price_scale 0.01 row plus CME:HE (USD/cwt sized in lb): RB, HO, ICE:M
  (pence), SGX:TF, ZS, ZL, ZC, ZW, KE, ZO, MWE, CT, SB, KC, OJ. Blank (1) elsewhere; CBOT:ZR
  (dollars per cwt) and DCE:JD (per 500 kg, scale 2) deliberately left blank: not the cents
  convention. Every other root in the file matched Bloomberg's quote (silver 66.57 USD/oz, SHFE
  silver 16,165 CNY/kg, zinc 26,634 CNY/t, iron ore 97.05 USD/t).
  The parser (ingest-parser) applies it: fill x broker_price_scale = Bloomberg's price; the
  universe's `multiplier` stays per 1.0 of the Bloomberg-quoted price.
- **SGX:XUC** (SGX USD/CNH FX future): sector `fx`, currency CNH, quote_unit CNH/USD, 100,000 USD
  a lot, multiplier 100000, calendar SG, cash. bbg_root `UC` / yellow key `Curncy` is a GUESS
  (SGX product code UC). `sector = fx` and a `Curncy` key load and ticker fine in this package;
  the screens group sectors as Energy/Metals/Agriculture/Ferrous, so `fx` lands wherever they
  put an unknown sector (briefed to ui). Currency narrowing aliases CNH/CNY on BOTH sides.

**Why:** a wrong exchange or a 100x fill is a wrong P&L; the export has no other field to tell them apart.
**How to apply:** a new symbol suffix goes in `_PB_SUFFIX_EXCHANGES`; a new cents-quoted root gets
broker_price_scale 100 and a note saying whether it was seen or inferred.
