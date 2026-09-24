---
name: template-units
description: How config/spreads templates size a leg - qty_factor semantics, same-dimension conversion via contract-master's quantity_factor, and the ratios that look wrong but are not
metadata:
  type: reference
---

`config/spreads/*.yaml` (269 templates, research app copy, all loadable as of 2026-09-24):
- `qty_factor` = spread-quantity units per ONE leg-quantity unit (7.45 bbl per t of gasoil,
  33.3333 bu per short ton of meal, 0.13643 t per bbl of crude). Only given for cross-dimension legs
  (and bushel legs, since the research validator treats `bu` as its own dimension).
- With no qty_factor the engine converts leg quote-unit -> spread unit with
  `data.contracts.universe.quantity_factor` (mass / volume / energy tables): 42,000 gal RBOB = 1,000 bbl,
  25,000 lb HG = 11.34 t, NBP therms -> MWh. Unit strings: spread `unit` 'USD/bbl' -> 'bbl', lower-cased.
- One lot in the spread unit = contract_size x quantity_factor(size_unit, quote qty) x factor.
- Oz-for-oz templates like `sub.pm.gold_vs_silver_comex` (weights 1/-1 in oz: 50 GC per SI) are
  "ratio" spreads the desk quotes as GC/SI; a real dollar-neutral gold/silver trade will never fit
  them and lands in `ratio_off`. Expect the user to bundle those.
- CME board crush package is 10 ZS : 11 ZM : 9 ZL (fits within 1.8 %); 1:1:1 is 17 % off.
