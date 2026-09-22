---
name: dash-details-open-never-synced-2026-09-22
description: Dash 4.4.1 never reports a native <details> toggle back to html.Details' `open` prop; a callback gated on Input(DETAILS_ID, "open") is unreachable by clicking. Fixed with a clientside mirror from the Summary's n_clicks; how to test a clientside callback; the running app does not reload.
metadata:
  type: project
---

**Dash fact (verified 2026-09-22, dash 4.4.1):** the html bundle
(`dash/html/dash_html_components.min.js`) wires only `n_clicks` / `n_clicks_timestamp` on
its elements (0 occurrences of "toggle"/"onToggle"/"setProps({open"). Clicking an
`html.Summary` opens the `<details>` in the browser, but the server's `open` prop stays
False, so `Input(DETAILS_ID, "open")` never fires. The header's LTD chart was unreachable
by clicking from the 2026-09-15 split (figures vs chart callbacks) until 2026-09-22
(user: "the LTD line chart not working"); a POST to `/_dash-update-component` with
`open=true` always worked, which is why server-side tests never caught it.

**Why it matters:** any collapsible whose content is built only while open needs a
clientside mirror: `app.clientside_callback(js, Output(DETAILS_ID, "open"),
Input(SUMMARY_ID, "n_clicks"))` returning `document.getElementById(id).open` (the native
toggle has completed by the time it runs; keyboard activation fires click too). Guard
with `if (!n_clicks) return window.dash_clientside.no_update` -- `n_clicks` defaults to
**0**, not null, so the initial call would otherwise write `open=false` and re-fire the
chart callback for nothing. `ui/tabs/header.py::_SUMMARY_OPEN_MIRROR_JS` is the copy.

**How to apply / test:** a clientside callback lands in `app.callback_map["<id>.open"]`
(inputs list, no usable `"callback"`) and in `app._callback_list` with
`clientside_function = {namespace, function_name}`; the JS source is in
`app._inline_scripts` (Dash wraps it in `ns["<sha256>"] = function...`). Build a bare
`dash.Dash(__name__)` and call `header.register_callbacks(app, get_db_path=...)`; no
layout is needed to register. A server callback's raw function is
`app.callback_map[key]["callback"].__wrapped__` (see [[environment]]); `dash.no_update`
returned from it is an instance whose `type(...).__name__ == "NoUpdate"`.

**The running app never reloads:** `ui/launch.py` serves through werkzeug `make_server`
with no reloader (`app.run(debug=True)` in `ui/app.py` is only the `__main__` path). A
code change is proven live only after the user restarts it; `GET /_dash-layout` on
127.0.0.1:8050 shows which layout is being served (grep for the new id). Never restart or
kill it yourself (see [[real-browser-verification-2026-09-18]]).

**Also fixed alongside:** `_update_chart` had no try/except, unlike `_update_figures`, so
an exception was an HTTP 500 and an opened collapsible stayed blank with no reason on the
page; it now returns an `html.P` via `_failure_reason("LTD chart could not be built", ...)`.
Any new header callback must wrap its builder the same way (CLAUDE.md: no figure blank
without its reason).
