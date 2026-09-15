---
name: ladder-reorder-headline-2026-09-15
description: Ladder tab "Reorder" pass -- equal gross/net headline cards, table order, stress.yaml scenario-order rendering, header-strip compaction
metadata:
  type: project
---

Supersedes [[ladder-simplification-2026-09-15]]'s exact headline-card and table-order
description (the 3-headline / 3-table *count* is unchanged, only their internals/order).

**Headline cards** (`ui.tabs.exposure._gross_net_card`): every card shows the GROSS
figure bold, then directly under it a NET line — small-caps "net" label + an equally
bold value coloured green/red by sign (gross itself always neutral). A card's `note`
param *replaces* the net line entirely (used for the Unavailable reason string and for
"no open futures"); a card never shows both. Order is still Delta combined / Delta
non-forward / Delta forward.

**Table order** in `exposure_section`: ladder grid ("Cash ladder: spot, forwards, swaps
and cash balances") → open futures → risk/scenario table ("Risk and scenarios"), each
with its own `H4` title. This is the opposite of the risk-table-first order from the
prior session — a follow-up user message reversed it explicitly.

**Scenario column order**: `config/stress.yaml`'s own insertion order, not alphabetical.
`load_scenarios` returns a plain dict (PyYAML preserves mapping order), so
`combined_risk_frame`/`combined_risk_table` must use `list(scenarios)`, never
`sorted(scenarios)` — this was a live bug fixed this session. Header cells get
`whiteSpace: normal` so long scenario names wrap to two lines.

**NDF marker removed**: the "Settlement type" summary row and its NDF super-header in
`combined_table`/`by_ccy_label` are gone outright (user decision) — NDF currencies stay
in the grid with no marker at all. `by_ccy_label` was deleted from exposure.py.

**Ladder title row**: single row, no card wrapper — `html.H3("Cash ladder")` left,
date-heading + date picker + Today button right, via a `.ladder-title-row` /
`.ladder-title-row-right` CSS pair (kept name-compatible with the Blotter tab's own
title row, which the blotter agent styled identically the same day — do not rename
without checking `ui/tabs/blotter.py`).

**Header strip compaction** (`ui.tabs.header.py`, edited with explicit coordinator
permission even though it's not normally an owned file): added a `previous_day` period
(LTD@T-1 − LTD@T-2) to `ui.tabs.blotter_pricing.scoped_period_pnl`, and a new
`ui.tabs.cash_ladder.net_gross_usd(conn, as_of)` helper (FX-only Net/Gross USD, same
records/rates/fallback path `_render` uses) so the header's Net/Gross figures always
match the Ladder tab for the same as_of. `header._pnl_card` renders bold sign-coloured
values, `colour=False` for Gross (always neutral per spec), and "n/a" + an HTML `title`
tooltip carrying the reason when unavailable. A `_divider()` between the P&L cards and
Net/Gross/as-of/marks-time cards must render a non-None child (`children=[html.Div()]`)
or it breaks `tests/test_ui_blotter.py::test_build_figures_includes_all_periods`
(owned by the blotter agent, does `c.children[0].children` over every header card).

**Concurrent editing gotcha**: `ui/app.py`, `ui/tabs/blotter.py`, `ui/uploads.py`,
`engine/pnl/valuation.py` were being edited live by other agents during this session.
Two `tests/test_ui.py` failures (`test_four_tabs_present_in_order`,
`test_tab_show_hide_callback_toggles_bodies`) traced to the other agent's in-flight
`ui/app.py` changes, not to this session's edits — verified by running the ladder/header
-scoped tests in isolation (all green) before committing only this session's files.

Requested-but-out-of-scope: moving the "Loaded: BNP report..." line + Upload history
onto the header figure row lives in `ui/app.py`'s top-bar assembly, which this agent
does not own — flagged rather than edited.
