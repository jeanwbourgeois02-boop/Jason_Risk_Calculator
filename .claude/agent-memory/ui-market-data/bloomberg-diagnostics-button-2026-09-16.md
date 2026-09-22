---
name: bloomberg-diagnostics-button-2026-09-16
description: "Check Bloomberg connection" button, placeholder interface, and expected bbg-diagnostics contract (module location superseded, see note)
metadata:
  type: project
---

SUPERSEDED (same day): originally added to `ui/tabs/header.py` (present on every tab),
then moved same-day to `ui/tabs/market_data.py` (Market Data tab only) per user
decision — see [[bloomberg-check-button-moved-to-market-data-2026-09-16]] for current
module/ids. The interface, placeholder behaviour, and status vocabulary described below
are unchanged, only the module changed from `header` to `market_data`.

Click callback (`prevent_initial_call=True`) is separate from other callbacks so it
never runs on page load and never blocks anything else.

**Expected interface for the housekeeper to wire up** once the `bbg-diagnostics` agent
lands its module:

```
data.bloomberg.bbg_diagnostics.run_bloomberg_diagnostics() -> list[dict]
# each dict: {"name": str, "status": "pass"|"fail"|"warning", "message": str}
```

`header._bbg_diagnostics_entry_point()` already tries to import that exact path/name
first and only falls back to `header._run_bloomberg_diagnostics_placeholder()` on
`ImportError` — no further UI change should be needed once that module exists, just
create it at that path with that name and signature.

Placeholder today uses what `data/bloomberg/live.py` already exposes
(`availability(host, port)` for a live session probe, `read_status(db_path)` for the
last completed pull's status file) plus one static "not available yet" row for
marks_official coverage. All failures — including "blpapi not installed" or any
exception — are swallowed and rendered as one plain-English "Could not reach
Bloomberg" row via `run_bloomberg_diagnostics_safe()`; no traceback ever reaches the
page (tests assert `"Traceback"` / exception class names are absent from message text).

**Why status vocabulary matters**: used "pass/fail/warning" (not e.g. "ok/error") to
leave room for `bbg-diagnostics` to report a mark source that resolves but is
reconciliation-only (BNP_BVAL, BBG_INTERP per CLAUDE.md's "Official marks" table) as a
"warning", distinct from a hard "fail".

CSS added under `.bbg-check-*` classes in `ui/assets/style.css`, same navy/gold header
styling family as the existing `.header-figure-*` cards.
