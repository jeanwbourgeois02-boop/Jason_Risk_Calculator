---
name: blotter-filters-suppress-callback-exceptions-2026-09-16
description: Root cause of "blotter filters don't work" — missing suppress_callback_exceptions=True in ui/app.py, plus stale react-select CSS for the current Dash Dropdown
metadata:
  type: project
---

2026-09-16 bug report: "filters on the blotter tabs don't work and look bad." Root
cause was NOT the filter logic in [[blotter-native-filters]] / [[blotter-subtabs-2026-09-15]]
(the `dcc.Dropdown`-per-column callback in `ui/tabs/blotter.py::_apply_filters` was
verified correct both as a bare Python function call and end-to-end through
`app.server.test_client()` once the fix below was applied). Two independent things
were actually wrong:

1. **`ui/app.py::create_app`** built `dash.Dash(__name__)` with
   `suppress_callback_exceptions` left at its default `False`. The Blotter sub-tabs'
   table/filter-dropdown/detail-panel ids (`blotter-datatable-{scope}`,
   `blotter-datatable-{scope}-filter-{col}`, `blotter-datatable-{scope}-detail`) only
   ever exist inside `blotter._update`'s own callback `Output` (`CONTENT_ID` children),
   never in the static `app.layout` tree at startup. Dash's default id validation
   silently rejects any callback wired to an id it can't find in the initial layout —
   so every filter dropdown, the row-click detail panel, and the P&L strip on every
   Blotter sub-tab were dead in the browser even though the Python callback function
   itself was correct. Fixed with one line:
   `app = dash.Dash(__name__, suppress_callback_exceptions=True)`.
   **How to check this class of bug in future**: calling the callback function directly
   (even via `__wrapped__`, per [[futures-upload-diagnostics-2026-09-16]]) does NOT
   catch this — it bypasses Dash's id-validation layer entirely. The only way to catch
   it is a round trip through `app.server.test_client().post("/_dash-update-component",
   ...)`, which is what `tests/test_ui.py::test_blotter_filter_dropdown_end_to_end_narrows_table`
   now does.

2. **`ui/assets/style.css`**'s `.blotter-filter-bar` block (added 2026-09-15) targeted
   react-select v1 class names (`.Select-control`, `.Select--multi`, `.Select-value`,
   `.VirtualizedSelectOption`, ...). The installed Dash (4.4.1) ships a rewritten
   `dcc.Dropdown` with NO react-select dependency — its real DOM classes (confirmed by
   grepping the installed `dash/dcc/async-dropdown.js` bundle) are `dash-dropdown-wrapper`
   / `-trigger` / `-content` / `-search` / `-options` / `-option` / `-value` /
   `-value-item` / `-value-count` / `-clear`, plus `dash-options-list-option` for the
   virtualized multi-select list. The old rules matched nothing, so every filter
   dropdown rendered as the raw unstyled browser widget. CSS rewritten to target the
   real classes.

**How to apply**: before writing CSS against any dcc component's internal DOM
structure, grep the installed `dash/dcc/*.js` bundle for the classnames rather than
assuming react-select conventions — Dash's own component internals have changed
Dash-version to Dash-version and this project pins whatever `pip install dash` resolves
to (see [[environment]] for the dash version check command). If `.venv`/site-packages
gets reinstalled at a different Dash version, re-verify these classnames don't drift
again.

See [[blotter-subtabs-2026-09-15]] and [[blotter-native-filters]] for the filter
feature's own history/design; this memory is about the wiring bug, not a design change.
