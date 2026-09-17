---
name: value-book-marks-source-kept-for-compat-2026-09-17
description: value_book's marks_source parameter was gutted (no fallback lookup ever happens) but NOT removed from the signature -- ledger.py's own default call path would break app-wide otherwise; engine/pnl/calendar.py split also landed same day
metadata:
  type: project
---

2026-09-17, "no bnp fall back" user decision (verbatim, via coordinator relay -- see
[[coordinator-relay-verification]]; also see `docs/bnp-excel-removal.md`).
`engine/pnl/valuation.py::value_book`'s `marks_source='BNP_BVAL'` retry path (read
`marks` directly for an explicit non-official source when the official lookup missed)
is deleted outright: `_mark_at` now ignores its `source` argument entirely and always
reads `marks_official`. `_BookConn.fallback_dated`/`fallback_spot` preloads (added
earlier the same day as a perf fix) are gone too -- the whole mechanism, not just its
caching.

**Deliberately did NOT remove `marks_source` from `value_book`'s signature**, despite
the instruction saying "the marks_source parameter goes" literally. Reason: grepping
callers turned up `engine/pnl/ledger.py`'s `ltd`/`period_pnl`/`period_pnl_by` (owned by
pnl-engine, not this lane) -- all three thread `marks_source` through to `value_book`
even on their own default (`marks_source=None`) call path, i.e. `ltd(conn, as_of)` with
NO override still ends up calling `value_book(conn, as_of, marks_source=None)`. Deleting
the parameter would make that keyword argument invalid and crash `ltd`/`period_pnl`
*unconditionally*, taking down the entire live P&L engine (everything that calls
`engine.pnl.ledger`) until pnl-engine also edits `ledger.py` -- a much bigger, more
central blast radius than the one caller (`ui/tabs/blotter_pricing.py`) the coordinator
mentioned by name. `engine/ladder/futures_delta.py` calls `_mark_at` directly with a
`marks_source` too (also kept, also now a no-op).

Chose instead: keep every signature (`value_book`, `usd_per_quote`, `_mark_at`) accepting
`marks_source`/`source`, but make it **functionally inert** -- passing it changes
nothing, official marks only, always. This achieves the literal behavioural requirement
("value_book reads marks_official only ... missing stays missing") immediately and
safely, with zero forced follow-up elsewhere. `ui/tabs/blotter_pricing.py` (blotter
agent) independently reached the same conclusion and removed its own call site the same
session (confirmed via its own updated docstring, found mid-task) -- so in practice
almost every external caller already stopped passing a non-None value anyway; only
`ledger.py`'s three functions and `futures_delta.py` still do, harmlessly.

**How to apply:** if asked to finish the literal removal later, first confirm
`engine/pnl/ledger.py`'s three functions and `engine/ladder/futures_delta.py` have
dropped their own `marks_source` parameters (not this agent's files) -- only then is it
safe to delete it from `value_book`/`usd_per_quote`/`_mark_at` too.

**Also same day:** `engine/pnl/aggregate.py`'s six business-day calendar helpers moved to
a new `engine/pnl/calendar.py` (so the live P&L path stops sharing a file with / import
of `engine/pnl/pnl.py`, the retired workbook arithmetic, per `docs/bnp-excel-removal.md`'s
"Blocked" item 1). `aggregate.py` re-exports every name for backward compatibility, but
`load_holidays` specifically is a **hand-written thin wrapper**, not a bare re-export --
`tests/test_ledger.py` (not owned by this lane) monkeypatches
`engine.pnl.aggregate._DEFAULT_HOLIDAYS_PATH`, and a bare re-export would leave that patch
pointing at a name `calendar.load_holidays`'s function body never actually reads (Python
`from x import y` binds a reference to the same function object, whose `__globals__` stay
fixed to the module it was *defined* in, regardless of how many other modules re-import
the name). Cost: `aggregate.load_holidays is not calendar.load_holidays` (two different
function objects, verified in `tests/test_calendar.py`) -- worth knowing if something
else ever compares identity instead of just calling either one.
