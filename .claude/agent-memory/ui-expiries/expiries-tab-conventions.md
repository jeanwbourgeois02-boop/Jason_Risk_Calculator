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
- Frozen contracts (engine `settled_expired`, user's recommended option 2026-09-24) sit in a collapsed html.Details "Expired and settled (N)" below the alert table, no level colour, never counted; the section is omitted when the list is empty.
- The Product column ("Future" / "Option" / "LME prompt") shows only when rows hold more than one product; futures-only book hides it.
- Phase 5 (2026-09-24): a date that does not apply to the product (option/LME first notice, LME last trade) reads an em dash with "not applicable to ..." on hover, never "missing"/"n/a" (housekeeper brief). LME tonnes go on hover of Lots; option type/strike/style/underlying event on hover of Next event (the Contract hover stays the reason, tests pin it). dates_source 'TICKET' reads "Ticket's prompt".
- The Bash tool's heredoc choked on a long quoted script with apostrophes; write edit scripts with the Write tool into the scratchpad and run them with py -3.
