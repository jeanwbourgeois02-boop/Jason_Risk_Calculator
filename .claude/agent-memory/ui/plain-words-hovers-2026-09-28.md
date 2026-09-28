---
name: plain-words-hovers-2026-09-28
description: User-approved wording for the Trades row hovers and the Risk margin/limits hovers (no mark codes, no file paths, no "underlyer"); where each pass lives and why the limits-file pass is local to risk.py
metadata:
  type: feedback
---

Every hover a tab builds itself goes through `formatting.plain_words` (the Trades tab's mark /
P&L / previous-close tips, `mark_used_text`, the row panel's spot source), and the Risk tab's
margin and limits hovers name the file as "the limits file" / "the limits you set" through a
local `risk._limits_words`, never `config/limits.yaml`; "underlyer" reads "the position" or
"the commodity or currency" on the column hovers (user-approved wording, 2026-09-28).

**Why:** the user's rule of 2026-09-28: no engine words, file paths or identifiers on any
screen. The limits-file pass is kept out of `plain_reason` and `plain_words` deliberately so
the Data issues drawer and every other screen keep their pinned text (the housekeeper's brief).

**How to apply:** a new hover built from an engine `reason`, `note` or `mark_source` is
wrapped in `plain_words` at the point it is built (the kit's chokepoints only cover `about`,
`marker`, `missing_cell`, `issues_drawer`). A raw source code seen on a hover (e.g.
`CLOSE_OUT_FILL` -> "the closing fill") is added to `_PLAIN_PHRASES`, one line, not reworded
per tab. `mark_used_text` says an em dash (`MISSING`), never "n/a". A test pin on an engine
reason should assert the plain form loosely ("no price" rather than "no price for": the
engine's "no FUTURE_PX mark for ..." renders "no price mark for ...").
