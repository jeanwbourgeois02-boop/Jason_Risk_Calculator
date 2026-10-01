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

2026-09-30: it happened AGAIN, as a stray empty `python - <<'EOF' ... EOF || true` line at the top of a
chained command: two venv python processes spun on CPU (PIDs 35428 / 27868) and blocked the whole chain
for 10 minutes. Never put `python -` / `py -3 -` in a command, even empty, even as a no-op. Also: long
multi-line heredocs holding Python with quotes can break the Bash tool's parser; write such scripts with
the Write tool instead. `sqlite3` CLI is not installed: query scratch dbs from a small script file.

2026-10-01: a stray `cat > "$TEMP/../rm_dummy"` with no input at the head of a chained command hung the
same way (cat.exe PID 7332 left waiting on stdin, reported, not killed). Never start a command with a
redirect target and no input source.
