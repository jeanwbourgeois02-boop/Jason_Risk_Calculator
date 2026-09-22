---
name: datatable-loading-state-wipes-typing-2026-09-21
description: Why an editable DataTable cell "cannot be typed into" on the Bloomberg PC while every test passes -- dash-table demotes the active input to a label while ANY callback with Output(table,"data") is in flight; the Options refresh gate; Strike column moved up front
metadata:
  type: project
---

2026-09-21, user: "make it so you can input strike in the table" -- three days after
[[options-inline-terms-and-headline-2026-09-18]] shipped editable Strike/Type/Payoff cells
with green tests. Lane: `ui/tabs/options.py` + `tests/test_ui_options.py` only.

**The library fact (read in the installed bundle, Dash 4.4.1 -- no browser needed).**
`dash/dash_table/async-table.js`:
- the DataTable wrapper takes `loading_state = useLoading(filterFunc: property === "data")`;
- cell type: `Input: active && editable && !loading ? Input : Label`, dropdown cells get
  `disabled: loading`;
- `CellInput.UNSAFE_componentWillReceiveProps`: `state.value !== props.value -> setState(props.value)`.
`dash-renderer` dispatches `loading(outputs)` for every DECLARED output when a callback is
DISPATCHED and `loaded` in its `finally`. So for the whole server round trip of any callback
that lists `Output(table, "data")`, the cell being typed into is a label: its input is
unmounted and the typed text is gone. **Returning `no_update` does not help** -- the damage
is at dispatch. A re-sent `data` additionally resets the text.

**Why it only bites on the Bloomberg PC.** `options._render` listened to DATA/BOOK revision
stores; the live feed publishes a revision every few seconds there, never on the dev
machine. It also bit deterministically after each saved strike: `blotter._publish_option_cell_edit`
publishes the data revision on a save, re-dispatching `_render` just as the next strike is
typed. No Python test can see any of this.

**How to apply.** A callback that outputs to an editable table's `data` must never be
triggered by a timer/revision while the user may be typing. Pattern now in options.py: a
gate callback (`_gate_refresh` / pure `refresh_gate`) is the only listener of the revision
stores, outputs to a `dcc.Store` (`options-terms-refresh`) and a note, NOT the table, and
holds the revision while `active_cell.column_id` is an editable column; `_render` takes
`Input(REFRESH_ID)`. `active_cell` is the only usable signal: `is_focused` is set by a
DOUBLE-click only, not by click-and-type; after Enter the selection moves one row down in
the same column, so the hold persists (a visible "refresh paused" line says so).
`ui/tabs/rates.py::_refresh` has the same shape (Direction dropdown + revision inputs) and
was NOT changed -- same exposure, reported.
Structural test: `test_no_callback_that_writes_the_table_listens_to_a_revision_store`.

**Residual, reported not fixed.** The edit's own `_render` round trip (save + price + full
book re-price because the mtime moved) still demotes the NEXT cell to a label: digits typed
during it are swallowed, so a fast "152" can land as "52". Pre-existing.

**Discoverability.** Strike was the 23rd of 26 visible columns (MARS slot after
Underlying), ~2,000 px right, with the scrollbar under the last row + 120 px padding, while
the row's note said "type it in the Strike cell". Moved to right after Payoff. CLAUDE.md's
Options bullet still lists Strike in MARS order (housekeeper wording; user to confirm).
Every option has `package_id = trade_id` (blotter.py and manual.py), so multi-leg option
packages do not exist in real data; `default_collapsed_packages` now leaves a package with
a missing-strike leg expanded anyway.

**Trap when scanning the bundle from Python:** never `cd` into `site-packages/dash` to run
`py -3` -- `dash/types.py` shadows the stdlib `types` and the interpreter will not start.
