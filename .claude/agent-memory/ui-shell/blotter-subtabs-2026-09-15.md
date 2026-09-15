---
name: blotter-subtabs-2026-09-15
description: Blotter rebuilt into five sub-tabs (Total book/FX/Rates/Options/Bundles) with row-scoped P&L strip and BNP_BVAL fallback pricing
metadata:
  type: project
---

2026-09-15: `ui/tabs/blotter.py` rebuilt per user decision into `dcc.Tabs` sub-tabs
(`className="subtabs"/"subtab"/"subtab--selected"`): Total book, FX, Rates, Options,
Bundles, in that order (`blotter.SCOPE_ORDER`). Each non-bundle sub-tab has its own
`dash_table.DataTable` (`blotter-datatable-{scope}`) and P&L strip container
(`blotter-strip-{scope}`), all rendered dynamically inside `blotter-content` — same
pattern as the old single `blotter-datatable`/`blotter-subtotal`.

**Why a new pricing module** ([[ui-shell]] convention: no calculation in `ui/`, only
calls into engine): the analyst's DB has no Bloomberg marks, only `BNP_BVAL` (written
from the BNP file), so `engine.pnl.valuation.value_book` alone returns every row as
NaN/reason. `ui/tabs/blotter_pricing.py::priced_value_book` retries a missing mark with
`marks_source='BNP_BVAL'` and relabels `mark_source` to `"BNP file (not Bloomberg)"` —
rows always render (trade/fill/dates/status) even with zero marks on file; only the
P&L columns go blank. `row_scoped_period_pnl` recomputes LTD/Daily/Previous
day/5d/MTD/YTD/Trading for exactly the currently-visible (post header-filter,
`derived_virtual_data`) trade_ids, not the whole scope.

**Engine addition**: `engine.pnl.ledger.period_reference_dates(as_of)` (public wrapper
over the private `_period_refs`) was added — the coordinator explicitly authorised this
one function in `engine/pnl/`, normally out of ui-shell's directory. Returns ISO date
strings `daily` (T-1), `previous_day` (T-2), `d5`, `mtd`, `ytd`; "Previous day" period =
`ltd(daily) - ltd(previous_day)`.

**Bundles**: new `bundles(name, description, created_at)` table (schema.py) plus
`data/ingest/themes.py::{create_bundle, list_bundles, bundle_pairs,
add_pair_to_bundle, remove_pair_from_bundle}` — membership is NOT stored in `bundles`,
it IS `instrument_theme.theme == bundle_name` (so `period_pnl_by(..., 'theme')` already
groups a bundle's trades with no join). `set_theme`'s existing docstring note applies:
an instrument-level bundle assignment does NOT retroactively relabel already-loaded
trades' `trades.theme` — only trades loaded afterwards inherit it. This is a
data-ingest-owned quirk, not something ui-shell can fix from `ui/`.

**Collateral for C5/housekeeper (not ui-shell's to edit)**: `tests/test_ui.py::
test_every_static_callback_id_exists_in_layout` has a `dynamic_ok` allow-list
(`{"blotter-datatable", "blotter-subtotal"}`) for components rendered inside a
callback's own output rather than the static layout. It needs the new dynamic ids added:
`blotter-datatable-{total,fx,rates,options}`, `blotter-strip-{total,fx,rates,options}`,
and the bundles-tab ids (`blotter-bundle-list`, `blotter-bundle-detail-container`,
`blotter-bundle-selected`, `blotter-bundle-revision`, `blotter-bundle-status`,
`blotter-bundle-create`, `blotter-bundle-add-pair(-button)`,
`blotter-bundle-remove-pair(-button)`, `blotter-bundle-name`, `blotter-bundle-desc`,
`blotter-bundle-pairs`). ui-shell does not own `tests/test_ui.py` per the brief, so this
was reported rather than edited; that one test is red until C5/housekeeper updates it.
