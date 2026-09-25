---
name: expiries-tab-conventions
description: Display conventions chosen for the Expiries tab (est. marker, level chips, fixed px widths, hover in place of wrapped columns, issues drawer, "#" engine-order column) and why
metadata:
  type: project
---

Expiries tab (built 2026-09-24, Phase 1 step 3; redesigned 2026-09-25, Screens redesign Phase A) shows every date of an `estimated` row with " (est.)" (the short marker), the sentence (`ESTIMATE_TIP`) on hover of each date.

**Why:** lane rule "an estimated date is always shown as estimated"; the alert date of an estimated physical contract is held early (first business day of the month before), which is itself derived from an estimate, so it carries "(est.)" too.

**How to apply:**
- Screenshot of 2026-09-25 (user-approved plan): rows were ~80 px tall from wrapped Reason / Alert basis columns and the count was cut off at 1680 px. Now: single-line cells (nowrap + ellipsis), fixed px widths in `COLUMN_WIDTHS_PX` summing under `WIDTH_BUDGET_PX` 1590 (the section's inner width at 1680 px; table font is Inter 12.5 px from ui/assets, not monospace). Keep new columns inside that budget or move them to hover.
- Column order: #, Level, Business days to alert, Alert date, Contract, Name, Exchange, Lots, Next event, Event date, Last trade, First notice, Product (last, only when mixed).
- What left the columns stays in the records (alert_basis, calendar, delivery, dates_source, reason) and is on hover: reason on contract / level / count; alert basis on alert date; calendar, delivery, dates source on exchange; full name on name.
- Definitions go on `about(...)` title hover (thresholds from the engine on "Roll calendar"); level counts are chips in `expiries-counts` with the threshold on hover; reasons (n/a counts, estimated dates as one line, beyond coverage, delivery not on file) in `issues_drawer` id `expiries-issues`.
- Level colours and chip styles are inline (`LEVEL_STYLES`), because `ui/assets/` is ui-shell's; a CSS class would be a Request.
- Beyond-coverage styling of the count uses a filter_query on the hidden `calendar` field; hover and drawer carry it too in case that filter does not fire in the browser (not browser-checked).
- Level text sorts alphabetically under native sort, so the "#" column (engine order, worst first) restores that order; never re-derive the order from level + days.
- The engine may return RED with a future event date (estimated physical past its alert date): render as given, never "fix" it.
- Frozen contracts (engine `settled_expired`) sit in a collapsed html.Details "Expired and settled (N)", no level colour, never counted; its caption is now the summary's hover; omitted when empty.
- A date that does not apply to the product (option/LME first notice, LME last trade) reads an em dash with "not applicable to ..." on hover. LME tonnes on hover of Lots; option type/strike/style/underlying event on hover of Next event. dates_source 'TICKET' reads "Ticket's prompt".
- The Bash tool's heredoc chokes on long quoted scripts with apostrophes; write edit scripts with the Write tool into the scratchpad, and write files back with `write_bytes` (Path.write_text on Windows turns LF into CRLF).
