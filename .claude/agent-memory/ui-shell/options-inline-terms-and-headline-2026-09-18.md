---
name: options-inline-terms-and-headline-2026-09-18
description: Options sub-tab rewrite (editable Strike/Type/Payoff cells, cost/value/P&L columns, Greeks headline, native numeric filters, flat view under a filter); two root causes, DataTable facts verified in a real browser, what survives the Blotter's rebuild
metadata:
  type: project
---

2026-09-18, `ui/tabs/options.py` + `tests/test_ui_options.py` only (five agents in
parallel, lanes by file). Supersedes the Greek-conversion and "read-only" parts of
[[options-tab-phase8-2026-09-17]].

**Root cause 1, "cannot input the strike".** The Option-terms editor is embedded in TWO
places (`options.build_layout` and `manual_entry.build_layout`) but its Save callback had
`options-collapsed-packages` as an Output and a State, and that store exists only under
Options. Dash's renderer DROPS a callback with a missing Output in the browser
(dash_renderer `executeCallback` -> `outputErrors` -> `executionPromise: null`), and with
`suppress_callback_exceptions=True` and no dev tools nothing is shown. The red banner
sends the user to Manual entry, where Save did nothing for ANY option. **How to apply:**
a component rendered in more than one place may only have callbacks whose every
Input/State/Output is inside that component or in the static page (`data-revision`,
`book-revision`, `blotter-date`). `test_terms_editor_callbacks_need_nothing_outside_the_editor`
pins it. Second cause under Options: `blotter.py::_update` rebuilds the whole options
scope on every data revision (`_MARKS_REBUILD_SCOPES`), which reset the editor's dropdown
to the first no-strike option and re-ran the prefill over what was being typed.

**Root cause 2, "headline doesn't work".** The strip above Options is blotter.py's generic
`row_scoped_headline`: no Greeks, and 6 of its 11 cards difference against a PAST close,
while option marks are only written for the live date (`live._options_step` prices
`today`; the backfill writes FWD_OUTRIGHT/FUTURE_PX only). Not a bug in the strip: those
cards stay n/a until the app has been live across those dates. The Greeks strip now lives
inside `options.build_layout` (`blotter-strip-options-greeks`).

**State that must survive the Blotter's rebuild of the sub-tab** (it is NOT mine to stop):
DataTable `persistence` (session) for `filter_query`/`sort_by`; `storage_type="session"`
on the collapse store (dcc.Store restores the stored value on mount when it differs from
the prop; a "memory" store is per instance and is lost); in-process dicts in options.py
keyed by the db file: `_PENDING_TERMS` (payoff chosen before a strike exists --
`set_option_terms` refuses a strike payoff with strike 0, so the choice waits and is saved
WITH the strike, never pricing a digital as a vanilla in between), `_LAST_SKIP`
(price_and_store's reason, shown on the row), `_LAST_SAVE` (editor flash message).

**`data.ingest.schema.connect()` WRITES on every open** (drops/recreates the views), which
moves the file's mtime, publishes a data revision and makes the Blotter rebuild. A refused
edit must therefore never open it: `apply_edit` reads through `connect_readonly` and only
`save_and_price` opens a writable handle. A test asserts the mtime is unchanged.

**`set_option_terms` writes every term it is given** (payoff defaults to VANILLA, barrier
to 0): a one-cell edit must hand the others back (`engine.options.store.on_file_terms`).

**Greeks in USD are the engine's**: `engine.options.portfolio.build_positions` fed a
`SimpleNamespace` outcome built from marks (quote_price=0.0, missing Greeks 0.0 then blanked
again). Units after the 2026-09-18 audit: delta = qty x DELTA x USD-per-BASE, gamma = USD
delta change per 1 % spot, vega/theta/rho x USD-per-QUOTE. My old copy used quote-ccy for
all five (USDJPY delta ~150x too small). Never keep a second copy of that formula here.

**DataTable facts, verified with real mouse/key events in headless Edge**
([[real-browser-verification-2026-09-18]]'s DevTools method works for typing into cells:
click the td centre, `Input.dispatchKeyEvent` per character, Enter):
- `editable` is per COLUMN only. Fence group rows with `pointerEvents: none` via
  `style_data_conditional` AND refuse them server-side.
- `dropdown_conditional` with a `filter_query` (`{is_leg} = 1`) gives dropdowns on some
  rows only; the others render the raw value as a label. Needs the classic
  `.Select-menu-outer {display: block !important}` css and padding under the table.
- `on_change: {"action": "coerce", "failure": "accept"}` on a numeric column: numbers
  arrive as numbers, junk arrives AS TYPED so the server can say what was wrong; the
  callback always answers with rows rebuilt from the DB, which is the revert.
- Column-level `filter_options` REPLACES the table-level one: repeat `case` in each.
- Edits arrive as `Input(table, "data_timestamp")` + `data`/`data_previous`. Only treat
  that trigger as an edit (`ctx.triggered`): `data_previous` stays stale afterwards, and
  re-applying it on a revision trigger would write -> revision -> write, forever.
- Unit-test a ctx-dependent callback with `dash._callback_context.context_value.set(
  AttributeDict(triggered_inputs=[{"prop_id": ...}]))` inside `contextvars.copy_context().run`.
- A `Format` object in a column spec is not subscriptable: store `.to_plotly_json()`.
- Sorting or filtering a grouped table is nonsense: while either is on, the rows are the
  legs FLAT, and the headline totals `derived_virtual_data` (skip the instant where the
  filter is on but the grouped rows are still in the table).

**Colours inside ANY DataTable (computed styles read in headless Edge, same day):**
- `ui/assets/style.css:213` forces `border-color: var(--line) !important` on every td/th:
  a coloured cell BORDER renders grey. Use `outline` + negative `outlineOffset`, or an
  inset `boxShadow` (rates.py does the same). Editable cue here: 1px dashed gold = holds a
  value; 2px solid gold on a gold ground = strike still missing (`NEEDS_STRIKE_QUERY`).
- dash-table defines its OWN `--muted` (#c8c8c8), `--accent` (hotpink), `--border`,
  `--hover`, `--text-color` on the table, SHADOWING the app's. `var(--muted)` text in a
  cell is near-white (the Note column's reasons were unreadable). Safe app variables:
  `--gold --navy --line --text --pos --neg --warn --warn-bg --card`. blotter.py (4),
  blotter_fx.py (1), rates.py (1) still use `var(--muted)`/`var(--accent)`: reported.
- Native filter row (Options is the app's first): filter cells are `th`, so style.css
  paints them navy `!important`; dash-table keeps typed text #3c3c3c and the placeholder
  TRANSPARENT until hover. Inline `style_filter` cannot beat `!important`; the table's
  own `css` prop can (`FILTER_ROW_CSS`, selector needs 3 classes + `th` to out-rank it).
- `Page.captureScreenshot` with a `clip` over CDP, then Read the PNG: fastest way to SEE it.

**New ids must reuse `tests/test_ui.py`'s dynamic prefixes** (`blotter-strip-`,
`blotter-datatable-`, `options-terms-`, `manual-`) or
`test_every_static_callback_id_exists_in_layout` fails; that file was out of lane.

**Collateral outside my lane, reported not fixed:** `tests/test_ui_blotter.py::
test_options_scope_prices_an_option_row` asserted formatted strings; the table now carries
floats (mktpx 0.0062, mktval 6851.0, delta 607750.0, strike 1.11).
