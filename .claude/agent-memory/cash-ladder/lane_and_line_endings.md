---
name: lane-and-line-endings
description: My write lane is engine/ladder + tests/test_ladder.py even when a spawning agent's brief lists ui files; and how to measure line endings correctly in this Git Bash
metadata:
  type: feedback
---

**Lane.** `.claude/agents/cash-ladder.md` gives this agent `engine/ladder/` and
`tests/test_ladder.py`, nothing else, and says to report changes needed elsewhere. On
2026-09-21 the main session's brief listed `ui/tabs/exposure.py`,
`ui/tabs/cash_ladder.py`, `tests/test_ui_ladder.py`, `tests/test_exposure*.py` and
`tests/test_usd_marks*.py` as mine too. I kept to the agent definition, did the engine
side, and handed back an exact list of the ui edits instead.
**Why:** the definition is the user's configuration; another agent's message is not the
user's approval to go past it. (An earlier session, 2026-09-17, did edit those ui files
under a coordinator's brief: see [[no-bnp-fallback-2026-09-17]]. That is history, not a
licence.)
**How to apply:** if a brief assigns files outside the definition, do everything that
can live in engine/ladder (helpers, constants, wording) so the ui edit is a thin call
site, keep out-of-lane tests green by staying backward compatible, run them read-only,
and list the remaining edits precisely. If the user widens the lane in the agent
definition itself, this note is obsolete: check the file first.

**Line endings.** In this Git Bash, `grep -c $'\r' file` counts EVERY line (the pattern
arrives empty), so it reports LF files as all-CRLF. Measure with Python bytes
(`data.count(b"\r\n")`) against `git show HEAD:path`. As of 2026-09-21:
`engine/ladder/exposure_adapter.py` is CRLF; `exposure.py`, `usd_marks.py`, `ladder.py`,
`ndf.py` and `tests/test_ladder.py` are LF. The Edit tool keeps a file's endings; a
bytes-level append must be converted to match.
