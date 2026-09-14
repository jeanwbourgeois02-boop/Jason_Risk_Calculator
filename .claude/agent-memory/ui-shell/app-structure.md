---
name: app-structure
description: ui/app.py skeleton design -- summary()/build_layout()/create_app() split, RISK_DB resolution
metadata:
  type: project
---

`ui/app.py` (created 2026-09-14) is the Dash skeleton, structured for testability without a browser:

- `get_db_path()` resolves `RISK_DB` env var (relative paths joined to repo root), default
  `data/raw/risk.db`.
- `connect_readonly(path)` opens sqlite via `file:...?mode=ro` URI -- caller must ensure the file
  exists first (sqlite errors on a missing file in ro mode), hence `load_summary()` checks
  `Path.exists()` before connecting and returns `empty_summary()` (as_of_date 'none', zero counts,
  plus a `message` key) if not.
- `summary(conn) -> dict` is the plain, testable query function: keys `as_of_date, trades,
  trade_legs, marks, positions`. `as_of_date = MAX(positions.as_of_date)` or `'none'` if positions
  is empty. Pass any sqlite3.Connection (e.g. from `data.ingest.schema.connect()` in tests).
- `build_tab_placeholder(label, data)` / `build_layout(data)` / `create_app(db_path=None)` are
  separated per the task spec so each tab can later move to its own `ui/<tab>.py` module. Six tabs,
  exact labels/order per CLAUDE.md: Cash ladder, FX, Rates, Options, Delta, Overall book.
- No P&L or ladder computation lives here -- placeholder only shows row counts, per CLAUDE.md
  "ui/ ... never recomputes P&L or delta itself".
- Run via `py -3 -m ui.app` (`if __name__ == "__main__": create_app().run(debug=True)`).
