# bbg-data agent memory

- [BNP marks data quirks](data_quirks_bnp_marks.md) — real-file behaviour of data/bloomberg/bnp_marks.py (229 rows -> 19 keys, no crosses, XXXUSD Fx==1.0 so no SPOT for those 4 pairs, shared validation on load)
- [pull_marks.py open questions](pull_marks_open_questions.md) — unverified Bloomberg field/override names to confirm on the real terminal (SPOT, FUTURE_PX, FWD_OUTRIGHT direct + tenor fallback, BBG_INTERP)
- [pull_marks.py diagnostics/probe layer](pull_marks_diagnostics.md) — diag JSON structure, --probe mode, exit codes, "partial" = requested-vs-written key sets (not just TIMEOUT), correlation ids, probe outcome
- [BUILD_PLAN.md Task B (2026-09-15)](build_plan_2026_09_15_task_b.md) — live.py/backfill.py rewrite, new inventory.py/manual.py, cross-team `theme` column pitfall in test fixtures
- [Bloomberg diagnostics consolidation (2026-09-16)](diagnostics_module_naming.md) — renamed diagnose.py->pull_report.py, bloomberg_diagnostic.py->bloomberg_terminal_probe.py; risk.py + test_bloomberg_diagnostic.py still need housekeeper fix
- [rates_marketdata.py OIS curve layer](rates_marketdata_ois.md) — ported from Rates Swap Calculator ref project; fake-blpapi needs by-name element access, not generic isArray()/numElements() walk; curve_quotes table written defensively
- [vol_marketdata.py FX vol feed](vol_marketdata_fx_vol_feed.md) — Phase 5 options_calc; vol_quotes schema, UNVERIFIED ticker convention (9 probe items), vol_smile/atm_vol_for_expiry read helpers, VolFileSource --probe tenor-filter gotcha
- [rates_vol_marketdata.py IR option vol feed](rates_vol_marketdata_ir_option_vol_feed.md) — Phase 6 swaption/cap vols; rate_vol_quotes PK lacks vol_type (mirrors rate_vols), UNVERIFIED USSV/USSN/USCV ticker encoding, atm_swaption_vol bilinear interpolation gotcha
- [Bloomberg feed diagnostics trap (2026-09-17)](bloomberg_feed_diagnostics_trap_2026_09_17.md) — "0 of 0 requested marks" false PASS root cause, stale_empty_pull_reason fix, trigger_now(), still-open tools/bbg_diagnostics.py + ui/uploads.py follow-ups
- [Launcher startup perf (2026-09-17)](launcher_startup_perf_2026_09_17.md) — availability() dual-stack localhost fix (4.07s->1.21s), packages.stamp, git fetch timeout, sample-load guard; what wasn't restructured and why
- [Live pull boundary cases (2026-09-17)](live_pull_boundary_cases_2026_09_17.md) — FWD_OUTRIGHT settle==as_of, FUTURE_PX before US close (PX_LAST+PX_SETTLE fallback), no rates/vol step ever ran for option-only currencies/pairs; tools/bbg_diagnostics.py verbatim skip reasons
