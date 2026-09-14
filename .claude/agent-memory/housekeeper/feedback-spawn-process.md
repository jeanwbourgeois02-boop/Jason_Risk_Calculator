---
name: feedback-spawn-process
description: Independent specialists run in parallel as background agents, dependent ones sequentially; an empty output file means still running, never re-spawn; reviewer on git diff only, at most twice, warnings reported not fixed
metadata:
  type: feedback
---

Specialists whose owned directories do not overlap and whose tasks do not depend on each other run in parallel as background agents; wait for all to finish before running the reviewer once on the combined diff. A specialist that depends on another's output runs after it. An empty output file means the agent is still running, not dead; never re-spawn on that basis. The reviewer runs after all specialists have finished.

**Why:** on 2026-09-13 a background data-ingest fix looked dead (0-byte output file, code unchanged) and was re-spawned; both ran, the second hit a "file changed since read" conflict and had to reconcile the first's edits. Background subagent output files stay 0 bytes until the agent finishes, so an empty file is not evidence it failed. The user set the parallel-background rule in .claude/agents/housekeeper.md step 2 on 2026-09-13.

**How to apply:** independent specialists in one request are spawned together with `run_in_background: true`; a specialist that needs another's output is spawned after that one completes; fix rounds wait for the original agent to finish. Reviewer runs once, on git diff only, after all specialists in a request finish. If it reports criticals, send those to the specialist and run the reviewer a second time on the fix diff only. Never a third pass. Warnings are listed in the report for the user, not fixed and not re-reviewed, unless the user's request says otherwise (this wording is in .claude/agents/housekeeper.md step 3, set by the user on 2026-09-13).
