---
name: timing-cash-tab-deleted-2026-09-28
description: The Timing & cash tab (ui/tabs/expiries.py) was deleted on 2026-09-28; six tabs remain; how the Book's "Needs you" lines point (or not) at a tab
metadata:
  type: project
---

The Timing & cash tab (the former Expiries, `ui/tabs/expiries.py`, key `expiries`) was deleted
on 2026-09-28 (user decision, Phase D rule: retired screens are deleted, not hidden). Six tabs:
Book, Exposure, P&L, Risk, Trades, Data. Its key sits in `ui/app.py::HIDDEN_TAB_KEYS` so a stale
`tab_link` is ignored.

**Why:** the user found the seven tabs confusing; a position's dates live in the Book's own
Next column (`engine.expiry.expiry_schedule`, which the Book still reads for Next and the alerts).

**How to apply:**
- A "Needs you" line on the Book passes `tab=""` to `_need` when its detail is on the Book
  itself (expiry alerts, estimated dates, the roll-calendar error); `book.pointer` renders no
  link for `""`, `"Book"` or an unknown tab. Marks lines link to Data, could-not-group to
  Trades, limits to Risk.
- `book.TAB_KEYS` mirrors `ui.app.TAB_KEYS` (a copy, kept in step by hand).
- The `.level-chip--*` CSS stayed (the Book's Next cell uses the level colours).
- Do not resurrect the cash section (settled cash, cash to come, margin estimate) or the
  timeline without the user's yes; `engine/ladder` and `engine/limits` still hold the readers.
