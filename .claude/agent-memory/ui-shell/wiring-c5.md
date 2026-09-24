---
name: wiring-c5
description: ui/app.py assembly pattern for the tab layout (Blotter, Ladder, Curve, Expiries, Risk, Market data + header as of 2026-09-24), how a new tab is wired in, and two test-helper gotchas (Dash callback wrapper, _walk skipping dcc.Interval)
metadata:
  type: project
---

`ui/app.py` assembles `ui/tabs/{header,blotter,cash_ladder,curve,expiries,risk,market_data}.py`,
each exposing `build_layout(default_date)` / `register_callbacks(app, get_db_path)` (header
also has a no-arg `layout()`). `VISIBLE_TABS = ["Blotter", "Ladder", "Curve", "Expiries",
"Risk", "Market data"]`; the app opens on the first. Reconciliation was removed 2026-09-16;
Risk (`ui/tabs/risk.py`) added 2026-09-22; Curve and Expiries (ui-curve / ui-expiries,
commodity conversion) added 2026-09-24 after the Ladder, same no-picker pattern as Risk.
Tests pin the order via `SIX_TABS` in tests/test_app.py; index bodies by label, not number.

**Wiring a new tab (2026-09-22, Risk):** import it in `from ui.tabs import ...`, add the
label to `VISIBLE_TABS`, an entry in `tab_builders` and `tab_defaults` inside
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
