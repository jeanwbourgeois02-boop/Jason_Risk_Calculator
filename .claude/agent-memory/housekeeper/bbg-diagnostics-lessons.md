---
name: bbg-diagnostics-lessons
description: Lessons from the pull_marks.py diagnostics rounds (2026-09-13/14) - state "exit code follows requested-vs-written keys" explicitly, reviewer verifies by executing the blpapi fake, _NY_ZONE cache trap in tz tests, tzdata exit-6 path now exists (item 39 closed, residual item 40)
metadata:
  type: project
---

When a specialist prompt says "any request failure must exit non-zero", spell out the completeness rule: exit 0 only if every requested `(instrument_id, settle_date, mark_type)` key was written. bbg-data otherwise made a judgement call (only TIMEOUT fatal, fieldException/securityError graceful) that the reviewer had to reverse as critical, costing an extra round.

**Why:** bbg-data reasoned "Bloomberg legitimately returns fieldExceptions" and treated them as warnings; the operator would have taken an exit-0 partial CSV as complete.

**How to apply:** for scripts that run unattended on the Bloomberg machine, phrase requirements as observable outcomes (exit code, files, summary fields), not as failure categories. The reviewer for `data/bloomberg/` verifies claims by executing scenarios against the fake blpapi in `tests/test_bloomberg.py` (not by reading docstrings) — ask it to do that every time; it caught late-response pollution, empty environment blocks, and (2026-09-14) a docstring-promised exit-6 handler that did not exist. When bbg-data's docstrings promise a handler, have the reviewer confirm the handler exists.

Status 2026-09-14: the missing-tzdata path is implemented (item 39 closed, suite 116 passed): `main()` resolves `_ny()` right after `Diagnostics()`, writes a `tz_prerequisite` diag failure, prints the pip hint, returns 6. Residual (item 40, open warning): that diag has an empty `environment` block because `record_environment` itself needs `_ny()`. Test trap: `_ny()` caches in `pull_marks._NY_ZONE`; any test of the tz branch must reset it to `None` or it passes vacuously. A tightly specified brief (exact stage name, hint string, placement, test assertion, expected pass count) got this done in one specialist round with zero criticals — keep briefs at that level of precision for bbg-data.

Operator steps live in `docs/bloomberg-run.md`; probe first, then pull, bring back the marks CSV and both `.diag.json` files.
