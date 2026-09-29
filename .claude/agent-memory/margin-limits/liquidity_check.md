---
name: liquidity-check
description: engine/limits/liquidity.py (2026-09-29) - choices not in the brief: weakest leg by level first, known-lots testing with excluded options, lot conversion rule, flat legs dropped
metadata:
  type: project
---

Liquidity check built 2026-09-29 on risk-history's `contract_liquidity` (research app's OI / volume;
on the dev PC the research db is MOCK data, so the sample's all-GREEN result says nothing real).

- Weakest leg = worst level first, then most days to exit (the brief said "highest days_to_exit";
  a leg RED on open interest alone would otherwise be hidden). Position level = weakest leg's.
- A contract with an option lacking delta is tested on the known lots, `incomplete` True and the
  option named in `excluded`; only with no known piece is it NO_DATA. Never GREEN by default.
- Our lots -> research lots only when both lots are in the same unit (ours x our size / theirs);
  different units: compared as they are, said in `note`. No factor for Chinese OI (not verified).
- A contract a position bought and sold back is not a leg; contracts net 0 across the book but
  held by two positions stay (net 0, gross > 0).
- Config `liquidity:` in config/limits.yaml, all placeholders (`placeholder: true`); the user's
  brief supplied the numbers, so they are placeholders by instruction, not invented.

**Why:** hard rule 2 spirit (never silent, never zero for missing) and [[margin-limits-conventions]] labelling.
**How to apply:** keep "research" + "placeholder" labels; when Jason gives thresholds set placeholder false.
