---
name: quiet-terminal-2026-09-30
description: In-app backfill prints only problem lines plus one "Bloomberg history:" summary; CLI keeps the full table; how a new log line picks its side
metadata:
  type: feedback
---

The app's terminal after "Pull Bloomberg now" gets only problem lines and ONE summary line
("Bloomberg history: N past days filled (a to b), R requests, F failed; M marks still missing on
the latest day: see the Data tab."). The command-line backfill (`py 2_launcher.py backfill`, a direct
backfill()/auto_backfill() with log=print) keeps the full per-day table.

**Why:** user, 2026-09-30: the terminal was cluttered by the per-day DONE table after every pull; those
figures are already in the status file and on the Data tab.

**How to apply:** a new `log(...)` line in backfill.py that reports a problem (a failed request or step,
a day ERROR / NO_CLOSES, ledger re-freeze or unrealisable news, days that cannot be completed) must be
wrapped in `_problem(...)`, a str subclass that `_QuietLog` (passed by start_auto_backfill) forwards;
an unwrapped line is info and goes nowhere in the app. Decide by what the code knows (status, step
errors), never by matching the text. The per-day line omits `options=` / `realised=` when None.
