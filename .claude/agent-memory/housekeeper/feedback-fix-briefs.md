---
name: feedback-fix-briefs
description: Critical-fix briefs must enumerate every table/bucket the fix must cover and pin the numeric tolerance; a vague "compare value columns" brief cost the second review pass on 2026-09-14 (C-1 left open, no third pass allowed)
metadata:
  type: feedback
---

When sending a reviewer critical to a specialist, restate the fix as a checklist that names every table, every counter that must feed the exit code, and the exact tolerance, and require a test per table. Do not paraphrase the reviewer's one-line fix.

**Why:** on 2026-09-14 the C-1 brief ("compare value columns, count mismatches in a conflicts bucket, small tolerance e.g. 1e-6 relative") let data-ingest count only `trades` conflicts in the exit code (legs/positions/cash/futures ignored) and pick a 1e-6 relative band that is 568 units on a JPY leg. Pass 2 found it, no third pass is allowed, so the critical stayed open (open-questions item 50). My suggesting "1e-6 relative" in the brief was itself the cause of gap (b): CSV -> SQLite REAL round-trips exactly, so exact equality is the right tolerance for re-load comparisons.

**How to apply:** for any "detect X across tables" fix: list the tables; say "exit code = sum of all buckets"; say "exact equality unless proven otherwise"; demand a test that amends a field living only in the secondary table (e.g. Local Cost -> legs/positions only; cash rows have no trades row). See [[fix-diff-snapshot]] and [[feedback-spawn-process]].
