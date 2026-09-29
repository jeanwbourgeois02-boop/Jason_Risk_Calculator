---
name: phase-g-round1-2026-09-29
description: Phase G round 1 (five tabs, trade as the unit): trade_filter store/bar pattern, Book on trade_book + period_rows, P&L on period_explain per fill, async trade_risk, top bar progress; browser-proof recipe and the Dash 4 click traps
metadata:
  type: project
---

2026-09-29, Phase G round 1 (user: "super logical tabs with beautiful tables that work"; rounds 2 and 3
= Risk/Blotter/Data and the look pass). What is true after it:

- Tabs Book, P&L, Risk, Blotter, Data; "curve" in HIDDEN_TAB_KEYS, curve.py on disk but NOT registered.
- `ui/tabs/trade_filter.py`: one session store `trade-filter-store` in the APP layout (outside tabs);
  each tab's bar is rendered by `register_bar(app, tab, get_db_path, options_of)` on as-of/revision with
  State(store) (never on a filter change: the search keeps focus); controls have pattern ids
  {"type": "tf-search"|"tf-type"|..., "tab": tab}; ONE `_sync` callback (ALL) writes the store, returning
  no_update when equal (the bar's insertion fires it). Clear writes the controls. Links
  {"type": "tf-link", "to", "trade", "idx"} set the store + main-tabs (allow_duplicate) or
  `trade-filter-see-fills` for the Blotter. Render callbacks read the STORE, not the controls.
- Book = `shared_trade_book` (blotter_pricing, memo per revision+research key) + per-fill
  `pnl.period_rows` summed (total row = header to the cent, verified sample: Daily/MTD/LTD). Fills on no
  trade -> pseudo-trade "No trade name". trade_book's own pnl can differ from period_rows sums: use
  period_rows. Header count reads shared_trade_book too (strategies' trade_ids count closed trades as open).
- `shared_trade_risk(wait=False)` computes on a thread; Book polls `book-risk-poll` (1.5 s) until ready,
  then `book-risk-ready` re-renders (z column fills ~5 s after the table on the sample).
- P&L: `pnl.period()` memo per choice: period_explain.by_trade (custom: period_pnl + classify_trades),
  per-day bars = period_pnl(prev day, d), line = period_pnl(start_ref, d), both per fill summed.
- Research figures through `tf.research_mark()` (engine.risk.commodity_history.research_source).

**Browser proof recipe** (scratchpad `ui-g1/`: `serve.py <db> <port>`, `drive_lib.py`, drive*.py; headless
Chrome `--remote-debugging-port --remote-allow-origins=* --user-data-dir=C:\tmp\g1c`): clear sessionStorage
and navigate to a NEW url (a reload keeps the old DOM for a moment and a `wait` returns at once). Dash 4
RadioItems / Dropdown: click the option's `input`, not the label; a `debounce=True` dcc.Input commits on
CDP `Input.insertText` + keyDown/char(13)/keyUp. Stop servers/Chrome by the PID on the port, never by name.

**Why:** the next rounds (Risk on the same bar, the look pass on `.tk-*`) build on these.
**How to apply:** Risk (round 2) calls `tf.register_bar(app, "risk", ...)`, `tf.bar_slot("risk")`, reads
`tf.STORE_ID`, `shared_trade_risk`, `subset_var` on the trade names showing; tables use
`book-table book-grid tk-table` in a `book-card book-main tk-card` with a `tk-table-slot`.
Bash heredocs turn "\\n" in Python source into real newlines: write edit scripts with Write.
