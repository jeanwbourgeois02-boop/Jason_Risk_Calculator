---
name: usd-notional-can-be-none
description: Since 2026-09-24 fx_blotter_rows' quantity_usd_notional can be None with notional_reason; the FX sub-tab shows it as n/a + hover, label "Quantity (USD)", never summed
metadata:
  type: project
---

pnl-series (Phase 3, 2026-09-24) made `quantity_usd_notional` USD for every row (a future's converted at spot of as_of) and None where it cannot be, with the why in the new last column `notional_reason`.

**Why:** futures in CNY / EUR / GBP / JPY had been shown as if their local notional were dollars; a blank must now carry its reason (hard rule 2, "no figure blank without its reason").

**How to apply:** the FX sub-tab passes `products=FX_PRODUCTS`, so no future ever reaches it; the change mattered here only as None-safety. A None notional reads `UNPRICED_TEXT` ("n/a") with `notional_reason` (else `NO_NOTIONAL`) as tooltip and gets the muted-italic rule (7 style rules now, pinned in tests/test_ui.py). The notional is display only: it is not in `_HIDDEN_COLUMNS`, so nothing here ever sums it. If futures ever return to this sub-tab, the "(USD)" label is still right. See [[fx-swap-tag-kept-phase2]].
