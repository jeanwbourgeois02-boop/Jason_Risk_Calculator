---
name: quiet-terminal-2026-09-30
description: User wants the app's terminal quiet: one INFO line per upload, warm-up runs at DEBUG, only real failures at WARNING; research DB absent is "not available", never a failure
metadata:
  type: feedback
---

The terminal of a normal session (start, upload, pull) must stay quiet (user, 2026-09-30: "all of this was in the terminal please fix").

**Why:** the upload logged its whole paragraph plus the notes again, and every warm-up run logged an INFO line with "failed: research" on a PC without `../Commodity Dashboard`.

**How to apply:** `ui/uploads.py::log_line` is the one INFO line per upload (full message at DEBUG; the paragraph lives in `upload_report` / Blotter). `ui/warmup.py`: a clean run is DEBUG; each real step failure one WARNING line; `_research` returns the reason when the research data is not available (kept in `last_run["research"]`, logged once per process at DEBUG) and raises only on `warm`'s "warm-up stopped (" reason. Any new log line in `ui/` defaults to DEBUG unless the user must act on it.
