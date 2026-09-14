---
name: fix-diff-snapshot
description: How to give the reviewer a fix-only diff (copy touched files to the scratchpad before spawning the fix agent), and the LTD reference-date lesson that any "LTD as of an earlier date" brief must state the trade_date <= as_of filter
metadata:
  type: feedback
---

Before spawning a critical-fix agent, copy the files it will touch into the scratchpad (`prefix/`); after it finishes, `diff -u` snapshot vs working tree into `fix.diff` and hand that path to the reviewer. `git diff` cannot isolate the fix because the original task's changes are also uncommitted, and the reviewer can also run the new test against the snapshot to prove it failed before the fix (reviewer did exactly that on 2026-09-14).

**Why:** the second reviewer pass must be on the fix diff only (housekeeper step 3); without a snapshot there is no way to separate the fix from the first-pass work in the same uncommitted tree.

**How to apply:** snapshot in the same turn as the fix spawn (independent calls). Take the snapshot before the spawn resolves; on 2026-09-14 the copy landed 4 seconds before the agent's first edit, which was close.

Second lesson from the same day: when a brief asks for P&L at earlier reference dates (period_pnl: t-1bd, t-5bd, month end, year end), state explicitly that LTD as of a date must filter `trade_date <= as_of`. pnl.py's `ltd_per_trade` only filtered `settle_date > as_of`, so a trade dated t was valued at t-1 marks and daily collapsed to m_t - m_ref (reviewer critical C-1). See [[feedback-brief-premises]] and [[feedback-spawn-process]].
