---
name: unrecognised-trades
description: value_book's status 'UNRECOGNISED' rows (2026-09-29): never filled (fill_book skips them), counted as unpriced "excl. N" everywhere, never in fx_blotter
metadata:
  type: project
---

Since 2026-09-29 `value_book` returns one row per product-'UNRECOGNISED' trade (a blotter row whose
contract is not in the universe; no legs, never marked): status 'UNRECOGNISED', every figure NaN,
reason "contract not recognised: ...; P&L can't be computed until it is mapped".

**Why:** hard rule 6 / "every row loads": a row the app cannot identify is kept and shown, never dropped.

**How to apply:**
- `reference.fill_book` skips it by status (`_NEVER_FILLED_STATUS`) and by reason prefix
  ("contract not recognised:" in `_NEVER_FILLED`): no earlier price can exist, so no look-back calls.
- Periods, months, track record: it is a_unpriced in `diff_split`, so "excl. N" with its reason and
  never blocks a reference close. Adding one to the sample book leaves every total unchanged (verified).
- `fx_blotter_rows` filters by product, so it never appears there.

Related: [[daily-series-2026-09-29]]
