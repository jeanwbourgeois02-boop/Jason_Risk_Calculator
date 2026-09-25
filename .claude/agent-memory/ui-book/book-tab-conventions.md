---
name: book-tab-conventions
description: How ui/tabs/book.py is built (Phase B, 2026-09-25): data gathering per section, memo, the chained VaR callback, what the golden book shows, test set-up pitfalls
metadata:
  type: project
---

The Book tab (built 2026-09-25, Phase B of the screens redesign) is `gather(conn, as_of)` -> `body(data)`:
each lane read sits in its own try so one failure costs one section. The P&L rows are spread positions
(`book_spreads(...)["positions"]`) plus outrights summed per root (header's display rule); sigma via
`research_spreads.sigma_move`, never divided here.

**Why:** the user wants one home screen ("how did I do today, and what needs me") where every number is
another lane's; one place per number.

**How to apply:**
- The VaR is never computed in the body: `_risk_if_ready` reads the header's memo (private
  `_risk_summary_if_ready` until ui-header makes a public `risk_summary_if_ready`; the getattr takes
  either). The body callback also outputs `VAR_PENDING_ID`; a chained callback then calls
  `header.risk_summary` and re-renders the alerts list (`ALERTS_ID`, inside the body: needs the app's
  suppress_callback_exceptions, which ui/app.py sets).
- `book_spreads` and `needed_marks` are memoised on (path, mtime, as_of) in `_MEMO`.
- Tests: open the golden db with a read-only sqlite URI, never `schema.connect` (it rewrites the views,
  so the mtime moves and every render is cold). Patch `book._open`, not `ui.app`.
- On the golden book at 2026-09-18 the research db on this PC has no stats before 2026-09-21, so every
  sigma is n/a; test sigma with a synthetic stats entry.
- Tab navigation from a "-> Curve" link is only a named pointer: a clickable jump would need ui-shell's
  MAIN_TABS_ID callback (not requested yet).

Related: [[ui-spreads conventions in .claude/agent-memory/ui-spreads]]
