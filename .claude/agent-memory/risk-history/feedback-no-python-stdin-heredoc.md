---
name: feedback-no-python-stdin-heredoc
description: Never run `py -3 -` with a heredoc in the Bash tool; write a script to the scratchpad and run that instead
metadata:
  type: feedback
---

Never run Python through `py -3 -` and a heredoc. Write the script to the scratchpad and run it, or use `py -3 -c` for a one-liner.

**Why:** the housekeeper forbids it. On 2026-09-24 one of these ran anyway. It hung waiting on stdin until the Bash tool timed out and moved it to the background. The permission classifier then refused `taskkill` ("interfere with workloads"), so the process stayed alive and had to be reported. It exited on its own later, but only after the report had gone out. Other agents' stuck `py -3 -` processes were running on the PC at the same time.
**How to apply:** this covers every inline Python run in this repo, including throwaway checks.
