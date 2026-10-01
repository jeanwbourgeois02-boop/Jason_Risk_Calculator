---
name: marks-check-closed-status-2026-10-01
description: inventory.mark_checks status CLOSED (exchange shut on a past day) on the Data tab and the diagnosis report - grey chip, not due, never counted as missing
metadata:
  type: project
---

2026-10-01, from bbg-library: `inventory.mark_checks` gives status `CLOSED` (`inventory.CHECK_CLOSED`) on a past day the contract's exchange was shut (column `exchange_closed`, `arrived` False, `arrived_reason` "exchange closed: ...").

How the screens treat it:
- `data_checks.STATUS_ORDER/WORDS/LEVEL`: MISSING, CHECK, CLOSED ("Exchange closed", grey), OK; also in `STATUS_OPTIONS` (the Status funnel). Chip hover = `arrived_reason`, as for MISSING; the Arrived check cell shows the dash (None), not a red cross; the total row says "N exchange closed".
- `market_data` status line: `status_line(..., marks=(due, arrived, closed))`; CLOSED rows are left out of "Marks X of Y" and named "· N exchange closed", with the sentence on hover. A 2-tuple still works.
- `ui/diagnostics_runner._missing_lines`: CLOSED is counted on the header line ("exchange closed (not due) N"), never listed as bad, so `_summary`'s `len(missing) > 1` test stays "something is wrong".

**Why:** a holiday close does not exist; counting it as missing made the Data tab cry wolf.
**How to apply:** any new reader of the marks check rows treats CLOSED as "not due", never as a problem.
