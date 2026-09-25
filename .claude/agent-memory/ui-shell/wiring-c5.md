---
name: wiring-c5
description: ui/app.py assembly pattern for the tab layout (seven tabs, Spreads first, stable TAB_KEYS since 2026-09-25), how a new tab is wired in, and two test-helper gotchas (Dash callback wrapper, _walk skipping dcc.Interval)
metadata:
  type: project
---

`ui/app.py` assembles `ui/tabs/{header,spreads,curve,risk,expiries,blotter,cash_ladder,market_data}.py`,
each exposing `build_layout(default_date)` / `register_callbacks(app, get_db_path)` (header
also has a no-arg `layout()`). Since the screens redesign (2026-09-25) the order is
Spreads, Curve, Risk, Expiries, Blotter, FX & cash, Data (Book joins first in Phase B) and
`TAB_KEYS` maps each label to a stable key: the dcc.Tab `value` AND the body id
`tab-body-<key>` (`tab_body_id(label)`). Renamed tabs kept their keys ("FX & cash" ->
`ladder`, "Data" -> `market-data`), so a label may hold '&' or spaces but an id never does.
The show/hide callback compares `TAB_KEYS[label]` with the selected value, so tests call
the toggle with a key ("risk"), not a label. `_slug` was removed. Tests pin the order via
`SEVEN_TABS` / `SEVEN_KEYS` in tests/test_app.py; index bodies by label, not number.

**Wiring a new tab (2026-09-22, Risk):** import it in `from ui.tabs import ...`, add the
label and its key to `TAB_KEYS`, an entry in `tab_builders` (keyed by key) inside
`build_layout`, and one `x.register_callbacks(app, get_db_path=lambda: resolved)` line in
`create_app`. The tab bodies and the show/hide callback derive from `VISIBLE_TABS`, so
nothing else changes. A tab with no date picker of its own (Risk) follows
`header.AS_OF_STORE_ID`, whose default is the Ladder's `cash_ladder.today_ny()`, so give it
the Ladder's default date and do not touch `_follow_pickers` / `_roll_to_today`.

The header's as-of store (`header.AS_OF_STORE_ID`) follows the Ladder's and the Blotter's
date pickers via small `app.callback(...)`s written directly in `create_app()`; there is no
dedicated cross-tab store module.

**Why:** [[app-structure]] (old memory) describes the retired six-tab / summary()
placeholder layout; `docs/BUILD_PLAN.md` 2026-09-15 replaced it with one valuation + tabs
+ header, and CLAUDE.md "Tabs as views" is now the authoritative list.

**How to apply:** when re-wiring `ui/app.py`, check each tab module's actual
`build_layout`/`register_callbacks` signature first (they can drift). Test helpers in
`tests/test_app.py` / `tests/test_ui.py`: (1) a registered callback is invoked through
`getattr(cb, "__wrapped__", cb)` (Dash's wrapper wants `outputs_list` kwargs); (2) the
`_walk` helper in test_app.py descends only into children that have `children` or
`className`, so a leaf like `dcc.Interval` (neither) is never yielded -- use `_ids`
(walks by `isinstance(child, dash.development.base_component.Component)`) to find an
interval's id.

**Concurrent-edit hazard (2026-09-15):** another agent was mid-editing a tab module while
this agent ran the suite, so a `NameError` inside a file you don't own may be their
in-progress state; re-run before reporting it as broken. If you must stash, `git stash
push -- <only your files>`, never a bare `git stash` (it scoops their dirty files too).
