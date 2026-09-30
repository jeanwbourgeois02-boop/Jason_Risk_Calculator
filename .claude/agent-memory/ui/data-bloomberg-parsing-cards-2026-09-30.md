---
name: data-bloomberg-parsing-cards-2026-09-30
description: Data tab Bloomberg card (pull problems in one place, background "Run Bloomberg check" via ui/diagnostics_runner.py) and Parsing card (parse_check on a dropped file); ids, patterns, traps
metadata:
  type: project
---

User 2026-09-30: "i want a button to diagnose in real time whenever ... this error thing for the bloomberg pull i see it everywhere ... all in one place" in the Data tab.

- Problems table = per-mark / per-trade rows only (`data_checks.problem_rows` no longer adds `pull_problems`). Pull / backfill rows live only in the Bloomberg card's "Pull problems (N)" (`market_data.bloomberg_head`, `data_checks.pull_problems_table`); the status line's pull link and count point at the card.
- Bloomberg card id `market-data-bloomberg` (`market_data.BBG_CARD_ID`); other tabs switch to it with `formatting.tab_link(label, "market-data", idx)` (tab_link only switches tab, no scroll). "Details" fold keeps `DIAGNOSTICS_ID` / `DIAG_SUMMARY_ID` / `DIAG_BODY_ID`.
- `ui/diagnostics_runner.py`: one background thread per db path (module lock), state in memory only; runs `tools.bbg_diagnostics.run_bloomberg_diagnostics(db_path=active)` then `ticker_check.check_book(on_progress=...)`; refuses while the status file's `progress.running` is fresh (< 15 min).
- Callbacks: button -> run store (start); one results callback (run store, dcc.Interval, filter store, sort store) owns results, progress, interval disabled, button disabled and the CSV style. Avoid allow_duplicate on `disabled` (needs prevent_initial_call).
- Parsing card: `dcc.Upload` id `market-data-parse-upload` (never `report-file`); its callback resets `contents` to None so the same file re-checks; result dict in a dcc.Store.
- Funnel tables: `data_checks.funnel_head` / `filter_rows` / `list_options` (tick lists only), pattern types `md-tick-col`, `md-parse-col`; sorts `data-tick-sort`, `data-parse-sort`.
- Trap: CSV buttons that are callback Inputs sit statically in the card strip, hidden by style (never created inside a callback's output).
- Trap: bash heredocs failed twice here with "unexpected EOF" on long Python blocks; write the script to the scratchpad with Write and run it.
- Stale tests left (not fixed): test_ui.py `_render_bbg_results` / `run_bloomberg_diagnostics_safe` tests (both removed).
