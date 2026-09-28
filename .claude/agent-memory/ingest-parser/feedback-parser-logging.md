---
name: feedback-parser-logging
description: Parser findings about a file's contents (rejects, skips, no-strike options, LME no-prompt, book-filter exclusions) log at DEBUG only; WARNING/ERROR is for app or environment faults
metadata:
  type: feedback
---

Log every per-row or per-file finding about a blotter's *contents* at DEBUG. Keep WARNING / ERROR only for a fault of the app or the environment (a file that cannot be read at all, an exception).

**Why:** user request 2026-09-28: the launched app's terminal printed `REJECT ... fits 2 contract roots` and `1 option(s) have no strike` on every load, duplicating what the screens already show (Book tab "Last load", `upload_issues`, Data tab). The screens are the designated place for those findings; the terminal should say nothing about a file's contents.

**How to apply:** any new `log.warning` / `log.info` in `blotter.py` or `common.py` that describes a row or a file goes in at `log.debug`. The parser's *returned* rejects, warnings and summary sentences are untouched: only the logging level moves, never what the screens read. All six calls in `blotter.py` were moved on 2026-09-28 (`_warn`, the pair-by-convention guess, the two CURRENCY cash-only lines, the REJECT loop and the `res.notes()` loop in `load`).
