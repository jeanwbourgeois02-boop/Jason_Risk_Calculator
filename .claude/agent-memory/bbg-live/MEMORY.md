# bbg-live agent memory

Older notes on these files (live.py, pull_marks.py, pull_report.py) are in `.claude/agent-memory/bbg-data/MEMORY.md` (lane split 2026-09-24).

- [Contract dates step (2026-09-24)](contract_dates_step_2026_09_24.md) — FUT_LAST_TRADE_DT before futures prices, status contract_dates / not_requestable shapes, parallel-lane assumptions, test fakes
- [Phase 2 removal (2026-09-24)](phase2_removal_2026_09_24.md) — status curves block replaced rates, NDF/dividend/index/fixings out, NDF tenor families removed, scratchpad traps
- [Phase 5 options on futures and LME (2026-09-24)](phase5_options_lme_2026_09_24.md) — option date fields, CMDTY PX_MID, _lme_step and status["lme"], LME source/key rule
- [15:00 futures close built and reversed (2026-09-28, morning)](eod_1500_futures_2026_09_28.md) — history: superseded the same day by the one 17:00 close; shared-test-file traps still apply
- [Fault isolation and progress (2026-09-29)](fault_isolation_progress_2026_09_29.md) — guarded steps, chunked asks, connected rule, status progress/steps keys, tests it flips
- [Pull speed (2026-09-30)](pull_speed_2026_09_30.md) — where a press's time goes, chunks 50, one library read, quiet steps, session lent to the backfill, harness
- [Contract dates: no re-ask of empty answers (2026-09-30)](contract_dates_empty_state_2026_09_30.md) — <db>.contract_dates_state.json, skip rule, new status keys
- [One close, 17:00 New York (2026-09-28)](one_close_1700_2026_09_28.md) — CLOSE_HOUR_NY 17 for every instrument, intraday helpers gone, build_future_rows `snapped`, daily_close probe, leftovers for other lanes
- [Pull speed, second pass (2026-10-01)](pull_speed_2026_10_01.md) — status["seconds"] per step, apply kept every press, spot+futures one PX_LAST request, timeout findings
