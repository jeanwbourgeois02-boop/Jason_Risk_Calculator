---
name: bbg-diagnostics-lessons
description: Lessons from the pull_marks.py diagnostics round (2026-09-13) - state "exit code follows requested-vs-written keys" explicitly, reviewer verifies by executing the blpapi fake, tzdata/zoneinfo risk on the Bloomberg machine
metadata:
  type: project
---

When a specialist prompt says "any request failure must exit non-zero", spell out the completeness rule: exit 0 only if every requested `(instrument_id, settle_date, mark_type)` key was written. bbg-data otherwise made a judgement call (only TIMEOUT fatal, fieldException/securityError graceful) that the reviewer had to reverse as critical, costing an extra round.

**Why:** bbg-data reasoned "Bloomberg legitimately returns fieldExceptions" and treated them as warnings; the operator would have taken an exit-0 partial CSV as complete.

**How to apply:** for scripts that run unattended on the Bloomberg machine, phrase requirements as observable outcomes (exit code, files, summary fields), not as failure categories. The reviewer for `data/bloomberg/` verifies claims by executing scenarios against the fake blpapi in `tests/test_bloomberg.py` (not by reading docstrings) — ask it to do that every time; it caught late-response pollution and empty environment blocks that way. Operational risk to keep in mind: `pull_marks.py` resolves `ZoneInfo("America/New_York")` lazily via `_ny()`, but `main()` calls it (through `record_environment`) before the try/finally that writes diagnostics, so a Windows terminal machine without `tzdata` still crashes with no `.diag.json` and the documented exit 6 never happens (open questions 33 and 39). When bbg-data's docstrings promise a handler, have the reviewer confirm the handler exists rather than trusting the docstring; that is how the 2026-09-14 review caught this. Operator steps live in `docs/bloomberg-run.md`; probe first, then pull, bring back the marks CSV and both `.diag.json` files.
