---
name: leftover-conventions
description: Choices behind by_subsector's months_units and leftover (2026-09-29): delta units, strategies not outrights, pair residual on the leg carrying its sign, reconciliation, fx hedge rule, lazy spreads import
metadata:
  type: project
---

Built 2026-09-29 for the rebuilt Exposure tab (user question: "Where is my risk, and is each spread
actually as hedged as I think?"; grid = subsector x month, exchange rows under it, Physical default).

- `months_units` / `net_delta_units` / `gross_units` are DELTA units (rows' `delta_units` x the
  subsector factor of `_unit_rule`), so Physical and Lots (`delta_months`) show the same exposure.
  The plain-lots `net_units` stays as it was (futures + LME only).
- Leftover is read from `book_spreads(...)["strategies"]` only (the '' entry holds the unlabelled
  trades, so strategies partition every open lot); `outrights` would double count and is not read.
- A pair's `residual_units` goes on the leg whose units share its sign (the larger side), in that
  leg's month, as residual / (leg units / leg lots). The engine does not name the leg; this is the
  attribution rule. Option residual = net option lots per instrument x the curve row's DELTA.
- Each (root, month) is reconciled: strategies' lots (pair legs + residuals) must equal the curve's
  lots, else None with both figures and a line in `reasons`.
- fx-sector subsector: leftover {} / None / "an FX hedge: not commodity leftover".
- engine.spreads is imported lazily inside `_leftover`; `curve_positions(..., spreads=None)` takes a
  held `book_spreads` result to skip a second read.
- Seen on the sample: UK gas (ICE:M therm vs ICE:TFM MWh) is None throughout because `_KG_PER` is
  mass-only; contract-master's `quantity_factor` could convert (not done, a unit-rule change).

**Why:** hard rule 2 (never a partial or estimated sum); do not re-derive spread matching.
**How to apply:** a change to the residual attribution or the reconciliation is an interface
change for ui; keep the keys in step with the curve_positions docstring. See [[by-subsector-conventions]].
