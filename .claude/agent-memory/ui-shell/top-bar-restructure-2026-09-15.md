---
name: top-bar-restructure-2026-09-15
description: dcc.Tabs holding no children (always-present tab-bodies Div + show/hide callback), header perf split, Ladder title/Today/rate-proxy, upload date-picker removal
metadata:
  type: project
---

Same-day follow-up to [[ladder-simplification-2026-09-15]]. Coordinator messages kept
arriving mid-task (structure fix, then three more Ladder-tab item batches, then a perf
finding, then an upload-flow change) — each was folded in before reporting done, per this
agent's working mode.

**Structure bug fixed**: `dcc.Tab(children=[...])` nests a tab's body inside Tab's own
styled wrapper, which was pushing the navy `.top-bar` around the whole page (upload/header
never visible on load). Fix: `dcc.Tab(label=label, value=label)` carries **no** children;
`ui/app.py build_layout` now puts all four tab bodies in one always-present
`html.Div(id="tab-bodies", children=[html.Div(body, id=f"tab-body-{slug}")...])`, sibling
of the top-bar/header, and registers one `create_app`-level callback
(`Output("tab-body-*","style") x4, Input("main-tabs","value")`) that hides the
non-selected ones. Bodies never leave the DOM, so every tab module's own callbacks (keyed
on ids inside their body) keep firing regardless of the selected tab. `tests/test_ui.py`
asserts both the no-children rule and the toggle callback's wiring.

**Ladder as-of default = today (America/New_York)**, not the last BNP snapshot date —
`cash_ladder.today_ny()` (zoneinfo). Only the Ladder tab and the header store (which
mirrors the Ladder picker) use it; Blotter/Market data/Reconciliation still default to the
last snapshot since they render the BNP file itself.
`engine.ladder.exposure_adapter.records_from_db` already filters `trade_date <= as_of <=
settle_date` generically, so passing today's date "just works" without an engine change.

**Ladder title row**: bare date-picker card replaced by `cash_ladder.heading_date_text`
("Tuesday 15 September 2026") + the picker + a "Today" button
(`cash_ladder.TODAY_BUTTON_ID`) that writes `today_ny()` into the same date-picker
Output — this collides with `uploads._confirm`'s existing Output on
`cash-ladder-date.date`, so **both** now need `allow_duplicate=True`.

**Rate fallback chain extended (item 5)**: BNP's file has no SPOT for AUD/EUR/GBP/XAU
(their `Fx` is 1 on those rows), so after `bnp_bval_rates` (SPOT) still leaves currencies
missing, `cash_ladder.bnp_forward_proxy_rates(conn, as_of, needed)` picks the
EARLIEST-settle_date `FWD_OUTRIGHT` mark (source `BNP_BVAL`) from the latest snapshot
`as_of_date <= as_of`, as a spot proxy, source-labelled `"BNP forward proxy"` — tracked in
its own `forward_proxy_ccys` set, separate from the plain SPOT-fallback set, so the
headline caption reports both counts distinctly ("13 currencies on BNP file rate...; 4
currencies on BNP forward proxy: ..."). Both sets are unioned for the `*` marker in
`combined_risk_table` / the "Rate source" row.

**Headline numbers redefined**: Delta combined = gross (Σ|USD delta| currencies + |futures|,
net beneath); Delta non-forward = futures USD delta, rendered as a real `0`/"no open
futures" caption when there are none (not Unavailable — `no_open_futures` check on
`futures['by_instrument']`/`['missing']` both empty); Delta forward = currency gross (was
net-only before), net beneath. `ui.tabs.exposure.headline_numbers` now takes
`fallback_ccys` and `forward_proxy_ccys` and renders the once-only fallback caption itself
(the old separate `fallback_note` in `exposure_section` was removed to avoid duplicating
it).

**Table style unified**: `ui.tabs.exposure._MONO`/`_HEAD`/`_TABLE_STYLE` now use the body
font stack (Inter/system, not monospace) and one `style_table={"overflowX":"auto",
"minWidth":"100%"}`, applied to `combined_table`, `combined_risk_table` and
`futures_table` alike so all three Ladder tables share one look; the empty-futures message
uses `.section-kicker` instead of an inline color so it also matches.

**Upload control**: pinned top-right of the top-bar permanently via CSS
(`.top-bar .source-strip { flex: 0 0 auto; max-width: 420px; align-items: flex-end; }`) —
it's a layout-level sibling of the tab bodies so tab switching never moves it.
Date-picker removed from the confirm stage entirely (coordinator decision): the filename
already carries the snapshot date via `data.ingest.upload.suggested_date` (T-1 business
day), shown as read-only text (`SNAPSHOT_TEXT_ID`); a `dcc.Input(type="date")`
(`MANUAL_DATE_ID`) only appears when the filename has no recognisable date. Both funnel
into one `dcc.Store(id=DATE_PICKER_ID)` (was a `DatePickerSingle`, same id, now a Store)
that `_confirm` reads as `State(..., "data")` instead of `"date"`.

**Header perf split** (coordinator finding: `_build_chart` used to run ~20
`value_book` evaluations, ~4s, blocking the figure cards too): `register_callbacks` now
registers two callbacks — figures update immediately on `AS_OF_STORE_ID` alone; the LTD
chart is a second callback gated on `Input(DETAILS_ID, "open")` (the collapsible's own
state) **and** `AS_OF_STORE_ID`, so it only computes when actually expanded.
`header._build_chart(conn, as_of, db_path=None)` gained an optional `db_path` param
(kept optional/back-compat because `tests/test_ui_blotter.py::test_build_chart_returns_graph`
calls it with only 2 args and isn't mine to edit) that enables
`header._cached_ltd` (`functools.lru_cache` keyed on `(db_path, os.path.getmtime(db_path),
as_of)`) plus a new `header._business_days_back` helper that walks
`engine.pnl.aggregate._is_business_day`/`load_holidays` (private-underscore names, but
that's the module the coordinator pointed at) and stops at `MIN(trade_date)` from
`trades` — so the chart no longer wastes evaluations on weekends/holidays or on dates
before the book existed. This intentionally changes `_build_chart`'s point count when the
earliest trade date is close to `as_of` (was always exactly `_CHART_LOOKBACK_DAYS`
calendar days; now up to that many *business* days, fewer if the book is young) — that
behavior change broke `test_ui_blotter.py::test_build_chart_returns_graph`'s hard-coded
"== 20" assertion on its fixture (earliest trade 2026-06-01 -> only 14 business days
before 2026-06-20). That test is outside this agent's ownership (not `tests/test_ui.py`)
so it was left failing and reported rather than edited; needs a fixture/assertion update
from whoever owns it.

**Concurrent-edit hazard observed**: mid-task, `ui/tabs/blotter.py` /
`ui/tabs/blotter_pricing.py` (not owned by this agent) were being actively rewritten by
another agent in the same working copy, transiently breaking `tests/test_ui_blotter.py`
(`NameError: PERIOD_ORDER`, then several `AttributeError`s) independent of anything this
session touched. Confirmed via `git diff --stat` showing changes to files never opened
this session. When `tests/` failures appear outside `test_ui.py`, check `git diff <file>`
before assuming your own change caused it. One genuine collision did land in
`tests/test_ui.py`'s own `dynamic_prefixes` allowlist (the blotter agent renamed its
per-sub-tab ids to `blotter-row-detail-*`) — fixed by adding that prefix since that
allowlist is inside this agent's owned test file.
