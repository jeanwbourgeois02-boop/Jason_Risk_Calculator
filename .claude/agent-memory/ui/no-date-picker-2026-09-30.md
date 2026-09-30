---
name: no-date-picker-2026-09-30
description: The header's as-of picker was removed 2026-09-30; as-of = today NY rolled at 17:00, `?as_of=YYYY-MM-DD` URL query (dcc.Location header.URL_ID) for ui_check; hidden store under the old picker id keeps the poll's outputs stable
metadata:
  type: project
---

User, 2026-09-30: "this as of wednesday button thing - that can go". The header starts with Daily.

**Why:** the app always values as of today in New York; a date control was clutter.

**How to apply:**
- `ui/app.py::_as_of_from_url` (Input `header.URL_ID` "search", same outputs as the old `_follow_pickers`:
  AS_OF_STORE_ID + AS_OF_PICKED_ID) reads `?as_of=` via `header.as_of_from_query`; no query = no_update.
- `tools/ui_check.py` opens `/?as_of={AS_OF}` and `_pick_as_of` only waits for the store (infra's file; the
  one change made on the coordinator's say-so).
- Trap: `revision.register`'s `_poll` outputs must never change shape (an older page asks a newer server
  by output key). So the tick still outputs `header.DATE_PICKER_ID` "date" (always no_update) and the layout
  keeps an empty `dcc.Store(id=header.DATE_PICKER_ID)`. Do not remove either without a legacy answer.
- Never add text telling the user to change the date in the header.
