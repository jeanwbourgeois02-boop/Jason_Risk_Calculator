# bbg-data agent memory

- [BNP marks data quirks](data_quirks_bnp_marks.md) — real-file behaviour of data/bloomberg/bnp_marks.py (229 rows -> 19 keys, no crosses, XXXUSD Fx==1.0 so no SPOT for those 4 pairs, shared validation on load)
- [pull_marks.py open questions](pull_marks_open_questions.md) — unverified Bloomberg field/override names to confirm on the real terminal (SPOT, FUTURE_PX, FWD_OUTRIGHT direct + tenor fallback, BBG_INTERP)
- [pull_marks.py diagnostics/probe layer](pull_marks_diagnostics.md) — diag JSON structure, --probe mode, exit codes, "partial" = requested-vs-written key sets (not just TIMEOUT), correlation ids, probe outcome
