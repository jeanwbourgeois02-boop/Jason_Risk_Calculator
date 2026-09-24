---
name: margin-limits-conventions
description: How engine/limits estimates margin (per root x month cells, spread credit budget) and defines limits (spot month, lots, NOT_SET/N/A); placeholder-rate policy
metadata:
  type: project
---

Built 2026-09-24 (Phase 5). Decisions that are not obvious from the brief:

- Margin cell = (root, contract month), all products netted at delta (options at delta lots). Months are NOT
  netted per root: netting per root would give calendars a 100 % credit; the credit % is the cross-month offset.
- Spread credit only on FUTURE legs, only up to the cell's same-sign net delta lots (a budget per cell), so
  lots offset by another trade in the month are not credited twice. Leftover per root is assigned to the
  legs of its sign, largest first.
- credit key: kind 'calendar' (or family 'calendar' for bundles/pins) else template family
  (benchmark/processing/substitution); no shape = no credit.
- Spot month = among HELD months of the root, nearest last trade after as_of (curve rows only hold expiry > as_of).
  contract-master's estimated last trade (last weekday of the month) is too late for CL etc, so listed-front
  logic from contract-master would be wrong; nearest held can only warn early, never miss.
- Levels: OK/WARN/BREACH/NOT_SET plus N/A (limit set, value missing), never OK by default. At exactly the
  limit = WARN, over = BREACH.

**Why:** lane rule "never invent a rate" vs housekeeper brief asking for sector placeholders: the brief won;
placeholders are commented "# placeholder, not the exchange's" and every result carries basis
"estimate (config/limits.yaml), not exchange SPAN". Setting a rate to null makes the commodity n/a.
**How to apply:** keep that labelling on any new figure; never fill an exchange limit from memory.
