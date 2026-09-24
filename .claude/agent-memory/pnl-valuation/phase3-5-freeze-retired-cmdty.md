---
name: phase3-5-freeze-retired-cmdty
description: 2026-09-24 wave A: settled future converts at expiry-date spot (C9), leftover IRS/SWAPTION/CAP_FLOOR blank rows (C10), CMDTY_OPTION on the listed path (C12); golden proof on a HEAD copy
metadata:
  type: project
---

- C9 (user, 2026-09-24): a settled future / listed option converts at the last official SPOT on or
  before its EXPIRY (leg settle_date), exact rows only. Helper `usd_per_quote_on_or_before(conn, ccy,
  date) -> (S, pair, source, spot_date)`; USD = (1.0, 'USD', 'identity', date); none = (NaN, None,
  None, None). `last_usd_conversion` now delegates to it (same result). pnl-ledger imports it.
  `_settled_future_row`'s display spot reads it at r.settle_date, not spot_as_of_date.
  The no-S reason became "... of <ccy> on or before its expiry <d>, so it cannot be frozen"; a spot
  dated before expiry adds "; converted at <pair> spot dated <d> (last before expiry)" to the note.
- C10: `RETIRED_PRODUCTS` (IRS, SWAPTION, CAP_FLOOR) -> `_retired_sql` / `_retired_row`: blank row
  with the reason verbatim, status from MAX(leg settle_date) ('' = OPEN), frozen realised row shown
  (spot 1 / identity). Consequence: `ledger.ltd` is NaN on an old DB with an unfrozen swap.
- C12: `LISTED_OPTION_PRODUCTS = (EQ_OPTION, CMDTY_OPTION)`, `FUTURE_PRODUCTS` feeds `_fut_sql`.
- **Why:** user's four answers "Later the same day" in CLAUDE.md's plan.
- **How to apply:** the working tree's golden book was red from other lanes' in-flight edits
  (contracts, expiry schedule); the proof that valuation moved nothing is HEAD via `git archive`
  into the scratchpad with only valuation.py swapped in (green). `test_live::test_availability_no_
  blpapi_never_touches_the_socket` fails on pure HEAD: environmental, not ours. Bash heredocs with
  `cat >> file <<'EOF'` failed to parse here; append with Edit instead.
