---
name: book-what-two-lines-chips-2026-09-30
description: Book What it is in two lines (position / grey sizes + hedge), flags as chips, "2 spreads" across Entry/Now, signed level moves, research-missing said once (Book + Risk) and z column hidden; builder rules and proof recipe
metadata:
  type: feedback
---

User, 2026-09-30, on the Book pasted from Jason's real book (Bloomberg PC, no research db): "yeah lets fix
all of these things" (six items). Supersedes the one-sentence form of book-size-in-words-2026-09-30.

**Why:** "Long 2,521 SGX iron ore Feb27, 1,000 Mar27 vs short 2,521 Oct26 ... + 1 more" and "CNH −599 % hedged"
could not be read; "Unbalanced +1" hid flags; brackets on a level move read as money; three drawer lines each
dumped the rv.sqlite path.

**How to apply (ui/tabs/book.py):**
- `what_parts(t, roots)` -> (line1, line2 parts, others). Line 1 long side FIRST always ("Long COMEX copper /
  short LME copper"); sides by sign, lots added per side; months inline only for one root (calendar "Long CME
  HRC Oct–Nov26 / short Dec26", second side never repeats the name) or one side ("Short SHFE silver Dec26");
  a cross puts months on line 2 ("Nov/Dec26"). Exchange only via `_needs_exchange` (commodity words or its
  subsector on >1 exchange, unless the name is its own benchmark: WTI, Brent, feeder cattle) and said once
  while it stays the same ("Long LME copper / short aluminium"). Names sharing a last word compress
  ("live and feeder cattle"). FX pair said once per side; FX notionals listed "5,000,000 + 2,000,000 USD".
- Line 2 = sizes "34 v 14 lots" · months · "COMEX leg closed" (`_closed_side_words`, cross types) · "+ N more
  spread(s)" · "+ N not recognised" · hedge (`hedge_parts`: "CNH 92 % hedged" only for 0..150 %; amber
  "CNH hedge runs the wrong way[, 6× the exposure]", "CNH hedge 14× the exposure", "CNH hedge left, no CNY
  leg open"; engine sentences on hover). Trade copy keys: what_words (both lines, search), what_title,
  what_sub, what_others. Several subs with ONE root set (ZNA1 month pairs) = one position, not "+ N more".
- Flags: every flag a `tk-chip` (`tk-chip--red`), FLAG_NAMES "Type ≠ PBRoot", "Hedge too big"; total row keeps count.
- `parts_count(t)` (>1 subs, blank level with mode/source '') -> one Td colSpan=2 "2 spreads ▸" (`_parts_td`);
  panel part rows (`_sub_tr(..., level)`) show each part's entry under Avg fill, now under Mark.
- `level_text(signed=True)` = real sign "+3.06" / "−0.0601", never brackets.
- Research missing: `tf.research_source()` now has missing/tried; `tf.research_issue`, `is_research_missing_reason`
  filter the engine's repeats; `show_z_column` drops z (columns(), lead colSpan, total Tds). Risk: same one line,
  `source_line` plain, `risk_limits.plain_reason` collapses "X Comdty: no commodity history database: tried..."
  to "research data not on this PC" (else ENGINE_WORD in ui-check with COMMODITY_HISTORY_DB missing).
- Proof: render_book.py / serve.py in the session scratchpad patch `book.gathered` with fake SCO1 / SILARB1 /
  HEDGEL1 (re-run `book._with_words` on the copies); run ui-check twice, once with
  COMMODITY_HISTORY_DB=C:\nowhere\rv.sqlite. Trap: never leave a bare `py -3 -` in a command (REPL spins,
  64 MB output); write edit scripts with Write, not heredocs.
