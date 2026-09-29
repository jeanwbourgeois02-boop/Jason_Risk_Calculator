---
name: checker-recipe-2026-09-29
description: Read-only running-app check of P&L / Risk (Phase G): CDP driver traps (static-block rows, DOM returnByValue, cp1252 exec), where the full figures live, engine cross-check
metadata:
  type: reference
---

Running-app check recipe (P&L and Risk, 2026-09-29, checker B; scripts in the session scratchpad `check-B/`):

- Serve: `create_app(db_path=..., start_feed=False)` on werkzeug `make_server(threaded=True)`; patch `today_ny` on every loaded `ui*` module for a fixed as-of (sample: 2026-09-18). Write the server PID to a file; stop only that PID; Chrome with its own `--user-data-dir` and `taskkill /PID <browser> /T`.
- Traps: table rows wrap their `td`s in `<div class="static-block">` (display: contents), so `tr.cells` is EMPTY: select `:scope > td, :scope > div > td`. A CDP `Runtime.evaluate` returning a DOM element comes back `{}` (falsy): wrap waits in `!!`. `exec(open(f).read())` reads cp1252 on this PC and mangles U+2212: pass `encoding="utf-8"` or use `chr(0x2212)`.
- Full figures are on hover: money cells carry `title="USD −4,125"`; parse those, not the k/m text.
- Pattern ids (filter bar): `[id*="tf-group"][id*="\"pnl\""] input`; search box needs the native value setter + input + Enter + blur (debounce).
- Engine cross-check: `engine.risk.trades.subset_var(conn, as_of, names)` on a COPY of the db equals the Risk headline exactly.
- Findings of that run are in the report, not here; see [[risk-trade-table-phase-g-2026-09-29]] and [[pnl-history-explain-2026-09-29]].
