---
name: options-tab-phase8-2026-09-17
description: Blotter "Options" sub-tab (ui/tabs/options.py) -- grouped MARS-style table, two different currency conversions by design, no-native-tree collapse mechanism
metadata:
  type: project
---

2026-09-17 (options_calc merge Phase 8): the Blotter "Options" sub-tab is now
`ui.tabs.options` (new module), not the generic `priced_value_book` path any more.
Old ids `blotter-datatable-options` / `blotter-datatable-options-detail` and the
filter dropdowns are gone for this scope -- replaced by `options.TABLE_ID`
("options-datatable") and `options.COLLAPSED_STORE_ID`
("options-collapsed-packages"). `blotter.py`'s `scope_layout` delegates to it exactly
like the "rates" scope delegates to [[rates-tab-2026-09-15]]'s module: strip Div
(`blotter-strip-options`, generic `priced_value_book`-scoped headline, unchanged) +
`options.build_layout(conn, as_of)`. Both new ids had to be added to
`tests/test_ui.py::test_every_static_callback_id_exists_in_layout`'s `dynamic_ok` set
(they only exist once the "options" sub-tab is selected, same as
`blotter-datatable-*`) -- a real regression the suite caught, not a false positive.

**Read-only, never re-prices.** Unlike `engine/options/structures.py::combine_package`
(which calls `price_and_store` and WRITES marks), `ui.tabs.options` only reads
`marks_official` for PREMIUM/DELTA/GAMMA/THETA/VEGA/RHO
(`source='QL_OPTIONS_PRICER'`) and builds the grid purely from stored marks --
matching every other Blotter sub-tab and the "ui/ never recomputes P&L" rule.

**Two different currency conversions, by design:**
  - MktVal = PREMIUM (base-notional fraction) x quantity (base ccy) x BASE ccy -> USD
    spot -- literal task mapping.
  - Delta/Theta/Gamma/Vega/Rho = native per-unit sensitivity x (quantity x
    multiplier) x QUOTE ccy -> USD spot, `engine/options/portfolio.py`'s documented
    formula (quote ccy is correct there, not base ccy -- that module's own docstring
    explains why). Don't "fix" this into one convention; it's intentional and the
    task spec is explicit about MktVal using base ccy.

**Aggregation is a uniform sum-skip-missing (`_agg`) bottom-up** over the 8 numeric
columns (position/notional/mktval/delta/theta/gamma/vega/rho) at every level --
TOTAL == sum(ASSET_CLASS) == sum(PACKAGE) == sum(LEG) always holds, including when a
leg is missing a value (contributes nothing, not zero -- `_agg` returns `None` only
when *every* value in the group is missing). The 5 identity columns
(mktpx/expiry/underlying/strike/undfwdpx) use `_single` instead: shown only when the
group has exactly one contributing leg -- this is also how a single-leg package
"is its own one-row package" (no separate LEG row emitted at all when
`len(pkg_legs) == 1`; the PACKAGE row IS that leg, with the identity columns
naturally passing through since there's exactly one leg).

**Beware pandas None->NaN coercion**: `pd.DataFrame(rows)` on a column where every
value is `None` sometimes keeps `None`, but as soon as ANY row in that column has a
real float, pandas upcasts the whole column to float64 and every `None` becomes
`np.nan` (`is None` fails, `!= self` or `math.isnan` is required). Bit both the
production code (`_is_missing` uses `v != v`, not `is None`, for exactly this
reason) and my first test draft (fixed to `math.isnan(...)`).

**Collapse mechanism** (no native tree in `dash_table.DataTable`, same precedent as
rates.py/blotter.py having no dropdown-filter widget): `dcc.Store` of collapsed
package_ids (`hidden_columns=["level","group_key","parent_key","leg_count"]` keeps
those keys in `data` for `filter_query`/callback logic without showing them) + a
server callback (`options.register_callbacks`) that re-runs `option_rows` against
the CURRENT as-of date on every toggle click (does not cache/filter client-side --
matches `blotter.py`'s own filter-dropdown callbacks re-querying the DB on every
selection). Default: packages with >1 leg start collapsed; single-leg packages have
no toggle at all. `register_callbacks(app, get_db_path, date_picker_id="blotter-date")`
hardcodes the Blotter's date-picker id as a string literal (not an import of
`ui.tabs.blotter`, which would be circular since `blotter.py` imports this module).

**Live-verified via real HTTP against a running `app.run()` thread + Dash's own
`/_dash-update-component` endpoint** (not a headless-browser click test -- Selenium/
Edge was judged too heavy for this task's effort budget): confirmed the "options"
sub-tab selection renders the strip + grouped table with Portfolio Totals/FX/Equity/
Commodity rows, default-collapsed PKG1, and that POSTing the click-triggered toggle
callback then the store-triggered refresh callback correctly expands PKG1 into its
2 leg rows. This is a legitimate live-render check, just not a pixel-level one.

**Console gotcha (unrelated to Dash)**: printing the collapse arrow glyphs
(`▸`/`▾`) crashes on this machine's default cp1252 stdout encoding when
running ad-hoc verification scripts -- use `PYTHONIOENCODING=utf-8` for any
throwaway script that prints `options.format_rows` output directly.

Equity/Commodity asset-class groups render present-but-empty by construction (no
FX_OPTION-only filter on the leg query -- `trades_official WHERE product IN
('FX_OPTION','EQ_OPTION','CMDTY_OPTION')`), matching `engine/options/__init__.py`'s
note that no data-ingest parser exists yet for EQ_OPTION/CMDTY_OPTION trades; nothing
to change here when that lands, the grouping already reads `instruments.asset_class`
generically via `ASSET_CLASS_BY_PRODUCT`.
