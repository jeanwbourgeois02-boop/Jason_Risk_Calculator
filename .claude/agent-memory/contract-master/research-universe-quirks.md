---
name: research-universe-quirks
description: Quirks of the research app's instruments.csv met when seeding config/contracts.csv (2026-09-24) - unit mismatches in the multiplier, placeholder roots, code collisions, delivery from notes
metadata:
  type: project
---

Seeded `config/contracts.csv` on 2026-09-24 from `../Commodity Dashboard/rvapp/universe/instruments.csv`
(202 rows, 11 `bbg_verified`), with a one-shot script (not kept in the repo). Dropped columns:
vat_rate, calendar_depth, foreign_access. Added: multiplier, calendar, delivery.

- **price_scale semantics** (research `validate.py`): price in `quote_unit` = raw Bloomberg price x
  price_scale (0.01 = quoted in cents, 2 = quoted per 500 kg for DCE:JD). So multiplier (currency
  per 1.0 of raw quoted price per lot) = contract_size x units(size_unit -> quote qty) x price_scale.
- **4 rows where size_unit is not the quote unit's quantity**, so plain size x scale is WRONG:
  CME:HE 40000 lb / USD/cwt -> 400 (not 40000); CME:LE -> 400; CME:GF 50000 lb -> 500;
  SGX:TF 5 t / USD/kg at 0.01 -> 50 (not 0.05). The loader recomputes with the research app's
  `quant/units.py` factors and refuses a CSV whose multiplier column disagrees.
- **price_scale unverified** where notes say "if Bloomberg shows cents, price_scale must become
  0.01": NYMEX:B0, NYMEX:C0, NYMEX:MBE, NYMEX:PGP, COMEX:AUP, COMEX:SI. A wrong scale = a 100x P&L.
- **bbg_root 'ZZ...' was a placeholder** (102 rows): all replaced by best guesses on 2026-09-24
  (Phase 2), see [[bbg-root-guesses]]. `ContractRoot.bbg_placeholder` still exists (blotter.py reads it).
- **Code collisions**: exchange codes repeat across exchanges (ZC, SI, SC, SR, NI, PB, SN, HC, JM,
  RB, PL, PS, B, M, ZS, RS) and a code in one namespace can be another root's Bloomberg root
  (CO: LME cobalt code / ICE Brent bbg; C: DCE corn + ICE cocoa codes / CBOT corn bbg; W, LC, LH,
  SH, PT, AA, JA, CA, SM). bbg_root + yellow key IS unique across the file.
- **One-letter Bloomberg roots**: CBOT:ZS 'S', CBOT:ZC 'C', CBOT:ZW 'W', CBOT:ZO 'O' (padded 'C Z26').
- **ICE** in research ids means ICE Futures Europe (calendar ICE_EU); ICEUS is ICE Futures US.
- **delivery**: first pass from the research notes (23 physical, 53 cash); second pass on the
  housekeeper's follow-up from the exchange specifications: CME Group energy / metals / grains,
  ICE softs (US and London), TTF, NBP, EUA, Matif, LME warrant metals (LME:CO too: its note's
  "cash-settled" is CME's cobalt), OSE gold / platinum / rubber, SGX SICOM rubber and every
  Chinese exchange contract physical; Platts / Argus / Fastmarkets / TSI / globalCOAL / PCW / PJM
  index and average-price contracts cash. Result 126 physical, 75 cash, 1 blank (COMEX:ZNC,
  not confirmed; noted in its row). expiry-monitor treats blank as physical.
- active_months is the MAIN-contract cycle, not the listed months (HG lists every month; cycle
  H K N U Z), so resolve never rejects a month outside it.
- Spread YAMLs: 269 hand-written spreads over 5 files (the plan's "264" is not this count);
  every leg instrument is a root in the universe (tested).
