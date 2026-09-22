---
name: rates-direction-dropdown-2026-09-18
description: Rates table Pay/Receive dropdown cell (front end of data/ingest/irs_direction.py) and the DataTable gotchas found doing it - grey "gold" borders, react-select v1 cell dropdowns, same-value pick fires nothing, real-browser pick recipe
metadata:
  type: project
---

**Fact.** The blotter export (reference sample AND the user's own) has no pay/receive marker
for swaps: Side is "Buy" on all of them, notionals unsigned. Every swap loads as pay fixed.
Three of the ten sample swaps are really receivers (Trade Id 918421481 625M, 920118423 995M,
932385416 158.22M). The user agreed (2026-09-18) to set Pay/Receive by hand in the Rates
table. Data layer: `data/ingest/irs_direction.py` (data-ingest's, not mine) - overrides
survive every re-upload; a flip REVERSES the swap's QL_PRICER PV/DV01/cashflow marks in
place (history kept), so the table shows the opposite sign at once, no re-pricing needed.
`ui/tabs/rates.py` is only its front end. Never infer a direction from anything.

**DataTable gotchas (all seen in a real browser, not guessed):**
- `ui/assets/style.css` sets `border-color: var(--line) !important` on every DataTable
  `td`/`th`. An inline `border: ... var(--gold)` from `style_data_conditional` therefore
  renders GREY. Use `boxShadow: "inset 4px 0 0 var(--gold)"` for an edge and
  `outline` + `outlineOffset` for an input look. (`ui/tabs/options.py`'s dashed gold border
  on its editable cells has the same problem - reported, not fixed, it was out of lane.)
- `.dash-table-container { overflow: hidden }` clips a cell dropdown's menu: keep
  `css=[{".Select-menu-outer": "display: block !important"}]` AND `paddingBottom` on
  `style_table` (100px is enough for two options).
- Dash 4.4.1's DataTable cell dropdown is still react-select v1 (`.Select-control`,
  `.Select-option`, `.Select-menu-outer`), unlike `dcc.Dropdown` (see
  [[blotter-filters-suppress-callback-exceptions-2026-09-16]]). A REAL pick in headless
  Edge: dispatch `mousedown` on the cell's `.Select-control`, wait, dispatch `mousedown` on
  the `.Select-option` whose text matches. Recipe for the rest: [[real-browser-verification-2026-09-18]].
- Picking the value a dropdown cell ALREADY has fires nothing (no `data_timestamp`), so
  "confirm this one is pay fixed" cannot be done in the cell: that is why the notice has a
  "The rest are pay fixed: confirm" button.
- Detect edits as `data` vs `data_previous` by trade id, never `data` vs the database: a
  stale table would otherwise flip swaps the user did not touch.
- `prevent_initial_call=True` does NOT stop a call when the sub-tab is inserted later and
  one of the callback's Outputs (the revision stores) is already on the page. Guard on the
  values (no click, no differing cell -> all `no_update`).
- A filter_query in `style_data_conditional` keyed on a DISPLAYED column
  (`{set_by} contains "Not set"`) avoids a hidden bookkeeping column and its
  "Toggle Columns" button.

**Refresh model.** rates.py has two callbacks: the write (cell edit / confirm button; publishes
both `ui/revision.py` stores with `allow_duplicate`) and `_refresh` (Inputs = the two
revision stores; rows returned as `no_update` when nothing on screen changed). Because of
`_refresh`, "rates" could be taken out of `blotter.py::_MARKS_REBUILD_SCOPES` (done by the
blotter-side agent the same day). A flip still changes `book_signature` (TOTAL(quantity)),
so the Blotter rebuilds the sub-tab once after a flip; `_LAST_MESSAGE` re-shows the status
line in that rebuilt layout (same device as options.py's `_LAST_SAVE`).

**Test id note.** `tests/test_ui_blotter.py` pins the table id `rates-datatable`, so new
ids must use the `rates-` prefix, which `tests/test_ui.py`'s `dynamic_prefixes` now lists.
