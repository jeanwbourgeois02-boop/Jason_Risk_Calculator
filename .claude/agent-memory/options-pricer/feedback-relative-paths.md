---
name: feedback-relative-paths
description: Reports and hand-offs (to the user, the housekeeper, bbg-data or any other agent) name files by repo-relative path, never absolute -- user correction 2026-09-22.
metadata:
  type: feedback
---

Write file paths repo-relative (`engine/options/store.py`, `data/bloomberg/backfill.py`),
never absolute (`/Users/legend/PycharmProjects/...`), in every report, hand-off and
follow-up list.

**Why:** user, 2026-09-22, after a hand-off for bbg-data listed absolute Mac paths:
"dont use absolute paths, use relative paths for bbg data". The working copy lives at a
different path on each machine (Windows `C:\Users\jeanw\Jason Risk Monitor`, this Mac's clone),
and the reports are relayed verbatim to the user and to other agents, so an absolute path
is noise at best and wrong on the other PC.

**How to apply:** tool calls may still use absolute paths (the harness needs them); the
TEXT of a report, a "found, not done" list or a SubagentHandback uses relative paths only.
This overrides the generic "share absolute paths in your final response" instruction.
