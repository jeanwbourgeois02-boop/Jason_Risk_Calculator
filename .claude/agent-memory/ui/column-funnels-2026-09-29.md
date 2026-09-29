---
name: column-funnels-2026-09-29
description: Every table's filters are funnels in the column headings (trade_filter kit), no filter bar above any table; store shapes, the triggered-only sync rule, the JS re-open, ui-check DUPLICATE_FILTER, playwright click-test recipe
metadata:
  type: feedback
---

User, 2026-09-29, angry after five rounds: "the table columns - and above theres filters with the same
names ... you havent done it". Rule: filters live in the column headings, spreadsheet-style; nothing above a
table repeats a column name. Only search, Group/Slice switch, Clear filters (while set), Expand/Collapse, CSV,
and P&L's Period/Table stay in the strip. infra's `ui-check` DUPLICATE_FILTER rule now fails on a control
outside a table whose label repeats a heading.

**Why:** the user asked for it five times; never add a Dropdown / Checklist filter above a table again.

**How to apply:**
- The kit is in `ui/tabs/trade_filter.py`: `funnel(key, body, active, summary)` (a `<details class=book-pop
  data-tf-key=tab:col>`), `pop_list` / `pop_number` / `pop_text`, `head_th(title, cls, tip, sort_id, arrow,
  pop, right, note)`, `trade_funnel`, `number_funnel`, `parse_compare` / `passes` / `keeps_cols`,
  `triggered_values()`. Blotter (`bf-col`, own store) and Data (`md-col`, `market-data-marks-fstore`) use it.
- Shared store `trade-filter-store`: {search, type, commodity, trade, group, cols{"<tab>:<col>": text|list}};
  type/trade/commodity carry across Book, P&L, Risk; cols are per tab; `is_filtered(state, tab)`.
  Book Flags funnel = cols "book:flags" ["any","red"]. Commodity family = Book "What it is" funnel, the
  Trade funnel's second list on P&L and Risk.
- Heads re-render with the table (not static): the sync callbacks apply ONLY `triggered_values()` (a
  re-render inserts every control and fires with "."; applying all present values would undo a Clear).
- `book_filters.js` remembers the open panel's data-tf-key and re-opens it after a re-render (MutationObserver).
- dcc.Input takes no `title` (Dash 4.4: TypeError) - put the hover on a wrapping Div; its className lands on
  a wrapper, so select `.book-pop-panel input`, not `input.book-pop-box`.
- ui_check (infra's) still calls `tf.bar`, `module.options_of`, `fills.bar(None, opts, dates)`,
  `fills_table(df,len,None,None,AS_OF)`, `md.render` 11-tuple (out[4] options, out[6] tools style) and reads
  `md.MARKS_FILTER_ID`: keep those names.
- Click-test recipe: scratchpad `click_test.py` (run with `py`, the .venv has playwright; `py -3` lacks it):
  `tools.ui_check.build_sample`, `create_app`, `_free_server`, `_pick_as_of`, `_settle`.
