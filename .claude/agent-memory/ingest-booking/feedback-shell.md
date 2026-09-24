---
name: feedback-shell
description: Shell habits the housekeeper requires - never `py -3 -` / python stdin heredocs, stay at repo root, tests under timeout 900 in the foreground
metadata:
  type: feedback
---

Never run `py -3 -` or a `python -` heredoc. Use `py -3 -c "..."` or the Edit / Write tools.
Keep the shell at the repo root. Run the lane tests in the foreground under `timeout 900`.

**Why:** these are housekeeper brief rules (2026-09-24). On Git Bash on this PC, a bare `py -3 -`
blocks waiting on stdin. The Bash tool then backgrounds it and the python process stays alive
until it is killed by hand. This happened once: the stray was found with Get-CimInstance
Win32_Process and stopped.

**How to apply:** for multi-line Python, write a one-off script to the session scratchpad, or
use `py -3 -c`. A bash heredoc with apostrophes in its body inside a longer command can break
the quoting, so appending test code is safer with the Edit tool.
