---
name: expiries-tab-conventions
description: Display conventions chosen for the Expiries tab (est. marker, level colours inline, "#" engine-order column) and why
metadata:
  type: project
---

Expiries tab (built 2026-09-24, Phase 1 step 3) shows every date of an `estimated` row with " (est.)", the same marker ui-curve uses for contract-master estimates, and Dates source reads "Estimated, not Bloomberg's".

**Why:** lane rule "an estimated date is always shown as estimated"; the alert date of an estimated physical contract is held early (first business day of the month before), which is itself derived from an estimate, so it carries "(est.)" too.

**How to apply:**
- Level colours are inline styles in `ui/tabs/expiries.py` (`LEVEL_STYLES`), because `ui/assets/` is ui-shell's; a CSS class would be a Request.
- Level text sorts alphabetically under native sort, so the "#" column (engine order, worst first) exists to restore that order; never re-derive the order from level + days.
- The engine may return RED with a future event date (estimated physical past its alert date): render as given, never "fix" it.
