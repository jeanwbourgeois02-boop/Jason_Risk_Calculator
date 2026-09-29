---
name: browser-timing-recipe-2026-09-29
description: How to time a click in the browser properly (fetch wrapped in the page, done = nothing pending for 500 ms), what the Phase G fix pass measured before any change, and why the checkers' 1.3-1.9 s tab switches were mostly their own sleep
metadata:
  type: project
---

Phase G fix pass (2026-09-29), stopped by the user before any ui/ edit. The measurement itself is worth keeping.

**Recipe** (session scratchpad `fix/`): `serve.py <db> <port> [as_of] [--warm]` logs every callback's server ms and bytes to `cb_<port>.log` and can run `ui.warmup` first; `chrome.py <cdp port>` writes its PID; `perf.py <port> <cdp> <label> sample|real` injects a fetch wrapper (`Page.addScriptToEvaluateOnNewDocument`) that counts pending `_dash-update-component` calls. It ignores the revision poll, and marks a call done after its `json()`, then setTimeout, rAF, setTimeout. A step is click -> the last response drawn, and it also records the number of calls, the bytes and the slowest round trip. Risk fold heads are `.risk-fold-head` with the key in the JSON id (match with `String.fromCharCode(34)`).

**Findings (sample, as-of 2026-09-18, warm-up run first):** most clicks and tab switches take 0.2-0.5 s. The checkers' drivers sleep 0.8 s in `busy_wait` plus a 0.3 s poll, so their "1.3-1.9 s" was mostly that sleep. The slow paths:
- Book row open the first time: 1.6-3.3 s, one server call, the panel's level history, not warmed.
- P&L YTD / All the first time: 0.4-0.6 s of server time; warm-up fills only MTD.
- Data tab: 0.7-1.4 s, 169 KB, market-data-body 0.3-0.6 s.
- Risk commodity row and grid unit: 0.6-0.7 s, 71 KB.
- Cold, with no warm-up (checker A): header 6.8 s, pnl 9.3 s, risk 8.2 s, mostly the dates valued more than once. The engine is adding a `value_fn` for that (item 16). `curve_positions(conn, as_of, spreads=None, value_fn=None)` has landed; `daily_series`, `book_positions` and `book_spreads` had not when the pass stopped.

**How to apply:** time with this driver, never with sleep-and-poll. First fixes: warm every P&L period and the Book panels in `ui/warmup.py`, pass `value_fn` into the engine calls, and trim the Data body.
