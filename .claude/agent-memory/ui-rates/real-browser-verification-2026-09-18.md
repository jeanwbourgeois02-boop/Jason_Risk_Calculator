---
name: real-browser-verification-2026-09-18
description: How to click-test the Dash app in a real headless Edge with nothing installed (DevTools + websocket-client), screenshot timing trap, and three Dash/status-file facts that bit the upload strip work
metadata:
  type: project
---

**Real clicks without Selenium/Playwright (neither is installed; do not pip-install for a
check).** `websocket-client` IS installed. Start Edge from Python with
`--headless=new --remote-debugging-port=N --remote-allow-origins=* --user-data-dir=<scratch>`,
read `http://127.0.0.1:N/json` for the page's `webSocketDebuggerUrl`, then send
`Runtime.evaluate`. `window.dash_clientside.set_props(id, {...})` injects a prop AND fires
the callbacks that listen to it, so a real file import works by setting the `dcc.Upload`'s
`contents` to a base64 data URL and clicking Confirm. Used for 26 end-to-end checks on
ui/uploads.py + ui/feed_controls.py.

**Never `taskkill /IM msedge.exe`** (what [[blotter-futures-subtab-2026-09-15]] used to
advise). **Why:** the user usually has the live app open in Edge (`2_launcher.py start` was
running during this session) and several agents share the machine. **How to apply:** an
isolated `--user-data-dir` is what prevents the hang; kill only the PID you started
(`taskkill /PID <pid> /T /F`), and make scratch servers exit by themselves
(`threading.Timer(45, lambda: os._exit(0))`).

**Screenshot trap:** `--screenshot` fires at the load event, before async chunks
(`dcc.Upload` is one) and before any callback. The Upload button and every
callback-filled line look MISSING. Add `--virtual-time-budget=9000` (with a `timeout`
wrapper) before concluding a component is gone.

**Top-bar geometry:** the three tabs STRETCH to fill everything left of a fixed reserve;
the upload strip is absolutely positioned inside that reserve. Widening the strip alone
draws it over the "Market data" tab at every width. Both now read `--strip-reserve` in
style.css. `nowrap` must reach the real `<button>` nested inside dcc.Upload's wrapper.

**Dash/status facts:**
- A component `setProps` with identical values fires nothing, so choosing the SAME file
  twice did nothing until `contents` is reset to None after an import/Cancel.
- The Bloomberg status file has whole-second timestamps and holds startup placeholders
  ("no pull has run yet", "first pull in progress"). "Did my requested pull land?" needs
  BOTH a changed report fingerprint and start time >= request; time alone let a second
  click through. Never call a placeholder's timestamp an "attempt".
- Rapid clicks reach the server before the first response disables the button: a
  browser-side `disabled` is not a guard. Needs a server-side lock.

**Size UI from REAL strings, never from a contract's examples.** The import-report contract
quoted ~60-character notes; the real ingest notes run to ~190 (they list trade ids). A
132px list cap sized from the examples hid the end of the fourth real note behind a
scroll, and a check built from the short examples would have PASSED. Run the real
function on `data/sample/blotter_sample.csv` (git-tracked, 857 trades) first and copy its
output into the browser check. Measure with `getBoundingClientRect` / `scrollHeight` vs
`clientHeight` over DevTools; `Page.captureScreenshot` gives the picture on the same
connection (no second headless run, no timing trap).

**"getattr so either order works" is not enough when another file's tests patch a name.**
tests/test_ui.py patches only `ui.uploads.import_blotter`. Once data-ingest landed
`import_blotter_report`, a bare getattr preference would bypass that patch and run the real
import on the tests' junk bytes. `ui/uploads.py::run_import` takes the structured path only
while this module's `import_blotter` IS the ingest module's own function (a substituted
`import_blotter` is the import). Before preferring a new optional function, grep every
test file for patches of the old name.

**Full suite touches the real DB:** the `create_app(db_path=None)` tests in tests/test_ui.py
that ran `ensure_schema` on data/raw/risk.db were pointed at tmp_path later the same day;
tests/test_launch.py still does it -- see [[blotter-self-refreshing-subtabs-2026-09-18]].
