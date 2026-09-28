---
name: by-subsector-conventions
description: Choices behind curve_positions' by_subsector (2026-09-28): the commodity across exchanges keyed by the universe's subsector, mass-unit netting in t > kg > largest-gross unit, fx subsector its own line, golden pin
metadata:
  type: project
---

Built 2026-09-28 on the housekeeper's brief (user: "exposure and pnl grouped by product type - so hrc,
copper etc.", "a lot of trades are long short on the same commodity - the net exposure must be seen").

- Key = `config/contracts.csv` `subsector` ('copper' holds COMEX:HG and LME:CA). Rows with no root
  fall under key '' ("Unknown commodity"), never dropped.
- Physical units net only when every root is sized in a mass unit (`_KG_PER`: t, kg, g, lb, st, lt,
  cwt, oz at exact definitions) or every root shares one unit (so SGX:XUC's 'USD' and a single
  bushel root keep their own unit). Mixed non-mass units -> `net_units` None, reason in `units_note`.
- The target unit is t when any root is sized in t, else kg when any is, else the unit of the root
  with the largest gross USD (`_PREFERRED_MASS`; housekeeper-approved 2026-09-28 after the brief's
  plain largest-gross rule was seen to flip a balanced COMEX/LME copper line between lb and t).
- Keys follow `by_sector`'s names (`net_delta_usd`, `gross_delta_usd`, `delta_missing`,
  `delta_reason`), not the brief's sketch (`delta_usd`, `delta_usd_gross`); `net_delta_lots` is
  left out because lots do not add across exchanges. `months` / `delta_months` are in USD.
- `split` reuses `by_commodity`'s entries (shallow copies with `root_id` added), largest gross first.
- A subsector of sector 'fx' only ever holds its own roots, so it is its own line by construction.
- The golden book pins the whole `curve_positions` output: a new top-level key reads
  "by_subsector: not in golden" in tests/test_golden_book.py until the user says to re-pin (infra).

**Why:** the trader's net exposure per commodity must be visible across exchanges without ever
adding lots of different sizes; hard rule 2 (nothing estimated) carries through the None / reason
pattern of by_sector.
**How to apply:** a change to the unit rule or the key names is an interface change for ui,
risk-metrics, commodity-stress, margin-limits, book-positions; brief them through the housekeeper.
