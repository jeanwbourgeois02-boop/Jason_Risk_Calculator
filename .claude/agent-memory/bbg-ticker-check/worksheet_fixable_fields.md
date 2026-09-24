---
name: worksheet-fixable-fields
description: Which worksheet fields contract-master's apply_fixes accepts; option_style / option_lead_months were not fixable on 2026-09-24 (requested)
metadata:
  type: project
---

On 2026-09-24, `data.contracts.FIXABLE_FIELDS` did not include `option_style` or `option_lead_months`. The Phase 5 check still writes rows for them (STYLE_MISMATCH, LEAD_MISMATCH), and `contracts-apply` refuses any such row marked yes as "cannot be fixed" until contract-master adds them. I sent that Request through the housekeeper on 2026-09-24.

LME findings write no worksheet rows. The LME tickers and prompt rules are code in engine/lme (lme-forwards), so they appear under the report's "For the housekeeper" section instead, through `Finding.owner`.

**Why:** the worksheet is only useful for fields apply_fixes can write. Anything else has to go to the owning lane.
**How to apply:** before adding a worksheet field, grep FIXABLE_FIELDS in data/contracts/fixes.py. If the field is missing, raise a Request to contract-master. Related: [[bloomberg-field-assumptions]].
