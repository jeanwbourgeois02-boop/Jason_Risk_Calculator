---
name: never-stale-page-2026-09-28
description: The browser-side "never a stale page" rule (2026-09-28): no-store after_request in create_app, readiness-gated browser open in ui/launch.py, how Dash 4.4.1 fingerprints bundles and assets, how to prove it against a scratch DB
metadata:
  type: project
---

User rule 2026-09-28: "make sure there is never a stale page that loads when you load the app."
Browser side is ui-shell's: `ui/app.py::create_app` has a Flask `after_request` that stamps
`Cache-Control: no-store, no-cache, must-revalidate, max-age=0` on every response except the
fingerprinted ones (`no_store_for` / `FINGERPRINTED_PATHS`); `ui/launch.py::wait_until_ready`
polls the identity route and only then opens the browser (never a timer); a port held by a
silent listener is named with `taskkill /PID <pid> /F` and the next port is tried (the
next-port behaviour is deliberate: Henry's `pnl` app may sit on 8050; 2_launcher.py's
`port_preflight`, infra's, says the same). Server side (old process, stale venv) is infra's.

Non-obvious facts learned (Dash 4.4.1, Flask 3.1.3):
- Component bundles are fingerprinted IN THE PATH (`bundle.v7_4_1m<mtime>.js`), not a `?v=`
  query; Dash serves them `Cache-Control: max-age=31536000`, which is right because a new
  version is a new URL. Do not add no-store there.
- `assets/style.css` is linked `?m=<mtime>` in the index and Dash serves it with its own
  `Cache-Control: no-cache` + ETag, so even the bare URL revalidates.
- The launched app never calls `app.run`: launch.py hands `app.server` to werkzeug's
  `make_server`, so `_dev_tools.hot_reload` / `ui` / `serve_dev_bundles` stay False and the
  index carries no hot-reload config. `ui/app.py`'s `__main__` block is a dev shortcut only.
- Proof recipe (no browser needed): create_app on a scratch db, `make_server(..., 0)`, request
  `/`, `/_dash-layout`, `/_dash-dependencies`, a `/_dash-update-component` POST (the revision
  poll's payload works), a suite URL and the css; to drive `main()` end to end, patch
  `ui.app.create_app`, `werkzeug.serving.make_server`, `launch.webbrowser.open` (call
  `server.shutdown()` from it) and `launch.PORTS`; a raw listening socket that never speaks
  HTTP stands in for a hung process (werkzeug prints WinError 10013's text itself).

Second pass the same day (coordinator's four items): `source_fingerprint` now hashes every
file under ui / engine / data / config except data/raw, data/bbg_snapshot, __pycache__ and
.db/.pyc (`source_files`); `IDENTITY_PREFIX` is `jason-risk-monitor:` and any
`LEGACY_PREFIX` (`risk-monitor:`) answer, Henry's fork included, is another application,
never stopped; `ui/revision.py` has `BUILD_ID` / `STALE_ID` / `RELOAD_SINK_ID`,
`page_is_stale`, the `_build_check` callback and the clientside `window.location.reload()`
(the app's one browser reload, on a code change only). `create_app(build_fingerprint=)`
computes or takes the build and passes it to `revision.register`; the layout lambda does
NOT yet pass `build=build` to `build_layout` (edit refused by the permission classifier as
"Interfere With Workloads", as was the end-to-end proof and a force-kill of a hung
port-owner by its command line): until a session with permission adds that one argument,
pages bake "" and no reload is ever forced. Not to be re-attempted by an agent after a
denial (see [[coordinator-relay-verification-2026-09-17]]).

**Why:** a reload on Windows kept serving an old index/layout, and the browser used to open on
a timer before the server answered.
**How to apply:** keep `no_store_for` in step with any new fingerprinted route; never put
`no-store` on `_dash-component-suites/` or `assets/`.
