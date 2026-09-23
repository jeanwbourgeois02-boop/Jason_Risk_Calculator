---
name: inflight-status-reports
description: How to answer "what are the other agents doing" when several Claude sessions share the working tree -- snapshot diffs go stale within minutes, so check the live file before calling anything unwired or half-done.
metadata:
  type: project
---

Jean asks for plain-language status of uncommitted work done by parallel sessions. The
launching session hands over diffs saved in its scratchpad, split by ownership lane.

**Why:** on 2026-09-18 the saved diff of `ui/tabs/options.py` was already behind the file
on disk (the diff ended before the layout and callbacks; the live file had them wired).
Reporting from the diff alone would have called finished wiring "defined but never used".

**How to apply:**
- Treat the diffs as a map, then Grep/Read the live file for every "not wired yet" claim.
- Useful half-done signals: a module's docstring describing a fix the code below does not
  have yet; a rewritten module whose own test file is untouched; a message telling the
  user to use a control that no file in `ui/` builds; a new helper only its test calls.
- The files rarely say which session made a change. Say so; do not guess.
- Always separate "changes how a number is calculated" from "changes how missing or bad
  data is reported" -- CLAUDE.md reserves the first for Jean's explicit yes.
- Open decisions seen that day (verify before repeating): digital payout currency assumed
  BASE, marked pending Jean's confirmation in `engine/options/store.py`;
  `docs/CLAUDE.draft.md` waiting for approval to replace `CLAUDE.md`.
