---
name: live-db-state-2026-09-16
description: what data/raw/risk.db actually contains for marks/curves as of 2026-09-16, and why marks_official is empty
metadata:
  type: project
---

Checked 2026-09-16 against `data/raw/risk.db`: `marks` has 33 rows, all `source='BNP_BVAL'`
(19 FWD_OUTRIGHT + 14 SPOT). BNP_BVAL is reconciliation-only per CLAUDE.md, never official,
so `marks_official` (a view keyed on `OFFICIAL_MARK_SOURCE`) returns **zero rows**. No row
in `marks` has ever come from `BBG_BFXFORWARD` or `BBG_BDH` — i.e. `data/bloomberg/pull_marks.py`
/ `live.py` (which write those sources) have never successfully run against a real terminal
on this machine; only the BNP CSV parser (`data/bloomberg/bnp_marks.py`, reconciliation path)
has ever populated `marks`. `curves` and `curve_quotes` are both empty (0 rows); `trades` has
229 rows but 0 with `product='IRS'` (xlsx IRS ingest not wired yet — matches
[[no-live-pipeline-caller-2026-09-16]] from rates-pricer's audit, confirmed independently).

This is a snapshot of DB *content*, not a code gap — the pipeline code
(`data/bloomberg/pull_marks.py`, `live.py`, `bnp_marks.py`) itself is fine; it just has
never been run live with a working blpapi/terminal connection on this dev machine, and no
xlsx IRS parser has fed `trades` yet. Re-check `SELECT source, mark_type, COUNT(*) FROM marks
GROUP BY 1,2` before assuming this is still true in a later session — it will change as soon
as someone runs `pull_marks.py`/`live.py` against a real terminal, or the new xlsx-IRS parser
lands.

**How to apply**: when auditing "does Bloomberg data flow end-to-end", always check actual
`marks`/`marks_official`/`curve_quotes` row counts and sources first (`sqlite3` query, or
`py -3 tools/bbg_diagnostics.py`) rather than assuming code presence means data presence —
on this dev machine the code paths for BBG_BFXFORWARD/BBG_BDH/QL_PRICER have code but zero
live rows; only the BNP reconciliation path has ever actually run.
