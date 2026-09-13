---
name: feedback-spawn-process
description: Run specialist fix rounds in the foreground and never re-spawn a background agent that is still working; the coordinator expects one reviewer pass after all specialists, re-run only on critical findings
metadata:
  type: feedback
---

Run specialist and reviewer rounds with `run_in_background: false`, one at a time. Do not re-spawn an agent for the same task while a background copy may still be running.

**Why:** on 2026-09-13 a background data-ingest fix looked dead (0-byte output file, code unchanged) and was re-spawned; both ran, the second hit a "file changed since read" conflict and had to reconcile the first's edits. The coordinator explicitly asked for foreground runs afterwards. Background subagent output files stay 0 bytes until the agent finishes, so an empty file is not evidence it failed.

**How to apply:** ingest/fix/review chains are strictly sequential; only truly independent work (docs, memory) runs alongside. Reviewer runs once after all specialists in a request finish, then again only on critical findings, until none remain (this wording is now in .claude/agents/housekeeper.md).
