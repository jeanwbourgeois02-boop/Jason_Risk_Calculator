---
name: blotter-futures-key-date-2026-09-25
description: Futures & LME sub-tab key-date header ("Expiry" / "Prompt" / "Expiry / prompt") with per-product hover; the "Settlement" (mark_date) column dropped
metadata:
  type: project
---

2026-09-25, user yes ("can you apply these") to my found-not-done item: the Futures & LME sub-tab's
"Settlement" label was wrong.

- The "Settlement" column was `mark_date`, not `settle_date`. `settle_date` was already labelled
  "Expiry / prompt". On an open future/LME row `mark_date == settle_date` (the key the mark is read at),
  so relabelling it would have produced two identical columns. I dropped the column instead; on a
  SETTLED row `mark_date` is the frozen price's date (realised_pnl.spot_as_of_date = the price's date
  for futures), now the hover of the key-date cell (`_key_date_tip`).
- The header is chosen from the whole scope frame (`futures_key_date_label`, via
  `scope_columns(scope, df)`): "Expiry" futures only, "Prompt" LME only, else "Expiry / prompt".
  The filter callback calls `scope_columns(scope)` without df and only refreshes data/tooltips,
  so the first-render header stands.
- Header hover via `tooltip_header` in `detail_table` (`scope_header_tips`, futures only).

**Why:** the housekeeper's brief assumed the "Settlement" column was the key date; reading the
labels first showed it was the duplicate mark_date. **How to apply:** check which column a label
sits on before relabelling; the row-click panel's "Mark ... dated {mark_date}" was the same misreading;
fixed the same day (`mark_used_text`: "keyed on the expiry/prompt/value date <d>", settled
"frozen at the official price of <d>", closed-out option "closed out on <d>", no mark = the reason). See
[[blotter-screens-redesign-phase-a-2026-09-25]].
