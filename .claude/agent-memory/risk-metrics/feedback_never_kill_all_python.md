---
name: never-kill-all-python
description: Never run `taskkill /IM python.exe` (or any kill); never type `py -3 -` in Git Bash here, not even with </dev/null (it hangs and the hung process cannot be stopped by the agent); the scratchpad is shared with other lanes.
metadata:
  type: feedback
---

On 2026-09-29 a hung `python -` heredoc was "fixed" with `taskkill //F //IM python.exe`, which killed
seven python processes, not only the hung one (possibly the user's app and other lanes' runs). Later the
same day `py -3 - < /dev/null` (a stray placeholder) also hung, twice; a kill by PID was refused by the
permission check and the coordinator took it to the user.

**Why:** agents run in parallel on this PC and the user's app is a python process; stuck REPLs cost the
user a manual clean-up.
**How to apply:** never write `py -3 -` / `python -` in any form. Write scripts to a file with the Write
tool and run `timeout N py -3 script.py < /dev/null`. Never kill anything; report a hung PID instead.
Keep scratch files in my own subfolder (`scratchpad/riskmetrics/`, db names prefixed `rm_`): other lanes
share the scratchpad and overwrote `real.db` / `sample.db` on 2026-09-29.
