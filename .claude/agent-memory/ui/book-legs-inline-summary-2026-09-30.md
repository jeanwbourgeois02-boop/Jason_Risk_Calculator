---
name: book-legs-inline-summary-2026-09-30
description: Book By trade shows every trade's legs as always-visible lighter rows under it (leg_rows), the panel keeps only value/roll-down/gross/carry; a Summary card above (Break down by Spread type | Commodity family | Commodity) whose Book row = header Daily/LTD
metadata:
  type: feedback
---

User, 2026-09-30 (after the research app's Book): "the regular book not expanded to show me both legs
with their pnl just like that - and then the summary".

**Why:** a click to see the legs was what bugged him; the summary gives the by-type view at a glance.

**How to apply (ui/tabs/book.py):**
- `leg_rows(data, t, hidden)` after each trade row in `table()` (none for the pseudo "No trade name" row):
  `leg_sequence` = `_leg_order`, part head rows (`part_tr`) only for >1 sub_spreads, hedges last. Leg row
  cells in the trade table's own columns: name spans Trade + What (colSpan 2, tag "Hedge" / "Closed" /
  red "Not recognised"), Quantity `leg_qty_words` (size_words), Entry = avg_fill, Level now = mark (≈ grey
  when INTERP or a price to check, `mark_hover`), P&L today / since entry = `fill_sum` over the leg's
  trade_ids (same source as the trade row, so legs add up exactly; checked 0 mismatches on the sample),
  Open / Locked in = engine `split_value(leg)`; z / Next / Check blank. A closed trade's legs: Open blank.
- Panel: `panel_legs` now only Leg | Value USD | Roll-down / mo for open legs + Trade total (gross, carry);
  None when nothing is open. LEG_COLUMNS shrank to those three.
- Summary: `summary_card()` static in the layout (first child of CONTENT_ID), `_summary` callback fills
  SUMMARY_SLOT_ID + SUMMARY_COUNT_ID from AS_OF, DATA_REVISION and SUMMARY_BY_ID (session-persisted
  RadioItems). Never filtered; groups sorted by |LTD|, "No trade name" last; % = group / book LTD, never
  totalled. `commodity_label` = non-hedge legs' commodities joined, metal pairs as the metal, FX "FX".
- ui-check's static Book render never fills the summary slot (tools/ui_check `_trade_tab` fills only the
  tab's known slots): check it with `walk` + `check_runs` on `book.render(...)`. The count line carries
  class `tk-headline` (a DESIGNED_LINE, report-only), else it is a LOOSE_BLOCK in the strip.
- CSS at the end of style.css: no zebra on `.tk-trades` (`:where` white / leg tint), leg rows 12px.
