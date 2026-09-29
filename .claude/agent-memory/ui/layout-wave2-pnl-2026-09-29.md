---
name: layout-wave2-pnl-2026-09-29
description: Layout wave 2 on the P&L tab (2026-09-29) - no % of total, blank split parts, row kinds (total/group badge vs trade/leg row_info), lean headline, chart card title + annotated empty frame, track record as kv tables; shot recipe
metadata:
  type: project
---

P&L tab after layout wave 2 (`ui/tabs/pnl.py`, CSS block `/* wave 2: pnl */` at the end of style.css).

- **% of total cut** (769 % on a near-zero total). Never bring back a share against a signed total.
- **Row kinds** `ROW_TOTAL / ROW_GROUP / ROW_TRADE / ROW_LEG` in `_cells`: total and group rows carry ONE
  "Excl. N" on the P&L cell; trade and leg rows no badge, reasons on the figure hover + `row_info` after
  the name (`_row_reasons`). Split parts (Spread/FX/Hedge/New/Realised/Other, LTD Realised/Open) are blank
  when |v| < 0.5 except on the total row; the P&L column always keeps its figure. Heading hover says
  "A blank cell: nothing in this part". Months: blank where no fill of the row was on file that month.
- **Headline** = Vs close (ref date), To (custom only), Best trade, Worst trade (only when > 0 / < 0).
  Count, total, split live in the total row; the header has the rest.
- **Chart** returns `Div([card-head about title, Graph])`; no data or all zero -> an empty plotly frame
  with a centred annotation (a Graph is "placed" for the checker; a P would be LOOSE_BLOCK). <= 3 days:
  category x axis with "Tue 29 Sep" ticks.
- **Track record**: `_kv_table` x2 ("Days", "Closed trades", class `book-table tk-table tk-kv` for the
  navy head) beside the by-type table in `.pnl-track-grid`.
- Drawer items are triples (period title, "TRADE · Fill id", reason).

**Why:** user "things wandering about not in tables", one place per number, badges -> one "i".
**How to apply:** shot recipe = scratchpad `w2pnl/shot_pnl.py` (build_sample + werkzeug on a socket-picked
port, since `ui_check._free_port` no longer exists; click the "P&L" tab text, wait `#pnl-table`; slice
switch = `.tf-bar label` has_text "Type"). The checker's own `--shots` did not wait for the P&L body
(showed "Loading the P&L...") on 2026-09-29.
