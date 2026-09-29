---
name: never-kill-all-python
description: Never run `taskkill /IM python.exe` (or any kill by image name) on this PC: it stops the user's running app (chelsea) and other agents' processes too.
metadata:
  type: feedback
---

On 2026-09-29 a hung `python -` heredoc was "fixed" with `taskkill //F //IM python.exe`, which killed
seven python processes, not only the hung one (possibly the user's app and other lanes' runs).

**Why:** agents run in parallel on this PC and the user's app is a python process.
**How to apply:** never start `py -3 -` / `python -` heredocs in Git Bash (they open the REPL and hang);
write the script to the scratchpad, run it with `timeout N py -3 script.py < /dev/null`, and if something
hangs, stop only that one PID (from the background task), never by image name.
