---
name: lazy-tab-bodies-2026-09-28
description: The shell builds only the first tab's layout with the page; the others build on first selection (ui/app.py build_tab_bodies, TAB_BUILT_ID store); why, the Dash rule it relies on, the browser proof recipe
metadata:
  type: project
---

Since 2026-09-28 `ui/app.py::build_layout` builds the Book's layout alone; the other five tab
bodies are empty `html.Div(id=tab_body_id(label))` wrappers until their tab is selected. One
callback (`build_tab_bodies`, pure: (selected, built) -> six children + the store) fills the
selected body the first time and records the key in `dcc.Store(TAB_BUILT_ID)`, so a tab is built
once per page and keeps its state after that. The show/hide callback (styles only) is untouched
and is what the shell tests pin ("..tab-body-book.style" key, `toggle("risk")`).

**Why:** the page's first load fired every tab's render callback (six 0.8-2.4 s full-book passes
in parallel for tabs nobody was looking at). After: the header (~0.9 s) and the Book (~0.9 s).

**Dash rule it relies on:** when a callback output inserts a layout chunk, the renderer fires
every callback that has an *output* in the chunk and at least one input present (the same as a
page load), and prunes on load any callback whose outputs are all absent (suppress_callback_exceptions
is on). Proven in headless Chrome: clicking Risk fired `risk-body.children`; clicking Data fired
its body callback and the two dropdown-options callbacks whose inputs are all outside the body.

**How to apply:** a tab's callbacks may keep Inputs outside the body (as-of store, revision):
they still fire on insertion because their outputs are inside. Never give a tab's render callback
`prevent_initial_call=True`, or it would not render when the tab is built. A test that walks
`build_layout` for another tab's ids must call `uiapp.tab_layout(key)` instead.

Proof recipe (scratchpad `lazy_load.py <db> <port> <png>`: a copy served on a spare port, headless
Chrome loads it, the server logs every `_dash-update-component` by outputs with timings;
`lazy_click.py <db> <port> <devtools port>`: Chrome with `--remote-debugging-port` and
`--remote-allow-origins=*`, driven over `websocket` (installed): click Risk, Data, Book and
print each body's text length and the callbacks seen). Change the ports on every run.
